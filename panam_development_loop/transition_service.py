"""State-Machine-owned transition evaluation for the DL-P1.1 POC."""

from datetime import datetime, timezone
from typing import Callable
from uuid import uuid4

from .models import (
    DevelopmentRun,
    DevelopmentRunState,
    TransitionReasonCode,
    TransitionRequest,
    TransitionResult,
)
from .sqlite_store import SqliteRunStore
from .transition_policy import TransitionPolicy


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _format_timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


class TransitionService:
    """The sole public POC authority for creating runs and accepting edges."""

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

    def transition(self, request: TransitionRequest) -> TransitionResult:
        connection = self._store._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT current_state, state_version FROM development_runs WHERE run_id = ?",
                (request.run_id,),
            ).fetchone()
            if row is None:
                connection.rollback()
                return self._rejected(request, TransitionReasonCode.RUN_NOT_FOUND, None, None)

            current_state = DevelopmentRunState(row["current_state"])
            current_version = row["state_version"]
            if (
                current_state != request.expected_state
                or current_version != request.expected_state_version
            ):
                connection.rollback()
                return self._rejected(
                    request,
                    TransitionReasonCode.STALE_EXPECTED_STATE,
                    current_state,
                    current_version,
                )
            if not self._policy.allows(current_state, request.requested_state):
                connection.rollback()
                return self._rejected(
                    request,
                    TransitionReasonCode.TRANSITION_NOT_ALLOWED,
                    current_state,
                    current_version,
                )

            next_version = current_version + 1
            occurred_at = _format_timestamp(self._clock())
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
                    request.run_id,
                    current_state.value,
                    current_version,
                ),
            ).rowcount
            if updated != 1:
                connection.rollback()
                return self._rejected(
                    request,
                    TransitionReasonCode.STALE_EXPECTED_STATE,
                    current_state,
                    current_version,
                )
            connection.execute(
                """
                INSERT INTO state_events(
                    event_id, run_id, from_state, to_state, transition_reason,
                    occurred_at, state_version
                ) VALUES(?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    self._id_factory(),
                    request.run_id,
                    current_state.value,
                    request.requested_state.value,
                    TransitionReasonCode.ACCEPTED.value,
                    occurred_at,
                    next_version,
                ),
            )
            connection.commit()
            return TransitionResult(
                accepted=True,
                reason_code=TransitionReasonCode.ACCEPTED,
                run_id=request.run_id,
                current_state=request.requested_state,
                current_state_version=next_version,
                requested_state=request.requested_state,
            )
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _rejected(
        request: TransitionRequest,
        reason_code: TransitionReasonCode,
        current_state: DevelopmentRunState | None,
        current_state_version: int | None,
    ) -> TransitionResult:
        return TransitionResult(
            accepted=False,
            reason_code=reason_code,
            run_id=request.run_id,
            current_state=current_state,
            current_state_version=current_state_version,
            requested_state=request.requested_state,
        )
