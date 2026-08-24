"""Focused deterministic tests for the DL-P1.1 through DL-P1.8 slices."""

import io
import hashlib
import inspect
import json
import os
import re
import sqlite3
import subprocess
import tempfile
import threading
import urllib.request
import unittest
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import MISSING, FrozenInstanceError, fields, replace
from datetime import datetime, timedelta, timezone, tzinfo
from decimal import Decimal
from pathlib import Path
from typing import Callable, get_type_hints
from uuid import UUID
from unittest.mock import Mock, patch

import panam_development_loop.sqlite_repositories as sqlite_repository_module

from panam_development_loop import (
    ApprovalBinding,
    ApprovalKind,
    ApprovalSnapshot,
    ApprovalSnapshotStatus,
    ApprovalTargetKind,
    ApprovalValidationCode,
    ApprovalValidationError,
    ApprovalVersion,
    CommandDefinition,
    CommandDefinitionRegistry,
    DevelopmentRun,
    ContractValidationCode,
    ContractValidationError,
    ContractVersion,
    DevelopmentRunState,
    DurableCommandQueueService,
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
    QueueMutationKind,
    QueueResult,
    QueueResultCode,
    SqliteApprovalBindingRepository,
    SqliteDevelopmentRunInspectionRepository,
    SqliteMilestoneContractRepository,
    SqlitePhaseContractRepository,
    SqliteProjectPolicyRepository,
    SqliteRunStore,
    SqliteWorkflowCommandRepository,
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
    WorkflowCommand,
    WorkflowCommandEvent,
    WorkflowCommandEventKind,
    WorkflowCommandRepository,
    WorkflowCommandState,
    WorkflowNodeType,
    ValidatedCommandEnvelope,
)
from panam_development_loop.command_queue import _format_queue_timestamp
from panam_development_loop.models import (
    _canonical_json,
    _canonical_queue_payload,
    _intent_digest,
    _parse_queue_timestamp,
    _validate_payload_object,
)
from panam_development_loop.sqlite_migrations import (
    Migration,
    MigrationError,
    MigrationFailureCode,
    PRODUCTION_MIGRATIONS,
    apply_migrations,
    validate_migration_registry,
)
from panam_development_loop.__main__ import main as foundation_query_main


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
            self.assertEqual([(1, "first-at"), (2, "first-at"), (3, "first-at"), (4, "first-at")], self._ledger_snapshot(connection))
            self.assertEqual(
                [
                    "approvals",
                    "development_runs",
                    "milestone_contracts",
                    "phases",
                    "project_policies",
                    "schema_migrations",
                    "state_events",
                    "workflow_command_events",
                    "workflow_commands",
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
                [(1, "legacy-applied-at"), (2, "new-at"), (3, "new-at"), (4, "new-at")],
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
            future.execute("INSERT INTO schema_migrations VALUES(5, 'future-at')")
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
            connection.execute("INSERT INTO schema_migrations VALUES(5, 'future')")
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
        self.assertEqual([1, 2, 3, 4], [migration.version for migration in validated])
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
                ["approvals", "development_runs", "milestone_contracts", "phases", "project_policies", "schema_migrations", "state_events", "workflow_command_events", "workflow_commands"],
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
                [(1, "initialized-at"), (2, "initialized-at"), (3, "initialized-at"), (4, "initialized-at")],
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
                [(1,), (2,), (3,), (4,)],
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
            connection.execute("INSERT INTO schema_migrations VALUES(5, 'future-at')")
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


class FoundationIntegrationTest(unittest.TestCase):
    """DL-P1.10 bounded integration coverage over isolated SQLite fixtures."""

    def setUp(self) -> None:
        self._temporary_directory = tempfile.TemporaryDirectory()
        self.directory = Path(self._temporary_directory.name)
        self.database_path = self.directory / "foundation-integration.sqlite3"
        self.values = FixedValues()
        self.store = SqliteRunStore(self.database_path)
        self.service = TransitionService(
            self.store,
            clock=self.values.clock,
            id_factory=self.values.identifier,
        )
        self.contract = MilestoneContract(
            project_id="panam",
            phase_id="DL-P1",
            milestone_id="DL-P1.10",
            contract_version="1",
            objective="Exercise durable foundation integration",
            scope=("isolated SQLite", "integration tests"),
            exclusions=("production changes", "external effects"),
            acceptance_criteria=("durable transitions", "read-only queries"),
            allowed_paths=("panam_development_loop_poc_test.py",),
            forbidden_paths=("panam_development_loop",),
            verification_plan=("focused tests", "full regression"),
            stop_conditions=("scope change",),
        )
        self.phase = PhaseContract("panam", "DL-P1", "1")
        self.approval = ApprovalBinding(
            approval_id="HA1-DL-P1.10-FOUNDATION-INTEGRATION-TESTS-001",
            approval_version="1",
            approval_kind="APPROVAL_1",
            subject_id="DL-P1.10",
            subject_digest=self.contract.sha256_digest(),
            target_kind="SOURCE_REPOSITORY",
            target_id="Panam_APP",
            target_branch="phase/panam-dl-p1-1-durable-state-transition-kernel-poc",
            base_commit="4f58aecc7e49ed096a68a553b69241251c894526",
            allowed_actions=("modify",),
            allowed_paths=("panam_development_loop_poc_test.py",),
            approver_id="Human Project Owner",
            approved_at="2026-08-20T12:00:00Z",
        )

    def tearDown(self) -> None:
        self._temporary_directory.cleanup()

    @staticmethod
    def _snapshot(path: Path) -> tuple[str, ...]:
        connection = sqlite3.connect(path)
        try:
            return tuple(connection.iterdump())
        finally:
            connection.close()

    def _initialize_and_create_run(self) -> DevelopmentRun:
        self.service.initialize()
        return self.service.create_run(self.contract.sha256_digest())

    def _seed_read_only_foundation(self) -> DevelopmentRun:
        self.service.initialize()
        SqlitePhaseContractRepository(self.database_path).create(self.phase)
        SqliteMilestoneContractRepository(self.database_path).create(self.contract)
        SqliteApprovalBindingRepository(self.database_path).create(self.approval)
        run = self.service.create_run(self.contract.sha256_digest())
        first = self.service.transition(
            _unconditional_transition_request(run, self.contract)
        )
        assert first.persisted_run is not None
        second = self.service.transition(
            _evidence_transition_request(first.persisted_run, self.contract)
        )
        assert second.persisted_run is not None
        connection = sqlite3.connect(self.database_path)
        try:
            connection.execute(
                "INSERT INTO project_policies VALUES(?, ?, ?)",
                ("panam", "1", "C:\\Panam_APP"),
            )
            connection.commit()
        finally:
            connection.close()
        reopened = SqliteRunStore(self.database_path).get_run(second.persisted_run.run_id)
        assert reopened is not None
        return reopened

    def _invoke_query(self, *arguments: str) -> tuple[int, str, str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = foundation_query_main(list(arguments))
        return code, stdout.getvalue(), stderr.getvalue()

    def test_migrations_repositories_and_reopen_preserve_exact_records(self) -> None:
        self.service.initialize()
        connection = sqlite3.connect(self.database_path)
        try:
            versions = [
                row[0]
                for row in connection.execute(
                    "SELECT version FROM schema_migrations ORDER BY version"
                )
            ]
        finally:
            connection.close()
        self.assertEqual([migration.version for migration in PRODUCTION_MIGRATIONS], versions)

        SqlitePhaseContractRepository(self.database_path).create(self.phase)
        SqliteMilestoneContractRepository(self.database_path).create(self.contract)
        SqliteApprovalBindingRepository(self.database_path).create(self.approval)
        policy = ProjectPolicy("panam", "1", "C:\\Panam_APP")
        connection = sqlite3.connect(self.database_path)
        try:
            connection.execute(
                "INSERT INTO project_policies VALUES(?, ?, ?)",
                (policy.project_id, policy.policy_version.value, policy.project_root),
            )
            connection.commit()
        finally:
            connection.close()
        run = self.store.create_run(
            "reopen-run", self.contract.sha256_digest(), "2026-08-20T12:00:00Z"
        )

        self.assertEqual(
            self.phase,
            SqlitePhaseContractRepository(self.database_path).get("panam", "DL-P1"),
        )
        self.assertEqual(
            self.contract,
            SqliteMilestoneContractRepository(self.database_path).get(
                "panam", "DL-P1", "DL-P1.10"
            ),
        )
        self.assertEqual(
            self.approval,
            SqliteApprovalBindingRepository(self.database_path).get(self.approval.approval_id),
        )
        self.assertEqual(policy, SqliteProjectPolicyRepository(self.database_path).get("panam"))
        self.assertEqual(
            ProjectPolicyReadOutcome.VALID_POLICY,
            ProjectPolicyReader(SqliteProjectPolicyRepository(self.database_path)).read(
                "panam"
            ).outcome,
        )
        reopened = SqliteRunStore(self.database_path)
        self.assertEqual(run, reopened.get_run(run.run_id))
        self.assertEqual([], reopened.get_history(run.run_id))

    def test_two_registered_rules_commit_durable_ordered_history(self) -> None:
        self.service.initialize()
        SqlitePhaseContractRepository(self.database_path).create(self.phase)
        SqliteMilestoneContractRepository(self.database_path).create(self.contract)
        created_run = self.service.create_run(self.contract.sha256_digest())
        run = SqliteRunStore(self.database_path).get_run(created_run.run_id)
        reloaded_contract = SqliteMilestoneContractRepository(self.database_path).get(
            "panam", "DL-P1", "DL-P1.10"
        )
        assert run is not None
        assert reloaded_contract is not None
        first = self.service.transition(
            _unconditional_transition_request(run, reloaded_contract)
        )
        self.assertTrue(first.committed)
        self.assertEqual(TransactionalTransitionReasonCode.COMMITTED, first.reason_code)
        self.assertEqual(TransitionEvaluationDecision.ALLOWED, first.evaluation_result.decision)
        self.assertEqual(
            TransitionRuleId.P1_6_DRAFT_TO_FEASIBILITY_CHECKING_V1,
            first.evaluation_result.rule_id,
        )
        self.assertEqual(DevelopmentRunState.FEASIBILITY_CHECKING, first.persisted_run.current_state)

        assert first.persisted_run is not None
        second = self.service.transition(
            _evidence_transition_request(first.persisted_run, reloaded_contract)
        )
        self.assertTrue(second.committed)
        self.assertEqual(TransactionalTransitionReasonCode.COMMITTED, second.reason_code)
        self.assertEqual(TransitionEvaluationDecision.ALLOWED, second.evaluation_result.decision)
        self.assertEqual(
            TransitionRuleId.P1_6_FEASIBILITY_CHECKING_TO_AWAITING_EXECUTION_APPROVAL_V1,
            second.evaluation_result.rule_id,
        )
        self.assertEqual(
            DevelopmentRunState.AWAITING_EXECUTION_APPROVAL,
            second.persisted_run.current_state,
        )

        reopened = SqliteRunStore(self.database_path)
        self.assertEqual(second.persisted_run, reopened.get_run(run.run_id))
        history = reopened.get_history(run.run_id)
        self.assertEqual([1, 2], [event.state_version for event in history])
        self.assertEqual(
            [DevelopmentRunState.DRAFT, DevelopmentRunState.FEASIBILITY_CHECKING],
            [event.from_state for event in history],
        )
        self.assertEqual(
            [
                DevelopmentRunState.FEASIBILITY_CHECKING,
                DevelopmentRunState.AWAITING_EXECUTION_APPROVAL,
            ],
            [event.to_state for event in history],
        )
        self.assertEqual(
            [TransitionEvaluationReasonCode.RULE_ALLOWED.value] * 2,
            [event.transition_reason for event in history],
        )

    def test_gated_denied_invalid_and_unsupported_requests_have_zero_writes(self) -> None:
        run = self._initialize_and_create_run()
        gated_run = self.service.transition(
            _unconditional_transition_request(run, self.contract)
        )
        assert gated_run.persisted_run is not None
        before = self._snapshot(self.database_path)
        requests = (
            (
                replace(
                    _evidence_transition_request(gated_run.persisted_run, self.contract),
                    evidence_snapshots=(),
                ),
                TransitionEvaluationDecision.GATED,
                TransitionEvaluationReasonCode.EVIDENCE_REQUIRED,
            ),
            (
                TransitionEvaluationRequest(
                    evaluation_version="1",
                    run=run,
                    rule_id=TransitionRuleId.P1_6_DRAFT_TO_FEASIBILITY_CHECKING_V1.value,
                    requested_state=DevelopmentRunState.AWAITING_EXECUTION_APPROVAL,
                    edge_type=WorkflowEdgeType.UNCONDITIONAL,
                ),
                TransitionEvaluationDecision.DENIED,
                TransitionEvaluationReasonCode.REGISTRY_NO_REGISTERED_TRANSITION,
            ),
            (
                object(),
                TransitionEvaluationDecision.INVALID,
                TransitionEvaluationReasonCode.REQUEST_NOT_EVALUATION_REQUEST,
            ),
            (
                _unconditional_transition_request(
                    run, self.contract, evaluation_version="2"
                ),
                TransitionEvaluationDecision.UNSUPPORTED,
                TransitionEvaluationReasonCode.EVALUATION_VERSION_UNSUPPORTED,
            ),
        )
        with patch.object(
            self.store, "_connect", side_effect=AssertionError("persistence invoked")
        ) as connect:
            for request, decision, reason in requests:
                with self.subTest(decision=decision):
                    result = self.service.transition(request)
                    self.assertFalse(result.committed)
                    self.assertEqual(
                        TransactionalTransitionReasonCode.EVALUATION_NOT_ALLOWED,
                        result.reason_code,
                    )
                    self.assertEqual(decision, result.evaluation_result.decision)
                    self.assertEqual(reason, result.evaluation_result.reason_code)
                    self.assertFalse(result.evaluation_result.side_effects_performed)
        connect.assert_not_called()
        self.assertEqual(before, self._snapshot(self.database_path))

    def test_stale_and_compare_and_swap_rejections_leave_no_partial_write(self) -> None:
        run = self._initialize_and_create_run()
        connection = sqlite3.connect(self.database_path)
        try:
            connection.execute(
                "UPDATE development_runs SET updated_at=? WHERE run_id=?",
                ("2026-08-20T12:03:00Z", run.run_id),
            )
            connection.commit()
        finally:
            connection.close()
        before_stale = self._snapshot(self.database_path)
        stale = self.service.transition(_unconditional_transition_request(run, self.contract))
        self.assertFalse(stale.committed)
        self.assertEqual(
            TransactionalTransitionReasonCode.STALE_PERSISTED_RUN, stale.reason_code
        )
        self.assertEqual(TransitionEvaluationDecision.ALLOWED, stale.evaluation_result.decision)
        self.assertEqual(before_stale, self._snapshot(self.database_path))
        self.assertEqual([], self.store.get_history(run.run_id))

        current = self.store.get_run(run.run_id)
        assert current is not None
        proxy = _ConnectionProxy(self.store._connect(), zero_update=True)
        before_compare_and_swap = self._snapshot(self.database_path)
        with patch.object(self.store, "_connect", return_value=proxy):
            compare_and_swap = self.service.transition(
                _unconditional_transition_request(current, self.contract)
            )
        self.assertFalse(compare_and_swap.committed)
        self.assertEqual(
            TransactionalTransitionReasonCode.STALE_PERSISTED_RUN,
            compare_and_swap.reason_code,
        )
        self.assertEqual(
            TransitionEvaluationDecision.ALLOWED,
            compare_and_swap.evaluation_result.decision,
        )
        self.assertEqual(1, proxy.rollback_calls)
        self.assertEqual(1, proxy.close_calls)
        self.assertEqual(before_compare_and_swap, self._snapshot(self.database_path))
        self.assertEqual([], self.store.get_history(run.run_id))

    def test_sqlite_event_constraint_failure_rolls_back_state_update(self) -> None:
        self.service.initialize()
        run = self.store.create_run(
            "constraint-run", self.contract.sha256_digest(), "2026-08-20T12:00:00Z"
        )
        connection = sqlite3.connect(self.database_path)
        try:
            connection.execute(
                """
                INSERT INTO state_events(
                    event_id, run_id, from_state, to_state, transition_reason,
                    occurred_at, state_version
                ) VALUES(?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "run-1",
                    run.run_id,
                    DevelopmentRunState.DRAFT.value,
                    DevelopmentRunState.FEASIBILITY_CHECKING.value,
                    TransitionEvaluationReasonCode.RULE_ALLOWED.value,
                    "2026-08-20T12:01:00Z",
                    99,
                ),
            )
            connection.commit()
        finally:
            connection.close()
        before = self._snapshot(self.database_path)

        with self.assertRaises(sqlite3.IntegrityError):
            self.service.transition(_unconditional_transition_request(run, self.contract))

        self.assertEqual(before, self._snapshot(self.database_path))
        self.assertEqual(run, self.store.get_run(run.run_id))
        self.assertEqual([99], [event.state_version for event in self.store.get_history(run.run_id)])

    def test_p1_8_and_p1_9_read_paths_are_integration_read_only(self) -> None:
        run = self._seed_read_only_foundation()
        before = self._snapshot(self.database_path)
        phase_repository = SqlitePhaseContractRepository(self.database_path)
        milestone_repository = SqliteMilestoneContractRepository(self.database_path)
        approval_repository = SqliteApprovalBindingRepository(self.database_path)
        policy_repository = SqliteProjectPolicyRepository(self.database_path)
        inspection_repository = SqliteDevelopmentRunInspectionRepository(self.database_path)
        with (
            patch.object(SqliteRunStore, "initialize", side_effect=AssertionError("bootstrap invoked")) as initialize,
            patch("panam_development_loop.sqlite_migrations.initialize_database", side_effect=AssertionError("migration invoked")) as initialize_database,
            patch.object(SqlitePhaseContractRepository, "create", side_effect=AssertionError("phase create invoked")) as phase_create,
            patch.object(SqliteMilestoneContractRepository, "create", side_effect=AssertionError("milestone create invoked")) as milestone_create,
            patch.object(SqliteApprovalBindingRepository, "create", side_effect=AssertionError("approval create invoked")) as approval_create,
            patch.object(TransitionService, "transition", side_effect=AssertionError("transition invoked")) as transition,
            patch("subprocess.run", side_effect=AssertionError("subprocess invoked")) as subprocess_run,
            patch("os.system", side_effect=AssertionError("os system invoked")) as os_system,
            patch("urllib.request.urlopen", side_effect=AssertionError("external request invoked")) as urlopen,
        ):
            self.assertEqual(self.phase, phase_repository.get("panam", "DL-P1"))
            self.assertEqual(
                self.contract,
                milestone_repository.get("panam", "DL-P1", "DL-P1.10"),
            )
            self.assertEqual(self.approval, approval_repository.get(self.approval.approval_id))
            self.assertEqual(ProjectPolicy("panam", "1", "C:\\Panam_APP"), policy_repository.get("panam"))
            self.assertEqual(run, inspection_repository.get_run(run.run_id))
            self.assertEqual([1, 2], [event.state_version for event in inspection_repository.get_history(run.run_id)])
            for arguments in (
                ("phase", "panam", "DL-P1"),
                ("milestone", "panam", "DL-P1", "DL-P1.10"),
                ("approval", self.approval.approval_id),
                ("run", run.run_id),
                ("project-policy", "panam"),
            ):
                code, _, stderr = self._invoke_query("--db", str(self.database_path), *arguments)
                self.assertEqual((0, ""), (code, stderr))
        self.assertEqual(before, self._snapshot(self.database_path))
        for sentinel in (
            initialize,
            initialize_database,
            phase_create,
            milestone_create,
            approval_create,
            transition,
            subprocess_run,
            os_system,
            urlopen,
        ):
            sentinel.assert_not_called()


class FoundationQueryCliTest(unittest.TestCase):
    """Focused DL-P1.9 CLI/query behavior using isolated fixture storage."""

    def setUp(self) -> None:
        self._temporary_directory = tempfile.TemporaryDirectory()
        self.directory = Path(self._temporary_directory.name)
        self.database_path = self.directory / "foundation.sqlite3"
        SqliteRunStore(self.database_path).initialize("initialized-at")

        self.phase = PhaseContract("panam", "phase-1", "1")
        self.milestone = MilestoneContract(
            project_id="panam",
            phase_id="phase-1",
            milestone_id="milestone-1",
            contract_version="1",
            objective="Inspect durable foundation",
            scope=("read-only", "query"),
            exclusions=("writes",),
            acceptance_criteria=("deterministic",),
            allowed_paths=("panam_development_loop/foundation_query.py",),
            forbidden_paths=("runtime",),
            verification_plan=("focused", "regression"),
            stop_conditions=("scope change",),
        )
        self.approval = ApprovalBinding(
            approval_id="approval-1",
            approval_version="1",
            approval_kind="APPROVAL_1",
            subject_id="DL-P1.9",
            subject_digest="a" * 64,
            target_kind="SOURCE_REPOSITORY",
            target_id="Panam_APP",
            target_branch="phase/panam",
            base_commit="base",
            allowed_actions=("inspect",),
            allowed_paths=("panam_development_loop/foundation_query.py",),
            approver_id="human",
            approved_at="2026-08-19T12:00:00Z",
        )
        SqlitePhaseContractRepository(self.database_path).create(self.phase)
        SqliteMilestoneContractRepository(self.database_path).create(self.milestone)
        SqliteApprovalBindingRepository(self.database_path).create(self.approval)
        self.run = SqliteRunStore(self.database_path).create_run(
            "run-1",
            "b" * 64,
            "created-at",
        )
        connection = sqlite3.connect(self.database_path)
        try:
            connection.execute(
                "UPDATE development_runs SET current_state=?, state_version=?, updated_at=? WHERE run_id=?",
                ("AWAITING_EXECUTION_APPROVAL", 2, "updated-at", "run-1"),
            )
            connection.execute(
                "INSERT INTO state_events VALUES(?, ?, ?, ?, ?, ?, ?)",
                (
                    "event-2",
                    "run-1",
                    "FEASIBILITY_CHECKING",
                    "AWAITING_EXECUTION_APPROVAL",
                    "RULE_ALLOWED",
                    "event-two-at",
                    2,
                ),
            )
            connection.execute(
                "INSERT INTO state_events VALUES(?, ?, ?, ?, ?, ?, ?)",
                (
                    "event-1",
                    "run-1",
                    "DRAFT",
                    "FEASIBILITY_CHECKING",
                    "RULE_ALLOWED",
                    "event-one-at",
                    1,
                ),
            )
            connection.execute(
                "INSERT INTO project_policies VALUES(?, ?, ?)",
                ("panam", "1", "C:\\workspace\\panam"),
            )
            connection.execute(
                "INSERT INTO project_policies VALUES(?, ?, ?)",
                ("invalid-policy", "2", "C:\\workspace\\invalid"),
            )
            connection.commit()
        finally:
            connection.close()

    def tearDown(self) -> None:
        self._temporary_directory.cleanup()

    @staticmethod
    def _snapshot(path: Path) -> tuple[str, ...]:
        connection = sqlite3.connect(path)
        try:
            return tuple(connection.iterdump())
        finally:
            connection.close()

    def _invoke(self, *arguments: str) -> tuple[int, str, str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = foundation_query_main(list(arguments))
        return code, stdout.getvalue(), stderr.getvalue()

    def _base(self, *arguments: str) -> tuple[str, ...]:
        return ("--db", str(self.database_path), *arguments)

    def test_help_and_all_successful_query_operations(self) -> None:
        stdout = io.StringIO()
        with redirect_stdout(stdout), self.assertRaises(SystemExit) as raised:
            foundation_query_main(["--help"])
        self.assertEqual(0, raised.exception.code)
        self.assertIn("project-policy", stdout.getvalue())

        phase_code, phase_text, phase_error = self._invoke(*self._base("phase", "panam", "phase-1"))
        self.assertEqual((0, ""), (phase_code, phase_error))
        self.assertEqual(
            ["project_id: panam", "phase_id: phase-1", "contract_version: 1"],
            phase_text.splitlines()[:3],
        )

        milestone_code, milestone_text, milestone_error = self._invoke(
            *self._base("milestone", "panam", "phase-1", "milestone-1")
        )
        self.assertEqual((0, ""), (milestone_code, milestone_error))
        self.assertIn("scope:\n- query\n- read-only", milestone_text)

        approval_code, approval_text, approval_error = self._invoke(
            *self._base("approval", "approval-1")
        )
        self.assertEqual((0, ""), (approval_code, approval_error))
        self.assertIn("approval_kind: APPROVAL_1", approval_text)

        run_code, run_text, run_error = self._invoke(*self._base("run", "run-1"))
        self.assertEqual((0, ""), (run_code, run_error))
        self.assertLess(run_text.index("- event_id: event-1"), run_text.index("- event_id: event-2"))

        policy_code, policy_text, policy_error = self._invoke(
            *self._base("project-policy", "panam")
        )
        self.assertEqual((0, ""), (policy_code, policy_error))
        self.assertEqual("result_category: VALID_POLICY", policy_text.splitlines()[0])

    def test_json_is_deterministic_and_semantically_equivalent_to_text(self) -> None:
        text_code, text_output, text_error = self._invoke(*self._base("phase", "panam", "phase-1"))
        json_code, json_output, json_error = self._invoke(
            *self._base("phase", "panam", "phase-1", "--json")
        )
        repeated_code, repeated_json, repeated_error = self._invoke(
            *self._base("--json", "phase", "panam", "phase-1")
        )
        self.assertEqual((0, "", 0, "", 0, ""), (
            text_code,
            text_error,
            json_code,
            json_error,
            repeated_code,
            repeated_error,
        ))
        self.assertEqual(json_output, repeated_json)
        self.assertTrue(json_output.endswith("\n"))
        payload = json.loads(json_output)
        self.assertEqual(
            ["project_id", "phase_id", "contract_version", "contract_digest"],
            list(payload),
        )
        self.assertIn(f"project_id: {payload['project_id']}", text_output)
        self.assertIn(f"contract_digest: {payload['contract_digest']}", text_output)

    def test_project_policy_result_mapping(self) -> None:
        valid = self._invoke(*self._base("project-policy", "panam"))
        missing = self._invoke(*self._base("project-policy", "absent"))
        invalid = self._invoke(*self._base("project-policy", "invalid-policy"))
        self.assertEqual((0, ""), (valid[0], valid[2]))
        self.assertEqual((3, "error_code: NOT_FOUND\n"), (missing[0], missing[2]))
        self.assertEqual(
            (4, "error_code: INVALID_PERSISTED_FOUNDATION_DATA\n"),
            (invalid[0], invalid[2]),
        )

        missing_database = self.directory / "missing.sqlite3"
        code, stdout, stderr = self._invoke("--db", str(missing_database), "project-policy", "panam")
        self.assertEqual((5, "", "error_code: STORAGE_FAILURE\n"), (code, stdout, stderr))
        self.assertFalse(missing_database.exists())

    def test_invalid_input_not_found_and_invalid_persisted_data_mapping(self) -> None:
        code, stdout, stderr = self._invoke(*self._base("phase", "", "phase-1"))
        self.assertEqual((2, "", "error_code: INVALID_CLI_INPUT\n"), (code, stdout, stderr))

        code, stdout, stderr = self._invoke(*self._base("approval", "missing"))
        self.assertEqual((3, "", "error_code: NOT_FOUND\n"), (code, stdout, stderr))

        connection = sqlite3.connect(self.database_path)
        try:
            connection.execute("UPDATE phases SET contract_digest=? WHERE project_id=? AND phase_id=?", ("0" * 64, "panam", "phase-1"))
            connection.commit()
        finally:
            connection.close()
        code, stdout, stderr = self._invoke(*self._base("phase", "panam", "phase-1"))
        self.assertEqual(
            (4, "", "error_code: INVALID_PERSISTED_FOUNDATION_DATA\n"),
            (code, stdout, stderr),
        )

    def test_query_path_is_read_only_and_never_initializes_or_transitions(self) -> None:
        before = self._snapshot(self.database_path)
        with (
            patch.object(SqliteRunStore, "initialize", side_effect=AssertionError("initialize invoked")) as initialize,
            patch("panam_development_loop.sqlite_migrations.initialize_database", side_effect=AssertionError("migration invoked")) as initialize_database,
            patch.object(TransitionService, "transition", side_effect=AssertionError("transition invoked")) as transition,
            patch.object(SqliteApprovalBindingRepository, "create", side_effect=AssertionError("approval mutation invoked")) as approval_create,
            patch("subprocess.run", side_effect=AssertionError("subprocess invoked")) as subprocess_run,
        ):
            for command in (
                self._base("phase", "panam", "phase-1"),
                self._base("milestone", "panam", "phase-1", "milestone-1"),
                self._base("approval", "approval-1"),
                self._base("run", "run-1"),
                self._base("project-policy", "panam"),
            ):
                code, _, stderr = self._invoke(*command)
                self.assertEqual((0, ""), (code, stderr))
        self.assertEqual(before, self._snapshot(self.database_path))
        for sentinel in (initialize, initialize_database, transition, approval_create, subprocess_run):
            sentinel.assert_not_called()

    def test_json_error_output_is_one_value(self) -> None:
        code, stdout, stderr = self._invoke(*self._base("project-policy", "absent", "--json"))
        self.assertEqual((3, ""), (code, stdout))
        self.assertEqual({"error_code": "NOT_FOUND"}, json.loads(stderr))
        self.assertTrue(stderr.endswith("\n"))


def _queue_payload_validator(payload: dict[str, object]) -> str:
    return _canonical_json(payload)


def _queue_definition() -> CommandDefinition:
    return CommandDefinition(
        command_kind="TEST_COMMAND",
        command_schema_version=1,
        required_keys=("value",),
        optional_keys=("note",),
        nullable_keys=("note",),
        payload_validator=_queue_payload_validator,
    )


def _queue_envelope(
    payload: dict[str, object] | None = None,
    *,
    project_id: str = "panam",
    idempotency_key: str = "key-1",
    priority: int = 0,
) -> ValidatedCommandEnvelope:
    value = {"value": 1} if payload is None else payload
    payload_json = _canonical_json(value)
    digest_payload = {
        "command_kind": "TEST_COMMAND",
        "command_schema_version": 1,
        "development_run_id": None,
        "payload": value,
        "phase_id": None,
        "priority": priority,
        "project_id": project_id,
    }
    digest = hashlib.sha256(
        _canonical_json(digest_payload).encode("utf-8")
    ).hexdigest()
    return ValidatedCommandEnvelope(
        project_id=project_id,
        development_run_id=None,
        phase_id=None,
        command_kind="TEST_COMMAND",
        command_schema_version=1,
        payload_json=payload_json,
        intent_digest=digest,
        idempotency_key=idempotency_key,
        priority=priority,
    )


def _pending_queue_command() -> WorkflowCommand:
    envelope = _queue_envelope()
    return WorkflowCommand(
        1,
        "00000000-0000-0000-0000-000000000001",
        envelope.project_id,
        None,
        None,
        envelope.command_kind,
        envelope.command_schema_version,
        envelope.payload_json,
        envelope.intent_digest,
        envelope.idempotency_key,
        envelope.priority,
        WorkflowCommandState.PENDING,
        1,
        0,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        "2026-08-24T12:00:00.000000Z",
        "2026-08-24T12:00:00.000000Z",
        None,
        None,
    )


def _enqueue_event(command: WorkflowCommand) -> WorkflowCommandEvent:
    return WorkflowCommandEvent(
        1,
        "00000000-0000-0000-0000-000000000002",
        command.command_id,
        WorkflowCommandEventKind.ENQUEUED,
        None,
        WorkflowCommandState.PENDING,
        None,
        1,
        "tester",
        command.created_at,
        None,
        None,
        0,
        None,
    )


class QueueProviders:
    def __init__(self) -> None:
        self.current = datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)
        self.clock_calls = 0
        self.id_calls = 0

    def clock(self) -> datetime:
        self.clock_calls += 1
        return self.current

    def identifier(self) -> str:
        self.id_calls += 1
        return str(UUID(int=self.id_calls))


class DeterministicFallbackTimezone(tzinfo):
    def utcoffset(self, value: datetime | None) -> timedelta | None:
        if value is None:
            return timedelta(hours=2)
        return timedelta(hours=2 if value.hour < 3 else 1)

    def dst(self, value: datetime | None) -> timedelta | None:
        return timedelta(0)


class WorkflowCommandPublicContractTest(unittest.TestCase):
    def test_exact_enum_vocabularies(self) -> None:
        expected = {
            WorkflowCommandState: ("PENDING", "CLAIMED", "RUNNING", "SUCCEEDED", "FAILED", "CANCELLED"),
            WorkflowCommandEventKind: ("ENQUEUED", "CLAIMED", "LEASE_RENEWED", "STARTED", "CANCELLATION_REQUESTED", "CANCELLED", "EXPIRED_CLAIM_RELEASED", "SUCCEEDED", "FAILED"),
            QueueMutationKind: ("ENQUEUE", "CLAIM_NEXT", "RENEW_LEASE", "MARK_RUNNING", "REQUEST_CANCELLATION", "ACKNOWLEDGE_CANCELLATION", "MARK_SUCCEEDED", "MARK_FAILED", "RECOVER_EXPIRED_CLAIM"),
            QueueResultCode: ("APPLIED", "FOUND", "NOT_FOUND", "LISTED", "HISTORY_RETURNED", "NO_ELIGIBLE_COMMAND", "EXISTING_IDENTICAL", "IDEMPOTENCY_CONFLICT", "CAS_CONFLICT", "LEASE_OWNER_MISMATCH", "LEASE_EXPIRED", "LEASE_NOT_EXPIRED", "CANCELLATION_ALREADY_REQUESTED", "CANCELLATION_NOT_REQUESTED", "TERMINAL_OBSERVED", "TRANSIENT_CONTENTION", "RECONCILIATION_REQUIRED"),
        }
        for enum_type, names in expected.items():
            with self.subTest(enum=enum_type.__name__):
                self.assertEqual(names, tuple(item.name for item in enum_type))
                self.assertEqual(names, tuple(item.value for item in enum_type))

    def test_exact_public_dataclass_fields_defaults_and_frozen_behavior(self) -> None:
        expected = {
            WorkflowCommand: ("queue_sequence", "command_id", "project_id", "development_run_id", "phase_id", "command_kind", "command_schema_version", "payload_json", "intent_digest", "idempotency_key", "priority", "state", "state_version", "claim_count", "lease_owner", "lease_acquired_at", "lease_expires_at", "cancellation_requested_at", "cancellation_requested_by", "cancellation_reason_code", "failure_code", "created_at", "updated_at", "started_at", "completed_at"),
            WorkflowCommandEvent: ("event_sequence", "event_id", "command_id", "event_kind", "prior_state", "next_state", "prior_state_version", "next_state_version", "actor_id", "occurred_at", "lease_owner", "lease_expires_at", "claim_count", "reason_code"),
            ValidatedCommandEnvelope: ("project_id", "development_run_id", "phase_id", "command_kind", "command_schema_version", "payload_json", "intent_digest", "idempotency_key", "priority"),
            QueueResult: ("code", "mutation_kind", "command", "event", "commands", "events"),
            CommandDefinition: ("command_kind", "command_schema_version", "required_keys", "optional_keys", "nullable_keys", "payload_validator"),
        }
        for model, names in expected.items():
            with self.subTest(model=model.__name__):
                self.assertEqual(names, tuple(item.name for item in fields(model)))
        command = _pending_queue_command()
        self.assertEqual(command, _pending_queue_command())
        self.assertIsInstance(hash(command), int)
        with self.assertRaises(FrozenInstanceError):
            command.state = WorkflowCommandState.CLAIMED  # type: ignore[misc]
        self.assertEqual(
            (QueueResultCode.NOT_FOUND, None, None, None, (), ()),
            tuple(getattr(QueueResult(QueueResultCode.NOT_FOUND), item.name) for item in fields(QueueResult)),
        )

    def test_models_require_exact_enums_and_validate_cross_fields(self) -> None:
        command = _pending_queue_command()
        with self.assertRaises(TypeError):
            replace(command, state="PENDING")
        with self.assertRaises(ValueError):
            replace(command, state=WorkflowCommandState.CLAIMED)
        event = _enqueue_event(command)
        with self.assertRaises(TypeError):
            replace(event, event_kind="ENQUEUED")
        with self.assertRaises(ValueError):
            replace(event, claim_count=1)

    def test_timestamp_and_payload_boundaries(self) -> None:
        self.assertEqual(
            "2026-08-24T10:00:00.000000Z",
            _format_queue_timestamp(
                datetime(2026, 8, 24, 12, 0, tzinfo=timezone(timedelta(hours=2)))
            ),
        )
        with self.assertRaises(ValueError):
            _format_queue_timestamp(datetime(2026, 8, 24, 12, 0))
        with self.assertRaises(ValueError):
            _queue_envelope({"ShElL": "echo"})
        with self.assertRaises(TypeError):
            _queue_envelope({"value": 1.5})
        with self.assertRaises(ValueError):
            _queue_envelope({"value": "x" * 4097})
        _queue_envelope({"value": "x" * 4096})

    def test_envelope_is_canonical_digest_bound_and_deeply_immutable(self) -> None:
        payload = {"value": 1, "note": "é"}
        envelope = _queue_envelope(payload)
        original_json = envelope.payload_json
        payload["value"] = 2
        self.assertEqual(original_json, envelope.payload_json)
        self.assertEqual('{"note":"é","value":1}', envelope.payload_json)
        with self.assertRaises(ValueError):
            replace(envelope, payload_json='{"value":1, "note":"é"}')
        with self.assertRaises(ValueError):
            replace(envelope, intent_digest="0" * 64)

    def test_queue_result_shapes_and_event_binding(self) -> None:
        command = _pending_queue_command()
        event = _enqueue_event(command)
        QueueResult(QueueResultCode.APPLIED, QueueMutationKind.ENQUEUE, command, event)
        QueueResult(QueueResultCode.FOUND, command=command)
        QueueResult(QueueResultCode.LISTED, commands=(command,))
        QueueResult(QueueResultCode.HISTORY_RETURNED, events=(event,))
        QueueResult(QueueResultCode.EXISTING_IDENTICAL, QueueMutationKind.ENQUEUE, command=command)
        QueueResult(QueueResultCode.NO_ELIGIBLE_COMMAND, QueueMutationKind.CLAIM_NEXT)
        for invalid in (
            lambda: QueueResult(QueueResultCode.APPLIED, QueueMutationKind.ENQUEUE),
            lambda: QueueResult(QueueResultCode.FOUND),
            lambda: QueueResult(QueueResultCode.HISTORY_RETURNED),
            lambda: QueueResult(QueueResultCode.NO_ELIGIBLE_COMMAND, QueueMutationKind.ENQUEUE),
            lambda: QueueResult(QueueResultCode.EXISTING_IDENTICAL, QueueMutationKind.ENQUEUE, command=command, event=event),
        ):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    invalid()

    def test_exact_public_field_types_defaults_and_value_semantics(self) -> None:
        expected_types = {
            WorkflowCommand: (
                int, str, str, str | None, str | None, str, int, str, str, str,
                int, WorkflowCommandState, int, int, str | None, str | None,
                str | None, str | None, str | None, str | None, str | None, str,
                str, str | None, str | None,
            ),
            WorkflowCommandEvent: (
                int, str, str, WorkflowCommandEventKind, WorkflowCommandState | None,
                WorkflowCommandState, int | None, int, str, str, str | None,
                str | None, int, str | None,
            ),
            ValidatedCommandEnvelope: (
                str, str | None, str | None, str, int, str, str, str, int,
            ),
            QueueResult: (
                QueueResultCode, QueueMutationKind | None, WorkflowCommand | None,
                WorkflowCommandEvent | None, tuple[WorkflowCommand, ...],
                tuple[WorkflowCommandEvent, ...],
            ),
            CommandDefinition: (
                str, int, tuple[str, ...], tuple[str, ...], tuple[str, ...],
                Callable[[dict[str, object]], str],
            ),
        }
        for model, annotations in expected_types.items():
            with self.subTest(model=model.__name__):
                hints = get_type_hints(model)
                self.assertEqual(annotations, tuple(hints[item.name] for item in fields(model)))
                defaults = tuple(item.default for item in fields(model))
                if model is QueueResult:
                    self.assertEqual((MISSING, None, None, None, (), ()), defaults)
                else:
                    self.assertTrue(all(value is MISSING for value in defaults))
        values = (
            _pending_queue_command(),
            _enqueue_event(_pending_queue_command()),
            _queue_envelope(),
            QueueResult(QueueResultCode.NOT_FOUND),
            _queue_definition(),
        )
        for value in values:
            with self.subTest(value=type(value).__name__):
                self.assertEqual(value, replace(value))
                with self.assertRaises(FrozenInstanceError):
                    setattr(value, fields(value)[0].name, None)
        for value in values[:3]:
            self.assertIsInstance(hash(value), int)

    def test_timestamp_parser_width_calendar_round_trip_and_order(self) -> None:
        valid = (
            "0001-01-01T00:00:00.000000Z",
            "2026-08-24T12:00:00.000001Z",
            "2026-08-24T12:01:00.000000Z",
            "9999-12-31T23:59:59.999999Z",
        )
        parsed = tuple(_parse_queue_timestamp(value, "time") for value in valid)
        self.assertEqual(tuple(sorted(valid)), valid)
        self.assertEqual(tuple(sorted(parsed)), parsed)
        for value, instant in zip(valid, parsed):
            self.assertEqual(27, len(value.encode("ascii")))
            self.assertEqual(value, _format_queue_timestamp(instant))
        for invalid in (
            "2026-08-24T12:00:00Z",
            "2026-08-24T12:00:00.00000Z",
            "2026-08-24T12:00:00.000000+00:00",
            "2026/08/24T12:00:00.000000Z",
            "2026-02-30T12:00:00.000000Z",
            "",
        ):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                _parse_queue_timestamp(invalid, "time")
        with self.assertRaises(TypeError):
            _parse_queue_timestamp(datetime.now(timezone.utc), "time")

        class OverflowingTimezone(tzinfo):
            def utcoffset(self, value: datetime | None) -> timedelta | None:
                raise OverflowError("invalid offset")

            def dst(self, value: datetime | None) -> timedelta | None:
                return None

        with self.assertRaises(ValueError):
            _format_queue_timestamp(datetime(2026, 8, 24, tzinfo=OverflowingTimezone()))

    def test_generic_payload_complete_boundary_matrix(self) -> None:
        for valid in (
            {},
            {f"k{index}": index for index in range(64)},
            {"value": [0] * 256},
            {"value": -(2**63)},
            {"value": 2**63 - 1},
            {"value": True, "nothing": None, "unicode": "é"},
        ):
            with self.subTest(valid=type(valid).__name__):
                self.assertIs(valid, _validate_payload_object(valid))
        nested: object = 1
        for _ in range(7):
            nested = {"nested": nested}
        _validate_payload_object(nested)

        node_limit = {f"k{index}": [0] * (14 if index == 63 else 15) for index in range(64)}
        _validate_payload_object(node_limit)
        too_many_nodes = {f"k{index}": [0] * 15 for index in range(64)}
        invalid_values = (
            {f"k{index}": index for index in range(65)},
            {"value": [0] * 257},
            {"value": -(2**63) - 1},
            {"value": 2**63},
            {"value": 1.0},
            {"value": float("nan")},
            {"value": float("inf")},
            {"value": Decimal("1")},
            {"value": b"x"},
            {"value": (1,)},
            {"value": {1}},
            {"value": "\ud800"},
            {"": 1},
            {"a" * 65: 1},
            too_many_nodes,
        )
        for invalid in invalid_values:
            with self.subTest(invalid=repr(invalid)[:60]), self.assertRaises((TypeError, ValueError, UnicodeError)):
                _validate_payload_object(invalid)
        too_deep: object = 1
        for _ in range(8):
            too_deep = {"nested": too_deep}
        with self.assertRaises(ValueError):
            _validate_payload_object(too_deep)
        for reserved in (
            "shell", "command_line", "argv", "executable", "executable_path",
            "script", "python_code", "import_target", "callback", "sql", "url",
            "http_request", "approved", "authorized",
        ):
            for key in (reserved, reserved.upper(), reserved.title()):
                with self.subTest(key=key), self.assertRaises(ValueError):
                    _validate_payload_object({"safe": {key: "x"}})

    def test_canonical_payload_size_unicode_and_digest_oracles(self) -> None:
        payload = {"z": None, "a": True, "n": -1, "unicode": "é"}
        expected_json = '{"a":true,"n":-1,"unicode":"é","z":null}'
        self.assertEqual(expected_json.encode("utf-8"), _canonical_queue_payload(payload).encode("utf-8"))
        digest_args = dict(
            project_id="panam", development_run_id=None, phase_id=None,
            command_kind="TEST_COMMAND", command_schema_version=1,
            payload=payload, priority=0,
        )
        expected_digest = hashlib.sha256(
            _canonical_json(
                {
                    "command_kind": "TEST_COMMAND", "command_schema_version": 1,
                    "development_run_id": None, "payload": payload, "phase_id": None,
                    "priority": 0, "project_id": "panam",
                }
            ).encode("utf-8")
        ).hexdigest()
        self.assertEqual(expected_digest, _intent_digest(**digest_args))
        variants = (
            {**digest_args, "project_id": "other"},
            {**digest_args, "development_run_id": "run"},
            {**digest_args, "phase_id": "phase"},
            {**digest_args, "command_kind": "OTHER"},
            {**digest_args, "command_schema_version": 2},
            {**digest_args, "payload": {"z": None}},
            {**digest_args, "priority": 1},
        )
        self.assertEqual(7, len({ _intent_digest(**value) for value in variants }))
        self.assertNotEqual(
            _canonical_queue_payload({"value": None}),
            _canonical_queue_payload({}),
        )
        self.assertNotEqual(
            _canonical_queue_payload({"value": "é"}),
            _canonical_queue_payload({"value": "e\u0301"}),
        )

        empty = {f"k{index:02d}": "" for index in range(16)}
        overhead = len(_canonical_queue_payload(empty).encode("utf-8"))
        remaining = 65_536 - overhead
        exact: dict[str, object] = {}
        for index in range(16):
            length = min(4096, remaining)
            exact[f"k{index:02d}"] = "x" * length
            remaining -= length
        self.assertEqual(0, remaining)
        self.assertEqual(65_536, len(_canonical_queue_payload(exact).encode("utf-8")))
        oversized = dict(exact)
        final_key = next(key for key, value in oversized.items() if len(value) < 4096)
        oversized[final_key] += "x"
        with self.assertRaises(ValueError):
            _canonical_queue_payload(oversized)

    def test_applied_result_binds_claim_count_and_operation_facts(self) -> None:
        pending = _pending_queue_command()
        claimed = replace(
            pending,
            state=WorkflowCommandState.CLAIMED,
            state_version=2,
            claim_count=1,
            lease_owner="worker",
            lease_acquired_at="2026-08-24T12:00:01.000000Z",
            lease_expires_at="2026-08-24T12:00:11.000000Z",
            updated_at="2026-08-24T12:00:01.000000Z",
        )
        event = WorkflowCommandEvent(
            2, "00000000-0000-0000-0000-000000000003", claimed.command_id,
            WorkflowCommandEventKind.CLAIMED, WorkflowCommandState.PENDING,
            WorkflowCommandState.CLAIMED, 1, 2, "worker", claimed.updated_at,
            "worker", claimed.lease_expires_at, 1, None,
        )
        QueueResult(QueueResultCode.APPLIED, QueueMutationKind.CLAIM_NEXT, claimed, event)
        with self.assertRaises(ValueError):
            QueueResult(
                QueueResultCode.APPLIED,
                QueueMutationKind.CLAIM_NEXT,
                replace(claimed, claim_count=2),
                event,
            )
        with self.assertRaises(ValueError):
            QueueResult(
                QueueResultCode.APPLIED,
                QueueMutationKind.CLAIM_NEXT,
                replace(
                    claimed,
                    lease_acquired_at="2026-08-24T12:00:00.000000Z",
                ),
                event,
            )

    def test_identifier_code_and_integer_boundaries(self) -> None:
        _queue_envelope(project_id="é" * 128, idempotency_key="A" * 128, priority=100)
        invalid_envelopes = (
            lambda: _queue_envelope(project_id="é" * 129),
            lambda: _queue_envelope(idempotency_key="A" * 129),
            lambda: _queue_envelope(idempotency_key="-bad"),
            lambda: _queue_envelope(priority=True),
            lambda: _queue_envelope(priority=-1),
            lambda: _queue_envelope(priority=101),
        )
        for invalid in invalid_envelopes:
            with self.subTest(invalid=invalid), self.assertRaises((TypeError, ValueError)):
                invalid()
        CommandDefinition("A" * 64, 2_147_483_647, ("value",), (), (), _queue_payload_validator)
        for invalid in (
            lambda: CommandDefinition("A" * 65, 1, ("value",), (), (), _queue_payload_validator),
            lambda: CommandDefinition("lower", 1, ("value",), (), (), _queue_payload_validator),
            lambda: CommandDefinition("A", True, ("value",), (), (), _queue_payload_validator),
            lambda: CommandDefinition("A", 2_147_483_648, ("value",), (), (), _queue_payload_validator),
        ):
            with self.assertRaises((TypeError, ValueError)):
                invalid()
        command = _pending_queue_command()
        with self.assertRaises(ValueError):
            replace(command, command_id="AAAAAAAA-AAAA-AAAA-AAAA-AAAAAAAAAAAA")
        with self.assertRaises(ValueError):
            replace(command, command_id="-0000000-0000-0000-0000-000000000001")
        event = _enqueue_event(command)
        replace(event, actor_id="A" * 128)
        with self.assertRaises(ValueError):
            replace(event, actor_id="A" * 129)
        failed = WorkflowCommandEvent(
            2, str(UUID(int=10)), command.command_id, WorkflowCommandEventKind.FAILED,
            WorkflowCommandState.RUNNING, WorkflowCommandState.FAILED, 1, 2,
            "worker", "2026-08-24T12:00:01.000000Z", "worker",
            "2026-08-24T12:00:02.000000Z", 1, "A" * 64,
        )
        with self.assertRaises(ValueError):
            replace(failed, reason_code="A" * 65)

    def test_full_event_compatibility_and_lease_time_matrix(self) -> None:
        t10 = "2026-08-24T12:00:10.000000Z"
        t20 = "2026-08-24T12:00:20.000000Z"
        rows = (
            (WorkflowCommandEventKind.ENQUEUED, None, WorkflowCommandState.PENDING, None, 1, "actor", t10, None, None, 0, None),
            (WorkflowCommandEventKind.CLAIMED, WorkflowCommandState.PENDING, WorkflowCommandState.CLAIMED, 1, 2, "worker", t10, "worker", t20, 1, None),
            (WorkflowCommandEventKind.LEASE_RENEWED, WorkflowCommandState.CLAIMED, WorkflowCommandState.CLAIMED, 2, 3, "worker", t10, "worker", t20, 1, None),
            (WorkflowCommandEventKind.LEASE_RENEWED, WorkflowCommandState.RUNNING, WorkflowCommandState.RUNNING, 3, 4, "worker", t10, "worker", t20, 1, None),
            (WorkflowCommandEventKind.STARTED, WorkflowCommandState.CLAIMED, WorkflowCommandState.RUNNING, 2, 3, "worker", t10, "worker", t20, 1, None),
            (WorkflowCommandEventKind.CANCELLATION_REQUESTED, WorkflowCommandState.CLAIMED, WorkflowCommandState.CLAIMED, 2, 3, "requester", t10, "worker", t20, 1, "STOPPED"),
            (WorkflowCommandEventKind.CANCELLATION_REQUESTED, WorkflowCommandState.RUNNING, WorkflowCommandState.RUNNING, 3, 4, "requester", t10, "worker", t20, 1, "STOPPED"),
            (WorkflowCommandEventKind.CANCELLED, WorkflowCommandState.PENDING, WorkflowCommandState.CANCELLED, 1, 2, "requester", t10, None, None, 0, "STOPPED"),
            (WorkflowCommandEventKind.CANCELLED, WorkflowCommandState.CLAIMED, WorkflowCommandState.CANCELLED, 3, 4, "worker", t10, "worker", t20, 1, "STOPPED"),
            (WorkflowCommandEventKind.CANCELLED, WorkflowCommandState.RUNNING, WorkflowCommandState.CANCELLED, 4, 5, "worker", t10, "worker", t20, 1, "STOPPED"),
            (WorkflowCommandEventKind.CANCELLED, WorkflowCommandState.CLAIMED, WorkflowCommandState.CANCELLED, 3, 4, "recovery", t20, "worker", t20, 1, "STOPPED"),
            (WorkflowCommandEventKind.EXPIRED_CLAIM_RELEASED, WorkflowCommandState.CLAIMED, WorkflowCommandState.PENDING, 2, 3, "recovery", t20, "worker", t20, 1, "LEASE_EXPIRED"),
            (WorkflowCommandEventKind.SUCCEEDED, WorkflowCommandState.RUNNING, WorkflowCommandState.SUCCEEDED, 3, 4, "worker", t10, "worker", t20, 1, None),
            (WorkflowCommandEventKind.FAILED, WorkflowCommandState.RUNNING, WorkflowCommandState.FAILED, 3, 4, "worker", t10, "worker", t20, 1, "FAILED_TEST"),
        )
        events = []
        for ordinal, row in enumerate(rows, start=1):
            with self.subTest(event=row[0].value, ordinal=ordinal):
                event = WorkflowCommandEvent(
                    ordinal, str(UUID(int=1000 + ordinal)), str(UUID(int=1)), *row
                )
                events.append(event)
        invalid = (
            lambda: replace(events[1], occurred_at=t20),
            lambda: replace(events[8], actor_id="other"),
            lambda: replace(events[9], actor_id="other"),
            lambda: replace(events[10], occurred_at=t10, actor_id="recovery"),
            lambda: replace(events[11], occurred_at=t10),
            lambda: replace(events[12], occurred_at=t20),
            lambda: replace(events[13], claim_count=0),
        )
        for candidate in invalid:
            with self.subTest(invalid=candidate), self.assertRaises(ValueError):
                candidate()

    def test_complete_nonapplied_result_code_kind_applicability(self) -> None:
        command = _pending_queue_command()
        all_kinds = set(QueueMutationKind)
        owner_kinds = {
            QueueMutationKind.RENEW_LEASE, QueueMutationKind.MARK_RUNNING,
            QueueMutationKind.ACKNOWLEDGE_CANCELLATION,
            QueueMutationKind.MARK_SUCCEEDED, QueueMutationKind.MARK_FAILED,
        }
        cas_kinds = owner_kinds | {
            QueueMutationKind.REQUEST_CANCELLATION,
            QueueMutationKind.RECOVER_EXPIRED_CLAIM,
        }
        applicability = {
            QueueResultCode.NOT_FOUND: {None} | cas_kinds,
            QueueResultCode.NO_ELIGIBLE_COMMAND: {QueueMutationKind.CLAIM_NEXT},
            QueueResultCode.EXISTING_IDENTICAL: {QueueMutationKind.ENQUEUE},
            QueueResultCode.IDEMPOTENCY_CONFLICT: {QueueMutationKind.ENQUEUE},
            QueueResultCode.CAS_CONFLICT: cas_kinds,
            QueueResultCode.LEASE_OWNER_MISMATCH: owner_kinds,
            QueueResultCode.LEASE_EXPIRED: owner_kinds,
            QueueResultCode.LEASE_NOT_EXPIRED: {QueueMutationKind.RECOVER_EXPIRED_CLAIM},
            QueueResultCode.CANCELLATION_ALREADY_REQUESTED: {QueueMutationKind.REQUEST_CANCELLATION},
            QueueResultCode.CANCELLATION_NOT_REQUESTED: {QueueMutationKind.ACKNOWLEDGE_CANCELLATION},
            QueueResultCode.TERMINAL_OBSERVED: cas_kinds,
            QueueResultCode.TRANSIENT_CONTENTION: all_kinds,
            QueueResultCode.RECONCILIATION_REQUIRED: {QueueMutationKind.RECOVER_EXPIRED_CLAIM},
        }
        command_codes = {
            QueueResultCode.EXISTING_IDENTICAL, QueueResultCode.IDEMPOTENCY_CONFLICT,
            QueueResultCode.CAS_CONFLICT, QueueResultCode.LEASE_OWNER_MISMATCH,
            QueueResultCode.LEASE_EXPIRED, QueueResultCode.LEASE_NOT_EXPIRED,
            QueueResultCode.CANCELLATION_ALREADY_REQUESTED,
            QueueResultCode.CANCELLATION_NOT_REQUESTED,
            QueueResultCode.TERMINAL_OBSERVED, QueueResultCode.RECONCILIATION_REQUIRED,
        }
        for code, allowed in applicability.items():
            for kind in (None, *QueueMutationKind):
                arguments = {"mutation_kind": kind}
                if code in command_codes:
                    arguments["command"] = command
                candidate = lambda: QueueResult(code, **arguments)
                if kind in allowed:
                    with self.subTest(code=code.value, kind=kind):
                        candidate()
                else:
                    with self.subTest(code=code.value, kind=kind), self.assertRaises(ValueError):
                        candidate()
        QueueResult(QueueResultCode.FOUND, command=command)
        QueueResult(QueueResultCode.LISTED, commands=())
        QueueResult(QueueResultCode.HISTORY_RETURNED, events=(_enqueue_event(command),))
        for invalid in (
            lambda: QueueResult(QueueResultCode.FOUND, QueueMutationKind.ENQUEUE, command=command),
            lambda: QueueResult(QueueResultCode.LISTED, commands=(command,), events=(_enqueue_event(command),)),
            lambda: QueueResult(QueueResultCode.HISTORY_RETURNED, events=()),
        ):
            with self.assertRaises(ValueError):
                invalid()

    def test_applied_cancelled_routes_are_operation_specific(self) -> None:
        pending = _pending_queue_command()
        cancelled = replace(
            pending,
            state=WorkflowCommandState.CANCELLED,
            state_version=4,
            claim_count=1,
            cancellation_requested_at="2026-08-24T12:00:04.000000Z",
            cancellation_requested_by="requester",
            cancellation_reason_code="STOPPED",
            updated_at="2026-08-24T12:00:05.000000Z",
            completed_at="2026-08-24T12:00:05.000000Z",
        )
        acknowledged = WorkflowCommandEvent(
            4, str(UUID(int=40)), pending.command_id, WorkflowCommandEventKind.CANCELLED,
            WorkflowCommandState.CLAIMED, WorkflowCommandState.CANCELLED, 3, 4,
            "worker", cancelled.updated_at, "worker",
            "2026-08-24T12:00:10.000000Z", 1, "STOPPED",
        )
        QueueResult(
            QueueResultCode.APPLIED,
            QueueMutationKind.ACKNOWLEDGE_CANCELLATION,
            cancelled,
            acknowledged,
        )
        with self.assertRaises(ValueError):
            QueueResult(
                QueueResultCode.APPLIED,
                QueueMutationKind.RECOVER_EXPIRED_CLAIM,
                cancelled,
                acknowledged,
            )
        with self.assertRaises(ValueError):
            QueueResult(
                QueueResultCode.APPLIED,
                QueueMutationKind.REQUEST_CANCELLATION,
                cancelled,
                acknowledged,
            )
        recovered_command = replace(
            cancelled,
            updated_at="2026-08-24T12:00:10.000000Z",
            completed_at="2026-08-24T12:00:10.000000Z",
        )
        recovered = replace(
            acknowledged,
            event_id=str(UUID(int=41)),
            actor_id="recovery",
            occurred_at="2026-08-24T12:00:10.000000Z",
        )
        QueueResult(
            QueueResultCode.APPLIED,
            QueueMutationKind.RECOVER_EXPIRED_CLAIM,
            recovered_command,
            recovered,
        )
        with self.assertRaises(ValueError):
            QueueResult(
                QueueResultCode.APPLIED,
                QueueMutationKind.ACKNOWLEDGE_CANCELLATION,
                recovered_command,
                recovered,
            )
        pending_cancelled = replace(
            pending,
            state=WorkflowCommandState.CANCELLED,
            state_version=2,
            cancellation_requested_at="2026-08-24T12:00:05.000000Z",
            cancellation_requested_by="requester",
            cancellation_reason_code="STOPPED",
            updated_at="2026-08-24T12:00:05.000000Z",
            completed_at="2026-08-24T12:00:05.000000Z",
        )
        pending_event = WorkflowCommandEvent(
            2, str(UUID(int=42)), pending.command_id, WorkflowCommandEventKind.CANCELLED,
            WorkflowCommandState.PENDING, WorkflowCommandState.CANCELLED, 1, 2,
            "requester", pending_cancelled.updated_at, None, None, 0, "STOPPED",
        )
        QueueResult(
            QueueResultCode.APPLIED,
            QueueMutationKind.REQUEST_CANCELLATION,
            pending_cancelled,
            pending_event,
        )
        with self.assertRaises(ValueError):
            QueueResult(
                QueueResultCode.APPLIED,
                QueueMutationKind.REQUEST_CANCELLATION,
                replace(pending_cancelled, cancellation_requested_by="other"),
                pending_event,
            )


class CommandDefinitionRegistryTest(unittest.TestCase):
    def test_definition_and_registry_lifecycle(self) -> None:
        definition = _queue_definition()
        registry = CommandDefinitionRegistry()
        registry.register(definition)
        self.assertIs(definition, registry.lookup("TEST_COMMAND", 1))
        with self.assertRaises(RuntimeError):
            registry.validate("TEST_COMMAND", 1, {"value": 1})
        registry.freeze()
        registry.freeze()
        self.assertEqual('{"value":1}', registry.validate("TEST_COMMAND", 1, {"value": 1}))
        with self.assertRaises(RuntimeError):
            registry.register(definition)
        with self.assertRaises(LookupError):
            registry.lookup("UNKNOWN", 1)
        with self.assertRaises(ValueError):
            CommandDefinitionRegistry((definition, definition))

    def test_definition_key_relations_and_validator_return(self) -> None:
        with self.assertRaises(ValueError):
            replace(_queue_definition(), required_keys=("value", "value"))
        with self.assertRaises(ValueError):
            replace(_queue_definition(), nullable_keys=("absent",))
        registry = CommandDefinitionRegistry(
            (replace(_queue_definition(), payload_validator=lambda value: value),)  # type: ignore[arg-type]
        )
        registry.freeze()
        with self.assertRaises(TypeError):
            registry.validate("TEST_COMMAND", 1, {"value": 1})

    def test_unknown_definition_precedes_clock_id_and_repository(self) -> None:
        repository = Mock(spec=WorkflowCommandRepository)
        providers = QueueProviders()
        service = DurableCommandQueueService(
            repository,
            CommandDefinitionRegistry(),
            10,
            clock=providers.clock,
            id_factory=providers.identifier,
        )
        with self.assertRaises(LookupError):
            service.enqueue(
                project_id="panam",
                command_kind="UNKNOWN",
                command_schema_version=1,
                payload={"value": 1},
                idempotency_key="key",
                actor_id="tester",
            )
        self.assertEqual((0, 0), (providers.clock_calls, providers.id_calls))
        repository.enqueue.assert_not_called()

    def test_definition_and_registry_complete_negative_surface(self) -> None:
        definition = _queue_definition()
        invalid_definitions = (
            lambda: CommandDefinition("bad", 1, ("value",), (), (), _queue_payload_validator),
            lambda: CommandDefinition("TEST", True, ("value",), (), (), _queue_payload_validator),
            lambda: replace(definition, required_keys=("z", "a")),
            lambda: replace(definition, required_keys=("value",), optional_keys=("value",)),
            lambda: replace(definition, required_keys=("bad-key",)),
            lambda: replace(definition, required_keys=["value"]),
            lambda: replace(definition, payload_validator=None),
        )
        for invalid in invalid_definitions:
            with self.subTest(invalid=invalid), self.assertRaises((TypeError, ValueError)):
                invalid()
        with self.assertRaises(TypeError):
            CommandDefinitionRegistry([definition])
        with self.assertRaises(TypeError):
            CommandDefinitionRegistry().register(object())
        registry = CommandDefinitionRegistry((definition,))
        for name in ("unregister", "replace", "thaw", "discover", "fallback"):
            self.assertFalse(hasattr(registry, name))

    def test_validator_defensive_copy_and_return_revalidation(self) -> None:
        observed: list[dict[str, object]] = []

        def mutating_validator(payload: dict[str, object]) -> str:
            observed.append(payload)
            payload["value"] = 9
            return _canonical_json(payload)

        source = {"value": 1}
        registry = CommandDefinitionRegistry(
            (replace(_queue_definition(), payload_validator=mutating_validator),)
        )
        registry.freeze()
        self.assertEqual('{"value":9}', registry.validate("TEST_COMMAND", 1, source))
        self.assertEqual({"value": 1}, source)
        self.assertIsNot(source, observed[0])

        invalid_returns = (
            lambda payload: '{"value":1, "note":null}',
            lambda payload: '{"value":1,"extra":2}',
            lambda payload: '{"value":1,"shell":"x"}',
        )
        for validator in invalid_returns:
            candidate = CommandDefinitionRegistry(
                (replace(_queue_definition(), payload_validator=validator),)
            )
            candidate.freeze()
            with self.subTest(validator=validator), self.assertRaises(ValueError):
                candidate.validate("TEST_COMMAND", 1, {"value": 1})


class Dl21OperationMatrixCompatibilityTest(unittest.TestCase):
    def test_repository_and_service_exact_method_sets_and_signatures(self) -> None:
        expected = (
            "enqueue", "get", "list_project", "history", "claim_next", "renew_lease",
            "mark_running", "request_cancellation", "acknowledge_cancellation",
            "mark_succeeded", "mark_failed", "recover_expired_claim",
        )
        for owner in (WorkflowCommandRepository, DurableCommandQueueService):
            public = tuple(
                name
                for name, value in owner.__dict__.items()
                if not name.startswith("_") and callable(value)
            )
            self.assertEqual(expected, public)
        query = {"get", "list_project", "history"}
        self.assertEqual(3, len(query))
        self.assertEqual(9, len(set(expected) - query))
        self.assertEqual(
            ("self", "repository", "registry", "lease_duration_seconds", "clock", "id_factory"),
            tuple(inspect.signature(DurableCommandQueueService.__init__).parameters),
        )
        self.assertEqual(
            inspect.Parameter.KEYWORD_ONLY,
            inspect.signature(DurableCommandQueueService.__init__).parameters["clock"].kind,
        )

    def test_exact_thirteen_additive_exports(self) -> None:
        import panam_development_loop as package

        expected = {
            "CommandDefinition", "CommandDefinitionRegistry", "DurableCommandQueueService",
            "QueueMutationKind", "QueueResult", "QueueResultCode",
            "SqliteWorkflowCommandRepository", "ValidatedCommandEnvelope", "WorkflowCommand",
            "WorkflowCommandEvent", "WorkflowCommandEventKind",
            "WorkflowCommandRepository", "WorkflowCommandState",
        }
        p1 = {
            "AcceptedStateEvent", "ApprovalBinding", "ApprovalBindingRepository",
            "DevelopmentRunInspectionRepository", "ApprovalKind", "ApprovalSnapshot",
            "ApprovalSnapshotStatus", "ApprovalTargetKind", "ApprovalValidationCode",
            "ApprovalValidationError", "ApprovalVersion", "ContractValidationCode",
            "ContractValidationError", "ContractVersion", "DevelopmentRun",
            "DevelopmentRunState", "EscalationTrigger", "EvidenceKind", "EvidenceSnapshot",
            "EvidenceVerdict", "ExpectedEvidenceBinding", "MilestoneContract",
            "MilestoneContractRepository", "PhaseContract", "PhaseContractRepository",
            "ProjectPolicy", "ProjectPolicyReadOutcome", "ProjectPolicyReadResult",
            "ProjectPolicyReader", "ProjectPolicyRepository", "ProjectPolicyVersion",
            "RepositoryError", "RepositoryEvidenceBinding", "RepositoryFailureCode",
            "SnapshotProducerKind", "SqliteApprovalBindingRepository",
            "SqliteDevelopmentRunInspectionRepository", "SqliteMilestoneContractRepository",
            "SqlitePhaseContractRepository", "SqliteProjectPolicyRepository", "SqliteRunStore",
            "TransactionalTransitionReasonCode", "TransactionalTransitionResult",
            "TransitionEvaluationDecision", "TransitionEvaluationReasonCode",
            "TransitionEvaluationRequest", "TransitionEvaluationResult", "TransitionPolicy",
            "TransitionReasonCode", "TransitionRequirement", "TransitionRequest",
            "TransitionResult", "TransitionRule", "TransitionRuleId",
            "TransitionRuleValidationCode", "TransitionRuleValidationError",
            "TransitionService", "WorkflowEdgeType", "WorkflowNodeType",
            "FoundationQueryOutcome", "FoundationQueryResult", "FoundationQueryService",
            "exit_code_for", "render_error", "render_json", "render_text",
        }
        self.assertEqual(p1 | expected, set(package.__all__))
        self.assertEqual(79, len(package.__all__))
        self.assertEqual(13, len(expected))
        for forbidden in ("Worker", "OperationJournal", "QueueQueryKind", "Executor"):
            self.assertNotIn(forbidden, package.__all__)

    def test_exact_result_sets_by_operation(self) -> None:
        matrix = {
            "enqueue": {"APPLIED", "EXISTING_IDENTICAL", "IDEMPOTENCY_CONFLICT", "TRANSIENT_CONTENTION"},
            "get": {"FOUND", "NOT_FOUND"},
            "list_project": {"LISTED"},
            "history": {"HISTORY_RETURNED", "NOT_FOUND"},
            "claim_next": {"APPLIED", "NO_ELIGIBLE_COMMAND", "TRANSIENT_CONTENTION"},
            "renew_lease": {"APPLIED", "NOT_FOUND", "CAS_CONFLICT", "LEASE_OWNER_MISMATCH", "LEASE_EXPIRED", "TERMINAL_OBSERVED", "TRANSIENT_CONTENTION"},
            "mark_running": {"APPLIED", "NOT_FOUND", "CAS_CONFLICT", "LEASE_OWNER_MISMATCH", "LEASE_EXPIRED", "TERMINAL_OBSERVED", "TRANSIENT_CONTENTION"},
            "request_cancellation": {"APPLIED", "NOT_FOUND", "CAS_CONFLICT", "CANCELLATION_ALREADY_REQUESTED", "TERMINAL_OBSERVED", "TRANSIENT_CONTENTION"},
            "acknowledge_cancellation": {"APPLIED", "NOT_FOUND", "CAS_CONFLICT", "LEASE_OWNER_MISMATCH", "LEASE_EXPIRED", "CANCELLATION_NOT_REQUESTED", "TERMINAL_OBSERVED", "TRANSIENT_CONTENTION"},
            "mark_succeeded": {"APPLIED", "NOT_FOUND", "CAS_CONFLICT", "LEASE_OWNER_MISMATCH", "LEASE_EXPIRED", "TERMINAL_OBSERVED", "TRANSIENT_CONTENTION"},
            "mark_failed": {"APPLIED", "NOT_FOUND", "CAS_CONFLICT", "LEASE_OWNER_MISMATCH", "LEASE_EXPIRED", "TERMINAL_OBSERVED", "TRANSIENT_CONTENTION"},
            "recover_expired_claim": {"APPLIED", "NOT_FOUND", "CAS_CONFLICT", "LEASE_NOT_EXPIRED", "TERMINAL_OBSERVED", "RECONCILIATION_REQUIRED", "TRANSIENT_CONTENTION"},
        }
        self.assertEqual(12, len(matrix))
        self.assertEqual(set(QueueResultCode), {QueueResultCode[name] for codes in matrix.values() for name in codes})

    def test_exact_twelve_by_ten_normative_matrix_and_signatures(self) -> None:
        service_parameters = {
            "enqueue": ("project_id", "command_kind", "command_schema_version", "payload", "idempotency_key", "actor_id", "development_run_id", "phase_id", "priority"),
            "get": ("command_id",), "list_project": ("project_id",), "history": ("command_id",),
            "claim_next": ("lease_owner",),
            "renew_lease": ("command_id", "expected_state", "expected_state_version", "lease_owner"),
            "mark_running": ("command_id", "expected_state", "expected_state_version", "lease_owner"),
            "request_cancellation": ("command_id", "expected_state", "expected_state_version", "requested_by", "reason_code"),
            "acknowledge_cancellation": ("command_id", "expected_state", "expected_state_version", "lease_owner"),
            "mark_succeeded": ("command_id", "expected_state", "expected_state_version", "lease_owner"),
            "mark_failed": ("command_id", "expected_state", "expected_state_version", "lease_owner", "failure_code"),
            "recover_expired_claim": ("command_id", "expected_state", "expected_state_version", "recovery_actor"),
        }
        repository_parameters = {
            "enqueue": ("command_id", "event_id", "envelope", "actor_id", "occurred_at"),
            "get": ("command_id",), "list_project": ("project_id",), "history": ("command_id",),
            "claim_next": ("event_id", "lease_owner", "lease_acquired_at", "lease_expires_at"),
            "renew_lease": ("command_id", "expected_state", "expected_state_version", "lease_owner", "observed_at", "lease_expires_at", "event_id"),
            "mark_running": ("command_id", "expected_state", "expected_state_version", "lease_owner", "occurred_at", "event_id"),
            "request_cancellation": ("command_id", "expected_state", "expected_state_version", "requested_by", "reason_code", "occurred_at", "event_id"),
            "acknowledge_cancellation": ("command_id", "expected_state", "expected_state_version", "lease_owner", "observed_at", "event_id"),
            "mark_succeeded": ("command_id", "expected_state", "expected_state_version", "lease_owner", "observed_at", "event_id"),
            "mark_failed": ("command_id", "expected_state", "expected_state_version", "lease_owner", "failure_code", "observed_at", "event_id"),
            "recover_expired_claim": ("command_id", "expected_state", "expected_state_version", "recovery_actor", "observed_at", "event_id"),
        }
        kinds = {name: QueueMutationKind[name.upper()] for name in service_parameters if name not in {"get", "list_project", "history"}}
        codes = {
            "enqueue": ("APPLIED", "EXISTING_IDENTICAL", "IDEMPOTENCY_CONFLICT", "TRANSIENT_CONTENTION"),
            "get": ("FOUND", "NOT_FOUND"), "list_project": ("LISTED",),
            "history": ("HISTORY_RETURNED", "NOT_FOUND"),
            "claim_next": ("APPLIED", "NO_ELIGIBLE_COMMAND", "TRANSIENT_CONTENTION"),
            "renew_lease": ("APPLIED", "NOT_FOUND", "CAS_CONFLICT", "LEASE_OWNER_MISMATCH", "LEASE_EXPIRED", "TERMINAL_OBSERVED", "TRANSIENT_CONTENTION"),
            "mark_running": ("APPLIED", "NOT_FOUND", "CAS_CONFLICT", "LEASE_OWNER_MISMATCH", "LEASE_EXPIRED", "TERMINAL_OBSERVED", "TRANSIENT_CONTENTION"),
            "request_cancellation": ("APPLIED", "NOT_FOUND", "CAS_CONFLICT", "CANCELLATION_ALREADY_REQUESTED", "TERMINAL_OBSERVED", "TRANSIENT_CONTENTION"),
            "acknowledge_cancellation": ("APPLIED", "NOT_FOUND", "CAS_CONFLICT", "LEASE_OWNER_MISMATCH", "LEASE_EXPIRED", "CANCELLATION_NOT_REQUESTED", "TERMINAL_OBSERVED", "TRANSIENT_CONTENTION"),
            "mark_succeeded": ("APPLIED", "NOT_FOUND", "CAS_CONFLICT", "LEASE_OWNER_MISMATCH", "LEASE_EXPIRED", "TERMINAL_OBSERVED", "TRANSIENT_CONTENTION"),
            "mark_failed": ("APPLIED", "NOT_FOUND", "CAS_CONFLICT", "LEASE_OWNER_MISMATCH", "LEASE_EXPIRED", "TERMINAL_OBSERVED", "TRANSIENT_CONTENTION"),
            "recover_expired_claim": ("APPLIED", "NOT_FOUND", "CAS_CONFLICT", "LEASE_NOT_EXPIRED", "TERMINAL_OBSERVED", "RECONCILIATION_REQUIRED", "TRANSIENT_CONTENTION"),
        }
        effects = {
            "enqueue": ("ABSENT_TO_PENDING", "ENQUEUED"), "get": ("NONE", "NONE"),
            "list_project": ("NONE", "NONE"), "history": ("NONE", "NONE"),
            "claim_next": ("PENDING_TO_CLAIMED", "CLAIMED"),
            "renew_lease": ("ACTIVE_SAME_STATE", "LEASE_RENEWED"),
            "mark_running": ("CLAIMED_TO_RUNNING", "STARTED"),
            "request_cancellation": ("PENDING_TO_CANCELLED_OR_ACTIVE_SAME", "CANCELLED_OR_CANCELLATION_REQUESTED"),
            "acknowledge_cancellation": ("ACTIVE_TO_CANCELLED", "CANCELLED"),
            "mark_succeeded": ("RUNNING_TO_SUCCEEDED", "SUCCEEDED"),
            "mark_failed": ("RUNNING_TO_FAILED", "FAILED"),
            "recover_expired_claim": ("EXPIRED_CLAIMED_TO_PENDING_OR_CANCELLED", "EXPIRED_CLAIM_RELEASED_OR_CANCELLED"),
        }
        type_by_parameter = {
            "command_schema_version": int, "expected_state_version": int,
            "priority": int, "lease_duration_seconds": int,
            "payload": dict[str, object], "envelope": ValidatedCommandEnvelope,
            "expected_state": WorkflowCommandState,
            "development_run_id": str | None, "phase_id": str | None,
        }
        repository_returns = {
            "get": WorkflowCommand | None,
            "list_project": tuple[WorkflowCommand, ...],
            "history": tuple[WorkflowCommandEvent, ...] | None,
        }
        headers = (
            "SERVICE_OPERATION", "QUERY_OR_MUTATION",
            "QUEUE_MUTATION_KIND_IF_APPLICABLE", "ARGUMENTS", "RETURN_TYPE",
            "QUEUE_RESULT_CODES", "EXCEPTIONS", "REPOSITORY_MAPPING",
            "STATE_EFFECT", "EVENT_EFFECT",
        )
        rows = []
        for name in service_parameters:
            service_signature = inspect.signature(getattr(DurableCommandQueueService, name))
            repository_signature = inspect.signature(getattr(WorkflowCommandRepository, name))
            self.assertEqual(("self",) + service_parameters[name], tuple(service_signature.parameters))
            self.assertEqual(("self",) + repository_parameters[name], tuple(repository_signature.parameters))
            self.assertIs(QueueResult, service_signature.return_annotation)
            service_hints = get_type_hints(getattr(DurableCommandQueueService, name))
            repository_hints = get_type_hints(getattr(WorkflowCommandRepository, name))
            for parameter in service_parameters[name]:
                self.assertEqual(type_by_parameter.get(parameter, str), service_hints[parameter])
            for parameter in repository_parameters[name]:
                self.assertEqual(type_by_parameter.get(parameter, str), repository_hints[parameter])
            self.assertEqual(QueueResult, service_hints["return"])
            self.assertEqual(repository_returns.get(name, QueueResult), repository_hints["return"])
            service_nonself = tuple(service_signature.parameters.values())[1:]
            repository_nonself = tuple(repository_signature.parameters.values())[1:]
            if name not in {"get", "list_project", "history"}:
                self.assertTrue(all(parameter.kind is inspect.Parameter.KEYWORD_ONLY for parameter in service_nonself))
                self.assertTrue(all(parameter.kind is inspect.Parameter.KEYWORD_ONLY for parameter in repository_nonself))
            else:
                self.assertTrue(all(parameter.kind is inspect.Parameter.POSITIONAL_OR_KEYWORD for parameter in service_nonself + repository_nonself))
            if name == "enqueue":
                self.assertEqual((None, None, 0), tuple(service_signature.parameters[key].default for key in ("development_run_id", "phase_id", "priority")))
            else:
                self.assertTrue(all(parameter.default is inspect.Parameter.empty for parameter in service_nonself))
            self.assertTrue(all(parameter.default is inspect.Parameter.empty for parameter in repository_nonself))
            exceptions = (
                (TypeError, ValueError, LookupError, RepositoryError)
                if name == "enqueue"
                else (TypeError, ValueError, RepositoryError)
            )
            row = {
                "SERVICE_OPERATION": name,
                "QUERY_OR_MUTATION": "QUERY" if name in {"get", "list_project", "history"} else "MUTATION",
                "QUEUE_MUTATION_KIND_IF_APPLICABLE": kinds.get(name),
                "ARGUMENTS": service_parameters[name],
                "RETURN_TYPE": QueueResult,
                "QUEUE_RESULT_CODES": tuple(QueueResultCode[value] for value in codes[name]),
                "EXCEPTIONS": exceptions,
                "REPOSITORY_MAPPING": getattr(WorkflowCommandRepository, name),
                "STATE_EFFECT": effects[name][0],
                "EVENT_EFFECT": effects[name][1],
            }
            self.assertEqual(headers, tuple(row))
            rows.append(row)
        self.assertEqual(12, len(rows))
        self.assertEqual(12, len({row["SERVICE_OPERATION"] for row in rows}))
        self.assertEqual((3, 9), (sum(row["QUERY_OR_MUTATION"] == "QUERY" for row in rows), sum(row["QUERY_OR_MUTATION"] == "MUTATION" for row in rows)))
        constructor = inspect.signature(DurableCommandQueueService.__init__)
        constructor_hints = get_type_hints(DurableCommandQueueService.__init__)
        self.assertEqual(
            ("self", "repository", "registry", "lease_duration_seconds", "clock", "id_factory"),
            tuple(constructor.parameters),
        )
        self.assertEqual(
            {
                "repository": WorkflowCommandRepository,
                "registry": CommandDefinitionRegistry,
                "lease_duration_seconds": int,
                "clock": Callable[[], datetime],
                "id_factory": Callable[[], str],
                "return": type(None),
            },
            constructor_hints,
        )
        self.assertTrue(all(parameter.default is inspect.Parameter.empty for parameter in tuple(constructor.parameters.values())[1:]))
        self.assertEqual(inspect.Parameter.KEYWORD_ONLY, constructor.parameters["clock"].kind)
        self.assertEqual(inspect.Parameter.KEYWORD_ONLY, constructor.parameters["id_factory"].kind)

    def test_service_clock_id_counts_repository_mapping_and_no_retry(self) -> None:
        query_repository = Mock(spec=WorkflowCommandRepository)
        query_repository.get.return_value = None
        query_repository.list_project.return_value = ()
        query_repository.history.return_value = None
        query_providers = QueueProviders()
        query_service = DurableCommandQueueService(
            query_repository, CommandDefinitionRegistry((_queue_definition(),)), 10,
            clock=query_providers.clock, id_factory=query_providers.identifier,
        )
        self.assertEqual(QueueResultCode.NOT_FOUND, query_service.get(str(UUID(int=99))).code)
        self.assertEqual(QueueResultCode.LISTED, query_service.list_project("panam").code)
        self.assertEqual(QueueResultCode.NOT_FOUND, query_service.history(str(UUID(int=99))).code)
        self.assertEqual((0, 0), (query_providers.clock_calls, query_providers.id_calls))
        for method in (query_repository.get, query_repository.list_project, query_repository.history):
            self.assertEqual(1, method.call_count)

        calls = {
            "enqueue": dict(project_id="panam", command_kind="TEST_COMMAND", command_schema_version=1, payload={"value": 1}, idempotency_key="key", actor_id="actor"),
            "claim_next": dict(lease_owner="worker"),
            "renew_lease": dict(command_id=str(UUID(int=99)), expected_state=WorkflowCommandState.CLAIMED, expected_state_version=1, lease_owner="worker"),
            "mark_running": dict(command_id=str(UUID(int=99)), expected_state=WorkflowCommandState.CLAIMED, expected_state_version=1, lease_owner="worker"),
            "request_cancellation": dict(command_id=str(UUID(int=99)), expected_state=WorkflowCommandState.PENDING, expected_state_version=1, requested_by="actor", reason_code="STOP"),
            "acknowledge_cancellation": dict(command_id=str(UUID(int=99)), expected_state=WorkflowCommandState.CLAIMED, expected_state_version=1, lease_owner="worker"),
            "mark_succeeded": dict(command_id=str(UUID(int=99)), expected_state=WorkflowCommandState.RUNNING, expected_state_version=1, lease_owner="worker"),
            "mark_failed": dict(command_id=str(UUID(int=99)), expected_state=WorkflowCommandState.RUNNING, expected_state_version=1, lease_owner="worker", failure_code="FAILED_TEST"),
            "recover_expired_claim": dict(command_id=str(UUID(int=99)), expected_state=WorkflowCommandState.CLAIMED, expected_state_version=1, recovery_actor="recovery"),
        }
        for name, arguments in calls.items():
            with self.subTest(operation=name):
                repository = Mock(spec=WorkflowCommandRepository)
                kind = QueueMutationKind[name.upper()]
                getattr(repository, name).return_value = QueueResult(QueueResultCode.TRANSIENT_CONTENTION, kind)
                providers = QueueProviders()
                service = DurableCommandQueueService(
                    repository, CommandDefinitionRegistry((_queue_definition(),)), 10,
                    clock=providers.clock, id_factory=providers.identifier,
                )
                result = getattr(service, name)(**arguments)
                self.assertEqual((QueueResultCode.TRANSIENT_CONTENTION, kind), (result.code, result.mutation_kind))
                self.assertEqual((1, 2 if name == "enqueue" else 1), (providers.clock_calls, providers.id_calls))
                getattr(repository, name).assert_called_once()
                forwarded = getattr(repository, name).call_args.kwargs
                self.assertEqual(str(UUID(int=2 if name == "enqueue" else 1)), forwarded["event_id"])
                if name in {"claim_next", "renew_lease"}:
                    self.assertEqual("2026-08-24T12:00:10.000000Z", forwarded["lease_expires_at"])

        repository = Mock(spec=WorkflowCommandRepository)
        providers = QueueProviders()
        providers.current = datetime.max.replace(tzinfo=timezone.utc)
        service = DurableCommandQueueService(
            repository, CommandDefinitionRegistry((_queue_definition(),)), 1,
            clock=providers.clock, id_factory=providers.identifier,
        )
        with self.assertRaises(ValueError):
            service.claim_next(lease_owner="worker")
        self.assertEqual((1, 0), (providers.clock_calls, providers.id_calls))
        repository.claim_next.assert_not_called()

    def test_claim_next_uses_elapsed_utc_duration_across_offset_transition(self) -> None:
        repository = Mock(spec=WorkflowCommandRepository)
        repository.claim_next.return_value = QueueResult(
            QueueResultCode.NO_ELIGIBLE_COMMAND,
            QueueMutationKind.CLAIM_NEXT,
        )
        clock_value = datetime(
            2026,
            10,
            25,
            2,
            59,
            tzinfo=DeterministicFallbackTimezone(),
        )
        service = DurableCommandQueueService(
            repository,
            CommandDefinitionRegistry(),
            120,
            clock=lambda: clock_value,
            id_factory=lambda: str(UUID(int=1)),
        )

        service.claim_next(lease_owner="worker")

        arguments = repository.claim_next.call_args.kwargs
        acquired = _parse_queue_timestamp(arguments["lease_acquired_at"], "acquired")
        expires = _parse_queue_timestamp(arguments["lease_expires_at"], "expires")
        self.assertEqual("2026-10-25T00:59:00.000000Z", arguments["lease_acquired_at"])
        self.assertEqual("2026-10-25T01:01:00.000000Z", arguments["lease_expires_at"])
        self.assertEqual(timedelta(seconds=120), expires - acquired)

    def test_renew_lease_uses_elapsed_utc_duration_across_offset_transition(self) -> None:
        repository = Mock(spec=WorkflowCommandRepository)
        repository.renew_lease.return_value = QueueResult(
            QueueResultCode.TRANSIENT_CONTENTION,
            QueueMutationKind.RENEW_LEASE,
        )
        clock_value = datetime(
            2026,
            10,
            25,
            2,
            59,
            tzinfo=DeterministicFallbackTimezone(),
        )
        service = DurableCommandQueueService(
            repository,
            CommandDefinitionRegistry(),
            120,
            clock=lambda: clock_value,
            id_factory=lambda: str(UUID(int=1)),
        )

        service.renew_lease(
            command_id=str(UUID(int=2)),
            expected_state=WorkflowCommandState.CLAIMED,
            expected_state_version=2,
            lease_owner="worker",
        )

        arguments = repository.renew_lease.call_args.kwargs
        observed = _parse_queue_timestamp(arguments["observed_at"], "observed")
        expires = _parse_queue_timestamp(arguments["lease_expires_at"], "expires")
        self.assertEqual("2026-10-25T00:59:00.000000Z", arguments["observed_at"])
        self.assertEqual("2026-10-25T01:01:00.000000Z", arguments["lease_expires_at"])
        self.assertEqual(timedelta(seconds=120), expires - observed)

    def test_service_constructor_lease_bounds_and_required_dependencies(self) -> None:
        repository = Mock(spec=WorkflowCommandRepository)
        registry = CommandDefinitionRegistry()
        DurableCommandQueueService(
            repository, registry, 1,
            clock=lambda: datetime.now(timezone.utc), id_factory=lambda: str(UUID(int=1)),
        )
        with self.assertRaises(RuntimeError):
            registry.register(_queue_definition())
        for duration in (1, 86_400):
            DurableCommandQueueService(
                repository, CommandDefinitionRegistry(), duration,
                clock=lambda: datetime.now(timezone.utc), id_factory=lambda: str(UUID(int=1)),
            )
        for duration in (0, 86_401, True):
            with self.subTest(duration=duration), self.assertRaises((TypeError, ValueError)):
                DurableCommandQueueService(
                    repository, CommandDefinitionRegistry(), duration,
                    clock=lambda: datetime.now(timezone.utc), id_factory=lambda: str(UUID(int=1)),
                )
        with self.assertRaises(TypeError):
            DurableCommandQueueService(None, CommandDefinitionRegistry(), 1, clock=lambda: datetime.now(timezone.utc), id_factory=lambda: str(UUID(int=1)))
        with self.assertRaises(TypeError):
            DurableCommandQueueService(repository, CommandDefinitionRegistry(), 1, clock=None, id_factory=lambda: str(UUID(int=1)))
        with self.assertRaises(TypeError):
            DurableCommandQueueService(repository, CommandDefinitionRegistry(), 1, clock=lambda: datetime.now(timezone.utc), id_factory=None)
        bad_ids = DurableCommandQueueService(
            repository, CommandDefinitionRegistry((_queue_definition(),)), 1,
            clock=lambda: datetime(2026, 8, 24, tzinfo=timezone.utc),
            id_factory=lambda: "not-a-uuid",
        )
        with self.assertRaises(ValueError):
            bad_ids.enqueue(
                project_id="panam", command_kind="TEST_COMMAND",
                command_schema_version=1, payload={"value": 1},
                idempotency_key="key", actor_id="actor",
            )
        repository.enqueue.assert_not_called()

    def test_complete_service_exception_boundary(self) -> None:
        command_id = str(UUID(int=77))
        valid = {
            "enqueue": dict(project_id="panam", command_kind="TEST_COMMAND", command_schema_version=1, payload={"value": 1}, idempotency_key="key", actor_id="actor"),
            "get": dict(command_id=command_id), "list_project": dict(project_id="panam"),
            "history": dict(command_id=command_id), "claim_next": dict(lease_owner="worker"),
            "renew_lease": dict(command_id=command_id, expected_state=WorkflowCommandState.CLAIMED, expected_state_version=1, lease_owner="worker"),
            "mark_running": dict(command_id=command_id, expected_state=WorkflowCommandState.CLAIMED, expected_state_version=1, lease_owner="worker"),
            "request_cancellation": dict(command_id=command_id, expected_state=WorkflowCommandState.PENDING, expected_state_version=1, requested_by="actor", reason_code="STOPPED"),
            "acknowledge_cancellation": dict(command_id=command_id, expected_state=WorkflowCommandState.CLAIMED, expected_state_version=1, lease_owner="worker"),
            "mark_succeeded": dict(command_id=command_id, expected_state=WorkflowCommandState.RUNNING, expected_state_version=1, lease_owner="worker"),
            "mark_failed": dict(command_id=command_id, expected_state=WorkflowCommandState.RUNNING, expected_state_version=1, lease_owner="worker", failure_code="FAILED_TEST"),
            "recover_expired_claim": dict(command_id=command_id, expected_state=WorkflowCommandState.CLAIMED, expected_state_version=1, recovery_actor="recovery"),
        }
        invalid_type = {
            "enqueue": {**valid["enqueue"], "payload": []}, "get": {"command_id": 1},
            "list_project": {"project_id": 1}, "history": {"command_id": 1},
            "claim_next": {"lease_owner": 1},
            **{name: {**valid[name], "expected_state": "CLAIMED"} for name in (
                "renew_lease", "mark_running", "request_cancellation",
                "acknowledge_cancellation", "mark_succeeded", "mark_failed",
                "recover_expired_claim",
            )},
        }
        invalid_value = {
            "enqueue": {**valid["enqueue"], "priority": -1}, "get": {"command_id": "bad"},
            "list_project": {"project_id": ""}, "history": {"command_id": "bad"},
            "claim_next": {"lease_owner": "-bad"},
            "renew_lease": {**valid["renew_lease"], "expected_state": WorkflowCommandState.PENDING},
            "mark_running": {**valid["mark_running"], "expected_state": WorkflowCommandState.RUNNING},
            "request_cancellation": {**valid["request_cancellation"], "reason_code": "lower"},
            "acknowledge_cancellation": {**valid["acknowledge_cancellation"], "expected_state": WorkflowCommandState.PENDING},
            "mark_succeeded": {**valid["mark_succeeded"], "expected_state": WorkflowCommandState.CLAIMED},
            "mark_failed": {**valid["mark_failed"], "expected_state": WorkflowCommandState.CLAIMED},
            "recover_expired_claim": {**valid["recover_expired_claim"], "expected_state": WorkflowCommandState.PENDING},
        }
        for name in valid:
            with self.subTest(operation=name):
                repository = Mock(spec=WorkflowCommandRepository)
                service = DurableCommandQueueService(
                    repository, CommandDefinitionRegistry((_queue_definition(),)), 10,
                    clock=lambda: datetime(2026, 8, 24, tzinfo=timezone.utc),
                    id_factory=lambda: str(UUID(int=1)),
                )
                with self.assertRaises(TypeError):
                    getattr(service, name)(**invalid_type[name])
                getattr(repository, name).assert_not_called()
                with self.assertRaises(ValueError):
                    getattr(service, name)(**invalid_value[name])
                getattr(repository, name).assert_not_called()
                getattr(repository, name).side_effect = RepositoryError(
                    RepositoryFailureCode.SQLITE_OPERATIONAL_FAILURE,
                    "WorkflowCommand", name,
                )
                with self.assertRaises(RepositoryError):
                    getattr(service, name)(**valid[name])
                getattr(repository, name).assert_called_once()


class SqliteMigrationFourTest(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary_directory = tempfile.TemporaryDirectory()
        self.directory = Path(self._temporary_directory.name)
        self.path = self.directory / "queue.sqlite3"

    def tearDown(self) -> None:
        self._temporary_directory.cleanup()

    def test_fresh_migration_four_schema_is_exact(self) -> None:
        SqliteRunStore(self.path).initialize("initialized-at")
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        try:
            self.assertEqual([1, 2, 3, 4], [row[0] for row in connection.execute("SELECT version FROM schema_migrations ORDER BY version")])
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
            self.assertEqual({"workflow_commands", "workflow_command_events"}, tables - {"schema_migrations", "development_runs", "state_events", "phases", "milestone_contracts", "approvals", "project_policies"})
            self.assertEqual(
                ["workflow_commands_claim_order_idx", "workflow_commands_lease_expiry_idx", "workflow_commands_project_sequence_idx"],
                sorted(row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'workflow_commands_%_idx'")),
            )
            self.assertEqual([], connection.execute("SELECT name FROM sqlite_master WHERE type='trigger'").fetchall())
            command_columns = [
                ("queue_sequence", "INTEGER", 0, None, 1), ("command_id", "TEXT", 1, None, 0),
                ("project_id", "TEXT", 1, None, 0), ("development_run_id", "TEXT", 0, None, 0),
                ("phase_id", "TEXT", 0, None, 0), ("command_kind", "TEXT", 1, None, 0),
                ("command_schema_version", "INTEGER", 1, None, 0), ("payload_json", "TEXT", 1, None, 0),
                ("intent_digest", "TEXT", 1, None, 0), ("idempotency_key", "TEXT", 1, None, 0),
                ("priority", "INTEGER", 1, "0", 0), ("state", "TEXT", 1, None, 0),
                ("state_version", "INTEGER", 1, None, 0), ("claim_count", "INTEGER", 1, "0", 0),
                ("lease_owner", "TEXT", 0, None, 0), ("lease_acquired_at", "TEXT", 0, None, 0),
                ("lease_expires_at", "TEXT", 0, None, 0), ("cancellation_requested_at", "TEXT", 0, None, 0),
                ("cancellation_requested_by", "TEXT", 0, None, 0), ("cancellation_reason_code", "TEXT", 0, None, 0),
                ("failure_code", "TEXT", 0, None, 0), ("created_at", "TEXT", 1, None, 0),
                ("updated_at", "TEXT", 1, None, 0), ("started_at", "TEXT", 0, None, 0),
                ("completed_at", "TEXT", 0, None, 0),
            ]
            event_columns = [
                ("event_sequence", "INTEGER", 0, None, 1), ("event_id", "TEXT", 1, None, 0),
                ("command_id", "TEXT", 1, None, 0), ("event_kind", "TEXT", 1, None, 0),
                ("prior_state", "TEXT", 0, None, 0), ("next_state", "TEXT", 1, None, 0),
                ("prior_state_version", "INTEGER", 0, None, 0), ("next_state_version", "INTEGER", 1, None, 0),
                ("actor_id", "TEXT", 1, None, 0), ("occurred_at", "TEXT", 1, None, 0),
                ("lease_owner", "TEXT", 0, None, 0), ("lease_expires_at", "TEXT", 0, None, 0),
                ("claim_count", "INTEGER", 1, None, 0), ("reason_code", "TEXT", 0, None, 0),
            ]
            shape = lambda table: [
                (row[1], row[2], row[3], row[4], row[5])
                for row in connection.execute(f"PRAGMA table_info({table})")
            ]
            self.assertEqual(command_columns, shape("workflow_commands"))
            self.assertEqual(event_columns, shape("workflow_command_events"))

            def index_shapes(table: str) -> dict[str, tuple[object, ...]]:
                result = {}
                for row in connection.execute(f"PRAGMA index_list({table})"):
                    columns = tuple(
                        (item[2], item[3], item[4], item[5])
                        for item in connection.execute(f"PRAGMA index_xinfo('{row[1]}')")
                        if item[5] == 1
                    )
                    result[row[1]] = (row[2], row[3], row[4], columns)
                return result

            command_indexes = index_shapes("workflow_commands")
            event_indexes = index_shapes("workflow_command_events")
            self.assertEqual(5, len(command_indexes))
            self.assertEqual(2, len(event_indexes))
            self.assertEqual(
                {
                    (("command_id", 0, "BINARY", 1),),
                    (("project_id", 0, "BINARY", 1), ("idempotency_key", 0, "BINARY", 1)),
                },
                {value[3] for value in command_indexes.values() if value[1] == "u"},
            )
            self.assertEqual(
                {
                    (("event_id", 0, "BINARY", 1),),
                    (("command_id", 0, "BINARY", 1), ("next_state_version", 0, "BINARY", 1)),
                },
                {value[3] for value in event_indexes.values() if value[1] == "u"},
            )
            self.assertEqual(
                [("state", 0), ("priority", 1), ("queue_sequence", 0)],
                [(item[0], item[1]) for item in command_indexes["workflow_commands_claim_order_idx"][3]],
            )
            self.assertEqual(
                [("state", 0), ("lease_expires_at", 0)],
                [(item[0], item[1]) for item in command_indexes["workflow_commands_lease_expiry_idx"][3]],
            )
            self.assertEqual(
                [("project_id", 0), ("queue_sequence", 0)],
                [(item[0], item[1]) for item in command_indexes["workflow_commands_project_sequence_idx"][3]],
            )
            self.assertEqual(
                [("phases", "project_id", "project_id", "RESTRICT", "RESTRICT"),
                 ("phases", "phase_id", "phase_id", "RESTRICT", "RESTRICT"),
                 ("development_runs", "development_run_id", "run_id", "RESTRICT", "RESTRICT")],
                [(row[2], row[3], row[4], row[5], row[6]) for row in connection.execute("PRAGMA foreign_key_list(workflow_commands)")],
            )
            self.assertEqual(
                [("workflow_commands", "command_id", "command_id", "RESTRICT", "RESTRICT")],
                [(row[2], row[3], row[4], row[5], row[6]) for row in connection.execute("PRAGMA foreign_key_list(workflow_command_events)")],
            )
            normalize = lambda value: " ".join(value.strip().rstrip(";").split()).lower()
            sql = dict(connection.execute("SELECT name, sql FROM sqlite_master WHERE type='table' AND name LIKE 'workflow_command%'").fetchall())
            self.assertEqual(normalize(PRODUCTION_MIGRATIONS[3].statements[0]), normalize(sql["workflow_commands"]))
            self.assertEqual(normalize(PRODUCTION_MIGRATIONS[3].statements[1]), normalize(sql["workflow_command_events"]))
        finally:
            connection.close()

    def test_populated_version_three_upgrades_preserving_all_p1_tables(self) -> None:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            apply_migrations(connection, "v3-at", PRODUCTION_MIGRATIONS[:3])
            connection.execute("INSERT INTO development_runs VALUES('run-1','digest','DRAFT',1,'created','updated')")
            connection.execute("INSERT INTO state_events VALUES('event-1','run-1','DRAFT','FEASIBILITY_CHECKING','ACCEPTED','event-at',1)")
            connection.execute("INSERT INTO phases VALUES('panam','phase-1','1',?)", ("a" * 64,))
            connection.execute("INSERT INTO milestone_contracts VALUES('panam','phase-1','milestone-1','1','objective','[]','[]','[]','[]','[]','[]','[]',?)", ("b" * 64,))
            connection.execute("INSERT INTO approvals VALUES('approval-1','1','SOURCE','subject',?,'PHASE','target','branch','commit','[]','[]','owner','at',?)", ("c" * 64, "d" * 64))
            connection.execute("INSERT INTO project_policies VALUES('panam','1','C:\\workspace\\panam')")
            connection.commit()
            before = {
                table: [tuple(row) for row in connection.execute(f"SELECT * FROM {table}")]
                for table in ("schema_migrations", "development_runs", "state_events", "phases", "milestone_contracts", "approvals", "project_policies")
            }
            apply_migrations(connection, "v4-at", PRODUCTION_MIGRATIONS)
            for table in before:
                expected = before[table]
                if table == "schema_migrations":
                    expected = expected + [(4, "v4-at")]
                self.assertEqual(expected, [tuple(row) for row in connection.execute(f"SELECT * FROM {table}")])
            self.assertEqual(0, connection.execute("SELECT count(*) FROM workflow_commands").fetchone()[0])
            self.assertEqual(0, connection.execute("SELECT count(*) FROM workflow_command_events").fetchone()[0])
        finally:
            connection.close()

    def test_migration_four_statement_and_ledger_failures_roll_back_version_three(self) -> None:
        for failure_kind in ("statement", "ledger"):
            with self.subTest(failure=failure_kind):
                path = self.directory / f"v4-{failure_kind}.sqlite3"
                connection = sqlite3.connect(path)
                connection.row_factory = sqlite3.Row
                try:
                    apply_migrations(connection, "v3-at", PRODUCTION_MIGRATIONS[:3])
                    connection.execute("INSERT INTO project_policies VALUES('panam','1','C:\\repo')")
                    connection.commit()
                    if failure_kind == "statement":
                        registry = PRODUCTION_MIGRATIONS[:3] + (
                            Migration(4, (PRODUCTION_MIGRATIONS[3].statements[0], "INSERT INTO absent_table VALUES(1)")),
                        )
                    else:
                        connection.execute(
                            "CREATE TRIGGER reject_v4 BEFORE INSERT ON schema_migrations "
                            "WHEN NEW.version=4 BEGIN SELECT RAISE(ABORT,'reject'); END"
                        )
                        connection.commit()
                        registry = PRODUCTION_MIGRATIONS
                    with self.assertRaises(MigrationError):
                        apply_migrations(connection, "v4-at", registry)
                    self.assertEqual([1, 2, 3], [row[0] for row in connection.execute("SELECT version FROM schema_migrations ORDER BY version")])
                    self.assertEqual(("panam", "1", "C:\\repo"), tuple(connection.execute("SELECT * FROM project_policies").fetchone()))
                    self.assertIsNone(connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='workflow_commands'").fetchone())
                    self.assertIsNone(connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='workflow_command_events'").fetchone())
                finally:
                    connection.close()


class SqliteWorkflowCommandRepositoryTest(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary_directory = tempfile.TemporaryDirectory()
        self.directory = Path(self._temporary_directory.name)
        self.path = self.directory / "queue.sqlite3"
        SqliteRunStore(self.path).initialize("initialized-at")
        self.repository = SqliteWorkflowCommandRepository(self.path)
        self.providers = QueueProviders()
        registry = CommandDefinitionRegistry((_queue_definition(),))
        self.service = DurableCommandQueueService(
            self.repository,
            registry,
            10,
            clock=self.providers.clock,
            id_factory=self.providers.identifier,
        )

    def tearDown(self) -> None:
        self._temporary_directory.cleanup()

    def _enqueue(self, *, key: str = "key-1", priority: int = 0, value: int = 1) -> QueueResult:
        return self.service.enqueue(
            project_id="panam",
            command_kind="TEST_COMMAND",
            command_schema_version=1,
            payload={"value": value},
            idempotency_key=key,
            actor_id="requester",
            priority=priority,
        )

    def test_enqueue_idempotency_queries_reopen_and_ordering(self) -> None:
        first = self._enqueue(priority=1)
        self.assertEqual(QueueResultCode.APPLIED, first.code)
        duplicate = self._enqueue(priority=1)
        self.assertEqual(QueueResultCode.EXISTING_IDENTICAL, duplicate.code)
        conflict = self._enqueue(priority=1, value=2)
        self.assertEqual(QueueResultCode.IDEMPOTENCY_CONFLICT, conflict.code)
        second = self._enqueue(key="key-2", priority=9)
        self.assertEqual(QueueResultCode.APPLIED, second.code)
        listed = self.service.list_project("panam")
        self.assertEqual(QueueResultCode.LISTED, listed.code)
        self.assertEqual((first.command.command_id, second.command.command_id), tuple(command.command_id for command in listed.commands))
        reopened = SqliteWorkflowCommandRepository(self.path)
        self.assertEqual(first.command, reopened.get(first.command.command_id))
        self.assertEqual(1, len(reopened.history(first.command.command_id)))

    def test_database_checks_reject_invalid_grammar_and_cross_field_rows(self) -> None:
        enqueued = self._enqueue()
        command_id = enqueued.command.command_id
        event_id = enqueued.event.event_id
        connection = sqlite3.connect(self.path)
        try:
            before = tuple(connection.iterdump())
            invalid_updates = (
                ("UPDATE workflow_commands SET command_id=? WHERE command_id=?", ("-0000000-0000-0000-0000-000000000001", command_id)),
                ("UPDATE workflow_commands SET created_at=? WHERE command_id=?", ("-026-08-24T12:00:00.000000Z", command_id)),
                ("UPDATE workflow_commands SET command_schema_version=0 WHERE command_id=?", (command_id,)),
                ("UPDATE workflow_commands SET payload_json='x' WHERE command_id=?", (command_id,)),
                ("UPDATE workflow_commands SET intent_digest=? WHERE command_id=?", ("A" * 64, command_id)),
                ("UPDATE workflow_commands SET idempotency_key='-bad' WHERE command_id=?", (command_id,)),
                ("UPDATE workflow_commands SET priority=101 WHERE command_id=?", (command_id,)),
                ("UPDATE workflow_commands SET state='UNKNOWN' WHERE command_id=?", (command_id,)),
                ("UPDATE workflow_commands SET state_version=0 WHERE command_id=?", (command_id,)),
                ("UPDATE workflow_commands SET claim_count=-1 WHERE command_id=?", (command_id,)),
                ("UPDATE workflow_commands SET lease_owner='worker' WHERE command_id=?", (command_id,)),
                ("UPDATE workflow_commands SET state='CLAIMED', lease_owner='worker', lease_acquired_at=created_at, lease_expires_at=created_at WHERE command_id=?", (command_id,)),
                ("UPDATE workflow_commands SET cancellation_requested_by='actor' WHERE command_id=?", (command_id,)),
                ("UPDATE workflow_commands SET failure_code='FAILED_TEST' WHERE command_id=?", (command_id,)),
                ("UPDATE workflow_commands SET started_at=created_at WHERE command_id=?", (command_id,)),
                ("UPDATE workflow_commands SET completed_at=created_at WHERE command_id=?", (command_id,)),
                ("UPDATE workflow_command_events SET event_id=? WHERE event_id=?", ("-0000000-0000-0000-0000-000000000002", event_id)),
                ("UPDATE workflow_command_events SET event_kind='UNKNOWN' WHERE event_id=?", (event_id,)),
                ("UPDATE workflow_command_events SET actor_id='-bad' WHERE event_id=?", (event_id,)),
                ("UPDATE workflow_command_events SET occurred_at='-026-08-24T12:00:00.000000Z' WHERE event_id=?", (event_id,)),
                ("UPDATE workflow_command_events SET claim_count=-1 WHERE event_id=?", (event_id,)),
                ("UPDATE workflow_command_events SET next_state_version=2 WHERE event_id=?", (event_id,)),
                ("UPDATE workflow_command_events SET lease_owner='worker' WHERE event_id=?", (event_id,)),
            )
            for statement, parameters in invalid_updates:
                with self.subTest(statement=statement), self.assertRaises(sqlite3.IntegrityError):
                    connection.execute(statement, parameters)
                connection.rollback()
                self.assertEqual(before, tuple(connection.iterdump()))
        finally:
            connection.close()

    def test_exact_schema_validation_rejects_extra_table_and_event_index(self) -> None:
        self._enqueue()
        connection = sqlite3.connect(self.path)
        try:
            connection.execute("CREATE TABLE extra_queue_scope(value INTEGER)")
            connection.commit()
        finally:
            connection.close()
        with self.assertRaises(RepositoryError) as raised:
            self.repository.list_project("panam")
        self.assertEqual(RepositoryFailureCode.SCHEMA_MISMATCH, raised.exception.code)

        connection = sqlite3.connect(self.path)
        try:
            connection.execute("DROP TABLE extra_queue_scope")
            connection.execute("CREATE INDEX unexpected_event_idx ON workflow_command_events(actor_id)")
            connection.commit()
        finally:
            connection.close()
        with self.assertRaises(RepositoryError) as raised:
            self.repository.list_project("panam")
        self.assertEqual(RepositoryFailureCode.SCHEMA_MISMATCH, raised.exception.code)

    def test_relationship_foreign_keys_and_read_queries_never_migrate(self) -> None:
        with self.assertRaises(RepositoryError):
            self.service.enqueue(
                project_id="panam", development_run_id="missing-run", phase_id=None,
                command_kind="TEST_COMMAND", command_schema_version=1,
                payload={"value": 1}, idempotency_key="missing-run", actor_id="actor",
            )
        with self.assertRaises(RepositoryError):
            self.service.enqueue(
                project_id="panam", development_run_id=None, phase_id="missing-phase",
                command_kind="TEST_COMMAND", command_schema_version=1,
                payload={"value": 1}, idempotency_key="missing-phase", actor_id="actor",
            )
        connection = sqlite3.connect(self.path)
        try:
            connection.execute("INSERT INTO development_runs VALUES('run-1','digest','DRAFT',1,'created','updated')")
            connection.execute("INSERT INTO phases VALUES('panam','phase-1','1',?)", ("a" * 64,))
            connection.commit()
        finally:
            connection.close()
        linked = self.service.enqueue(
            project_id="panam", development_run_id="run-1", phase_id="phase-1",
            command_kind="TEST_COMMAND", command_schema_version=1,
            payload={"value": 1}, idempotency_key="linked", actor_id="actor",
        )
        self.assertEqual(QueueResultCode.APPLIED, linked.code)
        with (
            patch.object(SqliteRunStore, "initialize", side_effect=AssertionError("initialize")) as initialize,
            patch("panam_development_loop.sqlite_migrations.initialize_database", side_effect=AssertionError("migrate")) as migrate,
        ):
            self.service.get(linked.command.command_id)
            self.service.list_project("panam")
            self.service.history(linked.command.command_id)
        initialize.assert_not_called()
        migrate.assert_not_called()

    def test_full_success_and_failure_paths(self) -> None:
        enqueued = self._enqueue()
        command = enqueued.command
        claimed = self.service.claim_next(lease_owner="worker-1")
        self.assertEqual((WorkflowCommandState.CLAIMED, 2, 1), (claimed.command.state, claimed.command.state_version, claimed.command.claim_count))
        self.providers.current += timedelta(seconds=1)
        running = self.service.mark_running(command_id=command.command_id, expected_state=WorkflowCommandState.CLAIMED, expected_state_version=2, lease_owner="worker-1")
        self.assertEqual(WorkflowCommandState.RUNNING, running.command.state)
        self.providers.current += timedelta(seconds=1)
        succeeded = self.service.mark_succeeded(command_id=command.command_id, expected_state=WorkflowCommandState.RUNNING, expected_state_version=3, lease_owner="worker-1")
        self.assertEqual(WorkflowCommandState.SUCCEEDED, succeeded.command.state)
        self.assertEqual((WorkflowCommandEventKind.ENQUEUED, WorkflowCommandEventKind.CLAIMED, WorkflowCommandEventKind.STARTED, WorkflowCommandEventKind.SUCCEEDED), tuple(event.event_kind for event in self.service.history(command.command_id).events))
        terminal_reuse = self._enqueue()
        self.assertEqual(QueueResultCode.EXISTING_IDENTICAL, terminal_reuse.code)
        self.assertEqual(succeeded.command, terminal_reuse.command)
        self.assertIsNone(terminal_reuse.event)
        self.assertEqual(4, len(self.service.history(command.command_id).events))
        terminal = self.service.mark_failed(command_id=command.command_id, expected_state=WorkflowCommandState.RUNNING, expected_state_version=4, lease_owner="worker-1", failure_code="FAILED_TEST")
        self.assertEqual(QueueResultCode.TERMINAL_OBSERVED, terminal.code)

    def test_all_normal_result_codes_and_remaining_event_routes(self) -> None:
        absent = str(UUID(int=999))
        self.assertEqual(QueueResultCode.NOT_FOUND, self.service.get(absent).code)
        self.assertEqual(QueueResultCode.NOT_FOUND, self.service.history(absent).code)
        self.assertEqual(QueueResultCode.NO_ELIGIBLE_COMMAND, self.service.claim_next(lease_owner="worker").code)
        missing = self.service.mark_running(
            command_id=absent, expected_state=WorkflowCommandState.CLAIMED,
            expected_state_version=1, lease_owner="worker",
        )
        self.assertEqual(QueueResultCode.NOT_FOUND, missing.code)

        active = self._enqueue(key="routes")
        claimed = self.service.claim_next(lease_owner="worker")
        not_expired = self.service.recover_expired_claim(
            command_id=active.command.command_id, expected_state=WorkflowCommandState.CLAIMED,
            expected_state_version=2, recovery_actor="recovery",
        )
        self.assertEqual(QueueResultCode.LEASE_NOT_EXPIRED, not_expired.code)
        no_request = self.service.acknowledge_cancellation(
            command_id=active.command.command_id, expected_state=WorkflowCommandState.CLAIMED,
            expected_state_version=2, lease_owner="worker",
        )
        self.assertEqual(QueueResultCode.CANCELLATION_NOT_REQUESTED, no_request.code)
        self.providers.current += timedelta(seconds=1)
        renewed_claimed = self.service.renew_lease(
            command_id=active.command.command_id, expected_state=WorkflowCommandState.CLAIMED,
            expected_state_version=2, lease_owner="worker",
        )
        self.assertEqual((WorkflowCommandState.CLAIMED, 3, 1, WorkflowCommandEventKind.LEASE_RENEWED), (renewed_claimed.command.state, renewed_claimed.command.state_version, renewed_claimed.command.claim_count, renewed_claimed.event.event_kind))
        self.providers.current += timedelta(seconds=1)
        running = self.service.mark_running(
            command_id=active.command.command_id, expected_state=WorkflowCommandState.CLAIMED,
            expected_state_version=3, lease_owner="worker",
        )
        self.providers.current += timedelta(seconds=1)
        renewed_running = self.service.renew_lease(
            command_id=active.command.command_id, expected_state=WorkflowCommandState.RUNNING,
            expected_state_version=4, lease_owner="worker",
        )
        self.assertEqual((WorkflowCommandState.RUNNING, 5, 1, WorkflowCommandEventKind.LEASE_RENEWED), (renewed_running.command.state, renewed_running.command.state_version, renewed_running.command.claim_count, renewed_running.event.event_kind))
        self.providers.current += timedelta(seconds=1)
        requested = self.service.request_cancellation(
            command_id=active.command.command_id, expected_state=WorkflowCommandState.RUNNING,
            expected_state_version=5, requested_by="requester", reason_code="STOPPED",
        )
        repeated = self.service.request_cancellation(
            command_id=active.command.command_id, expected_state=WorkflowCommandState.RUNNING,
            expected_state_version=6, requested_by="requester", reason_code="STOPPED",
        )
        self.assertEqual(QueueResultCode.CANCELLATION_ALREADY_REQUESTED, repeated.code)
        self.providers.current += timedelta(seconds=1)
        cancelled = self.service.acknowledge_cancellation(
            command_id=active.command.command_id, expected_state=WorkflowCommandState.RUNNING,
            expected_state_version=6, lease_owner="worker",
        )
        self.assertEqual((WorkflowCommandState.CANCELLED, WorkflowCommandEventKind.CANCELLED), (cancelled.command.state, cancelled.event.event_kind))

        failing = self._enqueue(key="failure")
        claimed_failure = self.service.claim_next(lease_owner="worker")
        self.providers.current += timedelta(seconds=1)
        running_failure = self.service.mark_running(
            command_id=failing.command.command_id, expected_state=WorkflowCommandState.CLAIMED,
            expected_state_version=2, lease_owner="worker",
        )
        self.providers.current += timedelta(seconds=1)
        failed = self.service.mark_failed(
            command_id=failing.command.command_id, expected_state=WorkflowCommandState.RUNNING,
            expected_state_version=3, lease_owner="worker", failure_code="FAILED_TEST",
        )
        self.assertEqual((WorkflowCommandState.FAILED, "FAILED_TEST", WorkflowCommandEventKind.FAILED), (failed.command.state, failed.command.failure_code, failed.event.event_kind))

        expiring = self._enqueue(key="expired-owner")
        expired_claim = self.service.claim_next(lease_owner="worker")
        self.providers.current += timedelta(seconds=10)
        expired = self.service.renew_lease(
            command_id=expiring.command.command_id, expected_state=WorkflowCommandState.CLAIMED,
            expected_state_version=2, lease_owner="worker",
        )
        self.assertEqual(QueueResultCode.LEASE_EXPIRED, expired.code)

    def test_cancellation_request_triplet_survives_success_and_failure(self) -> None:
        for terminal_operation in ("mark_succeeded", "mark_failed"):
            with self.subTest(operation=terminal_operation):
                enqueued = self._enqueue(key=f"preserve-{terminal_operation}")
                claimed = self.service.claim_next(lease_owner="worker")
                self.providers.current += timedelta(seconds=1)
                running = self.service.mark_running(
                    command_id=enqueued.command.command_id,
                    expected_state=WorkflowCommandState.CLAIMED,
                    expected_state_version=2,
                    lease_owner="worker",
                )
                self.providers.current += timedelta(seconds=1)
                requested = self.service.request_cancellation(
                    command_id=enqueued.command.command_id,
                    expected_state=WorkflowCommandState.RUNNING,
                    expected_state_version=3,
                    requested_by="requester",
                    reason_code="STOPPED",
                )
                self.providers.current += timedelta(seconds=1)
                arguments = dict(
                    command_id=enqueued.command.command_id,
                    expected_state=WorkflowCommandState.RUNNING,
                    expected_state_version=4,
                    lease_owner="worker",
                )
                if terminal_operation == "mark_failed":
                    arguments["failure_code"] = "FAILED_TEST"
                terminal = getattr(self.service, terminal_operation)(**arguments)
                self.assertEqual(
                    (requested.command.cancellation_requested_at, "requester", "STOPPED"),
                    (
                        terminal.command.cancellation_requested_at,
                        terminal.command.cancellation_requested_by,
                        terminal.command.cancellation_reason_code,
                    ),
                )
                if terminal_operation == "mark_succeeded":
                    self.assertIsNone(terminal.event.reason_code)
                else:
                    self.assertEqual("FAILED_TEST", terminal.event.reason_code)

    def test_claim_priority_fifo_and_idempotency_digest_dimensions(self) -> None:
        low = self._enqueue(key="low", priority=1)
        high_first = self._enqueue(key="high-1", priority=9)
        high_second = self._enqueue(key="high-2", priority=9)
        claimed = tuple(self.service.claim_next(lease_owner=f"worker-{index}").command.command_id for index in range(3))
        self.assertEqual(
            (high_first.command.command_id, high_second.command.command_id, low.command.command_id),
            claimed,
        )

        base = _queue_envelope(idempotency_key="digest-key")
        first = self.repository.enqueue(
            command_id=str(UUID(int=500)), event_id=str(UUID(int=501)), envelope=base,
            actor_id="actor", occurred_at="2026-08-24T12:00:00.000000Z",
        )
        self.assertEqual(QueueResultCode.APPLIED, first.code)

        def envelope(**updates: object) -> ValidatedCommandEnvelope:
            values = {
                "project_id": base.project_id, "development_run_id": base.development_run_id,
                "phase_id": base.phase_id, "command_kind": base.command_kind,
                "command_schema_version": base.command_schema_version,
                "payload": json.loads(base.payload_json), "priority": base.priority,
                "idempotency_key": base.idempotency_key,
            }
            values.update(updates)
            payload = values.pop("payload")
            digest = _intent_digest(payload=payload, **{key: values[key] for key in ("project_id", "development_run_id", "phase_id", "command_kind", "command_schema_version", "priority")})
            return ValidatedCommandEnvelope(
                values["project_id"], values["development_run_id"], values["phase_id"],
                values["command_kind"], values["command_schema_version"],
                _canonical_queue_payload(payload), digest, values["idempotency_key"], values["priority"],
            )

        variants = (
            envelope(development_run_id="run-1"), envelope(phase_id="phase-1"),
            envelope(command_kind="OTHER_COMMAND"), envelope(command_schema_version=2),
            envelope(payload={"value": 2}), envelope(priority=1),
        )
        for index, candidate in enumerate(variants, start=510):
            with self.subTest(field=index):
                result = self.repository.enqueue(
                    command_id=str(UUID(int=index)), event_id=str(UUID(int=index + 100)),
                    envelope=candidate, actor_id="actor",
                    occurred_at="2026-08-24T12:00:00.000000Z",
                )
                self.assertEqual(QueueResultCode.IDEMPOTENCY_CONFLICT, result.code)
        independent_project = envelope(project_id="Panam")
        self.assertEqual(
            QueueResultCode.APPLIED,
            self.repository.enqueue(
                command_id=str(UUID(int=700)), event_id=str(UUID(int=701)),
                envelope=independent_project, actor_id="actor",
                occurred_at="2026-08-24T12:00:00.000000Z",
            ).code,
        )
        excluded_key = envelope(idempotency_key="digest-key-2")
        self.assertEqual(base.intent_digest, excluded_key.intent_digest)
        self.assertEqual(
            QueueResultCode.APPLIED,
            self.repository.enqueue(
                command_id=str(UUID(int=702)), event_id=str(UUID(int=703)),
                envelope=excluded_key, actor_id="actor",
                occurred_at="2026-08-24T12:00:00.000000Z",
            ).code,
        )

    def test_cancellation_and_recovery_paths(self) -> None:
        pending = self._enqueue()
        cancelled = self.service.request_cancellation(command_id=pending.command.command_id, expected_state=WorkflowCommandState.PENDING, expected_state_version=1, requested_by="owner", reason_code="STOPPED")
        self.assertEqual(WorkflowCommandState.CANCELLED, cancelled.command.state)

        active = self._enqueue(key="active")
        claimed = self.service.claim_next(lease_owner="worker")
        requested = self.service.request_cancellation(command_id=active.command.command_id, expected_state=WorkflowCommandState.CLAIMED, expected_state_version=2, requested_by="owner", reason_code="STOPPED")
        self.assertEqual(WorkflowCommandState.CLAIMED, requested.command.state)
        acknowledged = self.service.acknowledge_cancellation(command_id=active.command.command_id, expected_state=WorkflowCommandState.CLAIMED, expected_state_version=3, lease_owner="worker")
        self.assertEqual(WorkflowCommandState.CANCELLED, acknowledged.command.state)

        recoverable = self._enqueue(key="recover")
        recovered_claim = self.service.claim_next(lease_owner="worker")
        self.providers.current += timedelta(seconds=11)
        recovered = self.service.recover_expired_claim(command_id=recoverable.command.command_id, expected_state=WorkflowCommandState.CLAIMED, expected_state_version=2, recovery_actor="recovery")
        self.assertEqual(WorkflowCommandState.PENDING, recovered.command.state)
        self.service.request_cancellation(
            command_id=recoverable.command.command_id,
            expected_state=WorkflowCommandState.PENDING,
            expected_state_version=3,
            requested_by="owner",
            reason_code="STOPPED",
        )

        requested_recovery = self._enqueue(key="recover-cancel")
        claimed_recovery = self.service.claim_next(lease_owner="worker")
        requested = self.service.request_cancellation(
            command_id=requested_recovery.command.command_id,
            expected_state=WorkflowCommandState.CLAIMED,
            expected_state_version=2,
            requested_by="owner",
            reason_code="STOPPED",
        )
        self.providers.current += timedelta(seconds=11)
        recovered_cancel = self.service.recover_expired_claim(
            command_id=requested_recovery.command.command_id,
            expected_state=WorkflowCommandState.CLAIMED,
            expected_state_version=3,
            recovery_actor="recovery",
        )
        self.assertEqual((WorkflowCommandState.CANCELLED, WorkflowCommandEventKind.CANCELLED), (recovered_cancel.command.state, recovered_cancel.event.event_kind))
        self.assertEqual("worker", recovered_cancel.event.lease_owner)

    def test_expired_running_reconciliation_and_fencing(self) -> None:
        enqueued = self._enqueue()
        claimed = self.service.claim_next(lease_owner="worker")
        stale = self.service.mark_running(command_id=enqueued.command.command_id, expected_state=WorkflowCommandState.CLAIMED, expected_state_version=1, lease_owner="wrong")
        self.assertEqual(QueueResultCode.CAS_CONFLICT, stale.code)
        wrong_owner = self.service.mark_running(command_id=enqueued.command.command_id, expected_state=WorkflowCommandState.CLAIMED, expected_state_version=2, lease_owner="wrong")
        self.assertEqual(QueueResultCode.LEASE_OWNER_MISMATCH, wrong_owner.code)
        running = self.service.mark_running(command_id=enqueued.command.command_id, expected_state=WorkflowCommandState.CLAIMED, expected_state_version=2, lease_owner="worker")
        self.providers.current += timedelta(seconds=11)
        reconciled = self.service.recover_expired_claim(command_id=enqueued.command.command_id, expected_state=WorkflowCommandState.RUNNING, expected_state_version=3, recovery_actor="recovery")
        self.assertEqual(QueueResultCode.RECONCILIATION_REQUIRED, reconciled.code)
        self.assertEqual(running.command, self.service.get(enqueued.command.command_id).command)

    def test_frozen_outcome_precedence_under_colliding_conditions(self) -> None:
        expired_command = self._enqueue(key="precedence-expired")
        expired_claim = self.service.claim_next(lease_owner="worker")
        self.providers.current += timedelta(seconds=11)
        stale_wrong_expired = self.service.mark_running(
            command_id=expired_command.command.command_id,
            expected_state=WorkflowCommandState.CLAIMED,
            expected_state_version=1,
            lease_owner="wrong",
        )
        self.assertEqual(QueueResultCode.CAS_CONFLICT, stale_wrong_expired.code)
        wrong_and_expired = self.service.mark_running(
            command_id=expired_command.command.command_id,
            expected_state=WorkflowCommandState.CLAIMED,
            expected_state_version=2,
            lease_owner="wrong",
        )
        self.assertEqual(QueueResultCode.LEASE_OWNER_MISMATCH, wrong_and_expired.code)

        no_request_command = self._enqueue(key="precedence-cancel")
        no_request_claim = self.service.claim_next(lease_owner="worker-2")
        cancellation_missing_and_wrong_owner = self.service.acknowledge_cancellation(
            command_id=no_request_command.command.command_id,
            expected_state=WorkflowCommandState.CLAIMED,
            expected_state_version=2,
            lease_owner="wrong",
        )
        self.assertEqual(
            QueueResultCode.CANCELLATION_NOT_REQUESTED,
            cancellation_missing_and_wrong_owner.code,
        )

        terminal_command = self._enqueue(key="precedence-terminal")
        terminal_claim = self.service.claim_next(lease_owner="worker-3")
        running = self.service.mark_running(
            command_id=terminal_command.command.command_id,
            expected_state=WorkflowCommandState.CLAIMED,
            expected_state_version=2,
            lease_owner="worker-3",
        )
        succeeded = self.service.mark_succeeded(
            command_id=terminal_command.command.command_id,
            expected_state=WorkflowCommandState.RUNNING,
            expected_state_version=3,
            lease_owner="worker-3",
        )
        terminal_and_stale = self.service.request_cancellation(
            command_id=terminal_command.command.command_id,
            expected_state=WorkflowCommandState.PENDING,
            expected_state_version=1,
            requested_by="actor",
            reason_code="STOPPED",
        )
        self.assertEqual(QueueResultCode.TERMINAL_OBSERVED, terminal_and_stale.code)

    def test_duplicate_event_rolls_back_claim(self) -> None:
        enqueued = self._enqueue()
        with self.assertRaises(RepositoryError):
            self.repository.claim_next(
                event_id=enqueued.event.event_id,
                lease_owner="worker",
                lease_acquired_at="2026-08-24T12:00:01.000000Z",
                lease_expires_at="2026-08-24T12:00:11.000000Z",
            )
        current = self.repository.get(enqueued.command.command_id)
        self.assertEqual((WorkflowCommandState.PENDING, 1), (current.state, current.state_version))
        self.assertEqual(1, len(self.repository.history(enqueued.command.command_id)))

    def test_every_mutation_family_rolls_back_row_when_event_insert_fails(self) -> None:
        operations = (
            "enqueue", "claim_next", "renew_lease", "mark_running",
            "request_cancellation", "acknowledge_cancellation",
            "mark_succeeded", "mark_failed", "recover_expired_claim",
        )
        for ordinal, operation in enumerate(operations, start=1):
            with self.subTest(operation=operation):
                path = self.directory / f"rollback-{ordinal}.sqlite3"
                SqliteRunStore(path).initialize("initialized-at")
                repository = SqliteWorkflowCommandRepository(path)
                providers = QueueProviders()
                service = DurableCommandQueueService(
                    repository, CommandDefinitionRegistry((_queue_definition(),)), 10,
                    clock=providers.clock, id_factory=providers.identifier,
                )
                command: WorkflowCommand | None = None
                if operation != "enqueue":
                    command = service.enqueue(
                        project_id="panam", command_kind="TEST_COMMAND",
                        command_schema_version=1, payload={"value": 1},
                        idempotency_key=f"key-{ordinal}", actor_id="actor",
                    ).command
                if operation in {"renew_lease", "mark_running", "acknowledge_cancellation", "mark_succeeded", "mark_failed", "recover_expired_claim"}:
                    claimed = service.claim_next(lease_owner="worker").command
                if operation == "acknowledge_cancellation":
                    requested = service.request_cancellation(
                        command_id=command.command_id, expected_state=WorkflowCommandState.CLAIMED,
                        expected_state_version=2, requested_by="actor", reason_code="STOPPED",
                    ).command
                if operation in {"mark_succeeded", "mark_failed"}:
                    running = service.mark_running(
                        command_id=command.command_id, expected_state=WorkflowCommandState.CLAIMED,
                        expected_state_version=2, lease_owner="worker",
                    ).command
                if operation == "recover_expired_claim":
                    providers.current += timedelta(seconds=11)

                connection = sqlite3.connect(path)
                try:
                    before = tuple(connection.iterdump())
                finally:
                    connection.close()
                arguments = {
                    "enqueue": dict(project_id="panam", command_kind="TEST_COMMAND", command_schema_version=1, payload={"value": 1}, idempotency_key="new-key", actor_id="actor"),
                    "claim_next": dict(lease_owner="worker"),
                    "renew_lease": dict(command_id=command.command_id if command else "", expected_state=WorkflowCommandState.CLAIMED, expected_state_version=2, lease_owner="worker"),
                    "mark_running": dict(command_id=command.command_id if command else "", expected_state=WorkflowCommandState.CLAIMED, expected_state_version=2, lease_owner="worker"),
                    "request_cancellation": dict(command_id=command.command_id if command else "", expected_state=WorkflowCommandState.PENDING, expected_state_version=1, requested_by="actor", reason_code="STOPPED"),
                    "acknowledge_cancellation": dict(command_id=command.command_id if command else "", expected_state=WorkflowCommandState.CLAIMED, expected_state_version=3, lease_owner="worker"),
                    "mark_succeeded": dict(command_id=command.command_id if command else "", expected_state=WorkflowCommandState.RUNNING, expected_state_version=3, lease_owner="worker"),
                    "mark_failed": dict(command_id=command.command_id if command else "", expected_state=WorkflowCommandState.RUNNING, expected_state_version=3, lease_owner="worker", failure_code="FAILED_TEST"),
                    "recover_expired_claim": dict(command_id=command.command_id if command else "", expected_state=WorkflowCommandState.CLAIMED, expected_state_version=2, recovery_actor="recovery"),
                }[operation]
                with patch.object(repository, "_insert_event", side_effect=sqlite3.OperationalError("injected event insert")):
                    with self.assertRaises(RepositoryError):
                        getattr(service, operation)(**arguments)
                connection = sqlite3.connect(path)
                try:
                    self.assertEqual(before, tuple(connection.iterdump()))
                finally:
                    connection.close()
                for failure_mode in ("row", "commit"):
                    with self.subTest(operation=operation, failure=failure_mode):
                        underlying = sqlite3.connect(path, timeout=0.0)
                        underlying.row_factory = sqlite3.Row
                        underlying.execute("PRAGMA foreign_keys=ON")
                        proxy = _ConnectionProxy(
                            underlying,
                            fail_statement=(
                                "INSERT INTO WORKFLOW_COMMANDS"
                                if failure_mode == "row" and operation == "enqueue"
                                else "UPDATE WORKFLOW_COMMANDS"
                                if failure_mode == "row"
                                else None
                            ),
                            fail_commit=failure_mode == "commit",
                        )
                        with patch(
                            "panam_development_loop.sqlite_repositories._open_connection",
                            return_value=proxy,
                        ):
                            with self.assertRaises(RepositoryError):
                                getattr(service, operation)(**arguments)
                        self.assertGreaterEqual(proxy.rollback_calls, 1)
                        self.assertEqual(1, proxy.close_calls)
                        connection = sqlite3.connect(path)
                        try:
                            self.assertEqual(before, tuple(connection.iterdump()))
                        finally:
                            connection.close()

    def test_corrupt_history_fails_closed_for_adjacency_sequence_time_and_count(self) -> None:
        corruptions = {
            "adjacency": (
                "UPDATE workflow_command_events SET prior_state='RUNNING', next_state='RUNNING' WHERE next_state_version=3",
                (),
            ),
            "event_sequence": (
                "UPDATE workflow_command_events SET event_sequence=99 WHERE next_state_version=3",
                (),
            ),
            "time_order": (
                "UPDATE workflow_command_events SET occurred_at='2026-08-24T12:00:00.500000Z' WHERE next_state_version=3",
                (),
            ),
            "claim_count": (
                "UPDATE workflow_command_events SET claim_count=2 WHERE next_state_version>=3",
                (),
            ),
        }
        for ordinal, (name, (statement, parameters)) in enumerate(corruptions.items(), start=1):
            with self.subTest(corruption=name):
                path = self.directory / f"corrupt-{ordinal}.sqlite3"
                SqliteRunStore(path).initialize("initialized-at")
                providers = QueueProviders()
                service = DurableCommandQueueService(
                    SqliteWorkflowCommandRepository(path),
                    CommandDefinitionRegistry((_queue_definition(),)), 10,
                    clock=providers.clock, id_factory=providers.identifier,
                )
                enqueued = service.enqueue(project_id="panam", command_kind="TEST_COMMAND", command_schema_version=1, payload={"value": 1}, idempotency_key="key", actor_id="actor")
                providers.current += timedelta(seconds=1)
                claimed = service.claim_next(lease_owner="worker")
                providers.current += timedelta(seconds=1)
                requested = service.request_cancellation(command_id=enqueued.command.command_id, expected_state=WorkflowCommandState.CLAIMED, expected_state_version=2, requested_by="actor", reason_code="STOPPED")
                providers.current += timedelta(seconds=1)
                running = service.mark_running(command_id=enqueued.command.command_id, expected_state=WorkflowCommandState.CLAIMED, expected_state_version=3, lease_owner="worker")
                connection = sqlite3.connect(path)
                try:
                    connection.execute("PRAGMA ignore_check_constraints=ON")
                    connection.execute(statement, parameters)
                    if name == "time_order":
                        connection.execute("UPDATE workflow_commands SET cancellation_requested_at='2026-08-24T12:00:00.500000Z'")
                    if name == "claim_count":
                        connection.execute("UPDATE workflow_commands SET claim_count=2")
                    connection.commit()
                finally:
                    connection.close()
                with self.assertRaises(RepositoryError) as raised:
                    SqliteWorkflowCommandRepository(path).get(enqueued.command.command_id)
                self.assertEqual(RepositoryFailureCode.MALFORMED_PERSISTED_PAYLOAD, raised.exception.code)

    def test_corrupt_payload_digest_calendar_and_event_facts_fail_closed(self) -> None:
        corruptions = {
            "payload": (
                "UPDATE workflow_commands SET payload_json='{\"value\": 1}'",
                None,
            ),
            "digest": (
                "UPDATE workflow_commands SET intent_digest=?",
                ("0" * 64,),
            ),
            "calendar": (
                "UPDATE workflow_commands SET created_at='2026-02-30T12:00:00.000000Z', updated_at='2026-02-30T12:00:00.000000Z'",
                "UPDATE workflow_command_events SET occurred_at='2026-02-30T12:00:00.000000Z'",
            ),
            "event_reason": (
                "UPDATE workflow_command_events SET reason_code='STOPPED'",
                None,
            ),
        }
        for ordinal, (name, (first_statement, second)) in enumerate(corruptions.items(), start=1):
            with self.subTest(corruption=name):
                path = self.directory / f"payload-corrupt-{ordinal}.sqlite3"
                SqliteRunStore(path).initialize("initialized-at")
                service = DurableCommandQueueService(
                    SqliteWorkflowCommandRepository(path),
                    CommandDefinitionRegistry((_queue_definition(),)), 10,
                    clock=lambda: datetime(2026, 8, 24, 12, tzinfo=timezone.utc),
                    id_factory=iter((str(UUID(int=ordinal * 10 + 1)), str(UUID(int=ordinal * 10 + 2)))).__next__,
                )
                enqueued = service.enqueue(
                    project_id="panam", command_kind="TEST_COMMAND",
                    command_schema_version=1, payload={"value": 1},
                    idempotency_key="key", actor_id="actor",
                )
                connection = sqlite3.connect(path)
                try:
                    if name == "digest":
                        connection.execute(first_statement, second)
                    else:
                        connection.execute(first_statement)
                        if isinstance(second, str):
                            connection.execute(second)
                    connection.commit()
                finally:
                    connection.close()
                with self.assertRaises(RepositoryError) as raised:
                    SqliteWorkflowCommandRepository(path).get(enqueued.command.command_id)
                self.assertEqual(RepositoryFailureCode.MALFORMED_PERSISTED_PAYLOAD, raised.exception.code)

    def test_history_rejects_cancel_without_request_and_late_renewal(self) -> None:
        for ordinal, corruption in enumerate(
            (
                "cancel_without_request",
                "late_renewal",
                "repeated_request",
                "mismatched_cancel_reason",
            ),
            start=1,
        ):
            with self.subTest(corruption=corruption):
                path = self.directory / f"history-lease-{ordinal}.sqlite3"
                SqliteRunStore(path).initialize("initialized-at")
                providers = QueueProviders()
                service = DurableCommandQueueService(
                    SqliteWorkflowCommandRepository(path),
                    CommandDefinitionRegistry((_queue_definition(),)), 10,
                    clock=providers.clock, id_factory=providers.identifier,
                )
                enqueued = service.enqueue(
                    project_id="panam", command_kind="TEST_COMMAND",
                    command_schema_version=1, payload={"value": 1},
                    idempotency_key="key", actor_id="actor",
                )
                claimed = service.claim_next(lease_owner="worker")
                command_id = enqueued.command.command_id
                if corruption in {"repeated_request", "mismatched_cancel_reason"}:
                    requested = service.request_cancellation(
                        command_id=command_id,
                        expected_state=WorkflowCommandState.CLAIMED,
                        expected_state_version=2,
                        requested_by="requester",
                        reason_code="STOPPED",
                    )
                connection = sqlite3.connect(path)
                try:
                    if corruption == "cancel_without_request":
                        occurred = "2026-08-24T12:00:05.000000Z"
                        connection.execute(
                            "UPDATE workflow_commands SET state='CANCELLED', state_version=3, "
                            "lease_owner=NULL, lease_acquired_at=NULL, lease_expires_at=NULL, "
                            "cancellation_requested_at=?, cancellation_requested_by='requester', "
                            "cancellation_reason_code='STOPPED', completed_at=?, updated_at=? "
                            "WHERE command_id=?",
                            (occurred, occurred, occurred, command_id),
                        )
                        connection.execute(
                            "INSERT INTO workflow_command_events(event_id,command_id,event_kind,prior_state,next_state,prior_state_version,next_state_version,actor_id,occurred_at,lease_owner,lease_expires_at,claim_count,reason_code) "
                            "VALUES(?,?,'CANCELLED','CLAIMED','CANCELLED',2,3,'worker',?,'worker','2026-08-24T12:00:10.000000Z',1,'STOPPED')",
                            (str(UUID(int=900)), command_id, occurred),
                        )
                    else:
                        if corruption == "repeated_request":
                            occurred = "2026-08-24T12:00:05.000000Z"
                            connection.execute(
                                "UPDATE workflow_commands SET state_version=4, updated_at=? WHERE command_id=?",
                                (occurred, command_id),
                            )
                            connection.execute(
                                "INSERT INTO workflow_command_events(event_id,command_id,event_kind,prior_state,next_state,prior_state_version,next_state_version,actor_id,occurred_at,lease_owner,lease_expires_at,claim_count,reason_code) "
                                "VALUES(?,?,'CANCELLATION_REQUESTED','CLAIMED','CLAIMED',3,4,'other-requester',?,'worker','2026-08-24T12:00:10.000000Z',1,'STOPPED')",
                                (str(UUID(int=902)), command_id, occurred),
                            )
                        elif corruption == "mismatched_cancel_reason":
                            occurred = "2026-08-24T12:00:05.000000Z"
                            connection.execute(
                                "UPDATE workflow_commands SET state='CANCELLED', state_version=4, "
                                "lease_owner=NULL, lease_acquired_at=NULL, lease_expires_at=NULL, "
                                "completed_at=?, updated_at=? WHERE command_id=?",
                                (occurred, occurred, command_id),
                            )
                            connection.execute(
                                "INSERT INTO workflow_command_events(event_id,command_id,event_kind,prior_state,next_state,prior_state_version,next_state_version,actor_id,occurred_at,lease_owner,lease_expires_at,claim_count,reason_code) "
                                "VALUES(?,?,'CANCELLED','CLAIMED','CANCELLED',3,4,'worker',?,'worker','2026-08-24T12:00:10.000000Z',1,'OTHER')",
                                (str(UUID(int=903)), command_id, occurred),
                            )
                        else:
                            occurred = "2026-08-24T12:00:11.000000Z"
                            new_expiry = "2026-08-24T12:00:21.000000Z"
                            connection.execute(
                                "UPDATE workflow_commands SET state_version=3, lease_expires_at=?, updated_at=? WHERE command_id=?",
                                (new_expiry, occurred, command_id),
                            )
                            connection.execute(
                                "INSERT INTO workflow_command_events(event_id,command_id,event_kind,prior_state,next_state,prior_state_version,next_state_version,actor_id,occurred_at,lease_owner,lease_expires_at,claim_count,reason_code) "
                                "VALUES(?,?,'LEASE_RENEWED','CLAIMED','CLAIMED',2,3,'worker',?,'worker',?,1,NULL)",
                                (str(UUID(int=901)), command_id, occurred, new_expiry),
                            )
                    connection.commit()
                finally:
                    connection.close()
                with self.assertRaises(RepositoryError) as raised:
                    SqliteWorkflowCommandRepository(path).get(command_id)
                self.assertEqual(RepositoryFailureCode.MALFORMED_PERSISTED_PAYLOAD, raised.exception.code)

    def test_mutation_time_regression_is_value_error_and_snapshot_unchanged(self) -> None:
        enqueued = self._enqueue()
        self.providers.current += timedelta(seconds=1)
        claimed = self.service.claim_next(lease_owner="worker")
        connection = sqlite3.connect(self.path)
        try:
            before = tuple(connection.iterdump())
        finally:
            connection.close()
        self.providers.current -= timedelta(microseconds=1)
        with self.assertRaises(ValueError):
            self.service.renew_lease(
                command_id=enqueued.command.command_id,
                expected_state=WorkflowCommandState.CLAIMED,
                expected_state_version=2,
                lease_owner="worker",
            )
        connection = sqlite3.connect(self.path)
        try:
            self.assertEqual(before, tuple(connection.iterdump()))
        finally:
            connection.close()

    def test_busy_claim_is_typed_and_not_retried(self) -> None:
        self._enqueue()
        lock = sqlite3.connect(self.path, timeout=0.0)
        lock.execute("BEGIN IMMEDIATE")
        try:
            with patch(
                "panam_development_loop.sqlite_repositories._open_connection",
                wraps=sqlite_repository_module._open_connection,
            ) as opened:
                result = self.service.claim_next(lease_owner="worker")
            self.assertEqual(1, opened.call_count)
            self.assertEqual(QueueResultCode.TRANSIENT_CONTENTION, result.code)
        finally:
            lock.rollback()
            lock.close()
        self.assertEqual(WorkflowCommandState.PENDING, self.service.list_project("panam").commands[0].state)

    def test_exclusive_lock_during_schema_validation_is_typed_contention(self) -> None:
        self._enqueue()
        lock = sqlite3.connect(self.path, timeout=0.0)
        lock.execute("BEGIN EXCLUSIVE")
        try:
            result = self.service.claim_next(lease_owner="worker")
            self.assertEqual(QueueResultCode.TRANSIENT_CONTENTION, result.code)
            self.assertEqual(QueueMutationKind.CLAIM_NEXT, result.mutation_kind)
        finally:
            lock.rollback()
            lock.close()
        current = self.service.list_project("panam").commands[0]
        self.assertEqual((WorkflowCommandState.PENDING, 1, 0), (current.state, current.state_version, current.claim_count))

    def test_claim_contention_has_one_winner(self) -> None:
        self._enqueue()
        barrier = threading.Barrier(2)
        results: list[QueueResult] = []
        errors: list[BaseException] = []

        def claim(owner: str, identifier_base: int) -> None:
            providers = QueueProviders()
            providers.id_calls = identifier_base
            registry = CommandDefinitionRegistry((_queue_definition(),))
            service = DurableCommandQueueService(SqliteWorkflowCommandRepository(self.path), registry, 10, clock=providers.clock, id_factory=providers.identifier)
            try:
                barrier.wait(timeout=5)
                results.append(service.claim_next(lease_owner=owner))
            except BaseException as error:
                errors.append(error)

        threads = [threading.Thread(target=claim, args=("worker-a", 100)), threading.Thread(target=claim, args=("worker-b", 200))]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=5)
        self.assertEqual([], errors)
        self.assertEqual(1, sum(result.code is QueueResultCode.APPLIED for result in results))
        self.assertEqual(1, sum(result.code in {QueueResultCode.NO_ELIGIBLE_COMMAND, QueueResultCode.TRANSIENT_CONTENTION} for result in results))
        current = self.service.list_project("panam").commands[0]
        history = self.service.history(current.command_id).events
        self.assertEqual((WorkflowCommandState.CLAIMED, 2, 1), (current.state, current.state_version, current.claim_count))
        self.assertEqual((WorkflowCommandEventKind.ENQUEUED, WorkflowCommandEventKind.CLAIMED), tuple(event.event_kind for event in history))
        self.assertEqual((1, 2), tuple(event.next_state_version for event in history))

    def test_concurrent_same_key_enqueue_has_one_durable_identity(self) -> None:
        barrier = threading.Barrier(2)
        results: list[QueueResult] = []
        errors: list[BaseException] = []

        def enqueue(identifier_base: int) -> None:
            providers = QueueProviders()
            providers.id_calls = identifier_base
            service = DurableCommandQueueService(
                SqliteWorkflowCommandRepository(self.path),
                CommandDefinitionRegistry((_queue_definition(),)), 10,
                clock=providers.clock, id_factory=providers.identifier,
            )
            try:
                barrier.wait(timeout=5)
                results.append(service.enqueue(
                    project_id="panam", command_kind="TEST_COMMAND",
                    command_schema_version=1, payload={"value": 1},
                    idempotency_key="concurrent-key", actor_id="actor",
                ))
            except BaseException as error:
                errors.append(error)

        threads = [threading.Thread(target=enqueue, args=(100,)), threading.Thread(target=enqueue, args=(200,))]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=5)
        self.assertEqual([], errors)
        self.assertEqual(1, sum(result.code is QueueResultCode.APPLIED for result in results))
        self.assertEqual(1, sum(result.code in {QueueResultCode.EXISTING_IDENTICAL, QueueResultCode.TRANSIENT_CONTENTION} for result in results))
        listed = self.service.list_project("panam")
        self.assertEqual(1, len(listed.commands))
        self.assertEqual(1, len(self.service.history(listed.commands[0].command_id).events))
        if any(result.code is QueueResultCode.TRANSIENT_CONTENTION for result in results):
            identical = self.service.enqueue(
                project_id="panam", command_kind="TEST_COMMAND",
                command_schema_version=1, payload={"value": 1},
                idempotency_key="concurrent-key", actor_id="actor",
            )
            self.assertEqual(QueueResultCode.EXISTING_IDENTICAL, identical.code)

    def test_no_free_form_execution_gateways(self) -> None:
        with (
            patch("subprocess.run", side_effect=AssertionError("process")) as process,
            patch("os.system", side_effect=AssertionError("shell")) as shell,
            patch("urllib.request.urlopen", side_effect=AssertionError("network")) as network,
            patch("threading.Thread.start", side_effect=AssertionError("thread")) as thread_start,
            patch.object(Path, "write_text", side_effect=AssertionError("file write")) as file_write,
            patch.object(TransitionService, "transition", side_effect=AssertionError("transition")) as transition,
            patch.object(SqliteApprovalBindingRepository, "create", side_effect=AssertionError("approval")) as approval,
        ):
            self._enqueue()
            self.service.list_project("panam")
        for sentinel in (process, shell, network, thread_start, file_write, transition, approval):
            sentinel.assert_not_called()


if __name__ == "__main__":
    unittest.main()
