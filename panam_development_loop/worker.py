"""Synchronous, fail-closed DL-2.2 Worker Process Foundation."""

import copy
import json
import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from .command_queue import (
    CommandDefinition,
    CommandDefinitionRegistry,
    DurableCommandQueueService,
    _format_queue_timestamp,
)
from .models import (
    QueueResult,
    QueueResultCode,
    WorkerCheckpointDirective,
    WorkerConfiguration,
    WorkerFailureCode,
    WorkerHandlerContext,
    WorkerHandlerResult,
    WorkerHandlerStatus,
    WorkerIterationResult,
    WorkerIterationStatus,
    WorkerJournalResult,
    WorkerJournalResultCode,
    WorkerOperationKind,
    WorkerOperationReceipt,
    WorkerOperationState,
    WorkerReconciliationStatus,
    WorkerSession,
    WorkerSessionState,
    WorkerStartupResult,
    WorkerStartupStatus,
    WorkflowCommand,
    WorkflowCommandState,
    _QUEUE_CODE_PATTERN,
    _WORKER_RESULT_CODE_PATTERN,
    _parse_queue_timestamp,
    _queue_integer,
    _queue_text,
    _worker_id,
    _worker_queue_owner,
    _worker_uuid,
    _validate_payload_object,
    _validated_payload_json,
)
from .repositories import RepositoryError, WorkerJournalRepository
from .sqlite_migrations import MigrationError
from .sqlite_repositories import _canonical_database_path


_TERMINAL_QUEUE_STATES = frozenset(
    {
        WorkflowCommandState.SUCCEEDED,
        WorkflowCommandState.FAILED,
        WorkflowCommandState.CANCELLED,
    }
)
_TERMINAL_RECEIPT_STATES = frozenset(
    {
        WorkerOperationState.SUCCEEDED,
        WorkerOperationState.FAILED,
        WorkerOperationState.CANCELLED,
        WorkerOperationState.CLAIM_RELEASED,
        WorkerOperationState.LEASE_LOST,
        WorkerOperationState.RECONCILIATION_REQUIRED,
    }
)


class _RegistryInvariantError(RuntimeError):
    pass


class _ValidatorExecutionError(RuntimeError):
    pass


_CHECKPOINT_SEAL = object()


@dataclass(frozen=True)
class _BoundOperationFence:
    worker_id: str
    session_sequence: int
    session_id: str
    session_state_version: int
    queue_owner_id: str
    command_id: str
    operation_id: str
    claim_count: int
    receipt_state_version: int
    precondition_state_version: int
    lease_expires_at: str


@dataclass(frozen=True)
class _BoundOperationDecision:
    precedence: str
    session: WorkerSession | None = None
    command: WorkflowCommand | None = None
    operation: WorkerOperationReceipt | None = None
    origin: str | None = None


class WorkerCheckpoint:
    """A handler-visible cooperative observation boundary."""

    __slots__ = ("_worker", "_fence", "_seal")

    def __init__(
        self,
        seal: object,
        worker: "DevelopmentWorker",
        fence: _BoundOperationFence,
    ) -> None:
        if seal is not _CHECKPOINT_SEAL or type(fence) is not _BoundOperationFence:
            raise TypeError("WorkerCheckpoint is Worker-owned")
        object.__setattr__(self, "_worker", worker)
        object.__setattr__(self, "_fence", fence)
        object.__setattr__(self, "_seal", seal)

    def __setattr__(self, _name: str, _value: object) -> None:
        raise AttributeError("WorkerCheckpoint is immutable")

    def __delattr__(self, _name: str) -> None:
        raise AttributeError("WorkerCheckpoint is immutable")

    def observe(self) -> WorkerCheckpointDirective:
        if self._seal is not _CHECKPOINT_SEAL:
            return WorkerCheckpointDirective.RECONCILIATION_REQUIRED
        directive = self._worker._observe_checkpoint(self._fence)
        if type(directive) is not WorkerCheckpointDirective:
            raise TypeError("checkpoint directive")
        return directive


def _is_worker_checkpoint(value: object) -> bool:
    """Validate the exact Worker-owned capability without executing it."""

    return (
        type(value) is WorkerCheckpoint
        and getattr(value, "_seal", None) is _CHECKPOINT_SEAL
        and type(getattr(value, "_fence", None)) is _BoundOperationFence
        and type(getattr(value, "_worker", None)) is DevelopmentWorker
    )


@dataclass(frozen=True)
class WorkerCommandHandler:
    handler_id: str
    callback: Callable[[WorkerHandlerContext], WorkerHandlerResult]

    def __post_init__(self) -> None:
        _queue_text(self.handler_id, "handler_id", _WORKER_RESULT_CODE_PATTERN)
        if not callable(self.callback):
            raise TypeError("callback")

    def __call__(self, context: WorkerHandlerContext) -> WorkerHandlerResult:
        if type(context) is not WorkerHandlerContext:
            raise TypeError("context")
        return self.callback(context)


@dataclass(frozen=True)
class WorkerHandlerEntry:
    definition: CommandDefinition
    handler: WorkerCommandHandler

    def __post_init__(self) -> None:
        if type(self.definition) is not CommandDefinition:
            raise TypeError("definition")
        if type(self.handler) is not WorkerCommandHandler:
            raise TypeError("handler")


class WorkerHandlerRegistry:
    """One immutable generation of definition, validator, and handler bindings."""

    def __init__(self, entries: tuple[WorkerHandlerEntry, ...] = ()) -> None:
        if type(entries) is not tuple:
            raise TypeError("entries")
        self._entries: dict[tuple[str, int], WorkerHandlerEntry] = {}
        self._definition_registry: CommandDefinitionRegistry | None = None
        self._frozen = False
        for entry in entries:
            self.register(entry)

    def register(self, entry: WorkerHandlerEntry) -> None:
        if self._frozen:
            raise RuntimeError("registry frozen")
        if type(entry) is not WorkerHandlerEntry:
            raise TypeError("entry")
        key = (
            entry.definition.command_kind,
            entry.definition.command_schema_version,
        )
        if key in self._entries:
            raise ValueError("duplicate entry")
        self._entries[key] = entry

    def freeze(self) -> None:
        if self._frozen:
            return
        definitions = tuple(
            self._entries[key].definition for key in sorted(self._entries)
        )
        registry = CommandDefinitionRegistry(definitions)
        registry.freeze()
        self._definition_registry = registry
        self._frozen = True

    def keys(self) -> tuple[tuple[str, int], ...]:
        if not self._frozen:
            raise RuntimeError("registry not frozen")
        return tuple(sorted(self._entries))

    def lookup(
        self, command_kind: str, command_schema_version: int
    ) -> WorkerHandlerEntry:
        if not self._frozen or self._definition_registry is None:
            raise RuntimeError("registry not frozen")
        definition = self._definition_registry.lookup(
            command_kind, command_schema_version
        )
        key = (definition.command_kind, definition.command_schema_version)
        try:
            entry = self._entries[key]
        except KeyError as error:
            raise LookupError(
                f"unknown worker handler: {key[0]}:{key[1]}"
            ) from error
        if entry.definition is not definition:
            raise _RegistryInvariantError("frozen registry invariant lost")
        return entry

    def validate(self, command: WorkflowCommand) -> WorkerHandlerEntry:
        if type(command) is not WorkflowCommand:
            raise TypeError("command")
        entry = self.lookup(
            command.command_kind, command.command_schema_version
        )
        if self._definition_registry is None:  # pragma: no cover - lookup fences it
            raise _RegistryInvariantError("frozen registry invariant lost")
        try:
            payload = json.loads(command.payload_json)
        except (json.JSONDecodeError, UnicodeError) as error:
            raise ValueError("payload_json") from error
        if type(payload) is not dict:
            raise ValueError("payload_json")
        _validate_payload_object(payload)
        CommandDefinitionRegistry._validate_definition_payload(
            entry.definition, payload
        )
        try:
            canonical = entry.definition.payload_validator(copy.deepcopy(payload))
        except Exception as error:
            raise _ValidatorExecutionError("payload validator") from error
        if type(canonical) is not str:
            raise _ValidatorExecutionError("payload validator return")
        try:
            _, validated = _validated_payload_json(canonical)
        except (TypeError, ValueError) as error:
            raise _ValidatorExecutionError("payload validator return") from error
        CommandDefinitionRegistry._validate_definition_payload(
            entry.definition, validated
        )
        if canonical != command.payload_json:
            raise ValueError("payload_json")
        if (
            self._definition_registry.lookup(
                command.command_kind, command.command_schema_version
            )
            is not entry.definition
        ):
            raise _RegistryInvariantError("frozen registry invariant lost")
        return entry


class DevelopmentWorker:
    """Own one durable session and run at most one local handler synchronously."""

    def __init__(
        self,
        configuration: WorkerConfiguration,
        queue_service: DurableCommandQueueService,
        journal_repository: WorkerJournalRepository,
        handler_registry: WorkerHandlerRegistry,
        migration_initializer: Callable[[Path, str], None],
        *,
        clock: Callable[[], datetime],
        monotonic_clock: Callable[[], float],
        wait: Callable[[float], bool],
        stop_requested: Callable[[], bool],
        session_id_factory: Callable[[], str],
        operation_id_factory: Callable[[], str],
        queue_database_path: Path,
        journal_database_path: Path,
    ) -> None:
        if type(configuration) is not WorkerConfiguration:
            raise TypeError("configuration")
        if type(queue_service) is not DurableCommandQueueService:
            raise TypeError("queue_service")
        required_journal_methods = (
            "start_session",
            "get_session",
            "heartbeat",
            "transition_session",
            "create_operation",
            "get_operation_for_claim",
            "transition_operation",
            "reconcile_operation",
            "list_nonterminal_operations",
            "list_owned_commands",
        )
        if journal_repository is None or any(
            not callable(getattr(journal_repository, name, None))
            for name in required_journal_methods
        ):
            raise TypeError("journal_repository")
        if type(handler_registry) is not WorkerHandlerRegistry:
            raise TypeError("handler_registry")
        for name, provider in (
            ("migration_initializer", migration_initializer),
            ("clock", clock),
            ("monotonic_clock", monotonic_clock),
            ("wait", wait),
            ("stop_requested", stop_requested),
            ("session_id_factory", session_id_factory),
            ("operation_id_factory", operation_id_factory),
        ):
            if not callable(provider):
                raise TypeError(name)
        self._configuration = configuration
        self._queue_service = queue_service
        self._journal = journal_repository
        self._registry = handler_registry
        self._migration_initializer = migration_initializer
        self._clock = clock
        self._monotonic_clock = monotonic_clock
        self._wait = wait
        self._stop_requested = stop_requested
        self._session_id_factory = session_id_factory
        self._operation_id_factory = operation_id_factory
        self._database_path = configuration.database_path
        self._database_identity: str | None = None
        self._queue_database_path = queue_database_path
        self._journal_database_path = journal_database_path
        self._session: WorkerSession | None = None
        self._last_monotonic: float | None = None
        self._last_heartbeat_monotonic: float | None = None
        self._stop_deadline: float | None = None
        self._stop_accepted = False
        self._stop_transition_attempted = False
        self._last_stop_transition_result: WorkerJournalResult | None = None
        self._stop_bridge_failure = False
        self._bound_fence: _BoundOperationFence | None = None

    def _database_binding_matches(self) -> bool:
        """Resolve all five claimed/actual identities before the first Worker I/O."""

        try:
            configuration_path, configuration_identity = _canonical_database_path(
                self._configuration.database_path
            )
            queue_path, queue_identity = _canonical_database_path(
                self._queue_database_path
            )
            journal_path, journal_identity = _canonical_database_path(
                self._journal_database_path
            )
            queue_repository = getattr(self._queue_service, "_repository")
            actual_queue_value = getattr(queue_repository, "database_path")
            actual_journal_value = getattr(self._journal, "database_path")
            bound_queue_identity = getattr(queue_repository, "database_identity")
            bound_journal_identity = getattr(self._journal, "database_identity")
            if type(bound_queue_identity) is not str or type(bound_journal_identity) is not str:
                return False
            actual_queue_path, actual_queue_identity = _canonical_database_path(
                actual_queue_value
            )
            actual_journal_path, actual_journal_identity = _canonical_database_path(
                actual_journal_value
            )
        except (AttributeError, OSError, TypeError, ValueError):
            return False
        identities = {
            configuration_identity,
            queue_identity,
            journal_identity,
            actual_queue_identity,
            actual_journal_identity,
            bound_queue_identity,
            bound_journal_identity,
        }
        if len(identities) != 1:
            return False
        self._database_path = configuration_path
        self._database_identity = configuration_identity
        self._queue_database_path = queue_path
        self._journal_database_path = journal_path
        # Preserve the resolved actual paths for diagnostic inspection without
        # making either adapter's display spelling authoritative.
        self._actual_queue_database_path = actual_queue_path
        self._actual_journal_database_path = actual_journal_path
        return True

    def _instant(self) -> tuple[datetime, str]:
        value = self._clock()
        if type(value) is not datetime:
            raise TypeError("clock")
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("clock")
        utc = value.astimezone(timezone.utc)
        return utc, _format_queue_timestamp(utc)

    def _monotonic(self) -> float:
        value = self._monotonic_clock()
        if type(value) is not float:
            raise TypeError("monotonic_clock")
        if not math.isfinite(value):
            raise ValueError("monotonic_clock")
        if self._last_monotonic is not None and value < self._last_monotonic:
            raise ValueError("monotonic_clock")
        self._last_monotonic = value
        return value

    def _stale_before(self, instant: datetime) -> str:
        return _format_queue_timestamp(
            instant
            - timedelta(
                seconds=self._configuration.heartbeat_stale_after_seconds
            )
        )

    def _active_session(self, instant: datetime) -> WorkerSession | None:
        if self._session is None:
            return None
        result = self._journal.get_session(session_id=self._session.session_id)
        if (
            result.code is not WorkerJournalResultCode.FOUND
            or result.session is None
        ):
            return None
        session = result.session
        if (
            session.worker_id != self._configuration.worker_id
            or session.queue_owner_id != self._session.queue_owner_id
            or session.state
            not in {WorkerSessionState.ACTIVE, WorkerSessionState.STOPPING}
            or session.last_heartbeat_at <= self._stale_before(instant)
        ):
            return None
        self._session = session
        return session

    def _heartbeat_if_due(self, instant: datetime, monotonic: float) -> bool:
        session = self._active_session(instant)
        if session is None:
            return False
        if (
            self._last_heartbeat_monotonic is not None
            and monotonic - self._last_heartbeat_monotonic
            < self._configuration.heartbeat_interval_seconds
        ):
            return True
        result = self._journal.heartbeat(
            session_id=session.session_id,
            expected_state=session.state,
            expected_state_version=session.state_version,
            observed_at=_format_queue_timestamp(instant),
            stale_before=self._stale_before(instant),
        )
        if result.code is not WorkerJournalResultCode.APPLIED or result.session is None:
            return False
        self._session = result.session
        self._last_heartbeat_monotonic = monotonic
        return True

    def _transition_session(
        self, next_state: WorkerSessionState, reason_code: str
    ) -> WorkerJournalResult:
        if self._session is None:
            return WorkerJournalResult(WorkerJournalResultCode.NOT_FOUND)
        instant, observed_at = self._instant()
        result = self._journal.transition_session(
            session_id=self._session.session_id,
            expected_state=self._session.state,
            expected_state_version=self._session.state_version,
            next_state=next_state,
            reason_code=reason_code,
            observed_at=observed_at,
            stale_before=self._stale_before(instant),
        )
        if result.session is not None:
            self._session = result.session
        return result

    def _fail_session(self, reason_code: str) -> None:
        if self._session is None or self._session.state not in {
            WorkerSessionState.ACTIVE,
            WorkerSessionState.STOPPING,
        }:
            return
        try:
            self._transition_session(WorkerSessionState.FAILED, reason_code)
        except (RepositoryError, TypeError, ValueError):
            return

    @staticmethod
    def _startup_status_for_queue(command: WorkflowCommand) -> WorkerStartupStatus:
        if command.state is WorkflowCommandState.RUNNING:
            if command.cancellation_requested_at is not None:
                return WorkerStartupStatus.CANCELLATION_OWNER_RECONCILIATION_REQUIRED
            return WorkerStartupStatus.AMBIGUOUS_RUNNING
        return WorkerStartupStatus.JOURNAL_CONFLICT

    @staticmethod
    def _startup_infrastructure_result(
        code: WorkerJournalResultCode,
    ) -> WorkerStartupResult | None:
        if code is WorkerJournalResultCode.DATABASE_MISMATCH:
            return WorkerStartupResult(
                WorkerStartupStatus.DATABASE_MISMATCH,
                detail_code=code.value,
            )
        if code is WorkerJournalResultCode.TRANSIENT_CONTENTION:
            return WorkerStartupResult(
                WorkerStartupStatus.PERSISTENCE_FAILURE,
                detail_code=code.value,
            )
        return None

    def _receipt_matches_command(
        self,
        receipt: WorkerOperationReceipt,
        command: WorkflowCommand,
    ) -> bool:
        return (
            receipt.command_id == command.command_id
            and receipt.project_id == command.project_id
            and receipt.development_run_id == command.development_run_id
            and receipt.phase_id == command.phase_id
            and receipt.claim_count == command.claim_count
            and receipt.worker_id == self._configuration.worker_id
            and (
                command.state
                not in {
                    WorkflowCommandState.CLAIMED,
                    WorkflowCommandState.RUNNING,
                }
                or command.lease_owner == receipt.queue_owner_id
            )
        )

    @staticmethod
    def _reconciled_receipt_state(
        command: WorkflowCommand,
    ) -> WorkerOperationState | None:
        return {
            WorkflowCommandState.PENDING: WorkerOperationState.CLAIM_RELEASED,
            WorkflowCommandState.FAILED: WorkerOperationState.FAILED,
            WorkflowCommandState.CANCELLED: WorkerOperationState.CANCELLED,
        }.get(command.state)

    def _startup_block(
        self, status: WorkerStartupStatus, detail_code: str
    ) -> WorkerStartupResult:
        self._fail_session(WorkerFailureCode.RECONCILIATION_REQUIRED.value)
        return WorkerStartupResult(status, detail_code=detail_code)

    def _startup_block_for_journal_result(
        self, code: WorkerJournalResultCode
    ) -> WorkerStartupResult:
        infrastructure = self._startup_infrastructure_result(code)
        status = (
            WorkerStartupStatus.JOURNAL_CONFLICT
            if infrastructure is None
            else infrastructure.status
        )
        return self._startup_block(status, code.value)

    def _startup_block_for_queue_result(
        self, code: QueueResultCode
    ) -> WorkerStartupResult:
        status = (
            WorkerStartupStatus.PERSISTENCE_FAILURE
            if code is QueueResultCode.TRANSIENT_CONTENTION
            else WorkerStartupStatus.JOURNAL_CONFLICT
        )
        return self._startup_block(status, code.value)

    def _recover_startup(
        self,
        receipt_facts: tuple[
            tuple[WorkerOperationReceipt, QueueResult], ...
        ],
        command_facts: tuple[
            tuple[WorkflowCommand, WorkerJournalResult], ...
        ],
    ) -> tuple[WorkerStartupResult | None, int, int]:
        """Route one pre-authority, strictly decoded startup snapshot."""

        recovered = 0
        mirrored = 0
        for receipt, queue_result in receipt_facts:
            command = queue_result.command
            assert command is not None
            if not self._receipt_matches_command(receipt, command):
                return (
                    self._startup_block(
                        WorkerStartupStatus.JOURNAL_CONFLICT,
                        "RECEIPT_COMMAND_IDENTITY_MISMATCH",
                    ),
                    recovered,
                    mirrored,
                )
            _, now = self._instant()
            if receipt.state is WorkerOperationState.PREPARED and command.state in {
                WorkflowCommandState.FAILED,
                WorkflowCommandState.CANCELLED,
                WorkflowCommandState.PENDING,
            }:
                reconciliation = self._journal.reconcile_operation(
                    operation_id=receipt.operation_id,
                    expected_state=receipt.state,
                    expected_state_version=receipt.state_version,
                    occurred_at=now,
                )
                expected_reconciled = self._reconciled_receipt_state(command)
                if (
                    reconciliation.code
                    in {
                        WorkerJournalResultCode.APPLIED,
                        WorkerJournalResultCode.TERMINAL_OBSERVED,
                    }
                    and reconciliation.operation is not None
                    and reconciliation.operation.state is expected_reconciled
                    and self._receipt_matches_command(
                        reconciliation.operation, command
                    )
                ):
                    applied = int(
                        reconciliation.code is WorkerJournalResultCode.APPLIED
                    )
                    if command.state is WorkflowCommandState.PENDING:
                        recovered += applied
                    else:
                        mirrored += applied
                    continue
                return (
                    self._startup_block_for_journal_result(
                        reconciliation.code
                    ),
                    recovered,
                    mirrored,
                )
            if command.state is WorkflowCommandState.CLAIMED:
                if command.lease_expires_at is None or now < command.lease_expires_at:
                    return (
                        self._startup_block(
                            WorkerStartupStatus.CLAIM_LEASE_ACTIVE_RECONCILIATION_REQUIRED,
                            "LIVE_OLD_CLAIM",
                        ),
                        recovered,
                        mirrored,
                    )
                recovery = self._queue_service.recover_expired_claim(
                    command_id=command.command_id,
                    expected_state=command.state,
                    expected_state_version=command.state_version,
                    recovery_actor=self._session.queue_owner_id,  # type: ignore[union-attr]
                )
                if recovery.code is not QueueResultCode.APPLIED:
                    return (
                        self._startup_block_for_queue_result(recovery.code),
                        recovered,
                        mirrored,
                    )
                if recovery.command is None:
                    return (
                        self._startup_block(
                            WorkerStartupStatus.JOURNAL_CONFLICT,
                            "RECOVERY_COMMAND_MISSING",
                        ),
                        recovered,
                        mirrored,
                    )
                recovered += 1
                recovered_command = recovery.command
                reconciliation = self._journal.reconcile_operation(
                    operation_id=receipt.operation_id,
                    expected_state=receipt.state,
                    expected_state_version=receipt.state_version,
                    occurred_at=self._instant()[1],
                )
                expected_reconciled = self._reconciled_receipt_state(
                    recovered_command
                )
                if (
                    reconciliation.code
                    not in {
                        WorkerJournalResultCode.APPLIED,
                        WorkerJournalResultCode.TERMINAL_OBSERVED,
                    }
                    or reconciliation.operation is None
                    or reconciliation.operation.state is not expected_reconciled
                    or not self._receipt_matches_command(
                        reconciliation.operation, recovered_command
                    )
                ):
                    return (
                        self._startup_block_for_journal_result(
                            reconciliation.code
                        ),
                        recovered,
                        mirrored,
                    )
                continue
            if command.state is WorkflowCommandState.RUNNING:
                return (
                    self._startup_block(
                        self._startup_status_for_queue(command),
                        "OLD_RUNNING_CLAIM",
                    ),
                    recovered,
                    mirrored,
                )
            if command.state in _TERMINAL_QUEUE_STATES:
                if (
                    command.state is WorkflowCommandState.CANCELLED
                    and receipt.state is WorkerOperationState.RUNNING
                ):
                    observed = self._journal.transition_operation(
                        operation_id=receipt.operation_id,
                        expected_state=receipt.state,
                        expected_state_version=receipt.state_version,
                        next_state=WorkerOperationState.CANCELLATION_OBSERVED,
                        reconciliation_status=WorkerReconciliationStatus.NOT_REQUIRED,
                        durable_failure_code=WorkerFailureCode.CANCELLATION_OBSERVED.value,
                        diagnostic_detail=None,
                        occurred_at=now,
                        stale_before=self._stale_before(self._instant()[0]),
                    )
                    if (
                        observed.code is WorkerJournalResultCode.TERMINAL_OBSERVED
                        and observed.operation is not None
                        and observed.operation.state
                        is WorkerOperationState.CANCELLED
                        and self._receipt_matches_command(
                            observed.operation, command
                        )
                    ):
                        mirrored += 1
                        continue
                    if (
                        observed.code is not WorkerJournalResultCode.APPLIED
                        or observed.operation is None
                        or observed.operation.state
                        is not WorkerOperationState.CANCELLATION_OBSERVED
                    ):
                        return (
                            self._startup_block_for_journal_result(
                                observed.code
                            ),
                            recovered,
                            mirrored,
                        )
                    receipt = observed.operation
                destination = {
                    WorkflowCommandState.SUCCEEDED: WorkerOperationState.SUCCEEDED,
                    WorkflowCommandState.FAILED: WorkerOperationState.FAILED,
                    WorkflowCommandState.CANCELLED: WorkerOperationState.CANCELLED,
                }[command.state]
                code = receipt.durable_failure_code
                diagnostic = receipt.diagnostic_detail
                if destination is WorkerOperationState.CANCELLED:
                    code = WorkerFailureCode.CANCELLATION_OBSERVED.value
                    diagnostic = None
                transition = self._journal.transition_operation(
                    operation_id=receipt.operation_id,
                    expected_state=receipt.state,
                    expected_state_version=receipt.state_version,
                    next_state=destination,
                    reconciliation_status=WorkerReconciliationStatus.NOT_REQUIRED,
                    durable_failure_code=code,
                    diagnostic_detail=diagnostic,
                    occurred_at=now,
                    stale_before=self._stale_before(self._instant()[0]),
                )
                if (
                    transition.code
                    in {
                        WorkerJournalResultCode.APPLIED,
                        WorkerJournalResultCode.TERMINAL_OBSERVED,
                    }
                    and transition.operation is not None
                    and transition.operation.state is destination
                    and self._receipt_matches_command(
                        transition.operation, command
                    )
                ):
                    mirrored += int(transition.code is WorkerJournalResultCode.APPLIED)
                    continue
            return (
                self._startup_block(
                    WorkerStartupStatus.JOURNAL_CONFLICT,
                    "UNRESOLVED_STARTUP_FACTS",
                ),
                recovered,
                mirrored,
            )
        for command, receipt_result in command_facts:
            if (
                receipt_result.code is WorkerJournalResultCode.FOUND
                and receipt_result.operation is not None
                and self._receipt_matches_command(
                    receipt_result.operation, command
                )
                and command.state in _TERMINAL_QUEUE_STATES
                and receipt_result.operation.state
                is {
                    WorkflowCommandState.SUCCEEDED: WorkerOperationState.SUCCEEDED,
                    WorkflowCommandState.FAILED: WorkerOperationState.FAILED,
                    WorkflowCommandState.CANCELLED: WorkerOperationState.CANCELLED,
                }[command.state]
            ):
                mirrored += 1
                continue
            if (
                receipt_result.code is WorkerJournalResultCode.FOUND
                and receipt_result.operation is not None
                and self._receipt_matches_command(
                    receipt_result.operation, command
                )
                and command.state is WorkflowCommandState.PENDING
                and receipt_result.operation.state
                in {
                    WorkerOperationState.CLAIM_RELEASED,
                    WorkerOperationState.LEASE_LOST,
                }
            ):
                recovered += 1
                continue
            if receipt_result.code is not WorkerJournalResultCode.NOT_FOUND:
                return (
                    self._startup_block_for_journal_result(
                        receipt_result.code
                    ),
                    recovered,
                    mirrored,
                )
            _, now = self._instant()
            if command.state is WorkflowCommandState.CLAIMED:
                if command.lease_expires_at is None or now < command.lease_expires_at:
                    return (
                        self._startup_block(
                            WorkerStartupStatus.CLAIM_LEASE_ACTIVE_RECONCILIATION_REQUIRED,
                            "LIVE_OLD_CLAIM",
                        ),
                        recovered,
                        mirrored,
                    )
                recovery = self._queue_service.recover_expired_claim(
                    command_id=command.command_id,
                    expected_state=command.state,
                    expected_state_version=command.state_version,
                    recovery_actor=self._session.queue_owner_id,  # type: ignore[union-attr]
                )
                if recovery.code is not QueueResultCode.APPLIED:
                    return (
                        self._startup_block_for_queue_result(recovery.code),
                        recovered,
                        mirrored,
                    )
                recovered += 1
                continue
            return (
                self._startup_block(
                    self._startup_status_for_queue(command),
                    "OLD_RUNNING_CLAIM",
                ),
                recovered,
                mirrored,
            )
        return None, recovered, mirrored

    def _preflight_startup_snapshot(
        self,
        receipt_facts: tuple[
            tuple[WorkerOperationReceipt, QueueResult], ...
        ],
        command_facts: tuple[
            tuple[WorkflowCommand, WorkerJournalResult], ...
        ],
        now: str,
    ) -> WorkerStartupResult | None:
        """Classify terminal stage-6 facts before new session authority."""

        mirror_sources = {
            WorkflowCommandState.SUCCEEDED: frozenset(
                {WorkerOperationState.RESULT_SUCCEEDED}
            ),
            WorkflowCommandState.FAILED: frozenset(
                {
                    WorkerOperationState.PREPARED,
                    WorkerOperationState.RESULT_FAILED,
                }
            ),
            WorkflowCommandState.CANCELLED: frozenset(
                {
                    WorkerOperationState.PREPARED,
                    WorkerOperationState.RUNNING,
                    WorkerOperationState.RESULT_SUCCEEDED,
                    WorkerOperationState.RESULT_FAILED,
                    WorkerOperationState.CANCELLATION_OBSERVED,
                }
            ),
        }
        for receipt, queue_result in receipt_facts:
            if (
                queue_result.code is not QueueResultCode.FOUND
                or queue_result.command is None
            ):
                return WorkerStartupResult(
                    WorkerStartupStatus.JOURNAL_CONFLICT,
                    detail_code=queue_result.code.value,
                )
            command = queue_result.command
            if not self._receipt_matches_command(receipt, command):
                return WorkerStartupResult(
                    WorkerStartupStatus.JOURNAL_CONFLICT,
                    detail_code="RECEIPT_COMMAND_IDENTITY_MISMATCH",
                )
            if command.state is WorkflowCommandState.RUNNING:
                return WorkerStartupResult(
                    self._startup_status_for_queue(command),
                    detail_code="OLD_RUNNING_CLAIM",
                )
            if command.state is WorkflowCommandState.CLAIMED:
                if receipt.state is not WorkerOperationState.PREPARED:
                    return WorkerStartupResult(
                        WorkerStartupStatus.JOURNAL_CONFLICT,
                        detail_code="UNRESOLVED_STARTUP_FACTS",
                    )
                if command.lease_expires_at is None or now < command.lease_expires_at:
                    return WorkerStartupResult(
                        WorkerStartupStatus.CLAIM_LEASE_ACTIVE_RECONCILIATION_REQUIRED,
                        detail_code="LIVE_OLD_CLAIM",
                    )
                continue
            if command.state is WorkflowCommandState.PENDING:
                if receipt.state is WorkerOperationState.PREPARED:
                    continue
                return WorkerStartupResult(
                    WorkerStartupStatus.JOURNAL_CONFLICT,
                    detail_code="UNRESOLVED_STARTUP_FACTS",
                )
            if receipt.state not in mirror_sources[command.state]:
                return WorkerStartupResult(
                    WorkerStartupStatus.JOURNAL_CONFLICT,
                    detail_code="UNRESOLVED_STARTUP_FACTS",
                )
        for command, receipt_result in command_facts:
            if receipt_result.code is WorkerJournalResultCode.FOUND:
                operation = receipt_result.operation
                if (
                    operation is not None
                    and self._receipt_matches_command(operation, command)
                    and command.state in _TERMINAL_QUEUE_STATES
                    and operation.state
                    is {
                        WorkflowCommandState.SUCCEEDED: WorkerOperationState.SUCCEEDED,
                        WorkflowCommandState.FAILED: WorkerOperationState.FAILED,
                        WorkflowCommandState.CANCELLED: WorkerOperationState.CANCELLED,
                    }[command.state]
                ):
                    continue
                if (
                    operation is not None
                    and self._receipt_matches_command(operation, command)
                    and command.state is WorkflowCommandState.PENDING
                    and operation.state
                    in {
                        WorkerOperationState.CLAIM_RELEASED,
                        WorkerOperationState.LEASE_LOST,
                    }
                ):
                    continue
                return WorkerStartupResult(
                    WorkerStartupStatus.JOURNAL_CONFLICT,
                    detail_code=receipt_result.code.value,
                )
            if receipt_result.code is not WorkerJournalResultCode.NOT_FOUND:
                return WorkerStartupResult(
                    WorkerStartupStatus.JOURNAL_CONFLICT,
                    detail_code=receipt_result.code.value,
                )
            if command.state is WorkflowCommandState.CLAIMED:
                if command.lease_expires_at is None or now < command.lease_expires_at:
                    return WorkerStartupResult(
                        WorkerStartupStatus.CLAIM_LEASE_ACTIVE_RECONCILIATION_REQUIRED,
                        detail_code="LIVE_OLD_CLAIM",
                    )
                continue
            if command.state is WorkflowCommandState.RUNNING:
                return WorkerStartupResult(
                    self._startup_status_for_queue(command),
                    detail_code="OLD_RUNNING_CLAIM",
                )
            return WorkerStartupResult(
                WorkerStartupStatus.JOURNAL_CONFLICT,
                detail_code="MISSING_RECEIPT",
            )
        return None

    def start(self) -> WorkerStartupResult:
        # Startup precedence is intentionally explicit: configuration has
        # already been closed-domain validated by WorkerConfiguration, then the
        # frozen registry is checked before database identity or any I/O.
        try:
            keys = self._registry.keys()
        except (RuntimeError, TypeError, ValueError):
            return WorkerStartupResult(WorkerStartupStatus.HANDLER_REGISTRY_INVALID)
        if (
            self._registry._definition_registry is None
            or self._queue_service._registry
            is not self._registry._definition_registry
        ):
            return WorkerStartupResult(
                WorkerStartupStatus.HANDLER_REGISTRY_INVALID
            )
        if not keys:
            return WorkerStartupResult(WorkerStartupStatus.HANDLER_REGISTRY_EMPTY)
        if len(keys) > 256:
            return WorkerStartupResult(WorkerStartupStatus.HANDLER_REGISTRY_OVERSIZED)
        if not self._database_binding_matches():
            return WorkerStartupResult(WorkerStartupStatus.DATABASE_MISMATCH)
        try:
            _, applied_at = self._instant()
            self._migration_initializer(self._database_path, applied_at)
        except MigrationError:
            return WorkerStartupResult(WorkerStartupStatus.MIGRATION_FAILURE)
        except (RepositoryError, OSError, TypeError, ValueError):
            return WorkerStartupResult(WorkerStartupStatus.PERSISTENCE_FAILURE)
        try:
            instant, started_at = self._instant()
            stale_before = self._stale_before(instant)
            inspect_startup_session = getattr(
                self._journal, "_inspect_startup_session", None
            )
            list_startup_terminal_commands = getattr(
                self._journal, "_list_startup_terminal_commands", None
            )
            if not callable(inspect_startup_session) or not callable(
                list_startup_terminal_commands
            ):
                return WorkerStartupResult(WorkerStartupStatus.PERSISTENCE_FAILURE)
            session_preflight = inspect_startup_session(
                worker_id=self._configuration.worker_id,
                stale_before=stale_before,
            )
            if session_preflight.code not in {
                WorkerJournalResultCode.NOT_FOUND,
                WorkerJournalResultCode.ACTIVE_SESSION_EXISTS,
                WorkerJournalResultCode.STALE_SESSION_RECONCILIATION_REQUIRED,
            }:
                infrastructure = self._startup_infrastructure_result(
                    session_preflight.code
                )
                return infrastructure or WorkerStartupResult(
                    WorkerStartupStatus.PERSISTENCE_FAILURE,
                    detail_code=session_preflight.code.value,
                )
            # Both lists strictly decode all identity-relevant rows.  They are
            # obtained before start_session and retained as the sole recovery
            # snapshot, so malformed persistence cannot create session
            # authority or trigger queue/history/receipt writes (C28).
            operations_result = self._journal.list_nonterminal_operations(
                worker_id=self._configuration.worker_id
            )
            if operations_result.code is not WorkerJournalResultCode.LISTED:
                infrastructure = self._startup_infrastructure_result(
                    operations_result.code
                )
                return infrastructure or WorkerStartupResult(
                    WorkerStartupStatus.PERSISTENCE_FAILURE,
                    detail_code=operations_result.code.value,
                )
            commands_result = self._journal.list_owned_commands(
                worker_id=self._configuration.worker_id
            )
            if commands_result.code is not WorkerJournalResultCode.LISTED:
                infrastructure = self._startup_infrastructure_result(
                    commands_result.code
                )
                return infrastructure or WorkerStartupResult(
                    WorkerStartupStatus.PERSISTENCE_FAILURE,
                    detail_code=commands_result.code.value,
                )
            terminal_commands_result = list_startup_terminal_commands(
                worker_id=self._configuration.worker_id
            )
            if (
                terminal_commands_result.code
                is not WorkerJournalResultCode.LISTED
            ):
                infrastructure = self._startup_infrastructure_result(
                    terminal_commands_result.code
                )
                return infrastructure or WorkerStartupResult(
                    WorkerStartupStatus.PERSISTENCE_FAILURE,
                    detail_code=terminal_commands_result.code.value,
                )
            receipt_facts: list[
                tuple[WorkerOperationReceipt, QueueResult]
            ] = []
            for receipt in operations_result.operations:
                queue_result = self._queue_service.get(receipt.command_id)
                if queue_result.code is QueueResultCode.TRANSIENT_CONTENTION:
                    return WorkerStartupResult(
                        WorkerStartupStatus.PERSISTENCE_FAILURE,
                        detail_code=queue_result.code.value,
                    )
                receipt_facts.append((receipt, queue_result))
            known_claims = {
                (receipt.command_id, receipt.claim_count)
                for receipt, _queue_result in receipt_facts
            }
            command_facts: list[
                tuple[WorkflowCommand, WorkerJournalResult]
            ] = []
            startup_commands = tuple(
                {
                    (command.command_id, command.claim_count): command
                    for command in (
                        *commands_result.commands,
                        *terminal_commands_result.commands,
                    )
                }.values()
            )
            for command in startup_commands:
                if (command.command_id, command.claim_count) in known_claims:
                    continue
                receipt_result = self._journal.get_operation_for_claim(
                    command_id=command.command_id,
                    claim_count=command.claim_count,
                )
                infrastructure = self._startup_infrastructure_result(
                    receipt_result.code
                )
                if infrastructure is not None:
                    return infrastructure
                command_facts.append((command, receipt_result))
            # Stage-4 persistence/decode preflight above is complete and
            # read-only.  Classify the retained stage-5 session fact before
            # any stage-6 command/receipt semantic result or new authority.
            if session_preflight.code is WorkerJournalResultCode.ACTIVE_SESSION_EXISTS:
                return WorkerStartupResult(
                    WorkerStartupStatus.ACTIVE_SESSION_EXISTS,
                    session=session_preflight.session,
                    detail_code=session_preflight.code.value,
                )
            if (
                session_preflight.code
                is WorkerJournalResultCode.STALE_SESSION_RECONCILIATION_REQUIRED
            ):
                return WorkerStartupResult(
                    WorkerStartupStatus.STALE_SESSION_RECONCILIATION_REQUIRED,
                    session=session_preflight.session,
                    detail_code=session_preflight.code.value,
                )
            blocked = self._preflight_startup_snapshot(
                tuple(receipt_facts), tuple(command_facts), started_at
            )
            if blocked is not None:
                return blocked
            session_id = _worker_uuid(
                self._session_id_factory(), "session_id"
            )
            worker_id = _worker_id(self._configuration.worker_id)
            owner = _worker_queue_owner(
                f"{worker_id}@{session_id}", worker_id, session_id
            )
            result = self._journal.start_session(
                session_id=session_id,
                worker_id=worker_id,
                queue_owner_id=owner,
                started_at=started_at,
                stale_before=stale_before,
            )
            if result.code is WorkerJournalResultCode.ACTIVE_SESSION_EXISTS:
                return WorkerStartupResult(
                    WorkerStartupStatus.ACTIVE_SESSION_EXISTS,
                    session=result.session,
                    detail_code=result.code.value,
                )
            if (
                result.code
                is WorkerJournalResultCode.STALE_SESSION_RECONCILIATION_REQUIRED
            ):
                return WorkerStartupResult(
                    WorkerStartupStatus.STALE_SESSION_RECONCILIATION_REQUIRED,
                    session=result.session,
                    detail_code=result.code.value,
                )
            if result.code is not WorkerJournalResultCode.APPLIED or result.session is None:
                infrastructure = self._startup_infrastructure_result(result.code)
                return infrastructure or WorkerStartupResult(
                    WorkerStartupStatus.JOURNAL_CONFLICT,
                    detail_code=result.code.value,
                )
            self._session = result.session
            monotonic = self._monotonic()
            self._last_heartbeat_monotonic = monotonic
            blocked, recovered, mirrored = self._recover_startup(
                tuple(receipt_facts), tuple(command_facts)
            )
            if blocked is not None:
                return blocked
            status = WorkerStartupStatus.STARTED_NO_RECOVERY
            if recovered and mirrored:
                status = WorkerStartupStatus.STARTED_AFTER_MIXED_RECOVERY
            elif recovered:
                status = WorkerStartupStatus.STARTED_AFTER_CLAIM_RECOVERY
            elif mirrored:
                status = WorkerStartupStatus.STARTED_AFTER_TERMINAL_MIRRORING
            return WorkerStartupResult(
                status,
                session=self._session,
                recovered_claim_count=recovered,
                mirrored_receipt_count=mirrored,
            )
        except RepositoryError:
            self._fail_session(WorkerFailureCode.WORKER_REPOSITORY_FAILURE.value)
            return WorkerStartupResult(WorkerStartupStatus.PERSISTENCE_FAILURE)
        except (TypeError, ValueError, OSError):
            self._fail_session(WorkerFailureCode.WORKER_REPOSITORY_FAILURE.value)
            return WorkerStartupResult(WorkerStartupStatus.PERSISTENCE_FAILURE)

    def _transition_operation(
        self,
        receipt: WorkerOperationReceipt,
        next_state: WorkerOperationState,
        failure_code: WorkerFailureCode | None,
        diagnostic_detail: str | None = None,
    ) -> WorkerJournalResult:
        instant, occurred_at = self._instant()
        result = self._journal.transition_operation(
            operation_id=receipt.operation_id,
            expected_state=receipt.state,
            expected_state_version=receipt.state_version,
            next_state=next_state,
            reconciliation_status=(
                WorkerReconciliationStatus.REQUIRED
                if next_state in {
                    WorkerOperationState.LEASE_LOST,
                    WorkerOperationState.RECONCILIATION_REQUIRED,
                }
                else WorkerReconciliationStatus.NOT_REQUIRED
            ),
            durable_failure_code=(None if failure_code is None else failure_code.value),
            diagnostic_detail=diagnostic_detail,
            occurred_at=occurred_at,
            stale_before=self._stale_before(instant),
        )
        return result

    def _refresh_command(self, command_id: str) -> QueueResult:
        return self._queue_service.get(command_id)

    @staticmethod
    def _terminal_iteration(
        operation: WorkerOperationReceipt,
    ) -> WorkerIterationResult:
        if operation.state is WorkerOperationState.SUCCEEDED:
            status = WorkerIterationStatus.SUCCEEDED
        elif operation.state is WorkerOperationState.FAILED:
            status = WorkerIterationStatus.FAILED
        elif operation.state is WorkerOperationState.CANCELLED:
            status = WorkerIterationStatus.CANCELLED
        else:
            status = WorkerIterationStatus.RECONCILIATION_REQUIRED
        return WorkerIterationResult(
            status,
            operation.command_id,
            operation.operation_id,
            operation.durable_failure_code,
        )

    def _non_applied_transition_result(
        self,
        result: WorkerJournalResult,
        receipt: WorkerOperationReceipt,
    ) -> WorkerIterationResult:
        """Route one failed transition without attempting another mutation."""

        if (
            result.code is WorkerJournalResultCode.TERMINAL_OBSERVED
            and result.operation is not None
        ):
            observed = result.operation
            queue_result = self._refresh_command(observed.command_id)
            if (
                queue_result.code is QueueResultCode.FOUND
                and queue_result.command is not None
            ):
                if queue_result.command.state in _TERMINAL_QUEUE_STATES:
                    return self._mirror_terminal(observed, queue_result.command)
                return self._terminal_conflict_result(observed)
            return WorkerIterationResult(
                WorkerIterationStatus.RECONCILIATION_REQUIRED,
                observed.command_id,
                observed.operation_id,
                queue_result.code.value,
            )
        return WorkerIterationResult(
            WorkerIterationStatus.RECONCILIATION_REQUIRED,
            receipt.command_id,
            receipt.operation_id,
            result.code.value,
        )

    def _reconciliation_result(
        self,
        receipt: WorkerOperationReceipt,
        failure_code: WorkerFailureCode,
        diagnostic_detail: str | None = None,
    ) -> WorkerIterationResult:
        durable_code = (
            WorkerFailureCode.RECONCILIATION_REQUIRED
            if receipt.state is WorkerOperationState.PREPARED
            else failure_code
        )
        transition = self._transition_operation(
            receipt,
            WorkerOperationState.RECONCILIATION_REQUIRED,
            durable_code,
            diagnostic_detail,
        )
        if transition.code is not WorkerJournalResultCode.APPLIED:
            return self._non_applied_transition_result(transition, receipt)
        return WorkerIterationResult(
            WorkerIterationStatus.RECONCILIATION_REQUIRED,
            receipt.command_id,
            receipt.operation_id,
            failure_code.value,
        )

    def _lease_lost_result(
        self, receipt: WorkerOperationReceipt
    ) -> WorkerIterationResult:
        transition = self._transition_operation(
            receipt,
            WorkerOperationState.LEASE_LOST,
            WorkerFailureCode.LEASE_LOST,
        )
        if transition.code is not WorkerJournalResultCode.APPLIED:
            return self._non_applied_transition_result(transition, receipt)
        return WorkerIterationResult(
            WorkerIterationStatus.RECONCILIATION_REQUIRED,
            receipt.command_id,
            receipt.operation_id,
            WorkerFailureCode.LEASE_LOST.value,
        )

    def _terminal_conflict_result(
        self,
        receipt: WorkerOperationReceipt,
        *,
        transition_receipt: bool = True,
    ) -> WorkerIterationResult:
        if (
            receipt.state in _TERMINAL_RECEIPT_STATES
            or not transition_receipt
        ):
            result = WorkerIterationResult(
                WorkerIterationStatus.RECONCILIATION_REQUIRED,
                receipt.command_id,
                receipt.operation_id,
                WorkerFailureCode.RECONCILIATION_REQUIRED.value,
            )
        else:
            result = self._reconciliation_result(
                receipt, WorkerFailureCode.RECONCILIATION_REQUIRED
            )
        if (
            self._session is not None
            and self._session.state
            in {WorkerSessionState.ACTIVE, WorkerSessionState.STOPPING}
            and receipt.worker_id == self._session.worker_id
            and receipt.session_id == self._session.session_id
            and receipt.queue_owner_id == self._session.queue_owner_id
        ):
            self._fail_session(WorkerFailureCode.RECONCILIATION_REQUIRED.value)
        return result

    def _mirror_terminal(
        self, receipt: WorkerOperationReceipt, command: WorkflowCommand
    ) -> WorkerIterationResult:
        destination = {
            WorkflowCommandState.SUCCEEDED: WorkerOperationState.SUCCEEDED,
            WorkflowCommandState.FAILED: WorkerOperationState.FAILED,
            WorkflowCommandState.CANCELLED: WorkerOperationState.CANCELLED,
        }.get(command.state)
        if destination is None:
            return self._reconciliation_result(
                receipt, WorkerFailureCode.RECONCILIATION_REQUIRED
            )
        if receipt.state in _TERMINAL_RECEIPT_STATES:
            if receipt.state is destination:
                return self._terminal_iteration(receipt)
            # A terminal cross-observation is evidence, never authority for a
            # second receipt mutation or for reporting a mismatched success.
            return self._terminal_conflict_result(receipt)
        if (
            receipt.state is WorkerOperationState.PREPARED
            and command.state
            in {WorkflowCommandState.FAILED, WorkflowCommandState.CANCELLED}
        ):
            reconciliation = self._journal.reconcile_operation(
                operation_id=receipt.operation_id,
                expected_state=receipt.state,
                expected_state_version=receipt.state_version,
                occurred_at=self._instant()[1],
            )
            if (
                reconciliation.code
                in {
                    WorkerJournalResultCode.APPLIED,
                    WorkerJournalResultCode.TERMINAL_OBSERVED,
                }
                and reconciliation.operation is not None
                and reconciliation.operation.state is destination
            ):
                return self._terminal_iteration(reconciliation.operation)
            if (
                reconciliation.code is WorkerJournalResultCode.TERMINAL_OBSERVED
                and reconciliation.operation is not None
            ):
                return self._terminal_conflict_result(reconciliation.operation)
            return self._non_applied_transition_result(reconciliation, receipt)
        if (
            receipt.state is WorkerOperationState.RUNNING
            and command.state is WorkflowCommandState.CANCELLED
        ):
            observed = self._transition_operation(
                receipt,
                WorkerOperationState.CANCELLATION_OBSERVED,
                WorkerFailureCode.CANCELLATION_OBSERVED,
            )
            if (
                observed.code is WorkerJournalResultCode.APPLIED
                and observed.operation is not None
            ):
                receipt = observed.operation
            elif (
                observed.code is WorkerJournalResultCode.TERMINAL_OBSERVED
                and observed.operation is not None
                and observed.operation.state is WorkerOperationState.CANCELLED
            ):
                return self._terminal_iteration(observed.operation)
            else:
                return self._non_applied_transition_result(observed, receipt)
        compatible_sources = {
            WorkerOperationState.SUCCEEDED: frozenset(
                {WorkerOperationState.RESULT_SUCCEEDED}
            ),
            WorkerOperationState.FAILED: frozenset(
                {WorkerOperationState.RESULT_FAILED}
            ),
            WorkerOperationState.CANCELLED: frozenset(
                {
                    WorkerOperationState.RESULT_SUCCEEDED,
                    WorkerOperationState.RESULT_FAILED,
                    WorkerOperationState.CANCELLATION_OBSERVED,
                }
            ),
        }
        if receipt.state not in compatible_sources[destination]:
            # The terminal queue row is evidence, but an incompatible receipt
            # history grants no authority for another receipt mutation.  Route
            # the contradiction directly and fail the owned session without
            # recursively attempting RECONCILIATION_REQUIRED.
            return self._terminal_conflict_result(
                receipt, transition_receipt=False
            )
        if destination is WorkerOperationState.SUCCEEDED:
            status = WorkerIterationStatus.SUCCEEDED
            code = None
            diagnostic = None
        elif destination is WorkerOperationState.FAILED:
            status = WorkerIterationStatus.FAILED
            code = (
                WorkerFailureCode.RECONCILIATION_REQUIRED
                if receipt.durable_failure_code is None
                else WorkerFailureCode(receipt.durable_failure_code)
            )
            diagnostic = receipt.diagnostic_detail
        else:
            destination = WorkerOperationState.CANCELLED
            status = WorkerIterationStatus.CANCELLED
            code = WorkerFailureCode.CANCELLATION_OBSERVED
            diagnostic = None
        transition = self._transition_operation(
            receipt, destination, code, diagnostic
        )
        if transition.code is not WorkerJournalResultCode.APPLIED:
            if (
                transition.code is WorkerJournalResultCode.TERMINAL_OBSERVED
                and transition.operation is not None
                and transition.operation.state is not destination
            ):
                return self._terminal_conflict_result(transition.operation)
            return self._non_applied_transition_result(transition, receipt)
        if transition.operation is None:  # guarded by WorkerJournalResult shape
            return self._non_applied_transition_result(transition, receipt)
        return self._terminal_iteration(transition.operation)

    def _evaluate_bound_operation(
        self,
        fence: _BoundOperationFence,
        session_result: WorkerJournalResult,
        queue_result: QueueResult,
        receipt_result: WorkerJournalResult,
        instant: datetime,
        monotonic: float,
        *,
        allow_renewal: bool,
        expected_queue_state: WorkflowCommandState = WorkflowCommandState.RUNNING,
        expected_receipt_state: WorkerOperationState = WorkerOperationState.RUNNING,
        expected_receipt_state_version: int | None = None,
        handler_exception: BaseException | None = None,
        handler_result: WorkerHandlerResult | None = None,
    ) -> _BoundOperationDecision:
        """Apply the accepted EP01 through EP09 precedence in one place."""

        session = session_result.session
        command = queue_result.command
        operation = receipt_result.operation
        stale_before = self._stale_before(instant)
        if self._bound_fence is not fence:
            return _BoundOperationDecision(
                "EP01", session, command, operation, "FENCE_INVALIDATED"
            )
        if session_result.code is not WorkerJournalResultCode.FOUND:
            return _BoundOperationDecision(
                "EP01", session, command, operation, session_result.code.value
            )
        if session is None:
            return _BoundOperationDecision(
                "EP01", session, command, operation, "INVALID_SESSION_RESULT"
            )
        if receipt_result.code is not WorkerJournalResultCode.FOUND:
            return _BoundOperationDecision(
                "EP01", session, command, operation, receipt_result.code.value
            )
        if operation is None:
            return _BoundOperationDecision(
                "EP01", session, command, operation, "INVALID_RECEIPT_RESULT"
            )
        if (
            session.session_sequence != fence.session_sequence
            or session.session_id != fence.session_id
            or session.worker_id != fence.worker_id
            or session.queue_owner_id != fence.queue_owner_id
            or session.state not in {WorkerSessionState.ACTIVE, WorkerSessionState.STOPPING}
            or self._session is None
            or self._session.session_id != session.session_id
            or self._session.state is not session.state
            or self._session.state_version != session.state_version
            or (
                session.state_version != fence.session_state_version
                and not (
                    self._stop_accepted
                    and session.state is WorkerSessionState.STOPPING
                    and session.state_version == fence.session_state_version + 1
                )
            )
            or session.last_heartbeat_at <= stale_before
            or operation.operation_id != fence.operation_id
            or operation.command_id != fence.command_id
            or operation.worker_id != fence.worker_id
            or operation.session_id != fence.session_id
            or operation.queue_owner_id != fence.queue_owner_id
            or operation.claim_count != fence.claim_count
            or operation.precondition_state_version != fence.precondition_state_version
            or operation.state is not expected_receipt_state
            or operation.state_version
            != (
                fence.receipt_state_version
                if expected_receipt_state_version is None
                else expected_receipt_state_version
            )
        ):
            return _BoundOperationDecision(
                "EP01", session, command, operation, "FENCE_INVALIDATED"
            )
        if queue_result.code is not QueueResultCode.FOUND:
            return _BoundOperationDecision(
                "EP01", session, command, operation, queue_result.code.value
            )
        if command is None:
            return _BoundOperationDecision(
                "EP01", session, command, operation, "INVALID_QUEUE_RESULT"
            )
        if not self._receipt_matches_command(operation, command):
            return _BoundOperationDecision(
                "EP01",
                session,
                command,
                operation,
                "RECEIPT_COMMAND_IDENTITY_MISMATCH",
            )
        if command.state in _TERMINAL_QUEUE_STATES:
            return _BoundOperationDecision("EP02", session, command, operation)
        now = _format_queue_timestamp(instant)
        if (
            command.command_id != fence.command_id
            or command.state is not expected_queue_state
            or command.claim_count != fence.claim_count
            or command.state_version
            < fence.precondition_state_version
            + int(expected_queue_state is WorkflowCommandState.RUNNING)
            or command.lease_owner != fence.queue_owner_id
            or command.lease_expires_at is None
            or now >= command.lease_expires_at
        ):
            return _BoundOperationDecision("EP03", session, command, operation)
        if command.cancellation_requested_at is not None:
            return _BoundOperationDecision("EP04", session, command, operation)
        if self._stop_deadline is not None and monotonic >= self._stop_deadline:
            return _BoundOperationDecision("EP05", session, command, operation)
        expiry = _parse_queue_timestamp(command.lease_expires_at, "lease_expires_at")
        if allow_renewal and instant >= expiry - timedelta(
            seconds=self._configuration.lease_renew_margin_seconds
        ):
            return _BoundOperationDecision("EP06", session, command, operation)
        if not self._stop_accepted and self._stop_requested():
            return _BoundOperationDecision("EP07", session, command, operation)
        if handler_exception is not None:
            return _BoundOperationDecision("EP08", session, command, operation)
        if handler_result is not None:
            return _BoundOperationDecision("EP09", session, command, operation)
        return _BoundOperationDecision("CONTINUE", session, command, operation)

    def _fresh_bound_decision(
        self,
        fence: _BoundOperationFence,
        *,
        expected_queue_state: WorkflowCommandState = WorkflowCommandState.RUNNING,
        expected_receipt_state: WorkerOperationState = WorkerOperationState.RUNNING,
        expected_receipt_state_version: int | None = None,
        allow_renewal: bool,
        handler_exception: BaseException | None = None,
        handler_result: WorkerHandlerResult | None = None,
    ) -> tuple[_BoundOperationDecision, bool]:
        """Read once, perform at most one renewal CAS, and reread once on conflict."""

        session_result = self._journal.get_session(session_id=fence.session_id)
        queue_result = self._refresh_command(fence.command_id)
        receipt_result = self._journal.get_operation_for_claim(
            command_id=fence.command_id, claim_count=fence.claim_count
        )
        # The clocks are part of the authoritative decision and therefore are
        # sampled after the reads they qualify.  A slow read cannot dispatch or
        # mutate using a pre-read lease/grace observation.
        instant, _ = self._instant()
        monotonic = self._monotonic()
        decision = self._evaluate_bound_operation(
            fence,
            session_result,
            queue_result,
            receipt_result,
            instant,
            monotonic,
            allow_renewal=allow_renewal,
            expected_queue_state=expected_queue_state,
            expected_receipt_state=expected_receipt_state,
            expected_receipt_state_version=expected_receipt_state_version,
            handler_exception=handler_exception,
            handler_result=handler_result,
        )
        renewal_conflict = False
        if decision.precedence == "EP06" and decision.command is not None:
            renewal_base = decision.command
            renewal = self._queue_service.renew_lease(
                command_id=renewal_base.command_id,
                expected_state=renewal_base.state,
                expected_state_version=renewal_base.state_version,
                lease_owner=fence.queue_owner_id,
            )
            renewal_failed = (
                renewal.code is not QueueResultCode.APPLIED
                or renewal.command is None
            )
            if renewal_failed:
                # This is the sole authoritative conflict reread and restarts
                # at EP01 with fresh session, queue, receipt, and clock facts.
                session_result = self._journal.get_session(
                    session_id=fence.session_id
                )
                queue_result = self._refresh_command(fence.command_id)
                receipt_result = self._journal.get_operation_for_claim(
                    command_id=fence.command_id,
                    claim_count=fence.claim_count,
                )
                instant, _ = self._instant()
                monotonic = self._monotonic()
            else:
                # The CAS result is the authoritative queue fact, but a slow
                # successful renewal may cross a session, receipt, or grace
                # boundary.  Refresh those facts and the clocks before any
                # lower-precedence action.
                session_result = self._journal.get_session(
                    session_id=fence.session_id
                )
                receipt_result = self._journal.get_operation_for_claim(
                    command_id=fence.command_id,
                    claim_count=fence.claim_count,
                )
                queue_result = QueueResult(
                    QueueResultCode.FOUND, command=renewal.command
                )
                instant, _ = self._instant()
                monotonic = self._monotonic()
            decision = self._evaluate_bound_operation(
                fence,
                session_result,
                queue_result,
                receipt_result,
                instant,
                monotonic,
                allow_renewal=False,
                expected_queue_state=expected_queue_state,
                expected_receipt_state=expected_receipt_state,
                expected_receipt_state_version=expected_receipt_state_version,
                handler_exception=handler_exception,
                handler_result=handler_result,
            )
            renewal_conflict = (
                renewal_failed
                and decision.precedence in {"CONTINUE", "EP08", "EP09"}
                and decision.command is not None
                and decision.command.state_version == renewal_base.state_version
                and decision.command.lease_expires_at
                == renewal_base.lease_expires_at
            )
        if decision.precedence == "EP07":
            if not self._accept_stop(monotonic):
                return (
                    _BoundOperationDecision(
                        "EP01",
                        self._session,
                        decision.command,
                        decision.operation,
                        WorkerJournalResultCode.CAS_CONFLICT.value,
                    ),
                    renewal_conflict,
                )
            session_result = self._journal.get_session(
                session_id=fence.session_id
            )
            queue_result = self._refresh_command(fence.command_id)
            receipt_result = self._journal.get_operation_for_claim(
                command_id=fence.command_id,
                claim_count=fence.claim_count,
            )
            instant, _ = self._instant()
            monotonic = self._monotonic()
            decision = self._evaluate_bound_operation(
                fence,
                session_result,
                queue_result,
                receipt_result,
                instant,
                monotonic,
                allow_renewal=False,
                expected_queue_state=expected_queue_state,
                expected_receipt_state=expected_receipt_state,
                expected_receipt_state_version=expected_receipt_state_version,
                handler_exception=handler_exception,
                handler_result=handler_result,
            )
        return decision, renewal_conflict

    def _accept_stop(self, monotonic: float, reason_code: str = "PROCESS_STOP") -> bool:
        if self._stop_deadline is None:
            self._stop_deadline = monotonic + self._configuration.shutdown_grace_seconds
        if self._session is None:
            self._last_stop_transition_result = WorkerJournalResult(
                WorkerJournalResultCode.NOT_FOUND
            )
            return False
        if self._session.state is WorkerSessionState.STOPPING:
            self._last_stop_transition_result = WorkerJournalResult(
                WorkerJournalResultCode.FOUND, session=self._session
            )
            self._stop_accepted = True
            return True
        if self._session.state is not WorkerSessionState.ACTIVE:
            self._last_stop_transition_result = WorkerJournalResult(
                WorkerJournalResultCode.TERMINAL_OBSERVED,
                session=self._session,
            )
            return False
        if self._stop_transition_attempted:
            # A failed one-shot transition is itself the authoritative typed
            # result.  Do not replace it with a synthetic CAS result when
            # shutdown observes the same stop request.
            return False
        self._stop_transition_attempted = True
        transition = self._transition_session(WorkerSessionState.STOPPING, reason_code)
        self._last_stop_transition_result = transition
        accepted = (
            transition.code is WorkerJournalResultCode.APPLIED
            and transition.session is not None
        )
        self._stop_accepted = accepted
        return accepted

    def _request_cooperative_stop(self) -> bool:
        """Start grace at signal observation and durably enter STOPPING once."""

        try:
            accepted = self._accept_stop(self._monotonic())
        except (RepositoryError, OSError, TypeError, ValueError):
            accepted = False
        if not accepted:
            self._stop_bridge_failure = True
        return accepted

    def _observe_bound_stop(
        self, receipt: WorkerOperationReceipt
    ) -> WorkerIterationResult | None:
        """Durably accept a stop without abandoning the already-bound claim."""

        if not self._stop_requested():
            return None
        if self._accept_stop(self._monotonic()):
            return None
        return self._reconciliation_result(
            receipt, WorkerFailureCode.SESSION_FENCED
        )

    @staticmethod
    def _directive_for_decision(decision: _BoundOperationDecision) -> WorkerCheckpointDirective:
        return {
            "EP02": WorkerCheckpointDirective.STOP,
            "EP03": WorkerCheckpointDirective.LEASE_LOST,
            "EP04": WorkerCheckpointDirective.CANCEL,
            "EP07": WorkerCheckpointDirective.STOP,
            "CONTINUE": WorkerCheckpointDirective.CONTINUE,
        }.get(decision.precedence, WorkerCheckpointDirective.RECONCILIATION_REQUIRED)

    def _observe_checkpoint(
        self, fence: _BoundOperationFence
    ) -> WorkerCheckpointDirective:
        if self._bound_fence is not fence:
            return WorkerCheckpointDirective.RECONCILIATION_REQUIRED
        decision, renewal_conflict = self._fresh_bound_decision(
            fence,
            allow_renewal=True,
        )
        if renewal_conflict:
            # Latch the failed fence so an ignoring handler cannot perform a
            # second renewal attempt through another checkpoint or return.
            self._bound_fence = None
            return WorkerCheckpointDirective.RECONCILIATION_REQUIRED
        directive = self._directive_for_decision(decision)
        if (
            directive is WorkerCheckpointDirective.CONTINUE
            and self._stop_accepted
        ):
            return WorkerCheckpointDirective.STOP
        return directive

    def _before_handler(
        self,
        fence: _BoundOperationFence,
        receipt: WorkerOperationReceipt,
    ) -> WorkerIterationResult | None:
        """Apply fresh EP01--EP07 checks immediately before dispatch."""

        decision, renewal_conflict = self._fresh_bound_decision(
            fence,
            allow_renewal=True,
        )
        if renewal_conflict:
            return self._reconciliation_result(
                receipt, WorkerFailureCode.QUEUE_CAS_LOST
            )
        if decision.precedence == "EP01":
            return self._ep01_result(receipt, decision)
        if decision.precedence == "EP02":
            assert decision.command is not None and decision.operation is not None
            return self._mirror_terminal(decision.operation, decision.command)
        if decision.precedence == "EP03":
            return self._lease_lost_result(receipt)
        if decision.precedence == "EP04":
            assert decision.command is not None
            return self._cancel(fence, receipt, decision.command)
        if decision.precedence == "EP05":
            result = self._reconciliation_result(
                receipt, WorkerFailureCode.SHUTDOWN_GRACE_EXPIRED
            )
            self._fail_session(WorkerFailureCode.SHUTDOWN_GRACE_EXPIRED.value)
            return result
        if decision.precedence == "CONTINUE":
            return None
        return self._reconciliation_result(
            receipt, WorkerFailureCode.RECONCILIATION_REQUIRED
        )

    def _ep01_result(
        self,
        receipt: WorkerOperationReceipt,
        decision: _BoundOperationDecision,
    ) -> WorkerIterationResult:
        if decision.origin == "RECEIPT_COMMAND_IDENTITY_MISMATCH":
            return self._terminal_conflict_result(
                decision.operation or receipt
            )
        if (
            decision.command is not None
            and decision.command.state in _TERMINAL_QUEUE_STATES
            and decision.operation is not None
            and self._receipt_matches_command(
                decision.operation, decision.command
            )
        ):
            return self._mirror_terminal(decision.operation, decision.command)
        # A typed repository/read failure is stronger than any partial cached
        # fact.  In particular, a terminal receipt cannot prove a contradiction
        # when the authoritative queue read itself failed.
        if decision.origin not in {None, "FENCE_INVALIDATED"}:
            return WorkerIterationResult(
                WorkerIterationStatus.RECONCILIATION_REQUIRED,
                receipt.command_id,
                receipt.operation_id,
                decision.origin,
            )
        if (
            decision.operation is not None
            and decision.operation.state in _TERMINAL_RECEIPT_STATES
        ):
            return self._terminal_conflict_result(decision.operation)
        return self._reconciliation_result(
            receipt, WorkerFailureCode.SESSION_FENCED
        )

    def _authorize_terminal_queue_mutation(
        self,
        fence: _BoundOperationFence,
        receipt: WorkerOperationReceipt,
        *,
        expected_queue_state: WorkflowCommandState = WorkflowCommandState.RUNNING,
    ) -> tuple[_BoundOperationDecision | None, WorkerIterationResult | None]:
        """Apply fresh EP01--EP07 immediately before one terminal queue CAS."""

        decision, renewal_conflict = self._fresh_bound_decision(
            fence,
            expected_queue_state=expected_queue_state,
            expected_receipt_state=receipt.state,
            expected_receipt_state_version=receipt.state_version,
            allow_renewal=True,
        )
        if renewal_conflict:
            return None, self._reconciliation_result(
                receipt, WorkerFailureCode.QUEUE_CAS_LOST
            )
        if decision.precedence == "EP01":
            return None, self._ep01_result(receipt, decision)
        if decision.precedence == "EP02":
            assert decision.command is not None and decision.operation is not None
            return None, self._mirror_terminal(
                decision.operation, decision.command
            )
        if decision.precedence == "EP03":
            return None, self._lease_lost_result(receipt)
        if decision.precedence == "EP05":
            result = self._reconciliation_result(
                receipt, WorkerFailureCode.SHUTDOWN_GRACE_EXPIRED
            )
            self._fail_session(WorkerFailureCode.SHUTDOWN_GRACE_EXPIRED.value)
            return None, result
        if decision.precedence in {"CONTINUE", "EP04"}:
            return decision, None
        return None, self._reconciliation_result(
            receipt, WorkerFailureCode.RECONCILIATION_REQUIRED
        )

    def _terminal_cas_conflict_result(
        self,
        fence: _BoundOperationFence,
        receipt: WorkerOperationReceipt,
        *,
        expected_queue_state: WorkflowCommandState = WorkflowCommandState.RUNNING,
    ) -> WorkerIterationResult:
        """Perform the sole full-precedence reread after a terminal queue CAS loss."""

        decision, _ = self._fresh_bound_decision(
            fence,
            expected_queue_state=expected_queue_state,
            expected_receipt_state=receipt.state,
            expected_receipt_state_version=receipt.state_version,
            allow_renewal=False,
        )
        if decision.precedence == "EP01":
            return self._ep01_result(receipt, decision)
        if decision.precedence == "EP02":
            assert decision.command is not None and decision.operation is not None
            return self._mirror_terminal(decision.operation, decision.command)
        if decision.precedence == "EP03":
            return self._lease_lost_result(receipt)
        if decision.precedence == "EP05":
            result = self._reconciliation_result(
                receipt, WorkerFailureCode.SHUTDOWN_GRACE_EXPIRED
            )
            self._fail_session(WorkerFailureCode.SHUTDOWN_GRACE_EXPIRED.value)
            return result
        return self._reconciliation_result(
            receipt, WorkerFailureCode.QUEUE_CAS_LOST
        )

    def _queue_failure(
        self,
        fence: _BoundOperationFence,
        receipt: WorkerOperationReceipt,
        failure_code: WorkerFailureCode,
        diagnostic: str | None,
    ) -> WorkerIterationResult:
        decision, blocked = self._authorize_terminal_queue_mutation(
            fence, receipt
        )
        if blocked is not None:
            return blocked
        assert decision is not None
        if decision.precedence == "EP04":
            assert decision.command is not None
            return self._cancel(fence, receipt, decision.command)
        if (
            decision.precedence != "CONTINUE"
            or decision.operation is None
        ):
            return self._reconciliation_result(
                receipt, WorkerFailureCode.RECONCILIATION_REQUIRED
            )
        active_receipt = decision.operation
        transition = self._transition_operation(
            active_receipt,
            WorkerOperationState.RESULT_FAILED,
            failure_code,
            diagnostic,
        )
        if (
            transition.code is not WorkerJournalResultCode.APPLIED
            or transition.operation is None
        ):
            return self._non_applied_transition_result(
                transition, active_receipt
            )
        result_receipt = transition.operation
        decision, blocked = self._authorize_terminal_queue_mutation(
            fence, result_receipt
        )
        if blocked is not None:
            return blocked
        assert decision is not None
        if decision.precedence == "EP04":
            assert decision.command is not None
            return self._cancel(fence, result_receipt, decision.command)
        if decision.precedence != "CONTINUE" or decision.command is None:
            return self._reconciliation_result(
                result_receipt, WorkerFailureCode.RECONCILIATION_REQUIRED
            )
        command = decision.command
        queue_result = self._queue_service.mark_failed(
            command_id=command.command_id,
            expected_state=command.state,
            expected_state_version=command.state_version,
            lease_owner=command.lease_owner,
            failure_code=failure_code.value,
        )
        if queue_result.code is QueueResultCode.APPLIED and queue_result.command is not None:
            return self._mirror_terminal(result_receipt, queue_result.command)
        return self._terminal_cas_conflict_result(
            fence, result_receipt
        )

    def _cancel(
        self,
        fence: _BoundOperationFence,
        receipt: WorkerOperationReceipt,
        command: WorkflowCommand,
    ) -> WorkerIterationResult:
        if command.cancellation_requested_at is None:
            return self._reconciliation_result(
                receipt, WorkerFailureCode.RECONCILIATION_REQUIRED
            )
        if (
            self._session is None
            or command.command_id != fence.command_id
            or command.claim_count != fence.claim_count
            or command.lease_owner != receipt.queue_owner_id
            or command.lease_owner != fence.queue_owner_id
            or command.lease_owner != self._session.queue_owner_id
            or receipt.command_id != fence.command_id
            or receipt.operation_id != fence.operation_id
            or receipt.session_id != self._session.session_id
            or receipt.worker_id != self._session.worker_id
        ):
            return self._lease_lost_result(receipt)
        observed = receipt
        if receipt.state is WorkerOperationState.RUNNING:
            transition = self._transition_operation(
                receipt,
                WorkerOperationState.CANCELLATION_OBSERVED,
                WorkerFailureCode.CANCELLATION_OBSERVED,
            )
            if (
                transition.code is not WorkerJournalResultCode.APPLIED
                or transition.operation is None
            ):
                return self._non_applied_transition_result(transition, receipt)
            observed = transition.operation
        decision, blocked = self._authorize_terminal_queue_mutation(
            fence,
            observed,
            expected_queue_state=command.state,
        )
        if blocked is not None:
            return blocked
        if (
            decision is None
            or decision.precedence != "EP04"
            or decision.command is None
        ):
            return self._reconciliation_result(
                observed, WorkerFailureCode.RECONCILIATION_REQUIRED
            )
        command = decision.command
        queue_result = self._queue_service.acknowledge_cancellation(
            command_id=command.command_id,
            expected_state=command.state,
            expected_state_version=command.state_version,
            lease_owner=receipt.queue_owner_id,
        )
        if queue_result.code is QueueResultCode.APPLIED and queue_result.command is not None:
            return self._mirror_terminal(observed, queue_result.command)
        return self._terminal_cas_conflict_result(
            fence,
            observed,
            expected_queue_state=command.state,
        )

    def _success(
        self, fence: _BoundOperationFence, receipt: WorkerOperationReceipt
    ) -> WorkerIterationResult:
        decision, blocked = self._authorize_terminal_queue_mutation(
            fence, receipt
        )
        if blocked is not None:
            return blocked
        assert decision is not None
        if decision.precedence == "EP04":
            assert decision.command is not None
            return self._cancel(fence, receipt, decision.command)
        if decision.precedence != "CONTINUE" or decision.operation is None:
            return self._reconciliation_result(
                receipt, WorkerFailureCode.RECONCILIATION_REQUIRED
            )
        active_receipt = decision.operation
        transition = self._transition_operation(
            active_receipt, WorkerOperationState.RESULT_SUCCEEDED, None
        )
        if (
            transition.code is not WorkerJournalResultCode.APPLIED
            or transition.operation is None
        ):
            return self._non_applied_transition_result(
                transition, active_receipt
            )
        result_receipt = transition.operation
        decision, blocked = self._authorize_terminal_queue_mutation(
            fence, result_receipt
        )
        if blocked is not None:
            return blocked
        assert decision is not None
        if decision.precedence == "EP04":
            assert decision.command is not None
            return self._cancel(fence, result_receipt, decision.command)
        if decision.precedence != "CONTINUE" or decision.command is None:
            return self._reconciliation_result(
                result_receipt, WorkerFailureCode.RECONCILIATION_REQUIRED
            )
        command = decision.command
        queue_result = self._queue_service.mark_succeeded(
            command_id=command.command_id,
            expected_state=command.state,
            expected_state_version=command.state_version,
            lease_owner=command.lease_owner,  # type: ignore[arg-type]
        )
        if queue_result.code is QueueResultCode.APPLIED and queue_result.command is not None:
            return self._mirror_terminal(result_receipt, queue_result.command)
        return self._terminal_cas_conflict_result(
            fence, result_receipt
        )

    def _after_handler(
        self,
        fence: _BoundOperationFence,
        receipt: WorkerOperationReceipt,
        handler_result: WorkerHandlerResult | None,
        handler_exception: BaseException | None = None,
        protocol_diagnostic: str | None = None,
    ) -> WorkerIterationResult:
        decision, renewal_conflict = self._fresh_bound_decision(
            fence,
            allow_renewal=True,
            handler_exception=handler_exception,
            handler_result=handler_result,
        )
        if renewal_conflict:
            return self._reconciliation_result(
                receipt, WorkerFailureCode.QUEUE_CAS_LOST
            )
        if decision.precedence == "EP01":
            return self._ep01_result(receipt, decision)
        if decision.precedence == "EP02":
            assert decision.command is not None and decision.operation is not None
            return self._mirror_terminal(decision.operation, decision.command)
        if decision.precedence == "EP03":
            return self._lease_lost_result(receipt)
        if decision.precedence == "EP04":
            assert decision.command is not None
            return self._cancel(fence, receipt, decision.command)
        if decision.precedence == "EP05":
            result = self._reconciliation_result(
                receipt, WorkerFailureCode.SHUTDOWN_GRACE_EXPIRED
            )
            self._fail_session(WorkerFailureCode.SHUTDOWN_GRACE_EXPIRED.value)
            return result
        if decision.precedence == "EP08":
            if handler_exception is not None and not isinstance(
                handler_exception, Exception
            ):
                try:
                    transition = self._transition_operation(
                        receipt,
                        WorkerOperationState.RECONCILIATION_REQUIRED,
                        WorkerFailureCode.HANDLER_BASE_EXCEPTION,
                    )
                    if transition.code is not WorkerJournalResultCode.APPLIED:
                        self._non_applied_transition_result(transition, receipt)
                except BaseException:
                    # The accepted EP08 path preserves the original handler
                    # BaseException even when best-effort durable recording
                    # itself fails.
                    pass
                try:
                    self._fail_session(
                        WorkerFailureCode.HANDLER_BASE_EXCEPTION.value
                    )
                except BaseException:
                    pass
                raise handler_exception
            return self._queue_failure(
                fence,
                receipt,
                WorkerFailureCode.HANDLER_EXCEPTION,
                protocol_diagnostic,
            )
        if decision.precedence != "EP09" or handler_result is None:
            return self._reconciliation_result(
                receipt, WorkerFailureCode.RECONCILIATION_REQUIRED
            )
        command = decision.command
        assert command is not None
        if handler_result.status is WorkerHandlerStatus.CANCELLED:
            return self._queue_failure(
                fence,
                receipt,
                WorkerFailureCode.HANDLER_EXCEPTION,
                "HANDLER_PROTOCOL_ERROR:CANCELLED_WITHOUT_AUTHORITATIVE_CANCELLATION",
            )
        if handler_result.status is WorkerHandlerStatus.SUCCEEDED:
            return self._success(fence, receipt)
        return self._queue_failure(
            fence,
            receipt,
            WorkerFailureCode.HANDLER_REPORTED_FAILURE,
            None,
        )

    def run_iteration(self) -> WorkerIterationResult:
        if self._session is None or self._session.state not in {
            WorkerSessionState.ACTIVE,
            WorkerSessionState.STOPPING,
        }:
            return WorkerIterationResult(WorkerIterationStatus.NOT_STARTED)
        try:
            if self._session.state is WorkerSessionState.STOPPING:
                shutdown = self.shutdown()
                if (
                    shutdown.session is not None
                    and shutdown.session.state is WorkerSessionState.STOPPED
                ):
                    return WorkerIterationResult(WorkerIterationStatus.STOPPED)
                if shutdown.operation is not None:
                    operation = shutdown.operation
                    return WorkerIterationResult(
                        WorkerIterationStatus.RECONCILIATION_REQUIRED,
                        operation.command_id,
                        operation.operation_id,
                        WorkerFailureCode.RECONCILIATION_REQUIRED.value,
                    )
                return WorkerIterationResult(
                    WorkerIterationStatus.STOPPED,
                    detail_code=WorkerFailureCode.CLEANUP_FAILURE.value,
                )
            instant, _ = self._instant()
            monotonic = self._monotonic()
            if not self._heartbeat_if_due(instant, monotonic):
                return WorkerIterationResult(
                    WorkerIterationStatus.STOPPED,
                    detail_code=WorkerFailureCode.SESSION_FENCED.value,
                )
            if self._stop_requested():
                shutdown = self.shutdown()
                if (
                    shutdown.session is not None
                    and shutdown.session.state is WorkerSessionState.STOPPED
                ):
                    return WorkerIterationResult(WorkerIterationStatus.STOPPED)
                return WorkerIterationResult(
                    WorkerIterationStatus.STOPPED,
                    detail_code=WorkerFailureCode.CLEANUP_FAILURE.value,
                )
            # Close the ordinary pre-claim observation window.  Once the queue
            # transaction begins, its BEGIN IMMEDIATE ordering with the durable
            # session transition is authoritative.
            if self._stop_requested() or self._stop_bridge_failure:
                shutdown = self.shutdown()
                clean = (
                    shutdown.session is not None
                    and shutdown.session.state is WorkerSessionState.STOPPED
                )
                return WorkerIterationResult(
                    WorkerIterationStatus.STOPPED,
                    detail_code=(
                        None
                        if clean
                        else (
                            self._last_stop_transition_result.code.value
                            if self._last_stop_transition_result is not None
                            else WorkerFailureCode.CLEANUP_FAILURE.value
                        )
                    ),
                )
            claimed = self._queue_service.claim_next_eligible(
                eligible_definition_keys=self._registry.keys(),
                lease_owner=self._session.queue_owner_id,
            )
            if claimed.code is QueueResultCode.NO_ELIGIBLE_COMMAND:
                return WorkerIterationResult(WorkerIterationStatus.IDLE)
            if claimed.code is QueueResultCode.TRANSIENT_CONTENTION:
                return WorkerIterationResult(WorkerIterationStatus.CONTENDED)
            if claimed.code is not QueueResultCode.APPLIED or claimed.command is None:
                return WorkerIterationResult(
                    WorkerIterationStatus.CONTENDED, detail_code=claimed.code.value
                )
            command = claimed.command
            operation_id = _worker_uuid(
                self._operation_id_factory(), "operation_id"
            )
            created = self._journal.create_operation(
                operation_id=operation_id,
                command=command,
                operation_kind=WorkerOperationKind.COMMAND_HANDLER_INVOCATION,
                worker_id=self._configuration.worker_id,
                session_id=self._session.session_id,
                queue_owner_id=self._session.queue_owner_id,
                occurred_at=self._instant()[1],
                stale_before=self._stale_before(self._instant()[0]),
            )
            if created.code is not WorkerJournalResultCode.APPLIED or created.operation is None:
                self._fail_session(
                    WorkerFailureCode.WORKER_REPOSITORY_FAILURE.value
                )
                return WorkerIterationResult(
                    WorkerIterationStatus.RECONCILIATION_REQUIRED,
                    command.command_id,
                    operation_id,
                    WorkerFailureCode.WORKER_REPOSITORY_FAILURE.value,
                )
            receipt = created.operation
            try:
                entry = self._registry.lookup(
                    command.command_kind, command.command_schema_version
                )
            except (_RegistryInvariantError, LookupError, RuntimeError):
                result = self._reconciliation_result(
                    receipt,
                    WorkerFailureCode.RECONCILIATION_REQUIRED,
                    "HANDLER_LOOKUP_MISMATCH:FROZEN_REGISTRY_INVARIANT_LOST",
                )
                self._fail_session(
                    WorkerFailureCode.RECONCILIATION_REQUIRED.value
                )
                return result
            assert self._session is not None
            fence = _BoundOperationFence(
                self._configuration.worker_id,
                self._session.session_sequence,
                self._session.session_id,
                self._session.state_version,
                self._session.queue_owner_id,
                command.command_id,
                receipt.operation_id,
                command.claim_count,
                receipt.state_version + 1,
                receipt.precondition_state_version,
                command.lease_expires_at,  # type: ignore[arg-type]
            )
            self._bound_fence = fence
            decision, renewal_conflict = self._fresh_bound_decision(
                fence,
                expected_queue_state=WorkflowCommandState.CLAIMED,
                expected_receipt_state=WorkerOperationState.PREPARED,
                expected_receipt_state_version=receipt.state_version,
                allow_renewal=True,
            )
            if renewal_conflict:
                self._bound_fence = None
                return self._reconciliation_result(
                    receipt, WorkerFailureCode.QUEUE_CAS_LOST
                )
            if decision.precedence == "EP01":
                self._bound_fence = None
                return self._ep01_result(receipt, decision)
            if decision.precedence == "EP02":
                assert decision.command is not None and decision.operation is not None
                self._bound_fence = None
                return self._mirror_terminal(decision.operation, decision.command)
            if decision.precedence == "EP03":
                self._bound_fence = None
                return self._lease_lost_result(receipt)
            if decision.precedence == "EP04":
                assert decision.command is not None
                result = self._cancel(fence, receipt, decision.command)
                self._bound_fence = None
                return result
            if decision.precedence == "EP05":
                result = self._reconciliation_result(
                    receipt, WorkerFailureCode.SHUTDOWN_GRACE_EXPIRED
                )
                self._fail_session(WorkerFailureCode.SHUTDOWN_GRACE_EXPIRED.value)
                self._bound_fence = None
                return result
            if decision.precedence != "CONTINUE" or decision.command is None:
                self._bound_fence = None
                return self._reconciliation_result(
                    receipt, WorkerFailureCode.RECONCILIATION_REQUIRED
                )
            command = decision.command
            running_queue = self._queue_service.mark_running(
                command_id=command.command_id,
                expected_state=command.state,
                expected_state_version=command.state_version,
                lease_owner=self._session.queue_owner_id,
            )
            if (
                running_queue.code is not QueueResultCode.APPLIED
                or running_queue.command is None
            ):
                decision, _ = self._fresh_bound_decision(
                    fence,
                    expected_queue_state=WorkflowCommandState.CLAIMED,
                    expected_receipt_state=WorkerOperationState.PREPARED,
                    expected_receipt_state_version=receipt.state_version,
                    allow_renewal=False,
                )
                if decision.precedence in {"EP01", "EP02"}:
                    result = self._ep01_result(receipt, decision)
                elif (
                    decision.precedence == "EP03"
                    and decision.command is not None
                    and decision.command.state is WorkflowCommandState.CLAIMED
                    and decision.command.lease_owner != fence.queue_owner_id
                ):
                    result = self._lease_lost_result(receipt)
                else:
                    result = self._reconciliation_result(
                        receipt, WorkerFailureCode.QUEUE_CAS_LOST
                    )
                self._bound_fence = None
                return result
            command = running_queue.command
            decision, renewal_conflict = self._fresh_bound_decision(
                fence,
                expected_queue_state=WorkflowCommandState.RUNNING,
                expected_receipt_state=WorkerOperationState.PREPARED,
                expected_receipt_state_version=receipt.state_version,
                allow_renewal=True,
            )
            if renewal_conflict:
                self._bound_fence = None
                return self._reconciliation_result(
                    receipt, WorkerFailureCode.QUEUE_CAS_LOST
                )
            if decision.precedence == "EP01":
                self._bound_fence = None
                return self._ep01_result(receipt, decision)
            if decision.precedence == "EP02":
                assert decision.command is not None and decision.operation is not None
                self._bound_fence = None
                return self._mirror_terminal(decision.operation, decision.command)
            if decision.precedence == "EP03":
                self._bound_fence = None
                return self._lease_lost_result(receipt)
            if decision.precedence == "EP04":
                assert decision.command is not None
                result = self._cancel(fence, receipt, decision.command)
                self._bound_fence = None
                return result
            if decision.precedence == "EP05":
                result = self._reconciliation_result(
                    receipt, WorkerFailureCode.SHUTDOWN_GRACE_EXPIRED
                )
                self._fail_session(WorkerFailureCode.SHUTDOWN_GRACE_EXPIRED.value)
                self._bound_fence = None
                return result
            if decision.precedence != "CONTINUE" or decision.command is None:
                self._bound_fence = None
                return self._reconciliation_result(
                    receipt, WorkerFailureCode.RECONCILIATION_REQUIRED
                )
            command = decision.command
            running_transition = self._transition_operation(
                receipt, WorkerOperationState.RUNNING, None
            )
            if (
                running_transition.code is not WorkerJournalResultCode.APPLIED
                or running_transition.operation is None
            ):
                self._bound_fence = None
                return self._non_applied_transition_result(
                    running_transition, receipt
                )
            running_receipt = running_transition.operation
            if running_receipt.state_version != fence.receipt_state_version:
                self._bound_fence = None
                return self._reconciliation_result(
                    running_receipt, WorkerFailureCode.RECONCILIATION_REQUIRED
                )
            try:
                validated_entry = self._registry.validate(command)
                if validated_entry is not entry:
                    raise _RegistryInvariantError("frozen registry invariant lost")
            except _RegistryInvariantError:
                result = self._reconciliation_result(
                    running_receipt,
                    WorkerFailureCode.RECONCILIATION_REQUIRED,
                )
                self._fail_session(
                    WorkerFailureCode.RECONCILIATION_REQUIRED.value
                )
                self._bound_fence = None
                return result
            except _ValidatorExecutionError:
                result = self._queue_failure(
                    fence,
                    running_receipt, WorkerFailureCode.VALIDATOR_EXCEPTION, None
                )
                self._bound_fence = None
                return result
            except (json.JSONDecodeError, UnicodeError, TypeError, ValueError):
                result = self._queue_failure(
                    fence,
                    running_receipt, WorkerFailureCode.PAYLOAD_INVALID, None
                )
                self._bound_fence = None
                return result
            predispatch = self._before_handler(fence, running_receipt)
            if predispatch is not None:
                self._bound_fence = None
                return predispatch
            checkpoint = WorkerCheckpoint(_CHECKPOINT_SEAL, self, fence)
            context = WorkerHandlerContext(
                command,
                self._configuration.worker_id,
                self._session.session_id,
                self._session.queue_owner_id,
                checkpoint,
            )
            try:
                try:
                    handler_result = entry.handler(context)
                except Exception as error:
                    return self._after_handler(
                        fence, running_receipt, None, error
                    )
                except BaseException as error:
                    return self._after_handler(
                        fence, running_receipt, None, error
                    )
                if type(handler_result) is not WorkerHandlerResult:
                    return self._after_handler(
                        fence,
                        running_receipt,
                        None,
                        TypeError("handler return"),
                        "HANDLER_PROTOCOL_ERROR:INVALID_RETURN",
                    )
                return self._after_handler(fence, running_receipt, handler_result)
            finally:
                self._bound_fence = None
        except RepositoryError:
            self._bound_fence = None
            self._fail_session(WorkerFailureCode.WORKER_REPOSITORY_FAILURE.value)
            return WorkerIterationResult(
                WorkerIterationStatus.STOPPED,
                detail_code=WorkerFailureCode.WORKER_REPOSITORY_FAILURE.value,
            )
        except (TypeError, ValueError, OSError):
            self._bound_fence = None
            self._fail_session(WorkerFailureCode.CLEANUP_FAILURE.value)
            return WorkerIterationResult(
                WorkerIterationStatus.STOPPED,
                detail_code=WorkerFailureCode.CLEANUP_FAILURE.value,
            )

    def run_forever(self) -> WorkerIterationResult:
        last = WorkerIterationResult(WorkerIterationStatus.NOT_STARTED)
        while True:
            last = self.run_iteration()
            if (
                last.status is WorkerIterationStatus.RECONCILIATION_REQUIRED
                and last.detail_code == WorkerFailureCode.LEASE_LOST.value
            ):
                # M16-05 is command-local convergence: the receipt records the
                # lease loss while the current session remains ACTIVE and may
                # continue polling.  It is not a Human-reconciliation exit.
                pass
            elif last.status in {
                WorkerIterationStatus.STOPPED,
                WorkerIterationStatus.NOT_STARTED,
                WorkerIterationStatus.RECONCILIATION_REQUIRED,
            }:
                return last
            waited = self._wait(self._configuration.poll_interval_seconds)
            if type(waited) is not bool:
                raise TypeError("wait")
            if waited or self._stop_requested():
                shutdown = self.shutdown()
                clean = (
                    shutdown.session is not None
                    and shutdown.session.state is WorkerSessionState.STOPPED
                )
                return WorkerIterationResult(
                    WorkerIterationStatus.STOPPED,
                    detail_code=(
                        None if clean else WorkerFailureCode.CLEANUP_FAILURE.value
                    ),
                )

    def shutdown(
        self, reason_code: str = "GRACEFUL_STOP"
    ) -> WorkerJournalResult:
        reason = _queue_text(reason_code, "reason_code", _QUEUE_CODE_PATTERN)
        if self._session is None:
            return WorkerJournalResult(WorkerJournalResultCode.NOT_FOUND)
        if self._session.state in {WorkerSessionState.STOPPED, WorkerSessionState.FAILED}:
            return WorkerJournalResult(
                WorkerJournalResultCode.TERMINAL_OBSERVED,
                session=self._session,
            )
        monotonic = self._monotonic()
        if not self._accept_stop(monotonic, reason):
            return self._last_stop_transition_result or WorkerJournalResult(
                WorkerJournalResultCode.CAS_CONFLICT, session=self._session
            )
        operations = self._journal.list_nonterminal_operations(
            worker_id=self._configuration.worker_id
        )
        commands = self._journal.list_owned_commands(
            worker_id=self._configuration.worker_id
        )
        if (
            operations.code is not WorkerJournalResultCode.LISTED
            or commands.code is not WorkerJournalResultCode.LISTED
        ):
            self._fail_session(WorkerFailureCode.CLEANUP_FAILURE.value)
            return (
                operations
                if operations.code is not WorkerJournalResultCode.LISTED
                else commands
            )
        if operations.operations or commands.commands:
            if operations.operations:
                return WorkerJournalResult(
                    WorkerJournalResultCode.CAS_CONFLICT,
                    operation=operations.operations[0],
                )
            self._fail_session(WorkerFailureCode.RECONCILIATION_REQUIRED.value)
            return WorkerJournalResult(
                WorkerJournalResultCode.CAS_CONFLICT, session=self._session
            )
        list_startup_terminal_commands = getattr(
            self._journal, "_list_startup_terminal_commands", None
        )
        if not callable(list_startup_terminal_commands):
            self._fail_session(WorkerFailureCode.CLEANUP_FAILURE.value)
            return WorkerJournalResult(WorkerJournalResultCode.CAS_CONFLICT)
        terminal_commands = list_startup_terminal_commands(
            worker_id=self._configuration.worker_id
        )
        if terminal_commands.code is not WorkerJournalResultCode.LISTED:
            self._fail_session(WorkerFailureCode.CLEANUP_FAILURE.value)
            return terminal_commands
        for command in terminal_commands.commands:
            receipt_result = self._journal.get_operation_for_claim(
                command_id=command.command_id,
                claim_count=command.claim_count,
            )
            receipt = receipt_result.operation
            if receipt_result.code is not WorkerJournalResultCode.FOUND:
                self._fail_session(
                    WorkerFailureCode.RECONCILIATION_REQUIRED.value
                )
                return receipt_result
            valid = (
                receipt is not None
                and self._receipt_matches_command(receipt, command)
                and (
                    (
                        command.state in _TERMINAL_QUEUE_STATES
                        and receipt.state
                        is {
                            WorkflowCommandState.SUCCEEDED: WorkerOperationState.SUCCEEDED,
                            WorkflowCommandState.FAILED: WorkerOperationState.FAILED,
                            WorkflowCommandState.CANCELLED: WorkerOperationState.CANCELLED,
                        }[command.state]
                    )
                    or (
                        command.state is WorkflowCommandState.PENDING
                        and receipt.state
                        in {
                            WorkerOperationState.CLAIM_RELEASED,
                            WorkerOperationState.LEASE_LOST,
                        }
                    )
                )
            )
            if not valid:
                self._fail_session(
                    WorkerFailureCode.RECONCILIATION_REQUIRED.value
                )
                return WorkerJournalResult(
                    WorkerJournalResultCode.CAS_CONFLICT,
                    operation=receipt,
                )
        return self._transition_session(WorkerSessionState.STOPPED, reason)
