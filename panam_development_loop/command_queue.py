"""Data-only DL-2.1 command definition registry and application service."""

import copy
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable

from .models import (
    QueueResult,
    QueueResultCode,
    ValidatedCommandEnvelope,
    WorkflowCommandState,
    _QUEUE_ACTOR_PATTERN,
    _QUEUE_CODE_PATTERN,
    _QUEUE_COMMAND_KIND_PATTERN,
    _QUEUE_IDEMPOTENCY_PATTERN,
    _QUEUE_PAYLOAD_KEY_PATTERN,
    _canonical_eligible_definition_keys,
    _canonical_queue_payload,
    _intent_digest,
    _queue_identity,
    _queue_integer,
    _queue_optional_identity,
    _queue_text,
    _queue_uuid,
    _validate_payload_object,
    _validated_payload_json,
)
from .repositories import WorkflowCommandRepository


@dataclass(frozen=True)
class CommandDefinition:
    command_kind: str
    command_schema_version: int
    required_keys: tuple[str, ...]
    optional_keys: tuple[str, ...]
    nullable_keys: tuple[str, ...]
    payload_validator: Callable[[dict[str, object]], str]

    def __post_init__(self) -> None:
        _queue_text(self.command_kind, "command_kind", _QUEUE_COMMAND_KIND_PATTERN)
        _queue_integer(
            self.command_schema_version,
            "command_schema_version",
            1,
            2_147_483_647,
        )
        for field_name, values in (
            ("required_keys", self.required_keys),
            ("optional_keys", self.optional_keys),
            ("nullable_keys", self.nullable_keys),
        ):
            if type(values) is not tuple:
                raise TypeError(field_name)
            if any(type(value) is not str for value in values):
                raise TypeError(field_name)
            if any(_QUEUE_PAYLOAD_KEY_PATTERN.fullmatch(value) is None for value in values):
                raise ValueError(field_name)
            if tuple(sorted(values)) != values or len(set(values)) != len(values):
                raise ValueError(field_name)
        required = set(self.required_keys)
        optional = set(self.optional_keys)
        nullable = set(self.nullable_keys)
        if required & optional:
            raise ValueError("required_keys")
        if not nullable <= required | optional:
            raise ValueError("nullable_keys")
        if not callable(self.payload_validator):
            raise TypeError("payload_validator")


class CommandDefinitionRegistry:
    def __init__(self, definitions: tuple[CommandDefinition, ...] = ()) -> None:
        if type(definitions) is not tuple:
            raise TypeError("definitions")
        self._definitions: dict[tuple[str, int], CommandDefinition] = {}
        self._frozen = False
        for definition in definitions:
            self.register(definition)

    def register(self, definition: CommandDefinition) -> None:
        if self._frozen:
            raise RuntimeError("registry frozen")
        if type(definition) is not CommandDefinition:
            raise TypeError("definition")
        key = (definition.command_kind, definition.command_schema_version)
        if key in self._definitions:
            raise ValueError("duplicate definition")
        self._definitions[key] = definition

    def lookup(self, command_kind: str, command_schema_version: int) -> CommandDefinition:
        kind = _queue_text(command_kind, "command_kind", _QUEUE_COMMAND_KIND_PATTERN)
        version = _queue_integer(
            command_schema_version,
            "command_schema_version",
            1,
            2_147_483_647,
        )
        try:
            return self._definitions[(kind, version)]
        except KeyError as error:
            raise LookupError(f"unknown command definition: {kind}:{version}") from error

    @staticmethod
    def _validate_definition_payload(
        definition: CommandDefinition,
        payload: dict[str, object],
    ) -> None:
        keys = set(payload)
        required = set(definition.required_keys)
        allowed = required | set(definition.optional_keys)
        if not required <= keys or not keys <= allowed:
            raise ValueError("payload keys")
        nullable = set(definition.nullable_keys)
        if any(value is None and key not in nullable for key, value in payload.items()):
            raise ValueError("payload null")

    def validate(
        self,
        command_kind: str,
        command_schema_version: int,
        payload: dict[str, object],
    ) -> str:
        if not self._frozen:
            raise RuntimeError("registry not frozen")
        definition = self.lookup(command_kind, command_schema_version)
        if type(payload) is not dict:
            raise TypeError("payload")
        _validate_payload_object(payload)
        self._validate_definition_payload(definition, payload)
        canonical = definition.payload_validator(copy.deepcopy(payload))
        if type(canonical) is not str:
            raise TypeError("payload_validator return")
        _, validated = _validated_payload_json(canonical)
        self._validate_definition_payload(definition, validated)
        return canonical

    def freeze(self) -> None:
        self._frozen = True


def _format_queue_timestamp(value: object) -> str:
    if type(value) is not datetime:
        raise TypeError("clock")
    try:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("clock")
        utc_value = value.astimezone(timezone.utc)
    except (OverflowError, OSError) as error:
        raise ValueError("clock") from error
    return (
        f"{utc_value.year:04d}-{utc_value.month:02d}-{utc_value.day:02d}"
        f"T{utc_value.hour:02d}:{utc_value.minute:02d}:{utc_value.second:02d}."
        f"{utc_value.microsecond:06d}Z"
    )


class DurableCommandQueueService:
    def __init__(
        self,
        repository: WorkflowCommandRepository,
        registry: CommandDefinitionRegistry,
        lease_duration_seconds: int,
        *,
        clock: Callable[[], datetime],
        id_factory: Callable[[], str],
    ) -> None:
        required_methods = (
            "enqueue",
            "get",
            "list_project",
            "history",
            "claim_next",
            "renew_lease",
            "mark_running",
            "request_cancellation",
            "acknowledge_cancellation",
            "mark_succeeded",
            "mark_failed",
            "recover_expired_claim",
        )
        if repository is None or any(
            not callable(getattr(repository, method, None)) for method in required_methods
        ):
            raise TypeError("repository")
        if type(registry) is not CommandDefinitionRegistry:
            raise TypeError("registry")
        duration = _queue_integer(
            lease_duration_seconds,
            "lease_duration_seconds",
            1,
            86_400,
        )
        if not callable(clock):
            raise TypeError("clock")
        if not callable(id_factory):
            raise TypeError("id_factory")
        registry.freeze()
        self._repository = repository
        self._registry = registry
        self._lease_duration_seconds = duration
        self._clock = clock
        self._id_factory = id_factory

    def _now(self) -> tuple[datetime, str]:
        instant = self._clock()
        return instant, _format_queue_timestamp(instant)

    def _event_id(self) -> str:
        return _queue_uuid(self._id_factory(), "event_id")

    def _lease_expiry(self, instant: datetime) -> str:
        try:
            utc_instant = instant.astimezone(timezone.utc)
            expiry = utc_instant + timedelta(seconds=self._lease_duration_seconds)
        except OverflowError as error:
            raise ValueError("lease_expires_at") from error
        return _format_queue_timestamp(expiry)

    @staticmethod
    def _expected(
        expected_state: WorkflowCommandState,
        expected_state_version: int,
        allowed: frozenset[WorkflowCommandState],
    ) -> tuple[WorkflowCommandState, int]:
        if type(expected_state) is not WorkflowCommandState:
            raise TypeError("expected_state")
        if expected_state not in allowed:
            raise ValueError("expected_state")
        version = _queue_integer(
            expected_state_version,
            "expected_state_version",
            1,
            9_223_372_036_854_775_807,
        )
        return expected_state, version

    def enqueue(
        self,
        *,
        project_id: str,
        command_kind: str,
        command_schema_version: int,
        payload: dict[str, object],
        idempotency_key: str,
        actor_id: str,
        development_run_id: str | None = None,
        phase_id: str | None = None,
        priority: int = 0,
    ) -> QueueResult:
        project = _queue_identity(project_id, "project_id")
        run = _queue_optional_identity(development_run_id, "development_run_id")
        phase = _queue_optional_identity(phase_id, "phase_id")
        kind = _queue_text(command_kind, "command_kind", _QUEUE_COMMAND_KIND_PATTERN)
        schema_version = _queue_integer(
            command_schema_version,
            "command_schema_version",
            1,
            2_147_483_647,
        )
        key = _queue_text(
            idempotency_key, "idempotency_key", _QUEUE_IDEMPOTENCY_PATTERN
        )
        actor = _queue_text(actor_id, "actor_id", _QUEUE_ACTOR_PATTERN)
        queue_priority = _queue_integer(priority, "priority", 0, 100)
        payload_json = self._registry.validate(kind, schema_version, payload)
        validated_payload = json.loads(payload_json)
        digest = _intent_digest(
            project_id=project,
            development_run_id=run,
            phase_id=phase,
            command_kind=kind,
            command_schema_version=schema_version,
            payload=validated_payload,
            priority=queue_priority,
        )
        envelope = ValidatedCommandEnvelope(
            project_id=project,
            development_run_id=run,
            phase_id=phase,
            command_kind=kind,
            command_schema_version=schema_version,
            payload_json=payload_json,
            intent_digest=digest,
            idempotency_key=key,
            priority=queue_priority,
        )
        _, occurred_at = self._now()
        command_id = _queue_uuid(self._id_factory(), "command_id")
        event_id = self._event_id()
        return self._repository.enqueue(
            command_id=command_id,
            event_id=event_id,
            envelope=envelope,
            actor_id=actor,
            occurred_at=occurred_at,
        )

    def get(self, command_id: str) -> QueueResult:
        command = self._repository.get(_queue_uuid(command_id, "command_id"))
        return QueueResult(
            QueueResultCode.NOT_FOUND if command is None else QueueResultCode.FOUND,
            command=command,
        )

    def list_project(self, project_id: str) -> QueueResult:
        commands = self._repository.list_project(_queue_identity(project_id, "project_id"))
        return QueueResult(QueueResultCode.LISTED, commands=commands)

    def history(self, command_id: str) -> QueueResult:
        events = self._repository.history(_queue_uuid(command_id, "command_id"))
        if events is None:
            return QueueResult(QueueResultCode.NOT_FOUND)
        return QueueResult(QueueResultCode.HISTORY_RETURNED, events=events)

    def claim_next(self, *, lease_owner: str) -> QueueResult:
        owner = _queue_text(lease_owner, "lease_owner", _QUEUE_ACTOR_PATTERN)
        instant, acquired_at = self._now()
        expires_at = self._lease_expiry(instant)
        return self._repository.claim_next(
            event_id=self._event_id(),
            lease_owner=owner,
            lease_acquired_at=acquired_at,
            lease_expires_at=expires_at,
        )

    def claim_next_eligible(
        self,
        *,
        eligible_definition_keys: tuple[tuple[str, int], ...],
        lease_owner: str,
    ) -> QueueResult:
        owner = _queue_text(lease_owner, "lease_owner", _QUEUE_ACTOR_PATTERN)
        keys = _canonical_eligible_definition_keys(eligible_definition_keys)
        repository_method = getattr(
            self._repository, "claim_next_eligible", None
        )
        if not callable(repository_method):
            raise TypeError("repository")
        instant, acquired_at = self._now()
        expires_at = self._lease_expiry(instant)
        return repository_method(
            event_id=self._event_id(),
            eligible_definition_keys=keys,
            lease_owner=owner,
            lease_acquired_at=acquired_at,
            lease_expires_at=expires_at,
        )

    def renew_lease(
        self,
        *,
        command_id: str,
        expected_state: WorkflowCommandState,
        expected_state_version: int,
        lease_owner: str,
    ) -> QueueResult:
        state, version = self._expected(
            expected_state,
            expected_state_version,
            frozenset({WorkflowCommandState.CLAIMED, WorkflowCommandState.RUNNING}),
        )
        command = _queue_uuid(command_id, "command_id")
        owner = _queue_text(lease_owner, "lease_owner", _QUEUE_ACTOR_PATTERN)
        instant, observed_at = self._now()
        expiry = self._lease_expiry(instant)
        return self._repository.renew_lease(
            command_id=command,
            expected_state=state,
            expected_state_version=version,
            lease_owner=owner,
            observed_at=observed_at,
            lease_expires_at=expiry,
            event_id=self._event_id(),
        )

    def mark_running(
        self,
        *,
        command_id: str,
        expected_state: WorkflowCommandState,
        expected_state_version: int,
        lease_owner: str,
    ) -> QueueResult:
        state, version = self._expected(
            expected_state,
            expected_state_version,
            frozenset({WorkflowCommandState.CLAIMED}),
        )
        command = _queue_uuid(command_id, "command_id")
        owner = _queue_text(lease_owner, "lease_owner", _QUEUE_ACTOR_PATTERN)
        _, occurred_at = self._now()
        return self._repository.mark_running(
            command_id=command,
            expected_state=state,
            expected_state_version=version,
            lease_owner=owner,
            occurred_at=occurred_at,
            event_id=self._event_id(),
        )

    def request_cancellation(
        self,
        *,
        command_id: str,
        expected_state: WorkflowCommandState,
        expected_state_version: int,
        requested_by: str,
        reason_code: str,
    ) -> QueueResult:
        state, version = self._expected(
            expected_state,
            expected_state_version,
            frozenset(WorkflowCommandState),
        )
        command = _queue_uuid(command_id, "command_id")
        requester = _queue_text(requested_by, "requested_by", _QUEUE_ACTOR_PATTERN)
        reason = _queue_text(reason_code, "reason_code", _QUEUE_CODE_PATTERN)
        _, occurred_at = self._now()
        return self._repository.request_cancellation(
            command_id=command,
            expected_state=state,
            expected_state_version=version,
            requested_by=requester,
            reason_code=reason,
            occurred_at=occurred_at,
            event_id=self._event_id(),
        )

    def _owner_mutation(
        self,
        method_name: str,
        *,
        command_id: str,
        expected_state: WorkflowCommandState,
        expected_state_version: int,
        lease_owner: str,
        allowed: frozenset[WorkflowCommandState],
        **extra: str,
    ) -> QueueResult:
        state, version = self._expected(expected_state, expected_state_version, allowed)
        command = _queue_uuid(command_id, "command_id")
        owner = _queue_text(lease_owner, "lease_owner", _QUEUE_ACTOR_PATTERN)
        _, observed_at = self._now()
        method = getattr(self._repository, method_name)
        return method(
            command_id=command,
            expected_state=state,
            expected_state_version=version,
            lease_owner=owner,
            observed_at=observed_at,
            event_id=self._event_id(),
            **extra,
        )

    def acknowledge_cancellation(
        self,
        *,
        command_id: str,
        expected_state: WorkflowCommandState,
        expected_state_version: int,
        lease_owner: str,
    ) -> QueueResult:
        return self._owner_mutation(
            "acknowledge_cancellation",
            command_id=command_id,
            expected_state=expected_state,
            expected_state_version=expected_state_version,
            lease_owner=lease_owner,
            allowed=frozenset({WorkflowCommandState.CLAIMED, WorkflowCommandState.RUNNING}),
        )

    def mark_succeeded(
        self,
        *,
        command_id: str,
        expected_state: WorkflowCommandState,
        expected_state_version: int,
        lease_owner: str,
    ) -> QueueResult:
        return self._owner_mutation(
            "mark_succeeded",
            command_id=command_id,
            expected_state=expected_state,
            expected_state_version=expected_state_version,
            lease_owner=lease_owner,
            allowed=frozenset({WorkflowCommandState.RUNNING}),
        )

    def mark_failed(
        self,
        *,
        command_id: str,
        expected_state: WorkflowCommandState,
        expected_state_version: int,
        lease_owner: str,
        failure_code: str,
    ) -> QueueResult:
        failure = _queue_text(failure_code, "failure_code", _QUEUE_CODE_PATTERN)
        return self._owner_mutation(
            "mark_failed",
            command_id=command_id,
            expected_state=expected_state,
            expected_state_version=expected_state_version,
            lease_owner=lease_owner,
            allowed=frozenset({WorkflowCommandState.RUNNING}),
            failure_code=failure,
        )

    def recover_expired_claim(
        self,
        *,
        command_id: str,
        expected_state: WorkflowCommandState,
        expected_state_version: int,
        recovery_actor: str,
    ) -> QueueResult:
        state, version = self._expected(
            expected_state,
            expected_state_version,
            frozenset({WorkflowCommandState.CLAIMED, WorkflowCommandState.RUNNING}),
        )
        command = _queue_uuid(command_id, "command_id")
        actor = _queue_text(recovery_actor, "recovery_actor", _QUEUE_ACTOR_PATTERN)
        _, observed_at = self._now()
        return self._repository.recover_expired_claim(
            command_id=command,
            expected_state=state,
            expected_state_version=version,
            recovery_actor=actor,
            observed_at=observed_at,
            event_id=self._event_id(),
        )
