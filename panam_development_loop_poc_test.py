"""Focused deterministic tests for the DL-P1.1 and DL-P1.2 slices."""

import sqlite3
import tempfile
import unittest
from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone
from pathlib import Path

from panam_development_loop import (
    ContractValidationCode,
    ContractValidationError,
    ContractVersion,
    DevelopmentRunState,
    MilestoneContract,
    PhaseContract,
    SqliteRunStore,
    TransitionReasonCode,
    TransitionRequest,
    TransitionService,
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


if __name__ == "__main__":
    unittest.main()
