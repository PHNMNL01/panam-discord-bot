"""SQLite repository implementations for the DL-P1.5 foundation entities."""

import json
import os
import sqlite3
import sys
from pathlib import Path
from typing import NoReturn

from .models import (
    AcceptedPhaseStateEvent,
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
    PhaseState,
    PhaseStateRecord,
    ProjectPolicy,
    QueueMutationKind,
    QueueResult,
    QueueResultCode,
    ValidatedCommandEnvelope,
    WorkerFailureCode,
    WorkerJournalResult,
    WorkerJournalResultCode,
    WorkerOperationKind,
    WorkerOperationReceipt,
    WorkerOperationState,
    WorkerReconciliationStatus,
    WorkerSession,
    WorkerSessionState,
    WorkflowCommand,
    WorkflowCommandEvent,
    WorkflowCommandEventKind,
    WorkflowCommandState,
    _QUEUE_ACTOR_PATTERN,
    _QUEUE_CODE_PATTERN,
    _SIGNED_64_MAX,
    _WORKER_RECEIPT_TRANSITIONS,
    _WORKER_DIAGNOSTICS,
    _WORKER_RESULT_CODE_PATTERN,
    _WORKER_TERMINAL_OPERATION_STATES,
    _canonical_eligible_definition_keys,
    _parse_queue_timestamp,
    _queue_identity,
    _queue_integer,
    _queue_text,
    _queue_uuid,
    _worker_id,
    _worker_queue_owner,
    _worker_receipt_code_diagnostic_valid,
    _worker_uuid,
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
        "worker_sessions",
        "worker_operations",
        "phase_states",
        "phase_state_events",
    }
)


def _identity_text(*parts: tuple[str, str]) -> str:
    return ",".join(f"{name}={value}" for name, value in parts)


class _CasRollback(Exception):
    """Private control flow for a rowcount-zero persistence CAS."""

    def __init__(self, result: WorkerJournalResult) -> None:
        super().__init__(WorkerJournalResultCode.CAS_CONFLICT.value)
        self.result = result


_WINDOWS_DEVICE_NAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
)


def _canonical_database_path(database_path: Path) -> tuple[Path, str]:
    """Return a lexical, non-resolving database path and comparison identity."""

    if type(database_path) is not type(Path()):
        raise TypeError("database_path")
    raw = os.fspath(database_path)
    if not raw or "\x00" in raw:
        raise ValueError("database_path")
    windows_text = raw.replace("/", "\\")
    if windows_text.startswith(("\\\\?\\", "\\\\.\\")):
        raise ValueError("database_path")
    drive, tail = os.path.splitdrive(raw)
    if drive and not tail.startswith(("\\", "/")):
        raise ValueError("database_path")
    final_component = raw.rstrip("\\/").rsplit("\\", 1)[-1].rsplit("/", 1)[-1]
    device_stem = final_component.split(".", 1)[0].rstrip(" .").upper()
    if device_stem in _WINDOWS_DEVICE_NAMES:
        raise ValueError("database_path")
    normalized = os.path.normcase(os.path.normpath(os.path.abspath(raw)))
    return Path(normalized), normalized


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
            RepositoryFailureCode.SQLITE_OPERATIONAL_FAILURE,
            entity_name,
            identity,
            error,
        )
    if versions != [1, 2, 3, 4, 5, 6]:
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
            RepositoryFailureCode.SQLITE_OPERATIONAL_FAILURE,
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
    _validate_worker_schema(connection, entity_name, identity)
    _validate_phase_schema(connection, entity_name, identity)


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
            RepositoryFailureCode.SQLITE_OPERATIONAL_FAILURE,
            entity_name,
            identity,
            error,
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
            RepositoryFailureCode.SQLITE_OPERATIONAL_FAILURE,
            entity_name,
            identity,
            error,
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
            RepositoryFailureCode.SQLITE_OPERATIONAL_FAILURE,
            entity_name,
            identity,
            error,
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


def _validate_worker_schema(
    connection: sqlite3.Connection,
    entity_name: str,
    identity: str,
) -> None:
    expected_sessions = [
        ("session_sequence", "INTEGER", 0, None, 1),
        ("session_id", "TEXT", 1, None, 0),
        ("worker_id", "TEXT", 1, None, 0),
        ("queue_owner_id", "TEXT", 1, None, 0),
        ("state", "TEXT", 1, None, 0),
        ("state_version", "INTEGER", 1, "1", 0),
        ("started_at", "TEXT", 1, None, 0),
        ("last_heartbeat_at", "TEXT", 1, None, 0),
        ("stopped_at", "TEXT", 0, None, 0),
        ("stop_reason_code", "TEXT", 0, None, 0),
    ]
    expected_operations = [
        ("operation_sequence", "INTEGER", 0, None, 1),
        ("operation_id", "TEXT", 1, None, 0),
        ("operation_kind", "TEXT", 1, None, 0),
        ("command_id", "TEXT", 1, None, 0),
        ("project_id", "TEXT", 1, None, 0),
        ("development_run_id", "TEXT", 0, None, 0),
        ("phase_id", "TEXT", 0, None, 0),
        ("session_id", "TEXT", 1, None, 0),
        ("worker_id", "TEXT", 1, None, 0),
        ("queue_owner_id", "TEXT", 1, None, 0),
        ("claim_count", "INTEGER", 1, None, 0),
        ("precondition_state_version", "INTEGER", 1, None, 0),
        ("state", "TEXT", 1, None, 0),
        ("state_version", "INTEGER", 1, "1", 0),
        ("external_effect_class", "TEXT", 1, "'NONE'", 0),
        ("reconciliation_status", "TEXT", 1, "'NOT_REQUIRED'", 0),
        ("durable_failure_code", "TEXT", 0, None, 0),
        ("diagnostic_detail", "TEXT", 0, None, 0),
        ("created_at", "TEXT", 1, None, 0),
        ("updated_at", "TEXT", 1, None, 0),
        ("started_at", "TEXT", 0, None, 0),
        ("completed_at", "TEXT", 0, None, 0),
    ]
    try:
        session_columns = connection.execute(
            "PRAGMA table_xinfo(worker_sessions)"
        ).fetchall()
        operation_columns = connection.execute(
            "PRAGMA table_xinfo(worker_operations)"
        ).fetchall()
        session_indexes = connection.execute(
            "PRAGMA index_list(worker_sessions)"
        ).fetchall()
        operation_indexes = connection.execute(
            "PRAGMA index_list(worker_operations)"
        ).fetchall()
        operation_fks = connection.execute(
            "PRAGMA foreign_key_list(worker_operations)"
        ).fetchall()
        triggers = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='trigger' "
            "AND tbl_name IN ('worker_sessions','worker_operations')"
        ).fetchall()
        schema_sql = {
            row["name"]: row["sql"]
            for row in connection.execute(
                "SELECT name, sql FROM sqlite_master WHERE "
                "name IN ('worker_sessions','worker_sessions_session_id_uq',"
                "'worker_sessions_one_nonterminal_worker_idx',"
                "'worker_sessions_state_heartbeat_idx','worker_operations',"
                "'worker_operations_operation_id_uq',"
                "'worker_operations_command_claim_uq',"
                "'worker_operations_worker_state_sequence_idx')"
            )
        }
    except sqlite3.Error as error:
        _raise_repository_error(
            RepositoryFailureCode.SQLITE_OPERATIONAL_FAILURE,
            entity_name,
            identity,
            error,
        )

    def shape(rows: list[sqlite3.Row]) -> list[tuple[object, ...]]:
        return [
            (
                row["name"],
                row["type"].upper(),
                row["notnull"],
                row["dflt_value"],
                row["pk"],
            )
            for row in rows
            if row["hidden"] == 0
        ]

    if shape(session_columns) != expected_sessions or shape(
        operation_columns
    ) != expected_operations:
        raise RepositoryError(
            RepositoryFailureCode.SCHEMA_MISMATCH, entity_name, identity
        )

    expected_indexes = {
        "worker_sessions_session_id_uq": (1, 0, (("session_id", 0),)),
        "worker_sessions_one_nonterminal_worker_idx": (
            1,
            1,
            (("worker_id", 0),),
        ),
        "worker_sessions_state_heartbeat_idx": (
            0,
            0,
            (("state", 0), ("last_heartbeat_at", 0), ("session_sequence", 0)),
        ),
        "worker_operations_operation_id_uq": (1, 0, (("operation_id", 0),)),
        "worker_operations_command_claim_uq": (
            1,
            0,
            (("command_id", 0), ("claim_count", 0)),
        ),
        "worker_operations_worker_state_sequence_idx": (
            0,
            0,
            (("worker_id", 0), ("state", 0), ("operation_sequence", 0)),
        ),
    }
    explicit = {
        row["name"]: row
        for row in (*session_indexes, *operation_indexes)
        if row["origin"] == "c"
    }
    if set(explicit) != set(expected_indexes):
        raise RepositoryError(
            RepositoryFailureCode.SCHEMA_MISMATCH, entity_name, identity
        )
    try:
        for name, (unique, partial, expected_columns) in expected_indexes.items():
            index = explicit[name]
            if index["unique"] != unique or index["partial"] != partial:
                raise RepositoryError(
                    RepositoryFailureCode.SCHEMA_MISMATCH, entity_name, identity
                )
            rows = connection.execute(
                "SELECT name, desc, key FROM pragma_index_xinfo(?) ORDER BY seqno",
                (name,),
            ).fetchall()
            actual = tuple(
                (row["name"], row["desc"]) for row in rows if row["key"] == 1
            )
            if actual != expected_columns:
                raise RepositoryError(
                    RepositoryFailureCode.SCHEMA_MISMATCH, entity_name, identity
                )
    except sqlite3.Error as error:
        _raise_repository_error(
            RepositoryFailureCode.SQLITE_OPERATIONAL_FAILURE,
            entity_name,
            identity,
            error,
        )

    fk_shape = {
        (row["table"], row["from"], row["to"], row["on_update"], row["on_delete"])
        for row in operation_fks
    }
    if fk_shape != {
        ("workflow_commands", "command_id", "command_id", "RESTRICT", "RESTRICT"),
        ("worker_sessions", "session_id", "session_id", "RESTRICT", "RESTRICT"),
    } or triggers:
        raise RepositoryError(
            RepositoryFailureCode.SCHEMA_MISMATCH, entity_name, identity
        )

    expected_statements: dict[str, str] = {}
    for statement in PRODUCTION_MIGRATIONS[4].statements:
        tokens = statement.split()
        name_index = 3 if tokens[1] == "UNIQUE" else 2
        expected_statements[tokens[name_index]] = statement
    normalize = lambda value: " ".join(value.strip().rstrip(";").split()).lower()
    if set(schema_sql) != set(expected_statements) or any(
        not isinstance(schema_sql[name], str)
        or normalize(schema_sql[name]) != normalize(expected_statements[name])
        for name in expected_statements
    ):
        raise RepositoryError(
            RepositoryFailureCode.SCHEMA_MISMATCH, entity_name, identity
        )


def _validate_phase_schema(
    connection: sqlite3.Connection,
    entity_name: str,
    identity: str,
) -> None:
    expected_states = [
        ("project_id", "TEXT", 1, None, 1),
        ("phase_id", "TEXT", 1, None, 2),
        ("phase_contract_digest", "TEXT", 1, None, 0),
        ("current_state", "TEXT", 1, None, 0),
        ("state_version", "INTEGER", 1, None, 0),
        ("created_at", "TEXT", 1, None, 0),
        ("updated_at", "TEXT", 1, None, 0),
    ]
    expected_events = [
        ("event_sequence", "INTEGER", 0, None, 1),
        ("event_id", "TEXT", 1, None, 0),
        ("project_id", "TEXT", 1, None, 0),
        ("phase_id", "TEXT", 1, None, 0),
        ("phase_contract_digest", "TEXT", 1, None, 0),
        ("from_state", "TEXT", 1, None, 0),
        ("to_state", "TEXT", 1, None, 0),
        ("transition_reason", "TEXT", 1, None, 0),
        ("occurred_at", "TEXT", 1, None, 0),
        ("state_version", "INTEGER", 1, None, 0),
    ]
    try:
        state_columns = connection.execute("PRAGMA table_xinfo(phase_states)").fetchall()
        event_columns = connection.execute(
            "PRAGMA table_xinfo(phase_state_events)"
        ).fetchall()
        state_indexes = connection.execute("PRAGMA index_list(phase_states)").fetchall()
        event_indexes = connection.execute(
            "PRAGMA index_list(phase_state_events)"
        ).fetchall()
        state_fks = connection.execute("PRAGMA foreign_key_list(phase_states)").fetchall()
        event_fks = connection.execute(
            "PRAGMA foreign_key_list(phase_state_events)"
        ).fetchall()
        triggers = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='trigger' "
            "AND tbl_name IN ('phase_states','phase_state_events')"
        ).fetchall()
        schema_sql = {
            row["name"]: row["sql"]
            for row in connection.execute(
                "SELECT name, sql FROM sqlite_master WHERE "
                "name IN ('phase_states','phase_state_events',"
                "'phases_identity_contract_digest_uq',"
                "'phase_states_current_state_idx',"
                "'phase_state_events_phase_sequence_idx')"
            )
        }
    except sqlite3.Error as error:
        _raise_repository_error(
            RepositoryFailureCode.SQLITE_OPERATIONAL_FAILURE,
            entity_name,
            identity,
            error,
        )

    shape = lambda rows: [
        (row["name"], row["type"].upper(), row["notnull"], row["dflt_value"], row["pk"])
        for row in rows
        if row["hidden"] == 0
    ]
    if shape(state_columns) != expected_states or shape(event_columns) != expected_events:
        raise RepositoryError(RepositoryFailureCode.SCHEMA_MISMATCH, entity_name, identity)

    explicit_state = {row["name"]: row for row in state_indexes if row["origin"] == "c"}
    explicit_event = {row["name"]: row for row in event_indexes if row["origin"] == "c"}
    if set(explicit_state) != {"phase_states_current_state_idx"} or set(
        explicit_event
    ) != {"phase_state_events_phase_sequence_idx"}:
        raise RepositoryError(RepositoryFailureCode.SCHEMA_MISMATCH, entity_name, identity)
    if any(
        row["unique"] != 0 or row["partial"] != 0
        for row in (*explicit_state.values(), *explicit_event.values())
    ):
        raise RepositoryError(RepositoryFailureCode.SCHEMA_MISMATCH, entity_name, identity)

    def index_shape(name: str) -> tuple[tuple[str, int], ...]:
        rows = connection.execute(
            "SELECT name, desc, key FROM pragma_index_xinfo(?) ORDER BY seqno",
            (name,),
        ).fetchall()
        return tuple((row["name"], row["desc"]) for row in rows if row["key"] == 1)

    try:
        if index_shape("phase_states_current_state_idx") != (
            ("current_state", 0),
            ("updated_at", 0),
            ("project_id", 0),
            ("phase_id", 0),
        ) or index_shape("phase_state_events_phase_sequence_idx") != (
            ("project_id", 0),
            ("phase_id", 0),
            ("event_sequence", 0),
        ):
            raise RepositoryError(
                RepositoryFailureCode.SCHEMA_MISMATCH,
                entity_name,
                identity,
            )
    except sqlite3.Error as error:
        _raise_repository_error(
            RepositoryFailureCode.SQLITE_OPERATIONAL_FAILURE,
            entity_name,
            identity,
            error,
        )

    state_fk_shape = sorted(
        (row["table"], row["from"], row["to"], row["on_update"], row["on_delete"])
        for row in state_fks
    )
    event_fk_shape = sorted(
        (row["table"], row["from"], row["to"], row["on_update"], row["on_delete"])
        for row in event_fks
    )
    if state_fk_shape != sorted(
        [
            ("phases", "project_id", "project_id", "RESTRICT", "RESTRICT"),
            ("phases", "phase_id", "phase_id", "RESTRICT", "RESTRICT"),
            ("phases", "phase_contract_digest", "contract_digest", "RESTRICT", "RESTRICT"),
        ]
    ) or event_fk_shape != sorted(
        [
            ("phase_states", "project_id", "project_id", "RESTRICT", "RESTRICT"),
            ("phase_states", "phase_id", "phase_id", "RESTRICT", "RESTRICT"),
            ("phase_states", "phase_contract_digest", "phase_contract_digest", "RESTRICT", "RESTRICT"),
        ]
    ):
        raise RepositoryError(RepositoryFailureCode.SCHEMA_MISMATCH, entity_name, identity)
    if triggers:
        raise RepositoryError(RepositoryFailureCode.SCHEMA_MISMATCH, entity_name, identity)

    expected_statements: dict[str, str] = {}
    for statement in PRODUCTION_MIGRATIONS[5].statements:
        tokens = statement.split()
        name_index = 3 if tokens[1] == "UNIQUE" else 2
        expected_statements[tokens[name_index]] = statement
    normalize = lambda value: " ".join(value.strip().rstrip(";").split()).lower()
    if set(schema_sql) != set(expected_statements) or any(
        not isinstance(schema_sql[name], str)
        or normalize(schema_sql[name]) != normalize(expected_statements[name])
        for name in expected_statements
    ):
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
        try:
            connection.close()
        except sqlite3.Error:
            pass
        raise
    except sqlite3.Error as error:
        try:
            connection.close()
        except sqlite3.Error:
            pass
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
        try:
            connection.close()
        except sqlite3.Error:
            pass
        raise
    except sqlite3.Error as error:
        try:
            connection.close()
        except sqlite3.Error:
            pass
        _raise_repository_error(
            RepositoryFailureCode.SQLITE_OPERATIONAL_FAILURE,
            entity_name,
            identity,
            error,
        )
    return connection


def _rollback_if_active(connection: sqlite3.Connection) -> None:
    try:
        if connection.in_transaction:
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


class SqlitePhaseStateInspectionRepository:
    """Read-only SQLite adapter for Phase state and accepted events."""

    def __init__(self, database_path: Path) -> None:
        self._database_path = Path(database_path)

    def get_state(self, project_id: str, phase_id: str) -> PhaseStateRecord | None:
        project_id = _validate_query_identity(project_id, "PhaseState", "project_id")
        phase_id = _validate_query_identity(phase_id, "PhaseState", "phase_id")
        identity = _identity_text(("project_id", project_id), ("phase_id", phase_id))
        row = _fetch_one(
            self._database_path,
            "PhaseState",
            identity,
            """
            SELECT ps.project_id, ps.phase_id, ps.phase_contract_digest,
                   ps.current_state, ps.state_version, ps.created_at, ps.updated_at,
                   p.contract_version,
                   p.contract_digest AS authoritative_contract_digest
            FROM phase_states AS ps
            LEFT JOIN phases AS p
              ON p.project_id = ps.project_id AND p.phase_id = ps.phase_id
            WHERE ps.project_id = ? AND ps.phase_id = ?
            """,
            (project_id, phase_id),
        )
        if row is None:
            return None
        try:
            state = _decode_phase_state(row)
            contract = PhaseContract(
                project_id=row["project_id"],
                phase_id=row["phase_id"],
                contract_version=row["contract_version"],
            )
            if (
                contract.project_id != state.project_id
                or contract.phase_id != state.phase_id
                or contract.sha256_digest() != row["authoritative_contract_digest"]
                or state.phase_contract_digest != row["authoritative_contract_digest"]
            ):
                raise ValueError("Phase state contract binding")
            return state
        except (ValueError, TypeError, IndexError, KeyError) as error:
            _raise_payload_error(error, "PhaseState", identity)

    def get_history(
        self,
        project_id: str,
        phase_id: str,
    ) -> list[AcceptedPhaseStateEvent]:
        project_id = _validate_query_identity(
            project_id,
            "AcceptedPhaseStateEvent",
            "project_id",
        )
        phase_id = _validate_query_identity(
            phase_id,
            "AcceptedPhaseStateEvent",
            "phase_id",
        )
        identity = _identity_text(("project_id", project_id), ("phase_id", phase_id))
        rows = _fetch_all(
            self._database_path,
            "AcceptedPhaseStateEvent",
            identity,
            """
            SELECT event_id, project_id, phase_id, phase_contract_digest,
                   from_state, to_state, transition_reason, occurred_at,
                   state_version
            FROM phase_state_events
            WHERE project_id = ? AND phase_id = ?
            ORDER BY state_version ASC, event_sequence ASC
            """,
            (project_id, phase_id),
        )
        try:
            events = [
                _decode_phase_state_event(row, project_id, phase_id)
                for row in rows
            ]
            if events and [event.state_version for event in events] != list(
                range(1, len(events) + 1)
            ):
                raise ValueError("state_version")
            return events
        except (ValueError, TypeError, IndexError, KeyError) as error:
            _raise_payload_error(error, "AcceptedPhaseStateEvent", identity)


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


def _decode_phase_state(row: sqlite3.Row) -> PhaseStateRecord:
    return PhaseStateRecord(
        project_id=row["project_id"],
        phase_id=row["phase_id"],
        phase_contract_digest=row["phase_contract_digest"],
        current_state=PhaseState(row["current_state"]),
        state_version=row["state_version"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _decode_phase_state_event(
    row: sqlite3.Row,
    expected_project_id: str,
    expected_phase_id: str,
) -> AcceptedPhaseStateEvent:
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
    if event.project_id != expected_project_id or event.phase_id != expected_phase_id:
        raise ValueError("Phase event identity")
    return event


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
        canonical, identity = _canonical_database_path(database_path)
        self._database_path = canonical
        self._database_identity = identity

    @property
    def database_path(self) -> Path:
        return self._database_path

    @property
    def database_identity(self) -> str:
        return self._database_identity

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

    def claim_next_eligible(
        self,
        *,
        event_id: str,
        eligible_definition_keys: tuple[tuple[str, int], ...],
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
        keys = _canonical_eligible_definition_keys(eligible_definition_keys)
        predicates = " OR ".join(
            "(candidate.command_kind=? AND candidate.command_schema_version=?)"
            for _ in keys
        )
        parameters = tuple(value for key in keys for value in key)

        def operation(connection: sqlite3.Connection) -> QueueResult:
            worker_session_table = connection.execute(
                "SELECT 1 FROM sqlite_master "
                "WHERE type='table' AND name='worker_sessions'"
            ).fetchone()
            if worker_session_table is not None:
                session_rows = connection.execute(
                    f"SELECT {_SESSION_COLUMNS} FROM worker_sessions "
                    "WHERE queue_owner_id=? ORDER BY session_sequence",
                    (owner,),
                ).fetchall()
                if len(session_rows) > 1:
                    _raise_payload_error(
                        ValueError("ambiguous queue owner session"),
                        "WorkerSession",
                        f"queue_owner_id={owner}",
                    )
                if session_rows:
                    try:
                        bound_session = _decode_worker_session(session_rows[0])
                    except (TypeError, ValueError, KeyError, IndexError) as error:
                        _raise_payload_error(
                            error,
                            "WorkerSession",
                            f"queue_owner_id={owner}",
                        )
                    if bound_session.state is not WorkerSessionState.ACTIVE:
                        return QueueResult(
                            QueueResultCode.NO_ELIGIBLE_COMMAND,
                            QueueMutationKind.CLAIM_NEXT_ELIGIBLE,
                        )
            row = connection.execute(
                f"SELECT {_COMMAND_COLUMNS} FROM workflow_commands AS candidate "
                "WHERE candidate.state='PENDING' AND ("
                f"{predicates}) ORDER BY candidate.priority DESC, "
                "candidate.queue_sequence ASC LIMIT 1",
                parameters,
            ).fetchone()
            if row is None:
                return QueueResult(
                    QueueResultCode.NO_ELIGIBLE_COMMAND,
                    QueueMutationKind.CLAIM_NEXT_ELIGIBLE,
                )
            current = self._command_from_row(
                connection, row, "claim_next_eligible"
            )
            self._require_chronological(current, lease_acquired_at)
            version = self._next_version(current)
            if current.claim_count >= 9_223_372_036_854_775_807:
                _raise_payload_error(
                    ValueError("claim_count"),
                    "WorkflowCommand",
                    self._identity(current.command_id),
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
                connection,
                QueueMutationKind.CLAIM_NEXT_ELIGIBLE,
                current.command_id,
                event,
            )

        return self._mutation(
            QueueMutationKind.CLAIM_NEXT_ELIGIBLE,
            "claim_next_eligible",
            operation,
        )

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


_SESSION_COLUMNS = """
session_sequence, session_id, worker_id, queue_owner_id, state, state_version,
started_at, last_heartbeat_at, stopped_at, stop_reason_code
"""

_OPERATION_COLUMNS = """
operation_sequence, operation_id, operation_kind, command_id, project_id,
development_run_id, phase_id, session_id, worker_id, queue_owner_id,
claim_count, precondition_state_version, state, state_version,
external_effect_class, reconciliation_status, durable_failure_code,
diagnostic_detail, created_at, updated_at, started_at, completed_at
"""


def _decode_worker_session(row: sqlite3.Row) -> WorkerSession:
    return WorkerSession(
        session_sequence=row["session_sequence"],
        session_id=row["session_id"],
        worker_id=row["worker_id"],
        queue_owner_id=row["queue_owner_id"],
        state=WorkerSessionState(row["state"]),
        state_version=row["state_version"],
        started_at=row["started_at"],
        last_heartbeat_at=row["last_heartbeat_at"],
        stopped_at=row["stopped_at"],
        stop_reason_code=row["stop_reason_code"],
    )


def _decode_worker_operation(row: sqlite3.Row) -> WorkerOperationReceipt:
    return WorkerOperationReceipt(
        operation_sequence=row["operation_sequence"],
        operation_id=row["operation_id"],
        operation_kind=WorkerOperationKind(row["operation_kind"]),
        command_id=row["command_id"],
        project_id=row["project_id"],
        development_run_id=row["development_run_id"],
        phase_id=row["phase_id"],
        session_id=row["session_id"],
        worker_id=row["worker_id"],
        queue_owner_id=row["queue_owner_id"],
        claim_count=row["claim_count"],
        precondition_state_version=row["precondition_state_version"],
        state=WorkerOperationState(row["state"]),
        state_version=row["state_version"],
        external_effect_class=row["external_effect_class"],
        reconciliation_status=WorkerReconciliationStatus(
            row["reconciliation_status"]
        ),
        durable_failure_code=row["durable_failure_code"],
        diagnostic_detail=row["diagnostic_detail"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        started_at=row["started_at"],
        completed_at=row["completed_at"],
    )


class SqliteWorkerJournalRepository:
    """SQLite Worker journal with single-attempt CAS and reconciliation."""

    def __init__(self, database_path: Path) -> None:
        canonical, identity = _canonical_database_path(database_path)
        self._database_path = canonical
        self._database_identity = identity

    @property
    def database_path(self) -> Path:
        return self._database_path

    @property
    def database_identity(self) -> str:
        return self._database_identity

    @staticmethod
    def _is_busy(error: BaseException) -> bool:
        return isinstance(error, sqlite3.Error) and any(
            token in str(error).lower() for token in ("locked", "busy")
        )

    @staticmethod
    def _session_identity(session_id: str) -> str:
        return _identity_text(("session_id", session_id),)

    @staticmethod
    def _operation_identity(operation_id: str) -> str:
        return _identity_text(("operation_id", operation_id),)

    def _open(self, entity_name: str, identity: str) -> sqlite3.Connection:
        return _open_connection(self._database_path, entity_name, identity)

    def _open_read_only(
        self, entity_name: str, identity: str
    ) -> sqlite3.Connection:
        return _open_read_only_connection(
            self._database_path, entity_name, identity
        )

    def _binding_matches(self) -> bool:
        try:
            _path, identity = _canonical_database_path(self._database_path)
        except (OSError, TypeError, ValueError):
            return False
        return identity == self._database_identity

    def _load_session(
        self, connection: sqlite3.Connection, session_id: str, identity: str
    ) -> WorkerSession | None:
        row = connection.execute(
            f"SELECT {_SESSION_COLUMNS} FROM worker_sessions WHERE session_id=?",
            (session_id,),
        ).fetchone()
        if row is None:
            return None
        try:
            return _decode_worker_session(row)
        except (TypeError, ValueError, KeyError, IndexError) as error:
            _raise_payload_error(error, "WorkerSession", identity)

    def _load_operation(
        self, connection: sqlite3.Connection, operation_id: str, identity: str
    ) -> WorkerOperationReceipt | None:
        row = connection.execute(
            f"SELECT {_OPERATION_COLUMNS} FROM worker_operations WHERE operation_id=?",
            (operation_id,),
        ).fetchone()
        if row is None:
            return None
        try:
            return _decode_worker_operation(row)
        except (TypeError, ValueError, KeyError, IndexError) as error:
            _raise_payload_error(error, "WorkerOperationReceipt", identity)

    def _command_in_connection(
        self,
        connection: sqlite3.Connection,
        command_id: str,
        *,
        validate_history: bool = True,
    ) -> WorkflowCommand | None:
        identity = _identity_text(("command_id", command_id),)
        row = connection.execute(
            f"SELECT {_COMMAND_COLUMNS} FROM workflow_commands WHERE command_id=?",
            (command_id,),
        ).fetchone()
        if row is None:
            return None
        queue_repository = SqliteWorkflowCommandRepository(self._database_path)
        if validate_history:
            return queue_repository._command_from_row(connection, row, identity)
        try:
            return _decode_workflow_command(row)
        except (TypeError, ValueError, KeyError, IndexError) as error:
            _raise_payload_error(error, "WorkflowCommand", identity)

    def _mutation(
        self,
        entity_name: str,
        identity: str,
        operation: object,
    ) -> WorkerJournalResult:
        if not self._binding_matches():
            return WorkerJournalResult(WorkerJournalResultCode.DATABASE_MISMATCH)
        try:
            connection = self._open(entity_name, identity)
        except RepositoryError as error:
            if self._is_busy(error.__cause__):
                return WorkerJournalResult(WorkerJournalResultCode.TRANSIENT_CONTENTION)
            raise
        except sqlite3.Error as error:
            if self._is_busy(error):
                return WorkerJournalResult(WorkerJournalResultCode.TRANSIENT_CONTENTION)
            _raise_repository_error(
                RepositoryFailureCode.SQLITE_OPERATIONAL_FAILURE,
                entity_name,
                identity,
                error,
            )
        preserve_secondary_failure = False
        try:
            connection.execute("BEGIN IMMEDIATE")
            result = operation(connection)  # type: ignore[operator]
            if type(result) is not WorkerJournalResult:
                raise RepositoryError(
                    RepositoryFailureCode.MALFORMED_PERSISTED_PAYLOAD,
                    entity_name,
                    identity,
                )
            connection.commit()
            return result
        except _CasRollback as disposition:
            preserve_secondary_failure = True
            try:
                _rollback_if_active(connection)
            except BaseException:
                pass
            return disposition.result
        except sqlite3.IntegrityError as error:
            preserve_secondary_failure = True
            try:
                _rollback_if_active(connection)
            except BaseException:
                pass
            _translate_integrity_error(error, entity_name, identity)
        except sqlite3.Error as error:
            preserve_secondary_failure = True
            try:
                _rollback_if_active(connection)
            except BaseException:
                pass
            if self._is_busy(error):
                return WorkerJournalResult(WorkerJournalResultCode.TRANSIENT_CONTENTION)
            _raise_repository_error(
                RepositoryFailureCode.SQLITE_OPERATIONAL_FAILURE,
                entity_name,
                identity,
                error,
            )
        except BaseException:
            preserve_secondary_failure = True
            try:
                _rollback_if_active(connection)
            except BaseException:
                pass
            raise
        finally:
            primary_active = sys.exc_info()[0] is not None
            try:
                connection.close()
            except sqlite3.Error as error:
                if not preserve_secondary_failure and not primary_active:
                    _raise_repository_error(
                        RepositoryFailureCode.SQLITE_OPERATIONAL_FAILURE,
                        entity_name,
                        identity,
                        error,
                    )

    def _read_result(
        self,
        entity_name: str,
        identity: str,
        operation: object,
    ) -> WorkerJournalResult:
        if not self._binding_matches():
            return WorkerJournalResult(WorkerJournalResultCode.DATABASE_MISMATCH)
        try:
            connection = self._open_read_only(entity_name, identity)
        except RepositoryError as error:
            if self._is_busy(error.__cause__):
                return WorkerJournalResult(
                    WorkerJournalResultCode.TRANSIENT_CONTENTION
                )
            raise
        except sqlite3.Error as error:
            if self._is_busy(error):
                return WorkerJournalResult(
                    WorkerJournalResultCode.TRANSIENT_CONTENTION
                )
            _raise_repository_error(
                RepositoryFailureCode.SQLITE_OPERATIONAL_FAILURE,
                entity_name,
                identity,
                error,
            )
        preserve_secondary_failure = False
        try:
            result = operation(connection)  # type: ignore[operator]
            if type(result) is not WorkerJournalResult:
                raise RepositoryError(
                    RepositoryFailureCode.MALFORMED_PERSISTED_PAYLOAD,
                    entity_name,
                    identity,
                )
            return result
        except sqlite3.Error as error:
            preserve_secondary_failure = True
            if self._is_busy(error):
                return WorkerJournalResult(
                    WorkerJournalResultCode.TRANSIENT_CONTENTION
                )
            _raise_repository_error(
                RepositoryFailureCode.SQLITE_OPERATIONAL_FAILURE,
                entity_name,
                identity,
                error,
            )
        except BaseException:
            preserve_secondary_failure = True
            raise
        finally:
            primary_active = sys.exc_info()[0] is not None
            try:
                connection.close()
            except sqlite3.Error as error:
                if not preserve_secondary_failure and not primary_active:
                    _raise_repository_error(
                        RepositoryFailureCode.SQLITE_OPERATIONAL_FAILURE,
                        entity_name,
                        identity,
                        error,
                    )

    def _inspect_startup_session(
        self,
        *,
        worker_id: str,
        stale_before: str,
    ) -> WorkerJournalResult:
        """Observe stage-5 session precedence without creating authority."""

        worker = _worker_id(worker_id)
        _parse_queue_timestamp(stale_before, "stale_before")
        identity = _identity_text(("worker_id", worker),)

        def operation(connection: sqlite3.Connection) -> WorkerJournalResult:
            rows = connection.execute(
                f"SELECT {_SESSION_COLUMNS} FROM worker_sessions "
                "WHERE worker_id=? ORDER BY session_sequence",
                (worker,),
            ).fetchall()
            try:
                decoded = tuple(_decode_worker_session(row) for row in rows)
            except (TypeError, ValueError, KeyError, IndexError) as error:
                _raise_payload_error(error, "WorkerSession", identity)
            nonterminal = tuple(
                session
                for session in decoded
                if session.state
                in {WorkerSessionState.ACTIVE, WorkerSessionState.STOPPING}
            )
            if not nonterminal:
                return WorkerJournalResult(WorkerJournalResultCode.NOT_FOUND)
            if len(nonterminal) != 1:
                raise RepositoryError(
                    RepositoryFailureCode.MALFORMED_PERSISTED_PAYLOAD,
                    "WorkerSession",
                    identity,
                )
            session = nonterminal[0]
            return WorkerJournalResult(
                (
                    WorkerJournalResultCode.STALE_SESSION_RECONCILIATION_REQUIRED
                    if session.last_heartbeat_at <= stale_before
                    else WorkerJournalResultCode.ACTIVE_SESSION_EXISTS
                ),
                session=session,
            )

        return self._read_result("WorkerSession", identity, operation)

    def _list_startup_terminal_commands(
        self, *, worker_id: str
    ) -> WorkerJournalResult:
        """List terminal/missing-receipt facts relevant only to startup."""

        worker = _worker_id(worker_id)
        identity = _identity_text(("worker_id", worker),)

        def operation(connection: sqlite3.Connection) -> WorkerJournalResult:
            session_rows = connection.execute(
                f"SELECT {_SESSION_COLUMNS} FROM worker_sessions "
                "WHERE worker_id=? ORDER BY session_sequence",
                (worker,),
            ).fetchall()
            try:
                sessions = tuple(
                    _decode_worker_session(row) for row in session_rows
                )
            except (TypeError, ValueError, KeyError, IndexError) as error:
                _raise_payload_error(error, "WorkerSession", identity)
            owners = tuple(
                dict.fromkeys(session.queue_owner_id for session in sessions)
            )
            if not owners:
                return WorkerJournalResult(WorkerJournalResultCode.LISTED)
            operation_rows = connection.execute(
                f"SELECT {_OPERATION_COLUMNS} FROM worker_operations "
                "WHERE worker_id=? ORDER BY operation_sequence",
                (worker,),
            ).fetchall()
            try:
                decoded_operations = tuple(
                    _decode_worker_operation(row) for row in operation_rows
                )
            except (TypeError, ValueError, KeyError, IndexError) as error:
                _raise_payload_error(error, "WorkerOperationReceipt", identity)
            owner_placeholders = ",".join("?" for _owner in owners)
            event_rows = connection.execute(
                f"SELECT {_EVENT_COLUMNS} FROM workflow_command_events "
                f"WHERE lease_owner IN ({owner_placeholders}) "
                "ORDER BY event_sequence",
                owners,
            ).fetchall()
            try:
                owned_events = tuple(
                    _decode_workflow_command_event(row) for row in event_rows
                )
            except (TypeError, ValueError, KeyError, IndexError) as error:
                _raise_payload_error(error, "WorkflowCommandEvent", identity)
            command_ids = tuple(
                dict.fromkeys(
                    (
                        *(value.command_id for value in decoded_operations),
                        *(value.command_id for value in owned_events),
                    )
                )
            )
            if not command_ids:
                return WorkerJournalResult(WorkerJournalResultCode.LISTED)
            command_placeholders = ",".join("?" for _command_id in command_ids)
            rows = connection.execute(
                f"SELECT {_COMMAND_COLUMNS} FROM workflow_commands "
                f"WHERE command_id IN ({command_placeholders}) "
                "ORDER BY queue_sequence",
                command_ids,
            ).fetchall()
            queue_repository = SqliteWorkflowCommandRepository(self._database_path)
            decoded_commands = tuple(
                queue_repository._command_from_row(
                    connection,
                    row,
                    _identity_text(("command_id", row["command_id"]),),
                )
                for row in rows
            )
            if len(decoded_commands) != len(command_ids):
                _raise_payload_error(
                    ValueError("missing owned command"),
                    "WorkflowCommand",
                    identity,
                )
            histories = {
                command.command_id: queue_repository._history_in_connection(
                    connection,
                    command,
                    _identity_text(("command_id", command.command_id),),
                )
                for command in decoded_commands
            }
            commands_by_id = {
                command.command_id: command for command in decoded_commands
            }
            sessions_by_id = {
                session.session_id: session for session in sessions
            }
            for receipt in decoded_operations:
                command = commands_by_id.get(receipt.command_id)
                if (
                    command is None
                    or receipt.claim_count != command.claim_count
                ):
                    continue
                claimed = tuple(
                    event
                    for event in histories[command.command_id]
                    if event.event_kind is WorkflowCommandEventKind.CLAIMED
                    and event.claim_count == receipt.claim_count
                )
                session = sessions_by_id.get(receipt.session_id)
                if (
                    receipt.project_id != command.project_id
                    or receipt.development_run_id != command.development_run_id
                    or receipt.phase_id != command.phase_id
                    or session is None
                    or session.worker_id != receipt.worker_id
                    or session.queue_owner_id != receipt.queue_owner_id
                    or len(claimed) != 1
                    or claimed[0].lease_owner != receipt.queue_owner_id
                    or claimed[0].next_state_version
                    != receipt.precondition_state_version
                ):
                    _raise_payload_error(
                        ValueError("owned receipt identity"),
                        "WorkerOperationReceipt",
                        identity,
                    )
            commands = tuple(
                command
                for command in decoded_commands
                if any(
                    receipt.command_id == command.command_id
                    and receipt.claim_count == command.claim_count
                    and receipt.state in _WORKER_TERMINAL_OPERATION_STATES
                    for receipt in decoded_operations
                )
                or (
                    command.state
                    in {
                        WorkflowCommandState.SUCCEEDED,
                        WorkflowCommandState.FAILED,
                        WorkflowCommandState.CANCELLED,
                    }
                    and any(
                        event.command_id == command.command_id
                        and event.claim_count == command.claim_count
                        and event.lease_owner in owners
                        for event in owned_events
                    )
                )
            )
            return WorkerJournalResult(
                WorkerJournalResultCode.LISTED, commands=commands
            )

        return self._read_result("WorkflowCommand", identity, operation)

    def start_session(
        self,
        *,
        session_id: str,
        worker_id: str,
        queue_owner_id: str,
        started_at: str,
        stale_before: str,
    ) -> WorkerJournalResult:
        session = _worker_uuid(session_id, "session_id")
        worker = _worker_id(worker_id)
        owner = _worker_queue_owner(queue_owner_id, worker, session)
        started_instant = _parse_queue_timestamp(started_at, "started_at")
        stale_instant = _parse_queue_timestamp(stale_before, "stale_before")
        if started_instant <= stale_instant:
            raise ValueError("started_at")
        identity = self._session_identity(session)

        def operation(connection: sqlite3.Connection) -> WorkerJournalResult:
            collision_row = connection.execute(
                f"SELECT {_SESSION_COLUMNS} FROM worker_sessions "
                "WHERE session_id=?",
                (session,),
            ).fetchone()
            if collision_row is not None:
                try:
                    collision = _decode_worker_session(collision_row)
                except (TypeError, ValueError, KeyError, IndexError) as error:
                    _raise_payload_error(error, "WorkerSession", identity)
                if (
                    collision.worker_id != worker
                    or collision.queue_owner_id != owner
                    or collision.started_at != started_at
                ):
                    return WorkerJournalResult(
                        WorkerJournalResultCode.CAS_CONFLICT, session=collision
                    )
                if collision.state not in {
                    WorkerSessionState.ACTIVE,
                    WorkerSessionState.STOPPING,
                }:
                    return WorkerJournalResult(
                        WorkerJournalResultCode.CAS_CONFLICT, session=collision
                    )
                code = (
                    WorkerJournalResultCode.ACTIVE_SESSION_EXISTS
                    if collision.last_heartbeat_at > stale_before
                    else WorkerJournalResultCode.STALE_SESSION_RECONCILIATION_REQUIRED
                )
                return WorkerJournalResult(code, session=collision)
            rows = connection.execute(
                f"SELECT {_SESSION_COLUMNS} FROM worker_sessions "
                "WHERE worker_id=? ORDER BY session_sequence DESC",
                (worker,),
            ).fetchall()
            try:
                decoded_sessions = tuple(_decode_worker_session(row) for row in rows)
            except (TypeError, ValueError, KeyError, IndexError) as error:
                _raise_payload_error(error, "WorkerSession", identity)
            current_sessions = tuple(
                value
                for value in decoded_sessions
                if value.state in {WorkerSessionState.ACTIVE, WorkerSessionState.STOPPING}
            )
            if current_sessions:
                current = current_sessions[0]
                code = (
                    WorkerJournalResultCode.ACTIVE_SESSION_EXISTS
                    if current.last_heartbeat_at > stale_before
                    else WorkerJournalResultCode.STALE_SESSION_RECONCILIATION_REQUIRED
                )
                return WorkerJournalResult(code, session=current)
            connection.execute(
                "INSERT INTO worker_sessions("
                "session_id,worker_id,queue_owner_id,state,state_version,"
                "started_at,last_heartbeat_at,stopped_at,stop_reason_code) "
                "VALUES(?,?,?,'ACTIVE',1,?,?,NULL,NULL)",
                (session, worker, owner, started_at, started_at),
            )
            created = self._load_session(connection, session, identity)
            assert created is not None
            return WorkerJournalResult(
                WorkerJournalResultCode.APPLIED, session=created
            )

        return self._mutation("WorkerSession", identity, operation)

    def get_session(self, *, session_id: str) -> WorkerJournalResult:
        session = _worker_uuid(session_id, "session_id")
        identity = self._session_identity(session)
        def operation(connection: sqlite3.Connection) -> WorkerJournalResult:
            value = self._load_session(connection, session, identity)
            return WorkerJournalResult(
                WorkerJournalResultCode.NOT_FOUND
                if value is None
                else WorkerJournalResultCode.FOUND,
                session=value,
            )

        return self._read_result("WorkerSession", identity, operation)

    def heartbeat(
        self,
        *,
        session_id: str,
        expected_state: WorkerSessionState,
        expected_state_version: int,
        observed_at: str,
        stale_before: str,
    ) -> WorkerJournalResult:
        session = _worker_uuid(session_id, "session_id")
        if type(expected_state) is not WorkerSessionState:
            raise TypeError("expected_state")
        expected_version = _queue_integer(
            expected_state_version, "expected_state_version", 1, _SIGNED_64_MAX
        )
        _parse_queue_timestamp(observed_at, "observed_at")
        _parse_queue_timestamp(stale_before, "stale_before")
        identity = self._session_identity(session)

        def operation(connection: sqlite3.Connection) -> WorkerJournalResult:
            current = self._load_session(connection, session, identity)
            if current is None:
                return WorkerJournalResult(WorkerJournalResultCode.NOT_FOUND)
            if current.state in {WorkerSessionState.STOPPED, WorkerSessionState.FAILED}:
                return WorkerJournalResult(
                    WorkerJournalResultCode.TERMINAL_OBSERVED, session=current
                )
            if (
                current.state is not expected_state
                or current.state_version != expected_version
            ):
                return WorkerJournalResult(
                    WorkerJournalResultCode.CAS_CONFLICT, session=current
                )
            if current.last_heartbeat_at <= stale_before:
                return WorkerJournalResult(
                    WorkerJournalResultCode.STALE_SESSION_RECONCILIATION_REQUIRED,
                    session=current,
                )
            if observed_at < current.last_heartbeat_at:
                raise ValueError("observed_at")
            cursor = connection.execute(
                "UPDATE worker_sessions SET state_version=state_version+1, "
                "last_heartbeat_at=? WHERE session_id=? AND state=? "
                "AND state_version=? AND last_heartbeat_at>?",
                (
                    observed_at,
                    session,
                    current.state.value,
                    current.state_version,
                    stale_before,
                ),
            )
            if cursor.rowcount != 1:
                reread = self._load_session(connection, session, identity)
                assert reread is not None
                raise _CasRollback(
                    WorkerJournalResult(
                        WorkerJournalResultCode.CAS_CONFLICT, session=reread
                    )
                )
            updated = self._load_session(connection, session, identity)
            assert updated is not None
            return WorkerJournalResult(
                WorkerJournalResultCode.APPLIED, session=updated
            )

        return self._mutation("WorkerSession", identity, operation)

    def transition_session(
        self,
        *,
        session_id: str,
        expected_state: WorkerSessionState,
        expected_state_version: int,
        next_state: WorkerSessionState,
        reason_code: str,
        observed_at: str,
        stale_before: str,
    ) -> WorkerJournalResult:
        session = _worker_uuid(session_id, "session_id")
        if type(expected_state) is not WorkerSessionState:
            raise TypeError("expected_state")
        if type(next_state) is not WorkerSessionState:
            raise TypeError("next_state")
        expected_version = _queue_integer(
            expected_state_version, "expected_state_version", 1, _SIGNED_64_MAX
        )
        reason = _queue_text(reason_code, "reason_code", _WORKER_RESULT_CODE_PATTERN)
        _parse_queue_timestamp(observed_at, "observed_at")
        _parse_queue_timestamp(stale_before, "stale_before")
        identity = self._session_identity(session)

        def operation(connection: sqlite3.Connection) -> WorkerJournalResult:
            current = self._load_session(connection, session, identity)
            if current is None:
                return WorkerJournalResult(WorkerJournalResultCode.NOT_FOUND)
            if current.state in {WorkerSessionState.STOPPED, WorkerSessionState.FAILED}:
                return WorkerJournalResult(
                    WorkerJournalResultCode.TERMINAL_OBSERVED, session=current
                )
            if (
                current.state is not expected_state
                or current.state_version != expected_version
            ):
                return WorkerJournalResult(
                    WorkerJournalResultCode.CAS_CONFLICT, session=current
                )
            if current.last_heartbeat_at <= stale_before:
                return WorkerJournalResult(
                    WorkerJournalResultCode.STALE_SESSION_RECONCILIATION_REQUIRED,
                    session=current,
                )
            if observed_at < current.last_heartbeat_at:
                raise ValueError("observed_at")
            if (
                current.state is WorkerSessionState.STOPPING
                and next_state is WorkerSessionState.STOPPING
            ):
                code = (
                    WorkerJournalResultCode.APPLIED
                    if current.stop_reason_code == reason
                    else WorkerJournalResultCode.CAS_CONFLICT
                )
                return WorkerJournalResult(code, session=current)
            allowed = {
                WorkerSessionState.ACTIVE: {
                    WorkerSessionState.STOPPING,
                    WorkerSessionState.FAILED,
                },
                WorkerSessionState.STOPPING: {
                    WorkerSessionState.STOPPED,
                    WorkerSessionState.FAILED,
                },
            }
            if next_state not in allowed[current.state]:
                return WorkerJournalResult(
                    WorkerJournalResultCode.CAS_CONFLICT, session=current
                )
            if next_state is WorkerSessionState.STOPPED:
                session_rows = connection.execute(
                    f"SELECT {_SESSION_COLUMNS} FROM worker_sessions "
                    "WHERE worker_id=? ORDER BY session_sequence",
                    (current.worker_id,),
                ).fetchall()
                operation_rows = connection.execute(
                    f"SELECT {_OPERATION_COLUMNS} FROM worker_operations "
                    "WHERE worker_id=? ORDER BY operation_sequence",
                    (current.worker_id,),
                ).fetchall()
                try:
                    worker_sessions = tuple(
                        _decode_worker_session(row) for row in session_rows
                    )
                    worker_operations = tuple(
                        _decode_worker_operation(row) for row in operation_rows
                    )
                except (TypeError, ValueError, KeyError, IndexError) as error:
                    _raise_payload_error(error, "WorkerSession", identity)
                owners = tuple(
                    value.queue_owner_id for value in worker_sessions
                )
                operation_command_ids = tuple(
                    dict.fromkeys(
                        value.command_id for value in worker_operations
                    )
                )
                clauses: list[str] = []
                command_parameters: list[str] = []
                if owners:
                    clauses.append(
                        "lease_owner IN (" + ",".join("?" for _ in owners) + ")"
                    )
                    command_parameters.extend(owners)
                if operation_command_ids:
                    clauses.append(
                        "command_id IN ("
                        + ",".join("?" for _ in operation_command_ids)
                        + ")"
                    )
                    command_parameters.extend(operation_command_ids)
                command_rows = (
                    connection.execute(
                        f"SELECT {_COMMAND_COLUMNS} FROM workflow_commands WHERE "
                        + " OR ".join(clauses)
                        + " ORDER BY queue_sequence",
                        tuple(command_parameters),
                    ).fetchall()
                    if clauses
                    else ()
                )
                queue_repository = SqliteWorkflowCommandRepository(
                    self._database_path
                )
                commands = tuple(
                    queue_repository._command_from_row(
                        connection,
                        row,
                        _identity_text(("command_id", row["command_id"]),),
                    )
                    for row in command_rows
                )
                if not set(operation_command_ids).issubset(
                    {value.command_id for value in commands}
                ):
                    _raise_payload_error(
                        ValueError("missing operation command"),
                        "WorkerOperationReceipt",
                        identity,
                    )
                if any(
                    value.state
                    in {
                        WorkerOperationState.PREPARED,
                        WorkerOperationState.RUNNING,
                        WorkerOperationState.RESULT_SUCCEEDED,
                        WorkerOperationState.RESULT_FAILED,
                        WorkerOperationState.CANCELLATION_OBSERVED,
                    }
                    for value in worker_operations
                ) or any(
                    value.state
                    in {WorkflowCommandState.CLAIMED, WorkflowCommandState.RUNNING}
                    for value in commands
                ):
                    return WorkerJournalResult(
                        WorkerJournalResultCode.CAS_CONFLICT, session=current
                    )
            terminal = next_state in {
                WorkerSessionState.STOPPED,
                WorkerSessionState.FAILED,
            }
            cursor = connection.execute(
                "UPDATE worker_sessions SET state=?, state_version=state_version+1, "
                "stopped_at=?, stop_reason_code=? WHERE session_id=? "
                "AND state=? AND state_version=? AND last_heartbeat_at>?",
                (
                    next_state.value,
                    observed_at if terminal else None,
                    reason,
                    session,
                    current.state.value,
                    current.state_version,
                    stale_before,
                ),
            )
            if cursor.rowcount != 1:
                reread = self._load_session(connection, session, identity)
                assert reread is not None
                raise _CasRollback(
                    WorkerJournalResult(
                        WorkerJournalResultCode.CAS_CONFLICT, session=reread
                    )
                )
            updated = self._load_session(connection, session, identity)
            assert updated is not None
            return WorkerJournalResult(
                WorkerJournalResultCode.APPLIED, session=updated
            )

        return self._mutation("WorkerSession", identity, operation)

    def create_operation(
        self,
        *,
        operation_id: str,
        command: WorkflowCommand,
        operation_kind: WorkerOperationKind,
        worker_id: str,
        session_id: str,
        queue_owner_id: str,
        occurred_at: str,
        stale_before: str,
    ) -> WorkerJournalResult:
        operation_value = _worker_uuid(operation_id, "operation_id")
        if type(command) is not WorkflowCommand:
            raise TypeError("command")
        if type(operation_kind) is not WorkerOperationKind:
            raise TypeError("operation_kind")
        worker = _worker_id(worker_id)
        session = _worker_uuid(session_id, "session_id")
        owner = _worker_queue_owner(queue_owner_id, worker, session)
        _parse_queue_timestamp(occurred_at, "occurred_at")
        _parse_queue_timestamp(stale_before, "stale_before")
        identity = self._operation_identity(operation_value)

        def operation(connection: sqlite3.Connection) -> WorkerJournalResult:
            existing_rows = connection.execute(
                f"SELECT {_OPERATION_COLUMNS} FROM worker_operations "
                "WHERE operation_id=? OR (command_id=? AND claim_count=?) "
                "ORDER BY operation_sequence",
                (operation_value, command.command_id, command.claim_count),
            ).fetchall()
            if existing_rows:
                try:
                    decoded_existing = tuple(
                        _decode_worker_operation(row) for row in existing_rows
                    )
                except (TypeError, ValueError, KeyError, IndexError) as error:
                    _raise_payload_error(error, "WorkerOperationReceipt", identity)
                existing = decoded_existing[0]
                exact = (
                    len(decoded_existing) == 1
                    and
                    existing.operation_id == operation_value
                    and existing.operation_kind is operation_kind
                    and existing.command_id == command.command_id
                    and existing.project_id == command.project_id
                    and existing.development_run_id == command.development_run_id
                    and existing.phase_id == command.phase_id
                    and existing.claim_count == command.claim_count
                    and existing.precondition_state_version
                    == command.state_version
                    and existing.session_id == session
                    and existing.worker_id == worker
                    and existing.queue_owner_id == owner
                )
                return WorkerJournalResult(
                    WorkerJournalResultCode.APPLIED
                    if exact
                    else WorkerJournalResultCode.CAS_CONFLICT,
                    operation=existing,
                )
            current_session = self._load_session(
                connection, session, self._session_identity(session)
            )
            if current_session is None:
                return WorkerJournalResult(WorkerJournalResultCode.NOT_FOUND)
            if (
                current_session.worker_id != worker
                or current_session.queue_owner_id != owner
            ):
                return WorkerJournalResult(
                    WorkerJournalResultCode.CAS_CONFLICT,
                    session=current_session,
                )
            if current_session.state in {
                WorkerSessionState.STOPPED,
                WorkerSessionState.FAILED,
            }:
                return WorkerJournalResult(
                    WorkerJournalResultCode.TERMINAL_OBSERVED,
                    session=current_session,
                )
            if current_session.last_heartbeat_at <= stale_before:
                return WorkerJournalResult(
                    WorkerJournalResultCode.STALE_SESSION_RECONCILIATION_REQUIRED,
                    session=current_session,
                )
            current_command = self._command_in_connection(
                connection, command.command_id
            )
            if current_command is None:
                return WorkerJournalResult(WorkerJournalResultCode.NOT_FOUND)
            if current_command != command:
                return WorkerJournalResult(WorkerJournalResultCode.CAS_CONFLICT)
            if occurred_at < current_command.updated_at:
                raise ValueError("occurred_at")
            if (
                current_command.state is not WorkflowCommandState.CLAIMED
                or current_command.claim_count < 1
                or current_command.lease_owner != owner
                or current_command.lease_expires_at is None
                or occurred_at >= current_command.lease_expires_at
            ):
                return WorkerJournalResult(WorkerJournalResultCode.CAS_CONFLICT)
            connection.execute(
                "INSERT INTO worker_operations("
                "operation_id,operation_kind,command_id,project_id,"
                "development_run_id,phase_id,session_id,worker_id,queue_owner_id,"
                "claim_count,precondition_state_version,state,state_version,"
                "external_effect_class,reconciliation_status,durable_failure_code,"
                "diagnostic_detail,created_at,updated_at,started_at,completed_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,'PREPARED',1,'NONE','NOT_REQUIRED',"
                "NULL,NULL,?,?,NULL,NULL)",
                (
                    operation_value,
                    operation_kind.value,
                    command.command_id,
                    command.project_id,
                    command.development_run_id,
                    command.phase_id,
                    session,
                    worker,
                    owner,
                    command.claim_count,
                    command.state_version,
                    occurred_at,
                    occurred_at,
                ),
            )
            created = self._load_operation(connection, operation_value, identity)
            assert created is not None
            return WorkerJournalResult(
                WorkerJournalResultCode.APPLIED, operation=created
            )

        return self._mutation("WorkerOperationReceipt", identity, operation)

    def get_operation_for_claim(
        self, *, command_id: str, claim_count: int
    ) -> WorkerJournalResult:
        command = _queue_uuid(command_id, "command_id")
        claim = _queue_integer(claim_count, "claim_count", 1, _SIGNED_64_MAX)
        identity = _identity_text(("command_id", command), ("claim_count", str(claim)))
        def operation(connection: sqlite3.Connection) -> WorkerJournalResult:
            row = connection.execute(
                f"SELECT {_OPERATION_COLUMNS} FROM worker_operations "
                "WHERE command_id=? AND claim_count=?",
                (command, claim),
            ).fetchone()
            if row is None:
                return WorkerJournalResult(WorkerJournalResultCode.NOT_FOUND)
            try:
                receipt = _decode_worker_operation(row)
            except (TypeError, ValueError, KeyError, IndexError) as error:
                _raise_payload_error(error, "WorkerOperationReceipt", identity)
            return WorkerJournalResult(
                WorkerJournalResultCode.FOUND, operation=receipt
            )

        return self._read_result("WorkerOperationReceipt", identity, operation)

    def transition_operation(
        self,
        *,
        operation_id: str,
        expected_state: WorkerOperationState,
        expected_state_version: int,
        next_state: WorkerOperationState,
        reconciliation_status: WorkerReconciliationStatus,
        durable_failure_code: str | None,
        diagnostic_detail: str | None,
        occurred_at: str,
        stale_before: str,
    ) -> WorkerJournalResult:
        operation_value = _worker_uuid(operation_id, "operation_id")
        if type(expected_state) is not WorkerOperationState:
            raise TypeError("expected_state")
        if type(next_state) is not WorkerOperationState:
            raise TypeError("next_state")
        if type(reconciliation_status) is not WorkerReconciliationStatus:
            raise TypeError("reconciliation_status")
        expected_version = _queue_integer(
            expected_state_version, "expected_state_version", 1, _SIGNED_64_MAX
        )
        if durable_failure_code is not None:
            _queue_text(
                durable_failure_code,
                "durable_failure_code",
                _WORKER_RESULT_CODE_PATTERN,
            )
        if durable_failure_code is not None and durable_failure_code not in {
            value.value for value in WorkerFailureCode
        }:
            raise ValueError("durable_failure_code")
        if diagnostic_detail is not None:
            if type(diagnostic_detail) is not str:
                raise TypeError("diagnostic_detail")
            if diagnostic_detail not in _WORKER_DIAGNOSTICS:
                raise ValueError("diagnostic_detail")
        if next_state not in _WORKER_RECEIPT_TRANSITIONS.get(
            expected_state, frozenset()
        ):
            raise ValueError("operation transition")
        if expected_state is WorkerOperationState.PREPARED and next_state in {
            WorkerOperationState.FAILED,
            WorkerOperationState.CANCELLED,
            WorkerOperationState.CLAIM_RELEASED,
        }:
            raise ValueError("reconciliation-only transition")
        required_reconciliation = next_state in {
            WorkerOperationState.LEASE_LOST,
            WorkerOperationState.RECONCILIATION_REQUIRED,
        }
        if required_reconciliation != (
            reconciliation_status is WorkerReconciliationStatus.REQUIRED
        ):
            raise ValueError("reconciliation_status")
        if not _worker_receipt_code_diagnostic_valid(
            next_state, durable_failure_code, diagnostic_detail
        ):
            raise ValueError("worker receipt domain")
        if next_state in {
            WorkerOperationState.CANCELLATION_OBSERVED,
            WorkerOperationState.CANCELLED,
        } and (
            durable_failure_code != WorkerFailureCode.CANCELLATION_OBSERVED.value
            or diagnostic_detail is not None
        ):
            raise ValueError("cancellation transition")
        if next_state is WorkerOperationState.LEASE_LOST and (
            durable_failure_code != WorkerFailureCode.LEASE_LOST.value
            or diagnostic_detail is not None
        ):
            raise ValueError("lease-loss transition")
        if (
            expected_state is WorkerOperationState.PREPARED
            and next_state is WorkerOperationState.RECONCILIATION_REQUIRED
            and durable_failure_code
            != WorkerFailureCode.RECONCILIATION_REQUIRED.value
        ):
            raise ValueError("prepared reconciliation transition")
        _parse_queue_timestamp(occurred_at, "occurred_at")
        _parse_queue_timestamp(stale_before, "stale_before")
        identity = self._operation_identity(operation_value)

        def operation(connection: sqlite3.Connection) -> WorkerJournalResult:
            current = self._load_operation(connection, operation_value, identity)
            if current is None:
                return WorkerJournalResult(WorkerJournalResultCode.NOT_FOUND)
            if current.state in _WORKER_TERMINAL_OPERATION_STATES:
                return WorkerJournalResult(
                    WorkerJournalResultCode.TERMINAL_OBSERVED, operation=current
                )
            if (
                current.state is not expected_state
                or current.state_version != expected_version
            ):
                return WorkerJournalResult(
                    WorkerJournalResultCode.CAS_CONFLICT, operation=current
                )
            if next_state not in _WORKER_RECEIPT_TRANSITIONS.get(
                current.state, frozenset()
            ):
                return WorkerJournalResult(
                    WorkerJournalResultCode.CAS_CONFLICT, operation=current
                )
            if occurred_at < current.updated_at:
                raise ValueError("occurred_at")
            if current.state is WorkerOperationState.PREPARED and next_state in {
                WorkerOperationState.FAILED,
                WorkerOperationState.CANCELLED,
                WorkerOperationState.CLAIM_RELEASED,
            }:
                return WorkerJournalResult(
                    WorkerJournalResultCode.CAS_CONFLICT, operation=current
                )
            if next_state in {
                WorkerOperationState.CANCELLATION_OBSERVED,
                WorkerOperationState.CANCELLED,
            } and (
                durable_failure_code
                != WorkerFailureCode.CANCELLATION_OBSERVED.value
                or diagnostic_detail is not None
            ):
                return WorkerJournalResult(
                    WorkerJournalResultCode.CAS_CONFLICT, operation=current
                )
            if next_state is WorkerOperationState.LEASE_LOST and (
                durable_failure_code != WorkerFailureCode.LEASE_LOST.value
                or diagnostic_detail is not None
            ):
                return WorkerJournalResult(
                    WorkerJournalResultCode.CAS_CONFLICT, operation=current
                )
            if (
                current.state is WorkerOperationState.PREPARED
                and next_state is WorkerOperationState.RECONCILIATION_REQUIRED
                and durable_failure_code
                != WorkerFailureCode.RECONCILIATION_REQUIRED.value
            ):
                return WorkerJournalResult(
                    WorkerJournalResultCode.CAS_CONFLICT, operation=current
                )
            diagnostic_introduction = (
                current.state is WorkerOperationState.RUNNING
                and next_state is WorkerOperationState.RESULT_FAILED
                and diagnostic_detail in {
                    "HANDLER_PROTOCOL_ERROR:INVALID_RETURN",
                    "HANDLER_PROTOCOL_ERROR:CANCELLED_WITHOUT_AUTHORITATIVE_CANCELLATION",
                }
            ) or (
                current.state is WorkerOperationState.PREPARED
                and next_state is WorkerOperationState.RECONCILIATION_REQUIRED
                and diagnostic_detail
                == "HANDLER_LOOKUP_MISMATCH:FROZEN_REGISTRY_INVARIANT_LOST"
            )
            if diagnostic_detail is not None and (
                current.diagnostic_detail is None and not diagnostic_introduction
            ):
                return WorkerJournalResult(
                    WorkerJournalResultCode.CAS_CONFLICT, operation=current
                )
            if current.diagnostic_detail is not None and diagnostic_detail not in {
                current.diagnostic_detail,
                None if next_state in {
                    WorkerOperationState.CANCELLED,
                    WorkerOperationState.LEASE_LOST,
                } else current.diagnostic_detail,
            }:
                return WorkerJournalResult(
                    WorkerJournalResultCode.CAS_CONFLICT, operation=current
                )
            if (
                current.state is WorkerOperationState.RESULT_FAILED
                and next_state is WorkerOperationState.FAILED
                and (
                    durable_failure_code != current.durable_failure_code
                    or diagnostic_detail != current.diagnostic_detail
                )
            ):
                return WorkerJournalResult(
                    WorkerJournalResultCode.CAS_CONFLICT, operation=current
                )
            command = self._command_in_connection(connection, current.command_id)
            if command is None:
                return WorkerJournalResult(WorkerJournalResultCode.NOT_FOUND)
            queue_repository = SqliteWorkflowCommandRepository(
                self._database_path
            )
            events = queue_repository._history_in_connection(
                connection,
                command,
                _identity_text(("command_id", command.command_id),),
            )
            claimed = tuple(
                event
                for event in events
                if event.event_kind is WorkflowCommandEventKind.CLAIMED
                and event.claim_count == current.claim_count
            )
            started = tuple(
                event
                for event in events
                if event.event_kind is WorkflowCommandEventKind.STARTED
                and event.claim_count == current.claim_count
            )
            prestart_renewals = tuple(
                event
                for event in events
                if event.event_kind is WorkflowCommandEventKind.LEASE_RENEWED
                and event.claim_count == current.claim_count
                and event.next_state_version
                > current.precondition_state_version
                and (
                    not started
                    or event.next_state_version <= started[0].prior_state_version
                )
            )
            if (
                current.project_id != command.project_id
                or current.development_run_id != command.development_run_id
                or current.phase_id != command.phase_id
                or len(claimed) != 1
                or claimed[0].lease_owner != current.queue_owner_id
                or claimed[0].next_state_version
                != current.precondition_state_version
                or (
                    current.state is not WorkerOperationState.PREPARED
                    and (
                        len(started) != 1
                        or started[0].prior_state
                        is not WorkflowCommandState.CLAIMED
                        or started[0].next_state
                        is not WorkflowCommandState.RUNNING
                        or started[0].lease_owner != current.queue_owner_id
                        or started[0].prior_state_version
                        != current.precondition_state_version
                        + len(prestart_renewals)
                        or any(
                            event.lease_owner != current.queue_owner_id
                            or event.prior_state
                            is not WorkflowCommandState.CLAIMED
                            or event.next_state
                            is not WorkflowCommandState.CLAIMED
                            for event in prestart_renewals
                        )
                    )
                )
            ):
                _raise_payload_error(
                    ValueError("operation command history identity"),
                    "WorkerOperationReceipt",
                    identity,
                )
            if command.claim_count != current.claim_count:
                return WorkerJournalResult(
                    WorkerJournalResultCode.CAS_CONFLICT, operation=current
                )
            terminal_mirror = (
                (command.state is WorkflowCommandState.SUCCEEDED
                 and next_state is WorkerOperationState.SUCCEEDED)
                or (command.state is WorkflowCommandState.FAILED
                    and next_state is WorkerOperationState.FAILED)
                or (command.state is WorkflowCommandState.CANCELLED
                    and next_state is WorkerOperationState.CANCELLED)
            )
            cancelled_running_observation = False
            if (
                command.state is WorkflowCommandState.CANCELLED
                and current.state is WorkerOperationState.RUNNING
                and next_state is WorkerOperationState.CANCELLATION_OBSERVED
            ):
                latest = events[-1]
                cancelled_running_observation = (
                    len(claimed) == 1
                    and claimed[0].lease_owner == current.queue_owner_id
                    and claimed[0].next_state_version
                    == current.precondition_state_version
                    and len(started) == 1
                    and started[0].prior_state is WorkflowCommandState.CLAIMED
                    and started[0].next_state is WorkflowCommandState.RUNNING
                    and started[0].lease_owner == current.queue_owner_id
                    and latest.event_kind is WorkflowCommandEventKind.CANCELLED
                    and latest.prior_state is WorkflowCommandState.RUNNING
                    and latest.next_state is WorkflowCommandState.CANCELLED
                    and latest.claim_count == current.claim_count
                    and latest.lease_owner == current.queue_owner_id
                )
            session = self._load_session(
                connection,
                current.session_id,
                self._session_identity(current.session_id),
            )
            if session is None:
                return WorkerJournalResult(WorkerJournalResultCode.NOT_FOUND)
            if (
                session.worker_id != current.worker_id
                or session.queue_owner_id != current.queue_owner_id
                or (
                    not terminal_mirror
                    and not cancelled_running_observation
                    and session.state
                    not in {WorkerSessionState.ACTIVE, WorkerSessionState.STOPPING}
                )
            ):
                return WorkerJournalResult(
                    WorkerJournalResultCode.CAS_CONFLICT, operation=current
                )
            if (
                not terminal_mirror
                and not cancelled_running_observation
                and session.last_heartbeat_at <= stale_before
            ):
                return WorkerJournalResult(
                    WorkerJournalResultCode.STALE_SESSION_RECONCILIATION_REQUIRED,
                    session=session,
                )
            active_authority = (
                command.state in {
                    WorkflowCommandState.CLAIMED,
                    WorkflowCommandState.RUNNING,
                }
                and command.lease_owner == current.queue_owner_id
                and command.lease_expires_at is not None
                and occurred_at < command.lease_expires_at
            )
            terminal_queue_observed = command.state in {
                WorkflowCommandState.SUCCEEDED,
                WorkflowCommandState.FAILED,
                WorkflowCommandState.CANCELLED,
            }
            if (
                next_state
                in {
                    WorkerOperationState.RUNNING,
                    WorkerOperationState.RESULT_SUCCEEDED,
                    WorkerOperationState.RESULT_FAILED,
                    WorkerOperationState.CANCELLATION_OBSERVED,
                }
                and terminal_queue_observed
                and not cancelled_running_observation
            ):
                # The requested receipt mutation was never attempted.  Surface
                # the higher-precedence queue terminal observation so Worker
                # can perform its sole compatible evidence mirror.
                return WorkerJournalResult(
                    WorkerJournalResultCode.TERMINAL_OBSERVED,
                    operation=current,
                )
            if next_state in {
                WorkerOperationState.RUNNING,
                WorkerOperationState.RESULT_SUCCEEDED,
                WorkerOperationState.RESULT_FAILED,
                WorkerOperationState.CANCELLATION_OBSERVED,
            } and (
                not cancelled_running_observation
                and (
                    not active_authority
                    or command.state is not WorkflowCommandState.RUNNING
                    or (
                        next_state is WorkerOperationState.CANCELLATION_OBSERVED
                        and command.cancellation_requested_at is None
                    )
                )
            ):
                return WorkerJournalResult(
                    WorkerJournalResultCode.CAS_CONFLICT, operation=current
                )
            if next_state in {
                WorkerOperationState.SUCCEEDED,
                WorkerOperationState.FAILED,
                WorkerOperationState.CANCELLED,
            } and not terminal_mirror:
                return WorkerJournalResult(
                    WorkerJournalResultCode.CAS_CONFLICT, operation=current
                )
            if (
                next_state is WorkerOperationState.LEASE_LOST
                and terminal_queue_observed
            ):
                return WorkerJournalResult(
                    WorkerJournalResultCode.TERMINAL_OBSERVED,
                    operation=current,
                )
            if (
                next_state is WorkerOperationState.LEASE_LOST
                and active_authority
            ):
                return WorkerJournalResult(
                    WorkerJournalResultCode.CAS_CONFLICT, operation=current
                )
            if (
                next_state is WorkerOperationState.RECONCILIATION_REQUIRED
                and terminal_queue_observed
            ):
                return WorkerJournalResult(
                    WorkerJournalResultCode.TERMINAL_OBSERVED,
                    operation=current,
                )
            started_at = (
                occurred_at
                if next_state is WorkerOperationState.RUNNING
                else current.started_at
            )
            completed_at = (
                occurred_at
                if next_state in _WORKER_TERMINAL_OPERATION_STATES
                else None
            )
            candidate = WorkerOperationReceipt(
                current.operation_sequence,
                current.operation_id,
                current.operation_kind,
                current.command_id,
                current.project_id,
                current.development_run_id,
                current.phase_id,
                current.session_id,
                current.worker_id,
                current.queue_owner_id,
                current.claim_count,
                current.precondition_state_version,
                next_state,
                current.state_version + 1,
                current.external_effect_class,
                reconciliation_status,
                durable_failure_code,
                diagnostic_detail,
                current.created_at,
                occurred_at,
                started_at,
                completed_at,
            )
            cursor = connection.execute(
                "UPDATE worker_operations SET state=?, state_version=?, "
                "reconciliation_status=?, durable_failure_code=?, diagnostic_detail=?, "
                "updated_at=?, started_at=?, completed_at=? WHERE operation_id=? "
                "AND state=? AND state_version=?",
                (
                    candidate.state.value,
                    candidate.state_version,
                    candidate.reconciliation_status.value,
                    candidate.durable_failure_code,
                    candidate.diagnostic_detail,
                    candidate.updated_at,
                    candidate.started_at,
                    candidate.completed_at,
                    operation_value,
                    current.state.value,
                    current.state_version,
                ),
            )
            if cursor.rowcount != 1:
                reread = self._load_operation(connection, operation_value, identity)
                assert reread is not None
                raise _CasRollback(
                    WorkerJournalResult(
                        WorkerJournalResultCode.CAS_CONFLICT, operation=reread
                    )
                )
            updated = self._load_operation(connection, operation_value, identity)
            assert updated is not None
            return WorkerJournalResult(
                WorkerJournalResultCode.APPLIED, operation=updated
            )

        return self._mutation("WorkerOperationReceipt", identity, operation)

    def reconcile_operation(
        self,
        *,
        operation_id: str,
        expected_state: WorkerOperationState,
        expected_state_version: int,
        occurred_at: str,
    ) -> WorkerJournalResult:
        operation_value = _worker_uuid(operation_id, "operation_id")
        if type(expected_state) is not WorkerOperationState:
            raise TypeError("expected_state")
        if expected_state is not WorkerOperationState.PREPARED:
            raise ValueError("expected_state")
        expected_version = _queue_integer(
            expected_state_version, "expected_state_version", 1, _SIGNED_64_MAX
        )
        _parse_queue_timestamp(occurred_at, "occurred_at")
        identity = self._operation_identity(operation_value)

        def operation(connection: sqlite3.Connection) -> WorkerJournalResult:
            current = self._load_operation(connection, operation_value, identity)
            if current is None:
                return WorkerJournalResult(WorkerJournalResultCode.NOT_FOUND)
            if current.state in _WORKER_TERMINAL_OPERATION_STATES:
                return WorkerJournalResult(
                    WorkerJournalResultCode.TERMINAL_OBSERVED, operation=current
                )
            if (
                current.state is not expected_state
                or current.state_version != expected_version
                or current.state is not WorkerOperationState.PREPARED
            ):
                return WorkerJournalResult(
                    WorkerJournalResultCode.CAS_CONFLICT, operation=current
                )
            if occurred_at < current.updated_at:
                raise ValueError("occurred_at")
            command = self._command_in_connection(
                connection, current.command_id, validate_history=False
            )
            if command is None:
                return WorkerJournalResult(
                    WorkerJournalResultCode.CAS_CONFLICT, operation=current
                )
            queue_repository = SqliteWorkflowCommandRepository(self._database_path)
            events = queue_repository._history_in_connection(
                connection,
                command,
                _identity_text(("command_id", command.command_id),),
            )
            if occurred_at < command.updated_at:
                raise ValueError("occurred_at")
            claimed = tuple(
                event
                for event in events
                if event.event_kind is WorkflowCommandEventKind.CLAIMED
                and event.claim_count == current.claim_count
            )
            if (
                current.project_id != command.project_id
                or current.development_run_id != command.development_run_id
                or current.phase_id != command.phase_id
                or len(claimed) != 1
                or claimed[0].lease_owner != current.queue_owner_id
                or claimed[0].next_state_version
                != current.precondition_state_version
            ):
                _raise_payload_error(
                    ValueError("operation command history identity"),
                    "WorkerOperationReceipt",
                    identity,
                )
            if command.claim_count != current.claim_count:
                return WorkerJournalResult(
                    WorkerJournalResultCode.CAS_CONFLICT, operation=current
                )
            started_at = current.started_at
            if command.state is WorkflowCommandState.FAILED:
                latest = events[-1]
                started = tuple(
                    event
                    for event in events
                    if event.event_kind is WorkflowCommandEventKind.STARTED
                    and event.claim_count == current.claim_count
                )
                if (
                    latest.event_kind is not WorkflowCommandEventKind.FAILED
                    or latest.prior_state is not WorkflowCommandState.RUNNING
                    or latest.next_state is not WorkflowCommandState.FAILED
                    or latest.claim_count != current.claim_count
                    or latest.lease_owner != current.queue_owner_id
                    or len(started) != 1
                    or started[0].prior_state is not WorkflowCommandState.CLAIMED
                    or started[0].next_state is not WorkflowCommandState.RUNNING
                    or started[0].lease_owner != current.queue_owner_id
                    or command.started_at is None
                    or command.started_at != started[0].occurred_at
                ):
                    _raise_payload_error(
                        ValueError("authoritative STARTED provenance"),
                        "WorkflowCommandEvent",
                        identity,
                    )
                next_state = WorkerOperationState.FAILED
                failure_code = WorkerFailureCode.RECONCILIATION_REQUIRED.value
                reconciliation = WorkerReconciliationStatus.NOT_REQUIRED
                started_at = command.started_at
            elif command.state is WorkflowCommandState.CANCELLED:
                latest = events[-1]
                if (
                    latest.event_kind is not WorkflowCommandEventKind.CANCELLED
                    or latest.prior_state
                    not in {
                        WorkflowCommandState.CLAIMED,
                        WorkflowCommandState.RUNNING,
                    }
                    or latest.next_state is not WorkflowCommandState.CANCELLED
                    or latest.claim_count != current.claim_count
                    or latest.lease_owner != current.queue_owner_id
                ):
                    return WorkerJournalResult(
                        WorkerJournalResultCode.CAS_CONFLICT, operation=current
                    )
                next_state = WorkerOperationState.CANCELLED
                failure_code = WorkerFailureCode.CANCELLATION_OBSERVED.value
                reconciliation = WorkerReconciliationStatus.NOT_REQUIRED
            elif command.state is WorkflowCommandState.PENDING:
                latest = events[-1]
                renewals = tuple(
                    event
                    for event in events
                    if event.event_kind is WorkflowCommandEventKind.LEASE_RENEWED
                    and event.claim_count == current.claim_count
                    and event.next_state_version
                    > current.precondition_state_version
                    and event.next_state_version <= latest.prior_state_version
                )
                if (
                    latest.event_kind
                    is not WorkflowCommandEventKind.EXPIRED_CLAIM_RELEASED
                    or latest.prior_state is not WorkflowCommandState.CLAIMED
                    or latest.next_state is not WorkflowCommandState.PENDING
                    or latest.claim_count != current.claim_count
                    or latest.prior_state_version
                    != current.precondition_state_version + len(renewals)
                    or latest.lease_owner != current.queue_owner_id
                    or latest.lease_expires_at is None
                    or latest.occurred_at < latest.lease_expires_at
                    or any(
                        event.lease_owner != current.queue_owner_id
                        or event.prior_state is not WorkflowCommandState.CLAIMED
                        or event.next_state is not WorkflowCommandState.CLAIMED
                        or event.prior_state_version
                        != current.precondition_state_version + index
                        or event.next_state_version
                        != current.precondition_state_version + index + 1
                        for index, event in enumerate(renewals)
                    )
                    or command.lease_owner is not None
                    or command.lease_acquired_at is not None
                    or command.lease_expires_at is not None
                ):
                    return WorkerJournalResult(
                        WorkerJournalResultCode.CAS_CONFLICT, operation=current
                    )
                next_state = WorkerOperationState.CLAIM_RELEASED
                failure_code = WorkerFailureCode.LEASE_LOST.value
                reconciliation = WorkerReconciliationStatus.NOT_REQUIRED
            else:
                return WorkerJournalResult(
                    WorkerJournalResultCode.CAS_CONFLICT, operation=current
                )
            candidate = WorkerOperationReceipt(
                current.operation_sequence,
                current.operation_id,
                current.operation_kind,
                current.command_id,
                current.project_id,
                current.development_run_id,
                current.phase_id,
                current.session_id,
                current.worker_id,
                current.queue_owner_id,
                current.claim_count,
                current.precondition_state_version,
                next_state,
                current.state_version + 1,
                current.external_effect_class,
                reconciliation,
                failure_code,
                None,
                current.created_at,
                occurred_at,
                started_at,
                occurred_at,
            )
            cursor = connection.execute(
                "UPDATE worker_operations SET state=?,state_version=?,"
                "reconciliation_status=?,durable_failure_code=?,diagnostic_detail=NULL,"
                "updated_at=?,started_at=?,completed_at=? WHERE operation_id=? "
                "AND state='PREPARED' AND state_version=? "
                "AND durable_failure_code IS NULL AND diagnostic_detail IS NULL",
                (
                    candidate.state.value,
                    candidate.state_version,
                    candidate.reconciliation_status.value,
                    candidate.durable_failure_code,
                    candidate.updated_at,
                    candidate.started_at,
                    candidate.completed_at,
                    operation_value,
                    current.state_version,
                ),
            )
            if cursor.rowcount != 1:
                reread = self._load_operation(connection, operation_value, identity)
                assert reread is not None
                raise _CasRollback(
                    WorkerJournalResult(
                        WorkerJournalResultCode.CAS_CONFLICT, operation=reread
                    )
                )
            updated = self._load_operation(connection, operation_value, identity)
            assert updated is not None
            return WorkerJournalResult(
                WorkerJournalResultCode.APPLIED, operation=updated
            )

        return self._mutation("WorkerOperationReceipt", identity, operation)

    def list_nonterminal_operations(
        self, *, worker_id: str
    ) -> WorkerJournalResult:
        worker = _worker_id(worker_id)
        identity = _identity_text(("worker_id", worker),)
        def operation(connection: sqlite3.Connection) -> WorkerJournalResult:
            rows = connection.execute(
                f"SELECT {_OPERATION_COLUMNS} FROM worker_operations "
                "WHERE worker_id=? ORDER BY operation_sequence",
                (worker,),
            ).fetchall()
            try:
                decoded = tuple(_decode_worker_operation(row) for row in rows)
            except (TypeError, ValueError, KeyError, IndexError) as error:
                _raise_payload_error(error, "WorkerOperationReceipt", identity)
            operations = tuple(
                value
                for value in decoded
                if value.state not in _WORKER_TERMINAL_OPERATION_STATES
            )
            return WorkerJournalResult(
                WorkerJournalResultCode.LISTED, operations=operations
            )

        return self._read_result("WorkerOperationReceipt", identity, operation)

    def list_owned_commands(self, *, worker_id: str) -> WorkerJournalResult:
        worker = _worker_id(worker_id)
        identity = _identity_text(("worker_id", worker),)
        def operation(connection: sqlite3.Connection) -> WorkerJournalResult:
            session_rows = connection.execute(
                f"SELECT {_SESSION_COLUMNS} FROM worker_sessions "
                "WHERE worker_id=? ORDER BY session_sequence",
                (worker,),
            ).fetchall()
            try:
                sessions = tuple(
                    _decode_worker_session(row) for row in session_rows
                )
            except (TypeError, ValueError, KeyError, IndexError) as error:
                _raise_payload_error(error, "WorkerSession", identity)
            owners = tuple(dict.fromkeys(value.queue_owner_id for value in sessions))
            if not owners:
                return WorkerJournalResult(WorkerJournalResultCode.LISTED)
            placeholders = ",".join("?" for _value in owners)
            rows = connection.execute(
                f"SELECT {_COMMAND_COLUMNS} FROM workflow_commands "
                f"WHERE lease_owner IN ({placeholders}) ORDER BY queue_sequence",
                owners,
            ).fetchall()
            queue_repository = SqliteWorkflowCommandRepository(self._database_path)
            decoded_commands = tuple(
                queue_repository._command_from_row(
                    connection,
                    row,
                    _identity_text(("command_id", row["command_id"]),),
                )
                for row in rows
            )
            commands = tuple(
                value
                for value in decoded_commands
                if value.state in {
                    WorkflowCommandState.CLAIMED,
                    WorkflowCommandState.RUNNING,
                }
            )
            return WorkerJournalResult(
                WorkerJournalResultCode.LISTED, commands=commands
            )

        return self._read_result("WorkflowCommand", identity, operation)
