"""Internal controlled SQLite migration support through the DL-2.1 schema."""

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
    Migration(
        version=4,
        statements=(
            """
            CREATE TABLE workflow_commands (
                queue_sequence INTEGER PRIMARY KEY AUTOINCREMENT
                    CHECK(queue_sequence > 0),
                command_id TEXT NOT NULL UNIQUE
                    CHECK(length(command_id) = 36
                          AND substr(command_id, 9, 1) = '-'
                          AND substr(command_id, 14, 1) = '-'
                          AND substr(command_id, 19, 1) = '-'
                          AND substr(command_id, 24, 1) = '-'
                          AND length(replace(command_id, '-', '')) = 32
                          AND command_id NOT GLOB '*[^0-9a-f-]*'),
                project_id TEXT NOT NULL
                    CHECK(length(CAST(project_id AS BLOB)) BETWEEN 1 AND 256),
                development_run_id TEXT NULL
                    CHECK(development_run_id IS NULL OR
                          length(CAST(development_run_id AS BLOB)) BETWEEN 1 AND 256),
                phase_id TEXT NULL
                    CHECK(phase_id IS NULL OR
                          length(CAST(phase_id AS BLOB)) BETWEEN 1 AND 256),
                command_kind TEXT NOT NULL
                    CHECK(length(command_kind) BETWEEN 1 AND 64
                          AND substr(command_kind, 1, 1) GLOB '[A-Z]'
                          AND command_kind NOT GLOB '*[^A-Z0-9_]*'),
                command_schema_version INTEGER NOT NULL
                    CHECK(command_schema_version BETWEEN 1 AND 2147483647),
                payload_json TEXT NOT NULL
                    CHECK(length(CAST(payload_json AS BLOB)) BETWEEN 2 AND 65536),
                intent_digest TEXT NOT NULL
                    CHECK(length(intent_digest) = 64
                          AND intent_digest NOT GLOB '*[^0-9a-f]*'),
                idempotency_key TEXT NOT NULL
                    CHECK(length(idempotency_key) BETWEEN 1 AND 128
                          AND substr(idempotency_key, 1, 1) GLOB '[A-Za-z0-9]'
                          AND idempotency_key NOT GLOB '*[^A-Za-z0-9._:/-]*'),
                priority INTEGER NOT NULL DEFAULT 0
                    CHECK(priority BETWEEN 0 AND 100),
                state TEXT NOT NULL
                    CHECK(state IN ('PENDING','CLAIMED','RUNNING','SUCCEEDED','FAILED','CANCELLED')),
                state_version INTEGER NOT NULL
                    CHECK(state_version BETWEEN 1 AND 9223372036854775807),
                claim_count INTEGER NOT NULL DEFAULT 0
                    CHECK(claim_count BETWEEN 0 AND 9223372036854775807),
                lease_owner TEXT NULL
                    CHECK(lease_owner IS NULL OR
                          (length(lease_owner) BETWEEN 1 AND 128
                           AND substr(lease_owner, 1, 1) GLOB '[A-Za-z0-9]'
                           AND lease_owner NOT GLOB '*[^A-Za-z0-9._:@/-]*')),
                lease_acquired_at TEXT NULL,
                lease_expires_at TEXT NULL,
                cancellation_requested_at TEXT NULL,
                cancellation_requested_by TEXT NULL
                    CHECK(cancellation_requested_by IS NULL OR
                          (length(cancellation_requested_by) BETWEEN 1 AND 128
                           AND substr(cancellation_requested_by, 1, 1) GLOB '[A-Za-z0-9]'
                           AND cancellation_requested_by NOT GLOB '*[^A-Za-z0-9._:@/-]*')),
                cancellation_reason_code TEXT NULL
                    CHECK(cancellation_reason_code IS NULL OR
                          (length(cancellation_reason_code) BETWEEN 1 AND 64
                           AND substr(cancellation_reason_code, 1, 1) GLOB '[A-Z]'
                           AND cancellation_reason_code NOT GLOB '*[^A-Z0-9_]*')),
                failure_code TEXT NULL
                    CHECK(failure_code IS NULL OR
                          (length(failure_code) BETWEEN 1 AND 64
                           AND substr(failure_code, 1, 1) GLOB '[A-Z]'
                           AND failure_code NOT GLOB '*[^A-Z0-9_]*')),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                started_at TEXT NULL,
                completed_at TEXT NULL,
                UNIQUE(project_id, idempotency_key),
                FOREIGN KEY(development_run_id) REFERENCES development_runs(run_id)
                    ON UPDATE RESTRICT ON DELETE RESTRICT,
                FOREIGN KEY(project_id, phase_id) REFERENCES phases(project_id, phase_id)
                    ON UPDATE RESTRICT ON DELETE RESTRICT,
                CHECK((state IN ('CLAIMED','RUNNING')) =
                      (lease_owner IS NOT NULL AND lease_acquired_at IS NOT NULL
                       AND lease_expires_at IS NOT NULL)),
                CHECK((state IN ('CLAIMED','RUNNING')) OR
                      (lease_owner IS NULL AND lease_acquired_at IS NULL
                       AND lease_expires_at IS NULL)),
                CHECK(lease_acquired_at IS NULL OR lease_acquired_at < lease_expires_at),
                CHECK((cancellation_requested_at IS NULL
                       AND cancellation_requested_by IS NULL
                       AND cancellation_reason_code IS NULL)
                      OR
                      (cancellation_requested_at IS NOT NULL
                       AND cancellation_requested_by IS NOT NULL
                       AND cancellation_reason_code IS NOT NULL)),
                CHECK(state != 'PENDING' OR cancellation_requested_at IS NULL),
                CHECK(state != 'CANCELLED' OR cancellation_requested_at IS NOT NULL),
                CHECK((state = 'FAILED') = (failure_code IS NOT NULL)),
                CHECK(state NOT IN ('PENDING','CLAIMED') OR started_at IS NULL),
                CHECK(state NOT IN ('RUNNING','SUCCEEDED','FAILED') OR started_at IS NOT NULL),
                CHECK((state IN ('SUCCEEDED','FAILED','CANCELLED')) = (completed_at IS NOT NULL)),
                CHECK(updated_at >= created_at),
                CHECK(started_at IS NULL OR started_at >= created_at),
                CHECK(completed_at IS NULL OR completed_at >= created_at),
                CHECK(completed_at IS NULL OR started_at IS NULL OR completed_at >= started_at),
                CHECK(cancellation_requested_at IS NULL OR cancellation_requested_at >= created_at),
                CHECK(length(created_at) = 27 AND substr(created_at,5,1)='-'
                      AND substr(created_at,8,1)='-' AND substr(created_at,11,1)='T'
                      AND substr(created_at,14,1)=':' AND substr(created_at,17,1)=':'
                      AND substr(created_at,20,1)='.' AND substr(created_at,27,1)='Z'
                      AND length(replace(replace(replace(replace(replace(created_at,'-',''),'T',''),':',''),'.',''),'Z',''))=20
                      AND replace(replace(replace(replace(replace(created_at,'-',''),'T',''),':',''),'.',''),'Z','') NOT GLOB '*[^0-9]*'),
                CHECK(length(updated_at) = 27 AND substr(updated_at,5,1)='-'
                      AND substr(updated_at,8,1)='-' AND substr(updated_at,11,1)='T'
                      AND substr(updated_at,14,1)=':' AND substr(updated_at,17,1)=':'
                      AND substr(updated_at,20,1)='.' AND substr(updated_at,27,1)='Z'
                      AND length(replace(replace(replace(replace(replace(updated_at,'-',''),'T',''),':',''),'.',''),'Z',''))=20
                      AND replace(replace(replace(replace(replace(updated_at,'-',''),'T',''),':',''),'.',''),'Z','') NOT GLOB '*[^0-9]*'),
                CHECK(lease_acquired_at IS NULL OR
                      (length(lease_acquired_at)=27 AND substr(lease_acquired_at,5,1)='-'
                       AND substr(lease_acquired_at,8,1)='-' AND substr(lease_acquired_at,11,1)='T'
                       AND substr(lease_acquired_at,14,1)=':' AND substr(lease_acquired_at,17,1)=':'
                       AND substr(lease_acquired_at,20,1)='.' AND substr(lease_acquired_at,27,1)='Z'
                       AND length(replace(replace(replace(replace(replace(lease_acquired_at,'-',''),'T',''),':',''),'.',''),'Z',''))=20
                       AND replace(replace(replace(replace(replace(lease_acquired_at,'-',''),'T',''),':',''),'.',''),'Z','') NOT GLOB '*[^0-9]*')),
                CHECK(lease_expires_at IS NULL OR
                      (length(lease_expires_at)=27 AND substr(lease_expires_at,5,1)='-'
                       AND substr(lease_expires_at,8,1)='-' AND substr(lease_expires_at,11,1)='T'
                       AND substr(lease_expires_at,14,1)=':' AND substr(lease_expires_at,17,1)=':'
                       AND substr(lease_expires_at,20,1)='.' AND substr(lease_expires_at,27,1)='Z'
                       AND length(replace(replace(replace(replace(replace(lease_expires_at,'-',''),'T',''),':',''),'.',''),'Z',''))=20
                       AND replace(replace(replace(replace(replace(lease_expires_at,'-',''),'T',''),':',''),'.',''),'Z','') NOT GLOB '*[^0-9]*')),
                CHECK(cancellation_requested_at IS NULL OR
                      (length(cancellation_requested_at)=27 AND substr(cancellation_requested_at,5,1)='-'
                       AND substr(cancellation_requested_at,8,1)='-' AND substr(cancellation_requested_at,11,1)='T'
                       AND substr(cancellation_requested_at,14,1)=':' AND substr(cancellation_requested_at,17,1)=':'
                       AND substr(cancellation_requested_at,20,1)='.' AND substr(cancellation_requested_at,27,1)='Z'
                       AND length(replace(replace(replace(replace(replace(cancellation_requested_at,'-',''),'T',''),':',''),'.',''),'Z',''))=20
                       AND replace(replace(replace(replace(replace(cancellation_requested_at,'-',''),'T',''),':',''),'.',''),'Z','') NOT GLOB '*[^0-9]*')),
                CHECK(started_at IS NULL OR
                      (length(started_at)=27 AND substr(started_at,5,1)='-'
                       AND substr(started_at,8,1)='-' AND substr(started_at,11,1)='T'
                       AND substr(started_at,14,1)=':' AND substr(started_at,17,1)=':'
                       AND substr(started_at,20,1)='.' AND substr(started_at,27,1)='Z'
                       AND length(replace(replace(replace(replace(replace(started_at,'-',''),'T',''),':',''),'.',''),'Z',''))=20
                       AND replace(replace(replace(replace(replace(started_at,'-',''),'T',''),':',''),'.',''),'Z','') NOT GLOB '*[^0-9]*')),
                CHECK(completed_at IS NULL OR
                      (length(completed_at)=27 AND substr(completed_at,5,1)='-'
                       AND substr(completed_at,8,1)='-' AND substr(completed_at,11,1)='T'
                       AND substr(completed_at,14,1)=':' AND substr(completed_at,17,1)=':'
                       AND substr(completed_at,20,1)='.' AND substr(completed_at,27,1)='Z'
                       AND length(replace(replace(replace(replace(replace(completed_at,'-',''),'T',''),':',''),'.',''),'Z',''))=20
                       AND replace(replace(replace(replace(replace(completed_at,'-',''),'T',''),':',''),'.',''),'Z','') NOT GLOB '*[^0-9]*'))
            )
            """,
            """
            CREATE TABLE workflow_command_events (
                event_sequence INTEGER PRIMARY KEY AUTOINCREMENT
                    CHECK(event_sequence > 0),
                event_id TEXT NOT NULL UNIQUE
                    CHECK(length(event_id)=36
                          AND substr(event_id,9,1)='-' AND substr(event_id,14,1)='-'
                          AND substr(event_id,19,1)='-' AND substr(event_id,24,1)='-'
                          AND length(replace(event_id,'-',''))=32
                          AND event_id NOT GLOB '*[^0-9a-f-]*'),
                command_id TEXT NOT NULL,
                event_kind TEXT NOT NULL
                    CHECK(event_kind IN ('ENQUEUED','CLAIMED','LEASE_RENEWED','STARTED',
                                         'CANCELLATION_REQUESTED','CANCELLED',
                                         'EXPIRED_CLAIM_RELEASED','SUCCEEDED','FAILED')),
                prior_state TEXT NULL
                    CHECK(prior_state IS NULL OR prior_state IN
                          ('PENDING','CLAIMED','RUNNING','SUCCEEDED','FAILED','CANCELLED')),
                next_state TEXT NOT NULL
                    CHECK(next_state IN ('PENDING','CLAIMED','RUNNING','SUCCEEDED','FAILED','CANCELLED')),
                prior_state_version INTEGER NULL
                    CHECK(prior_state_version IS NULL OR
                          prior_state_version BETWEEN 1 AND 9223372036854775806),
                next_state_version INTEGER NOT NULL
                    CHECK(next_state_version BETWEEN 1 AND 9223372036854775807),
                actor_id TEXT NOT NULL
                    CHECK(length(actor_id) BETWEEN 1 AND 128
                          AND substr(actor_id,1,1) GLOB '[A-Za-z0-9]'
                          AND actor_id NOT GLOB '*[^A-Za-z0-9._:@/-]*'),
                occurred_at TEXT NOT NULL
                    CHECK(length(occurred_at)=27 AND substr(occurred_at,5,1)='-'
                          AND substr(occurred_at,8,1)='-' AND substr(occurred_at,11,1)='T'
                          AND substr(occurred_at,14,1)=':' AND substr(occurred_at,17,1)=':'
                          AND substr(occurred_at,20,1)='.' AND substr(occurred_at,27,1)='Z'
                          AND length(replace(replace(replace(replace(replace(occurred_at,'-',''),'T',''),':',''),'.',''),'Z',''))=20
                          AND replace(replace(replace(replace(replace(occurred_at,'-',''),'T',''),':',''),'.',''),'Z','') NOT GLOB '*[^0-9]*'),
                lease_owner TEXT NULL
                    CHECK(lease_owner IS NULL OR
                          (length(lease_owner) BETWEEN 1 AND 128
                           AND substr(lease_owner,1,1) GLOB '[A-Za-z0-9]'
                           AND lease_owner NOT GLOB '*[^A-Za-z0-9._:@/-]*')),
                lease_expires_at TEXT NULL
                    CHECK(lease_expires_at IS NULL OR
                          (length(lease_expires_at)=27 AND substr(lease_expires_at,5,1)='-'
                           AND substr(lease_expires_at,8,1)='-' AND substr(lease_expires_at,11,1)='T'
                           AND substr(lease_expires_at,14,1)=':' AND substr(lease_expires_at,17,1)=':'
                           AND substr(lease_expires_at,20,1)='.' AND substr(lease_expires_at,27,1)='Z'
                           AND length(replace(replace(replace(replace(replace(lease_expires_at,'-',''),'T',''),':',''),'.',''),'Z',''))=20
                           AND replace(replace(replace(replace(replace(lease_expires_at,'-',''),'T',''),':',''),'.',''),'Z','') NOT GLOB '*[^0-9]*')),
                claim_count INTEGER NOT NULL
                    CHECK(claim_count BETWEEN 0 AND 9223372036854775807),
                reason_code TEXT NULL
                    CHECK(reason_code IS NULL OR
                          (length(reason_code) BETWEEN 1 AND 64
                           AND substr(reason_code,1,1) GLOB '[A-Z]'
                           AND reason_code NOT GLOB '*[^A-Z0-9_]*')),
                UNIQUE(command_id, next_state_version),
                FOREIGN KEY(command_id) REFERENCES workflow_commands(command_id)
                    ON UPDATE RESTRICT ON DELETE RESTRICT,
                CHECK((prior_state IS NULL) = (prior_state_version IS NULL)),
                CHECK((event_kind='ENQUEUED' AND prior_state IS NULL
                       AND next_state='PENDING' AND next_state_version=1)
                      OR
                      (event_kind!='ENQUEUED' AND prior_state IS NOT NULL
                       AND next_state_version=prior_state_version+1)),
                CHECK((lease_owner IS NULL) = (lease_expires_at IS NULL))
            )
            """,
            """
            CREATE INDEX workflow_commands_claim_order_idx
            ON workflow_commands(state, priority DESC, queue_sequence ASC)
            """,
            """
            CREATE INDEX workflow_commands_lease_expiry_idx
            ON workflow_commands(state, lease_expires_at ASC)
            """,
            """
            CREATE INDEX workflow_commands_project_sequence_idx
            ON workflow_commands(project_id, queue_sequence ASC)
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
    if require_production_version and versions != [1, 2, 3, 4]:
        raise MigrationError(MigrationFailureCode.INVALID_REGISTRY, "production version")
    return registry


def initialize_database(database_path: Path, applied_at: str) -> None:
    """Initialize an explicit production database path through migration version 4."""

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
