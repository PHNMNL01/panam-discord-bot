"""Focused deterministic tests for the DL-P1.1 durable-state POC."""

import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from panam_development_loop import (
    DevelopmentRunState,
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


if __name__ == "__main__":
    unittest.main()
