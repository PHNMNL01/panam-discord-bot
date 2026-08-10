"""SQLite repository implementations for the DL-P1.5 foundation entities."""

import json
import sqlite3
from pathlib import Path
from typing import NoReturn

from .models import (
    ApprovalBinding,
    ApprovalValidationCode,
    ApprovalValidationError,
    ContractValidationCode,
    ContractValidationError,
    MilestoneContract,
    PhaseContract,
)
from .repositories import (
    RepositoryError,
    RepositoryFailureCode,
    _validate_query_identity,
)


_REQUIRED_TABLES = frozenset(
    {
        "schema_migrations",
        "development_runs",
        "state_events",
        "phases",
        "milestone_contracts",
        "approvals",
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
        if not _REQUIRED_TABLES.issubset(tables):
            raise RepositoryError(RepositoryFailureCode.SCHEMA_MISMATCH, entity_name, identity)
        versions = [
            row["version"]
            for row in connection.execute(
                "SELECT version FROM schema_migrations ORDER BY version ASC"
            )
        ]
    except RepositoryError:
        raise
    except sqlite3.Error as error:
        _raise_repository_error(
            RepositoryFailureCode.SCHEMA_MISMATCH,
            entity_name,
            identity,
            error,
        )
    if versions != [1, 2]:
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
    connection = _open_connection(database_path, entity_name, identity)
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
