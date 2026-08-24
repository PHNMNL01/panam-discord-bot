"""SQLite repository implementations for the DL-P1.5 foundation entities."""

import json
import sqlite3
from pathlib import Path
from typing import NoReturn

from .models import (
    AcceptedStateEvent,
    ApprovalBinding,
    ApprovalValidationCode,
    ApprovalValidationError,
    ContractValidationCode,
    ContractValidationError,
    DevelopmentRun,
    DevelopmentRunState,
    MilestoneContract,
    PhaseContract,
    ProjectPolicy,
    QueueMutationKind,
    QueueResult,
    QueueResultCode,
    ValidatedCommandEnvelope,
    WorkflowCommand,
    WorkflowCommandEvent,
    WorkflowCommandEventKind,
    WorkflowCommandState,
    _QUEUE_ACTOR_PATTERN,
    _QUEUE_CODE_PATTERN,
    _parse_queue_timestamp,
    _queue_identity,
    _queue_integer,
    _queue_text,
    _queue_uuid,
)
from .repositories import (
    RepositoryError,
    RepositoryFailureCode,
    _validate_query_identity,
)
from .sqlite_migrations import PRODUCTION_MIGRATIONS


_REQUIRED_TABLES = frozenset(
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
    }
)


def _identity_text(*parts: tuple[str, str]) -> str:
    return ",".join(f"{name}={value}" for name, value in parts)


def _raise_repository_error(
    code: RepositoryFailureCode,
    entity_name: str,
    identity: str,
    cause: BaseException,
) -> NoReturn:
    raise RepositoryError(code, entity_name, identity) from cause


def _validate_current_schema(
    connection: sqlite3.Connection,
    entity_name: str,
    identity: str,
) -> None:
    try:
        tables = {
            row["name"]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            )
        }
        if tables != _REQUIRED_TABLES:
            raise RepositoryError(RepositoryFailureCode.SCHEMA_MISMATCH, entity_name, identity)
        versions = [
            row["version"]
            for row in connection.execute(
                "SELECT version FROM schema_migrations ORDER BY version ASC"
            )
        ]
        project_policy_columns = connection.execute(
            "PRAGMA table_xinfo(project_policies)"
        ).fetchall()
        project_policy_indexes = connection.execute(
            "PRAGMA index_list(project_policies)"
        ).fetchall()
    except RepositoryError:
        raise
    except sqlite3.Error as error:
        _raise_repository_error(
            RepositoryFailureCode.SCHEMA_MISMATCH,
            entity_name,
            identity,
            error,
        )
    if versions != [1, 2, 3, 4]:
        raise RepositoryError(RepositoryFailureCode.SCHEMA_MISMATCH, entity_name, identity)
    project_policy_shape = [
        (
            row["name"],
            row["type"].upper(),
            row["notnull"],
            row["pk"],
            row["hidden"],
        )
        for row in project_policy_columns
    ]
    if project_policy_shape != [
        ("project_id", "TEXT", 1, 1, 0),
        ("policy_version", "TEXT", 1, 0, 0),
        ("project_root", "TEXT", 1, 0, 0),
    ]:
        raise RepositoryError(RepositoryFailureCode.SCHEMA_MISMATCH, entity_name, identity)

    primary_key_indexes = [
        row
        for row in project_policy_indexes
        if row["origin"] == "pk"
    ]
    if (
        len(primary_key_indexes) != 1
        or primary_key_indexes[0]["unique"] != 1
        or primary_key_indexes[0]["partial"] != 0
    ):
        raise RepositoryError(RepositoryFailureCode.SCHEMA_MISMATCH, entity_name, identity)

    try:
        primary_key_columns = connection.execute(
            "SELECT seqno, cid, name, desc, coll, key "
            "FROM pragma_index_xinfo(?) ORDER BY seqno",
            (primary_key_indexes[0]["name"],),
        ).fetchall()
    except sqlite3.Error as error:
        _raise_repository_error(
            RepositoryFailureCode.SCHEMA_MISMATCH,
            entity_name,
            identity,
            error,
        )
    primary_key_shape = [
        (row["cid"], row["name"], row["coll"].upper())
        for row in primary_key_columns
        if row["key"] == 1 and isinstance(row["coll"], str)
    ]
    if primary_key_shape != [(0, "project_id", "BINARY")]:
        raise RepositoryError(RepositoryFailureCode.SCHEMA_MISMATCH, entity_name, identity)
    _validate_queue_schema(connection, entity_name, identity)


def _validate_queue_schema(
    connection: sqlite3.Connection,
    entity_name: str,
    identity: str,
) -> None:
    expected_commands = [
        ("queue_sequence", "INTEGER", 0, None, 1),
        ("command_id", "TEXT", 1, None, 0),
        ("project_id", "TEXT", 1, None, 0),
        ("development_run_id", "TEXT", 0, None, 0),
        ("phase_id", "TEXT", 0, None, 0),
        ("command_kind", "TEXT", 1, None, 0),
        ("command_schema_version", "INTEGER", 1, None, 0),
        ("payload_json", "TEXT", 1, None, 0),
        ("intent_digest", "TEXT", 1, None, 0),
        ("idempotency_key", "TEXT", 1, None, 0),
        ("priority", "INTEGER", 1, "0", 0),
        ("state", "TEXT", 1, None, 0),
        ("state_version", "INTEGER", 1, None, 0),
        ("claim_count", "INTEGER", 1, "0", 0),
        ("lease_owner", "TEXT", 0, None, 0),
        ("lease_acquired_at", "TEXT", 0, None, 0),
        ("lease_expires_at", "TEXT", 0, None, 0),
        ("cancellation_requested_at", "TEXT", 0, None, 0),
        ("cancellation_requested_by", "TEXT", 0, None, 0),
        ("cancellation_reason_code", "TEXT", 0, None, 0),
        ("failure_code", "TEXT", 0, None, 0),
        ("created_at", "TEXT", 1, None, 0),
        ("updated_at", "TEXT", 1, None, 0),
        ("started_at", "TEXT", 0, None, 0),
        ("completed_at", "TEXT", 0, None, 0),
    ]
    expected_events = [
        ("event_sequence", "INTEGER", 0, None, 1),
        ("event_id", "TEXT", 1, None, 0),
        ("command_id", "TEXT", 1, None, 0),
        ("event_kind", "TEXT", 1, None, 0),
        ("prior_state", "TEXT", 0, None, 0),
        ("next_state", "TEXT", 1, None, 0),
        ("prior_state_version", "INTEGER", 0, None, 0),
        ("next_state_version", "INTEGER", 1, None, 0),
        ("actor_id", "TEXT", 1, None, 0),
        ("occurred_at", "TEXT", 1, None, 0),
        ("lease_owner", "TEXT", 0, None, 0),
        ("lease_expires_at", "TEXT", 0, None, 0),
        ("claim_count", "INTEGER", 1, None, 0),
        ("reason_code", "TEXT", 0, None, 0),
    ]
    try:
        command_columns = connection.execute("PRAGMA table_xinfo(workflow_commands)").fetchall()
        event_columns = connection.execute(
            "PRAGMA table_xinfo(workflow_command_events)"
        ).fetchall()
        command_indexes = connection.execute(
            "PRAGMA index_list(workflow_commands)"
        ).fetchall()
        event_indexes = connection.execute(
            "PRAGMA index_list(workflow_command_events)"
        ).fetchall()
        command_fks = connection.execute(
            "PRAGMA foreign_key_list(workflow_commands)"
        ).fetchall()
        event_fks = connection.execute(
            "PRAGMA foreign_key_list(workflow_command_events)"
        ).fetchall()
        triggers = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='trigger' "
            "AND tbl_name IN ('workflow_commands','workflow_command_events')"
        ).fetchall()
        table_sql = {
            row["name"]: row["sql"]
            for row in connection.execute(
                "SELECT name, sql FROM sqlite_master WHERE type='table' "
                "AND name IN ('workflow_commands','workflow_command_events')"
            )
        }
    except sqlite3.Error as error:
        _raise_repository_error(
            RepositoryFailureCode.SCHEMA_MISMATCH, entity_name, identity, error
        )
    shape = lambda rows: [
        (row["name"], row["type"].upper(), row["notnull"], row["dflt_value"], row["pk"])
        for row in rows
        if row["hidden"] == 0
    ]
    if shape(command_columns) != expected_commands or shape(event_columns) != expected_events:
        raise RepositoryError(RepositoryFailureCode.SCHEMA_MISMATCH, entity_name, identity)
    explicit = {
        row["name"]: row
        for row in command_indexes
        if row["origin"] == "c"
    }
    expected_index_shapes = {
        "workflow_commands_claim_order_idx": [
            ("state", 0), ("priority", 1), ("queue_sequence", 0)
        ],
        "workflow_commands_lease_expiry_idx": [("state", 0), ("lease_expires_at", 0)],
        "workflow_commands_project_sequence_idx": [("project_id", 0), ("queue_sequence", 0)],
    }
    if set(explicit) != set(expected_index_shapes) or any(
        row["unique"] != 0 or row["partial"] != 0 for row in explicit.values()
    ):
        raise RepositoryError(RepositoryFailureCode.SCHEMA_MISMATCH, entity_name, identity)
    if any(row["origin"] == "c" for row in event_indexes):
        raise RepositoryError(RepositoryFailureCode.SCHEMA_MISMATCH, entity_name, identity)
    try:
        for name, expected in expected_index_shapes.items():
            rows = connection.execute(
                "SELECT name, desc, key FROM pragma_index_xinfo(?) ORDER BY seqno",
                (name,),
            ).fetchall()
            actual = [(row["name"], row["desc"]) for row in rows if row["key"] == 1]
            if actual != expected:
                raise RepositoryError(
                    RepositoryFailureCode.SCHEMA_MISMATCH, entity_name, identity
                )
    except sqlite3.Error as error:
        _raise_repository_error(
            RepositoryFailureCode.SCHEMA_MISMATCH, entity_name, identity, error
        )

    def automatic_unique_shapes(
        indexes: list[sqlite3.Row],
    ) -> set[tuple[tuple[str, int, str], ...]]:
        shapes: set[tuple[tuple[str, int, str], ...]] = set()
        for index in indexes:
            if index["origin"] != "u":
                continue
            if index["unique"] != 1 or index["partial"] != 0:
                raise RepositoryError(
                    RepositoryFailureCode.SCHEMA_MISMATCH, entity_name, identity
                )
            rows = connection.execute(
                "SELECT name, desc, coll, key FROM pragma_index_xinfo(?) ORDER BY seqno",
                (index["name"],),
            ).fetchall()
            shapes.add(
                tuple(
                    (row["name"], row["desc"], row["coll"].upper())
                    for row in rows
                    if row["key"] == 1 and isinstance(row["coll"], str)
                )
            )
        return shapes

    try:
        if automatic_unique_shapes(command_indexes) != {
            (("command_id", 0, "BINARY"),),
            (("project_id", 0, "BINARY"), ("idempotency_key", 0, "BINARY")),
        } or automatic_unique_shapes(event_indexes) != {
            (("event_id", 0, "BINARY"),),
            (("command_id", 0, "BINARY"), ("next_state_version", 0, "BINARY")),
        }:
            raise RepositoryError(
                RepositoryFailureCode.SCHEMA_MISMATCH, entity_name, identity
            )
    except sqlite3.Error as error:
        _raise_repository_error(
            RepositoryFailureCode.SCHEMA_MISMATCH, entity_name, identity, error
        )

    normalize_sql = lambda value: " ".join(value.strip().rstrip(";").split()).lower()
    expected_table_sql = {
        "workflow_commands": PRODUCTION_MIGRATIONS[3].statements[0],
        "workflow_command_events": PRODUCTION_MIGRATIONS[3].statements[1],
    }
    if set(table_sql) != set(expected_table_sql) or any(
        not isinstance(table_sql[name], str)
        or normalize_sql(table_sql[name]) != normalize_sql(statement)
        for name, statement in expected_table_sql.items()
    ):
        raise RepositoryError(RepositoryFailureCode.SCHEMA_MISMATCH, entity_name, identity)
    command_fk_shape = sorted(
        (
            row["table"], row["from"], row["to"], row["on_update"], row["on_delete"], row["seq"]
        )
        for row in command_fks
    )
    if command_fk_shape != sorted(
        [
            ("development_runs", "development_run_id", "run_id", "RESTRICT", "RESTRICT", 0),
            ("phases", "project_id", "project_id", "RESTRICT", "RESTRICT", 0),
            ("phases", "phase_id", "phase_id", "RESTRICT", "RESTRICT", 1),
        ]
    ):
        raise RepositoryError(RepositoryFailureCode.SCHEMA_MISMATCH, entity_name, identity)
    if [
        (row["table"], row["from"], row["to"], row["on_update"], row["on_delete"])
        for row in event_fks
    ] != [("workflow_commands", "command_id", "command_id", "RESTRICT", "RESTRICT")]:
        raise RepositoryError(RepositoryFailureCode.SCHEMA_MISMATCH, entity_name, identity)
    if triggers:
        raise RepositoryError(RepositoryFailureCode.SCHEMA_MISMATCH, entity_name, identity)


def _open_connection(
    database_path: Path,
    entity_name: str,
    identity: str,
) -> sqlite3.Connection:
    try:
        is_file = database_path.is_file()
    except OSError as error:
        _raise_repository_error(
            RepositoryFailureCode.SQLITE_OPERATIONAL_FAILURE,
            entity_name,
            identity,
            error,
        )
    if not is_file:
        raise RepositoryError(RepositoryFailureCode.SCHEMA_MISMATCH, entity_name, identity)

    try:
        connection = sqlite3.connect(database_path, timeout=0.0)
    except sqlite3.Error as error:
        _raise_repository_error(
            RepositoryFailureCode.SQLITE_OPERATIONAL_FAILURE,
            entity_name,
            identity,
            error,
        )
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        if connection.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
            raise RepositoryError(RepositoryFailureCode.SCHEMA_MISMATCH, entity_name, identity)
        _validate_current_schema(connection, entity_name, identity)
    except RepositoryError:
        connection.close()
        raise
    except sqlite3.Error as error:
        connection.close()
        _raise_repository_error(
            RepositoryFailureCode.SQLITE_OPERATIONAL_FAILURE,
            entity_name,
            identity,
            error,
        )
    return connection


def _open_read_only_connection(
    database_path: Path,
    entity_name: str,
    identity: str,
) -> sqlite3.Connection:
    """Open an existing foundation database with SQLite-enforced read-only mode."""

    try:
        is_file = database_path.is_file()
    except OSError as error:
        _raise_repository_error(
            RepositoryFailureCode.SQLITE_OPERATIONAL_FAILURE,
            entity_name,
            identity,
            error,
        )
    if not is_file:
        raise RepositoryError(RepositoryFailureCode.SCHEMA_MISMATCH, entity_name, identity)

    try:
        database_uri = f"{database_path.absolute().as_uri()}?mode=ro"
        connection = sqlite3.connect(database_uri, uri=True, timeout=0.0)
    except (OSError, ValueError, sqlite3.Error) as error:
        _raise_repository_error(
            RepositoryFailureCode.SQLITE_OPERATIONAL_FAILURE,
            entity_name,
            identity,
            error,
        )
    connection.row_factory = sqlite3.Row
    try:
        _validate_current_schema(connection, entity_name, identity)
    except RepositoryError:
        connection.close()
        raise
    except sqlite3.Error as error:
        connection.close()
        _raise_repository_error(
            RepositoryFailureCode.SQLITE_OPERATIONAL_FAILURE,
            entity_name,
            identity,
            error,
        )
    return connection


def _rollback_if_active(connection: sqlite3.Connection) -> None:
    if connection.in_transaction:
        try:
            connection.rollback()
        except sqlite3.Error:
            pass


def _translate_integrity_error(
    error: sqlite3.IntegrityError,
    entity_name: str,
    identity: str,
) -> NoReturn:
    message = str(error).upper()
    if "FOREIGN KEY CONSTRAINT FAILED" in message:
        _raise_repository_error(
            RepositoryFailureCode.RELATIONSHIP_VIOLATION,
            entity_name,
            identity,
            error,
        )
    if "UNIQUE CONSTRAINT FAILED" in message or "PRIMARY KEY" in message:
        _raise_repository_error(
            RepositoryFailureCode.DUPLICATE_ENTITY,
            entity_name,
            identity,
            error,
        )
    _raise_repository_error(
        RepositoryFailureCode.SQLITE_OPERATIONAL_FAILURE,
        entity_name,
        identity,
        error,
    )


def _execute_insert(
    database_path: Path,
    entity_name: str,
    identity: str,
    statement: str,
    parameters: tuple[object, ...],
) -> None:
    connection = _open_connection(database_path, entity_name, identity)
    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(statement, parameters)
        connection.commit()
    except sqlite3.IntegrityError as error:
        _rollback_if_active(connection)
        _translate_integrity_error(error, entity_name, identity)
    except sqlite3.Error as error:
        _rollback_if_active(connection)
        _raise_repository_error(
            RepositoryFailureCode.SQLITE_OPERATIONAL_FAILURE,
            entity_name,
            identity,
            error,
        )
    finally:
        connection.close()


def _fetch_one(
    database_path: Path,
    entity_name: str,
    identity: str,
    statement: str,
    parameters: tuple[object, ...],
) -> sqlite3.Row | None:
    connection = _open_read_only_connection(database_path, entity_name, identity)
    try:
        return connection.execute(statement, parameters).fetchone()
    except sqlite3.Error as error:
        _raise_repository_error(
            RepositoryFailureCode.SQLITE_OPERATIONAL_FAILURE,
            entity_name,
            identity,
            error,
        )
    finally:
        connection.close()


def _fetch_all(
    database_path: Path,
    entity_name: str,
    identity: str,
    statement: str,
    parameters: tuple[object, ...],
) -> list[sqlite3.Row]:
    connection = _open_read_only_connection(database_path, entity_name, identity)
    try:
        return connection.execute(statement, parameters).fetchall()
    except sqlite3.Error as error:
        _raise_repository_error(
            RepositoryFailureCode.SQLITE_OPERATIONAL_FAILURE,
            entity_name,
            identity,
            error,
        )
    finally:
        connection.close()


def _encode_tuple(values: tuple[str, ...]) -> str:
    return json.dumps(list(values), ensure_ascii=False, separators=(",", ":"))


def _decode_tuple(value: object, field_name: str) -> tuple[str, ...]:
    if not isinstance(value, str):
        raise ValueError(field_name)
    decoded = json.loads(value)
    if not isinstance(decoded, list) or any(not isinstance(item, str) for item in decoded):
        raise ValueError(field_name)
    return tuple(decoded)


def _raise_payload_error(
    error: BaseException,
    entity_name: str,
    identity: str,
) -> NoReturn:
    unsupported = (
        isinstance(error, ContractValidationError)
        and error.code is ContractValidationCode.UNSUPPORTED_VERSION
    ) or (
        isinstance(error, ApprovalValidationError)
        and error.code is ApprovalValidationCode.UNSUPPORTED_VERSION
    )
    code = (
        RepositoryFailureCode.UNSUPPORTED_PERSISTED_VERSION
        if unsupported
        else RepositoryFailureCode.MALFORMED_PERSISTED_PAYLOAD
    )
    _raise_repository_error(code, entity_name, identity, error)


def _verify_digest(
    actual_digest: str,
    persisted_digest: object,
    entity_name: str,
    identity: str,
) -> None:
    if not isinstance(persisted_digest, str) or actual_digest != persisted_digest:
        _raise_payload_error(ValueError("digest mismatch"), entity_name, identity)


class SqlitePhaseContractRepository:
    """Insert and reload immutable phase contracts."""

    def __init__(self, database_path: Path) -> None:
        self._database_path = Path(database_path)

    def create(self, contract: PhaseContract) -> PhaseContract:
        identity = _identity_text(
            ("project_id", contract.project_id),
            ("phase_id", contract.phase_id),
        )
        _execute_insert(
            self._database_path,
            "PhaseContract",
            identity,
            """
            INSERT INTO phases(project_id, phase_id, contract_version, contract_digest)
            VALUES(?, ?, ?, ?)
            """,
            (
                contract.project_id,
                contract.phase_id,
                contract.contract_version.value,
                contract.sha256_digest(),
            ),
        )
        return contract

    def get(self, project_id: str, phase_id: str) -> PhaseContract | None:
        project_id = _validate_query_identity(project_id, "PhaseContract", "project_id")
        phase_id = _validate_query_identity(phase_id, "PhaseContract", "phase_id")
        identity = _identity_text(("project_id", project_id), ("phase_id", phase_id))
        row = _fetch_one(
            self._database_path,
            "PhaseContract",
            identity,
            """
            SELECT project_id, phase_id, contract_version, contract_digest
            FROM phases WHERE project_id = ? AND phase_id = ?
            """,
            (project_id, phase_id),
        )
        if row is None:
            return None
        try:
            contract = PhaseContract(row["project_id"], row["phase_id"], row["contract_version"])
            _verify_digest(contract.sha256_digest(), row["contract_digest"], "PhaseContract", identity)
            return contract
        except RepositoryError:
            raise
        except (ContractValidationError, ValueError, TypeError, IndexError, KeyError) as error:
            _raise_payload_error(error, "PhaseContract", identity)


class SqliteMilestoneContractRepository:
    """Insert and reload immutable milestone contracts."""

    def __init__(self, database_path: Path) -> None:
        self._database_path = Path(database_path)

    def create(self, contract: MilestoneContract) -> MilestoneContract:
        identity = _identity_text(
            ("project_id", contract.project_id),
            ("phase_id", contract.phase_id),
            ("milestone_id", contract.milestone_id),
        )
        _execute_insert(
            self._database_path,
            "MilestoneContract",
            identity,
            """
            INSERT INTO milestone_contracts(
                project_id, phase_id, milestone_id, contract_version, objective,
                scope_json, exclusions_json, acceptance_criteria_json,
                allowed_paths_json, forbidden_paths_json, verification_plan_json,
                stop_conditions_json, contract_digest
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                contract.project_id,
                contract.phase_id,
                contract.milestone_id,
                contract.contract_version.value,
                contract.objective,
                _encode_tuple(contract.scope),
                _encode_tuple(contract.exclusions),
                _encode_tuple(contract.acceptance_criteria),
                _encode_tuple(contract.allowed_paths),
                _encode_tuple(contract.forbidden_paths),
                _encode_tuple(contract.verification_plan),
                _encode_tuple(contract.stop_conditions),
                contract.sha256_digest(),
            ),
        )
        return contract

    def get(
        self,
        project_id: str,
        phase_id: str,
        milestone_id: str,
    ) -> MilestoneContract | None:
        project_id = _validate_query_identity(project_id, "MilestoneContract", "project_id")
        phase_id = _validate_query_identity(phase_id, "MilestoneContract", "phase_id")
        milestone_id = _validate_query_identity(
            milestone_id,
            "MilestoneContract",
            "milestone_id",
        )
        identity = _identity_text(
            ("project_id", project_id),
            ("phase_id", phase_id),
            ("milestone_id", milestone_id),
        )
        row = _fetch_one(
            self._database_path,
            "MilestoneContract",
            identity,
            """
            SELECT project_id, phase_id, milestone_id, contract_version, objective,
                   scope_json, exclusions_json, acceptance_criteria_json,
                   allowed_paths_json, forbidden_paths_json, verification_plan_json,
                   stop_conditions_json, contract_digest
            FROM milestone_contracts
            WHERE project_id = ? AND phase_id = ? AND milestone_id = ?
            """,
            (project_id, phase_id, milestone_id),
        )
        if row is None:
            return None
        try:
            contract = MilestoneContract(
                project_id=row["project_id"],
                phase_id=row["phase_id"],
                milestone_id=row["milestone_id"],
                contract_version=row["contract_version"],
                objective=row["objective"],
                scope=_decode_tuple(row["scope_json"], "scope_json"),
                exclusions=_decode_tuple(row["exclusions_json"], "exclusions_json"),
                acceptance_criteria=_decode_tuple(
                    row["acceptance_criteria_json"],
                    "acceptance_criteria_json",
                ),
                allowed_paths=_decode_tuple(row["allowed_paths_json"], "allowed_paths_json"),
                forbidden_paths=_decode_tuple(
                    row["forbidden_paths_json"],
                    "forbidden_paths_json",
                ),
                verification_plan=_decode_tuple(
                    row["verification_plan_json"],
                    "verification_plan_json",
                ),
                stop_conditions=_decode_tuple(
                    row["stop_conditions_json"],
                    "stop_conditions_json",
                ),
            )
            _verify_digest(
                contract.sha256_digest(),
                row["contract_digest"],
                "MilestoneContract",
                identity,
            )
            return contract
        except RepositoryError:
            raise
        except (
            ContractValidationError,
            json.JSONDecodeError,
            ValueError,
            TypeError,
            IndexError,
            KeyError,
        ) as error:
            _raise_payload_error(error, "MilestoneContract", identity)


class SqliteApprovalBindingRepository:
    """Insert and reload immutable approval bindings."""

    def __init__(self, database_path: Path) -> None:
        self._database_path = Path(database_path)

    def create(self, binding: ApprovalBinding) -> ApprovalBinding:
        identity = _identity_text(("approval_id", binding.approval_id),)
        _execute_insert(
            self._database_path,
            "ApprovalBinding",
            identity,
            """
            INSERT INTO approvals(
                approval_id, approval_version, approval_kind, subject_id,
                subject_digest, target_kind, target_id, target_branch, base_commit,
                allowed_actions_json, allowed_paths_json, approver_id, approved_at,
                binding_digest
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                binding.approval_id,
                binding.approval_version.value,
                binding.approval_kind.value,
                binding.subject_id,
                binding.subject_digest,
                binding.target_kind.value,
                binding.target_id,
                binding.target_branch,
                binding.base_commit,
                _encode_tuple(binding.allowed_actions),
                _encode_tuple(binding.allowed_paths),
                binding.approver_id,
                binding.approved_at,
                binding.sha256_digest(),
            ),
        )
        return binding

    def get(self, approval_id: str) -> ApprovalBinding | None:
        approval_id = _validate_query_identity(approval_id, "ApprovalBinding", "approval_id")
        identity = _identity_text(("approval_id", approval_id),)
        row = _fetch_one(
            self._database_path,
            "ApprovalBinding",
            identity,
            """
            SELECT approval_id, approval_version, approval_kind, subject_id,
                   subject_digest, target_kind, target_id, target_branch, base_commit,
                   allowed_actions_json, allowed_paths_json, approver_id, approved_at,
                   binding_digest
            FROM approvals WHERE approval_id = ?
            """,
            (approval_id,),
        )
        if row is None:
            return None
        try:
            binding = ApprovalBinding(
                approval_id=row["approval_id"],
                approval_version=row["approval_version"],
                approval_kind=row["approval_kind"],
                subject_id=row["subject_id"],
                subject_digest=row["subject_digest"],
                target_kind=row["target_kind"],
                target_id=row["target_id"],
                target_branch=row["target_branch"],
                base_commit=row["base_commit"],
                allowed_actions=_decode_tuple(
                    row["allowed_actions_json"],
                    "allowed_actions_json",
                ),
                allowed_paths=_decode_tuple(row["allowed_paths_json"], "allowed_paths_json"),
                approver_id=row["approver_id"],
                approved_at=row["approved_at"],
            )
            _verify_digest(
                binding.sha256_digest(),
                row["binding_digest"],
                "ApprovalBinding",
                identity,
            )
            return binding
        except RepositoryError:
            raise
        except (
            ApprovalValidationError,
            json.JSONDecodeError,
            ValueError,
            TypeError,
            IndexError,
            KeyError,
        ) as error:
            _raise_payload_error(error, "ApprovalBinding", identity)


class SqliteProjectPolicyRepository:
    """Read-only authoritative access to registered Project Policy v1 rows."""

    def __init__(self, database_path: Path) -> None:
        self._database_path = Path(database_path)

    def get(self, project_id: str) -> ProjectPolicy | None:
        project_id = _validate_query_identity(project_id, "ProjectPolicy", "project_id")
        identity = _identity_text(("project_id", project_id),)
        row = _fetch_one(
            self._database_path,
            "ProjectPolicy",
            identity,
            """
            SELECT project_id, policy_version, project_root
            FROM project_policies WHERE project_id = ?
            """,
            (project_id,),
        )
        if row is None:
            return None
        try:
            return ProjectPolicy(
                project_id=row["project_id"],
                policy_version=row["policy_version"],
                project_root=row["project_root"],
            )
        except (ContractValidationError, ValueError, TypeError, IndexError, KeyError) as error:
            _raise_payload_error(error, "ProjectPolicy", identity)


class SqliteDevelopmentRunInspectionRepository:
    """Read-only SQLite adapter for persisted run facts and accepted events."""

    def __init__(self, database_path: Path) -> None:
        self._database_path = Path(database_path)

    def get_run(self, run_id: str) -> DevelopmentRun | None:
        run_id = _validate_query_identity(run_id, "DevelopmentRun", "run_id")
        identity = _identity_text(("run_id", run_id),)
        row = _fetch_one(
            self._database_path,
            "DevelopmentRun",
            identity,
            """
            SELECT run_id, milestone_contract_digest, current_state, state_version,
                   created_at, updated_at
            FROM development_runs WHERE run_id = ?
            """,
            (run_id,),
        )
        if row is None:
            return None
        try:
            return _decode_development_run(row)
        except (ValueError, TypeError, IndexError, KeyError) as error:
            _raise_payload_error(error, "DevelopmentRun", identity)

    def get_history(self, run_id: str) -> list[AcceptedStateEvent]:
        run_id = _validate_query_identity(run_id, "DevelopmentRun", "run_id")
        identity = _identity_text(("run_id", run_id),)
        rows = _fetch_all(
            self._database_path,
            "AcceptedStateEvent",
            identity,
            """
            SELECT event_id, run_id, from_state, to_state, transition_reason,
                   occurred_at, state_version
            FROM state_events WHERE run_id = ? ORDER BY state_version ASC
            """,
            (run_id,),
        )
        try:
            return [_decode_accepted_state_event(row, run_id) for row in rows]
        except (ValueError, TypeError, IndexError, KeyError) as error:
            _raise_payload_error(error, "AcceptedStateEvent", identity)


def _required_persisted_text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(field_name)
    return value


def _required_state_version(value: object, field_name: str, *, minimum: int) -> int:
    if type(value) is not int or value < minimum:
        raise ValueError(field_name)
    return value


def _decode_development_run(row: sqlite3.Row) -> DevelopmentRun:
    return DevelopmentRun(
        run_id=_required_persisted_text(row["run_id"], "run_id"),
        milestone_contract_digest=_required_persisted_text(
            row["milestone_contract_digest"],
            "milestone_contract_digest",
        ),
        current_state=DevelopmentRunState(row["current_state"]),
        state_version=_required_state_version(row["state_version"], "state_version", minimum=0),
        created_at=_required_persisted_text(row["created_at"], "created_at"),
        updated_at=_required_persisted_text(row["updated_at"], "updated_at"),
    )


def _decode_accepted_state_event(row: sqlite3.Row, expected_run_id: str) -> AcceptedStateEvent:
    run_id = _required_persisted_text(row["run_id"], "run_id")
    if run_id != expected_run_id:
        raise ValueError("run_id")
    return AcceptedStateEvent(
        event_id=_required_persisted_text(row["event_id"], "event_id"),
        run_id=run_id,
        from_state=DevelopmentRunState(row["from_state"]),
        to_state=DevelopmentRunState(row["to_state"]),
        transition_reason=_required_persisted_text(
            row["transition_reason"],
            "transition_reason",
        ),
        occurred_at=_required_persisted_text(row["occurred_at"], "occurred_at"),
        state_version=_required_state_version(row["state_version"], "state_version", minimum=1),
    )


_COMMAND_COLUMNS = """
queue_sequence, command_id, project_id, development_run_id, phase_id,
command_kind, command_schema_version, payload_json, intent_digest,
idempotency_key, priority, state, state_version, claim_count,
lease_owner, lease_acquired_at, lease_expires_at,
cancellation_requested_at, cancellation_requested_by,
cancellation_reason_code, failure_code, created_at, updated_at,
started_at, completed_at
"""

_EVENT_COLUMNS = """
event_sequence, event_id, command_id, event_kind, prior_state, next_state,
prior_state_version, next_state_version, actor_id, occurred_at,
lease_owner, lease_expires_at, claim_count, reason_code
"""


def _decode_workflow_command(row: sqlite3.Row) -> WorkflowCommand:
    return WorkflowCommand(
        queue_sequence=row["queue_sequence"],
        command_id=row["command_id"],
        project_id=row["project_id"],
        development_run_id=row["development_run_id"],
        phase_id=row["phase_id"],
        command_kind=row["command_kind"],
        command_schema_version=row["command_schema_version"],
        payload_json=row["payload_json"],
        intent_digest=row["intent_digest"],
        idempotency_key=row["idempotency_key"],
        priority=row["priority"],
        state=WorkflowCommandState(row["state"]),
        state_version=row["state_version"],
        claim_count=row["claim_count"],
        lease_owner=row["lease_owner"],
        lease_acquired_at=row["lease_acquired_at"],
        lease_expires_at=row["lease_expires_at"],
        cancellation_requested_at=row["cancellation_requested_at"],
        cancellation_requested_by=row["cancellation_requested_by"],
        cancellation_reason_code=row["cancellation_reason_code"],
        failure_code=row["failure_code"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        started_at=row["started_at"],
        completed_at=row["completed_at"],
    )


def _decode_workflow_command_event(row: sqlite3.Row) -> WorkflowCommandEvent:
    return WorkflowCommandEvent(
        event_sequence=row["event_sequence"],
        event_id=row["event_id"],
        command_id=row["command_id"],
        event_kind=WorkflowCommandEventKind(row["event_kind"]),
        prior_state=None if row["prior_state"] is None else WorkflowCommandState(row["prior_state"]),
        next_state=WorkflowCommandState(row["next_state"]),
        prior_state_version=row["prior_state_version"],
        next_state_version=row["next_state_version"],
        actor_id=row["actor_id"],
        occurred_at=row["occurred_at"],
        lease_owner=row["lease_owner"],
        lease_expires_at=row["lease_expires_at"],
        claim_count=row["claim_count"],
        reason_code=row["reason_code"],
    )


class SqliteWorkflowCommandRepository:
    """Exact v4 SQLite queue adapter; it performs no command execution."""

    def __init__(self, database_path: Path) -> None:
        self._database_path = Path(database_path)

    @staticmethod
    def _identity(command_id: str) -> str:
        return _identity_text(("command_id", command_id),)

    @staticmethod
    def _is_busy(error: sqlite3.Error) -> bool:
        message = str(error).lower()
        return "locked" in message or "busy" in message

    def _history_in_connection(
        self,
        connection: sqlite3.Connection,
        command: WorkflowCommand,
        identity: str,
    ) -> tuple[WorkflowCommandEvent, ...]:
        rows = connection.execute(
            f"SELECT {_EVENT_COLUMNS} FROM workflow_command_events "
            "WHERE command_id=? ORDER BY next_state_version ASC",
            (command.command_id,),
        ).fetchall()
        try:
            events = tuple(_decode_workflow_command_event(row) for row in rows)
        except (TypeError, ValueError, KeyError, IndexError) as error:
            _raise_payload_error(error, "WorkflowCommandEvent", identity)
        if (
            not events
            or events[0].event_kind is not WorkflowCommandEventKind.ENQUEUED
            or tuple(event.next_state_version for event in events)
            != tuple(range(1, command.state_version + 1))
            or any(event.command_id != command.command_id for event in events)
            or events[-1].next_state is not command.state
            or events[-1].next_state_version != command.state_version
            or events[-1].claim_count != command.claim_count
        ):
            _raise_payload_error(
                ValueError("history continuity"), "WorkflowCommandEvent", identity
            )
        previous: WorkflowCommandEvent | None = None
        tracked_claim_count = 0
        tracked_owner: str | None = None
        tracked_acquired_at: str | None = None
        tracked_expires_at: str | None = None
        started_events: list[WorkflowCommandEvent] = []
        cancellation_events: list[WorkflowCommandEvent] = []
        for event in events:
            if previous is not None and (
                event.prior_state is not previous.next_state
                or event.prior_state_version != previous.next_state_version
                or event.event_sequence <= previous.event_sequence
                or event.occurred_at < previous.occurred_at
            ):
                _raise_payload_error(
                    ValueError("history adjacency"), "WorkflowCommandEvent", identity
                )
            expected_claim_count = tracked_claim_count + (
                1 if event.event_kind is WorkflowCommandEventKind.CLAIMED else 0
            )
            if event.claim_count != expected_claim_count:
                _raise_payload_error(
                    ValueError("history claim count"), "WorkflowCommandEvent", identity
                )
            tracked_claim_count = expected_claim_count

            if event.event_kind is WorkflowCommandEventKind.CLAIMED:
                tracked_owner = event.lease_owner
                tracked_acquired_at = event.occurred_at
                tracked_expires_at = event.lease_expires_at
            elif event.event_kind is WorkflowCommandEventKind.LEASE_RENEWED:
                if (
                    event.lease_owner != tracked_owner
                    or tracked_expires_at is None
                    or event.occurred_at >= tracked_expires_at
                ):
                    _raise_payload_error(
                        ValueError("history lease renewal"), "WorkflowCommandEvent", identity
                    )
                tracked_expires_at = event.lease_expires_at
            elif event.prior_state in {
                WorkflowCommandState.CLAIMED,
                WorkflowCommandState.RUNNING,
            }:
                if (
                    event.lease_owner != tracked_owner
                    or event.lease_expires_at != tracked_expires_at
                ):
                    _raise_payload_error(
                        ValueError("history lease snapshot"),
                        "WorkflowCommandEvent",
                        identity,
                    )
            if event.event_kind is WorkflowCommandEventKind.STARTED:
                started_events.append(event)
            if event.event_kind in {
                WorkflowCommandEventKind.CANCELLATION_REQUESTED,
                WorkflowCommandEventKind.CANCELLED,
            }:
                if (
                    event.event_kind
                    is WorkflowCommandEventKind.CANCELLATION_REQUESTED
                    and cancellation_events
                ):
                    _raise_payload_error(
                        ValueError("history repeated cancellation request"),
                        "WorkflowCommandEvent",
                        identity,
                    )
                if (
                    event.event_kind is WorkflowCommandEventKind.CANCELLED
                    and event.prior_state in {
                        WorkflowCommandState.CLAIMED,
                        WorkflowCommandState.RUNNING,
                    }
                    and not cancellation_events
                ):
                    _raise_payload_error(
                        ValueError("history missing cancellation request"),
                        "WorkflowCommandEvent",
                        identity,
                    )
                if (
                    event.event_kind is WorkflowCommandEventKind.CANCELLED
                    and cancellation_events
                    and event.reason_code != cancellation_events[0].reason_code
                ):
                    _raise_payload_error(
                        ValueError("history cancellation reason drift"),
                        "WorkflowCommandEvent",
                        identity,
                    )
                cancellation_events.append(event)
            if event.next_state not in {
                WorkflowCommandState.CLAIMED,
                WorkflowCommandState.RUNNING,
            }:
                tracked_owner = None
                tracked_acquired_at = None
                tracked_expires_at = None
            previous = event

        first = events[0]
        last = events[-1]
        cancellation = cancellation_events[0] if cancellation_events else None
        terminal = command.state in {
            WorkflowCommandState.SUCCEEDED,
            WorkflowCommandState.FAILED,
            WorkflowCommandState.CANCELLED,
        }
        if (
            first.occurred_at != command.created_at
            or last.occurred_at != command.updated_at
            or tracked_claim_count != command.claim_count
            or (started_events[0].occurred_at if len(started_events) == 1 else None)
            != command.started_at
            or (last.occurred_at if terminal else None) != command.completed_at
            or (cancellation.occurred_at if cancellation else None)
            != command.cancellation_requested_at
            or (cancellation.actor_id if cancellation else None)
            != command.cancellation_requested_by
            or (cancellation.reason_code if cancellation else None)
            != command.cancellation_reason_code
            or (
                last.reason_code
                if last.event_kind is WorkflowCommandEventKind.FAILED
                else None
            )
            != command.failure_code
        ):
            _raise_payload_error(
                ValueError("history aggregate facts"), "WorkflowCommandEvent", identity
            )
        if command.state in {
            WorkflowCommandState.CLAIMED,
            WorkflowCommandState.RUNNING,
        } and (
            tracked_owner != command.lease_owner
            or tracked_acquired_at != command.lease_acquired_at
            or tracked_expires_at != command.lease_expires_at
        ):
            _raise_payload_error(
                ValueError("history aggregate lease"), "WorkflowCommandEvent", identity
            )
        return events

    def _command_from_row(
        self,
        connection: sqlite3.Connection,
        row: sqlite3.Row,
        identity: str,
    ) -> WorkflowCommand:
        try:
            command = _decode_workflow_command(row)
        except (TypeError, ValueError, KeyError, IndexError) as error:
            _raise_payload_error(error, "WorkflowCommand", identity)
        self._history_in_connection(connection, command, identity)
        return command

    def _load_command(
        self,
        connection: sqlite3.Connection,
        command_id: str,
    ) -> WorkflowCommand | None:
        row = connection.execute(
            f"SELECT {_COMMAND_COLUMNS} FROM workflow_commands WHERE command_id=?",
            (command_id,),
        ).fetchone()
        if row is None:
            return None
        return self._command_from_row(connection, row, self._identity(command_id))

    def _event_by_id(
        self,
        connection: sqlite3.Connection,
        event_id: str,
        identity: str,
    ) -> WorkflowCommandEvent:
        row = connection.execute(
            f"SELECT {_EVENT_COLUMNS} FROM workflow_command_events WHERE event_id=?",
            (event_id,),
        ).fetchone()
        if row is None:
            _raise_payload_error(ValueError("missing event"), "WorkflowCommandEvent", identity)
        try:
            return _decode_workflow_command_event(row)
        except (TypeError, ValueError, KeyError, IndexError) as error:
            _raise_payload_error(error, "WorkflowCommandEvent", identity)

    def _mutation(
        self,
        mutation_kind: QueueMutationKind,
        identity: str,
        operation: object,
    ) -> QueueResult:
        try:
            connection = _open_connection(
                self._database_path, "WorkflowCommand", identity
            )
        except RepositoryError as error:
            if isinstance(error.__cause__, sqlite3.Error) and self._is_busy(
                error.__cause__
            ):
                return QueueResult(
                    QueueResultCode.TRANSIENT_CONTENTION, mutation_kind
                )
            raise
        try:
            connection.execute("BEGIN IMMEDIATE")
            result = operation(connection)  # type: ignore[operator]
            if type(result) is not QueueResult or result.mutation_kind is not mutation_kind:
                raise RepositoryError(
                    RepositoryFailureCode.MALFORMED_PERSISTED_PAYLOAD,
                    "WorkflowCommand",
                    identity,
                )
            connection.commit()
            return result
        except sqlite3.OperationalError as error:
            _rollback_if_active(connection)
            if self._is_busy(error):
                return QueueResult(QueueResultCode.TRANSIENT_CONTENTION, mutation_kind)
            _raise_repository_error(
                RepositoryFailureCode.SQLITE_OPERATIONAL_FAILURE,
                "WorkflowCommand",
                identity,
                error,
            )
        except sqlite3.IntegrityError as error:
            _rollback_if_active(connection)
            _translate_integrity_error(error, "WorkflowCommand", identity)
        except (TypeError, ValueError):
            _rollback_if_active(connection)
            raise
        except RepositoryError:
            _rollback_if_active(connection)
            raise
        except sqlite3.Error as error:
            _rollback_if_active(connection)
            _raise_repository_error(
                RepositoryFailureCode.SQLITE_OPERATIONAL_FAILURE,
                "WorkflowCommand",
                identity,
                error,
            )
        finally:
            connection.close()

    @staticmethod
    def _next_version(command: WorkflowCommand) -> int:
        if command.state_version >= 9_223_372_036_854_775_807:
            raise RepositoryError(
                RepositoryFailureCode.MALFORMED_PERSISTED_PAYLOAD,
                "WorkflowCommand",
                f"command_id={command.command_id}",
            )
        return command.state_version + 1

    @staticmethod
    def _require_chronological(command: WorkflowCommand, observed_at: str) -> None:
        if observed_at < command.updated_at:
            raise ValueError("observed_at")

    @staticmethod
    def _update_one(
        connection: sqlite3.Connection,
        statement: str,
        parameters: tuple[object, ...],
        identity: str,
    ) -> None:
        cursor = connection.execute(statement, parameters)
        if cursor.rowcount != 1:
            raise RepositoryError(
                RepositoryFailureCode.MALFORMED_PERSISTED_PAYLOAD,
                "WorkflowCommand",
                identity,
            )

    @staticmethod
    def _insert_event(
        connection: sqlite3.Connection,
        *,
        event_id: str,
        command_id: str,
        event_kind: WorkflowCommandEventKind,
        prior_state: WorkflowCommandState | None,
        next_state: WorkflowCommandState,
        prior_state_version: int | None,
        next_state_version: int,
        actor_id: str,
        occurred_at: str,
        lease_owner: str | None,
        lease_expires_at: str | None,
        claim_count: int,
        reason_code: str | None,
    ) -> None:
        connection.execute(
            """
            INSERT INTO workflow_command_events(
                event_id, command_id, event_kind, prior_state, next_state,
                prior_state_version, next_state_version, actor_id, occurred_at,
                lease_owner, lease_expires_at, claim_count, reason_code
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                event_id,
                command_id,
                event_kind.value,
                None if prior_state is None else prior_state.value,
                next_state.value,
                prior_state_version,
                next_state_version,
                actor_id,
                occurred_at,
                lease_owner,
                lease_expires_at,
                claim_count,
                reason_code,
            ),
        )

    def _applied(
        self,
        connection: sqlite3.Connection,
        mutation_kind: QueueMutationKind,
        command_id: str,
        event_id: str,
    ) -> QueueResult:
        command = self._load_command(connection, command_id)
        if command is None:
            raise RepositoryError(
                RepositoryFailureCode.MALFORMED_PERSISTED_PAYLOAD,
                "WorkflowCommand",
                self._identity(command_id),
            )
        event = self._event_by_id(connection, event_id, self._identity(command_id))
        return QueueResult(
            QueueResultCode.APPLIED,
            mutation_kind,
            command=command,
            event=event,
        )

    def enqueue(
        self,
        *,
        command_id: str,
        event_id: str,
        envelope: ValidatedCommandEnvelope,
        actor_id: str,
        occurred_at: str,
    ) -> QueueResult:
        command = _queue_uuid(command_id, "command_id")
        event = _queue_uuid(event_id, "event_id")
        if type(envelope) is not ValidatedCommandEnvelope:
            raise TypeError("envelope")
        actor = _queue_text(actor_id, "actor_id", _QUEUE_ACTOR_PATTERN)
        _parse_queue_timestamp(occurred_at, "occurred_at")
        identity = self._identity(command)

        def operation(connection: sqlite3.Connection) -> QueueResult:
            existing_row = connection.execute(
                f"SELECT {_COMMAND_COLUMNS} FROM workflow_commands "
                "WHERE project_id=? AND idempotency_key=?",
                (envelope.project_id, envelope.idempotency_key),
            ).fetchone()
            if existing_row is not None:
                existing = self._command_from_row(connection, existing_row, identity)
                identical = (
                    existing.project_id == envelope.project_id
                    and existing.development_run_id == envelope.development_run_id
                    and existing.phase_id == envelope.phase_id
                    and existing.command_kind == envelope.command_kind
                    and existing.command_schema_version == envelope.command_schema_version
                    and existing.payload_json == envelope.payload_json
                    and existing.intent_digest == envelope.intent_digest
                    and existing.priority == envelope.priority
                )
                return QueueResult(
                    QueueResultCode.EXISTING_IDENTICAL
                    if identical
                    else QueueResultCode.IDEMPOTENCY_CONFLICT,
                    QueueMutationKind.ENQUEUE,
                    command=existing,
                )
            connection.execute(
                """
                INSERT INTO workflow_commands(
                    command_id, project_id, development_run_id, phase_id,
                    command_kind, command_schema_version, payload_json,
                    intent_digest, idempotency_key, priority, state,
                    state_version, claim_count, created_at, updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,'PENDING',1,0,?,?)
                """,
                (
                    command,
                    envelope.project_id,
                    envelope.development_run_id,
                    envelope.phase_id,
                    envelope.command_kind,
                    envelope.command_schema_version,
                    envelope.payload_json,
                    envelope.intent_digest,
                    envelope.idempotency_key,
                    envelope.priority,
                    occurred_at,
                    occurred_at,
                ),
            )
            self._insert_event(
                connection,
                event_id=event,
                command_id=command,
                event_kind=WorkflowCommandEventKind.ENQUEUED,
                prior_state=None,
                next_state=WorkflowCommandState.PENDING,
                prior_state_version=None,
                next_state_version=1,
                actor_id=actor,
                occurred_at=occurred_at,
                lease_owner=None,
                lease_expires_at=None,
                claim_count=0,
                reason_code=None,
            )
            return self._applied(connection, QueueMutationKind.ENQUEUE, command, event)

        return self._mutation(QueueMutationKind.ENQUEUE, identity, operation)

    def get(self, command_id: str) -> WorkflowCommand | None:
        command = _queue_uuid(command_id, "command_id")
        identity = self._identity(command)
        connection = _open_read_only_connection(
            self._database_path, "WorkflowCommand", identity
        )
        try:
            connection.execute("BEGIN")
            return self._load_command(connection, command)
        except RepositoryError:
            raise
        except sqlite3.Error as error:
            _raise_repository_error(
                RepositoryFailureCode.SQLITE_OPERATIONAL_FAILURE,
                "WorkflowCommand",
                identity,
                error,
            )
        finally:
            connection.close()

    def list_project(self, project_id: str) -> tuple[WorkflowCommand, ...]:
        project = _queue_identity(project_id, "project_id")
        identity = _identity_text(("project_id", project),)
        connection = _open_read_only_connection(
            self._database_path, "WorkflowCommand", identity
        )
        try:
            connection.execute("BEGIN")
            rows = connection.execute(
                f"SELECT {_COMMAND_COLUMNS} FROM workflow_commands "
                "WHERE project_id=? ORDER BY queue_sequence ASC",
                (project,),
            ).fetchall()
            return tuple(
                self._command_from_row(connection, row, identity) for row in rows
            )
        except RepositoryError:
            raise
        except sqlite3.Error as error:
            _raise_repository_error(
                RepositoryFailureCode.SQLITE_OPERATIONAL_FAILURE,
                "WorkflowCommand",
                identity,
                error,
            )
        finally:
            connection.close()

    def history(self, command_id: str) -> tuple[WorkflowCommandEvent, ...] | None:
        command = _queue_uuid(command_id, "command_id")
        identity = self._identity(command)
        connection = _open_read_only_connection(
            self._database_path, "WorkflowCommandEvent", identity
        )
        try:
            connection.execute("BEGIN")
            current = self._load_command(connection, command)
            if current is None:
                return None
            return self._history_in_connection(connection, current, identity)
        except RepositoryError:
            raise
        except sqlite3.Error as error:
            _raise_repository_error(
                RepositoryFailureCode.SQLITE_OPERATIONAL_FAILURE,
                "WorkflowCommandEvent",
                identity,
                error,
            )
        finally:
            connection.close()

    def claim_next(
        self,
        *,
        event_id: str,
        lease_owner: str,
        lease_acquired_at: str,
        lease_expires_at: str,
    ) -> QueueResult:
        event = _queue_uuid(event_id, "event_id")
        owner = _queue_text(lease_owner, "lease_owner", _QUEUE_ACTOR_PATTERN)
        acquired = _parse_queue_timestamp(lease_acquired_at, "lease_acquired_at")
        expires = _parse_queue_timestamp(lease_expires_at, "lease_expires_at")
        if acquired >= expires:
            raise ValueError("lease_expires_at")

        def operation(connection: sqlite3.Connection) -> QueueResult:
            row = connection.execute(
                f"SELECT {_COMMAND_COLUMNS} FROM workflow_commands "
                "WHERE state='PENDING' ORDER BY priority DESC, queue_sequence ASC LIMIT 1"
            ).fetchone()
            if row is None:
                return QueueResult(
                    QueueResultCode.NO_ELIGIBLE_COMMAND, QueueMutationKind.CLAIM_NEXT
                )
            current = self._command_from_row(connection, row, "claim_next")
            self._require_chronological(current, lease_acquired_at)
            version = self._next_version(current)
            if current.claim_count >= 9_223_372_036_854_775_807:
                _raise_payload_error(
                    ValueError("claim_count"), "WorkflowCommand", self._identity(current.command_id)
                )
            count = current.claim_count + 1
            self._update_one(
                connection,
                """
                UPDATE workflow_commands
                SET state='CLAIMED', state_version=?, claim_count=?, lease_owner=?,
                    lease_acquired_at=?, lease_expires_at=?, updated_at=?
                WHERE command_id=? AND state='PENDING' AND state_version=?
                """,
                (
                    version,
                    count,
                    owner,
                    lease_acquired_at,
                    lease_expires_at,
                    lease_acquired_at,
                    current.command_id,
                    current.state_version,
                ),
                self._identity(current.command_id),
            )
            self._insert_event(
                connection,
                event_id=event,
                command_id=current.command_id,
                event_kind=WorkflowCommandEventKind.CLAIMED,
                prior_state=WorkflowCommandState.PENDING,
                next_state=WorkflowCommandState.CLAIMED,
                prior_state_version=current.state_version,
                next_state_version=version,
                actor_id=owner,
                occurred_at=lease_acquired_at,
                lease_owner=owner,
                lease_expires_at=lease_expires_at,
                claim_count=count,
                reason_code=None,
            )
            return self._applied(
                connection, QueueMutationKind.CLAIM_NEXT, current.command_id, event
            )

        return self._mutation(QueueMutationKind.CLAIM_NEXT, "claim_next", operation)

    @staticmethod
    def _terminal(command: WorkflowCommand) -> bool:
        return command.state in {
            WorkflowCommandState.SUCCEEDED,
            WorkflowCommandState.FAILED,
            WorkflowCommandState.CANCELLED,
        }

    def _load_for_mutation(
        self,
        connection: sqlite3.Connection,
        command_id: str,
        mutation_kind: QueueMutationKind,
        expected_state: WorkflowCommandState,
        expected_state_version: int,
    ) -> tuple[WorkflowCommand | None, QueueResult | None]:
        current = self._load_command(connection, command_id)
        if current is None:
            return None, QueueResult(QueueResultCode.NOT_FOUND, mutation_kind)
        if self._terminal(current):
            return current, QueueResult(
                QueueResultCode.TERMINAL_OBSERVED, mutation_kind, command=current
            )
        if current.state is not expected_state or current.state_version != expected_state_version:
            return current, QueueResult(
                QueueResultCode.CAS_CONFLICT, mutation_kind, command=current
            )
        return current, None

    @staticmethod
    def _owner_lease_observation(
        current: WorkflowCommand,
        mutation_kind: QueueMutationKind,
        lease_owner: str,
        observed_at: str,
    ) -> QueueResult | None:
        if current.lease_owner != lease_owner:
            return QueueResult(
                QueueResultCode.LEASE_OWNER_MISMATCH, mutation_kind, command=current
            )
        if current.lease_expires_at is None or observed_at >= current.lease_expires_at:
            return QueueResult(
                QueueResultCode.LEASE_EXPIRED, mutation_kind, command=current
            )
        return None

    def renew_lease(
        self,
        *,
        command_id: str,
        expected_state: WorkflowCommandState,
        expected_state_version: int,
        lease_owner: str,
        observed_at: str,
        lease_expires_at: str,
        event_id: str,
    ) -> QueueResult:
        return self._renew_or_transition(
            "renew",
            QueueMutationKind.RENEW_LEASE,
            command_id=command_id,
            expected_state=expected_state,
            expected_state_version=expected_state_version,
            lease_owner=lease_owner,
            observed_at=observed_at,
            event_id=event_id,
            lease_expires_at=lease_expires_at,
        )

    def mark_running(
        self,
        *,
        command_id: str,
        expected_state: WorkflowCommandState,
        expected_state_version: int,
        lease_owner: str,
        occurred_at: str,
        event_id: str,
    ) -> QueueResult:
        return self._renew_or_transition(
            "running",
            QueueMutationKind.MARK_RUNNING,
            command_id=command_id,
            expected_state=expected_state,
            expected_state_version=expected_state_version,
            lease_owner=lease_owner,
            observed_at=occurred_at,
            event_id=event_id,
        )

    def _renew_or_transition(
        self,
        action: str,
        mutation_kind: QueueMutationKind,
        *,
        command_id: str,
        expected_state: WorkflowCommandState,
        expected_state_version: int,
        lease_owner: str,
        observed_at: str,
        event_id: str,
        lease_expires_at: str | None = None,
    ) -> QueueResult:
        command = _queue_uuid(command_id, "command_id")
        event = _queue_uuid(event_id, "event_id")
        if type(expected_state) is not WorkflowCommandState:
            raise TypeError("expected_state")
        version_expected = _queue_integer(
            expected_state_version, "expected_state_version", 1, 9_223_372_036_854_775_807
        )
        owner = _queue_text(lease_owner, "lease_owner", _QUEUE_ACTOR_PATTERN)
        _parse_queue_timestamp(observed_at, "observed_at")
        if action == "renew":
            if expected_state not in {WorkflowCommandState.CLAIMED, WorkflowCommandState.RUNNING}:
                raise ValueError("expected_state")
            if lease_expires_at is None:
                raise ValueError("lease_expires_at")
            _parse_queue_timestamp(lease_expires_at, "lease_expires_at")
            if lease_expires_at <= observed_at:
                raise ValueError("lease_expires_at")
        elif expected_state is not WorkflowCommandState.CLAIMED:
            raise ValueError("expected_state")
        identity = self._identity(command)

        def operation(connection: sqlite3.Connection) -> QueueResult:
            current, result = self._load_for_mutation(
                connection, command, mutation_kind, expected_state, version_expected
            )
            if result is not None:
                return result
            assert current is not None
            self._require_chronological(current, observed_at)
            observation = self._owner_lease_observation(
                current, mutation_kind, owner, observed_at
            )
            if observation is not None:
                return observation
            version = self._next_version(current)
            if action == "renew":
                next_state = current.state
                event_kind = WorkflowCommandEventKind.LEASE_RENEWED
                statement = (
                    "UPDATE workflow_commands SET state_version=?, lease_expires_at=?, "
                    "updated_at=? WHERE command_id=? AND state=? AND state_version=? "
                    "AND lease_owner=? AND lease_expires_at>?"
                )
                parameters = (
                    version,
                    lease_expires_at,
                    observed_at,
                    command,
                    current.state.value,
                    current.state_version,
                    owner,
                    observed_at,
                )
                event_expiry = lease_expires_at
            else:
                next_state = WorkflowCommandState.RUNNING
                event_kind = WorkflowCommandEventKind.STARTED
                statement = (
                    "UPDATE workflow_commands SET state='RUNNING', state_version=?, "
                    "started_at=?, updated_at=? WHERE command_id=? AND state='CLAIMED' "
                    "AND state_version=? AND lease_owner=? AND lease_expires_at>?"
                )
                parameters = (
                    version,
                    observed_at,
                    observed_at,
                    command,
                    current.state_version,
                    owner,
                    observed_at,
                )
                event_expiry = current.lease_expires_at
            self._update_one(connection, statement, parameters, identity)
            self._insert_event(
                connection,
                event_id=event,
                command_id=command,
                event_kind=event_kind,
                prior_state=current.state,
                next_state=next_state,
                prior_state_version=current.state_version,
                next_state_version=version,
                actor_id=owner,
                occurred_at=observed_at,
                lease_owner=owner,
                lease_expires_at=event_expiry,
                claim_count=current.claim_count,
                reason_code=None,
            )
            return self._applied(connection, mutation_kind, command, event)

        return self._mutation(mutation_kind, identity, operation)

    def request_cancellation(
        self,
        *,
        command_id: str,
        expected_state: WorkflowCommandState,
        expected_state_version: int,
        requested_by: str,
        reason_code: str,
        occurred_at: str,
        event_id: str,
    ) -> QueueResult:
        command = _queue_uuid(command_id, "command_id")
        event = _queue_uuid(event_id, "event_id")
        if type(expected_state) is not WorkflowCommandState:
            raise TypeError("expected_state")
        expected_version = _queue_integer(
            expected_state_version, "expected_state_version", 1, 9_223_372_036_854_775_807
        )
        requester = _queue_text(requested_by, "requested_by", _QUEUE_ACTOR_PATTERN)
        reason = _queue_text(reason_code, "reason_code", _QUEUE_CODE_PATTERN)
        _parse_queue_timestamp(occurred_at, "occurred_at")
        identity = self._identity(command)

        def operation(connection: sqlite3.Connection) -> QueueResult:
            current, result = self._load_for_mutation(
                connection,
                command,
                QueueMutationKind.REQUEST_CANCELLATION,
                expected_state,
                expected_version,
            )
            if result is not None:
                return result
            assert current is not None
            self._require_chronological(current, occurred_at)
            if current.cancellation_requested_at is not None:
                return QueueResult(
                    QueueResultCode.CANCELLATION_ALREADY_REQUESTED,
                    QueueMutationKind.REQUEST_CANCELLATION,
                    command=current,
                )
            if current.state not in {
                WorkflowCommandState.PENDING,
                WorkflowCommandState.CLAIMED,
                WorkflowCommandState.RUNNING,
            }:
                raise RepositoryError(
                    RepositoryFailureCode.MALFORMED_PERSISTED_PAYLOAD,
                    "WorkflowCommand",
                    identity,
                )
            version = self._next_version(current)
            next_state = (
                WorkflowCommandState.CANCELLED
                if current.state is WorkflowCommandState.PENDING
                else current.state
            )
            completed = occurred_at if next_state is WorkflowCommandState.CANCELLED else None
            self._update_one(
                connection,
                """
                UPDATE workflow_commands
                SET state=?, state_version=?, cancellation_requested_at=?,
                    cancellation_requested_by=?, cancellation_reason_code=?,
                    completed_at=?, updated_at=?
                WHERE command_id=? AND state=? AND state_version=?
                """,
                (
                    next_state.value,
                    version,
                    occurred_at,
                    requester,
                    reason,
                    completed,
                    occurred_at,
                    command,
                    current.state.value,
                    current.state_version,
                ),
                identity,
            )
            event_kind = (
                WorkflowCommandEventKind.CANCELLED
                if next_state is WorkflowCommandState.CANCELLED
                else WorkflowCommandEventKind.CANCELLATION_REQUESTED
            )
            self._insert_event(
                connection,
                event_id=event,
                command_id=command,
                event_kind=event_kind,
                prior_state=current.state,
                next_state=next_state,
                prior_state_version=current.state_version,
                next_state_version=version,
                actor_id=requester,
                occurred_at=occurred_at,
                lease_owner=current.lease_owner,
                lease_expires_at=current.lease_expires_at,
                claim_count=current.claim_count,
                reason_code=reason,
            )
            return self._applied(
                connection, QueueMutationKind.REQUEST_CANCELLATION, command, event
            )

        return self._mutation(
            QueueMutationKind.REQUEST_CANCELLATION, identity, operation
        )

    def acknowledge_cancellation(
        self,
        *,
        command_id: str,
        expected_state: WorkflowCommandState,
        expected_state_version: int,
        lease_owner: str,
        observed_at: str,
        event_id: str,
    ) -> QueueResult:
        return self._finish_owner_mutation(
            "cancel",
            QueueMutationKind.ACKNOWLEDGE_CANCELLATION,
            command_id=command_id,
            expected_state=expected_state,
            expected_state_version=expected_state_version,
            lease_owner=lease_owner,
            observed_at=observed_at,
            event_id=event_id,
        )

    def mark_succeeded(
        self,
        *,
        command_id: str,
        expected_state: WorkflowCommandState,
        expected_state_version: int,
        lease_owner: str,
        observed_at: str,
        event_id: str,
    ) -> QueueResult:
        return self._finish_owner_mutation(
            "succeed",
            QueueMutationKind.MARK_SUCCEEDED,
            command_id=command_id,
            expected_state=expected_state,
            expected_state_version=expected_state_version,
            lease_owner=lease_owner,
            observed_at=observed_at,
            event_id=event_id,
        )

    def mark_failed(
        self,
        *,
        command_id: str,
        expected_state: WorkflowCommandState,
        expected_state_version: int,
        lease_owner: str,
        failure_code: str,
        observed_at: str,
        event_id: str,
    ) -> QueueResult:
        failure = _queue_text(failure_code, "failure_code", _QUEUE_CODE_PATTERN)
        return self._finish_owner_mutation(
            "fail",
            QueueMutationKind.MARK_FAILED,
            command_id=command_id,
            expected_state=expected_state,
            expected_state_version=expected_state_version,
            lease_owner=lease_owner,
            observed_at=observed_at,
            event_id=event_id,
            failure_code=failure,
        )

    def _finish_owner_mutation(
        self,
        action: str,
        mutation_kind: QueueMutationKind,
        *,
        command_id: str,
        expected_state: WorkflowCommandState,
        expected_state_version: int,
        lease_owner: str,
        observed_at: str,
        event_id: str,
        failure_code: str | None = None,
    ) -> QueueResult:
        command = _queue_uuid(command_id, "command_id")
        event = _queue_uuid(event_id, "event_id")
        if type(expected_state) is not WorkflowCommandState:
            raise TypeError("expected_state")
        allowed = (
            {WorkflowCommandState.CLAIMED, WorkflowCommandState.RUNNING}
            if action == "cancel"
            else {WorkflowCommandState.RUNNING}
        )
        if expected_state not in allowed:
            raise ValueError("expected_state")
        expected_version = _queue_integer(
            expected_state_version, "expected_state_version", 1, 9_223_372_036_854_775_807
        )
        owner = _queue_text(lease_owner, "lease_owner", _QUEUE_ACTOR_PATTERN)
        _parse_queue_timestamp(observed_at, "observed_at")
        identity = self._identity(command)

        def operation(connection: sqlite3.Connection) -> QueueResult:
            current, result = self._load_for_mutation(
                connection, command, mutation_kind, expected_state, expected_version
            )
            if result is not None:
                return result
            assert current is not None
            self._require_chronological(current, observed_at)
            if action == "cancel" and current.cancellation_requested_at is None:
                return QueueResult(
                    QueueResultCode.CANCELLATION_NOT_REQUESTED,
                    mutation_kind,
                    command=current,
                )
            observation = self._owner_lease_observation(
                current, mutation_kind, owner, observed_at
            )
            if observation is not None:
                return observation
            version = self._next_version(current)
            next_state = {
                "cancel": WorkflowCommandState.CANCELLED,
                "succeed": WorkflowCommandState.SUCCEEDED,
                "fail": WorkflowCommandState.FAILED,
            }[action]
            event_kind = {
                "cancel": WorkflowCommandEventKind.CANCELLED,
                "succeed": WorkflowCommandEventKind.SUCCEEDED,
                "fail": WorkflowCommandEventKind.FAILED,
            }[action]
            reason = current.cancellation_reason_code if action == "cancel" else failure_code
            self._update_one(
                connection,
                """
                UPDATE workflow_commands
                SET state=?, state_version=?, lease_owner=NULL,
                    lease_acquired_at=NULL, lease_expires_at=NULL,
                    failure_code=?, completed_at=?, updated_at=?
                WHERE command_id=? AND state=? AND state_version=?
                    AND lease_owner=? AND lease_expires_at>?
                """,
                (
                    next_state.value,
                    version,
                    failure_code,
                    observed_at,
                    observed_at,
                    command,
                    current.state.value,
                    current.state_version,
                    owner,
                    observed_at,
                ),
                identity,
            )
            self._insert_event(
                connection,
                event_id=event,
                command_id=command,
                event_kind=event_kind,
                prior_state=current.state,
                next_state=next_state,
                prior_state_version=current.state_version,
                next_state_version=version,
                actor_id=owner,
                occurred_at=observed_at,
                lease_owner=current.lease_owner,
                lease_expires_at=current.lease_expires_at,
                claim_count=current.claim_count,
                reason_code=reason,
            )
            return self._applied(connection, mutation_kind, command, event)

        return self._mutation(mutation_kind, identity, operation)

    def recover_expired_claim(
        self,
        *,
        command_id: str,
        expected_state: WorkflowCommandState,
        expected_state_version: int,
        recovery_actor: str,
        observed_at: str,
        event_id: str,
    ) -> QueueResult:
        command = _queue_uuid(command_id, "command_id")
        event = _queue_uuid(event_id, "event_id")
        if type(expected_state) is not WorkflowCommandState:
            raise TypeError("expected_state")
        if expected_state not in {WorkflowCommandState.CLAIMED, WorkflowCommandState.RUNNING}:
            raise ValueError("expected_state")
        expected_version = _queue_integer(
            expected_state_version, "expected_state_version", 1, 9_223_372_036_854_775_807
        )
        actor = _queue_text(recovery_actor, "recovery_actor", _QUEUE_ACTOR_PATTERN)
        _parse_queue_timestamp(observed_at, "observed_at")
        identity = self._identity(command)

        def operation(connection: sqlite3.Connection) -> QueueResult:
            current, result = self._load_for_mutation(
                connection,
                command,
                QueueMutationKind.RECOVER_EXPIRED_CLAIM,
                expected_state,
                expected_version,
            )
            if result is not None:
                return result
            assert current is not None
            self._require_chronological(current, observed_at)
            if current.lease_expires_at is None or observed_at < current.lease_expires_at:
                return QueueResult(
                    QueueResultCode.LEASE_NOT_EXPIRED,
                    QueueMutationKind.RECOVER_EXPIRED_CLAIM,
                    command=current,
                )
            if current.state is WorkflowCommandState.RUNNING:
                return QueueResult(
                    QueueResultCode.RECONCILIATION_REQUIRED,
                    QueueMutationKind.RECOVER_EXPIRED_CLAIM,
                    command=current,
                )
            version = self._next_version(current)
            cancelled = current.cancellation_requested_at is not None
            next_state = (
                WorkflowCommandState.CANCELLED
                if cancelled
                else WorkflowCommandState.PENDING
            )
            event_kind = (
                WorkflowCommandEventKind.CANCELLED
                if cancelled
                else WorkflowCommandEventKind.EXPIRED_CLAIM_RELEASED
            )
            reason = current.cancellation_reason_code if cancelled else "LEASE_EXPIRED"
            self._update_one(
                connection,
                """
                UPDATE workflow_commands
                SET state=?, state_version=?, lease_owner=NULL,
                    lease_acquired_at=NULL, lease_expires_at=NULL,
                    completed_at=?, updated_at=?
                WHERE command_id=? AND state='CLAIMED' AND state_version=?
                    AND lease_expires_at<=?
                """,
                (
                    next_state.value,
                    version,
                    observed_at if cancelled else None,
                    observed_at,
                    command,
                    current.state_version,
                    observed_at,
                ),
                identity,
            )
            self._insert_event(
                connection,
                event_id=event,
                command_id=command,
                event_kind=event_kind,
                prior_state=WorkflowCommandState.CLAIMED,
                next_state=next_state,
                prior_state_version=current.state_version,
                next_state_version=version,
                actor_id=actor,
                occurred_at=observed_at,
                lease_owner=current.lease_owner,
                lease_expires_at=current.lease_expires_at,
                claim_count=current.claim_count,
                reason_code=reason,
            )
            return self._applied(
                connection,
                QueueMutationKind.RECOVER_EXPIRED_CLAIM,
                command,
                event,
            )

        return self._mutation(
            QueueMutationKind.RECOVER_EXPIRED_CLAIM, identity, operation
        )
