"""State-Machine-owned transactional transition persistence."""

from datetime import datetime, timezone
from typing import Callable
from uuid import uuid4

from .models import (
    AcceptedStateEvent,
    DevelopmentRun,
    TransactionalTransitionReasonCode,
    TransactionalTransitionResult,
    TransitionEvaluationDecision,
    TransitionEvaluationReasonCode,
    TransitionEvaluationRequest,
)
from .sqlite_store import SqliteRunStore
from .transition_policy import TransitionPolicy


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _format_timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


class TransitionService:
    """The sole public authority for evaluating and persisting transitions."""

    def __init__(
        self,
        store: SqliteRunStore,
        policy: TransitionPolicy | None = None,
        clock: Callable[[], datetime] = _utc_now,
        id_factory: Callable[[], str] = lambda: str(uuid4()),
    ) -> None:
        self._store = store
        self._policy = policy or TransitionPolicy()
        self._clock = clock
        self._id_factory = id_factory

    def initialize(self) -> None:
        self._store.initialize(_format_timestamp(self._clock()))

    def create_run(self, milestone_contract_digest: str) -> DevelopmentRun:
        return self._store.create_run(
            run_id=self._id_factory(),
            milestone_contract_digest=milestone_contract_digest,
            created_at=_format_timestamp(self._clock()),
        )

    def transition(self, request: object) -> TransactionalTransitionResult:
        evaluation = self._policy.evaluate(request)
        if evaluation.decision is not TransitionEvaluationDecision.ALLOWED:
            return TransactionalTransitionResult(
                committed=False,
                reason_code=(
                    TransactionalTransitionReasonCode.EVALUATION_NOT_ALLOWED
                ),
                evaluation_result=evaluation,
                persisted_run=None,
                accepted_event=None,
            )

        assert type(request) is TransitionEvaluationRequest
        assert request.run is not None
        assert request.requested_state is not None
        event_id = self._id_factory()
        occurred_at = _format_timestamp(self._clock())

        connection = self._store._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT run_id, milestone_contract_digest, current_state, state_version,
                       created_at, updated_at
                FROM development_runs WHERE run_id = ?
                """,
                (request.run.run_id,),
            ).fetchone()
            if row is None:
                connection.rollback()
                return TransactionalTransitionResult(
                    committed=False,
                    reason_code=TransactionalTransitionReasonCode.RUN_NOT_FOUND,
                    evaluation_result=evaluation,
                    persisted_run=None,
                    accepted_event=None,
                )

            persisted_run = self._store._to_run(row)
            if persisted_run != request.run:
                connection.rollback()
                return TransactionalTransitionResult(
                    committed=False,
                    reason_code=TransactionalTransitionReasonCode.STALE_PERSISTED_RUN,
                    evaluation_result=evaluation,
                    persisted_run=persisted_run,
                    accepted_event=None,
                )

            next_version = persisted_run.state_version + 1
            updated = connection.execute(
                """
                UPDATE development_runs
                SET current_state = ?, state_version = ?, updated_at = ?
                WHERE run_id = ? AND current_state = ? AND state_version = ?
                """,
                (
                    request.requested_state.value,
                    next_version,
                    occurred_at,
                    persisted_run.run_id,
                    persisted_run.current_state.value,
                    persisted_run.state_version,
                ),
            ).rowcount
            if updated != 1:
                connection.rollback()
                return TransactionalTransitionResult(
                    committed=False,
                    reason_code=TransactionalTransitionReasonCode.STALE_PERSISTED_RUN,
                    evaluation_result=evaluation,
                    persisted_run=persisted_run,
                    accepted_event=None,
                )
            accepted_event = AcceptedStateEvent(
                event_id=event_id,
                run_id=persisted_run.run_id,
                from_state=persisted_run.current_state,
                to_state=request.requested_state,
                transition_reason=TransitionEvaluationReasonCode.RULE_ALLOWED.value,
                occurred_at=occurred_at,
                state_version=next_version,
            )
            connection.execute(
                """
                INSERT INTO state_events(
                    event_id, run_id, from_state, to_state, transition_reason,
                    occurred_at, state_version
                ) VALUES(?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    accepted_event.event_id,
                    accepted_event.run_id,
                    accepted_event.from_state.value,
                    accepted_event.to_state.value,
                    accepted_event.transition_reason,
                    accepted_event.occurred_at,
                    accepted_event.state_version,
                ),
            )
            connection.commit()
            committed_run = DevelopmentRun(
                run_id=persisted_run.run_id,
                milestone_contract_digest=persisted_run.milestone_contract_digest,
                current_state=request.requested_state,
                state_version=next_version,
                created_at=persisted_run.created_at,
                updated_at=occurred_at,
            )
            return TransactionalTransitionResult(
                committed=True,
                reason_code=TransactionalTransitionReasonCode.COMMITTED,
                evaluation_result=evaluation,
                persisted_run=committed_run,
                accepted_event=accepted_event,
            )
        except Exception as error:
            if connection.in_transaction:
                try:
                    connection.rollback()
                except Exception as rollback_error:
                    raise rollback_error from error
            raise
        finally:
            connection.close()
