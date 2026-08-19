"""Focused deterministic tests for the DL-P1.1 through DL-P1.8 slices."""

import os
import re
import sqlite3
import subprocess
import tempfile
import threading
import urllib.request
import unittest
from dataclasses import FrozenInstanceError, fields, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from panam_development_loop import (
    ApprovalBinding,
    ApprovalKind,
    ApprovalSnapshot,
    ApprovalSnapshotStatus,
    ApprovalTargetKind,
    ApprovalValidationCode,
    ApprovalValidationError,
    ApprovalVersion,
    DevelopmentRun,
    ContractValidationCode,
    ContractValidationError,
    ContractVersion,
    DevelopmentRunState,
    EscalationTrigger,
    EvidenceKind,
    EvidenceSnapshot,
    EvidenceVerdict,
    ExpectedEvidenceBinding,
    MilestoneContract,
    PhaseContract,
    ProjectPolicy,
    ProjectPolicyReadOutcome,
    ProjectPolicyReadResult,
    ProjectPolicyReader,
    ProjectPolicyVersion,
    RepositoryError,
    RepositoryEvidenceBinding,
    RepositoryFailureCode,
    SqliteApprovalBindingRepository,
    SqliteMilestoneContractRepository,
    SqlitePhaseContractRepository,
    SqliteProjectPolicyRepository,
    SqliteRunStore,
    SnapshotProducerKind,
    TransactionalTransitionReasonCode,
    TransactionalTransitionResult,
    TransitionEvaluationDecision,
    TransitionEvaluationReasonCode,
    TransitionEvaluationRequest,
    TransitionEvaluationResult,
    TransitionPolicy,
    TransitionReasonCode,
    TransitionRequirement,
    TransitionRequest,
    TransitionService,
    TransitionRule,
    TransitionRuleId,
    TransitionRuleValidationCode,
    TransitionRuleValidationError,
    WorkflowEdgeType,
    WorkflowNodeType,
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


class RaisingEquality:
    def __init__(self) -> None:
        self.invoked = False

    def __eq__(self, other: object) -> bool:
        self.invoked = True
        raise RuntimeError("snapshot-version-equality-invoked")


class ParsingOnlyDatetime:
    def __new__(cls, *args: object, **kwargs: object) -> datetime:
        return datetime(*args, **kwargs)  # type: ignore[arg-type]

    @classmethod
    def now(cls, *args: object, **kwargs: object) -> datetime:
        raise AssertionError("wall-clock lookup invoked")

    @classmethod
    def utcnow(cls) -> datetime:
        raise AssertionError("wall-clock lookup invoked")

    @classmethod
    def today(cls) -> datetime:
        raise AssertionError("wall-clock lookup invoked")


def _service_contract() -> MilestoneContract:
    return MilestoneContract(
        project_id="panam",
        phase_id="DL-P1",
        milestone_id="DL-P1.7",
        contract_version="1",
        objective="Persist transitions and state events transactionally",
        scope=("transactional transition service",),
        exclusions=("external effects",),
        acceptance_criteria=("atomic state and event",),
        allowed_paths=("panam_development_loop/transition_service.py",),
        forbidden_paths=("panam_development_loop/transition_policy.py",),
        verification_plan=("focused tests", "full regression"),
        stop_conditions=("scope change",),
    )


def _unconditional_transition_request(
    run: DevelopmentRun,
    contract: MilestoneContract,
    **changes: object,
) -> TransitionEvaluationRequest:
    values: dict[str, object] = {
        "evaluation_version": "1",
        "run": run,
        "rule_id": TransitionRuleId.P1_6_DRAFT_TO_FEASIBILITY_CHECKING_V1.value,
        "requested_state": DevelopmentRunState.FEASIBILITY_CHECKING,
        "edge_type": WorkflowEdgeType.UNCONDITIONAL,
        "milestone_contract": contract,
    }
    values.update(changes)
    return TransitionEvaluationRequest(**values)  # type: ignore[arg-type]


def _evidence_transition_request(
    run: DevelopmentRun,
    contract: MilestoneContract,
) -> TransitionEvaluationRequest:
    repository = RepositoryEvidenceBinding(
        repository_id="Panam_APP",
        branch="phase/panam-dl-p1",
        head_commit="a" * 40,
        worktree_digest="b" * 64,
    )
    expected = ExpectedEvidenceBinding(
        assessment_authority_id="HAD-DL-P1.7-FEASIBILITY-001",
        artifact_path=r"C:\Panam_Runtime\development-runs\dl-p1-7\feasibility\FEASIBILITY-ASSESSMENT.md",
        artifact_byte_count=20933,
        artifact_sha256="c" * 64,
    )
    evidence = EvidenceSnapshot(
        snapshot_version="1",
        producer_kind=SnapshotProducerKind.FEASIBILITY_ASSESSOR,
        run_id=run.run_id,
        milestone_id=contract.milestone_id,
        milestone_contract_digest=contract.sha256_digest(),
        source_state=run.current_state,
        state_version=run.state_version,
        rule_id=(
            TransitionRuleId.P1_6_FEASIBILITY_CHECKING_TO_AWAITING_EXECUTION_APPROVAL_V1
        ),
        evidence_kind=EvidenceKind.FEASIBILITY_ASSESSMENT,
        assessment_authority_id=expected.assessment_authority_id,
        artifact_path=expected.artifact_path,
        artifact_byte_count=expected.artifact_byte_count,
        artifact_sha256=expected.artifact_sha256,
        verdict=EvidenceVerdict.FEASIBLE,
        ready_for_approval_1=True,
        assessed_at="2026-08-17T12:00:00Z",
        repository_binding=repository,
    )
    return TransitionEvaluationRequest(
        evaluation_version="1",
        run=run,
        rule_id=(
            TransitionRuleId.P1_6_FEASIBILITY_CHECKING_TO_AWAITING_EXECUTION_APPROVAL_V1.value
        ),
        requested_state=DevelopmentRunState.AWAITING_EXECUTION_APPROVAL,
        edge_type=WorkflowEdgeType.EVIDENCE_GATED,
        milestone_contract=contract,
        expected_evidence=expected,
        repository_binding=repository,
        evidence_snapshots=(evidence,),
    )


class _ZeroRowCount:
    rowcount = 0


class _ConnectionProxy:
    def __init__(
        self,
        connection: sqlite3.Connection,
        *,
        fail_statement: str | None = None,
        zero_update: bool = False,
        fail_commit: bool = False,
        fail_rollback: bool = False,
    ) -> None:
        self.connection = connection
        self.fail_statement = fail_statement
        self.zero_update = zero_update
        self.fail_commit = fail_commit
        self.fail_rollback = fail_rollback
        self.rollback_calls = 0
        self.commit_calls = 0
        self.close_calls = 0

    @property
    def in_transaction(self) -> bool:
        return self.connection.in_transaction

    def execute(
        self,
        statement: str,
        parameters: tuple[object, ...] = (),
    ) -> object:
        normalized = " ".join(statement.upper().split())
        if self.fail_statement is not None and self.fail_statement in normalized:
            raise sqlite3.OperationalError(f"injected {self.fail_statement}")
        if self.zero_update and normalized.startswith("UPDATE DEVELOPMENT_RUNS"):
            return _ZeroRowCount()
        return self.connection.execute(statement, parameters)

    def commit(self) -> None:
        self.commit_calls += 1
        if self.fail_commit:
            raise sqlite3.OperationalError("injected commit failure")
        self.connection.commit()

    def rollback(self) -> None:
        self.rollback_calls += 1
        if self.fail_rollback:
            raise sqlite3.OperationalError("injected rollback failure")
        self.connection.rollback()

    def close(self) -> None:
        self.close_calls += 1
        self.connection.close()


class _BarrierPolicy(TransitionPolicy):
    def __init__(self, barrier: threading.Barrier) -> None:
        super().__init__()
        self._barrier = barrier

    def evaluate(self, candidate: object) -> TransitionEvaluationResult:
        result = super().evaluate(candidate)
        if result.decision is TransitionEvaluationDecision.ALLOWED:
            self._barrier.wait(timeout=5)
        return result


class TransitionServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary_directory = tempfile.TemporaryDirectory()
        self.database_path = Path(self._temporary_directory.name) / "run.sqlite3"
        self.values = FixedValues()
        self.contract = _service_contract()
        self.store = SqliteRunStore(self.database_path)
        self.service = TransitionService(
            self.store,
            clock=self.values.clock,
            id_factory=self.values.identifier,
        )
        self.service.initialize()
        self.run = self.service.create_run(self.contract.sha256_digest())

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
            _unconditional_transition_request(self.run, self.contract)
        )
        self.assertTrue(first.committed)
        self.assertEqual(TransactionalTransitionReasonCode.COMMITTED, first.reason_code)
        self.assertIsNotNone(first.persisted_run)
        second = self.service.transition(
            _evidence_transition_request(first.persisted_run, self.contract)  # type: ignore[arg-type]
        )
        self.assertTrue(second.committed)
        self.assertEqual(2, SqliteRunStore(self.database_path).get_run(self.run.run_id).state_version)
        history = SqliteRunStore(self.database_path).get_history(self.run.run_id)
        self.assertEqual([1, 2], [event.state_version for event in history])
        self.assertEqual(
            [TransitionEvaluationReasonCode.RULE_ALLOWED.value] * 2,
            [event.transition_reason for event in history],
        )
        self.assertEqual(
            [
                DevelopmentRunState.FEASIBILITY_CHECKING,
                DevelopmentRunState.AWAITING_EXECUTION_APPROVAL,
            ],
            [event.to_state for event in history],
        )

    def test_non_allowed_decisions_do_not_open_database_or_mutate(self) -> None:
        before = self.store.get_run(self.run.run_id)
        allowed = _unconditional_transition_request(self.run, self.contract)
        candidates = (
            (
                TransitionRequest(
                    self.run.run_id,
                    DevelopmentRunState.DRAFT,
                    0,
                    DevelopmentRunState.FEASIBILITY_CHECKING,
                ),
                TransitionEvaluationDecision.INVALID,
            ),
            (replace(allowed, evaluation_version="2"), TransitionEvaluationDecision.UNSUPPORTED),
            (replace(allowed, milestone_contract=None), TransitionEvaluationDecision.GATED),
            (
                TransitionEvaluationRequest(
                    evaluation_version="1",
                    run=self.run,
                    rule_id=TransitionRuleId.P1_6_DRAFT_TO_FEASIBILITY_CHECKING_V1.value,
                    requested_state=DevelopmentRunState.SOURCE_COMPLETED,
                    edge_type=WorkflowEdgeType.UNCONDITIONAL,
                ),
                TransitionEvaluationDecision.DENIED,
            ),
        )
        with patch.object(
            self.store,
            "_connect",
            side_effect=AssertionError("database opened for non-ALLOWED result"),
        ):
            for candidate, decision in candidates:
                with self.subTest(decision=decision):
                    result = self.service.transition(candidate)
                    self.assertFalse(result.committed)
                    self.assertEqual(
                        TransactionalTransitionReasonCode.EVALUATION_NOT_ALLOWED,
                        result.reason_code,
                    )
                    self.assertEqual(decision, result.evaluation_result.decision)
        self.assertEqual(before, self.store.get_run(self.run.run_id))
        self.assertEqual([], self.store.get_history(self.run.run_id))

    def test_unknown_run_does_not_create_records(self) -> None:
        unknown = replace(self.run, run_id="unknown-run")
        result = self.service.transition(
            _unconditional_transition_request(unknown, self.contract)
        )
        self.assertFalse(result.committed)
        self.assertEqual(TransactionalTransitionReasonCode.RUN_NOT_FOUND, result.reason_code)
        self.assertEqual(TransitionEvaluationDecision.ALLOWED, result.evaluation_result.decision)
        self.assertIsNone(result.persisted_run)
        self.assertIsNone(result.accepted_event)
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


class TransactionalTransitionServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary_directory = tempfile.TemporaryDirectory()
        self.database_path = Path(self._temporary_directory.name) / "transaction.sqlite3"
        self.values = FixedValues()
        self.contract = _service_contract()
        self.store = SqliteRunStore(self.database_path)
        self.service = TransitionService(
            self.store,
            clock=self.values.clock,
            id_factory=self.values.identifier,
        )
        self.service.initialize()
        self.run = self.service.create_run(self.contract.sha256_digest())

    def tearDown(self) -> None:
        self._temporary_directory.cleanup()

    def _request(self) -> TransitionEvaluationRequest:
        return _unconditional_transition_request(self.run, self.contract)

    def _proxy(
        self,
        *,
        fail_statement: str | None = None,
        zero_update: bool = False,
        fail_commit: bool = False,
        fail_rollback: bool = False,
    ) -> _ConnectionProxy:
        return _ConnectionProxy(
            self.store._connect(),
            fail_statement=fail_statement,
            zero_update=zero_update,
            fail_commit=fail_commit,
            fail_rollback=fail_rollback,
        )

    def _assert_unchanged(self) -> None:
        self.assertEqual(self.run, self.store.get_run(self.run.run_id))
        self.assertEqual([], self.store.get_history(self.run.run_id))

    def test_result_contract_is_minimal_immutable_and_bound(self) -> None:
        result = self.service.transition(self._request())
        self.assertEqual(
            [
                "committed",
                "reason_code",
                "evaluation_result",
                "persisted_run",
                "accepted_event",
            ],
            [item.name for item in fields(TransactionalTransitionResult)],
        )
        self.assertTrue(result.committed)
        self.assertFalse(result.evaluation_result.side_effects_performed)
        with self.assertRaises(FrozenInstanceError):
            result.committed = False  # type: ignore[misc]
        with self.assertRaises(ValueError):
            TransactionalTransitionResult(
                committed=False,
                reason_code=TransactionalTransitionReasonCode.EVALUATION_NOT_ALLOWED,
                evaluation_result=result.evaluation_result,
                persisted_run=None,
                accepted_event=None,
            )

    def test_exact_persisted_run_mismatch_is_stale_and_non_mutating(self) -> None:
        request = self._request()
        connection = sqlite3.connect(self.database_path)
        connection.execute(
            "UPDATE development_runs SET updated_at = ? WHERE run_id = ?",
            ("2026-08-17T13:00:00Z", self.run.run_id),
        )
        connection.commit()
        connection.close()
        current = self.store.get_run(self.run.run_id)

        result = self.service.transition(request)

        self.assertFalse(result.committed)
        self.assertEqual(
            TransactionalTransitionReasonCode.STALE_PERSISTED_RUN,
            result.reason_code,
        )
        self.assertEqual(current, result.persisted_run)
        self.assertEqual(current, self.store.get_run(self.run.run_id))
        self.assertEqual([], self.store.get_history(self.run.run_id))

    def _assert_persisted_mismatch_is_stale(
        self,
        statement: str,
        parameters: tuple[object, ...],
    ) -> None:
        request = self._request()
        connection = sqlite3.connect(self.database_path)
        connection.execute(statement, parameters)
        connection.commit()
        connection.close()
        current = self.store.get_run(self.run.run_id)

        result = self.service.transition(request)

        self.assertFalse(result.committed)
        self.assertEqual(
            TransactionalTransitionReasonCode.STALE_PERSISTED_RUN,
            result.reason_code,
        )
        self.assertEqual(current, result.persisted_run)
        self.assertEqual(current, self.store.get_run(self.run.run_id))
        self.assertEqual([], self.store.get_history(self.run.run_id))

    def test_persisted_state_mismatch_is_stale_and_non_mutating(self) -> None:
        self._assert_persisted_mismatch_is_stale(
            "UPDATE development_runs SET current_state = ? WHERE run_id = ?",
            (DevelopmentRunState.FEASIBILITY_CHECKING.value, self.run.run_id),
        )

    def test_persisted_state_version_mismatch_is_stale_and_non_mutating(self) -> None:
        self._assert_persisted_mismatch_is_stale(
            "UPDATE development_runs SET state_version = ? WHERE run_id = ?",
            (7, self.run.run_id),
        )

    def test_persisted_contract_digest_mismatch_is_stale_and_non_mutating(self) -> None:
        self._assert_persisted_mismatch_is_stale(
            "UPDATE development_runs SET milestone_contract_digest = ? WHERE run_id = ?",
            ("f" * 64, self.run.run_id),
        )

    def test_compare_and_swap_zero_rows_rolls_back_without_event(self) -> None:
        proxy = self._proxy(zero_update=True)
        with patch.object(self.store, "_connect", return_value=proxy):
            result = self.service.transition(self._request())
        self.assertEqual(
            TransactionalTransitionReasonCode.STALE_PERSISTED_RUN,
            result.reason_code,
        )
        self.assertEqual(1, proxy.rollback_calls)
        self.assertEqual(1, proxy.close_calls)
        self._assert_unchanged()

    def test_state_update_failure_rolls_back_and_inserts_no_event(self) -> None:
        proxy = self._proxy(fail_statement="UPDATE DEVELOPMENT_RUNS")
        with patch.object(self.store, "_connect", return_value=proxy):
            with self.assertRaisesRegex(sqlite3.OperationalError, "injected UPDATE"):
                self.service.transition(self._request())
        self.assertEqual(1, proxy.rollback_calls)
        self.assertEqual(1, proxy.close_calls)
        self._assert_unchanged()

    def test_event_insert_failure_rolls_back_state_mutation(self) -> None:
        proxy = self._proxy(fail_statement="INSERT INTO STATE_EVENTS")
        with patch.object(self.store, "_connect", return_value=proxy):
            with self.assertRaisesRegex(sqlite3.OperationalError, "injected INSERT"):
                self.service.transition(self._request())
        self.assertEqual(1, proxy.rollback_calls)
        self.assertEqual(1, proxy.close_calls)
        self._assert_unchanged()

    def test_exception_after_begin_rolls_back_and_closes(self) -> None:
        proxy = self._proxy(fail_statement="SELECT RUN_ID")
        with patch.object(self.store, "_connect", return_value=proxy):
            with self.assertRaisesRegex(sqlite3.OperationalError, "injected SELECT"):
                self.service.transition(self._request())
        self.assertEqual(1, proxy.rollback_calls)
        self.assertEqual(1, proxy.close_calls)
        self._assert_unchanged()

    def test_commit_failure_never_reports_success_and_rolls_back(self) -> None:
        proxy = self._proxy(fail_commit=True)
        with patch.object(self.store, "_connect", return_value=proxy):
            with self.assertRaisesRegex(sqlite3.OperationalError, "commit failure"):
                self.service.transition(self._request())
        self.assertEqual(1, proxy.commit_calls)
        self.assertEqual(1, proxy.rollback_calls)
        self.assertEqual(1, proxy.close_calls)
        self._assert_unchanged()

    def test_event_id_collision_rolls_back_state_mutation(self) -> None:
        connection = sqlite3.connect(self.database_path)
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(
            """
            INSERT INTO state_events(
                event_id, run_id, from_state, to_state, transition_reason,
                occurred_at, state_version
            ) VALUES(?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "event-1",
                self.run.run_id,
                DevelopmentRunState.DRAFT.value,
                DevelopmentRunState.FEASIBILITY_CHECKING.value,
                TransitionEvaluationReasonCode.RULE_ALLOWED.value,
                "2026-08-17T11:00:00Z",
                99,
            ),
        )
        connection.commit()
        connection.close()

        with self.assertRaises(sqlite3.IntegrityError):
            self.service.transition(self._request())

        self.assertEqual(self.run, self.store.get_run(self.run.run_id))
        history = self.store.get_history(self.run.run_id)
        self.assertEqual([99], [event.state_version for event in history])

    def test_event_version_collision_rolls_back_state_mutation(self) -> None:
        connection = sqlite3.connect(self.database_path)
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(
            """
            INSERT INTO state_events(
                event_id, run_id, from_state, to_state, transition_reason,
                occurred_at, state_version
            ) VALUES(?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "occupied-version",
                self.run.run_id,
                DevelopmentRunState.DRAFT.value,
                DevelopmentRunState.FEASIBILITY_CHECKING.value,
                TransitionEvaluationReasonCode.RULE_ALLOWED.value,
                "2026-08-17T11:00:00Z",
                1,
            ),
        )
        connection.commit()
        connection.close()

        with self.assertRaises(sqlite3.IntegrityError):
            self.service.transition(self._request())

        self.assertEqual(self.run, self.store.get_run(self.run.run_id))
        history = self.store.get_history(self.run.run_id)
        self.assertEqual([1], [event.state_version for event in history])

    def test_clock_and_identifier_providers_run_before_begin(self) -> None:
        directory = tempfile.TemporaryDirectory()
        try:
            store = SqliteRunStore(Path(directory.name) / "guarded.sqlite3")
            holder: dict[str, _ConnectionProxy] = {}
            identifiers = iter(("guarded-run", "guarded-event"))
            current_time = datetime(2026, 8, 17, tzinfo=timezone.utc)

            def assert_outside_transaction() -> None:
                proxy = holder.get("proxy")
                if proxy is not None:
                    self.assertFalse(proxy.in_transaction)

            def identifier() -> str:
                assert_outside_transaction()
                return next(identifiers)

            def clock() -> datetime:
                assert_outside_transaction()
                return current_time

            service = TransitionService(store, clock=clock, id_factory=identifier)
            service.initialize()
            run = service.create_run(self.contract.sha256_digest())
            original_connect = store._connect

            def guarded_connect() -> _ConnectionProxy:
                proxy = _ConnectionProxy(original_connect())
                holder["proxy"] = proxy
                return proxy

            with patch.object(store, "_connect", side_effect=guarded_connect):
                result = service.transition(
                    _unconditional_transition_request(run, self.contract)
                )
            self.assertTrue(result.committed)
        finally:
            directory.cleanup()

    def test_clock_failure_precedes_connection_and_preserves_state(self) -> None:
        def failing_clock() -> datetime:
            raise RuntimeError("injected clock failure")

        service = TransitionService(
            self.store,
            clock=failing_clock,
            id_factory=lambda: "event-before-clock-failure",
        )
        with patch.object(
            self.store,
            "_connect",
            side_effect=AssertionError("connection opened after clock failure"),
        ) as connect:
            with self.assertRaisesRegex(RuntimeError, "injected clock failure"):
                service.transition(self._request())
        connect.assert_not_called()
        self._assert_unchanged()

    def test_event_id_failure_precedes_clock_connection_and_state_mutation(self) -> None:
        def failing_identifier() -> str:
            raise RuntimeError("injected identifier failure")

        def unexpected_clock() -> datetime:
            raise AssertionError("clock called after identifier failure")

        service = TransitionService(
            self.store,
            clock=unexpected_clock,
            id_factory=failing_identifier,
        )
        with patch.object(
            self.store,
            "_connect",
            side_effect=AssertionError("connection opened after identifier failure"),
        ) as connect:
            with self.assertRaisesRegex(RuntimeError, "injected identifier failure"):
                service.transition(self._request())
        connect.assert_not_called()
        self._assert_unchanged()

    def test_transition_avoids_forbidden_effect_repository_and_run_creation_paths(self) -> None:
        proxy = self._proxy()
        forbidden = AssertionError("forbidden transition path invoked")
        with (
            patch("builtins.open", side_effect=forbidden),
            patch.object(subprocess, "run", side_effect=forbidden),
            patch.object(os, "system", side_effect=forbidden),
            patch.object(urllib.request, "urlopen", side_effect=forbidden),
            patch.object(Path, "write_text", side_effect=forbidden),
            patch.object(threading.Thread, "start", side_effect=forbidden),
            patch.object(SqlitePhaseContractRepository, "create", side_effect=forbidden),
            patch.object(
                SqliteMilestoneContractRepository,
                "create",
                side_effect=forbidden,
            ),
            patch.object(
                SqliteApprovalBindingRepository,
                "create",
                side_effect=forbidden,
            ),
            patch.object(SqliteRunStore, "create_run", side_effect=forbidden),
            patch.object(self.store, "_connect", return_value=proxy) as connect,
        ):
            result = self.service.transition(self._request())

        self.assertTrue(result.committed)
        connect.assert_called_once_with()
        self.assertEqual(1, proxy.commit_calls)
        self.assertEqual(0, proxy.rollback_calls)
        self.assertEqual(1, proxy.close_calls)
        persisted = self.store.get_run(self.run.run_id)
        self.assertEqual(1, persisted.state_version)  # type: ignore[union-attr]
        self.assertEqual(1, len(self.store.get_history(self.run.run_id)))

    def test_rollback_failure_preserves_original_cause_closes_and_never_commits(self) -> None:
        proxy = self._proxy(
            fail_statement="SELECT RUN_ID",
            fail_rollback=True,
        )
        with patch.object(self.store, "_connect", return_value=proxy) as connect:
            with self.assertRaisesRegex(
                sqlite3.OperationalError,
                "injected rollback failure",
            ) as raised:
                self.service.transition(self._request())

        connect.assert_called_once_with()
        self.assertIsInstance(raised.exception.__cause__, sqlite3.OperationalError)
        self.assertIn("injected SELECT RUN_ID", str(raised.exception.__cause__))
        self.assertEqual(1, proxy.rollback_calls)
        self.assertEqual(0, proxy.commit_calls)
        self.assertEqual(1, proxy.close_calls)
        self._assert_unchanged()

    def test_event_constraint_failure_rolls_back_closes_and_never_retries(self) -> None:
        connection = sqlite3.connect(self.database_path)
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(
            """
            INSERT INTO state_events(
                event_id, run_id, from_state, to_state, transition_reason,
                occurred_at, state_version
            ) VALUES(?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "event-1",
                self.run.run_id,
                DevelopmentRunState.DRAFT.value,
                DevelopmentRunState.FEASIBILITY_CHECKING.value,
                TransitionEvaluationReasonCode.RULE_ALLOWED.value,
                "2026-08-17T11:00:00Z",
                99,
            ),
        )
        connection.commit()
        connection.close()
        proxy = self._proxy()

        with patch.object(self.store, "_connect", return_value=proxy) as connect:
            with self.assertRaises(sqlite3.IntegrityError):
                self.service.transition(self._request())

        connect.assert_called_once_with()
        self.assertEqual(1, proxy.rollback_calls)
        self.assertEqual(0, proxy.commit_calls)
        self.assertEqual(1, proxy.close_calls)
        self.assertEqual(self.run, self.store.get_run(self.run.run_id))
        history = self.store.get_history(self.run.run_id)
        self.assertEqual([99], [event.state_version for event in history])

    def test_competing_writers_commit_at_most_one_transition(self) -> None:
        barrier = threading.Barrier(2)
        service = TransitionService(self.store, policy=_BarrierPolicy(barrier))
        request = self._request()
        results: list[TransactionalTransitionResult] = []
        errors: list[BaseException] = []

        def execute() -> None:
            try:
                results.append(service.transition(request))
            except BaseException as error:
                errors.append(error)

        workers = [threading.Thread(target=execute) for _ in range(2)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(timeout=10)

        self.assertFalse(any(worker.is_alive() for worker in workers))
        self.assertEqual([], errors)
        self.assertEqual(2, len(results))
        self.assertEqual(
            {
                TransactionalTransitionReasonCode.COMMITTED,
                TransactionalTransitionReasonCode.STALE_PERSISTED_RUN,
            },
            {result.reason_code for result in results},
        )
        persisted = self.store.get_run(self.run.run_id)
        self.assertEqual(1, persisted.state_version)  # type: ignore[union-attr]
        self.assertEqual(1, len(self.store.get_history(self.run.run_id)))


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
            self.assertEqual([(1, "first-at"), (2, "first-at"), (3, "first-at")], self._ledger_snapshot(connection))
            self.assertEqual(
                [
                    "approvals",
                    "development_runs",
                    "milestone_contracts",
                    "phases",
                    "project_policies",
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
                [(1, "legacy-applied-at"), (2, "new-at"), (3, "new-at")],
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
            future.execute("INSERT INTO schema_migrations VALUES(4, 'future-at')")
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
            connection.execute("INSERT INTO schema_migrations VALUES(4, 'future')")
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
        self.assertEqual([1, 2, 3], [migration.version for migration in validated])
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
                ["approvals", "development_runs", "milestone_contracts", "phases", "project_policies", "schema_migrations", "state_events"],
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


class ProjectPolicyReadOnlyFoundationTest(unittest.TestCase):
    """Focused DL-P1.8 model, migration, adapter, and reader coverage."""

    def setUp(self) -> None:
        self._temporary_directory = tempfile.TemporaryDirectory()
        self.directory = Path(self._temporary_directory.name)
        self.database_path = self.directory / "project-registry.sqlite3"
        SqliteRunStore(self.database_path).initialize("initialized-at")

    def tearDown(self) -> None:
        self._temporary_directory.cleanup()

    @staticmethod
    def _policy(project_id: str = "panam", root: str = "C:\\workspace\\panam") -> ProjectPolicy:
        return ProjectPolicy(project_id, "1", root)

    @staticmethod
    def _snapshot(path: Path) -> tuple[str, ...]:
        connection = sqlite3.connect(path)
        try:
            return tuple(connection.iterdump())
        finally:
            connection.close()

    @staticmethod
    def _insert(path: Path, project_id: str, version: str, root: str) -> None:
        connection = sqlite3.connect(path)
        try:
            connection.execute(
                "INSERT INTO project_policies(project_id, policy_version, project_root) VALUES(?, ?, ?)",
                (project_id, version, root),
            )
            connection.commit()
        finally:
            connection.close()

    def test_policy_v1_is_closed_immutable_and_lexically_normalized(self) -> None:
        policy = ProjectPolicy("panam", ProjectPolicyVersion.V1, "C:/workspace/panam")
        self.assertEqual(
            ["project_id", "policy_version", "project_root"],
            [field_.name for field_ in fields(ProjectPolicy)],
        )
        self.assertEqual("panam", policy.project_id)
        self.assertIs(ProjectPolicyVersion.V1, policy.policy_version)
        self.assertEqual("C:\\workspace\\panam", policy.project_root)
        self.assertEqual("C:\\", ProjectPolicy("panam", "1", "C:\\").project_root)
        self.assertEqual("C:\\Projects", ProjectPolicy("panam", "1", "C:\\Projects\\").project_root)
        self.assertEqual("C:\\Projects", ProjectPolicy("panam", "1", "C:/Projects/").project_root)
        self.assertEqual(
            "D:\\Projects\\nested",
            ProjectPolicy("panam", "1", "D:\\Projects\\nested\\").project_root,
        )
        with self.assertRaises(FrozenInstanceError):
            policy.project_root = "D:\\other"  # type: ignore[misc]

    def test_policy_validation_rejects_invalid_identity_version_and_root(self) -> None:
        for identity in ("", " panam", "panam ", "panam\n", 7, None):
            with self.subTest(identity=identity), self.assertRaises(ContractValidationError):
                ProjectPolicy(identity, "1", "C:\\repo")  # type: ignore[arg-type]
        for version in ("", "2", 1, None):
            with self.subTest(version=version), self.assertRaises(ContractValidationError):
                ProjectPolicy("panam", version, "C:\\repo")  # type: ignore[arg-type]
        invalid_roots = (
            "",
            " repo",
            "relative\\repo",
            "C:repo",
            "É:\\repo",
            "\\\\server\\share",
            "//server/share",
            "\\\\?\\C:\\repo",
            "\\\\.\\C:\\repo",
            "C:\\repo\\\\child",
            "C:\\repo\\.\\child",
            "C:\\repo\\..\\child",
            "C:\\repo?",
            "C:\\repo.\\child",
            "C:\\repo \\child",
            "C:\\CON",
            "C:\\con",
            "C:\\NUL.txt",
            "C:\\Projects\\AUX\\data",
            "C:\\Projects\\COM1.log",
            "C:\\LPT9\\output",
            "C:\\PrN.doc",
            "C:\\CONIN$",
            "C:\\conin$",
            "C:\\CONIN$.txt",
            "C:\\CONOUT$",
            "C:\\ConOut$.txt",
            "C:\\CONOUT$.log",
            "C:\\Projects\\CONOUT$\\data",
            "C:\\COM\u00b9",
            "C:\\COM\u00b2.txt",
            "C:\\Projects\\COM\u00b3.log",
            "C:\\LPT\u00b9",
            "C:\\Projects\\LPT\u00b2.txt",
            "C:\\LPT\u00b3\\output",
            "C:\\Projects\\lpt\u00b2.log\\data",
        )
        for root in invalid_roots:
            with self.subTest(root=root), self.assertRaises(ContractValidationError):
                ProjectPolicy("panam", "1", root)

    def test_policy_validation_has_no_filesystem_or_external_effects(self) -> None:
        with (
            patch.object(Path, "exists", side_effect=AssertionError("exists invoked")) as exists,
            patch.object(Path, "stat", side_effect=AssertionError("stat invoked")) as stat,
            patch.object(Path, "resolve", side_effect=AssertionError("resolve invoked")) as resolve,
            patch("os.path.realpath", side_effect=AssertionError("realpath invoked")) as realpath,
            patch("subprocess.run", side_effect=AssertionError("subprocess invoked")) as subprocess_run,
            patch("urllib.request.urlopen", side_effect=AssertionError("network invoked")) as urlopen,
        ):
            self.assertEqual("C:\\repo", self._policy(root="C:/repo/").project_root)
            for root in ("C:\\CONIN$", "C:\\COM\u00b9"):
                with self.subTest(root=root), self.assertRaises(ContractValidationError):
                    self._policy(root=root)
        for sentinel in (exists, stat, resolve, realpath, subprocess_run, urlopen):
            sentinel.assert_not_called()

    def test_result_contract_has_exactly_four_outcomes_and_payload_invariant(self) -> None:
        self.assertEqual(
            {"VALID_POLICY", "PROJECT_NOT_REGISTERED", "INVALID_POLICY", "STORAGE_FAILURE"},
            {outcome.value for outcome in ProjectPolicyReadOutcome},
        )
        policy = self._policy()
        self.assertEqual(
            policy,
            ProjectPolicyReadResult(ProjectPolicyReadOutcome.VALID_POLICY, policy).policy,
        )
        for outcome in (
            ProjectPolicyReadOutcome.PROJECT_NOT_REGISTERED,
            ProjectPolicyReadOutcome.INVALID_POLICY,
            ProjectPolicyReadOutcome.STORAGE_FAILURE,
        ):
            self.assertIsNone(ProjectPolicyReadResult(outcome).policy)
            with self.assertRaises(ValueError):
                ProjectPolicyReadResult(outcome, policy)
        with self.assertRaises(ValueError):
            ProjectPolicyReadResult(ProjectPolicyReadOutcome.VALID_POLICY)

    def test_migration_three_schema_is_exact_empty_unique_and_nonnullable(self) -> None:
        connection = sqlite3.connect(self.database_path)
        try:
            self.assertEqual(
                [(1, "initialized-at"), (2, "initialized-at"), (3, "initialized-at")],
                connection.execute(
                    "SELECT version, applied_at FROM schema_migrations ORDER BY version"
                ).fetchall(),
            )
            self.assertEqual(
                [
                    ("project_id", "TEXT", 1, 1),
                    ("policy_version", "TEXT", 1, 0),
                    ("project_root", "TEXT", 1, 0),
                ],
                [(row[1], row[2], row[3], row[5]) for row in connection.execute("PRAGMA table_info(project_policies)")],
            )
            self.assertEqual(0, connection.execute("SELECT COUNT(*) FROM project_policies").fetchone()[0])
            connection.execute("INSERT INTO project_policies VALUES('panam', '1', 'C:\\repo')")
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute("INSERT INTO project_policies VALUES('panam', '1', 'D:\\repo')")
            for values in ((None, "1", "C:\\repo"), ("x", None, "C:\\repo"), ("y", "1", None)):
                with self.subTest(values=values), self.assertRaises(sqlite3.IntegrityError):
                    connection.execute("INSERT INTO project_policies VALUES(?, ?, ?)", values)
        finally:
            connection.close()

    def test_migration_three_statement_and_ledger_failures_roll_back(self) -> None:
        create_statement = PRODUCTION_MIGRATIONS[2].statements[0]
        statement_path = self.directory / "migration-three-statement.sqlite3"
        statement_connection = sqlite3.connect(statement_path)
        statement_connection.row_factory = sqlite3.Row
        try:
            apply_migrations(statement_connection, "v2-at", PRODUCTION_MIGRATIONS[:2])
            failing_registry = PRODUCTION_MIGRATIONS[:2] + (
                Migration(3, (create_statement, "INSERT INTO absent_table VALUES(1)")),
            )
            with self.assertRaises(MigrationError):
                apply_migrations(statement_connection, "v3-at", failing_registry)
            self.assertEqual(
                [(1,), (2,)],
                [tuple(row) for row in statement_connection.execute("SELECT version FROM schema_migrations ORDER BY version")],
            )
            self.assertIsNone(
                statement_connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='project_policies'"
                ).fetchone()
            )
        finally:
            statement_connection.close()

        ledger_path = self.directory / "migration-three-ledger.sqlite3"
        ledger_connection = sqlite3.connect(ledger_path)
        ledger_connection.row_factory = sqlite3.Row
        try:
            apply_migrations(ledger_connection, "v2-at", PRODUCTION_MIGRATIONS[:2])
            ledger_connection.execute(
                "CREATE TRIGGER reject_v3 BEFORE INSERT ON schema_migrations "
                "WHEN NEW.version = 3 BEGIN SELECT RAISE(ABORT, 'reject'); END"
            )
            ledger_connection.commit()
            with self.assertRaises(MigrationError):
                apply_migrations(ledger_connection, "v3-at", PRODUCTION_MIGRATIONS)
            self.assertEqual(
                [(1,), (2,)],
                [tuple(row) for row in ledger_connection.execute("SELECT version FROM schema_migrations ORDER BY version")],
            )
            self.assertIsNone(
                ledger_connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='project_policies'"
                ).fetchone()
            )
        finally:
            ledger_connection.close()

    def test_existing_version_two_record_survives_migration_three(self) -> None:
        path = self.directory / "version-two-upgrade.sqlite3"
        connection = sqlite3.connect(path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            apply_migrations(connection, "v2-at", PRODUCTION_MIGRATIONS[:2])
            connection.execute(
                "INSERT INTO phases(project_id, phase_id, contract_version, contract_digest) "
                "VALUES('panam', 'DL-P1', '1', ?)",
                ("a" * 64,),
            )
            connection.commit()
            apply_migrations(connection, "v3-at", PRODUCTION_MIGRATIONS)
            self.assertEqual(
                ("panam", "DL-P1", "1", "a" * 64),
                tuple(connection.execute("SELECT * FROM phases").fetchone()),
            )
            self.assertEqual(0, connection.execute("SELECT COUNT(*) FROM project_policies").fetchone()[0])
            self.assertEqual(
                [(1,), (2,), (3,)],
                [tuple(row) for row in connection.execute("SELECT version FROM schema_migrations ORDER BY version")],
            )
        finally:
            connection.close()

    def test_reader_valid_and_missing_outcomes_are_read_only(self) -> None:
        self._insert(self.database_path, "panam", "1", "C:/workspace/panam")
        adapter = SqliteProjectPolicyRepository(self.database_path)
        reader = ProjectPolicyReader(adapter)
        before = self._snapshot(self.database_path)
        with (
            patch.object(SqliteRunStore, "initialize", side_effect=AssertionError("migration invoked")) as initialize,
            patch("panam_development_loop.sqlite_migrations.initialize_database", side_effect=AssertionError("migration invoked")) as initialize_database,
        ):
            first = reader.read("panam")
            second = reader.read("panam")
            case_different = reader.read("PANAM")
            missing = reader.read("absent")
        self.assertIs(ProjectPolicyReadOutcome.VALID_POLICY, first.outcome)
        self.assertEqual(self._policy(root="C:/workspace/panam"), first.policy)
        self.assertEqual(first, second)
        self.assertIs(ProjectPolicyReadOutcome.PROJECT_NOT_REGISTERED, case_different.outcome)
        self.assertIsNone(case_different.policy)
        self.assertIs(ProjectPolicyReadOutcome.PROJECT_NOT_REGISTERED, missing.outcome)
        self.assertIsNone(missing.policy)
        self.assertEqual(before, self._snapshot(self.database_path))
        initialize.assert_not_called()
        initialize_database.assert_not_called()
        for forbidden_name in ("create", "update", "delete", "seed", "provision", "admin"):
            self.assertFalse(hasattr(adapter, forbidden_name), forbidden_name)

    def test_invalid_caller_identity_is_rejected_before_storage(self) -> None:
        class NeverCalledRepository:
            calls = 0

            def get(self, project_id: str) -> ProjectPolicy | None:
                self.calls += 1
                raise AssertionError("storage accessed")

        repository = NeverCalledRepository()
        reader = ProjectPolicyReader(repository)
        for identity in ("", " panam", "panam ", "panam\n", 7, None):
            with self.subTest(identity=identity), self.assertRaises(RepositoryError) as raised:
                reader.read(identity)  # type: ignore[arg-type]
            self.assertIs(RepositoryFailureCode.INVALID_IDENTITY, raised.exception.code)
        self.assertEqual(0, repository.calls)

        missing_path = self.directory / "never-created.sqlite3"
        with self.assertRaises(RepositoryError) as raised:
            SqliteProjectPolicyRepository(missing_path).get("")
        self.assertIs(RepositoryFailureCode.INVALID_IDENTITY, raised.exception.code)
        self.assertFalse(missing_path.exists())

    def test_persisted_invalid_version_and_root_map_to_invalid_policy(self) -> None:
        reader = ProjectPolicyReader(SqliteProjectPolicyRepository(self.database_path))
        cases = (
            ("unsupported", "2", "C:\\repo"),
            ("relative", "1", "relative\\repo"),
            ("network", "1", "\\\\server\\share"),
            ("malformed", "1", "C:\\repo\\..\\other"),
        )
        for project_id, version, root in cases:
            self._insert(self.database_path, project_id, version, root)
            with self.subTest(project_id=project_id):
                result = reader.read(project_id)
                self.assertIs(ProjectPolicyReadOutcome.INVALID_POLICY, result.outcome)
                self.assertIsNone(result.policy)

    def test_broken_schema_and_storage_errors_map_to_storage_failure(self) -> None:
        malformed_path = self.directory / "malformed-schema.sqlite3"
        SqliteRunStore(malformed_path).initialize("at")
        connection = sqlite3.connect(malformed_path)
        try:
            connection.execute("DROP TABLE project_policies")
            connection.execute(
                "CREATE TABLE project_policies(project_id TEXT, policy_version TEXT, project_root TEXT)"
            )
            connection.execute("INSERT INTO project_policies VALUES('panam', '1', 'C:\\repo')")
            connection.commit()
        finally:
            connection.close()
        malformed = ProjectPolicyReader(SqliteProjectPolicyRepository(malformed_path)).read("panam")
        self.assertIs(ProjectPolicyReadOutcome.STORAGE_FAILURE, malformed.outcome)
        self.assertIsNone(malformed.policy)

        extra_column_path = self.directory / "extra-column.sqlite3"
        SqliteRunStore(extra_column_path).initialize("at")
        connection = sqlite3.connect(extra_column_path)
        try:
            connection.execute("ALTER TABLE project_policies ADD COLUMN remote TEXT")
            connection.commit()
        finally:
            connection.close()

        generated_column_path = self.directory / "generated-column.sqlite3"
        SqliteRunStore(generated_column_path).initialize("at")
        connection = sqlite3.connect(generated_column_path)
        try:
            connection.execute("DROP TABLE project_policies")
            connection.execute(
                "CREATE TABLE project_policies("
                "project_id TEXT NOT NULL PRIMARY KEY, "
                "policy_version TEXT NOT NULL, "
                "project_root TEXT NOT NULL, "
                "remote TEXT GENERATED ALWAYS AS ('forbidden') VIRTUAL)"
            )
            connection.execute(
                "INSERT INTO project_policies(project_id, policy_version, project_root) "
                "VALUES('panam', '1', 'C:\\repo')"
            )
            connection.commit()
            self.assertEqual(
                ["project_id", "policy_version", "project_root"],
                [row[1] for row in connection.execute("PRAGMA table_info(project_policies)")],
            )
            self.assertEqual(
                ["project_id", "policy_version", "project_root", "remote"],
                [row[1] for row in connection.execute("PRAGMA table_xinfo(project_policies)")],
            )
        finally:
            connection.close()
        generated_before = self._snapshot(generated_column_path)

        missing_column_path = self.directory / "missing-column.sqlite3"
        SqliteRunStore(missing_column_path).initialize("at")
        connection = sqlite3.connect(missing_column_path)
        try:
            connection.execute("DROP TABLE project_policies")
            connection.execute(
                "CREATE TABLE project_policies("
                "project_id TEXT NOT NULL PRIMARY KEY, policy_version TEXT NOT NULL)"
            )
            connection.commit()
        finally:
            connection.close()

        future_path = self.directory / "future-schema.sqlite3"
        SqliteRunStore(future_path).initialize("at")
        connection = sqlite3.connect(future_path)
        try:
            connection.execute("INSERT INTO schema_migrations VALUES(4, 'future-at')")
            connection.commit()
        finally:
            connection.close()

        corrupt_path = self.directory / "corrupt.sqlite3"
        corrupt_path.write_bytes(b"not a sqlite database")
        missing_path = self.directory / "missing.sqlite3"
        for path in (
            extra_column_path,
            generated_column_path,
            missing_column_path,
            future_path,
            corrupt_path,
            missing_path,
        ):
            with self.subTest(path=path.name):
                failed_storage = ProjectPolicyReader(SqliteProjectPolicyRepository(path)).read("panam")
                self.assertIs(ProjectPolicyReadOutcome.STORAGE_FAILURE, failed_storage.outcome)
                self.assertIsNone(failed_storage.policy)
        self.assertEqual(generated_before, self._snapshot(generated_column_path))
        self.assertFalse(missing_path.exists())

        class FailingRepository:
            def get(self, project_id: str) -> ProjectPolicy | None:
                raise RepositoryError(
                    RepositoryFailureCode.SQLITE_OPERATIONAL_FAILURE,
                    "ProjectPolicy",
                    f"project_id={project_id}",
                )

        failed = ProjectPolicyReader(FailingRepository()).read("panam")
        self.assertIs(ProjectPolicyReadOutcome.STORAGE_FAILURE, failed.outcome)
        self.assertIsNone(failed.policy)
        self.assertNotIn("sqlite", str(failed).lower())

    def test_incompatible_project_id_collations_fail_closed_without_mutation(self) -> None:
        for collation, lookup_identity in (("NOCASE", "PANAM"), ("RTRIM", "panam")):
            path = self.directory / f"{collation.lower()}-collation.sqlite3"
            SqliteRunStore(path).initialize("at")
            connection = sqlite3.connect(path)
            try:
                connection.execute("DROP TABLE project_policies")
                connection.execute(
                    "CREATE TABLE project_policies("
                    f"project_id TEXT NOT NULL COLLATE {collation} PRIMARY KEY, "
                    "policy_version TEXT NOT NULL, "
                    "project_root TEXT NOT NULL)"
                )
                connection.execute(
                    "INSERT INTO project_policies VALUES('panam', '1', 'C:\\repo')"
                )
                if collation == "NOCASE":
                    self.assertEqual(
                        "panam",
                        connection.execute(
                            "SELECT project_id FROM project_policies WHERE project_id = 'PANAM'"
                        ).fetchone()[0],
                    )
                connection.commit()
            finally:
                connection.close()

            before = self._snapshot(path)
            with self.subTest(collation=collation):
                result = ProjectPolicyReader(SqliteProjectPolicyRepository(path)).read(
                    lookup_identity
                )
                self.assertIs(ProjectPolicyReadOutcome.STORAGE_FAILURE, result.outcome)
                self.assertIsNone(result.policy)
                self.assertEqual(before, self._snapshot(path))

    def test_busy_database_maps_to_storage_failure_and_reader_has_no_forbidden_effects(self) -> None:
        self._insert(self.database_path, "panam", "1", "C:\\repo")
        reader = ProjectPolicyReader(SqliteProjectPolicyRepository(self.database_path))
        blocker = sqlite3.connect(self.database_path)
        try:
            blocker.execute("BEGIN EXCLUSIVE")
            blocked = reader.read("panam")
        finally:
            blocker.rollback()
            blocker.close()
        self.assertIs(ProjectPolicyReadOutcome.STORAGE_FAILURE, blocked.outcome)
        self.assertIsNone(blocked.policy)

        with (
            patch("subprocess.run", side_effect=AssertionError("subprocess invoked")) as subprocess_run,
            patch("urllib.request.urlopen", side_effect=AssertionError("network invoked")) as urlopen,
            patch.object(TransitionService, "transition", side_effect=AssertionError("transition invoked")) as transition,
            patch("threading.Thread", side_effect=AssertionError("worker invoked")) as worker,
        ):
            result = reader.read("panam")
        self.assertIs(ProjectPolicyReadOutcome.VALID_POLICY, result.outcome)
        for sentinel in (subprocess_run, urlopen, transition, worker):
            sentinel.assert_not_called()


class TransitionEvaluationPolicyTest(unittest.TestCase):
    """Deterministic contract coverage for the approved DL-P1.6 evaluator."""

    def setUp(self) -> None:
        self.policy = TransitionPolicy()

    @staticmethod
    def _contract(milestone_id: str = "DL-P1.6") -> MilestoneContract:
        return MilestoneContract(
            project_id="panam",
            phase_id="DL-P1",
            milestone_id=milestone_id,
            contract_version="1",
            objective="Implement pure transition evaluation",
            scope=("deterministic evaluator", "typed transition policy"),
            exclusions=("persistence",),
            acceptance_criteria=("complete results", "fail closed"),
            allowed_paths=("panam_development_loop/transition_policy.py",),
            forbidden_paths=("panam_development_loop/transition_service.py",),
            verification_plan=("focused tests", "full regression"),
            stop_conditions=("scope change",),
        )

    @staticmethod
    def _run(
        contract: MilestoneContract,
        state: DevelopmentRunState = DevelopmentRunState.DRAFT,
        version: int = 3,
    ) -> DevelopmentRun:
        return DevelopmentRun(
            run_id="run-p1-6",
            milestone_contract_digest=contract.sha256_digest(),
            current_state=state,
            state_version=version,
            created_at="2026-08-11T10:00:00Z",
            updated_at="2026-08-11T10:01:00.000001Z",
        )

    @staticmethod
    def _repository() -> RepositoryEvidenceBinding:
        return RepositoryEvidenceBinding(
            repository_id="Panam_APP",
            branch="phase/panam-dl-p1",
            head_commit="a" * 40,
            worktree_digest="b" * 64,
        )

    @staticmethod
    def _expected() -> ExpectedEvidenceBinding:
        return ExpectedEvidenceBinding(
            assessment_authority_id="HAD-DL-P1.6-FRESH-FEASIBILITY-007",
            artifact_path=r"C:\Panam_Runtime\FEASIBILITY-ASSESSMENT-8.md",
            artifact_byte_count=20431,
            artifact_sha256="c" * 64,
        )

    def _unconditional_request(self, **changes: object) -> TransitionEvaluationRequest:
        contract = self._contract()
        values: dict[str, object] = {
            "evaluation_version": "1",
            "run": self._run(contract),
            "rule_id": TransitionRuleId.P1_6_DRAFT_TO_FEASIBILITY_CHECKING_V1.value,
            "requested_state": DevelopmentRunState.FEASIBILITY_CHECKING,
            "edge_type": WorkflowEdgeType.UNCONDITIONAL,
            "milestone_contract": contract,
        }
        values.update(changes)
        return TransitionEvaluationRequest(**values)  # type: ignore[arg-type]

    def _evidence_request(self, **changes: object) -> TransitionEvaluationRequest:
        contract = self._contract()
        run = self._run(contract, DevelopmentRunState.FEASIBILITY_CHECKING, 7)
        repository = self._repository()
        expected = self._expected()
        evidence = EvidenceSnapshot(
            snapshot_version="1",
            producer_kind=SnapshotProducerKind.FEASIBILITY_ASSESSOR,
            run_id=run.run_id,
            milestone_id=contract.milestone_id,
            milestone_contract_digest=contract.sha256_digest(),
            source_state=run.current_state,
            state_version=run.state_version,
            rule_id=(
                TransitionRuleId.P1_6_FEASIBILITY_CHECKING_TO_AWAITING_EXECUTION_APPROVAL_V1
            ),
            evidence_kind=EvidenceKind.FEASIBILITY_ASSESSMENT,
            assessment_authority_id=expected.assessment_authority_id,
            artifact_path=expected.artifact_path,
            artifact_byte_count=expected.artifact_byte_count,
            artifact_sha256=expected.artifact_sha256,
            verdict=EvidenceVerdict.FEASIBLE_WITH_NON_BLOCKING_FINDINGS,
            ready_for_approval_1=True,
            assessed_at="2026-08-11T12:00:00Z",
            repository_binding=repository,
        )
        values: dict[str, object] = {
            "evaluation_version": "1",
            "run": run,
            "rule_id": (
                TransitionRuleId.P1_6_FEASIBILITY_CHECKING_TO_AWAITING_EXECUTION_APPROVAL_V1.value
            ),
            "requested_state": DevelopmentRunState.AWAITING_EXECUTION_APPROVAL,
            "edge_type": WorkflowEdgeType.EVIDENCE_GATED,
            "milestone_contract": contract,
            "expected_evidence": expected,
            "repository_binding": repository,
            "evidence_snapshots": (evidence,),
        }
        values.update(changes)
        return TransitionEvaluationRequest(**values)  # type: ignore[arg-type]

    def _assert_outcome(
        self,
        request: object,
        decision: TransitionEvaluationDecision,
        reason: TransitionEvaluationReasonCode,
    ) -> TransitionEvaluationResult:
        run = None
        requested_state = None
        rule_id = None
        edge_type = None
        version_is_lexically_valid = (
            type(request) is TransitionEvaluationRequest
            and type(request.evaluation_version) is str
            and re.fullmatch(r"[1-9][0-9]*", request.evaluation_version, re.ASCII)
            is not None
        )
        if (
            version_is_lexically_valid
            and type(request.run) is DevelopmentRun
        ):
            run = request.run
            rule_id_is_lexically_valid = (
                type(request.rule_id) is str
                and 4 <= len(request.rule_id) <= 128
                and re.fullmatch(
                    r"[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)*_V1",
                    request.rule_id,
                    re.ASCII,
                )
                is not None
            )
            if rule_id_is_lexically_valid and type(
                request.requested_state
            ) is DevelopmentRunState:
                requested_state = request.requested_state
                early_structure_is_valid = (
                    type(request.edge_type) is WorkflowEdgeType
                    and (
                        request.node_type is None
                        or type(request.node_type) is WorkflowNodeType
                    )
                    and type(request.evidence_snapshots) is tuple
                    and (
                        request.escalation_trigger is None
                        or type(request.escalation_trigger) is EscalationTrigger
                    )
                )
                contradiction = (
                    request.retry_budget_snapshot is not None
                    and request.escalation_trigger is not None
                )
                pair = (run.current_state, requested_state)
                matched_rule = self.policy._RULES_BY_PAIR.get(pair)
                requested_rule_id = next(
                    (
                        candidate
                        for candidate in TransitionRuleId
                        if candidate.value == request.rule_id
                    ),
                    None,
                )
                if (
                    early_structure_is_valid
                    and request.evaluation_version == "1"
                    and not contradiction
                    and matched_rule is not None
                    and requested_rule_id is not None
                ):
                    rule_id = requested_rule_id
                    requested_rule = self.policy._RULES_BY_ID[requested_rule_id]
                    if (
                        requested_rule.source_state is run.current_state
                        and requested_rule.target_state is requested_state
                        and request.edge_type is requested_rule.edge_type
                    ):
                        edge_type = requested_rule.edge_type

        missing = {
            TransitionEvaluationReasonCode.CONTRACT_REQUIRED: (
                TransitionRequirement.CONTRACT,
            ),
            TransitionEvaluationReasonCode.REPOSITORY_BINDING_REQUIRED: (
                TransitionRequirement.REPOSITORY,
            ),
            TransitionEvaluationReasonCode.EVIDENCE_REQUIRED: (
                TransitionRequirement.EVIDENCE,
            ),
            TransitionEvaluationReasonCode.APPROVAL_REQUIRED: (
                TransitionRequirement.APPROVAL,
            ),
        }.get(reason, ())
        expected = TransitionEvaluationResult(
            decision=decision,
            reason_code=reason,
            run_id=None if run is None else run.run_id,
            current_state=None if run is None else run.current_state,
            current_state_version=None if run is None else run.state_version,
            requested_state=requested_state,
            rule_id=rule_id,
            edge_type=edge_type,
            node_type=None,
            missing_requirements=missing,
        )
        result = self.policy.evaluate(request)
        self.assertEqual(expected, result)
        return result

    def _assert_rule_error(
        self,
        code: TransitionRuleValidationCode,
        field_name: str,
        operation: object,
    ) -> None:
        with self.assertRaises(TransitionRuleValidationError) as raised:
            operation()  # type: ignore[operator]
        self.assertEqual(code, raised.exception.code)
        self.assertEqual(field_name, raised.exception.field_name)
        self.assertEqual(f"{code.value}: {field_name}", str(raised.exception))

    def test_public_vocabularies_shapes_defaults_and_immutability(self) -> None:
        self.assertEqual(
            [
                "DETERMINISTIC",
                "MODEL_CALL",
                "SPECIALIST_AGENT",
                "HUMAN_APPROVAL",
                "EXTERNAL_EFFECT",
            ],
            [value.value for value in WorkflowNodeType],
        )
        self.assertEqual(
            [
                "UNCONDITIONAL",
                "STATE_CONDITIONAL",
                "EVIDENCE_GATED",
                "APPROVAL_GATED",
                "RETRY",
                "ESCALATION",
                "TERMINAL",
            ],
            [value.value for value in WorkflowEdgeType],
        )
        self.assertEqual(2, len(TransitionRuleId))
        self.assertEqual(1, len(EvidenceKind))
        self.assertEqual(5, len(EvidenceVerdict))
        self.assertEqual(11, len(EscalationTrigger))
        self.assertEqual(
            [
                "evaluation_version",
                "run",
                "rule_id",
                "requested_state",
                "edge_type",
                "node_type",
                "milestone_contract",
                "expected_evidence",
                "repository_binding",
                "approval_snapshot",
                "evidence_snapshots",
                "retry_budget_snapshot",
                "escalation_trigger",
            ],
            [item.name for item in fields(TransitionEvaluationRequest)],
        )
        self.assertEqual(
            [
                "decision",
                "reason_code",
                "run_id",
                "current_state",
                "current_state_version",
                "requested_state",
                "rule_id",
                "edge_type",
                "node_type",
                "missing_requirements",
                "side_effects_performed",
            ],
            [item.name for item in fields(TransitionEvaluationResult)],
        )
        omitted = TransitionEvaluationRequest()
        explicit = TransitionEvaluationRequest(evidence_snapshots=())
        self.assertEqual(omitted, explicit)
        self.assertEqual((), omitted.evidence_snapshots)
        self.assertIsNone(TransitionEvaluationRequest.__hash__)
        with self.assertRaises(FrozenInstanceError):
            omitted.evaluation_version = "1"  # type: ignore[misc]

    def test_transition_rule_contract_validation_equality_and_hash(self) -> None:
        self.assertEqual(
            [
                "rule_id",
                "source_state",
                "target_state",
                "edge_type",
                "requirements",
                "node_type_constraint",
                "evidence_kind_requirement",
                "evidence_producer_kind_requirement",
            ],
            [item.name for item in fields(TransitionRule)],
        )
        self.assertEqual(
            [
                "WRONG_TYPE",
                "RULE_ID_MALFORMED",
                "SOURCE_EQUALS_TARGET",
                "REQUIREMENTS_NOT_CANONICAL_TUPLE",
                "REQUIREMENT_WRONG_TYPE",
                "REQUIREMENTS_NON_CANONICAL_ORDER",
                "DUPLICATE_REQUIREMENT",
                "EDGE_REQUIREMENTS_INCOMPATIBLE",
                "METADATA_INCOMPATIBLE",
                "CONFLICTS_WITH_RULE_ID",
            ],
            [value.value for value in TransitionRuleValidationCode],
        )
        rule = TransitionRule(
            TransitionRuleId.P1_6_DRAFT_TO_FEASIBILITY_CHECKING_V1,
            DevelopmentRunState.DRAFT,
            DevelopmentRunState.FEASIBILITY_CHECKING,
            WorkflowEdgeType.UNCONDITIONAL,
            (TransitionRequirement.CONTRACT,),
        )
        self.assertEqual(rule, replace(rule))
        self.assertEqual(hash(rule), hash(replace(rule)))
        self.assertEqual(1, len({rule, replace(rule)}))
        with self.assertRaises(FrozenInstanceError):
            rule.edge_type = WorkflowEdgeType.RETRY  # type: ignore[misc]

        base = {
            "rule_id": TransitionRuleId.P1_6_DRAFT_TO_FEASIBILITY_CHECKING_V1,
            "source_state": DevelopmentRunState.DRAFT,
            "target_state": DevelopmentRunState.FEASIBILITY_CHECKING,
            "edge_type": WorkflowEdgeType.UNCONDITIONAL,
            "requirements": (TransitionRequirement.CONTRACT,),
        }
        cases = (
            (
                TransitionRuleValidationCode.WRONG_TYPE,
                "rule_id",
                {"rule_id": rule.rule_id.value},
            ),
            (
                TransitionRuleValidationCode.SOURCE_EQUALS_TARGET,
                "source_state/target_state",
                {"target_state": DevelopmentRunState.DRAFT},
            ),
            (
                TransitionRuleValidationCode.REQUIREMENTS_NOT_CANONICAL_TUPLE,
                "requirements",
                {"requirements": [TransitionRequirement.CONTRACT]},
            ),
            (
                TransitionRuleValidationCode.REQUIREMENT_WRONG_TYPE,
                "requirements[0]",
                {"requirements": ("CONTRACT",)},
            ),
            (
                TransitionRuleValidationCode.REQUIREMENTS_NON_CANONICAL_ORDER,
                "requirements",
                {
                    "requirements": (
                        TransitionRequirement.REPOSITORY,
                        TransitionRequirement.CONTRACT,
                    )
                },
            ),
            (
                TransitionRuleValidationCode.DUPLICATE_REQUIREMENT,
                "requirements",
                {
                    "requirements": (
                        TransitionRequirement.CONTRACT,
                        TransitionRequirement.CONTRACT,
                    )
                },
            ),
            (
                TransitionRuleValidationCode.EDGE_REQUIREMENTS_INCOMPATIBLE,
                "requirements",
                {"requirements": ()},
            ),
            (
                TransitionRuleValidationCode.METADATA_INCOMPATIBLE,
                "node_type_constraint",
                {"node_type_constraint": WorkflowNodeType.DETERMINISTIC},
            ),
            (
                TransitionRuleValidationCode.CONFLICTS_WITH_RULE_ID,
                "source_state",
                {
                    "source_state": DevelopmentRunState.FEASIBILITY_CHECKING,
                    "target_state": DevelopmentRunState.AWAITING_EXECUTION_APPROVAL,
                },
            ),
        )
        for code, field_name, changes in cases:
            values = dict(base)
            values.update(changes)
            with self.subTest(code=code):
                self._assert_rule_error(
                    code,
                    field_name,
                    lambda values=values: TransitionRule(**values),  # type: ignore[arg-type]
                )

        precedence_cases = (
            (
                TransitionRuleValidationCode.WRONG_TYPE,
                "rule_id",
                {"rule_id": "BAD", "source_state": "DRAFT"},
            ),
            (
                TransitionRuleValidationCode.WRONG_TYPE,
                "source_state",
                {"source_state": "DRAFT", "target_state": "TARGET"},
            ),
            (
                TransitionRuleValidationCode.SOURCE_EQUALS_TARGET,
                "source_state/target_state",
                {
                    "target_state": DevelopmentRunState.DRAFT,
                    "edge_type": "UNCONDITIONAL",
                },
            ),
            (
                TransitionRuleValidationCode.WRONG_TYPE,
                "edge_type",
                {
                    "edge_type": "UNCONDITIONAL",
                    "requirements": [TransitionRequirement.CONTRACT],
                },
            ),
            (
                TransitionRuleValidationCode.REQUIREMENTS_NOT_CANONICAL_TUPLE,
                "requirements",
                {
                    "requirements": ["CONTRACT"],
                    "node_type_constraint": "DETERMINISTIC",
                },
            ),
            (
                TransitionRuleValidationCode.REQUIREMENT_WRONG_TYPE,
                "requirements[0]",
                {"requirements": ("CONTRACT", TransitionRequirement.CONTRACT)},
            ),
            (
                TransitionRuleValidationCode.REQUIREMENTS_NON_CANONICAL_ORDER,
                "requirements",
                {
                    "requirements": (
                        TransitionRequirement.REPOSITORY,
                        TransitionRequirement.CONTRACT,
                        TransitionRequirement.CONTRACT,
                    )
                },
            ),
            (
                TransitionRuleValidationCode.DUPLICATE_REQUIREMENT,
                "requirements",
                {
                    "requirements": (
                        TransitionRequirement.CONTRACT,
                        TransitionRequirement.CONTRACT,
                    ),
                    "node_type_constraint": "DETERMINISTIC",
                },
            ),
            (
                TransitionRuleValidationCode.WRONG_TYPE,
                "evidence_kind_requirement",
                {
                    "evidence_kind_requirement": "FEASIBILITY_ASSESSMENT",
                    "evidence_producer_kind_requirement": "FEASIBILITY_ASSESSOR",
                },
            ),
            (
                TransitionRuleValidationCode.EDGE_REQUIREMENTS_INCOMPATIBLE,
                "requirements",
                {
                    "edge_type": WorkflowEdgeType.EVIDENCE_GATED,
                    "requirements": (TransitionRequirement.CONTRACT,),
                    "node_type_constraint": WorkflowNodeType.DETERMINISTIC,
                },
            ),
        )
        for code, field_name, changes in precedence_cases:
            values = dict(base)
            values.update(changes)
            with self.subTest(precedence=code, changes=changes):
                self._assert_rule_error(
                    code,
                    field_name,
                    lambda values=values: TransitionRule(**values),  # type: ignore[arg-type]
                )

        self.assertNotIn(
            "CONTRADICTORY_REQUIREMENTS",
            {value.value for value in TransitionRuleValidationCode},
        )

    def test_registry_is_exact_immutable_and_legacy_allows_is_preserved(self) -> None:
        rules = self.policy._RULES_BY_ID
        self.assertEqual(set(TransitionRuleId), set(rules))
        self.assertEqual(2, len(rules))
        with self.assertRaises(TypeError):
            rules[TransitionRuleId.P1_6_DRAFT_TO_FEASIBILITY_CHECKING_V1] = rules[  # type: ignore[index]
                TransitionRuleId.P1_6_DRAFT_TO_FEASIBILITY_CHECKING_V1
            ]
        allowed_pairs = {
            (
                DevelopmentRunState.DRAFT,
                DevelopmentRunState.FEASIBILITY_CHECKING,
            ),
            (
                DevelopmentRunState.FEASIBILITY_CHECKING,
                DevelopmentRunState.AWAITING_EXECUTION_APPROVAL,
            ),
        }
        for source in DevelopmentRunState:
            for target in DevelopmentRunState:
                self.assertEqual(
                    (source, target) in allowed_pairs,
                    self.policy.allows(source, target),
                )
        self.assertTrue(
            all(
                other.source_state is not rule.source_state
                for rule in rules.values()
                for other in rules.values()
                if other is not rule
            )
        )

    def test_stage_one_version_and_validation_progress(self) -> None:
        self._assert_outcome(
            object(),
            TransitionEvaluationDecision.INVALID,
            TransitionEvaluationReasonCode.REQUEST_NOT_EVALUATION_REQUEST,
        )
        self._assert_outcome(
            TransitionEvaluationRequest(),
            TransitionEvaluationDecision.INVALID,
            TransitionEvaluationReasonCode.REQUEST_REQUIRED_FIELD_MISSING,
        )
        self._assert_outcome(
            TransitionEvaluationRequest(evaluation_version=1),  # type: ignore[arg-type]
            TransitionEvaluationDecision.INVALID,
            TransitionEvaluationReasonCode.REQUEST_WRONG_TYPE,
        )
        self._assert_outcome(
            TransitionEvaluationRequest(evaluation_version="01"),
            TransitionEvaluationDecision.INVALID,
            TransitionEvaluationReasonCode.EVALUATION_VERSION_MALFORMED,
        )
        unsupported = self._unconditional_request(evaluation_version="2")
        self._assert_outcome(
            unsupported,
            TransitionEvaluationDecision.UNSUPPORTED,
            TransitionEvaluationReasonCode.EVALUATION_VERSION_UNSUPPORTED,
        )
        invalid_rule = self._unconditional_request(rule_id="bad")
        rule_result = self._assert_outcome(
            invalid_rule,
            TransitionEvaluationDecision.INVALID,
            TransitionEvaluationReasonCode.REQUEST_RULE_ID_MALFORMED,
        )
        self.assertEqual(invalid_rule.run.run_id, rule_result.run_id)  # type: ignore[union-attr]
        self.assertIsNone(rule_result.requested_state)
        invalid_trigger = self._unconditional_request(escalation_trigger="NO_PROGRESS_STOP")
        trigger_result = self._assert_outcome(
            invalid_trigger,
            TransitionEvaluationDecision.INVALID,
            TransitionEvaluationReasonCode.ESCALATION_SNAPSHOT_MALFORMED,
        )
        self.assertEqual(DevelopmentRunState.FEASIBILITY_CHECKING, trigger_result.requested_state)
        self.assertIsNone(trigger_result.rule_id)
        self.assertIsNone(trigger_result.edge_type)
        self.assertIsNone(trigger_result.node_type)
        self._assert_outcome(
            self._unconditional_request(evidence_snapshots=None),
            TransitionEvaluationDecision.INVALID,
            TransitionEvaluationReasonCode.REQUEST_WRONG_TYPE,
        )

    def test_exact_four_by_four_pair_behavior(self) -> None:
        registered = {
            (
                DevelopmentRunState.DRAFT,
                DevelopmentRunState.FEASIBILITY_CHECKING,
            ),
            (
                DevelopmentRunState.FEASIBILITY_CHECKING,
                DevelopmentRunState.AWAITING_EXECUTION_APPROVAL,
            ),
        }
        for source in DevelopmentRunState:
            for target in DevelopmentRunState:
                pair = (source, target)
                if pair == (
                    DevelopmentRunState.DRAFT,
                    DevelopmentRunState.FEASIBILITY_CHECKING,
                ):
                    request = self._unconditional_request()
                elif pair == (
                    DevelopmentRunState.FEASIBILITY_CHECKING,
                    DevelopmentRunState.AWAITING_EXECUTION_APPROVAL,
                ):
                    request = self._evidence_request()
                else:
                    contract = self._contract()
                    request = TransitionEvaluationRequest(
                        evaluation_version="1",
                        run=self._run(contract, source),
                        rule_id=(
                            TransitionRuleId.P1_6_DRAFT_TO_FEASIBILITY_CHECKING_V1.value
                        ),
                        requested_state=target,
                        edge_type=WorkflowEdgeType.UNCONDITIONAL,
                    )
                with self.subTest(source=source, target=target):
                    if pair in registered:
                        self._assert_outcome(
                            request,
                            TransitionEvaluationDecision.ALLOWED,
                            TransitionEvaluationReasonCode.RULE_ALLOWED,
                        )
                    else:
                        self._assert_outcome(
                            request,
                            TransitionEvaluationDecision.DENIED,
                            TransitionEvaluationReasonCode.REGISTRY_NO_REGISTERED_TRANSITION,
                        )

    def test_registered_coherence_contract_and_surplus_precedence(self) -> None:
        self._assert_outcome(
            self._unconditional_request(rule_id="P1_6_UNKNOWN_V1"),
            TransitionEvaluationDecision.UNSUPPORTED,
            TransitionEvaluationReasonCode.REGISTRY_RULE_ID_UNSUPPORTED,
        )
        self._assert_outcome(
            self._unconditional_request(
                rule_id=(
                    TransitionRuleId.P1_6_FEASIBILITY_CHECKING_TO_AWAITING_EXECUTION_APPROVAL_V1.value
                )
            ),
            TransitionEvaluationDecision.INVALID,
            TransitionEvaluationReasonCode.REGISTRY_RULE_SOURCE_MISMATCH,
        )
        self._assert_outcome(
            self._unconditional_request(edge_type=WorkflowEdgeType.RETRY),
            TransitionEvaluationDecision.INVALID,
            TransitionEvaluationReasonCode.REGISTRY_EDGE_TYPE_MISMATCH,
        )
        self._assert_outcome(
            self._unconditional_request(node_type=WorkflowNodeType.DETERMINISTIC),
            TransitionEvaluationDecision.INVALID,
            TransitionEvaluationReasonCode.REGISTRY_NODE_METADATA_MISMATCH,
        )
        self._assert_outcome(
            self._unconditional_request(expected_evidence=self._expected()),
            TransitionEvaluationDecision.INVALID,
            TransitionEvaluationReasonCode.REQUEST_SURPLUS_PREREQUISITE,
        )
        self._assert_outcome(
            self._unconditional_request(
                edge_type=WorkflowEdgeType.RETRY,
                milestone_contract="malformed",
            ),
            TransitionEvaluationDecision.INVALID,
            TransitionEvaluationReasonCode.CONTRACT_MALFORMED,
        )
        missing = replace(self._unconditional_request(), milestone_contract=None)
        missing_result = self._assert_outcome(
            missing,
            TransitionEvaluationDecision.GATED,
            TransitionEvaluationReasonCode.CONTRACT_REQUIRED,
        )
        self.assertEqual((TransitionRequirement.CONTRACT,), missing_result.missing_requirements)
        mismatch = self._unconditional_request(milestone_contract=self._contract("other"))
        self._assert_outcome(
            mismatch,
            TransitionEvaluationDecision.INVALID,
            TransitionEvaluationReasonCode.CONTRACT_BINDING_MISMATCH,
        )

    def test_closed_contradiction_lone_facts_and_deferred_edges(self) -> None:
        trigger = EscalationTrigger.NO_PROGRESS_STOP
        contradiction = self._unconditional_request(
            retry_budget_snapshot={"opaque": True},
            escalation_trigger=trigger,
        )
        result = self._assert_outcome(
            contradiction,
            TransitionEvaluationDecision.INVALID,
            TransitionEvaluationReasonCode.REQUEST_CONTRADICTORY_FACTS,
        )
        self.assertIsNone(result.rule_id)
        self.assertIsNone(result.edge_type)
        self.assertEqual((), result.missing_requirements)

        self._assert_outcome(
            self._unconditional_request(escalation_trigger=trigger),
            TransitionEvaluationDecision.INVALID,
            TransitionEvaluationReasonCode.REQUEST_SURPLUS_PREREQUISITE,
        )
        self._assert_outcome(
            self._unconditional_request(
                edge_type=WorkflowEdgeType.RETRY,
                escalation_trigger=trigger,
            ),
            TransitionEvaluationDecision.INVALID,
            TransitionEvaluationReasonCode.REGISTRY_EDGE_TYPE_MISMATCH,
        )
        self._assert_outcome(
            self._unconditional_request(retry_budget_snapshot=object()),
            TransitionEvaluationDecision.INVALID,
            TransitionEvaluationReasonCode.REQUEST_SURPLUS_PREREQUISITE,
        )
        self._assert_outcome(
            self._unconditional_request(
                edge_type=WorkflowEdgeType.RETRY,
                retry_budget_snapshot=object(),
            ),
            TransitionEvaluationDecision.INVALID,
            TransitionEvaluationReasonCode.REGISTRY_EDGE_TYPE_MISMATCH,
        )

        contract = self._contract()
        run = self._run(contract, DevelopmentRunState.SOURCE_COMPLETED)
        base = {
            "evaluation_version": "1",
            "run": run,
            "rule_id": TransitionRuleId.P1_6_DRAFT_TO_FEASIBILITY_CHECKING_V1.value,
            "requested_state": DevelopmentRunState.DRAFT,
        }
        self._assert_outcome(
            TransitionEvaluationRequest(
                **base,
                edge_type=WorkflowEdgeType.UNCONDITIONAL,
                escalation_trigger=trigger,
            ),
            TransitionEvaluationDecision.INVALID,
            TransitionEvaluationReasonCode.REQUEST_SURPLUS_PREREQUISITE,
        )
        self._assert_outcome(
            TransitionEvaluationRequest(
                **base,
                edge_type=WorkflowEdgeType.ESCALATION,
                escalation_trigger=trigger,
            ),
            TransitionEvaluationDecision.UNSUPPORTED,
            TransitionEvaluationReasonCode.ESCALATION_RULE_DEFERRED,
        )
        self._assert_outcome(
            TransitionEvaluationRequest(**base, edge_type=WorkflowEdgeType.ESCALATION),
            TransitionEvaluationDecision.INVALID,
            TransitionEvaluationReasonCode.ESCALATION_SNAPSHOT_MALFORMED,
        )
        self._assert_outcome(
            TransitionEvaluationRequest(**base, edge_type=WorkflowEdgeType.RETRY),
            TransitionEvaluationDecision.UNSUPPORTED,
            TransitionEvaluationReasonCode.RETRY_RULE_DEFERRED,
        )
        self._assert_outcome(
            TransitionEvaluationRequest(
                **base,
                edge_type=WorkflowEdgeType.RETRY,
                retry_budget_snapshot=object(),
            ),
            TransitionEvaluationDecision.INVALID,
            TransitionEvaluationReasonCode.REQUEST_SURPLUS_PREREQUISITE,
        )
        for edge, reason in (
            (
                WorkflowEdgeType.STATE_CONDITIONAL,
                TransitionEvaluationReasonCode.STATE_CONDITIONAL_RULE_DEFERRED,
            ),
            (
                WorkflowEdgeType.APPROVAL_GATED,
                TransitionEvaluationReasonCode.APPROVAL_GATED_RULE_DEFERRED,
            ),
            (
                WorkflowEdgeType.TERMINAL,
                TransitionEvaluationReasonCode.TERMINAL_RULE_DEFERRED,
            ),
        ):
            self._assert_outcome(
                TransitionEvaluationRequest(**base, edge_type=edge),
                TransitionEvaluationDecision.UNSUPPORTED,
                reason,
            )

        malformed_trigger_cases = (
            self._unconditional_request(
                edge_type=WorkflowEdgeType.RETRY,
                escalation_trigger="NO_PROGRESS_STOP",
            ),
            TransitionEvaluationRequest(
                **base,
                edge_type=WorkflowEdgeType.UNCONDITIONAL,
                escalation_trigger="NO_PROGRESS_STOP",
            ),
            TransitionEvaluationRequest(
                **base,
                edge_type=WorkflowEdgeType.RETRY,
                escalation_trigger="NO_PROGRESS_STOP",
            ),
            self._unconditional_request(
                expected_evidence=self._expected(),
                escalation_trigger="NO_PROGRESS_STOP",
            ),
        )
        for malformed_request in malformed_trigger_cases:
            with self.subTest(malformed_trigger=malformed_request.edge_type):
                self._assert_outcome(
                    malformed_request,
                    TransitionEvaluationDecision.INVALID,
                    TransitionEvaluationReasonCode.ESCALATION_SNAPSHOT_MALFORMED,
                )

    def test_evidence_gate_success_missing_and_full_precedence(self) -> None:
        allowed = self._evidence_request()
        self._assert_outcome(
            allowed,
            TransitionEvaluationDecision.ALLOWED,
            TransitionEvaluationReasonCode.RULE_ALLOWED,
        )
        missing_expected = replace(allowed, expected_evidence=None)
        self._assert_outcome(
            missing_expected,
            TransitionEvaluationDecision.INVALID,
            TransitionEvaluationReasonCode.EXPECTED_EVIDENCE_ANCHOR_MISSING,
        )
        malformed_expected = replace(
            allowed,
            expected_evidence=replace(allowed.expected_evidence, artifact_path="relative"),
        )
        self._assert_outcome(
            malformed_expected,
            TransitionEvaluationDecision.INVALID,
            TransitionEvaluationReasonCode.EXPECTED_EVIDENCE_ANCHOR_MALFORMED,
        )
        missing_repository = replace(allowed, repository_binding=None)
        repository_result = self._assert_outcome(
            missing_repository,
            TransitionEvaluationDecision.GATED,
            TransitionEvaluationReasonCode.REPOSITORY_BINDING_REQUIRED,
        )
        self.assertEqual(
            (TransitionRequirement.REPOSITORY,),
            repository_result.missing_requirements,
        )
        missing_evidence = replace(allowed, evidence_snapshots=())
        evidence_result = self._assert_outcome(
            missing_evidence,
            TransitionEvaluationDecision.GATED,
            TransitionEvaluationReasonCode.EVIDENCE_REQUIRED,
        )
        self.assertEqual((TransitionRequirement.EVIDENCE,), evidence_result.missing_requirements)

        evidence = allowed.evidence_snapshots[0]
        malformed = replace(evidence, artifact_path="relative")
        self._assert_outcome(
            replace(allowed, evidence_snapshots=(malformed,)),
            TransitionEvaluationDecision.INVALID,
            TransitionEvaluationReasonCode.EVIDENCE_SNAPSHOT_MALFORMED,
        )
        raising_version = RaisingEquality()
        wrong_type_version = replace(evidence, snapshot_version=raising_version)
        self._assert_outcome(
            replace(allowed, evidence_snapshots=(wrong_type_version,)),
            TransitionEvaluationDecision.INVALID,
            TransitionEvaluationReasonCode.EVIDENCE_SNAPSHOT_MALFORMED,
        )
        self.assertFalse(raising_version.invoked)
        self._assert_outcome(
            replace(allowed, evidence_snapshots=(evidence, replace(evidence))),
            TransitionEvaluationDecision.INVALID,
            TransitionEvaluationReasonCode.EVIDENCE_SNAPSHOT_MALFORMED,
        )

        cases = (
            (
                replace(evidence, ready_for_approval_1=False),
                TransitionEvaluationDecision.INVALID,
                TransitionEvaluationReasonCode.EVIDENCE_VERDICT_READINESS_INCONSISTENT,
            ),
            (
                replace(evidence, artifact_byte_count=evidence.artifact_byte_count + 1),
                TransitionEvaluationDecision.INVALID,
                TransitionEvaluationReasonCode.EVIDENCE_ARTIFACT_IDENTITY_MISMATCH,
            ),
            (
                replace(evidence, artifact_sha256="d" * 64),
                TransitionEvaluationDecision.INVALID,
                TransitionEvaluationReasonCode.EVIDENCE_ARTIFACT_DIGEST_MISMATCH,
            ),
            (
                replace(evidence, assessment_authority_id="other-authority"),
                TransitionEvaluationDecision.INVALID,
                TransitionEvaluationReasonCode.EVIDENCE_AUTHORITY_MISMATCH,
            ),
            (
                replace(evidence, run_id="other-run"),
                TransitionEvaluationDecision.INVALID,
                TransitionEvaluationReasonCode.EVIDENCE_RUN_MISMATCH,
            ),
            (
                replace(evidence, milestone_id="DL-P1.other"),
                TransitionEvaluationDecision.INVALID,
                TransitionEvaluationReasonCode.EVIDENCE_CONTRACT_BINDING_MISMATCH,
            ),
            (
                replace(
                    evidence,
                    rule_id=TransitionRuleId.P1_6_DRAFT_TO_FEASIBILITY_CHECKING_V1,
                ),
                TransitionEvaluationDecision.INVALID,
                TransitionEvaluationReasonCode.EVIDENCE_RULE_MISMATCH,
            ),
            (
                replace(evidence, source_state=DevelopmentRunState.DRAFT),
                TransitionEvaluationDecision.GATED,
                TransitionEvaluationReasonCode.EVIDENCE_STALE,
            ),
            (
                replace(evidence, state_version=evidence.state_version + 1),
                TransitionEvaluationDecision.GATED,
                TransitionEvaluationReasonCode.EVIDENCE_STALE,
            ),
            (
                replace(
                    evidence,
                    repository_binding=replace(
                        evidence.repository_binding,
                        repository_id="other-repository",
                    ),
                ),
                TransitionEvaluationDecision.INVALID,
                TransitionEvaluationReasonCode.EVIDENCE_REPOSITORY_IDENTITY_MISMATCH,
            ),
            (
                replace(
                    evidence,
                    repository_binding=replace(
                        evidence.repository_binding,
                        branch="other-branch",
                    ),
                ),
                TransitionEvaluationDecision.GATED,
                TransitionEvaluationReasonCode.EVIDENCE_STALE,
            ),
            (
                replace(
                    evidence,
                    repository_binding=replace(
                        evidence.repository_binding,
                        head_commit="d" * 40,
                    ),
                ),
                TransitionEvaluationDecision.GATED,
                TransitionEvaluationReasonCode.EVIDENCE_STALE,
            ),
            (
                replace(
                    evidence,
                    repository_binding=replace(
                        evidence.repository_binding,
                        worktree_digest="d" * 64,
                    ),
                ),
                TransitionEvaluationDecision.GATED,
                TransitionEvaluationReasonCode.EVIDENCE_STALE,
            ),
            (
                replace(
                    evidence,
                    repository_binding=replace(
                        evidence.repository_binding,
                        repository_id="other-repository",
                        branch="other-branch",
                        head_commit="d" * 40,
                        worktree_digest="d" * 64,
                    ),
                ),
                TransitionEvaluationDecision.INVALID,
                TransitionEvaluationReasonCode.EVIDENCE_REPOSITORY_IDENTITY_MISMATCH,
            ),
            (
                replace(evidence, producer_kind=SnapshotProducerKind.APPROVAL_REPOSITORY),
                TransitionEvaluationDecision.INVALID,
                TransitionEvaluationReasonCode.EVIDENCE_PRODUCER_MISMATCH,
            ),
            (
                replace(
                    evidence,
                    verdict=EvidenceVerdict.INFEASIBLE,
                    ready_for_approval_1=False,
                ),
                TransitionEvaluationDecision.GATED,
                TransitionEvaluationReasonCode.EVIDENCE_NOT_SATISFIED,
            ),
        )
        for changed_evidence, decision, reason in cases:
            with self.subTest(reason=reason):
                self._assert_outcome(
                    replace(allowed, evidence_snapshots=(changed_evidence,)),
                    decision,
                    reason,
                )

        stale_non_favorable = replace(
            evidence,
            source_state=DevelopmentRunState.DRAFT,
            verdict=EvidenceVerdict.INFEASIBLE,
            ready_for_approval_1=False,
        )
        self._assert_outcome(
            replace(allowed, evidence_snapshots=(stale_non_favorable,)),
            TransitionEvaluationDecision.GATED,
            TransitionEvaluationReasonCode.EVIDENCE_STALE,
        )

    def test_all_verdict_readiness_pairs_have_one_oracle(self) -> None:
        request = self._evidence_request()
        evidence = request.evidence_snapshots[0]
        favorable = {
            EvidenceVerdict.FEASIBLE,
            EvidenceVerdict.FEASIBLE_WITH_NON_BLOCKING_FINDINGS,
        }
        for verdict in EvidenceVerdict:
            for readiness in (False, True):
                changed = replace(
                    evidence,
                    verdict=verdict,
                    ready_for_approval_1=readiness,
                )
                with self.subTest(verdict=verdict, readiness=readiness):
                    if (verdict in favorable) != readiness:
                        self._assert_outcome(
                            replace(request, evidence_snapshots=(changed,)),
                            TransitionEvaluationDecision.INVALID,
                            TransitionEvaluationReasonCode.EVIDENCE_VERDICT_READINESS_INCONSISTENT,
                        )
                    elif verdict in favorable:
                        self._assert_outcome(
                            replace(request, evidence_snapshots=(changed,)),
                            TransitionEvaluationDecision.ALLOWED,
                            TransitionEvaluationReasonCode.RULE_ALLOWED,
                        )
                    else:
                        self._assert_outcome(
                            replace(request, evidence_snapshots=(changed,)),
                            TransitionEvaluationDecision.GATED,
                            TransitionEvaluationReasonCode.EVIDENCE_NOT_SATISFIED,
                        )

    def test_evaluator_uses_only_supplied_snapshots_and_no_live_lookups(self) -> None:
        request = self._evidence_request()
        forbidden = AssertionError("forbidden live lookup invoked")
        with (
            patch("builtins.open", side_effect=forbidden) as open_call,
            patch("os.stat", side_effect=forbidden) as stat_call,
            patch("pathlib.Path.exists", side_effect=forbidden) as exists_call,
            patch("pathlib.Path.read_bytes", side_effect=forbidden) as read_call,
            patch("subprocess.run", side_effect=forbidden) as subprocess_call,
            patch("time.time", side_effect=forbidden) as time_call,
            patch("panam_development_loop.transition_policy.datetime", ParsingOnlyDatetime),
        ):
            self._assert_outcome(
                request,
                TransitionEvaluationDecision.ALLOWED,
                TransitionEvaluationReasonCode.RULE_ALLOWED,
            )
        for live_lookup in (
            open_call,
            stat_call,
            exists_call,
            read_call,
            subprocess_call,
            time_call,
        ):
            live_lookup.assert_not_called()

    def test_complete_results_are_equal_hashable_and_side_effect_free(self) -> None:
        request = self._evidence_request()
        before = replace(request)
        first = self.policy.evaluate(request)
        second = self.policy.evaluate(replace(request))
        self.assertEqual(first, second)
        self.assertEqual(hash(first), hash(second))
        self.assertEqual(request, before)
        self.assertFalse(first.side_effects_performed)
        with self.assertRaises(FrozenInstanceError):
            first.side_effects_performed = True  # type: ignore[misc]
        with self.assertRaises(ValueError):
            TransitionEvaluationResult(
                TransitionEvaluationDecision.ALLOWED,
                TransitionEvaluationReasonCode.CONTRACT_REQUIRED,
            )

    def test_approval_snapshot_surface_is_preserved_but_not_expanded(self) -> None:
        request = self._unconditional_request()
        snapshot = ApprovalSnapshot(
            snapshot_version="1",
            producer_kind=SnapshotProducerKind.APPROVAL_REPOSITORY,
            run_id=request.run.run_id,  # type: ignore[union-attr]
            milestone_id=request.milestone_contract.milestone_id,  # type: ignore[union-attr]
            milestone_contract_digest=request.run.milestone_contract_digest,  # type: ignore[union-attr]
            source_state=request.run.current_state,  # type: ignore[union-attr]
            state_version=request.run.state_version,  # type: ignore[union-attr]
            rule_id=TransitionRuleId.P1_6_DRAFT_TO_FEASIBILITY_CHECKING_V1,
            status=ApprovalSnapshotStatus.ABSENT,
            approval_binding=None,
            approval_binding_digest=None,
        )
        self._assert_outcome(
            replace(request, approval_snapshot=snapshot),
            TransitionEvaluationDecision.INVALID,
            TransitionEvaluationReasonCode.REQUEST_SURPLUS_PREREQUISITE,
        )


if __name__ == "__main__":
    unittest.main()
