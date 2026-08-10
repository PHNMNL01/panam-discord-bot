"""Focused deterministic tests for the DL-P1.1 through DL-P1.5 slices."""

import sqlite3
import tempfile
import unittest
from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone
from pathlib import Path

from panam_development_loop import (
    ApprovalBinding,
    ApprovalKind,
    ApprovalTargetKind,
    ApprovalValidationCode,
    ApprovalValidationError,
    ApprovalVersion,
    DevelopmentRun,
    ContractValidationCode,
    ContractValidationError,
    ContractVersion,
    DevelopmentRunState,
    MilestoneContract,
    PhaseContract,
    RepositoryError,
    RepositoryFailureCode,
    SqliteApprovalBindingRepository,
    SqliteMilestoneContractRepository,
    SqlitePhaseContractRepository,
    SqliteRunStore,
    TransitionReasonCode,
    TransitionRequest,
    TransitionService,
)
from panam_development_loop.sqlite_migrations import (
    Migration,
    MigrationError,
    MigrationFailureCode,
    PRODUCTION_MIGRATIONS,
    apply_migrations,
    validate_migration_registry,
)


class FixedValues:
    def __init__(self) -> None:
        self._time = datetime(2026, 8, 6, tzinfo=timezone.utc)
        self._identifiers = iter(("run-1", "event-1", "event-2", "event-3"))

    def clock(self) -> datetime:
        value = self._time
        self._time += timedelta(seconds=1)
        return value

    def identifier(self) -> str:
        return next(self._identifiers)


class TransitionServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary_directory = tempfile.TemporaryDirectory()
        self.database_path = Path(self._temporary_directory.name) / "run.sqlite3"
        self.values = FixedValues()
        self.store = SqliteRunStore(self.database_path)
        self.service = TransitionService(
            self.store,
            clock=self.values.clock,
            id_factory=self.values.identifier,
        )
        self.service.initialize()
        self.run = self.service.create_run("contract-digest")

    def tearDown(self) -> None:
        self._temporary_directory.cleanup()

    def test_creation_and_reload(self) -> None:
        self.assertEqual("run-1", self.run.run_id)
        self.assertEqual(DevelopmentRunState.DRAFT, self.run.current_state)
        self.assertEqual(0, self.run.state_version)
        self.assertTrue(self.database_path.is_relative_to(Path(self._temporary_directory.name)))
        reloaded = SqliteRunStore(self.database_path).get_run(self.run.run_id)
        self.assertEqual(self.run, reloaded)

    def test_accepted_transitions_have_ordered_history(self) -> None:
        first = self.service.transition(
            TransitionRequest(
                self.run.run_id,
                DevelopmentRunState.DRAFT,
                0,
                DevelopmentRunState.FEASIBILITY_CHECKING,
            )
        )
        second = self.service.transition(
            TransitionRequest(
                self.run.run_id,
                DevelopmentRunState.FEASIBILITY_CHECKING,
                1,
                DevelopmentRunState.AWAITING_EXECUTION_APPROVAL,
            )
        )
        self.assertTrue(first.accepted)
        self.assertTrue(second.accepted)
        self.assertEqual(2, SqliteRunStore(self.database_path).get_run(self.run.run_id).state_version)
        history = SqliteRunStore(self.database_path).get_history(self.run.run_id)
        self.assertEqual([1, 2], [event.state_version for event in history])
        self.assertEqual(
            [
                DevelopmentRunState.FEASIBILITY_CHECKING,
                DevelopmentRunState.AWAITING_EXECUTION_APPROVAL,
            ],
            [event.to_state for event in history],
        )

    def test_rejected_paths_do_not_mutate_state_or_history(self) -> None:
        before = self.store.get_run(self.run.run_id)
        invalid = self.service.transition(
            TransitionRequest(
                self.run.run_id,
                DevelopmentRunState.DRAFT,
                0,
                DevelopmentRunState.SOURCE_COMPLETED,
            )
        )
        stale_state = self.service.transition(
            TransitionRequest(
                self.run.run_id,
                DevelopmentRunState.FEASIBILITY_CHECKING,
                0,
                DevelopmentRunState.AWAITING_EXECUTION_APPROVAL,
            )
        )
        stale_version = self.service.transition(
            TransitionRequest(
                self.run.run_id,
                DevelopmentRunState.DRAFT,
                1,
                DevelopmentRunState.FEASIBILITY_CHECKING,
            )
        )
        self.assertEqual(TransitionReasonCode.TRANSITION_NOT_ALLOWED, invalid.reason_code)
        self.assertEqual(DevelopmentRunState.DRAFT, invalid.current_state)
        self.assertEqual(TransitionReasonCode.STALE_EXPECTED_STATE, stale_state.reason_code)
        self.assertEqual(TransitionReasonCode.STALE_EXPECTED_STATE, stale_version.reason_code)
        self.assertEqual(before, self.store.get_run(self.run.run_id))
        self.assertEqual([], self.store.get_history(self.run.run_id))

    def test_unknown_run_does_not_create_records(self) -> None:
        result = self.service.transition(
            TransitionRequest(
                "unknown-run",
                DevelopmentRunState.DRAFT,
                0,
                DevelopmentRunState.FEASIBILITY_CHECKING,
            )
        )
        self.assertFalse(result.accepted)
        self.assertEqual(TransitionReasonCode.RUN_NOT_FOUND, result.reason_code)
        self.assertEqual("unknown-run", result.run_id)
        self.assertIsNone(result.current_state)
        self.assertIsNone(result.current_state_version)
        self.assertIsNone(self.store.get_run("unknown-run"))
        self.assertEqual([], self.store.get_history("unknown-run"))

    def test_unique_event_version_constraint(self) -> None:
        connection = sqlite3.connect(self.database_path)
        connection.execute("PRAGMA foreign_keys = ON")
        with self.assertRaises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO state_events(
                    event_id, run_id, from_state, to_state, transition_reason,
                    occurred_at, state_version
                ) VALUES(?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "duplicate-version",
                    self.run.run_id,
                    DevelopmentRunState.DRAFT.value,
                    DevelopmentRunState.FEASIBILITY_CHECKING.value,
                    "ACCEPTED",
                    "2026-08-06T00:00:00Z",
                    0,
                ),
            )
            connection.execute(
                """
                INSERT INTO state_events(
                    event_id, run_id, from_state, to_state, transition_reason,
                    occurred_at, state_version
                ) VALUES(?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "duplicate-version-two",
                    self.run.run_id,
                    DevelopmentRunState.DRAFT.value,
                    DevelopmentRunState.FEASIBILITY_CHECKING.value,
                    "ACCEPTED",
                    "2026-08-06T00:00:01Z",
                    0,
                ),
            )
        connection.close()


class ContractModelTest(unittest.TestCase):
    def _contract(self, **changes: object) -> MilestoneContract:
        values: dict[str, object] = {
            "project_id": "panam",
            "phase_id": "DL-P1",
            "milestone_id": "DL-P1.2",
            "contract_version": "1",
            "objective": "Implement domain contracts",
            "scope": ("canonical digest", "immutable models"),
            "exclusions": (),
            "acceptance_criteria": ("deterministic", "validated"),
            "allowed_paths": (
                "panam_development_loop/__init__.py",
                "panam_development_loop/models.py",
            ),
            "forbidden_paths": (".git",),
            "verification_plan": ("python -B panam_development_loop_poc_test.py", "git diff --check"),
            "stop_conditions": ("scope change", "test failure"),
        }
        values.update(changes)
        return MilestoneContract(**values)  # type: ignore[arg-type]

    def _assert_validation_code(
        self,
        code: ContractValidationCode,
        factory: object,
    ) -> None:
        with self.assertRaises(ContractValidationError) as raised:
            factory()  # type: ignore[operator]
        self.assertEqual(code, raised.exception.code)

    def test_valid_phase_contract_is_immutable_and_canonical(self) -> None:
        contract = PhaseContract("panam", "DL-P1", "1")
        self.assertEqual(ContractVersion.V1, contract.contract_version)
        self.assertEqual(
            '{"contract_version":"1","phase_id":"DL-P1","project_id":"panam"}',
            contract.canonical_json(),
        )
        self.assertEqual(contract.sha256_digest(), contract.sha256_digest())
        with self.assertRaises(FrozenInstanceError):
            contract.phase_id = "other"  # type: ignore[misc]

    def test_valid_milestone_contract_normalizes_unordered_values(self) -> None:
        contract = self._contract()
        self.assertEqual(("canonical digest", "immutable models"), contract.scope)
        self.assertEqual(("deterministic", "validated"), contract.acceptance_criteria)
        self.assertIsInstance(contract.allowed_paths, tuple)
        with self.assertRaises(FrozenInstanceError):
            contract.objective = "other"  # type: ignore[misc]

    def test_unsupported_version_and_invalid_identity_are_rejected(self) -> None:
        self._assert_validation_code(
            ContractValidationCode.UNSUPPORTED_VERSION,
            lambda: PhaseContract("panam", "DL-P1", "2"),
        )
        self._assert_validation_code(
            ContractValidationCode.INVALID_IDENTITY,
            lambda: PhaseContract("", "DL-P1", "1"),
        )
        self._assert_validation_code(
            ContractValidationCode.INVALID_IDENTITY,
            lambda: PhaseContract(" panam", "DL-P1", "1"),
        )

    def test_required_and_duplicate_values_are_rejected(self) -> None:
        self._assert_validation_code(
            ContractValidationCode.EMPTY_REQUIRED_VALUE,
            lambda: self._contract(objective=""),
        )
        self._assert_validation_code(
            ContractValidationCode.EMPTY_REQUIRED_VALUE,
            lambda: self._contract(scope=()),
        )
        self._assert_validation_code(
            ContractValidationCode.DUPLICATE_VALUE,
            lambda: self._contract(scope=("same", "same")),
        )

    def test_malformed_paths_and_path_collisions_are_rejected(self) -> None:
        for path in (
            "/absolute/path",
            "C:/drive/path",
            "\\\\server\\share",
            "package\\model.py",
            "package//model.py",
            "package/../model.py",
            "package/*.py",
            "package/",
        ):
            with self.subTest(path=path):
                self._assert_validation_code(
                    ContractValidationCode.INVALID_PATH,
                    lambda: self._contract(allowed_paths=(path,)),
                )
        self._assert_validation_code(
            ContractValidationCode.PATH_POLICY_COLLISION,
            lambda: self._contract(allowed_paths=("package",), forbidden_paths=("package/model.py",)),
        )

    def test_canonical_json_and_digest_are_deterministic(self) -> None:
        first = self._contract(
            scope=("immutable models", "canonical digest"),
            acceptance_criteria=("validated", "deterministic"),
            allowed_paths=(
                "panam_development_loop/models.py",
                "panam_development_loop/__init__.py",
            ),
            stop_conditions=("test failure", "scope change"),
        )
        second = self._contract()
        self.assertEqual(second.canonical_json(), first.canonical_json())
        self.assertEqual(second.sha256_digest(), first.sha256_digest())
        self.assertEqual(first.sha256_digest(), first.sha256_digest())

    def test_ordered_verification_plan_and_meaningful_changes_affect_digest(self) -> None:
        contract = self._contract()
        reordered_plan = self._contract(
            verification_plan=("git diff --check", "python -B panam_development_loop_poc_test.py"),
        )
        changed_objective = self._contract(objective="Implement validated domain contracts")
        self.assertNotEqual(contract.sha256_digest(), reordered_plan.sha256_digest())
        self.assertNotEqual(contract.sha256_digest(), changed_objective.sha256_digest())

    def test_contract_operations_create_no_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            before = set(Path(directory).iterdir())
            phase = PhaseContract("panam", "DL-P1", "1")
            milestone = self._contract()
            phase.canonical_json()
            phase.sha256_digest()
            milestone.canonical_json()
            milestone.sha256_digest()
            self.assertEqual(before, set(Path(directory).iterdir()))




class ApprovalBindingTest(unittest.TestCase):
    def _approval(self, **changes: object) -> ApprovalBinding:
        values: dict[str, object] = {
            "approval_id": "approval-1",
            "approval_version": "1",
            "approval_kind": "APPROVAL_1",
            "subject_id": "DL-P1.3",
            "subject_digest": "a" * 64,
            "target_kind": "SOURCE_REPOSITORY",
            "target_id": "Panam_APP",
            "target_branch": "phase/panam-dl-p1",
            "base_commit": "base-commit",
            "allowed_actions": ("verify", "implement"),
            "allowed_paths": (
                "panam_development_loop/models.py",
                "panam_development_loop/__init__.py",
            ),
            "approver_id": "human",
            "approved_at": "2026-08-07T12:00:00Z",
        }
        values.update(changes)
        return ApprovalBinding(**values)  # type: ignore[arg-type]

    def _assert_code(self, code: ApprovalValidationCode, factory: object) -> None:
        with self.assertRaises(ApprovalValidationError) as raised:
            factory()  # type: ignore[operator]
        self.assertEqual(code, raised.exception.code)

    def test_development_run_shape_remains_unchanged_and_immutable(self) -> None:
        run = DevelopmentRun(
            "run-1",
            "contract-digest",
            DevelopmentRunState.DRAFT,
            0,
            "created",
            "updated",
        )
        self.assertEqual(
            (
                "run_id",
                "milestone_contract_digest",
                "current_state",
                "state_version",
                "created_at",
                "updated_at",
            ),
            tuple(run.__dataclass_fields__),
        )
        with self.assertRaises(FrozenInstanceError):
            run.run_id = "other"  # type: ignore[misc]

    def test_valid_binding_is_immutable_canonical_and_normalized(self) -> None:
        approval = self._approval()
        reordered = self._approval(
            allowed_actions=("implement", "verify"),
            allowed_paths=(
                "panam_development_loop/__init__.py",
                "panam_development_loop/models.py",
            ),
        )
        self.assertEqual(ApprovalVersion.V1, approval.approval_version)
        self.assertEqual(ApprovalKind.APPROVAL_1, approval.approval_kind)
        self.assertEqual(ApprovalTargetKind.SOURCE_REPOSITORY, approval.target_kind)
        self.assertEqual(("implement", "verify"), approval.allowed_actions)
        self.assertEqual(
            (
                "panam_development_loop/__init__.py",
                "panam_development_loop/models.py",
            ),
            approval.allowed_paths,
        )
        self.assertEqual(approval.canonical_json(), reordered.canonical_json())
        self.assertEqual(approval.sha256_digest(), reordered.sha256_digest())
        self.assertNotIn(": ", approval.canonical_json())
        with self.assertRaises(FrozenInstanceError):
            approval.approver_id = "other"  # type: ignore[misc]

    def test_kinds_and_invalid_bindings_are_deterministic(self) -> None:
        self.assertEqual(
            ("PHASE_START_APPROVAL", "APPROVAL_1", "APPROVAL_2"),
            tuple(value.value for value in ApprovalKind),
        )
        self.assertEqual(
            ("SOURCE_REPOSITORY", "VAULT"),
            tuple(value.value for value in ApprovalTargetKind),
        )
        self.assertEqual((), self._approval(allowed_paths=()).allowed_paths)
        cases = (
            (ApprovalValidationCode.UNSUPPORTED_VERSION, {"approval_version": "2"}),
            (ApprovalValidationCode.INVALID_KIND, {"approval_kind": "OTHER"}),
            (ApprovalValidationCode.INVALID_KIND, {"target_kind": "OTHER"}),
            (ApprovalValidationCode.INVALID_IDENTITY, {"approval_id": ""}),
            (ApprovalValidationCode.INVALID_IDENTITY, {"approval_id": "approval\n1"}),
            (ApprovalValidationCode.EMPTY_REQUIRED_VALUE, {"target_branch": " "}),
            (ApprovalValidationCode.EMPTY_REQUIRED_VALUE, {"allowed_actions": ()}),
            (ApprovalValidationCode.EMPTY_REQUIRED_VALUE, {"allowed_actions": ["implement"]}),
            (ApprovalValidationCode.EMPTY_REQUIRED_VALUE, {"allowed_paths": ["models.py"]}),
            (ApprovalValidationCode.INVALID_DIGEST, {"subject_digest": "A" * 64}),
            (ApprovalValidationCode.DUPLICATE_VALUE, {"allowed_actions": ("implement", "implement")}),
            (ApprovalValidationCode.DUPLICATE_VALUE, {"allowed_paths": ("package/model.py", "package/model.py")}),
            (ApprovalValidationCode.INVALID_PATH, {"allowed_paths": ("package/../model.py",)}),
        )
        for code, changes in cases:
            with self.subTest(code=code, changes=changes):
                self._assert_code(code, lambda changes=changes: self._approval(**changes))

    def test_digest_changes_and_operations_have_no_authority_or_side_effect(self) -> None:
        approval = self._approval()
        self.assertEqual(approval.sha256_digest(), self._approval().sha256_digest())
        self.assertNotEqual(
            approval.sha256_digest(),
            self._approval(subject_digest="b" * 64).sha256_digest(),
        )
        self.assertFalse(hasattr(approval, "approve"))
        self.assertFalse(hasattr(approval, "execute"))
        with tempfile.TemporaryDirectory() as directory:
            before = set(Path(directory).iterdir())
            approval.canonical_json()
            approval.sha256_digest()
            self.assertEqual(before, set(Path(directory).iterdir()))

class SqliteMigrationTest(unittest.TestCase):
    """DL-P1.4 regression and legacy-version-1 compatibility coverage."""

    def setUp(self) -> None:
        self._temporary_directory = tempfile.TemporaryDirectory()
        self.directory = Path(self._temporary_directory.name)

    def tearDown(self) -> None:
        self._temporary_directory.cleanup()

    def _path(self, name: str = "migration.sqlite3") -> Path:
        return self.directory / name

    def _connection(self, path: Path) -> sqlite3.Connection:
        connection = sqlite3.connect(path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    @staticmethod
    def _schema_snapshot(connection: sqlite3.Connection) -> list[tuple[str, str, str]]:
        return [
            (row["type"], row["name"], row["sql"])
            for row in connection.execute(
                "SELECT type, name, sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name"
            )
        ]

    @staticmethod
    def _ledger_snapshot(connection: sqlite3.Connection) -> list[tuple[int, str]]:
        if connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'schema_migrations'"
        ).fetchone() is None:
            return []
        return [
            (row["version"], row["applied_at"])
            for row in connection.execute(
                "SELECT version, applied_at FROM schema_migrations ORDER BY version"
            )
        ]

    def _create_existing_version_one_database(self, path: Path) -> None:
        connection = self._connection(path)
        try:
            connection.execute(
                """
                CREATE TABLE schema_migrations (
                    version INTEGER PRIMARY KEY,
                    applied_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE development_runs (
                    run_id TEXT PRIMARY KEY,
                    milestone_contract_digest TEXT NOT NULL,
                    current_state TEXT NOT NULL,
                    state_version INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
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
                """
            )
            connection.execute("INSERT INTO schema_migrations VALUES(1, 'legacy-applied-at')")
            connection.execute(
                "INSERT INTO development_runs VALUES('legacy-run', 'legacy-digest', 'DRAFT', 1, 'created', 'updated')"
            )
            connection.execute(
                "INSERT INTO state_events VALUES('legacy-event', 'legacy-run', 'DRAFT', 'FEASIBILITY_CHECKING', 'ACCEPTED', 'event-at', 1)"
            )
            connection.commit()
        finally:
            connection.close()

    def _assert_history_rejected_without_mutation(
        self,
        connection: sqlite3.Connection,
        registry: tuple[Migration, ...] = PRODUCTION_MIGRATIONS,
    ) -> MigrationError:
        before_schema = self._schema_snapshot(connection)
        before_ledger = self._ledger_snapshot(connection)
        with self.assertRaises(MigrationError) as raised:
            apply_migrations(connection, "new-at", registry)
        self.assertEqual(before_schema, self._schema_snapshot(connection))
        self.assertEqual(before_ledger, self._ledger_snapshot(connection))
        return raised.exception

    def test_fresh_database_applies_current_production_registry_once(self) -> None:
        path = self._path()
        SqliteRunStore(path).initialize("first-at")
        connection = self._connection(path)
        try:
            self.assertEqual([(1, "first-at"), (2, "first-at")], self._ledger_snapshot(connection))
            self.assertEqual(
                [
                    "approvals",
                    "development_runs",
                    "milestone_contracts",
                    "phases",
                    "schema_migrations",
                    "state_events",
                ],
                [row["name"] for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
                )],
            )
        finally:
            connection.close()

    def test_current_initialization_is_idempotent_and_preserves_timestamp(self) -> None:
        path = self._path()
        store = SqliteRunStore(path)
        store.initialize("first-at")
        connection = self._connection(path)
        try:
            before_schema = self._schema_snapshot(connection)
            before_ledger = self._ledger_snapshot(connection)
        finally:
            connection.close()
        store.initialize("second-at")
        connection = self._connection(path)
        try:
            self.assertEqual(before_schema, self._schema_snapshot(connection))
            self.assertEqual(before_ledger, self._ledger_snapshot(connection))
        finally:
            connection.close()

    def test_independent_existing_version_one_data_is_upgraded_without_replay(self) -> None:
        path = self._path()
        self._create_existing_version_one_database(path)
        connection = self._connection(path)
        try:
            before_schema = {
                row[1]: row[2]
                for row in self._schema_snapshot(connection)
                if row[0] == "table"
            }
            before_runs = [tuple(row) for row in connection.execute("SELECT * FROM development_runs")]
            before_events = [tuple(row) for row in connection.execute("SELECT * FROM state_events ORDER BY state_version")]
        finally:
            connection.close()
        SqliteRunStore(path).initialize("new-at")
        connection = self._connection(path)
        try:
            after_schema = {
                row[1]: row[2]
                for row in self._schema_snapshot(connection)
                if row[0] == "table"
            }
            for table_name, table_sql in before_schema.items():
                self.assertEqual(table_sql, after_schema[table_name])
            self.assertEqual(
                [(1, "legacy-applied-at"), (2, "new-at")],
                self._ledger_snapshot(connection),
            )
            self.assertEqual(before_runs, [tuple(row) for row in connection.execute("SELECT * FROM development_runs")])
            self.assertEqual(before_events, [tuple(row) for row in connection.execute("SELECT * FROM state_events ORDER BY state_version")])
        finally:
            connection.close()

    def test_private_contiguous_registry_applies_in_order(self) -> None:
        registry = (
            Migration(1, ("CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)",)),
            Migration(2, ("CREATE TABLE synthetic_second (value TEXT NOT NULL)",)),
        )
        connection = self._connection(self._path())
        try:
            apply_migrations(connection, "synthetic-at", registry)
            self.assertEqual([(1, "synthetic-at"), (2, "synthetic-at")], self._ledger_snapshot(connection))
            self.assertIsNotNone(connection.execute("SELECT name FROM sqlite_master WHERE name = 'synthetic_second'").fetchone())
        finally:
            connection.close()

    def test_failed_synthetic_migration_rolls_back_only_its_effects(self) -> None:
        registry = (
            Migration(1, ("CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)",)),
            Migration(2, ("CREATE TABLE failed_effect (value TEXT NOT NULL)", "INSERT INTO absent_table VALUES(1)")),
        )
        connection = self._connection(self._path())
        try:
            with self.assertRaises(MigrationError) as raised:
                apply_migrations(connection, "synthetic-at", registry)
            self.assertEqual(MigrationFailureCode.MIGRATION_EXECUTION_FAILED, raised.exception.code)
            self.assertIsInstance(raised.exception.__cause__, sqlite3.Error)
            self.assertEqual([(1, "synthetic-at")], self._ledger_snapshot(connection))
            self.assertIsNone(connection.execute("SELECT name FROM sqlite_master WHERE name = 'failed_effect'").fetchone())
        finally:
            connection.close()

    def test_failed_ledger_insert_rolls_back_schema_effect(self) -> None:
        registry = (
            Migration(1, ("CREATE TABLE initial_effect (value TEXT NOT NULL)",)),
            Migration(2, ("CREATE TABLE rolled_back_effect (value TEXT NOT NULL)",)),
        )
        connection = self._connection(self._path())
        try:
            connection.execute("CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)")
            connection.execute("CREATE TABLE initial_effect (value TEXT NOT NULL)")
            connection.execute("INSERT INTO schema_migrations VALUES(1, 'first-at')")
            connection.execute(
                "CREATE TRIGGER reject_second_ledger_entry BEFORE INSERT ON schema_migrations WHEN NEW.version = 2 BEGIN SELECT RAISE(ABORT, 'reject version two'); END"
            )
            connection.commit()
            with self.assertRaises(MigrationError) as raised:
                apply_migrations(connection, "second-at", registry)
            self.assertEqual(MigrationFailureCode.MIGRATION_EXECUTION_FAILED, raised.exception.code)
            self.assertIsInstance(raised.exception.__cause__, sqlite3.IntegrityError)
            self.assertEqual([(1, "first-at")], self._ledger_snapshot(connection))
            self.assertIsNone(connection.execute("SELECT name FROM sqlite_master WHERE name = 'rolled_back_effect'").fetchone())
            self.assertIsNotNone(connection.execute("SELECT name FROM sqlite_master WHERE name = 'reject_second_ledger_entry'").fetchone())
        finally:
            connection.close()

    def test_malformed_registries_are_rejected_before_database_creation(self) -> None:
        cases = (
            (Migration(1, ()),),
            (Migration(1, ("SELECT 1",)), Migration(1, ("SELECT 2",))),
            (Migration(2, ("SELECT 2",)), Migration(1, ("SELECT 1",))),
            (Migration(0, ("SELECT 1",)),),
            (Migration(True, ("SELECT 1",)),),
            (Migration(1, ("SELECT 1",)), Migration(3, ("SELECT 3",))),
            (Migration(1, ("BEGIN IMMEDIATE",)),),
        )
        path = self._path("must-not-exist.sqlite3")
        for registry in cases:
            with self.subTest(registry=registry):
                with self.assertRaises(MigrationError) as raised:
                    validate_migration_registry(registry)
                self.assertEqual(MigrationFailureCode.INVALID_REGISTRY, raised.exception.code)
                self.assertFalse(path.exists())

    def test_nonempty_database_without_ledger_is_rejected(self) -> None:
        connection = self._connection(self._path())
        try:
            connection.execute("CREATE TABLE unrecorded (value TEXT NOT NULL)")
            connection.commit()
            self.assertEqual(MigrationFailureCode.INVALID_APPLIED_HISTORY, self._assert_history_rejected_without_mutation(connection).code)
        finally:
            connection.close()

    def test_empty_ledger_is_rejected_unchanged(self) -> None:
        connection = self._connection(self._path())
        try:
            connection.execute("CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)")
            connection.commit()
            self.assertEqual(MigrationFailureCode.INVALID_APPLIED_HISTORY, self._assert_history_rejected_without_mutation(connection).code)
        finally:
            connection.close()

    def test_malformed_and_nonprefix_history_is_rejected_unchanged(self) -> None:
        synthetic_registry = (
            Migration(1, ("CREATE TABLE first (value TEXT NOT NULL)",)),
            Migration(2, ("CREATE TABLE second (value TEXT NOT NULL)",)),
            Migration(3, ("CREATE TABLE third (value TEXT NOT NULL)",)),
        )
        connection = self._connection(self._path())
        try:
            connection.execute("CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)")
            connection.execute("INSERT INTO schema_migrations VALUES(1, 'first-at')")
            connection.execute("INSERT INTO schema_migrations VALUES(3, 'third-at')")
            connection.commit()
            self.assertEqual(MigrationFailureCode.INVALID_APPLIED_HISTORY, self._assert_history_rejected_without_mutation(connection, synthetic_registry).code)
        finally:
            connection.close()

    def test_malformed_ledger_and_unknown_future_history_are_rejected_unchanged(self) -> None:
        malformed = self._connection(self._path("malformed.sqlite3"))
        future = self._connection(self._path("future.sqlite3"))
        try:
            malformed.execute("CREATE TABLE schema_migrations (version TEXT PRIMARY KEY, applied_at TEXT NOT NULL)")
            malformed.execute("INSERT INTO schema_migrations VALUES('1', 'first-at')")
            malformed.commit()
            self.assertEqual(MigrationFailureCode.INVALID_APPLIED_HISTORY, self._assert_history_rejected_without_mutation(malformed).code)
            future.execute("CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)")
            future.execute("INSERT INTO schema_migrations VALUES(3, 'future-at')")
            future.commit()
            self.assertEqual(MigrationFailureCode.UNKNOWN_FUTURE_VERSION, self._assert_history_rejected_without_mutation(future).code)
        finally:
            malformed.close()
            future.close()

    def test_nonpositive_and_duplicate_history_are_rejected_unchanged(self) -> None:
        nonpositive = self._connection(self._path("nonpositive.sqlite3"))
        duplicate = self._connection(self._path("duplicate.sqlite3"))
        try:
            nonpositive.execute("CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)")
            nonpositive.execute("INSERT INTO schema_migrations VALUES(0, 'zero-at')")
            nonpositive.commit()
            self.assertEqual(MigrationFailureCode.INVALID_APPLIED_HISTORY, self._assert_history_rejected_without_mutation(nonpositive).code)
            duplicate.execute("CREATE TABLE schema_migrations (version INTEGER NOT NULL, applied_at TEXT NOT NULL)")
            duplicate.execute("INSERT INTO schema_migrations VALUES(1, 'first-at')")
            duplicate.execute("INSERT INTO schema_migrations VALUES(1, 'duplicate-at')")
            duplicate.commit()
            self.assertEqual(MigrationFailureCode.INVALID_APPLIED_HISTORY, self._assert_history_rejected_without_mutation(duplicate).code)
        finally:
            nonpositive.close()
            duplicate.close()


class SqliteRepositoryTest(unittest.TestCase):
    """Focused DL-P1.5 repository behavior and failure-boundary coverage."""

    def setUp(self) -> None:
        self._temporary_directory = tempfile.TemporaryDirectory()
        self.directory = Path(self._temporary_directory.name)
        self.database_path = self.directory / "repositories.sqlite3"
        SqliteRunStore(self.database_path).initialize("initialized-at")

    def tearDown(self) -> None:
        self._temporary_directory.cleanup()

    @staticmethod
    def _phase(phase_id: str = "DL-P1") -> PhaseContract:
        return PhaseContract("panam", phase_id, "1")

    @staticmethod
    def _milestone(milestone_id: str = "DL-P1.5", phase_id: str = "DL-P1") -> MilestoneContract:
        return MilestoneContract(
            project_id="panam",
            phase_id=phase_id,
            milestone_id=milestone_id,
            contract_version="1",
            objective="Persist café contracts ✓",
            scope=("SQLite repositories", "canonical JSON"),
            exclusions=(),
            acceptance_criteria=("durable", "deterministic"),
            allowed_paths=("panam_development_loop/repositories.py",),
            forbidden_paths=("runtime",),
            verification_plan=("focused tests", "full regression"),
            stop_conditions=("scope change", "test failure"),
        )

    @staticmethod
    def _approval(
        approval_id: str = "approval-1",
        approval_kind: str = "APPROVAL_1",
        target_kind: str = "SOURCE_REPOSITORY",
    ) -> ApprovalBinding:
        return ApprovalBinding(
            approval_id=approval_id,
            approval_version="1",
            approval_kind=approval_kind,
            subject_id="DL-P1.5",
            subject_digest="a" * 64,
            target_kind=target_kind,
            target_id="Panam_APP",
            target_branch="phase/panam-dl-p1",
            base_commit="b6b18a26",
            allowed_actions=("verify", "implement"),
            allowed_paths=(
                "panam_development_loop/sqlite_repositories.py",
                "panam_development_loop/repositories.py",
            ),
            approver_id="human-č",
            approved_at="2026-08-10T10:00:00Z",
        )

    def _assert_code(self, code: RepositoryFailureCode, operation: object) -> RepositoryError:
        with self.assertRaises(RepositoryError) as raised:
            operation()  # type: ignore[operator]
        self.assertEqual(code, raised.exception.code)
        return raised.exception

    def test_phase_create_reopen_missing_duplicate_and_immutable_api(self) -> None:
        contract = self._phase()
        repository = SqlitePhaseContractRepository(self.database_path)
        self.assertIs(contract, repository.create(contract))
        reopened = SqlitePhaseContractRepository(self.database_path)
        self.assertEqual(contract, reopened.get("panam", "DL-P1"))
        self.assertEqual(contract.sha256_digest(), reopened.get("panam", "DL-P1").sha256_digest())  # type: ignore[union-attr]
        self.assertIsNone(reopened.get("panam", "missing"))
        error = self._assert_code(
            RepositoryFailureCode.DUPLICATE_ENTITY,
            lambda: reopened.create(contract),
        )
        self.assertIsInstance(error.__cause__, sqlite3.IntegrityError)
        for method_name in ("update", "upsert", "replace", "delete", "list"):
            self.assertFalse(hasattr(repository, method_name))

    def test_milestone_round_trip_preserves_unicode_tuples_order_and_relationship(self) -> None:
        SqlitePhaseContractRepository(self.database_path).create(self._phase())
        contract = self._milestone()
        repository = SqliteMilestoneContractRepository(self.database_path)
        self.assertIs(contract, repository.create(contract))
        reopened = SqliteMilestoneContractRepository(self.database_path)
        loaded = reopened.get("panam", "DL-P1", "DL-P1.5")
        self.assertEqual(contract, loaded)
        self.assertEqual(("focused tests", "full regression"), loaded.verification_plan)  # type: ignore[union-attr]
        self.assertEqual(contract.sha256_digest(), loaded.sha256_digest())  # type: ignore[union-attr]
        self.assertIsNone(reopened.get("panam", "DL-P1", "missing"))
        self._assert_code(
            RepositoryFailureCode.DUPLICATE_ENTITY,
            lambda: reopened.create(contract),
        )
        connection = sqlite3.connect(self.database_path)
        try:
            row = connection.execute(
                "SELECT objective, scope_json, exclusions_json, verification_plan_json FROM milestone_contracts"
            ).fetchone()
            self.assertEqual("Persist café contracts ✓", row[0])
            self.assertEqual('["SQLite repositories","canonical JSON"]', row[1])
            self.assertEqual("[]", row[2])
            self.assertEqual('["focused tests","full regression"]', row[3])
        finally:
            connection.close()

    def test_milestone_requires_composite_phase_parent_and_persists_nothing_on_failure(self) -> None:
        repository = SqliteMilestoneContractRepository(self.database_path)
        error = self._assert_code(
            RepositoryFailureCode.RELATIONSHIP_VIOLATION,
            lambda: repository.create(self._milestone()),
        )
        self.assertIsInstance(error.__cause__, sqlite3.IntegrityError)
        connection = sqlite3.connect(self.database_path)
        try:
            self.assertEqual(0, connection.execute("SELECT COUNT(*) FROM milestone_contracts").fetchone()[0])
        finally:
            connection.close()

    def test_approval_round_trip_preserves_all_current_kinds_and_structured_values(self) -> None:
        fixtures = (
            self._approval("approval-phase", "PHASE_START_APPROVAL"),
            self._approval("approval-one", "APPROVAL_1"),
            self._approval("approval-two", "APPROVAL_2", "VAULT"),
        )
        repository = SqliteApprovalBindingRepository(self.database_path)
        for binding in fixtures:
            self.assertIs(binding, repository.create(binding))
        reopened = SqliteApprovalBindingRepository(self.database_path)
        for binding in fixtures:
            with self.subTest(binding=binding.approval_id):
                loaded = reopened.get(binding.approval_id)
                self.assertEqual(binding, loaded)
                self.assertEqual(binding.sha256_digest(), loaded.sha256_digest())  # type: ignore[union-attr]
        self.assertIsNone(reopened.get("missing"))
        self._assert_code(
            RepositoryFailureCode.DUPLICATE_ENTITY,
            lambda: reopened.create(fixtures[0]),
        )
        connection = sqlite3.connect(self.database_path)
        try:
            row = connection.execute(
                "SELECT allowed_actions_json, allowed_paths_json, approver_id FROM approvals WHERE approval_id = 'approval-one'"
            ).fetchone()
            self.assertEqual('["implement","verify"]', row[0])
            self.assertEqual(
                '["panam_development_loop/repositories.py","panam_development_loop/sqlite_repositories.py"]',
                row[1],
            )
            self.assertEqual("human-č", row[2])
        finally:
            connection.close()

    def test_invalid_lookup_identity_fails_before_sqlite_access(self) -> None:
        missing_path = self.directory / "must-not-exist.sqlite3"
        repositories_and_calls = (
            (SqlitePhaseContractRepository(missing_path), lambda repo: repo.get("", "DL-P1")),
            (SqliteMilestoneContractRepository(missing_path), lambda repo: repo.get("panam", " padded", "DL-P1.5")),
            (SqliteApprovalBindingRepository(missing_path), lambda repo: repo.get("approval\n1")),
            (SqliteApprovalBindingRepository(missing_path), lambda repo: repo.get(1)),
        )
        for repository, operation in repositories_and_calls:
            with self.subTest(repository=type(repository).__name__):
                self._assert_code(
                    RepositoryFailureCode.INVALID_IDENTITY,
                    lambda repository=repository, operation=operation: operation(repository),
                )
                self.assertFalse(missing_path.exists())

    def test_digest_uniqueness_conflict_is_duplicate_and_preserves_snapshot(self) -> None:
        contract = self._phase("DL-P2")
        connection = sqlite3.connect(self.database_path)
        try:
            connection.execute(
                "INSERT INTO phases VALUES(?, ?, ?, ?)",
                ("panam", "other-phase", "1", contract.sha256_digest()),
            )
            connection.commit()
            before = connection.execute("SELECT * FROM phases ORDER BY project_id, phase_id").fetchall()
        finally:
            connection.close()
        self._assert_code(
            RepositoryFailureCode.DUPLICATE_ENTITY,
            lambda: SqlitePhaseContractRepository(self.database_path).create(contract),
        )
        connection = sqlite3.connect(self.database_path)
        try:
            self.assertEqual(before, connection.execute("SELECT * FROM phases ORDER BY project_id, phase_id").fetchall())
        finally:
            connection.close()

    def test_corrupt_payloads_fail_closed_with_bounded_categories(self) -> None:
        phase_repository = SqlitePhaseContractRepository(self.database_path)
        milestone_repository = SqliteMilestoneContractRepository(self.database_path)
        approval_repository = SqliteApprovalBindingRepository(self.database_path)
        phase_repository.create(self._phase())
        milestone_repository.create(self._milestone())
        milestone_repository.create(self._milestone("DL-P1.5-shape"))
        milestone_repository.create(self._milestone("DL-P1.5-scalar"))
        approval_repository.create(self._approval("approval-invalid-kind"))
        approval_repository.create(self._approval("approval-unsupported"))
        connection = sqlite3.connect(self.database_path)
        try:
            connection.execute("UPDATE phases SET contract_version = '2'")
            connection.execute(
                "UPDATE milestone_contracts SET scope_json = '{bad json' WHERE milestone_id = 'DL-P1.5'"
            )
            connection.execute(
                "UPDATE milestone_contracts SET scope_json = '{}' WHERE milestone_id = 'DL-P1.5-shape'"
            )
            connection.execute(
                "UPDATE milestone_contracts SET objective = '' WHERE milestone_id = 'DL-P1.5-scalar'"
            )
            connection.execute(
                "UPDATE approvals SET approval_kind = 'UNKNOWN' WHERE approval_id = 'approval-invalid-kind'"
            )
            connection.execute(
                "UPDATE approvals SET approval_version = '2' WHERE approval_id = 'approval-unsupported'"
            )
            connection.commit()
        finally:
            connection.close()
        self._assert_code(
            RepositoryFailureCode.UNSUPPORTED_PERSISTED_VERSION,
            lambda: phase_repository.get("panam", "DL-P1"),
        )
        self._assert_code(
            RepositoryFailureCode.MALFORMED_PERSISTED_PAYLOAD,
            lambda: milestone_repository.get("panam", "DL-P1", "DL-P1.5"),
        )
        for milestone_id in ("DL-P1.5-shape", "DL-P1.5-scalar"):
            self._assert_code(
                RepositoryFailureCode.MALFORMED_PERSISTED_PAYLOAD,
                lambda milestone_id=milestone_id: milestone_repository.get(
                    "panam", "DL-P1", milestone_id
                ),
            )
        self._assert_code(
            RepositoryFailureCode.MALFORMED_PERSISTED_PAYLOAD,
            lambda: approval_repository.get("approval-invalid-kind"),
        )
        self._assert_code(
            RepositoryFailureCode.UNSUPPORTED_PERSISTED_VERSION,
            lambda: approval_repository.get("approval-unsupported"),
        )

    def test_digest_mismatch_fails_closed(self) -> None:
        repository = SqlitePhaseContractRepository(self.database_path)
        repository.create(self._phase())
        connection = sqlite3.connect(self.database_path)
        try:
            connection.execute("UPDATE phases SET contract_digest = ?", ("f" * 64,))
            connection.commit()
        finally:
            connection.close()
        self._assert_code(
            RepositoryFailureCode.MALFORMED_PERSISTED_PAYLOAD,
            lambda: repository.get("panam", "DL-P1"),
        )

    def test_missing_stale_future_and_incomplete_schema_are_not_repaired(self) -> None:
        missing = self.directory / "missing.sqlite3"
        self._assert_code(
            RepositoryFailureCode.SCHEMA_MISMATCH,
            lambda: SqlitePhaseContractRepository(missing).get("panam", "DL-P1"),
        )
        self.assertFalse(missing.exists())

        stale = self.directory / "stale.sqlite3"
        connection = sqlite3.connect(stale)
        try:
            connection.execute("CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)")
            connection.execute("INSERT INTO schema_migrations VALUES(1, 'old')")
            connection.commit()
        finally:
            connection.close()
        self._assert_code(
            RepositoryFailureCode.SCHEMA_MISMATCH,
            lambda: SqliteApprovalBindingRepository(stale).get("approval"),
        )

        future = self.directory / "future.sqlite3"
        SqliteRunStore(future).initialize("at")
        connection = sqlite3.connect(future)
        try:
            connection.execute("INSERT INTO schema_migrations VALUES(3, 'future')")
            connection.commit()
        finally:
            connection.close()
        self._assert_code(
            RepositoryFailureCode.SCHEMA_MISMATCH,
            lambda: SqlitePhaseContractRepository(future).get("panam", "DL-P1"),
        )

        malformed = self.directory / "malformed.sqlite3"
        SqliteRunStore(malformed).initialize("at")
        connection = sqlite3.connect(malformed)
        try:
            connection.execute("DELETE FROM schema_migrations WHERE version = 1")
            connection.commit()
        finally:
            connection.close()
        self._assert_code(
            RepositoryFailureCode.SCHEMA_MISMATCH,
            lambda: SqlitePhaseContractRepository(malformed).get("panam", "DL-P1"),
        )

        incomplete = self.directory / "incomplete.sqlite3"
        SqliteRunStore(incomplete).initialize("at")
        connection = sqlite3.connect(incomplete)
        try:
            connection.execute("DROP TABLE approvals")
            connection.commit()
        finally:
            connection.close()
        self._assert_code(
            RepositoryFailureCode.SCHEMA_MISMATCH,
            lambda: SqliteApprovalBindingRepository(incomplete).get("approval"),
        )

    def test_busy_write_fails_immediately_rolls_back_and_closes_for_all_adapters(self) -> None:
        phase_repository = SqlitePhaseContractRepository(self.database_path)
        milestone_repository = SqliteMilestoneContractRepository(self.database_path)
        approval_repository = SqliteApprovalBindingRepository(self.database_path)
        phase_repository.create(self._phase())
        blocker = sqlite3.connect(self.database_path)
        try:
            blocker.execute("BEGIN IMMEDIATE")
            operations = (
                lambda: phase_repository.create(self._phase("DL-P2")),
                lambda: milestone_repository.create(self._milestone()),
                lambda: approval_repository.create(self._approval()),
            )
            for operation in operations:
                self._assert_code(RepositoryFailureCode.SQLITE_OPERATIONAL_FAILURE, operation)
        finally:
            blocker.rollback()
            blocker.close()
        phase_repository.create(self._phase("DL-P2"))
        milestone_repository.create(self._milestone())
        approval_repository.create(self._approval())

    def test_independent_databases_do_not_share_entities(self) -> None:
        second_path = self.directory / "second.sqlite3"
        SqliteRunStore(second_path).initialize("at")
        SqlitePhaseContractRepository(self.database_path).create(self._phase())
        self.assertIsNone(SqlitePhaseContractRepository(second_path).get("panam", "DL-P1"))


class SqliteMigrationTwoTest(unittest.TestCase):
    """Focused DL-P1.5 production migration and validator coverage."""

    def setUp(self) -> None:
        self._temporary_directory = tempfile.TemporaryDirectory()
        self.directory = Path(self._temporary_directory.name)

    def tearDown(self) -> None:
        self._temporary_directory.cleanup()

    def _connection(self, name: str) -> sqlite3.Connection:
        connection = sqlite3.connect(self.directory / name)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    @staticmethod
    def _install_independent_v1(connection: sqlite3.Connection) -> None:
        connection.execute("CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)")
        connection.execute(
            "CREATE TABLE development_runs (run_id TEXT PRIMARY KEY, milestone_contract_digest TEXT NOT NULL, current_state TEXT NOT NULL, state_version INTEGER NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"
        )
        connection.execute(
            "CREATE TABLE state_events (event_id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES development_runs(run_id), from_state TEXT NOT NULL, to_state TEXT NOT NULL, transition_reason TEXT NOT NULL, occurred_at TEXT NOT NULL, state_version INTEGER NOT NULL, UNIQUE(run_id, state_version))"
        )
        connection.execute("INSERT INTO schema_migrations VALUES(1, 'legacy-at')")
        connection.execute("INSERT INTO development_runs VALUES('run', 'digest', 'DRAFT', 1, 'created', 'updated')")
        connection.execute("INSERT INTO state_events VALUES('event', 'run', 'DRAFT', 'FEASIBILITY_CHECKING', 'ACCEPTED', 'event-at', 1)")
        connection.commit()

    def test_exact_production_registry_and_identifier_tokens_are_accepted(self) -> None:
        validated = validate_migration_registry(
            PRODUCTION_MIGRATIONS,
            require_production_version=True,
        )
        self.assertEqual([1, 2], [migration.version for migration in validated])
        self.assertIn("base_commit", PRODUCTION_MIGRATIONS[1].statements[2])
        for identifier in ("base_commit", "commit_hash", "rollback_reason"):
            with self.subTest(identifier=identifier):
                result = validate_migration_registry(
                    (Migration(1, (f"CREATE TABLE token_test ({identifier} TEXT NOT NULL)",)),)
                )
                self.assertEqual(1, result[0].version)

    def test_actual_forbidden_operations_remain_rejected_case_insensitively(self) -> None:
        statements = (
            "  begin   immediate  ",
            "CoMmIt",
            "\trollback \n",
            "savePOINT nested",
            " ReLeAsE nested ",
            "vacuum",
            " AtTaCh database 'x' AS y",
            "detach database y",
        )
        for statement in statements:
            with self.subTest(statement=statement):
                with self.assertRaises(MigrationError) as raised:
                    validate_migration_registry((Migration(1, (statement,)),))
                self.assertEqual(MigrationFailureCode.INVALID_REGISTRY, raised.exception.code)

    def test_migration_two_schema_and_composite_foreign_key_are_exactly_enforced(self) -> None:
        path = self.directory / "schema.sqlite3"
        SqliteRunStore(path).initialize("at")
        connection = sqlite3.connect(path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            tables = [
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
                )
            ]
            self.assertEqual(
                ["approvals", "development_runs", "milestone_contracts", "phases", "schema_migrations", "state_events"],
                tables,
            )
            foreign_keys = connection.execute("PRAGMA foreign_key_list(milestone_contracts)").fetchall()
            self.assertEqual(2, len(foreign_keys))
            self.assertEqual({("project_id", "project_id"), ("phase_id", "phase_id")}, {(row[3], row[4]) for row in foreign_keys})
            self.assertEqual({"RESTRICT"}, {row[5] for row in foreign_keys})
            self.assertEqual({"RESTRICT"}, {row[6] for row in foreign_keys})
        finally:
            connection.close()

    def test_migration_two_statement_failure_rolls_back_schema_and_preserves_v1(self) -> None:
        connection = self._connection("statement-failure.sqlite3")
        try:
            self._install_independent_v1(connection)
            failing_registry = (
                PRODUCTION_MIGRATIONS[0],
                Migration(2, (PRODUCTION_MIGRATIONS[1].statements[0], "INSERT INTO absent_table VALUES(1)")),
            )
            with self.assertRaises(MigrationError) as raised:
                apply_migrations(connection, "new-at", failing_registry)
            self.assertEqual(MigrationFailureCode.MIGRATION_EXECUTION_FAILED, raised.exception.code)
            self.assertEqual([(1, "legacy-at")], [tuple(row) for row in connection.execute("SELECT * FROM schema_migrations")])
            self.assertIsNone(connection.execute("SELECT name FROM sqlite_master WHERE name = 'phases'").fetchone())
            self.assertEqual(1, connection.execute("SELECT COUNT(*) FROM development_runs").fetchone()[0])
            self.assertEqual(1, connection.execute("SELECT COUNT(*) FROM state_events").fetchone()[0])
        finally:
            connection.close()

    def test_rejected_version_two_ledger_insert_rolls_back_all_production_tables(self) -> None:
        connection = self._connection("ledger-failure.sqlite3")
        try:
            self._install_independent_v1(connection)
            connection.execute(
                "CREATE TRIGGER reject_v2 BEFORE INSERT ON schema_migrations WHEN NEW.version = 2 BEGIN SELECT RAISE(ABORT, 'reject'); END"
            )
            connection.commit()
            with self.assertRaises(MigrationError) as raised:
                apply_migrations(connection, "new-at", PRODUCTION_MIGRATIONS)
            self.assertEqual(MigrationFailureCode.MIGRATION_EXECUTION_FAILED, raised.exception.code)
            self.assertEqual([(1, "legacy-at")], [tuple(row) for row in connection.execute("SELECT * FROM schema_migrations")])
            for table_name in ("phases", "milestone_contracts", "approvals"):
                self.assertIsNone(
                    connection.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?",
                        (table_name,),
                    ).fetchone()
                )
            self.assertEqual(("run", "digest", "DRAFT", 1, "created", "updated"), tuple(connection.execute("SELECT * FROM development_runs").fetchone()))
        finally:
            connection.close()


if __name__ == "__main__":
    unittest.main()
