"""SQLite persistence for the bounded DL-P1.1 run and event model."""

import sqlite3
from pathlib import Path
from typing import Optional

from .models import AcceptedStateEvent, DevelopmentRun, DevelopmentRunState
from .sqlite_migrations import initialize_database


class SqliteRunStore:
    """Persists only the approved DL-P1.1 schema at an explicit path."""

    def __init__(self, database_path: Path) -> None:
        self._database_path = Path(database_path)

    def initialize(self, applied_at: str) -> None:
        initialize_database(self._database_path, applied_at)

    def create_run(
        self,
        run_id: str,
        milestone_contract_digest: str,
        created_at: str,
    ) -> DevelopmentRun:
        connection = self._connect()
        try:
            connection.execute(
                """
                INSERT INTO development_runs(
                    run_id, milestone_contract_digest, current_state, state_version,
                    created_at, updated_at
                ) VALUES(?, ?, ?, 0, ?, ?)
                """,
                (
                    run_id,
                    milestone_contract_digest,
                    DevelopmentRunState.DRAFT.value,
                    created_at,
                    created_at,
                ),
            )
            connection.commit()
        finally:
            connection.close()
        return self.get_run(run_id)

    def get_run(self, run_id: str) -> Optional[DevelopmentRun]:
        connection = self._connect()
        try:
            row = connection.execute(
                """
                SELECT run_id, milestone_contract_digest, current_state, state_version,
                       created_at, updated_at
                FROM development_runs WHERE run_id = ?
                """,
                (run_id,),
            ).fetchone()
        finally:
            connection.close()
        return self._to_run(row) if row is not None else None

    def get_history(self, run_id: str) -> list[AcceptedStateEvent]:
        connection = self._connect()
        try:
            rows = connection.execute(
                """
                SELECT event_id, run_id, from_state, to_state, transition_reason,
                       occurred_at, state_version
                FROM state_events WHERE run_id = ? ORDER BY state_version ASC
                """,
                (run_id,),
            ).fetchall()
        finally:
            connection.close()
        return [self._to_event(row) for row in rows]

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._database_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    @staticmethod
    def _to_run(row: sqlite3.Row) -> DevelopmentRun:
        return DevelopmentRun(
            run_id=row["run_id"],
            milestone_contract_digest=row["milestone_contract_digest"],
            current_state=DevelopmentRunState(row["current_state"]),
            state_version=row["state_version"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _to_event(row: sqlite3.Row) -> AcceptedStateEvent:
        return AcceptedStateEvent(
            event_id=row["event_id"],
            run_id=row["run_id"],
            from_state=DevelopmentRunState(row["from_state"]),
            to_state=DevelopmentRunState(row["to_state"]),
            transition_reason=row["transition_reason"],
            occurred_at=row["occurred_at"],
            state_version=row["state_version"],
        )
