"""Transactional State Machine service for the bounded DL-2.3 Phase edge."""

from datetime import datetime, timezone
from typing import Callable
from uuid import uuid4

from .models import (
    AcceptedPhaseStateEvent,
    ContractValidationError,
    PhaseContract,
    PhaseStateRecord,
    PhaseTransactionalTransitionReasonCode,
    PhaseTransactionalTransitionResult,
    PhaseTransitionEvaluationReasonCode,
    PhaseTransitionEvaluationRequest,
    TransitionEvaluationDecision,
)
from .phase_transition_policy import PhaseTransitionPolicy
from .repositories import RepositoryError, RepositoryFailureCode
from .sqlite_phase_store import SqlitePhaseStore


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _format_phase_timestamp(value: datetime) -> str:
    if type(value) is not datetime or value.tzinfo is None:
        raise TypeError("clock result")
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace(
        "+00:00",
        "Z",
    )


class PhaseTransitionService:
    """The only DL-2.3 service that may persist a Phase transition."""

    def __init__(
        self,
        store: SqlitePhaseStore,
        policy: PhaseTransitionPolicy | None = None,
        clock: Callable[[], datetime] = _utc_now,
        id_factory: Callable[[], str] = lambda: str(uuid4()),
    ) -> None:
        if not isinstance(store, SqlitePhaseStore):
            raise TypeError("store")
        if policy is not None and not isinstance(policy, PhaseTransitionPolicy):
            raise TypeError("policy")
        if not callable(clock):
            raise TypeError("clock")
        if not callable(id_factory):
            raise TypeError("id_factory")
        self._store = store
        self._policy = policy or PhaseTransitionPolicy()
        self._clock = clock
        self._id_factory = id_factory

    def initialize(self) -> None:
        self._store.initialize(_format_phase_timestamp(self._clock()))

    def initialize_phase(self, phase_contract: PhaseContract) -> PhaseStateRecord:
        return self._store.initialize_phase(
            phase_contract,
            _format_phase_timestamp(self._clock()),
        )

    def transition(self, request: object) -> PhaseTransactionalTransitionResult:
        evaluation = self._policy.evaluate(request)
        if evaluation.decision is not TransitionEvaluationDecision.ALLOWED:
            return PhaseTransactionalTransitionResult(
                committed=False,
                reason_code=PhaseTransactionalTransitionReasonCode.EVALUATION_NOT_ALLOWED,
                evaluation_result=evaluation,
                persisted_state=None,
                accepted_event=None,
            )

        assert type(request) is PhaseTransitionEvaluationRequest
        assert request.phase_state is not None
        assert request.phase_contract is not None
        assert request.requested_state is not None

        event_id = self._id_factory()
        occurred_at = _format_phase_timestamp(self._clock())
        identity = (
            f"project_id={request.phase_state.project_id},"
            f"phase_id={request.phase_state.phase_id}"
        )
        connection = self._store._connect("PhaseTransition", identity)
        try:
            connection.execute("BEGIN IMMEDIATE")
            self._store._validate_mutation_schema(connection, "PhaseTransition", identity)
            row = connection.execute(
                """
                SELECT ps.project_id, ps.phase_id, ps.phase_contract_digest,
                       ps.current_state, ps.state_version, ps.created_at, ps.updated_at,
                       p.contract_version,
                       p.contract_digest AS authoritative_contract_digest
                FROM phase_states AS ps
                JOIN phases AS p
                  ON p.project_id = ps.project_id AND p.phase_id = ps.phase_id
                WHERE ps.project_id = ? AND ps.phase_id = ?
                """,
                (request.phase_state.project_id, request.phase_state.phase_id),
            ).fetchone()
            if row is None:
                connection.rollback()
                return PhaseTransactionalTransitionResult(
                    committed=False,
                    reason_code=PhaseTransactionalTransitionReasonCode.PHASE_STATE_NOT_FOUND,
                    evaluation_result=evaluation,
                    persisted_state=None,
                    accepted_event=None,
                )

            persisted_state = self._store._to_state(row, identity)
            try:
                persisted_contract = PhaseContract(
                    project_id=row["project_id"],
                    phase_id=row["phase_id"],
                    contract_version=row["contract_version"],
                )
            except (ContractValidationError, TypeError, ValueError, KeyError, IndexError):
                raise
            if (
                persisted_state != request.phase_state
                or persisted_contract != request.phase_contract
                or persisted_contract.sha256_digest()
                != row["authoritative_contract_digest"]
                or persisted_state.phase_contract_digest
                != row["authoritative_contract_digest"]
            ):
                connection.rollback()
                return PhaseTransactionalTransitionResult(
                    committed=False,
                    reason_code=(
                        PhaseTransactionalTransitionReasonCode.STALE_PERSISTED_PHASE_STATE
                    ),
                    evaluation_result=evaluation,
                    persisted_state=persisted_state,
                    accepted_event=None,
                )

            next_version = persisted_state.state_version + 1
            accepted_event = AcceptedPhaseStateEvent(
                event_id=event_id,
                project_id=persisted_state.project_id,
                phase_id=persisted_state.phase_id,
                phase_contract_digest=persisted_state.phase_contract_digest,
                from_state=persisted_state.current_state,
                to_state=request.requested_state,
                transition_reason=PhaseTransitionEvaluationReasonCode.RULE_ALLOWED.value,
                occurred_at=occurred_at,
                state_version=next_version,
            )
            updated = connection.execute(
                """
                UPDATE phase_states
                SET current_state = ?, state_version = ?, updated_at = ?
                WHERE project_id = ? AND phase_id = ?
                  AND phase_contract_digest = ? AND current_state = ?
                  AND state_version = ? AND created_at = ? AND updated_at = ?
                """,
                (
                    request.requested_state.value,
                    next_version,
                    occurred_at,
                    persisted_state.project_id,
                    persisted_state.phase_id,
                    persisted_state.phase_contract_digest,
                    persisted_state.current_state.value,
                    persisted_state.state_version,
                    persisted_state.created_at,
                    persisted_state.updated_at,
                ),
            ).rowcount
            if updated != 1:
                connection.rollback()
                return PhaseTransactionalTransitionResult(
                    committed=False,
                    reason_code=(
                        PhaseTransactionalTransitionReasonCode.STALE_PERSISTED_PHASE_STATE
                    ),
                    evaluation_result=evaluation,
                    persisted_state=persisted_state,
                    accepted_event=None,
                )

            inserted = connection.execute(
                """
                INSERT INTO phase_state_events(
                    event_id, project_id, phase_id, phase_contract_digest,
                    from_state, to_state, transition_reason, occurred_at,
                    state_version
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    accepted_event.event_id,
                    accepted_event.project_id,
                    accepted_event.phase_id,
                    accepted_event.phase_contract_digest,
                    accepted_event.from_state.value,
                    accepted_event.to_state.value,
                    accepted_event.transition_reason,
                    accepted_event.occurred_at,
                    accepted_event.state_version,
                ),
            ).rowcount
            if inserted != 1:
                raise RepositoryError(
                    RepositoryFailureCode.SQLITE_OPERATIONAL_FAILURE,
                    "AcceptedPhaseStateEvent",
                    identity,
                )
            event_rows = connection.execute(
                """
                SELECT event_id, project_id, phase_id, phase_contract_digest,
                       from_state, to_state, transition_reason, occurred_at,
                       state_version
                FROM phase_state_events
                WHERE event_id = ?
                   OR (project_id = ? AND phase_id = ? AND state_version = ?)
                """,
                (
                    accepted_event.event_id,
                    accepted_event.project_id,
                    accepted_event.phase_id,
                    accepted_event.state_version,
                ),
            ).fetchall()
            if len(event_rows) != 1:
                raise RepositoryError(
                    RepositoryFailureCode.SQLITE_OPERATIONAL_FAILURE,
                    "AcceptedPhaseStateEvent",
                    identity,
                )
            persisted_event = self._store._to_event(
                event_rows[0],
                accepted_event.project_id,
                accepted_event.phase_id,
                identity,
            )
            if persisted_event != accepted_event:
                raise RepositoryError(
                    RepositoryFailureCode.SQLITE_OPERATIONAL_FAILURE,
                    "AcceptedPhaseStateEvent",
                    identity,
                )
            committed_state = PhaseStateRecord(
                project_id=persisted_state.project_id,
                phase_id=persisted_state.phase_id,
                phase_contract_digest=persisted_state.phase_contract_digest,
                current_state=request.requested_state,
                state_version=next_version,
                created_at=persisted_state.created_at,
                updated_at=occurred_at,
            )
            result = PhaseTransactionalTransitionResult(
                committed=True,
                reason_code=PhaseTransactionalTransitionReasonCode.COMMITTED,
                evaluation_result=evaluation,
                persisted_state=committed_state,
                accepted_event=persisted_event,
            )
            connection.commit()
            return result
        except Exception as error:
            if connection.in_transaction:
                try:
                    connection.rollback()
                except Exception as rollback_error:
                    raise rollback_error from error
            raise
        finally:
            connection.close()
