"""State-Machine-owned SQLite persistence for the DL-2.3 Phase lifecycle."""

import sqlite3
from pathlib import Path

from .models import (
    AcceptedPhaseStateEvent,
    ContractValidationError,
    PhaseContract,
    PhaseState,
    PhaseStateRecord,
    _phase_contract_is_exact,
)
from .repositories import (
    RepositoryError,
    RepositoryFailureCode,
    _validate_query_identity,
)
from .sqlite_migrations import initialize_database
from .sqlite_repositories import _validate_current_schema


_CURRENT_TABLES = frozenset(
    {
        "schema_migrations",
        "development_runs",
        "state_events",
        "phases",
        "milestone_contracts",
        "approvals",
        "project_policies",
        "workflow_commands",
        "workflow_command_events",
        "worker_sessions",
        "worker_operations",
        "phase_states",
        "phase_state_events",
    }
)


def _identity(project_id: str, phase_id: str) -> str:
    return f"project_id={project_id},phase_id={phase_id}"


def _malformed(identity: str, error: BaseException) -> RepositoryError:
    return RepositoryError(
        RepositoryFailureCode.MALFORMED_PERSISTED_PAYLOAD,
        "PhaseState",
        identity,
    )


class SqlitePhaseStore:
    """Provides only explicit initialization and Phase transition storage."""

    def __init__(self, database_path: Path) -> None:
        self._database_path = Path(database_path)

    @property
    def database_path(self) -> Path:
        return self._database_path

    def initialize(self, applied_at: str) -> None:
        initialize_database(self._database_path, applied_at)

    def initialize_phase(
        self,
        phase_contract: PhaseContract,
        created_at: str,
    ) -> PhaseStateRecord:
        if not _phase_contract_is_exact(phase_contract):
            raise TypeError("phase_contract")
        candidate = PhaseStateRecord(
            project_id=phase_contract.project_id,
            phase_id=phase_contract.phase_id,
            phase_contract_digest=phase_contract.sha256_digest(),
            current_state=PhaseState.DRAFT,
            state_version=0,
            created_at=created_at,
            updated_at=created_at,
        )
        identity = _identity(candidate.project_id, candidate.phase_id)
        connection = self._connect("PhaseState", identity)
        try:
            connection.execute("BEGIN IMMEDIATE")
            self._validate_mutation_schema(connection, "PhaseState", identity)
            row = connection.execute(
                """
                SELECT project_id, phase_id, contract_version, contract_digest
                FROM phases WHERE project_id = ? AND phase_id = ?
                """,
                (candidate.project_id, candidate.phase_id),
            ).fetchone()
            if row is None:
                raise RepositoryError(
                    RepositoryFailureCode.RELATIONSHIP_VIOLATION,
                    "PhaseState",
                    identity,
                )
            try:
                persisted_contract = PhaseContract(
                    project_id=row["project_id"],
                    phase_id=row["phase_id"],
                    contract_version=row["contract_version"],
                )
            except (ContractValidationError, TypeError, ValueError, KeyError, IndexError) as error:
                raise _malformed(identity, error) from error
            if (
                persisted_contract != phase_contract
                or row["contract_digest"] != candidate.phase_contract_digest
                or persisted_contract.sha256_digest() != row["contract_digest"]
            ):
                error = ValueError("Phase Contract binding mismatch")
                raise _malformed(identity, error) from error
            connection.execute(
                """
                INSERT INTO phase_states(
                    project_id, phase_id, phase_contract_digest, current_state,
                    state_version, created_at, updated_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    candidate.project_id,
                    candidate.phase_id,
                    candidate.phase_contract_digest,
                    candidate.current_state.value,
                    candidate.state_version,
                    candidate.created_at,
                    candidate.updated_at,
                ),
            )
            connection.commit()
            return candidate
        except RepositoryError:
            if connection.in_transaction:
                connection.rollback()
            raise
        except sqlite3.IntegrityError as error:
            if connection.in_transaction:
                connection.rollback()
            message = str(error).upper()
            code = (
                RepositoryFailureCode.DUPLICATE_ENTITY
                if "UNIQUE" in message or "PRIMARY KEY" in message
                else RepositoryFailureCode.RELATIONSHIP_VIOLATION
                if "FOREIGN KEY" in message
                else RepositoryFailureCode.SQLITE_OPERATIONAL_FAILURE
            )
            raise RepositoryError(code, "PhaseState", identity) from error
        except sqlite3.Error as error:
            if connection.in_transaction:
                connection.rollback()
            raise RepositoryError(
                RepositoryFailureCode.SQLITE_OPERATIONAL_FAILURE,
                "PhaseState",
                identity,
            ) from error
        finally:
            connection.close()

    def get_state(self, project_id: str, phase_id: str) -> PhaseStateRecord | None:
        project_id = _validate_query_identity(project_id, "PhaseState", "project_id")
        phase_id = _validate_query_identity(phase_id, "PhaseState", "phase_id")
        identity = _identity(project_id, phase_id)
        connection = self._connect("PhaseState", identity)
        try:
            row = connection.execute(
                """
                SELECT project_id, phase_id, phase_contract_digest, current_state,
                       state_version, created_at, updated_at
                FROM phase_states WHERE project_id = ? AND phase_id = ?
                """,
                (project_id, phase_id),
            ).fetchone()
        except sqlite3.Error as error:
            raise RepositoryError(
                RepositoryFailureCode.SQLITE_OPERATIONAL_FAILURE,
                "PhaseState",
                identity,
            ) from error
        finally:
            connection.close()
        return None if row is None else self._to_state(row, identity)

    def get_history(
        self,
        project_id: str,
        phase_id: str,
    ) -> list[AcceptedPhaseStateEvent]:
        project_id = _validate_query_identity(project_id, "PhaseState", "project_id")
        phase_id = _validate_query_identity(phase_id, "PhaseState", "phase_id")
        identity = _identity(project_id, phase_id)
        connection = self._connect("AcceptedPhaseStateEvent", identity)
        try:
            rows = connection.execute(
                """
                SELECT event_id, project_id, phase_id, phase_contract_digest,
                       from_state, to_state, transition_reason, occurred_at,
                       state_version
                FROM phase_state_events
                WHERE project_id = ? AND phase_id = ?
                ORDER BY state_version ASC, event_sequence ASC
                """,
                (project_id, phase_id),
            ).fetchall()
        except sqlite3.Error as error:
            raise RepositoryError(
                RepositoryFailureCode.SQLITE_OPERATIONAL_FAILURE,
                "AcceptedPhaseStateEvent",
                identity,
            ) from error
        finally:
            connection.close()
        return self._to_history(rows, project_id, phase_id, identity)

    def _connect(self, entity_name: str = "PhaseState", identity: str = "database") -> sqlite3.Connection:
        if not self._database_path.is_file():
            raise RepositoryError(
                RepositoryFailureCode.SCHEMA_MISMATCH,
                entity_name,
                identity,
            )
        try:
            connection = sqlite3.connect(self._database_path, timeout=5.0)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            if connection.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
                raise RepositoryError(
                    RepositoryFailureCode.SCHEMA_MISMATCH,
                    entity_name,
                    identity,
                )
            _validate_current_schema(connection, entity_name, identity)
            tables = {
                row["name"]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master "
                    "WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                )
            }
            versions = [
                row["version"]
                for row in connection.execute(
                    "SELECT version FROM schema_migrations ORDER BY version"
                )
            ]
            if tables != _CURRENT_TABLES or versions != [1, 2, 3, 4, 5, 6]:
                raise RepositoryError(
                    RepositoryFailureCode.SCHEMA_MISMATCH,
                    entity_name,
                    identity,
                )
            return connection
        except RepositoryError:
            try:
                connection.close()
            except (NameError, sqlite3.Error):
                pass
            raise
        except (OSError, sqlite3.Error) as error:
            try:
                connection.close()
            except (NameError, sqlite3.Error):
                pass
            raise RepositoryError(
                RepositoryFailureCode.SQLITE_OPERATIONAL_FAILURE,
                entity_name,
                identity,
            ) from error

    @staticmethod
    def _validate_mutation_schema(
        connection: sqlite3.Connection,
        entity_name: str,
        identity: str,
    ) -> None:
        """Validate on the writer connection after its BEGIN IMMEDIATE."""
        if not connection.in_transaction:
            raise RepositoryError(
                RepositoryFailureCode.SQLITE_OPERATIONAL_FAILURE,
                entity_name,
                identity,
            )
        _validate_current_schema(connection, entity_name, identity)

    @staticmethod
    def _to_state(row: sqlite3.Row, identity: str = "PhaseState") -> PhaseStateRecord:
        try:
            return PhaseStateRecord(
                project_id=row["project_id"],
                phase_id=row["phase_id"],
                phase_contract_digest=row["phase_contract_digest"],
                current_state=PhaseState(row["current_state"]),
                state_version=row["state_version"],
                created_at=row["created_at"],
                updated_at=row["updated_at"],
            )
        except (TypeError, ValueError, KeyError, IndexError) as error:
            raise _malformed(identity, error) from error

    @staticmethod
    def _to_event(
        row: sqlite3.Row,
        project_id: str,
        phase_id: str,
        identity: str,
    ) -> AcceptedPhaseStateEvent:
        try:
            event = AcceptedPhaseStateEvent(
                event_id=row["event_id"],
                project_id=row["project_id"],
                phase_id=row["phase_id"],
                phase_contract_digest=row["phase_contract_digest"],
                from_state=PhaseState(row["from_state"]),
                to_state=PhaseState(row["to_state"]),
                transition_reason=row["transition_reason"],
                occurred_at=row["occurred_at"],
                state_version=row["state_version"],
            )
            if event.project_id != project_id or event.phase_id != phase_id:
                raise ValueError("Phase event identity mismatch")
            return event
        except (TypeError, ValueError, KeyError, IndexError) as error:
            raise _malformed(identity, error) from error

    @classmethod
    def _to_history(
        cls,
        rows: list[sqlite3.Row],
        project_id: str,
        phase_id: str,
        identity: str,
    ) -> list[AcceptedPhaseStateEvent]:
        events = [cls._to_event(row, project_id, phase_id, identity) for row in rows]
        if events and [event.state_version for event in events] != list(
            range(1, len(events) + 1)
        ):
            error = ValueError("non-contiguous Phase event history")
            raise _malformed(identity, error) from error
        return events
