"""Internal controlled SQLite migration support through the DL-P1.8 schema."""

import re
import sqlite3
from dataclasses import dataclass
from enum import Enum
from pathlib import Path


class MigrationFailureCode(str, Enum):
    """Stable failure categories for controlled migration handling."""

    INVALID_REGISTRY = "INVALID_REGISTRY"
    INVALID_APPLIED_HISTORY = "INVALID_APPLIED_HISTORY"
    UNKNOWN_FUTURE_VERSION = "UNKNOWN_FUTURE_VERSION"
    INVALID_CONNECTION_STATE = "INVALID_CONNECTION_STATE"
    MIGRATION_EXECUTION_FAILED = "MIGRATION_EXECUTION_FAILED"


class MigrationError(RuntimeError):
    """Raised when the migration runner rejects or cannot apply a database state."""

    def __init__(self, code: MigrationFailureCode, detail: str) -> None:
        self.code = code
        super().__init__(f"{code.value}: {detail}")


@dataclass(frozen=True)
class Migration:
    """An immutable, ordered collection of single SQLite statements."""

    version: int
    statements: tuple[str, ...]


PRODUCTION_MIGRATIONS = (
    Migration(
        version=1,
        statements=(
            """
            CREATE TABLE schema_migrations (
                version INTEGER PRIMARY KEY,
                applied_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE development_runs (
                run_id TEXT PRIMARY KEY,
                milestone_contract_digest TEXT NOT NULL,
                current_state TEXT NOT NULL,
                state_version INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE state_events (
                event_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL REFERENCES development_runs(run_id),
                from_state TEXT NOT NULL,
                to_state TEXT NOT NULL,
                transition_reason TEXT NOT NULL,
                occurred_at TEXT NOT NULL,
                state_version INTEGER NOT NULL,
                UNIQUE(run_id, state_version)
            )
            """,
        ),
    ),
    Migration(
        version=2,
        statements=(
            """
            CREATE TABLE phases (
                project_id TEXT NOT NULL,
                phase_id TEXT NOT NULL,
                contract_version TEXT NOT NULL,
                contract_digest TEXT NOT NULL UNIQUE
                    CHECK(length(contract_digest) = 64
                          AND contract_digest NOT GLOB '*[^0-9a-f]*'),
                PRIMARY KEY(project_id, phase_id)
            )
            """,
            """
            CREATE TABLE milestone_contracts (
                project_id TEXT NOT NULL,
                phase_id TEXT NOT NULL,
                milestone_id TEXT NOT NULL,
                contract_version TEXT NOT NULL,
                objective TEXT NOT NULL,
                scope_json TEXT NOT NULL,
                exclusions_json TEXT NOT NULL,
                acceptance_criteria_json TEXT NOT NULL,
                allowed_paths_json TEXT NOT NULL,
                forbidden_paths_json TEXT NOT NULL,
                verification_plan_json TEXT NOT NULL,
                stop_conditions_json TEXT NOT NULL,
                contract_digest TEXT NOT NULL UNIQUE
                    CHECK(length(contract_digest) = 64
                          AND contract_digest NOT GLOB '*[^0-9a-f]*'),
                PRIMARY KEY(project_id, phase_id, milestone_id),
                FOREIGN KEY(project_id, phase_id)
                    REFERENCES phases(project_id, phase_id)
                    ON UPDATE RESTRICT ON DELETE RESTRICT
            )
            """,
            """
            CREATE TABLE approvals (
                approval_id TEXT PRIMARY KEY,
                approval_version TEXT NOT NULL,
                approval_kind TEXT NOT NULL,
                subject_id TEXT NOT NULL,
                subject_digest TEXT NOT NULL
                    CHECK(length(subject_digest) = 64
                          AND subject_digest NOT GLOB '*[^0-9a-f]*'),
                target_kind TEXT NOT NULL,
                target_id TEXT NOT NULL,
                target_branch TEXT NOT NULL,
                base_commit TEXT NOT NULL,
                allowed_actions_json TEXT NOT NULL,
                allowed_paths_json TEXT NOT NULL,
                approver_id TEXT NOT NULL,
                approved_at TEXT NOT NULL,
                binding_digest TEXT NOT NULL UNIQUE
                    CHECK(length(binding_digest) = 64
                          AND binding_digest NOT GLOB '*[^0-9a-f]*')
            )
            """,
        ),
    ),
    Migration(
        version=3,
        statements=(
            """
            CREATE TABLE project_policies (
                project_id TEXT NOT NULL PRIMARY KEY,
                policy_version TEXT NOT NULL,
                project_root TEXT NOT NULL
            )
            """,
        ),
    ),
)

_FORBIDDEN_OPERATION_TOKENS = frozenset(
    {"BEGIN", "COMMIT", "ROLLBACK", "SAVEPOINT", "RELEASE", "VACUUM", "ATTACH", "DETACH"}
)
_SQL_IDENTIFIER_OR_KEYWORD_TOKEN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def validate_migration_registry(
    registry: tuple[Migration, ...],
    *,
    require_production_version: bool = False,
) -> tuple[Migration, ...]:
    """Validate a declared registry without opening or mutating a database."""

    if not isinstance(registry, tuple) or not registry:
        raise MigrationError(MigrationFailureCode.INVALID_REGISTRY, "registry")

    versions: list[int] = []
    for migration in registry:
        if not isinstance(migration, Migration):
            raise MigrationError(MigrationFailureCode.INVALID_REGISTRY, "descriptor")
        if isinstance(migration.version, bool) or not isinstance(migration.version, int) or migration.version <= 0:
            raise MigrationError(MigrationFailureCode.INVALID_REGISTRY, "version")
        if not isinstance(migration.statements, tuple) or not migration.statements:
            raise MigrationError(MigrationFailureCode.INVALID_REGISTRY, "statements")
        for statement in migration.statements:
            if not isinstance(statement, str) or not statement.strip():
                raise MigrationError(MigrationFailureCode.INVALID_REGISTRY, "statement")
            tokens = frozenset(_SQL_IDENTIFIER_OR_KEYWORD_TOKEN.findall(statement.upper()))
            if tokens & _FORBIDDEN_OPERATION_TOKENS:
                raise MigrationError(MigrationFailureCode.INVALID_REGISTRY, "transaction control")
        versions.append(migration.version)

    if len(set(versions)) != len(versions):
        raise MigrationError(MigrationFailureCode.INVALID_REGISTRY, "duplicate version")
    if versions != sorted(versions):
        raise MigrationError(MigrationFailureCode.INVALID_REGISTRY, "declared order")
    if versions != list(range(1, len(versions) + 1)):
        raise MigrationError(MigrationFailureCode.INVALID_REGISTRY, "contiguous versions")
    if require_production_version and versions != [1, 2, 3]:
        raise MigrationError(MigrationFailureCode.INVALID_REGISTRY, "production version")
    return registry


def initialize_database(database_path: Path, applied_at: str) -> None:
    """Initialize an explicit production database path through migration version 3."""

    registry = validate_migration_registry(PRODUCTION_MIGRATIONS, require_production_version=True)
    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        _apply_validated_migrations(connection, applied_at, registry)
    finally:
        connection.close()


def apply_migrations(
    connection: sqlite3.Connection,
    applied_at: str,
    registry: tuple[Migration, ...],
) -> None:
    """Apply a private synthetic or production-like registry on one connection."""

    validated_registry = validate_migration_registry(registry)
    _apply_validated_migrations(connection, applied_at, validated_registry)


def _apply_validated_migrations(
    connection: sqlite3.Connection,
    applied_at: str,
    registry: tuple[Migration, ...],
) -> None:
    if connection.in_transaction:
        raise MigrationError(MigrationFailureCode.INVALID_CONNECTION_STATE, "active transaction")

    applied_versions = _read_applied_history(connection, len(registry))
    for migration in registry[len(applied_versions) :]:
        try:
            connection.execute("BEGIN IMMEDIATE")
            for statement in migration.statements:
                connection.execute(statement)
            connection.execute(
                "INSERT INTO schema_migrations(version, applied_at) VALUES(?, ?)",
                (migration.version, applied_at),
            )
            connection.commit()
        except Exception as error:
            if connection.in_transaction:
                connection.rollback()
            raise MigrationError(
                MigrationFailureCode.MIGRATION_EXECUTION_FAILED,
                f"migration {migration.version}",
            ) from error


def _read_applied_history(connection: sqlite3.Connection, registry_version: int) -> list[int]:
    try:
        user_objects = connection.execute(
            """
            SELECT name FROM sqlite_master
            WHERE name NOT LIKE 'sqlite_%' AND type IN ('index', 'table', 'trigger', 'view')
            ORDER BY name
            """
        ).fetchall()
    except sqlite3.Error as error:
        raise MigrationError(MigrationFailureCode.INVALID_APPLIED_HISTORY, "schema inspection") from error

    has_ledger = any(row["name"] == "schema_migrations" for row in user_objects)
    if not has_ledger:
        if user_objects:
            raise MigrationError(MigrationFailureCode.INVALID_APPLIED_HISTORY, "missing ledger")
        return []

    _validate_ledger_structure(connection)
    try:
        rows = connection.execute("SELECT version FROM schema_migrations ORDER BY version ASC").fetchall()
    except sqlite3.Error as error:
        raise MigrationError(MigrationFailureCode.INVALID_APPLIED_HISTORY, "ledger read") from error
    if not rows:
        raise MigrationError(MigrationFailureCode.INVALID_APPLIED_HISTORY, "empty ledger")

    versions: list[int] = []
    for row in rows:
        version = row["version"]
        if isinstance(version, bool) or not isinstance(version, int) or version <= 0:
            raise MigrationError(MigrationFailureCode.INVALID_APPLIED_HISTORY, "ledger version")
        versions.append(version)
    if any(version > registry_version for version in versions):
        raise MigrationError(MigrationFailureCode.UNKNOWN_FUTURE_VERSION, "ledger version")
    if versions != list(range(1, len(versions) + 1)):
        raise MigrationError(MigrationFailureCode.INVALID_APPLIED_HISTORY, "non-prefix ledger")
    return versions


def _validate_ledger_structure(connection: sqlite3.Connection) -> None:
    try:
        columns = connection.execute("PRAGMA table_info(schema_migrations)").fetchall()
    except sqlite3.Error as error:
        raise MigrationError(MigrationFailureCode.INVALID_APPLIED_HISTORY, "ledger structure") from error
    shape = [(row["name"], row["type"].upper(), row["notnull"], row["pk"]) for row in columns]
    if shape != [("version", "INTEGER", 0, 1), ("applied_at", "TEXT", 1, 0)]:
        raise MigrationError(MigrationFailureCode.INVALID_APPLIED_HISTORY, "ledger structure")
