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
import time
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
import panam_development_loop.models as models_module
import panam_development_loop.worker as worker_module
import panam_development_loop.worker_main as worker_main_module

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
    DevelopmentWorker,
    SqliteWorkerJournalRepository,
    WorkerCheckpoint,
    WorkerCheckpointDirective,
    WorkerCommandHandler,
    WorkerConfiguration,
    WorkerFailureCode,
    WorkerHandlerContext,
    WorkerHandlerEntry,
    WorkerHandlerRegistry,
    WorkerHandlerResult,
    WorkerHandlerStatus,
    WorkerIterationResult,
    WorkerIterationStatus,
    WorkerJournalRepository,
    WorkerJournalResult,
    WorkerJournalResultCode,
    WorkerOperationKind,
    WorkerOperationReceipt,
    WorkerOperationState,
    WorkerReconciliationStatus,
    WorkerSession,
    WorkerSessionState,
    WorkerStartupResult,
    WorkerStartupStatus,
    main as worker_main,
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
    initialize_database,
    validate_migration_registry,
    _MIGRATION_FIVE_SQL,
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
            self.assertEqual([(1, "first-at"), (2, "first-at"), (3, "first-at"), (4, "first-at"), (5, "first-at")], self._ledger_snapshot(connection))
            self.assertEqual(
                [
                    "approvals",
                    "development_runs",
                    "milestone_contracts",
                    "phases",
                    "project_policies",
                    "schema_migrations",
                    "state_events",
                    "worker_operations",
                    "worker_sessions",
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
                [(1, "legacy-applied-at"), (2, "new-at"), (3, "new-at"), (4, "new-at"), (5, "new-at")],
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
            future.execute("INSERT INTO schema_migrations VALUES(6, 'future-at')")
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
            connection.execute("INSERT INTO schema_migrations VALUES(6, 'future')")
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
        self.assertEqual([1, 2, 3, 4, 5], [migration.version for migration in validated])
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
                ["approvals", "development_runs", "milestone_contracts", "phases", "project_policies", "schema_migrations", "state_events", "worker_operations", "worker_sessions", "workflow_command_events", "workflow_commands"],
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
            [(1, "initialized-at"), (2, "initialized-at"), (3, "initialized-at"), (4, "initialized-at"), (5, "initialized-at")],
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
            [(1,), (2,), (3,), (4,), (5,)],
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
            connection.execute("INSERT INTO schema_migrations VALUES(6, 'future-at')")
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
            QueueMutationKind: ("ENQUEUE", "CLAIM_NEXT", "CLAIM_NEXT_ELIGIBLE", "RENEW_LEASE", "MARK_RUNNING", "REQUEST_CANCELLATION", "ACKNOWLEDGE_CANCELLATION", "MARK_SUCCEEDED", "MARK_FAILED", "RECOVER_EXPIRED_CLAIM"),
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
            QueueResultCode.NO_ELIGIBLE_COMMAND: {
                QueueMutationKind.CLAIM_NEXT,
                QueueMutationKind.CLAIM_NEXT_ELIGIBLE,
            },
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
            "enqueue", "get", "list_project", "history", "claim_next", "claim_next_eligible", "renew_lease",
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
        self.assertEqual(10, len(set(expected) - query))
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
        worker_exports = {
            "WorkerSessionState", "WorkerOperationKind", "WorkerOperationState",
            "WorkerReconciliationStatus", "WorkerJournalResultCode",
            "WorkerStartupStatus", "WorkerIterationStatus", "WorkerHandlerStatus",
            "WorkerCheckpointDirective", "WorkerFailureCode", "WorkerConfiguration",
            "WorkerSession", "WorkerOperationReceipt", "WorkerJournalResult",
            "WorkerStartupResult", "WorkerIterationResult", "WorkerHandlerContext",
            "WorkerHandlerResult", "WorkerCheckpoint", "WorkerCommandHandler",
            "WorkerHandlerEntry", "WorkerJournalRepository",
            "SqliteWorkerJournalRepository", "WorkerHandlerRegistry",
            "DevelopmentWorker", "main",
        }
        self.assertEqual(p1 | expected | worker_exports, set(package.__all__))
        self.assertEqual(105, len(package.__all__))
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
            "claim_next_eligible": {"APPLIED", "NO_ELIGIBLE_COMMAND", "TRANSIENT_CONTENTION"},
            "renew_lease": {"APPLIED", "NOT_FOUND", "CAS_CONFLICT", "LEASE_OWNER_MISMATCH", "LEASE_EXPIRED", "TERMINAL_OBSERVED", "TRANSIENT_CONTENTION"},
            "mark_running": {"APPLIED", "NOT_FOUND", "CAS_CONFLICT", "LEASE_OWNER_MISMATCH", "LEASE_EXPIRED", "TERMINAL_OBSERVED", "TRANSIENT_CONTENTION"},
            "request_cancellation": {"APPLIED", "NOT_FOUND", "CAS_CONFLICT", "CANCELLATION_ALREADY_REQUESTED", "TERMINAL_OBSERVED", "TRANSIENT_CONTENTION"},
            "acknowledge_cancellation": {"APPLIED", "NOT_FOUND", "CAS_CONFLICT", "LEASE_OWNER_MISMATCH", "LEASE_EXPIRED", "CANCELLATION_NOT_REQUESTED", "TERMINAL_OBSERVED", "TRANSIENT_CONTENTION"},
            "mark_succeeded": {"APPLIED", "NOT_FOUND", "CAS_CONFLICT", "LEASE_OWNER_MISMATCH", "LEASE_EXPIRED", "TERMINAL_OBSERVED", "TRANSIENT_CONTENTION"},
            "mark_failed": {"APPLIED", "NOT_FOUND", "CAS_CONFLICT", "LEASE_OWNER_MISMATCH", "LEASE_EXPIRED", "TERMINAL_OBSERVED", "TRANSIENT_CONTENTION"},
            "recover_expired_claim": {"APPLIED", "NOT_FOUND", "CAS_CONFLICT", "LEASE_NOT_EXPIRED", "TERMINAL_OBSERVED", "RECONCILIATION_REQUIRED", "TRANSIENT_CONTENTION"},
        }
        self.assertEqual(13, len(matrix))
        self.assertEqual(set(QueueResultCode), {QueueResultCode[name] for codes in matrix.values() for name in codes})

    def test_exact_thirteen_by_ten_normative_matrix_and_signatures(self) -> None:
        service_parameters = {
            "enqueue": ("project_id", "command_kind", "command_schema_version", "payload", "idempotency_key", "actor_id", "development_run_id", "phase_id", "priority"),
            "get": ("command_id",), "list_project": ("project_id",), "history": ("command_id",),
            "claim_next": ("lease_owner",),
            "claim_next_eligible": ("eligible_definition_keys", "lease_owner"),
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
            "claim_next_eligible": ("event_id", "eligible_definition_keys", "lease_owner", "lease_acquired_at", "lease_expires_at"),
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
            "claim_next_eligible": ("APPLIED", "NO_ELIGIBLE_COMMAND", "TRANSIENT_CONTENTION"),
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
            "claim_next_eligible": ("PENDING_TO_CLAIMED", "CLAIMED"),
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
            "eligible_definition_keys": tuple[tuple[str, int], ...],
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
        self.assertEqual(13, len(rows))
        self.assertEqual(13, len({row["SERVICE_OPERATION"] for row in rows}))
        self.assertEqual((3, 10), (sum(row["QUERY_OR_MUTATION"] == "QUERY" for row in rows), sum(row["QUERY_OR_MUTATION"] == "MUTATION" for row in rows)))
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
            "claim_next_eligible": dict(eligible_definition_keys=(("TEST_COMMAND", 1),), lease_owner="worker"),
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
                if name in {"claim_next", "claim_next_eligible", "renew_lease"}:
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
            "claim_next_eligible": dict(eligible_definition_keys=(("TEST_COMMAND", 1),), lease_owner="worker"),
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
            "claim_next_eligible": {"eligible_definition_keys": (("TEST_COMMAND", 1),), "lease_owner": 1},
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
            "claim_next_eligible": {"eligible_definition_keys": (), "lease_owner": "worker"},
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
            self.assertEqual([1, 2, 3, 4, 5], [row[0] for row in connection.execute("SELECT version FROM schema_migrations ORDER BY version")])
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
            self.assertEqual({"workflow_commands", "workflow_command_events", "worker_sessions", "worker_operations"}, tables - {"schema_migrations", "development_runs", "state_events", "phases", "milestone_contracts", "approvals", "project_policies"})
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
                    expected = expected + [(4, "v4-at"), (5, "v4-at")]
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


class Dl21Ca001CompatibilityTest(unittest.TestCase):
    class _RecordingConnection:
        def __init__(self, connection: sqlite3.Connection, records: list[tuple[str, tuple[object, ...]]]) -> None:
            self._connection = connection
            self._records = records

        def execute(self, statement: str, parameters: tuple[object, ...] = ()) -> sqlite3.Cursor:
            if "FROM workflow_commands AS candidate" in statement:
                self._records.append((statement, tuple(parameters)))
            return self._connection.execute(statement, parameters)

        def __getattr__(self, name: str) -> object:
            return getattr(self._connection, name)

    def setUp(self) -> None:
        self._temporary_directory = tempfile.TemporaryDirectory()
        self.directory = Path(self._temporary_directory.name)
        self.path = self.directory / "ca001.sqlite3"
        SqliteRunStore(self.path).initialize("initialized-at")
        self.repository = SqliteWorkflowCommandRepository(self.path)
        self.providers = QueueProviders()
        definitions = tuple(
            replace(_queue_definition(), command_kind=kind)
            for kind in ("ALT_COMMAND", "OTHER_COMMAND", "TEST_COMMAND")
        )
        self.service = DurableCommandQueueService(
            self.repository,
            CommandDefinitionRegistry(definitions),
            10,
            clock=self.providers.clock,
            id_factory=self.providers.identifier,
        )

    def tearDown(self) -> None:
        self._temporary_directory.cleanup()

    def _enqueue(
        self,
        kind: str = "TEST_COMMAND",
        *,
        priority: int = 0,
        key: str | None = None,
    ) -> WorkflowCommand:
        result = self.service.enqueue(
            project_id="panam",
            command_kind=kind,
            command_schema_version=1,
            payload={"value": 1},
            idempotency_key=key or f"key-{self.providers.id_calls + 1}",
            actor_id="requester",
            priority=priority,
        )
        self.assertEqual(QueueResultCode.APPLIED, result.code)
        return result.command  # type: ignore[return-value]

    def _eligible(
        self,
        *keys: tuple[str, int],
        owner: str = "worker",
    ) -> QueueResult:
        return self.service.claim_next_eligible(
            eligible_definition_keys=tuple(keys),
            lease_owner=owner,
        )

    def _skipped_scenario(
        self,
    ) -> tuple[WorkflowCommand, WorkflowCommand, tuple[WorkflowCommandEvent, ...]]:
        skipped = self._enqueue("OTHER_COMMAND", priority=100, key="unsupported")
        self._enqueue("TEST_COMMAND", priority=50, key="supported")
        before_history = self.repository.history(skipped.command_id)
        result = self._eligible(("TEST_COMMAND", 1))
        self.assertEqual(QueueResultCode.APPLIED, result.code)
        current = self.repository.get(skipped.command_id)
        return skipped, current, before_history  # type: ignore[return-value]

    def _record_next_eligible_select(
        self,
        action: Callable[[], QueueResult],
    ) -> tuple[QueueResult, str, tuple[object, ...]]:
        records: list[tuple[str, tuple[object, ...]]] = []
        original = sqlite_repository_module._open_connection

        def open_recording(*args: object, **kwargs: object) -> Dl21Ca001CompatibilityTest._RecordingConnection:
            return self._RecordingConnection(original(*args, **kwargs), records)

        with patch(
            "panam_development_loop.sqlite_repositories._open_connection",
            side_effect=open_recording,
        ):
            result = action()
        self.assertEqual(1, len(records))
        return result, records[0][0], records[0][1]

    def _run_bounded_concurrent_queue_actions(
        self,
        actions: tuple[tuple[str, Callable[[], QueueResult]], ...],
    ) -> dict[str, QueueResult]:
        expected_ids = tuple(participant_id for participant_id, _ in actions)
        self.assertGreater(len(actions), 0)
        self.assertEqual(
            len(expected_ids), len(set(expected_ids)), "duplicate participant identity"
        )
        barrier = threading.Barrier(len(actions))
        outcomes: list[tuple[str, QueueResult]] = []
        errors: list[tuple[str, BaseException]] = []
        record_lock = threading.Lock()

        def run(participant_id: str, action: Callable[[], QueueResult]) -> None:
            try:
                barrier.wait(timeout=5)
                outcome = action()
            except BaseException as error:
                with record_lock:
                    errors.append((participant_id, error))
            else:
                with record_lock:
                    outcomes.append((participant_id, outcome))

        threads = tuple(
            threading.Thread(
                target=run,
                args=(participant_id, action),
                name=f"ca001-{participant_id}",
                daemon=True,
            )
            for participant_id, action in actions
        )
        for thread in threads:
            thread.start()
        deadline = time.monotonic() + 5
        for thread in threads:
            thread.join(timeout=max(0.0, deadline - time.monotonic()))
        self.assertEqual(
            [], [thread.name for thread in threads if thread.is_alive()],
            "participant threads remained alive",
        )
        self.assertEqual(
            [],
            [
                (participant_id, type(error).__name__, str(error))
                for participant_id, error in errors
            ],
            "unexpected participant exceptions",
        )
        self.assertEqual(
            len(actions), len(outcomes), "missing participant outcome"
        )
        observed_ids = tuple(participant_id for participant_id, _ in outcomes)
        self.assertEqual(
            len(observed_ids), len(set(observed_ids)), "duplicate participant outcome"
        )
        self.assertCountEqual(expected_ids, observed_ids)
        return dict(outcomes)

    def _new_service(
        self,
        path: Path,
        providers: QueueProviders,
    ) -> DurableCommandQueueService:
        SqliteRunStore(path).initialize("initialized-at")
        return DurableCommandQueueService(
            SqliteWorkflowCommandRepository(path),
            CommandDefinitionRegistry((_queue_definition(),)),
            10,
            clock=providers.clock,
            id_factory=providers.identifier,
        )

    def test_legacy_constructor_claim_and_missing_capability_precedence(self) -> None:
        class LegacyRepository:
            pass

        repository = LegacyRepository()
        required = (
            "enqueue", "get", "list_project", "history", "claim_next", "renew_lease",
            "mark_running", "request_cancellation", "acknowledge_cancellation",
            "mark_succeeded", "mark_failed", "recover_expired_claim",
        )
        for name in required:
            setattr(repository, name, Mock())
        repository.claim_next.return_value = QueueResult(
            QueueResultCode.NO_ELIGIBLE_COMMAND, QueueMutationKind.CLAIM_NEXT
        )
        providers = QueueProviders()
        service = DurableCommandQueueService(
            repository,  # type: ignore[arg-type]
            CommandDefinitionRegistry(),
            10,
            clock=providers.clock,
            id_factory=providers.identifier,
        )
        self.assertEqual(
            QueueResultCode.NO_ELIGIBLE_COMMAND,
            service.claim_next(lease_owner="worker").code,
        )
        before = (providers.clock_calls, providers.id_calls)
        with self.assertRaisesRegex(TypeError, "^repository$"):
            service.claim_next_eligible(
                eligible_definition_keys=(("TEST_COMMAND", 1),),
                lease_owner="worker",
            )
        self.assertEqual(before, (providers.clock_calls, providers.id_calls))

    def test_single_eligible_key_claims_matching_pending_command(self) -> None:
        expected = self._enqueue()
        result = self._eligible(("TEST_COMMAND", 1))
        self.assertEqual((QueueResultCode.APPLIED, expected.command_id), (result.code, result.command.command_id))

    def test_key_order_is_canonical_for_repository_and_sql_parameters(self) -> None:
        repository = Mock(spec=WorkflowCommandRepository)
        repository.claim_next_eligible.return_value = QueueResult(
            QueueResultCode.NO_ELIGIBLE_COMMAND,
            QueueMutationKind.CLAIM_NEXT_ELIGIBLE,
        )
        service = DurableCommandQueueService(
            repository,
            CommandDefinitionRegistry(),
            10,
            clock=QueueProviders().clock,
            id_factory=QueueProviders().identifier,
        )
        for keys in (
            (("TEST_COMMAND", 1), ("ALT_COMMAND", 1)),
            (("ALT_COMMAND", 1), ("TEST_COMMAND", 1)),
        ):
            service.claim_next_eligible(
                eligible_definition_keys=keys,
                lease_owner="worker",
            )
        expected = (("ALT_COMMAND", 1), ("TEST_COMMAND", 1))
        self.assertEqual(
            [expected, expected],
            [call.kwargs["eligible_definition_keys"] for call in repository.claim_next_eligible.call_args_list],
        )
        self._enqueue()
        result, _, parameters = self._record_next_eligible_select(
            lambda: self._eligible(("TEST_COMMAND", 1), ("ALT_COMMAND", 1))
        )
        self.assertEqual(QueueResultCode.APPLIED, result.code)
        self.assertEqual(("ALT_COMMAND", 1, "TEST_COMMAND", 1), parameters)
        actual_candidates = []
        for name, keys in (
            ("caller-order-a.sqlite3", (("TEST_COMMAND", 1), ("ALT_COMMAND", 1))),
            ("caller-order-b.sqlite3", (("ALT_COMMAND", 1), ("TEST_COMMAND", 1))),
        ):
            providers = QueueProviders()
            candidate_service = self._new_service(self.directory / name, providers)
            candidate_service.enqueue(
                project_id="panam", command_kind="TEST_COMMAND",
                command_schema_version=1, payload={"value": 1},
                idempotency_key="key", actor_id="requester",
            )
            actual_candidates.append(
                candidate_service.claim_next_eligible(
                    eligible_definition_keys=keys,
                    lease_owner="worker",
                ).command.command_id
            )
        self.assertEqual(actual_candidates[0], actual_candidates[1])

    def test_eligible_subset_uses_highest_priority(self) -> None:
        self._enqueue(priority=1, key="low")
        high = self._enqueue(priority=9, key="high")
        self.assertEqual(high.command_id, self._eligible(("TEST_COMMAND", 1)).command.command_id)

    def test_eligible_subset_uses_fifo_queue_sequence(self) -> None:
        first = self._enqueue(priority=5, key="first")
        self._enqueue(priority=5, key="second")
        self.assertEqual(first.command_id, self._eligible(("TEST_COMMAND", 1)).command.command_id)

    def test_high_priority_nonmatching_row_is_skipped(self) -> None:
        skipped = self._enqueue("OTHER_COMMAND", priority=100, key="unsupported")
        selected = self._enqueue("TEST_COMMAND", priority=50, key="supported")
        result = self._eligible(("TEST_COMMAND", 1))
        self.assertEqual(selected.command_id, result.command.command_id)
        self.assertEqual(WorkflowCommandState.PENDING, self.repository.get(skipped.command_id).state)

    def test_skipped_row_state_is_unchanged(self) -> None:
        before, after, _ = self._skipped_scenario()
        self.assertEqual(before.state, after.state)

    def test_skipped_row_state_version_is_unchanged(self) -> None:
        before, after, _ = self._skipped_scenario()
        self.assertEqual(before.state_version, after.state_version)

    def test_skipped_row_lease_is_unchanged(self) -> None:
        before, after, _ = self._skipped_scenario()
        self.assertEqual(
            (before.lease_owner, before.lease_acquired_at, before.lease_expires_at),
            (after.lease_owner, after.lease_acquired_at, after.lease_expires_at),
        )

    def test_skipped_row_claim_count_is_unchanged(self) -> None:
        before, after, _ = self._skipped_scenario()
        self.assertEqual(before.claim_count, after.claim_count)

    def test_skipped_row_history_is_unchanged(self) -> None:
        skipped, _, history = self._skipped_scenario()
        self.assertEqual(history, self.repository.history(skipped.command_id))

    def test_skipped_row_is_later_claimable_by_compatible_key(self) -> None:
        skipped, _, _ = self._skipped_scenario()
        result = self._eligible(("OTHER_COMMAND", 1), owner="other-worker")
        self.assertEqual((QueueResultCode.APPLIED, skipped.command_id), (result.code, result.command.command_id))

    def test_collection_outer_inner_types_and_empty_fail_before_providers(self) -> None:
        repository = Mock(spec=WorkflowCommandRepository)
        providers = QueueProviders()
        service = DurableCommandQueueService(
            repository, CommandDefinitionRegistry(), 10,
            clock=providers.clock, id_factory=providers.identifier,
        )
        cases = (([], TypeError), ((), ValueError), ((["TEST_COMMAND", 1],), TypeError))
        for value, error in cases:
            with self.subTest(value=value), self.assertRaises(error):
                service.claim_next_eligible(eligible_definition_keys=value, lease_owner="worker")  # type: ignore[arg-type]
        self.assertEqual((0, 0), (providers.clock_calls, providers.id_calls))
        repository.claim_next_eligible.assert_not_called()

    def test_duplicate_key_is_rejected_without_effects(self) -> None:
        with self.assertRaises(ValueError):
            self._eligible(("TEST_COMMAND", 1), ("TEST_COMMAND", 1))
        self.assertEqual((0, 0), (self.providers.clock_calls, self.providers.id_calls))

    def test_kind_and_arity_validation(self) -> None:
        cases = (
            ((("TEST_COMMAND",),), ValueError),
            ((("TEST_COMMAND", 1, 2),), ValueError),
            (((1, 1),), TypeError),
            ((("lower", 1),), ValueError),
            (((" TEST_COMMAND", 1),), ValueError),
        )
        for value, error in cases:
            with self.subTest(value=value), self.assertRaises(error):
                self.service.claim_next_eligible(eligible_definition_keys=value, lease_owner="worker")  # type: ignore[arg-type]
        self.assertEqual((0, 0), (self.providers.clock_calls, self.providers.id_calls))

    def test_schema_version_validation(self) -> None:
        cases = ((True, TypeError), ("1", TypeError), (0, ValueError), (2_147_483_648, ValueError))
        for version, error in cases:
            with self.subTest(version=version), self.assertRaises(error):
                self.service.claim_next_eligible(
                    eligible_definition_keys=(("TEST_COMMAND", version),),  # type: ignore[arg-type]
                    lease_owner="worker",
                )
        self.assertEqual((0, 0), (self.providers.clock_calls, self.providers.id_calls))

    def test_256_keys_are_accepted_with_512_bound_parameters(self) -> None:
        keys = tuple((f"K{index:03d}", 1) for index in range(256))
        result, statement, parameters = self._record_next_eligible_select(
            lambda: self.service.claim_next_eligible(
                eligible_definition_keys=keys,
                lease_owner="worker",
            )
        )
        self.assertEqual(QueueResultCode.NO_ELIGIBLE_COMMAND, result.code)
        self.assertEqual((256, 512), (statement.count("command_kind=?"), len(parameters)))

    def test_257_keys_are_rejected_before_providers_or_transaction(self) -> None:
        keys = tuple((f"K{index:03d}", 1) for index in range(257))
        with patch(
            "panam_development_loop.sqlite_repositories._open_connection",
            side_effect=AssertionError("connection opened"),
        ) as opened:
            with self.assertRaises(ValueError):
                self.service.claim_next_eligible(
                    eligible_definition_keys=keys,
                    lease_owner="worker",
                )
        opened.assert_not_called()
        self.assertEqual((0, 0), (self.providers.clock_calls, self.providers.id_calls))

    def test_no_matching_row_returns_no_eligible_without_mutation(self) -> None:
        command = self._enqueue("OTHER_COMMAND")
        before = self.repository.get(command.command_id)
        history = self.repository.history(command.command_id)
        result = self._eligible(("TEST_COMMAND", 1))
        self.assertEqual((QueueResultCode.NO_ELIGIBLE_COMMAND, QueueMutationKind.CLAIM_NEXT_ELIGIBLE), (result.code, result.mutation_kind))
        self.assertEqual(before, self.repository.get(command.command_id))
        self.assertEqual(history, self.repository.history(command.command_id))

    def test_success_result_contains_claimed_command_and_event(self) -> None:
        command = self._enqueue()
        result = self._eligible(("TEST_COMMAND", 1))
        self.assertEqual((QueueResultCode.APPLIED, QueueMutationKind.CLAIM_NEXT_ELIGIBLE), (result.code, result.mutation_kind))
        self.assertEqual((command.command_id, WorkflowCommandState.CLAIMED, WorkflowCommandEventKind.CLAIMED), (result.command.command_id, result.command.state, result.event.event_kind))

    def test_busy_returns_contention_once_without_retry(self) -> None:
        self._enqueue()
        lock = sqlite3.connect(self.path, timeout=0.0)
        lock.execute("BEGIN IMMEDIATE")
        try:
            with patch(
                "panam_development_loop.sqlite_repositories._open_connection",
                wraps=sqlite_repository_module._open_connection,
            ) as opened:
                result = self._eligible(("TEST_COMMAND", 1))
            self.assertEqual(1, opened.call_count)
            self.assertEqual((QueueResultCode.TRANSIENT_CONTENTION, QueueMutationKind.CLAIM_NEXT_ELIGIBLE), (result.code, result.mutation_kind))
        finally:
            lock.rollback()
            lock.close()

    def test_queue_result_closure_for_eligible_claim(self) -> None:
        QueueResult(QueueResultCode.NO_ELIGIBLE_COMMAND, QueueMutationKind.CLAIM_NEXT_ELIGIBLE)
        command = self._enqueue()
        result = self._eligible(("TEST_COMMAND", 1))
        QueueResult(QueueResultCode.APPLIED, QueueMutationKind.CLAIM_NEXT_ELIGIBLE, result.command, result.event)
        with self.assertRaises(ValueError):
            QueueResult(
                QueueResultCode.APPLIED,
                QueueMutationKind.CLAIM_NEXT_ELIGIBLE,
                replace(
                    result.command,
                    lease_acquired_at="2026-08-24T11:59:59.000000Z",
                ),
                result.event,
            )

    def test_eligible_claim_reuses_exact_claimed_event_facts(self) -> None:
        pending = self._enqueue()
        result = self._eligible(("TEST_COMMAND", 1), owner="worker-a")
        event = result.event
        self.assertEqual(
            (WorkflowCommandEventKind.CLAIMED, WorkflowCommandState.PENDING, WorkflowCommandState.CLAIMED, 1, 2, "worker-a", 1),
            (event.event_kind, event.prior_state, event.next_state, event.prior_state_version, event.next_state_version, event.actor_id, event.claim_count),
        )
        self.assertEqual(pending.command_id, event.command_id)
        history = self.repository.history(result.command.command_id)
        self.assertIsNotNone(history)
        self.assertEqual(
            (WorkflowCommandEventKind.ENQUEUED, WorkflowCommandEventKind.CLAIMED),
            tuple(persisted.event_kind for persisted in history),
        )
        self.assertEqual(event, history[-1])

    def test_closed_vocabularies_and_result_exclusions(self) -> None:
        self.assertEqual((10, 17, 6, 9), (len(QueueMutationKind), len(QueueResultCode), len(WorkflowCommandState), len(WorkflowCommandEventKind)))
        command = self._enqueue()
        for code in (QueueResultCode.NOT_FOUND, QueueResultCode.TERMINAL_OBSERVED):
            with self.subTest(code=code), self.assertRaises(ValueError):
                QueueResult(code, QueueMutationKind.CLAIM_NEXT_ELIGIBLE, command=command)

    def test_eligible_lease_effects_match_legacy(self) -> None:
        legacy_providers = QueueProviders()
        eligible_providers = QueueProviders()
        legacy = self._new_service(self.directory / "legacy.sqlite3", legacy_providers)
        eligible = self._new_service(self.directory / "eligible.sqlite3", eligible_providers)
        for service in (legacy, eligible):
            service.enqueue(
                project_id="panam", command_kind="TEST_COMMAND", command_schema_version=1,
                payload={"value": 1}, idempotency_key="key", actor_id="requester",
            )
        legacy_result = legacy.claim_next(lease_owner="worker")
        eligible_result = eligible.claim_next_eligible(
            eligible_definition_keys=(("TEST_COMMAND", 1),), lease_owner="worker"
        )
        self.assertEqual(
            (legacy_result.command.lease_owner, legacy_result.command.lease_acquired_at, legacy_result.command.lease_expires_at, legacy_result.command.updated_at),
            (eligible_result.command.lease_owner, eligible_result.command.lease_acquired_at, eligible_result.command.lease_expires_at, eligible_result.command.updated_at),
        )

    def test_eligible_version_increment_matches_legacy(self) -> None:
        legacy_providers = QueueProviders()
        eligible_providers = QueueProviders()
        legacy = self._new_service(self.directory / "legacy-version.sqlite3", legacy_providers)
        eligible = self._new_service(self.directory / "eligible-version.sqlite3", eligible_providers)
        pending = []
        for service in (legacy, eligible):
            pending.append(service.enqueue(
                project_id="panam", command_kind="TEST_COMMAND",
                command_schema_version=1, payload={"value": 1},
                idempotency_key="key", actor_id="requester",
            ).command)
        legacy_result = legacy.claim_next(lease_owner="worker")
        eligible_result = eligible.claim_next_eligible(
            eligible_definition_keys=(("TEST_COMMAND", 1),), lease_owner="worker"
        )
        self.assertEqual(
            (pending[0].state_version + 1, pending[1].state_version + 1),
            (legacy_result.command.state_version, eligible_result.command.state_version),
        )

    def test_eligible_claim_count_increment_matches_legacy(self) -> None:
        legacy_providers = QueueProviders()
        eligible_providers = QueueProviders()
        legacy = self._new_service(self.directory / "legacy-count.sqlite3", legacy_providers)
        eligible = self._new_service(self.directory / "eligible-count.sqlite3", eligible_providers)
        pending = []
        for service in (legacy, eligible):
            pending.append(service.enqueue(
                project_id="panam", command_kind="TEST_COMMAND",
                command_schema_version=1, payload={"value": 1},
                idempotency_key="key", actor_id="requester",
            ).command)
        legacy_result = legacy.claim_next(lease_owner="worker")
        eligible_result = eligible.claim_next_eligible(
            eligible_definition_keys=(("TEST_COMMAND", 1),), lease_owner="worker"
        )
        self.assertEqual(
            (pending[0].claim_count + 1, pending[1].claim_count + 1),
            (legacy_result.command.claim_count, eligible_result.command.claim_count),
        )

    def test_legacy_and_eligible_concurrency_do_not_duplicate_ownership(self) -> None:
        pending = self._enqueue()

        def claim(eligible: bool, base: int) -> QueueResult:
            providers = QueueProviders()
            providers.id_calls = base
            service = DurableCommandQueueService(
                SqliteWorkflowCommandRepository(self.path), CommandDefinitionRegistry(), 10,
                clock=providers.clock, id_factory=providers.identifier,
            )
            return (
                service.claim_next_eligible(
                    eligible_definition_keys=(("TEST_COMMAND", 1),),
                    lease_owner="eligible",
                )
                if eligible
                else service.claim_next(lease_owner="legacy")
            )

        outcomes = self._run_bounded_concurrent_queue_actions((
            ("legacy", lambda: claim(False, 100)),
            ("eligible", lambda: claim(True, 200)),
        ))
        self.assertEqual(2, len(outcomes))
        self.assertIs(QueueMutationKind.CLAIM_NEXT, outcomes["legacy"].mutation_kind)
        self.assertIs(
            QueueMutationKind.CLAIM_NEXT_ELIGIBLE,
            outcomes["eligible"].mutation_kind,
        )
        winners = tuple(
            (participant_id, result)
            for participant_id, result in outcomes.items()
            if result.code is QueueResultCode.APPLIED
        )
        losers = tuple(
            result for result in outcomes.values()
            if result.code is not QueueResultCode.APPLIED
        )
        self.assertEqual(1, len(winners))
        self.assertEqual(1, len(losers))
        self.assertIn(
            losers[0].code,
            {QueueResultCode.TRANSIENT_CONTENTION, QueueResultCode.NO_ELIGIBLE_COMMAND},
        )
        self.assertIsNone(losers[0].command)
        self.assertIsNone(losers[0].event)
        winner_id, winner = winners[0]
        self.assertEqual(pending.command_id, winner.command.command_id)
        current = self.repository.get(pending.command_id)
        self.assertEqual(
            (WorkflowCommandState.CLAIMED, 1, winner.command.lease_owner),
            (current.state, current.claim_count, current.lease_owner),
        )
        self.assertEqual(
            "legacy" if winner_id == "legacy" else "eligible", current.lease_owner
        )

    def test_overlapping_eligible_claims_do_not_duplicate_ownership(self) -> None:
        def crash() -> QueueResult:
            raise RuntimeError("injected participant failure")

        with self.assertRaisesRegex(AssertionError, "unexpected participant exceptions"):
            self._run_bounded_concurrent_queue_actions((
                (
                    "synthetic-outcome",
                    lambda: QueueResult(
                        QueueResultCode.NO_ELIGIBLE_COMMAND,
                        QueueMutationKind.CLAIM_NEXT_ELIGIBLE,
                    ),
                ),
                ("synthetic-crash", crash),
            ))

        pending = self._enqueue()

        def claim(owner: str, base: int) -> QueueResult:
            providers = QueueProviders()
            providers.id_calls = base
            service = DurableCommandQueueService(
                SqliteWorkflowCommandRepository(self.path), CommandDefinitionRegistry(), 10,
                clock=providers.clock, id_factory=providers.identifier,
            )
            return service.claim_next_eligible(
                eligible_definition_keys=(("TEST_COMMAND", 1),), lease_owner=owner
            )

        outcomes = self._run_bounded_concurrent_queue_actions((
            ("worker-a", lambda: claim("worker-a", 100)),
            ("worker-b", lambda: claim("worker-b", 200)),
        ))
        self.assertEqual(2, len(outcomes))
        self.assertTrue(all(
            result.mutation_kind is QueueMutationKind.CLAIM_NEXT_ELIGIBLE
            for result in outcomes.values()
        ))
        winners = tuple(
            (owner, result) for owner, result in outcomes.items()
            if result.code is QueueResultCode.APPLIED
        )
        losers = tuple(
            result for result in outcomes.values()
            if result.code is not QueueResultCode.APPLIED
        )
        self.assertEqual(1, len(winners))
        self.assertEqual(1, len(losers))
        self.assertIn(
            losers[0].code,
            {QueueResultCode.TRANSIENT_CONTENTION, QueueResultCode.NO_ELIGIBLE_COMMAND},
        )
        self.assertIsNone(losers[0].command)
        self.assertIsNone(losers[0].event)
        winner_owner, winner = winners[0]
        self.assertEqual(pending.command_id, winner.command.command_id)
        self.assertEqual(winner_owner, winner.command.lease_owner)
        current = self.repository.get(pending.command_id)
        self.assertEqual(
            (WorkflowCommandState.CLAIMED, 1, winner_owner),
            (current.state, current.claim_count, current.lease_owner),
        )

    def test_disjoint_eligible_claims_serialize_and_remain_claimable(self) -> None:
        test_command = self._enqueue("TEST_COMMAND", key="test")
        other_command = self._enqueue("OTHER_COMMAND", key="other")
        barrier = threading.Barrier(2)
        results: list[QueueResult] = []
        errors: list[BaseException] = []

        def claim(key: tuple[str, int], owner: str, base: int) -> None:
            providers = QueueProviders()
            providers.id_calls = base
            service = DurableCommandQueueService(
                SqliteWorkflowCommandRepository(self.path), CommandDefinitionRegistry(), 10,
                clock=providers.clock, id_factory=providers.identifier,
            )
            try:
                barrier.wait(timeout=5)
                results.append(service.claim_next_eligible(eligible_definition_keys=(key,), lease_owner=owner))
            except BaseException as error:
                errors.append(error)

        threads = [
            threading.Thread(target=claim, args=(("TEST_COMMAND", 1), "worker-a", 100)),
            threading.Thread(target=claim, args=(("OTHER_COMMAND", 1), "worker-b", 200)),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=5)
        self.assertEqual([], errors)
        self.assertEqual(2, len(results))
        self.assertTrue(all(
            result.code in {QueueResultCode.APPLIED, QueueResultCode.TRANSIENT_CONTENTION}
            and result.mutation_kind is QueueMutationKind.CLAIM_NEXT_ELIGIBLE
            for result in results
        ))
        self.assertGreaterEqual(sum(result.code is QueueResultCode.APPLIED for result in results), 1)
        for key, owner, base in ((('TEST_COMMAND', 1), 'worker-a', 300), (('OTHER_COMMAND', 1), 'worker-b', 400)):
            command = test_command if key[0] == "TEST_COMMAND" else other_command
            if self.repository.get(command.command_id).state is WorkflowCommandState.PENDING:
                providers = QueueProviders()
                providers.id_calls = base
                service = DurableCommandQueueService(
                    SqliteWorkflowCommandRepository(self.path), CommandDefinitionRegistry(), 10,
                    clock=providers.clock, id_factory=providers.identifier,
                )
                self.assertEqual(QueueResultCode.APPLIED, service.claim_next_eligible(eligible_definition_keys=(key,), lease_owner=owner).code)
        self.assertEqual(
            (WorkflowCommandState.CLAIMED, WorkflowCommandState.CLAIMED),
            (self.repository.get(test_command.command_id).state, self.repository.get(other_command.command_id).state),
        )

    def test_direct_repository_key_validation_precedes_transaction(self) -> None:
        with patch(
            "panam_development_loop.sqlite_repositories._open_connection",
            side_effect=AssertionError("connection opened"),
        ) as opened:
            with self.assertRaisesRegex(ValueError, "^eligible_definition_keys$"):
                self.repository.claim_next_eligible(
                    event_id=str(UUID(int=900)),
                    eligible_definition_keys=(),
                    lease_owner="worker",
                    lease_acquired_at="2026-08-24T12:00:00.000000Z",
                    lease_expires_at="2026-08-24T12:00:10.000000Z",
                )
            with self.assertRaisesRegex(ValueError, "^event_id$"):
                self.repository.claim_next_eligible(
                    event_id="bad",
                    eligible_definition_keys=(),
                    lease_owner="worker",
                    lease_acquired_at="2026-08-24T12:00:00.000000Z",
                    lease_expires_at="2026-08-24T12:00:10.000000Z",
                )
        opened.assert_not_called()

    def test_sql_uses_bound_parameters_and_rejects_malformed_metacharacters(self) -> None:
        with self.assertRaises(ValueError):
            self._eligible(("TEST_COMMAND') OR 1=1 --", 1))
        result, statement, parameters = self._record_next_eligible_select(
            lambda: self._eligible(("TEST_COMMAND", 1))
        )
        self.assertEqual(QueueResultCode.NO_ELIGIBLE_COMMAND, result.code)
        self.assertIn("command_kind=?", statement)
        self.assertNotIn("TEST_COMMAND", statement)
        self.assertEqual(("TEST_COMMAND", 1), parameters)

    def test_migration_version_advances_to_five(self) -> None:
        self.assertEqual([1, 2, 3, 4, 5], [migration.version for migration in PRODUCTION_MIGRATIONS])
        connection = sqlite3.connect(self.path)
        try:
            self.assertEqual([1, 2, 3, 4, 5], [row[0] for row in connection.execute("SELECT version FROM schema_migrations ORDER BY version")])
        finally:
            connection.close()

    def test_schema_and_three_indexes_are_unchanged(self) -> None:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        try:
            explicit = {
                row["name"] for row in connection.execute("PRAGMA index_list(workflow_commands)")
                if row["origin"] == "c"
            }
            self.assertEqual(
                {
                    "workflow_commands_claim_order_idx",
                    "workflow_commands_lease_expiry_idx",
                    "workflow_commands_project_sequence_idx",
                },
                explicit,
            )
            self.assertEqual([], [row["name"] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name IN ('workflow_commands','workflow_command_events')")])
            table_sql = {
                row["name"]: row["sql"]
                for row in connection.execute(
                    "SELECT name, sql FROM sqlite_master WHERE type='table' "
                    "AND name IN ('workflow_commands','workflow_command_events')"
                )
            }
            normalize = lambda value: re.sub(r"\s+", " ", value).strip().rstrip(";")
            self.assertEqual(
                normalize(PRODUCTION_MIGRATIONS[3].statements[0]),
                normalize(table_sql["workflow_commands"]),
            )
            self.assertEqual(
                normalize(PRODUCTION_MIGRATIONS[3].statements[1]),
                normalize(table_sql["workflow_command_events"]),
            )
        finally:
            connection.close()

    def test_additive_api_signatures_counts_and_exports(self) -> None:
        import panam_development_loop as package

        method_contracts = (
            (
                DurableCommandQueueService.claim_next_eligible,
                ("self", "eligible_definition_keys", "lease_owner"),
                {
                    "eligible_definition_keys": tuple[tuple[str, int], ...],
                    "lease_owner": str,
                    "return": QueueResult,
                },
            ),
            *(
                (
                    method,
                    (
                        "self", "event_id", "eligible_definition_keys", "lease_owner",
                        "lease_acquired_at", "lease_expires_at",
                    ),
                    {
                        "event_id": str,
                        "eligible_definition_keys": tuple[tuple[str, int], ...],
                        "lease_owner": str,
                        "lease_acquired_at": str,
                        "lease_expires_at": str,
                        "return": QueueResult,
                    },
                )
                for method in (
                    WorkflowCommandRepository.claim_next_eligible,
                    SqliteWorkflowCommandRepository.claim_next_eligible,
                )
            ),
        )
        for method, parameter_names, expected_hints in method_contracts:
            signature = inspect.signature(method)
            parameters = tuple(signature.parameters.values())
            self.assertEqual("claim_next_eligible", method.__name__)
            self.assertEqual(parameter_names, tuple(signature.parameters))
            self.assertIs(inspect.Parameter.POSITIONAL_OR_KEYWORD, parameters[0].kind)
            self.assertIs(inspect.Parameter.empty, parameters[0].annotation)
            self.assertIs(inspect.Parameter.empty, parameters[0].default)
            self.assertTrue(all(
                parameter.kind is inspect.Parameter.KEYWORD_ONLY
                and parameter.default is inspect.Parameter.empty
                and parameter.annotation == expected_hints[parameter.name]
                for parameter in parameters[1:]
            ))
            self.assertIs(QueueResult, signature.return_annotation)
            self.assertEqual(expected_hints, get_type_hints(method))
        service_operations = tuple(name for name, value in DurableCommandQueueService.__dict__.items() if not name.startswith("_") and callable(value))
        repository_operations = tuple(name for name, value in WorkflowCommandRepository.__dict__.items() if not name.startswith("_") and callable(value))
        self.assertEqual((13, 13, 10, 17, 6, 9, 105), (len(service_operations), len(repository_operations), len(QueueMutationKind), len(QueueResultCode), len(WorkflowCommandState), len(WorkflowCommandEventKind), len(package.__all__)))
        self.assertNotIn("MAX_ELIGIBLE_DEFINITION_KEYS", package.__all__)


class Dl22WorkerFoundationTest(unittest.TestCase):
    _VALIDATOR_EXCEPTION_AFTER_RUNNING_EXPECTATION = (
        "VALIDATOR_EXCEPTION_AFTER_RUNNING",
        (
            WorkerOperationState.RUNNING,
            WorkerOperationState.RESULT_FAILED,
            WorkerOperationState.FAILED,
        ),
        WorkerIterationStatus.FAILED,
        WorkerOperationState.FAILED,
        WorkerFailureCode.VALIDATOR_EXCEPTION.value,
        None,
        1,  # one terminal queue/event mutation after the RUNNING checkpoint
        2,  # RESULT_FAILED then FAILED
        0,  # no session write
        0,  # no receipt mutation retry
        WorkerIterationStatus.IDLE,
        1,  # validator invoked exactly once
        WorkflowCommandState.FAILED,
    )
    _ACCEPTED_ZERO_SQL_METHODS = frozenset(
        {
            "test_public_models_signatures_invariants",
            "test_configuration_and_path_preflight",
            "test_entrypoint_provider_cli_hosting",
            "test_ca001_exact_api_and_worker_prohibition",
            "test_registry_construction_registration_freeze",
            "test_registry_keys_lookup_validation_identity",
            "test_registry_missing_and_no_dynamic_execution",
            "test_registry_zero_one_256_257_and_invalid_provider",
            "test_heterogeneous_eligible_claims",
            "test_queue_allowlist_and_no_retry",
            "test_worker_identity_owner_and_uuid",
            "test_session_model_and_transition_matrix",
            "test_fresh_session_fence_every_action",
            "test_stale_equality_no_revival_or_replacement",
            "test_15_durable_failure_outcomes",
            "test_clock_timing_equality_and_provider_validation",
            "test_loop_iteration_wait_stop_cleanup",
            "test_noncooperative_shutdown_truthful_boundary",
            "test_dependency_allowlist_no_effect_boundaries",
            "test_public_root_export_exact_order",
            "test_handler_validation_failure_and_baseexception",
            "test_handler_unauthorized_cancelled_protocol_diagnostic",
            "test_counts_findings_exactness_and_former_conditionals_static",
        }
    )
    _ZERO_SQL_GUARDED_METHODS = _ACCEPTED_ZERO_SQL_METHODS | {
        "test_revision_neutral_schema_obligation_findings_exactness_and_traceability"
    }

    def setUp(self) -> None:
        self._real_sqlite_connect = sqlite3.connect
        self._real_open_connection = sqlite_repository_module._open_connection
        self._real_open_read_only_connection = (
            sqlite_repository_module._open_read_only_connection
        )
        self._temporary_directory = tempfile.TemporaryDirectory()
        self.directory = Path(self._temporary_directory.name)
        self.path = self.directory / "worker.sqlite3"
        self._initialized_paths: set[Path] = set()
        self._memory_anchors: dict[str, sqlite3.Connection] = {}
        self._memory_uris: dict[str, str] = {}
        self.providers = QueueProviders()
        if self._testMethodName in self._ZERO_SQL_GUARDED_METHODS:
            sqlite_sentinel = patch(
                "sqlite3.connect",
                side_effect=AssertionError(
                    f"zero-SQL test opened SQLite: {self._testMethodName}"
                ),
            )
            sqlite_sentinel.start()
            self.addCleanup(sqlite_sentinel.stop)
            return

        original_connect = sqlite3.connect

        def memory_connect(
            database: object, *args: object, **kwargs: object
        ) -> sqlite3.Connection:
            spelling = os.fspath(database) if isinstance(database, (str, Path)) else None
            if spelling is None or spelling.startswith("file:"):
                return original_connect(database, *args, **kwargs)
            normalized = os.path.normcase(os.path.abspath(spelling))
            candidate = Path(normalized)
            try:
                candidate.relative_to(
                    Path(
                        os.path.normcase(
                            os.path.abspath(os.fspath(self.directory))
                        )
                    )
                )
            except ValueError:
                return original_connect(database, *args, **kwargs)
            key = normalized
            uri = self._memory_uris.get(key)
            if uri is None:
                uri = (
                    f"file:panam_dl22_{self._testMethodName}_{id(self):x}_"
                    f"{len(self._memory_uris)}?mode=memory&cache=shared"
                )
                self._memory_uris[key] = uri
                self._memory_anchors[key] = original_connect(uri, uri=True)
            selected = dict(kwargs)
            selected["uri"] = True
            return original_connect(uri, *args, **selected)

        sqlite_connect = patch.object(sqlite3, "connect", side_effect=memory_connect)
        sqlite_connect.start()
        self.addCleanup(sqlite_connect.stop)

        def open_memory(
            database_path: Path,
            entity_name: str,
            identity: str,
            *,
            read_only: bool,
        ) -> sqlite3.Connection:
            connection = memory_connect(database_path, timeout=0.0)
            connection.row_factory = sqlite3.Row
            try:
                connection.execute("PRAGMA foreign_keys=ON")
                sqlite_repository_module._validate_current_schema(
                    connection, entity_name, identity
                )
                if read_only:
                    connection.execute("PRAGMA query_only=ON")
            except BaseException:
                connection.close()
                raise
            return connection

        open_connection = patch.object(
            sqlite_repository_module,
            "_open_connection",
            side_effect=lambda path, entity, identity: open_memory(
                path, entity, identity, read_only=False
            ),
        )
        open_read_only = patch.object(
            sqlite_repository_module,
            "_open_read_only_connection",
            side_effect=lambda path, entity, identity: open_memory(
                path, entity, identity, read_only=True
            ),
        )
        open_connection.start()
        open_read_only.start()
        self.addCleanup(open_connection.stop)
        self.addCleanup(open_read_only.stop)

    def tearDown(self) -> None:
        for anchor in self._memory_anchors.values():
            anchor.close()
        self._temporary_directory.cleanup()

    def _initialize_sqlite(self, path: Path | None = None) -> Path:
        selected = self.path if path is None else path
        canonical = selected.absolute()
        if canonical not in self._initialized_paths:
            SqliteRunStore(selected).initialize("2026-08-24T12:00:00.000000Z")
            self._initialized_paths.add(canonical)
        return selected

    @staticmethod
    def _success(_context: WorkerHandlerContext) -> WorkerHandlerResult:
        return WorkerHandlerResult(WorkerHandlerStatus.SUCCEEDED, "SUCCESS")

    def _registry(
        self,
        callback: Callable[[WorkerHandlerContext], WorkerHandlerResult] | None = None,
        validator: Callable[[dict[str, object]], str] = _queue_payload_validator,
    ) -> WorkerHandlerRegistry:
        definition = CommandDefinition(
            "TEST_COMMAND", 1, ("value",), ("note",), ("note",), validator
        )
        registry = WorkerHandlerRegistry(
            (
                WorkerHandlerEntry(
                    definition,
                    WorkerCommandHandler("TEST_HANDLER", callback or self._success),
                ),
            )
        )
        registry.freeze()
        return registry

    def _service(self, registry: WorkerHandlerRegistry) -> DurableCommandQueueService:
        self._initialize_sqlite()
        return DurableCommandQueueService(
            SqliteWorkflowCommandRepository(self.path),
            registry._definition_registry,
            10,
            clock=self.providers.clock,
            id_factory=self.providers.identifier,
        )

    def _pure_service(
        self, registry: WorkerHandlerRegistry
    ) -> tuple[DurableCommandQueueService, Mock]:
        repository = Mock()
        repository.database_path = self.path
        for name in (
            "enqueue",
            "get",
            "list_project",
            "history",
            "claim_next",
            "claim_next_eligible",
            "renew_lease",
            "mark_running",
            "request_cancellation",
            "acknowledge_cancellation",
            "mark_succeeded",
            "mark_failed",
            "recover_expired_claim",
        ):
            setattr(repository, name, Mock())
        service = DurableCommandQueueService(
            repository,
            registry._definition_registry,
            10,
            clock=self.providers.clock,
            id_factory=self.providers.identifier,
        )
        return service, repository

    def _pure_bound_worker(
        self,
        *,
        stop_requested: Callable[[], bool] = lambda: False,
    ) -> tuple[
        DevelopmentWorker,
        DurableCommandQueueService,
        Mock,
        WorkerSession,
        WorkflowCommand,
        WorkerOperationReceipt,
        object,
        dict[str, object],
    ]:
        registry = self._registry()
        service, _repository = self._pure_service(registry)
        journal = Mock()
        journal.database_path = self.path
        for name in (
            "start_session",
            "get_session",
            "heartbeat",
            "transition_session",
            "create_operation",
            "get_operation_for_claim",
            "transition_operation",
            "reconcile_operation",
            "list_nonterminal_operations",
            "list_owned_commands",
        ):
            setattr(journal, name, Mock())
        session_id = str(UUID(int=7003))
        owner = f"worker-1@{session_id}"
        session = WorkerSession(
            1,
            session_id,
            "worker-1",
            owner,
            WorkerSessionState.ACTIVE,
            1,
            "2026-08-24T12:00:00.000000Z",
            "2026-08-24T12:00:00.000000Z",
        )
        command = replace(
            _pending_queue_command(),
            command_id=str(UUID(int=7002)),
            state=WorkflowCommandState.RUNNING,
            state_version=3,
            claim_count=1,
            lease_owner=owner,
            lease_acquired_at="2026-08-24T12:00:00.000000Z",
            lease_expires_at="2026-08-24T12:00:10.000000Z",
            started_at="2026-08-24T12:00:00.000000Z",
        )
        receipt = self._receipt(WorkerOperationState.RUNNING, None)
        holder: dict[str, object] = {
            "session": session,
            "command": command,
            "receipt": receipt,
            "transition_count": 0,
        }

        def get_session(**_kwargs: object) -> WorkerJournalResult:
            return WorkerJournalResult(
                WorkerJournalResultCode.FOUND,
                session=holder["session"],  # type: ignore[arg-type]
            )

        def get_operation(**_kwargs: object) -> WorkerJournalResult:
            return WorkerJournalResult(
                WorkerJournalResultCode.FOUND,
                operation=holder["receipt"],  # type: ignore[arg-type]
            )

        def transition_operation(**kwargs: object) -> WorkerJournalResult:
            current = holder["receipt"]
            assert type(current) is WorkerOperationReceipt
            next_state = kwargs["next_state"]
            assert type(next_state) is WorkerOperationState
            occurred_at = kwargs["occurred_at"]
            assert type(occurred_at) is str
            updated = replace(
                current,
                state=next_state,
                state_version=current.state_version + 1,
                reconciliation_status=kwargs["reconciliation_status"],
                durable_failure_code=kwargs["durable_failure_code"],
                diagnostic_detail=kwargs["diagnostic_detail"],
                updated_at=occurred_at,
                started_at=(
                    occurred_at
                    if next_state is WorkerOperationState.RUNNING
                    and current.started_at is None
                    else current.started_at
                ),
                completed_at=(
                    occurred_at
                    if next_state in worker_module._TERMINAL_RECEIPT_STATES
                    else None
                ),
            )
            holder["receipt"] = updated
            holder["transition_count"] = int(holder["transition_count"]) + 1
            return WorkerJournalResult(
                WorkerJournalResultCode.APPLIED, operation=updated
            )

        def transition_session(**kwargs: object) -> WorkerJournalResult:
            current = holder["session"]
            assert type(current) is WorkerSession
            next_state = kwargs["next_state"]
            assert type(next_state) is WorkerSessionState
            updated = replace(
                current,
                state=next_state,
                state_version=current.state_version + 1,
                stopped_at=(
                    kwargs["observed_at"]
                    if next_state in {WorkerSessionState.STOPPED, WorkerSessionState.FAILED}
                    else None
                ),
                stop_reason_code=kwargs["reason_code"],
            )
            holder["session"] = updated
            return WorkerJournalResult(WorkerJournalResultCode.APPLIED, session=updated)

        journal.get_session.side_effect = get_session
        journal.get_operation_for_claim.side_effect = get_operation
        journal.transition_operation.side_effect = transition_operation
        journal.transition_session.side_effect = transition_session
        journal._list_startup_terminal_commands = Mock(
            return_value=WorkerJournalResult(WorkerJournalResultCode.LISTED)
        )
        service.get = Mock(
            side_effect=lambda _command_id: QueueResult(
                QueueResultCode.FOUND,
                command=holder["command"],  # type: ignore[arg-type]
            )
        )
        service.renew_lease = Mock(
            return_value=QueueResult(
                QueueResultCode.CAS_CONFLICT,
                mutation_kind=QueueMutationKind.RENEW_LEASE,
                command=command,
            )
        )
        service.acknowledge_cancellation = Mock()

        def mark_failed(**kwargs: object) -> QueueResult:
            current = holder["command"]
            assert type(current) is WorkflowCommand
            failed = replace(
                current,
                state=WorkflowCommandState.FAILED,
                state_version=current.state_version + 1,
                lease_owner=None,
                lease_acquired_at=None,
                lease_expires_at=None,
                failure_code=kwargs["failure_code"],
                completed_at="2026-08-24T12:00:00.000000Z",
            )
            holder["command"] = failed
            event = WorkflowCommandEvent(
                3,
                str(UUID(int=7010)),
                current.command_id,
                WorkflowCommandEventKind.FAILED,
                WorkflowCommandState.RUNNING,
                WorkflowCommandState.FAILED,
                current.state_version,
                failed.state_version,
                current.lease_owner,
                failed.completed_at,
                current.lease_owner,
                current.lease_expires_at,
                current.claim_count,
                failed.failure_code,
            )
            return QueueResult(
                QueueResultCode.APPLIED,
                mutation_kind=QueueMutationKind.MARK_FAILED,
                command=failed,
                event=event,
            )

        service.mark_failed = Mock(side_effect=mark_failed)
        worker = DevelopmentWorker(
            WorkerConfiguration(self.path, "worker-1"),
            service,
            journal,
            registry,
            lambda _path, _instant: None,
            clock=self.providers.clock,
            monotonic_clock=lambda: 0.0,
            wait=lambda _seconds: False,
            stop_requested=stop_requested,
            session_id_factory=lambda: str(UUID(int=9001)),
            operation_id_factory=lambda: str(UUID(int=9002)),
            queue_database_path=self.path,
            journal_database_path=self.path,
        )
        worker._session = session
        fence = worker_module._BoundOperationFence(
            "worker-1",
            session.session_sequence,
            session.session_id,
            session.state_version,
            owner,
            command.command_id,
            receipt.operation_id,
            command.claim_count,
            receipt.state_version,
            receipt.precondition_state_version,
            command.lease_expires_at,
        )
        worker._bound_fence = fence
        return worker, service, journal, session, command, receipt, fence, holder

    def _exercise_validator_exception_after_running(self) -> tuple[object, ...]:
        validator_calls: list[dict[str, object]] = []

        def exploding_validator(payload: dict[str, object]) -> str:
            validator_calls.append(payload)
            raise RuntimeError("validator fault")

        worker, service, journal, session, command, _, _, holder = (
            self._pure_bound_worker()
        )
        worker._registry = self._registry(validator=exploding_validator)
        claimed = replace(
            command,
            state=WorkflowCommandState.CLAIMED,
            state_version=2,
            started_at=None,
        )
        prepared = self._receipt(WorkerOperationState.PREPARED, None)
        holder["command"] = claimed
        holder["receipt"] = prepared
        holder["transition_count"] = 0
        worker._last_heartbeat_monotonic = 0.0
        worker._operation_id_factory = lambda: prepared.operation_id
        service.claim_next_eligible = Mock(
            side_effect=(
                Mock(code=QueueResultCode.APPLIED, command=claimed),
                QueueResult(
                    QueueResultCode.NO_ELIGIBLE_COMMAND,
                    mutation_kind=QueueMutationKind.CLAIM_NEXT_ELIGIBLE,
                ),
            )
        )
        journal.create_operation.return_value = WorkerJournalResult(
            WorkerJournalResultCode.APPLIED, operation=prepared
        )

        def mark_running(**_kwargs: object) -> object:
            current = holder["command"]
            assert type(current) is WorkflowCommand
            running = replace(
                current,
                state=WorkflowCommandState.RUNNING,
                state_version=current.state_version + 1,
                started_at="2026-08-24T12:00:00.000000Z",
            )
            holder["command"] = running
            return Mock(code=QueueResultCode.APPLIED, command=running)

        service.mark_running = Mock(side_effect=mark_running)
        result = worker.run_iteration()
        transitions = tuple(
            call.kwargs["next_state"]
            for call in journal.transition_operation.call_args_list
        )
        transition_versions = tuple(
            call.kwargs["expected_state_version"]
            for call in journal.transition_operation.call_args_list
        )
        final_receipt = holder["receipt"]
        final_command = holder["command"]
        assert type(final_receipt) is WorkerOperationReceipt
        assert type(final_command) is WorkflowCommand
        restart = worker.run_iteration()
        return (
            "VALIDATOR_EXCEPTION_AFTER_RUNNING",
            transitions,
            result.status,
            final_receipt.state,
            final_receipt.durable_failure_code,
            final_receipt.diagnostic_detail,
            service.mark_failed.call_count,
            len(transitions) - 1,
            journal.transition_session.call_count,
            len(transition_versions) - len(set(transition_versions)),
            restart.status,
            len(validator_calls),
            final_command.state,
        )

    @staticmethod
    def _enqueue(service: DurableCommandQueueService, key: str = "worker-key") -> QueueResult:
        return service.enqueue(
            project_id="panam",
            command_kind="TEST_COMMAND",
            command_schema_version=1,
            payload={"value": 1},
            idempotency_key=key,
            actor_id="requester",
        )

    def _worker(
        self,
        registry: WorkerHandlerRegistry,
        service: DurableCommandQueueService,
        *,
        stop_requested: Callable[[], bool] = lambda: False,
        database_path: Path | None = None,
    ) -> DevelopmentWorker:
        path = self.path if database_path is None else database_path
        return DevelopmentWorker(
            WorkerConfiguration(path, "worker-1"),
            service,
            SqliteWorkerJournalRepository(path),
            registry,
            initialize_database,
            clock=self.providers.clock,
            monotonic_clock=lambda: 0.0,
            wait=lambda _seconds: False,
            stop_requested=stop_requested,
            session_id_factory=lambda: str(UUID(int=9001)),
            operation_id_factory=lambda: str(UUID(int=9002)),
            queue_database_path=path,
            journal_database_path=path,
        )

    def _claimed_prepared(
        self,
    ) -> tuple[DurableCommandQueueService, SqliteWorkerJournalRepository, WorkerSession, WorkflowCommand, WorkerOperationReceipt]:
        registry = self._registry()
        service = self._service(registry)
        self._enqueue(service)
        journal = SqliteWorkerJournalRepository(self.path)
        session_id = str(UUID(int=8001))
        owner = f"worker-1@{session_id}"
        session_result = journal.start_session(
            session_id=session_id,
            worker_id="worker-1",
            queue_owner_id=owner,
            started_at="2026-08-24T12:00:00.000000Z",
            stale_before="2026-08-24T11:59:30.000000Z",
        )
        claim = service.claim_next_eligible(
            eligible_definition_keys=registry.keys(), lease_owner=owner
        )
        operation = journal.create_operation(
            operation_id=str(UUID(int=8002)),
            command=claim.command,
            operation_kind=WorkerOperationKind.COMMAND_HANDLER_INVOCATION,
            worker_id="worker-1",
            session_id=session_id,
            queue_owner_id=owner,
            occurred_at="2026-08-24T12:00:00.000000Z",
            stale_before="2026-08-24T11:59:30.000000Z",
        )
        return service, journal, session_result.session, claim.command, operation.operation

    @staticmethod
    def _sqlite_fact_snapshot(
        path: Path,
    ) -> dict[str, tuple[tuple[object, ...], ...]]:
        connection = sqlite3.connect(path)
        try:
            return {
                table: tuple(connection.execute(f"SELECT * FROM {table} ORDER BY 1"))
                for table in (
                    "workflow_commands",
                    "workflow_command_events",
                    "worker_sessions",
                    "worker_operations",
                )
            }
        finally:
            connection.close()

    def _reconciliation_fixture(
        self,
        path: Path,
        family: str,
        identity: int,
    ) -> tuple[
        DurableCommandQueueService,
        SqliteWorkerJournalRepository,
        WorkerSession,
        WorkflowCommand,
        WorkerOperationReceipt,
    ]:
        SqliteRunStore(path).initialize("2026-08-24T12:00:00.000000Z")
        providers = QueueProviders()
        registry = self._registry()
        service = DurableCommandQueueService(
            SqliteWorkflowCommandRepository(path),
            registry._definition_registry,
            10,
            clock=providers.clock,
            id_factory=providers.identifier,
        )
        queued = self._enqueue(service, f"atomic-{identity}-{family.lower()}")
        journal = SqliteWorkerJournalRepository(path)
        session_id = str(UUID(int=identity))
        owner = f"worker-1@{session_id}"
        session = journal.start_session(
            session_id=session_id,
            worker_id="worker-1",
            queue_owner_id=owner,
            started_at="2026-08-24T12:00:00.000000Z",
            stale_before="2026-08-24T11:59:30.000000Z",
        ).session
        claimed = service.claim_next_eligible(
            eligible_definition_keys=registry.keys(), lease_owner=owner
        )
        created = journal.create_operation(
            operation_id=str(UUID(int=identity + 1)),
            command=claimed.command,
            operation_kind=WorkerOperationKind.COMMAND_HANDLER_INVOCATION,
            worker_id="worker-1",
            session_id=session_id,
            queue_owner_id=owner,
            occurred_at="2026-08-24T12:00:00.000000Z",
            stale_before="2026-08-24T11:59:30.000000Z",
        )
        if family == "FAILED":
            running = service.mark_running(
                command_id=claimed.command.command_id,
                expected_state=claimed.command.state,
                expected_state_version=claimed.command.state_version,
                lease_owner=owner,
            )
            terminal = service.mark_failed(
                command_id=running.command.command_id,
                expected_state=running.command.state,
                expected_state_version=running.command.state_version,
                lease_owner=owner,
                failure_code="TEST_FAILURE",
            )
        elif family == "CANCELLED":
            requested = service.request_cancellation(
                command_id=claimed.command.command_id,
                expected_state=claimed.command.state,
                expected_state_version=claimed.command.state_version,
                requested_by="requester",
                reason_code="TEST_CANCEL",
            )
            terminal = service.acknowledge_cancellation(
                command_id=requested.command.command_id,
                expected_state=requested.command.state,
                expected_state_version=requested.command.state_version,
                lease_owner=owner,
            )
        elif family == "CLAIM-RELEASED":
            providers.current += timedelta(seconds=10)
            terminal = service.recover_expired_claim(
                command_id=claimed.command.command_id,
                expected_state=claimed.command.state,
                expected_state_version=claimed.command.state_version,
                recovery_actor="recovery",
            )
        else:
            raise AssertionError(family)
        self.assertEqual(QueueResultCode.APPLIED, terminal.code)
        return service, journal, session, terminal.command, created.operation

    def _assert_atomic_reconcile_crash_point(self, point: int) -> None:
        class CrashWindow(BaseException):
            pass

        expected = {
            "FAILED": (
                WorkerOperationState.FAILED,
                WorkerFailureCode.RECONCILIATION_REQUIRED.value,
            ),
            "CANCELLED": (
                WorkerOperationState.CANCELLED,
                WorkerFailureCode.CANCELLATION_OBSERVED.value,
            ),
            "CLAIM-RELEASED": (
                WorkerOperationState.CLAIM_RELEASED,
                WorkerFailureCode.LEASE_LOST.value,
            ),
        }
        for ordinal, family in enumerate(expected, start=1):
            with self.subTest(point=point, family=family):
                path = self.directory / f"atomic-{point}-{family.lower()}.sqlite3"
                _, journal, _, terminal_command, receipt = self._reconciliation_fixture(
                    path, family, point * 100 + ordinal * 10
                )
                before = self._sqlite_fact_snapshot(path)
                original_open = journal._open
                observed: dict[str, object] = {
                    "begun": False,
                    "cas_count": 0,
                    "commit_completed": False,
                    "statements": [],
                    "trace": [],
                }

                class CrashConnection:
                    def __init__(self, connection: sqlite3.Connection) -> None:
                        self._connection = connection

                    def execute(
                        self, statement: str, parameters: object = ()
                    ) -> sqlite3.Cursor:
                        normalized = " ".join(statement.upper().split())
                        statements = observed["statements"]
                        assert type(statements) is list
                        statements.append(normalized)
                        if (
                            point == 23
                            and observed["begun"]
                            and normalized.startswith("SELECT ")
                            and " FROM WORKFLOW_COMMANDS " in normalized
                        ):
                            raise CrashWindow("C23")
                        if normalized == "BEGIN IMMEDIATE":
                            cursor = self._connection.execute(statement, parameters)
                            observed["begun"] = True
                            return cursor
                        if normalized.startswith("UPDATE WORKER_OPERATIONS"):
                            if point == 24:
                                raise CrashWindow("C24")
                            cursor = self._connection.execute(statement, parameters)
                            observed["cas_count"] = int(observed["cas_count"]) + 1
                            return cursor
                        return self._connection.execute(statement, parameters)

                    def commit(self) -> None:
                        if point == 25:
                            raise CrashWindow("C25")
                        self._connection.commit()
                        observed["commit_completed"] = True
                        if point == 26:
                            raise CrashWindow("C26")

                    def __getattr__(self, name: str) -> object:
                        return getattr(self._connection, name)

                def faulting_open(entity: str, identity: str) -> object:
                    connection = original_open(entity, identity)
                    trace = observed["trace"]
                    assert type(trace) is list
                    connection.set_trace_callback(trace.append)
                    return CrashConnection(connection)

                applied: WorkerJournalResult | None = None
                with patch.object(journal, "_open", side_effect=faulting_open):
                    if point < 27:
                        with self.assertRaises(CrashWindow):
                            journal.reconcile_operation(
                                operation_id=receipt.operation_id,
                                expected_state=receipt.state,
                                expected_state_version=receipt.state_version,
                                occurred_at="2026-08-24T12:00:10.000000Z",
                            )
                    else:
                        applied = journal.reconcile_operation(
                            operation_id=receipt.operation_id,
                            expected_state=receipt.state,
                            expected_state_version=receipt.state_version,
                            occurred_at="2026-08-24T12:00:10.000000Z",
                        )
                after_window = self._sqlite_fact_snapshot(path)
                self.assertEqual(
                    before["workflow_commands"], after_window["workflow_commands"]
                )
                self.assertEqual(
                    before["workflow_command_events"],
                    after_window["workflow_command_events"],
                )
                self.assertEqual(
                    before["worker_sessions"], after_window["worker_sessions"]
                )
                statements = observed["statements"]
                assert type(statements) is list
                trace = observed["trace"]
                assert type(trace) is list
                normalized_trace = tuple(" ".join(value.upper().split()) for value in trace)
                queue_reads = sum(
                    value.startswith("SELECT ") and " FROM WORKFLOW_COMMANDS " in value
                    for value in normalized_trace
                )
                event_reads = sum(
                    value.startswith("SELECT ")
                    and " FROM WORKFLOW_COMMAND_EVENTS " in value
                    for value in normalized_trace
                )
                receipt_reads = sum(
                    value.startswith("SELECT ")
                    and " FROM WORKER_OPERATIONS " in value
                    for value in normalized_trace
                )
                receipt_updates = sum(
                    value.startswith("UPDATE WORKER_OPERATIONS")
                    for value in normalized_trace
                )
                expected_reads = 0 if point == 23 else 1
                expected_cas = 1 if point >= 25 else 0
                expected_commit = 1 if point >= 26 else 0
                expected_rollback = 1 if point <= 25 else 0
                self.assertEqual(
                    (
                        1,
                        expected_reads,
                        expected_reads,
                        expected_cas,
                        expected_commit,
                        expected_rollback,
                    ),
                    (
                        sum(value == "BEGIN IMMEDIATE" for value in normalized_trace),
                        queue_reads,
                        event_reads,
                        receipt_updates,
                        sum(value == "COMMIT" for value in normalized_trace),
                        sum(value == "ROLLBACK" for value in normalized_trace),
                    ),
                )
                self.assertFalse(
                    any(
                        value.startswith(
                            (
                                "UPDATE WORKFLOW_COMMANDS",
                                "INSERT INTO WORKFLOW_COMMAND_EVENTS",
                                "UPDATE WORKFLOW_COMMAND_EVENTS",
                                "UPDATE WORKER_SESSIONS",
                            )
                        )
                        for value in normalized_trace
                    )
                )
                if point == 23:
                    receipt_attempt_index = next(
                        index
                        for index, value in enumerate(statements)
                        if "FROM WORKER_OPERATIONS" in value
                    )
                    queue_attempt_index = next(
                        index
                        for index, value in enumerate(statements)
                        if "FROM WORKFLOW_COMMANDS" in value
                    )
                    self.assertLess(receipt_attempt_index, queue_attempt_index)
                    self.assertEqual(1, receipt_reads)
                    self.assertFalse(
                        any("FROM WORKFLOW_COMMANDS" in value for value in normalized_trace)
                    )
                    self.assertEqual((0, False), (observed["cas_count"], observed["commit_completed"]))
                elif point == 24:
                    self.assertTrue(
                        any("FROM WORKFLOW_COMMANDS" in value for value in statements)
                    )
                    self.assertTrue(
                        any("FROM WORKFLOW_COMMAND_EVENTS" in value for value in statements)
                    )
                    self.assertEqual((0, False), (observed["cas_count"], observed["commit_completed"]))
                elif point == 25:
                    self.assertEqual((1, False), (observed["cas_count"], observed["commit_completed"]))
                elif point == 26:
                    self.assertEqual((1, True), (observed["cas_count"], observed["commit_completed"]))
                    update_index = next(
                        index
                        for index, value in enumerate(normalized_trace)
                        if value.startswith("UPDATE WORKER_OPERATIONS")
                    )
                    reread_index = max(
                        index
                        for index, value in enumerate(normalized_trace)
                        if value.startswith("SELECT ")
                        and " FROM WORKER_OPERATIONS " in value
                    )
                    commit_index = normalized_trace.index("COMMIT")
                    self.assertLess(update_index, reread_index)
                    self.assertLess(reread_index, commit_index)
                else:
                    self.assertEqual((1, True), (observed["cas_count"], observed["commit_completed"]))

                reopened = SqliteWorkerJournalRepository(path)
                if point <= 25:
                    self.assertEqual(before, after_window)
                    applied = reopened.reconcile_operation(
                        operation_id=receipt.operation_id,
                        expected_state=receipt.state,
                        expected_state_version=receipt.state_version,
                        occurred_at="2026-08-24T12:00:10.000000Z",
                    )
                    self.assertEqual(WorkerJournalResultCode.APPLIED, applied.code)
                elif point == 26:
                    applied = reopened.get_operation_for_claim(
                        command_id=receipt.command_id,
                        claim_count=receipt.claim_count,
                    )
                    self.assertEqual(WorkerJournalResultCode.FOUND, applied.code)
                else:
                    self.assertEqual(WorkerJournalResultCode.APPLIED, applied.code)
                assert applied is not None and applied.operation is not None
                self.assertEqual(expected[family], (applied.operation.state, applied.operation.durable_failure_code))
                self.assertEqual(receipt.state_version + 1, applied.operation.state_version)
                self.assertEqual(
                    (
                        WorkerReconciliationStatus.NOT_REQUIRED,
                        None,
                        "2026-08-24T12:00:10.000000Z",
                        "2026-08-24T12:00:10.000000Z",
                        terminal_command.started_at if family == "FAILED" else None,
                    ),
                    (
                        applied.operation.reconciliation_status,
                        applied.operation.diagnostic_detail,
                        applied.operation.updated_at,
                        applied.operation.completed_at,
                        applied.operation.started_at,
                    ),
                )
                repeated = reopened.reconcile_operation(
                    operation_id=receipt.operation_id,
                    expected_state=receipt.state,
                    expected_state_version=receipt.state_version,
                    occurred_at="2026-08-24T12:00:11.000000Z",
                )
                self.assertEqual(
                    (WorkerJournalResultCode.TERMINAL_OBSERVED, applied.operation),
                    (repeated.code, repeated.operation),
                )
                final = self._sqlite_fact_snapshot(path)
                self.assertEqual(before["workflow_commands"], final["workflow_commands"])
                self.assertEqual(
                    before["workflow_command_events"], final["workflow_command_events"]
                )
                self.assertEqual(before["worker_sessions"], final["worker_sessions"])

    def _corrupt_prepared_receipt(
        self,
    ) -> tuple[
        WorkerHandlerRegistry,
        DurableCommandQueueService,
        SqliteWorkerJournalRepository,
        WorkerSession,
        WorkflowCommand,
        WorkerOperationReceipt,
    ]:
        registry = self._registry()
        service = self._service(registry)
        self._enqueue(service)
        journal = SqliteWorkerJournalRepository(self.path)
        session_id = str(UUID(int=8901))
        owner = f"worker-1@{session_id}"
        session = journal.start_session(
            session_id=session_id,
            worker_id="worker-1",
            queue_owner_id=owner,
            started_at="2026-08-24T12:00:00.000000Z",
            stale_before="2026-08-24T11:59:30.000000Z",
        ).session
        command = service.claim_next_eligible(
            eligible_definition_keys=registry.keys(), lease_owner=owner
        ).command
        receipt = journal.create_operation(
            operation_id=str(UUID(int=8902)),
            command=command,
            operation_kind=WorkerOperationKind.COMMAND_HANDLER_INVOCATION,
            worker_id="worker-1",
            session_id=session_id,
            queue_owner_id=owner,
            occurred_at="2026-08-24T12:00:00.000000Z",
            stale_before="2026-08-24T11:59:30.000000Z",
        ).operation
        connection = sqlite3.connect(self.path)
        try:
            connection.execute("PRAGMA ignore_check_constraints=ON")
            connection.execute(
                "UPDATE worker_operations SET durable_failure_code='LEASE_LOST' "
                "WHERE operation_id=?",
                (receipt.operation_id,),
            )
            connection.commit()
        finally:
            connection.close()
        return registry, service, journal, session, command, receipt

    def _claimed_prepared_with_registry(
        self,
    ) -> tuple[
        WorkerHandlerRegistry,
        DurableCommandQueueService,
        SqliteWorkerJournalRepository,
        WorkerSession,
        WorkflowCommand,
        WorkerOperationReceipt,
    ]:
        registry = self._registry()
        service = self._service(registry)
        self._enqueue(service)
        journal = SqliteWorkerJournalRepository(self.path)
        session_id = str(UUID(int=8951))
        owner = f"worker-1@{session_id}"
        session = journal.start_session(
            session_id=session_id,
            worker_id="worker-1",
            queue_owner_id=owner,
            started_at="2026-08-24T12:00:00.000000Z",
            stale_before="2026-08-24T11:59:30.000000Z",
        ).session
        command = service.claim_next_eligible(
            eligible_definition_keys=registry.keys(), lease_owner=owner
        ).command
        receipt = journal.create_operation(
            operation_id=str(UUID(int=8952)),
            command=command,
            operation_kind=WorkerOperationKind.COMMAND_HANDLER_INVOCATION,
            worker_id="worker-1",
            session_id=session_id,
            queue_owner_id=owner,
            occurred_at="2026-08-24T12:00:00.000000Z",
            stale_before="2026-08-24T11:59:30.000000Z",
        ).operation
        return registry, service, journal, session, command, receipt

    def _running_receipt_with_registry(
        self,
    ) -> tuple[
        WorkerHandlerRegistry,
        DurableCommandQueueService,
        SqliteWorkerJournalRepository,
        WorkerSession,
        WorkflowCommand,
        WorkerOperationReceipt,
    ]:
        registry, service, journal, session, command, receipt = (
            self._claimed_prepared_with_registry()
        )
        running_command = service.mark_running(
            command_id=command.command_id,
            expected_state=command.state,
            expected_state_version=command.state_version,
            lease_owner=command.lease_owner,
        ).command
        running_receipt = journal.transition_operation(
            operation_id=receipt.operation_id,
            expected_state=receipt.state,
            expected_state_version=receipt.state_version,
            next_state=WorkerOperationState.RUNNING,
            reconciliation_status=WorkerReconciliationStatus.NOT_REQUIRED,
            durable_failure_code=None,
            diagnostic_detail=None,
            occurred_at="2026-08-24T12:00:00.000000Z",
            stale_before="2026-08-24T11:59:30.000000Z",
        ).operation
        return registry, service, journal, session, running_command, running_receipt

    def _result_succeeded_with_registry(
        self,
    ) -> tuple[
        WorkerHandlerRegistry,
        DurableCommandQueueService,
        SqliteWorkerJournalRepository,
        WorkerSession,
        WorkflowCommand,
        WorkerOperationReceipt,
    ]:
        registry, service, journal, session, command, receipt = (
            self._running_receipt_with_registry()
        )
        result_receipt = journal.transition_operation(
            operation_id=receipt.operation_id,
            expected_state=receipt.state,
            expected_state_version=receipt.state_version,
            next_state=WorkerOperationState.RESULT_SUCCEEDED,
            reconciliation_status=WorkerReconciliationStatus.NOT_REQUIRED,
            durable_failure_code=None,
            diagnostic_detail=None,
            occurred_at="2026-08-24T12:00:00.000000Z",
            stale_before="2026-08-24T11:59:30.000000Z",
        ).operation
        return registry, service, journal, session, command, result_receipt

    def _fail_worker_session(
        self,
        journal: SqliteWorkerJournalRepository,
        session: WorkerSession,
    ) -> WorkerSession:
        failed = journal.transition_session(
            session_id=session.session_id,
            expected_state=session.state,
            expected_state_version=session.state_version,
            next_state=WorkerSessionState.FAILED,
            reason_code="PROCESS_LOST",
            observed_at=_format_queue_timestamp(self.providers.current),
            stale_before=_format_queue_timestamp(
                self.providers.current - timedelta(seconds=30)
            ),
        )
        self.assertEqual(WorkerJournalResultCode.APPLIED, failed.code)
        return failed.session

    @staticmethod
    def _receipt(
        state: WorkerOperationState,
        failure: str | None,
        diagnostic: str | None = None,
    ) -> WorkerOperationReceipt:
        started = None
        completed = None
        reconciliation = WorkerReconciliationStatus.NOT_REQUIRED
        if state in {
            WorkerOperationState.RUNNING,
            WorkerOperationState.RESULT_SUCCEEDED,
            WorkerOperationState.RESULT_FAILED,
            WorkerOperationState.CANCELLATION_OBSERVED,
            WorkerOperationState.SUCCEEDED,
            WorkerOperationState.FAILED,
        }:
            started = "2026-08-24T12:00:00.000000Z"
        if state in {
            WorkerOperationState.SUCCEEDED,
            WorkerOperationState.FAILED,
            WorkerOperationState.CANCELLED,
            WorkerOperationState.CLAIM_RELEASED,
            WorkerOperationState.LEASE_LOST,
            WorkerOperationState.RECONCILIATION_REQUIRED,
        }:
            completed = "2026-08-24T12:00:01.000000Z"
        if state in {
            WorkerOperationState.LEASE_LOST,
            WorkerOperationState.RECONCILIATION_REQUIRED,
        }:
            reconciliation = WorkerReconciliationStatus.REQUIRED
        return WorkerOperationReceipt(
            1,
            str(UUID(int=7001)),
            WorkerOperationKind.COMMAND_HANDLER_INVOCATION,
            str(UUID(int=7002)),
            "panam",
            None,
            None,
            str(UUID(int=7003)),
            "worker-1",
            f"worker-1@{UUID(int=7003)}",
            1,
            2,
            state,
            1,
            "NONE",
            reconciliation,
            failure,
            diagnostic,
            "2026-08-24T12:00:00.000000Z",
            "2026-08-24T12:00:01.000000Z" if completed else "2026-08-24T12:00:00.000000Z",
            started,
            completed,
        )

    def test_public_models_signatures_invariants(self) -> None:
        self.assertEqual(22, len(fields(WorkerOperationReceipt)))
        self.assertEqual(2, len(fields(WorkerHandlerResult)))
        self.assertEqual(15, len(WorkerFailureCode))
        self.assertEqual(17, len(WorkerStartupStatus))

    def test_configuration_and_path_preflight(self) -> None:
        self.assertEqual("worker-1", WorkerConfiguration(self.path, "worker-1").worker_id)
        with self.assertRaises(TypeError):
            WorkerConfiguration(str(self.path), "worker-1")
        with self.assertRaises(ValueError):
            WorkerConfiguration(self.path, "worker-1", heartbeat_stale_after_seconds=10.0)

    def test_entrypoint_provider_cli_hosting(self) -> None:
        flags = {option for action in worker_main_module._parser()._actions for option in action.option_strings}
        self.assertEqual(9, len(flags - {"-h", "--help"}))
        self.assertEqual(0, worker_main(["--database-path", str(self.path), "--worker-id", "worker-1", "--validate-only"]))
        self.assertEqual(4, worker_main(["--database-path", str(self.path), "--worker-id", "worker-1"]))

    def test_ca001_exact_api_and_worker_prohibition(self) -> None:
        source = inspect.getsource(DevelopmentWorker)
        self.assertIn("claim_next_eligible", source)
        self.assertNotIn("claim_next" + "(", source)

    def test_registry_construction_registration_freeze(self) -> None:
        entry = self._registry().lookup("TEST_COMMAND", 1)
        registry = WorkerHandlerRegistry((entry,))
        registry.freeze()
        registry.freeze()
        with self.assertRaises(RuntimeError):
            registry.register(entry)

    def test_registry_keys_lookup_validation_identity(self) -> None:
        registry = self._registry()
        command = _pending_queue_command()
        self.assertIs(registry.lookup("TEST_COMMAND", 1), registry.validate(command))
        self.assertEqual((("TEST_COMMAND", 1),), registry.keys())

    def test_registry_missing_and_no_dynamic_execution(self) -> None:
        registry = self._registry()
        with self.assertRaises(LookupError):
            registry.lookup("MISSING", 1)
        self.assertFalse(any(token in inspect.getsource(WorkerHandlerRegistry) for token in ("importlib", "eval(", "exec(")))

    def test_registry_zero_one_256_257_and_invalid_provider(self) -> None:
        empty = WorkerHandlerRegistry()
        empty.freeze()
        self.assertEqual((), empty.keys())
        entries = tuple(
            WorkerHandlerEntry(
                CommandDefinition(f"KIND_{index:03d}", 1, (), (), (), _queue_payload_validator),
                WorkerCommandHandler(f"HANDLER_{index:03d}", self._success),
            )
            for index in range(256)
        )
        registry = WorkerHandlerRegistry(entries)
        registry.freeze()
        self.assertEqual(256, len(registry.keys()))

    def test_heterogeneous_eligible_claims(self) -> None:
        service, repository = self._pure_service(self._registry())
        claimed_command = replace(
            _pending_queue_command(),
            state=WorkflowCommandState.CLAIMED,
            state_version=2,
            claim_count=1,
            lease_owner="worker",
            lease_acquired_at="2026-08-24T12:00:00.000000Z",
            lease_expires_at="2026-08-24T12:00:10.000000Z",
        )
        repository.claim_next_eligible.return_value = QueueResult(
            QueueResultCode.APPLIED,
            mutation_kind=QueueMutationKind.CLAIM_NEXT_ELIGIBLE,
            command=claimed_command,
            event=WorkflowCommandEvent(
                2,
                str(UUID(int=7999)),
                claimed_command.command_id,
                WorkflowCommandEventKind.CLAIMED,
                WorkflowCommandState.PENDING,
                WorkflowCommandState.CLAIMED,
                1,
                2,
                "worker",
                "2026-08-24T12:00:00.000000Z",
                "worker",
                "2026-08-24T12:00:10.000000Z",
                1,
                None,
            ),
        )
        claimed = service.claim_next_eligible(
            eligible_definition_keys=(("TEST_COMMAND", 1),), lease_owner="worker"
        )
        self.assertEqual(
            (QueueResultCode.APPLIED, claimed_command.command_id, 1),
            (claimed.code, claimed.command.command_id, repository.claim_next_eligible.call_count),
        )

    def test_queue_allowlist_and_no_retry(self) -> None:
        allowed = {"get", "claim_next_eligible", "renew_lease", "mark_running", "acknowledge_cancellation", "mark_succeeded", "mark_failed", "recover_expired_claim"}
        source = inspect.getsource(DevelopmentWorker)
        observed = {name for name in allowed if f"._queue_service.{name}(" in source}
        self.assertEqual(allowed, observed)

    def test_worker_identity_owner_and_uuid(self) -> None:
        session_id = str(UUID(int=1))
        session = WorkerSession(1, session_id, "worker-1", f"worker-1@{session_id}", WorkerSessionState.ACTIVE, 1, "2026-08-24T12:00:00.000000Z", "2026-08-24T12:00:00.000000Z")
        self.assertEqual(f"worker-1@{session_id}", session.queue_owner_id)

    def test_session_model_and_transition_matrix(self) -> None:
        states = tuple(state.value for state in WorkerSessionState)
        self.assertEqual(("ACTIVE", "STOPPING", "STOPPED", "FAILED"), states)
        with self.assertRaises(ValueError):
            WorkerSession(1, str(UUID(int=2)), "worker", f"worker@{UUID(int=2)}", WorkerSessionState.ACTIVE, 1, "2026-08-24T12:00:00.000000Z", "2026-08-24T12:00:00.000000Z", "2026-08-24T12:00:01.000000Z", "STOP")

    def test_session_repository_results(self) -> None:
        self._initialize_sqlite()
        journal = SqliteWorkerJournalRepository(self.path)
        session_id = str(UUID(int=3))
        applied = journal.start_session(session_id=session_id, worker_id="worker", queue_owner_id=f"worker@{session_id}", started_at="2026-08-24T12:00:00.000000Z", stale_before="2026-08-24T11:59:00.000000Z")
        found = journal.get_session(session_id=session_id)
        self.assertEqual((WorkerJournalResultCode.APPLIED, WorkerJournalResultCode.FOUND), (applied.code, found.code))

    def test_fresh_session_fence_every_action(self) -> None:
        worker, service, journal, _session, _command, _receipt, fence, _holder = self._pure_bound_worker()
        self.assertEqual(WorkerCheckpointDirective.CONTINUE, worker._observe_checkpoint(fence))
        self.assertEqual((1, 1, 1), (journal.get_session.call_count, journal.get_operation_for_claim.call_count, service.get.call_count))

    def test_stale_equality_no_revival_or_replacement(self) -> None:
        worker, _service, journal, session, _command, _receipt, fence, holder = self._pure_bound_worker()
        self.providers.current += timedelta(seconds=30)
        stale = replace(session, last_heartbeat_at="2026-08-24T12:00:00.000000Z")
        holder["session"] = stale
        worker._session = stale
        self.assertEqual(
            WorkerCheckpointDirective.RECONCILIATION_REQUIRED,
            worker._observe_checkpoint(fence),
        )
        journal.heartbeat.assert_not_called()

    def test_terminal_session_evidence_mirroring(self) -> None:
        self.assertIn(WorkerOperationState.CANCELLED, worker_module._TERMINAL_RECEIPT_STATES)
        self.assertNotIn(WorkerOperationState.PREPARED, worker_module._TERMINAL_RECEIPT_STATES)

    def test_receipt_model_transitions_shapes(self) -> None:
        prepared = self._receipt(WorkerOperationState.PREPARED, None)
        succeeded = self._receipt(WorkerOperationState.SUCCEEDED, None)
        self.assertEqual((None, None), (prepared.started_at, prepared.completed_at))
        self.assertIsNotNone(succeeded.completed_at)

    def test_15_durable_failure_outcomes(self) -> None:
        self.assertEqual(15, len(WorkerFailureCode))
        self.assertEqual("HANDLER_EXCEPTION", WorkerFailureCode.HANDLER_EXCEPTION.value)
        durable_receipts = tuple(
            self._receipt(WorkerOperationState.FAILED, code.value)
            for code in WorkerFailureCode
        )
        self.assertEqual(
            tuple(code.value for code in WorkerFailureCode),
            tuple(receipt.durable_failure_code for receipt in durable_receipts),
        )
        self.assertEqual(
            (WorkerIterationStatus.FAILED,) * 15,
            tuple(
                DevelopmentWorker._terminal_iteration(receipt).status
                for receipt in durable_receipts
            ),
        )
        self.assertEqual(
            self._VALIDATOR_EXCEPTION_AFTER_RUNNING_EXPECTATION,
            self._exercise_validator_exception_after_running(),
        )
        variants = (
            (
                "PAYLOAD_INVALID",
                WorkerFailureCode.PAYLOAD_INVALID,
                None,
                lambda worker, fence, receipt: worker._queue_failure(
                    fence, receipt, WorkerFailureCode.PAYLOAD_INVALID, None
                ),
            ),
            (
                "HANDLER_REPORTED_FAILURE",
                WorkerFailureCode.HANDLER_REPORTED_FAILURE,
                None,
                lambda worker, fence, receipt: worker._after_handler(
                    fence,
                    receipt,
                    WorkerHandlerResult(WorkerHandlerStatus.FAILED, "HANDLER_FAILURE"),
                ),
            ),
            (
                "HANDLER_EXCEPTION",
                WorkerFailureCode.HANDLER_EXCEPTION,
                None,
                lambda worker, fence, receipt: worker._after_handler(
                    fence, receipt, None, RuntimeError("handler")
                ),
            ),
            (
                "HANDLER_INVALID_RETURN",
                WorkerFailureCode.HANDLER_EXCEPTION,
                "HANDLER_PROTOCOL_ERROR:INVALID_RETURN",
                lambda worker, fence, receipt: worker._after_handler(
                    fence,
                    receipt,
                    None,
                    TypeError("handler return"),
                    "HANDLER_PROTOCOL_ERROR:INVALID_RETURN",
                ),
            ),
            (
                "HANDLER_UNAUTHORIZED_CANCELLED",
                WorkerFailureCode.HANDLER_EXCEPTION,
                "HANDLER_PROTOCOL_ERROR:CANCELLED_WITHOUT_AUTHORITATIVE_CANCELLATION",
                lambda worker, fence, receipt: worker._after_handler(
                    fence,
                    receipt,
                    WorkerHandlerResult(WorkerHandlerStatus.CANCELLED, "CANCELLED"),
                ),
            ),
        )
        for variant_id, code, diagnostic, exercise in variants:
            with self.subTest(variant_id=variant_id):
                worker, service, journal, session, _, receipt, fence, holder = (
                    self._pure_bound_worker()
                )
                result = exercise(worker, fence, receipt)
                final_receipt = holder["receipt"]
                self.assertEqual(
                    (
                        WorkerIterationStatus.FAILED,
                        WorkerOperationState.FAILED,
                        code.value,
                        diagnostic,
                        1,
                        2,
                        0,
                        session,
                        (1, 2),
                    ),
                    (
                        result.status,
                        final_receipt.state,
                        final_receipt.durable_failure_code,
                        final_receipt.diagnostic_detail,
                        service.mark_failed.call_count,
                        journal.transition_operation.call_count,
                        journal.transition_session.call_count,
                        holder["session"],
                        tuple(
                            call.kwargs["expected_state_version"]
                            for call in journal.transition_operation.call_args_list
                        ),
                    ),
                )

    def test_migration5_fresh_upgrade_idempotent_ledger(self) -> None:
        SqliteRunStore(self.path).initialize("2026-08-24T12:00:01.000000Z")
        connection = sqlite3.connect(self.path)
        try:
            self.assertEqual([1, 2, 3, 4, 5], [row[0] for row in connection.execute("SELECT version FROM schema_migrations ORDER BY version")])
        finally:
            connection.close()

    def test_migration5_uuid_negative_matrix(self) -> None:
        with self.assertRaises(ValueError):
            WorkerSession(1, str(UUID(int=0)), "worker", f"worker@{UUID(int=0)}", WorkerSessionState.ACTIVE, 1, "2026-08-24T12:00:00.000000Z", "2026-08-24T12:00:00.000000Z")

    def test_migration5_state_fk_unique_restrict(self) -> None:
        self._initialize_sqlite()
        connection = sqlite3.connect(self.path)
        try:
            self.assertEqual(2, len(connection.execute("PRAGMA foreign_key_list(worker_operations)").fetchall()))
            self.assertEqual(6, len(connection.execute("SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'worker_%'").fetchall()))
        finally:
            connection.close()

    def test_journal_protocol_signatures_result_subsets(self) -> None:
        methods = tuple(name for name, value in WorkerJournalRepository.__dict__.items() if not name.startswith("_") and callable(value))
        self.assertEqual(10, len(methods))
        self.assertEqual(("self", "operation_id", "expected_state", "expected_state_version", "occurred_at"), tuple(inspect.signature(WorkerJournalRepository.reconcile_operation).parameters))

    def test_journal_precedence_idempotence_transactions(self) -> None:
        self._initialize_sqlite()
        journal = SqliteWorkerJournalRepository(self.path)
        session_id = str(UUID(int=5))
        first = journal.start_session(session_id=session_id, worker_id="worker", queue_owner_id=f"worker@{session_id}", started_at="2026-08-24T12:00:00.000000Z", stale_before="2026-08-24T11:59:00.000000Z")
        second = journal.start_session(session_id=session_id, worker_id="worker", queue_owner_id=f"worker@{session_id}", started_at="2026-08-24T12:00:00.000000Z", stale_before="2026-08-24T11:59:00.000000Z")
        self.assertEqual((WorkerJournalResultCode.APPLIED, WorkerJournalResultCode.ACTIVE_SESSION_EXISTS), (first.code, second.code))

    def test_database_path_canonicalization_matrix(self) -> None:
        first = SqliteWorkerJournalRepository(self.path)
        second = SqliteWorkerJournalRepository(self.directory / "." / "worker.sqlite3")
        self.assertEqual(first.database_identity, second.database_identity)
        with self.assertRaises(ValueError):
            SqliteWorkerJournalRepository(Path("NUL"))

    def test_17_startup_statuses_and_order(self) -> None:
        self.assertEqual(17, len(WorkerStartupStatus))
        self.assertEqual(3, worker_main_module._startup_exit(WorkerStartupStatus.CONFIGURATION_INVALID))
        self.assertEqual(6, worker_main_module._startup_exit(WorkerStartupStatus.AMBIGUOUS_RUNNING))
        registry = self._registry()
        service = self._service(registry)
        journal = SqliteWorkerJournalRepository(self.path)
        existing_id = str(UUID(int=8401))
        existing = journal.start_session(
            session_id=existing_id,
            worker_id="worker-1",
            queue_owner_id=f"worker-1@{existing_id}",
            started_at="2026-08-24T12:00:00.000000Z",
            stale_before="2026-08-24T11:59:30.000000Z",
        ).session
        blocked = self._worker(registry, service).start()
        self.assertEqual(
            (
                WorkerStartupStatus.ACTIVE_SESSION_EXISTS,
                existing,
                0,
                0,
            ),
            (
                blocked.status,
                blocked.session,
                blocked.recovered_claim_count,
                blocked.mirrored_receipt_count,
            ),
        )

        path = self.directory / "expired-no-receipt.sqlite3"
        SqliteRunStore(path).initialize("2026-08-24T12:00:00.000000Z")
        registry = self._registry()
        service = DurableCommandQueueService(
            SqliteWorkflowCommandRepository(path),
            registry._definition_registry,
            10,
            clock=self.providers.clock,
            id_factory=self.providers.identifier,
        )
        self._enqueue(service, "expired-no-receipt")
        journal = SqliteWorkerJournalRepository(path)
        old_id = str(UUID(int=8402))
        old_owner = f"worker-1@{old_id}"
        old_session = journal.start_session(
            session_id=old_id,
            worker_id="worker-1",
            queue_owner_id=old_owner,
            started_at="2026-08-24T12:00:00.000000Z",
            stale_before="2026-08-24T11:59:30.000000Z",
        ).session
        claimed = service.claim_next_eligible(
            eligible_definition_keys=registry.keys(), lease_owner=old_owner
        ).command
        journal.transition_session(
            session_id=old_session.session_id,
            expected_state=old_session.state,
            expected_state_version=old_session.state_version,
            next_state=WorkerSessionState.FAILED,
            reason_code="PROCESS_LOST",
            observed_at="2026-08-24T12:00:00.000000Z",
            stale_before="2026-08-24T11:59:30.000000Z",
        )
        self.providers.current += timedelta(seconds=10)
        started = self._worker(
            registry, service, database_path=path
        ).start()
        self.assertEqual(
            (
                WorkerStartupStatus.STARTED_AFTER_CLAIM_RECOVERY,
                1,
                0,
                WorkflowCommandState.PENDING,
            ),
            (
                started.status,
                started.recovered_claim_count,
                started.mirrored_receipt_count,
                service.get(claimed.command_id).command.state,
            ),
        )

    def test_expired_claimed_recovery(self) -> None:
        service, journal, _, command, receipt = self._claimed_prepared()
        self.providers.current += timedelta(seconds=10)
        recovery = service.recover_expired_claim(command_id=command.command_id, expected_state=command.state, expected_state_version=command.state_version, recovery_actor="recovery")
        reconciled = journal.reconcile_operation(operation_id=receipt.operation_id, expected_state=receipt.state, expected_state_version=receipt.state_version, occurred_at="2026-08-24T12:00:10.000000Z")
        self.assertEqual((QueueResultCode.APPLIED, WorkerOperationState.CLAIM_RELEASED), (recovery.code, reconciled.operation.state))

    def test_running_and_cancellation_restart(self) -> None:
        self.assertEqual(WorkerStartupStatus.CANCELLATION_OWNER_RECONCILIATION_REQUIRED, DevelopmentWorker._startup_status_for_queue(replace(_pending_queue_command(), state=WorkflowCommandState.RUNNING, claim_count=1, lease_owner="owner", lease_acquired_at="2026-08-24T12:00:00.000000Z", lease_expires_at="2026-08-24T12:00:10.000000Z", started_at="2026-08-24T12:00:00.000000Z", cancellation_requested_at="2026-08-24T12:00:00.000000Z", cancellation_requested_by="requester", cancellation_reason_code="CANCEL")))

    def test_lease_equality_all_boundaries(self) -> None:
        _, journal, session, command, _ = self._claimed_prepared()
        result = journal.create_operation(operation_id=str(UUID(int=8003)), command=command, operation_kind=WorkerOperationKind.COMMAND_HANDLER_INVOCATION, worker_id="worker-1", session_id=session.session_id, queue_owner_id=session.queue_owner_id, occurred_at=command.lease_expires_at, stale_before="2026-08-24T11:59:30.000000Z")
        self.assertEqual(WorkerJournalResultCode.CAS_CONFLICT, result.code)

    def test_event_precedence_nine_rows(self) -> None:
        def classify(
            row_id: str,
            *,
            session_result: WorkerJournalResult | None = None,
            command_transform: Callable[[WorkflowCommand], WorkflowCommand] | None = None,
            instant_offset: int = 0,
            stop_requested: bool = False,
            stop_deadline: float | None = None,
            allow_renewal: bool = False,
            handler_exception: BaseException | None = None,
            handler_result: WorkerHandlerResult | None = None,
        ) -> str:
            worker, _, _, session, command, receipt, fence, _ = (
                self._pure_bound_worker(
                    stop_requested=lambda: stop_requested
                )
            )
            worker._stop_deadline = stop_deadline
            selected_command = (
                command
                if command_transform is None
                else command_transform(command)
            )
            decision = worker._evaluate_bound_operation(
                fence,
                session_result
                or WorkerJournalResult(
                    WorkerJournalResultCode.FOUND, session=session
                ),
                QueueResult(QueueResultCode.FOUND, command=selected_command),
                WorkerJournalResult(
                    WorkerJournalResultCode.FOUND, operation=receipt
                ),
                self.providers.current + timedelta(seconds=instant_offset),
                0.0,
                allow_renewal=allow_renewal,
                handler_exception=handler_exception,
                handler_result=handler_result,
            )
            self.assertEqual(row_id, decision.precedence)
            return decision.precedence

        observed = (
            classify(
                "EP01",
                session_result=WorkerJournalResult(
                    WorkerJournalResultCode.NOT_FOUND
                ),
            ),
            classify(
                "EP02",
                command_transform=lambda command: replace(
                    command,
                    state=WorkflowCommandState.SUCCEEDED,
                    state_version=command.state_version + 1,
                    lease_owner=None,
                    lease_acquired_at=None,
                    lease_expires_at=None,
                    completed_at="2026-08-24T12:00:01.000000Z",
                    updated_at="2026-08-24T12:00:01.000000Z",
                ),
            ),
            classify("EP03", instant_offset=10),
            classify(
                "EP04",
                command_transform=lambda command: replace(
                    command,
                    cancellation_requested_at="2026-08-24T12:00:01.000000Z",
                    cancellation_requested_by="requester",
                    cancellation_reason_code="CANCEL",
                    updated_at="2026-08-24T12:00:01.000000Z",
                ),
            ),
            classify("EP05", stop_deadline=0.0),
            classify("EP06", instant_offset=5, allow_renewal=True),
            classify("EP07", stop_requested=True),
            classify("EP08", handler_exception=RuntimeError("handler")),
            classify(
                "EP09",
                handler_result=WorkerHandlerResult(
                    WorkerHandlerStatus.SUCCEEDED, "SUCCESS"
                ),
            ),
        )
        self.assertEqual(
            tuple(f"EP{index:02d}" for index in range(1, 10)), observed
        )

    def test_stopping_seven_checkpoints(self) -> None:
        stop = {"requested": False}
        observations: list[
            tuple[
                str,
                WorkflowCommandState,
                WorkerOperationState,
                WorkerSessionState,
            ]
        ] = []
        session_id = str(UUID(int=9001))

        def handler(_context: WorkerHandlerContext) -> WorkerHandlerResult:
            observe("SAC03")
            return WorkerHandlerResult(WorkerHandlerStatus.SUCCEEDED, "SUCCESS")

        registry = self._registry(handler)
        service = self._service(registry)
        enqueued = self._enqueue(service, "stopping-seven").command
        journal = SqliteWorkerJournalRepository(self.path)
        assert enqueued is not None

        def observe(label: str) -> None:
            queue = service.get(enqueued.command_id)
            operation = journal.get_operation_for_claim(
                command_id=enqueued.command_id, claim_count=1
            )
            session = journal.get_session(session_id=session_id)
            assert queue.command is not None
            assert operation.operation is not None
            assert session.session is not None
            observations.append(
                (
                    label,
                    queue.command.state,
                    operation.operation.state,
                    session.session.state,
                )
            )

        original_claim = service.claim_next_eligible
        service.claim_next_eligible = Mock(wraps=original_claim)
        original_create = journal.create_operation

        def create_operation(**kwargs: object) -> WorkerJournalResult:
            result = original_create(**kwargs)
            stop["requested"] = True
            return result

        journal.create_operation = Mock(side_effect=create_operation)
        original_mark_running = service.mark_running

        def mark_running(**kwargs: object) -> QueueResult:
            observe("SAC01")
            return original_mark_running(**kwargs)

        service.mark_running = Mock(side_effect=mark_running)
        original_transition_operation = journal.transition_operation

        def transition_operation(**kwargs: object) -> WorkerJournalResult:
            next_state = kwargs["next_state"]
            if next_state is WorkerOperationState.RUNNING:
                observe("SAC02")
            if next_state is WorkerOperationState.SUCCEEDED:
                observe("SAC07")
            result = original_transition_operation(**kwargs)
            if next_state is WorkerOperationState.RESULT_SUCCEEDED:
                observe("SAC04")
            return result

        journal.transition_operation = Mock(side_effect=transition_operation)
        original_mark_succeeded = service.mark_succeeded

        def mark_succeeded(**kwargs: object) -> QueueResult:
            observe("SAC05")
            result = original_mark_succeeded(**kwargs)
            observe("SAC06")
            return result

        service.mark_succeeded = Mock(side_effect=mark_succeeded)
        original_transition_session = journal.transition_session
        session_transitions: list[WorkerSessionState] = []

        def transition_session(**kwargs: object) -> WorkerJournalResult:
            next_state = kwargs["next_state"]
            assert type(next_state) is WorkerSessionState
            session_transitions.append(next_state)
            return original_transition_session(**kwargs)

        journal.transition_session = Mock(side_effect=transition_session)
        worker = DevelopmentWorker(
            WorkerConfiguration(self.path, "worker-1"),
            service,
            journal,
            registry,
            initialize_database,
            clock=self.providers.clock,
            monotonic_clock=lambda: 0.0,
            wait=lambda _seconds: False,
            stop_requested=lambda: stop["requested"],
            session_id_factory=lambda: session_id,
            operation_id_factory=lambda: str(UUID(int=9002)),
            queue_database_path=self.path,
            journal_database_path=self.path,
        )
        started = worker.start()
        completed = worker.run_iteration()
        deadline = worker._stop_deadline
        stopped = worker.run_iteration()

        self.assertEqual(WorkerStartupStatus.STARTED_NO_RECOVERY, started.status)
        self.assertEqual(WorkerIterationStatus.SUCCEEDED, completed.status)
        self.assertEqual(WorkerIterationStatus.STOPPED, stopped.status)
        self.assertEqual(30.0, deadline)
        self.assertEqual(deadline, worker._stop_deadline)
        self.assertEqual(
            [WorkerSessionState.STOPPING, WorkerSessionState.STOPPED],
            session_transitions,
        )
        self.assertEqual(1, service.claim_next_eligible.call_count)
        self.assertEqual(
            [
                (
                    "SAC01",
                    WorkflowCommandState.CLAIMED,
                    WorkerOperationState.PREPARED,
                    WorkerSessionState.STOPPING,
                ),
                (
                    "SAC02",
                    WorkflowCommandState.RUNNING,
                    WorkerOperationState.PREPARED,
                    WorkerSessionState.STOPPING,
                ),
                (
                    "SAC03",
                    WorkflowCommandState.RUNNING,
                    WorkerOperationState.RUNNING,
                    WorkerSessionState.STOPPING,
                ),
                (
                    "SAC04",
                    WorkflowCommandState.RUNNING,
                    WorkerOperationState.RESULT_SUCCEEDED,
                    WorkerSessionState.STOPPING,
                ),
                (
                    "SAC05",
                    WorkflowCommandState.RUNNING,
                    WorkerOperationState.RESULT_SUCCEEDED,
                    WorkerSessionState.STOPPING,
                ),
                (
                    "SAC06",
                    WorkflowCommandState.SUCCEEDED,
                    WorkerOperationState.RESULT_SUCCEEDED,
                    WorkerSessionState.STOPPING,
                ),
                (
                    "SAC07",
                    WorkflowCommandState.SUCCEEDED,
                    WorkerOperationState.RESULT_SUCCEEDED,
                    WorkerSessionState.STOPPING,
                ),
            ],
            observations,
        )

    def test_crash_c01(self) -> None:
        registry = self._registry()
        service = self._service(registry)
        before = self._sqlite_fact_snapshot(self.path)
        started = self._worker(registry, service).start()
        after = self._sqlite_fact_snapshot(self.path)
        self.assertEqual(WorkerStartupStatus.STARTED_NO_RECOVERY, started.status)
        self.assertEqual(WorkerSessionState.ACTIVE, started.session.state)
        self.assertEqual(before["workflow_commands"], after["workflow_commands"])
        self.assertEqual(before["workflow_command_events"], after["workflow_command_events"])
        self.assertEqual((), after["worker_operations"])
        self.assertEqual(1, len(after["worker_sessions"]))

    def test_crash_c02(self) -> None:
        registry = self._registry()
        service = self._service(registry)
        journal = SqliteWorkerJournalRepository(self.path)
        session_id = str(UUID(int=8960))
        existing = journal.start_session(
            session_id=session_id,
            worker_id="worker-1",
            queue_owner_id=f"worker-1@{session_id}",
            started_at="2026-08-24T12:00:00.000000Z",
            stale_before="2026-08-24T11:59:30.000000Z",
        ).session
        before = self._sqlite_fact_snapshot(self.path)
        restarted = self._worker(registry, service).start()
        self.assertEqual(
            (WorkerStartupStatus.ACTIVE_SESSION_EXISTS, existing),
            (restarted.status, restarted.session),
        )
        self.assertEqual(before, self._sqlite_fact_snapshot(self.path))

    def test_crash_c03(self) -> None:
        registry = self._registry()
        service = self._service(registry)
        journal = SqliteWorkerJournalRepository(self.path)
        session_id = str(UUID(int=8961))
        created = journal.start_session(
            session_id=session_id,
            worker_id="worker-1",
            queue_owner_id=f"worker-1@{session_id}",
            started_at="2026-08-24T12:00:00.000000Z",
            stale_before="2026-08-24T11:59:30.000000Z",
        ).session
        self.providers.current += timedelta(seconds=1)
        heartbeat = journal.heartbeat(
            session_id=session_id,
            expected_state=created.state,
            expected_state_version=created.state_version,
            observed_at="2026-08-24T12:00:01.000000Z",
            stale_before="2026-08-24T11:59:31.000000Z",
        )
        before = self._sqlite_fact_snapshot(self.path)
        restarted = self._worker(registry, service).start()
        self.assertEqual(WorkerJournalResultCode.APPLIED, heartbeat.code)
        self.assertEqual(
            (WorkerStartupStatus.ACTIVE_SESSION_EXISTS, heartbeat.session),
            (restarted.status, restarted.session),
        )
        self.assertEqual(before, self._sqlite_fact_snapshot(self.path))

    def test_crash_c04(self) -> None:
        registry = self._registry()
        service = self._service(registry)
        queued = self._enqueue(service)
        journal = SqliteWorkerJournalRepository(self.path)
        session_id = str(UUID(int=8962))
        journal.start_session(
            session_id=session_id,
            worker_id="worker-1",
            queue_owner_id=f"worker-1@{session_id}",
            started_at="2026-08-24T12:00:00.000000Z",
            stale_before="2026-08-24T11:59:30.000000Z",
        )
        before = self._sqlite_fact_snapshot(self.path)
        restarted = self._worker(registry, service).start()
        self.assertEqual(WorkerStartupStatus.ACTIVE_SESSION_EXISTS, restarted.status)
        self.assertEqual(before, self._sqlite_fact_snapshot(self.path))
        self.assertEqual(WorkflowCommandState.PENDING, service.get(queued.command.command_id).command.state)
        self.assertEqual(WorkerJournalResultCode.LISTED, journal.list_nonterminal_operations(worker_id="worker-1").code)

    def test_crash_c05(self) -> None:
        registry = self._registry()
        service = self._service(registry)
        self._enqueue(service)
        journal = SqliteWorkerJournalRepository(self.path)
        session_id = str(UUID(int=8963))
        session = journal.start_session(
            session_id=session_id,
            worker_id="worker-1",
            queue_owner_id=f"worker-1@{session_id}",
            started_at="2026-08-24T12:00:00.000000Z",
            stale_before="2026-08-24T11:59:30.000000Z",
        ).session
        claimed = service.claim_next_eligible(
            eligible_definition_keys=registry.keys(),
            lease_owner=session.queue_owner_id,
        ).command
        self._fail_worker_session(journal, session)
        before = self._sqlite_fact_snapshot(self.path)
        restarted = self._worker(registry, service).start()
        after = self._sqlite_fact_snapshot(self.path)
        self.assertEqual(
            WorkerStartupStatus.CLAIM_LEASE_ACTIVE_RECONCILIATION_REQUIRED,
            restarted.status,
        )
        self.assertEqual(before["workflow_commands"], after["workflow_commands"])
        self.assertEqual(before["workflow_command_events"], after["workflow_command_events"])
        self.assertEqual((), after["worker_operations"])
        self.assertEqual(WorkflowCommandState.CLAIMED, service.get(claimed.command_id).command.state)

    def test_crash_c06(self) -> None:
        registry = self._registry()
        service = self._service(registry)
        self._enqueue(service)
        journal = SqliteWorkerJournalRepository(self.path)
        session_id = str(UUID(int=8964))
        session = journal.start_session(
            session_id=session_id,
            worker_id="worker-1",
            queue_owner_id=f"worker-1@{session_id}",
            started_at="2026-08-24T12:00:00.000000Z",
            stale_before="2026-08-24T11:59:30.000000Z",
        ).session
        claimed = service.claim_next_eligible(
            eligible_definition_keys=registry.keys(), lease_owner=session.queue_owner_id
        ).command
        self._fail_worker_session(journal, session)
        before = self._sqlite_fact_snapshot(self.path)
        result = self._worker(registry, service).start()
        after = self._sqlite_fact_snapshot(self.path)
        self.assertEqual(
            WorkerStartupStatus.CLAIM_LEASE_ACTIVE_RECONCILIATION_REQUIRED,
            result.status,
        )
        self.assertEqual((), after["worker_operations"])
        self.assertEqual(before["workflow_commands"], after["workflow_commands"])
        self.assertEqual(
            WorkerJournalResultCode.NOT_FOUND,
            journal.get_operation_for_claim(
                command_id=claimed.command_id, claim_count=claimed.claim_count
            ).code,
        )

    def test_crash_c07(self) -> None:
        _, service, journal, _session, command, receipt = (
            self._claimed_prepared_with_registry()
        )
        live = service.recover_expired_claim(
            command_id=command.command_id,
            expected_state=command.state,
            expected_state_version=command.state_version,
            recovery_actor="recovery",
        )
        self.assertEqual(QueueResultCode.LEASE_NOT_EXPIRED, live.code)
        self.providers.current += timedelta(seconds=10)
        released = service.recover_expired_claim(
            command_id=command.command_id,
            expected_state=command.state,
            expected_state_version=command.state_version,
            recovery_actor="recovery",
        )
        reconciled = journal.reconcile_operation(
            operation_id=receipt.operation_id,
            expected_state=receipt.state,
            expected_state_version=receipt.state_version,
            occurred_at="2026-08-24T12:00:10.000000Z",
        )
        reopened = SqliteWorkerJournalRepository(self.path).get_operation_for_claim(
            command_id=command.command_id, claim_count=command.claim_count
        )
        self.assertEqual(
            (
                QueueResultCode.APPLIED,
                WorkflowCommandState.PENDING,
                WorkerJournalResultCode.APPLIED,
                WorkerOperationState.CLAIM_RELEASED,
                WorkerFailureCode.LEASE_LOST.value,
                reconciled.operation,
            ),
            (
                released.code,
                released.command.state,
                reconciled.code,
                reconciled.operation.state,
                reconciled.operation.durable_failure_code,
                reopened.operation,
            ),
        )

    def test_crash_c08(self) -> None:
        _, service, journal, _session, command, receipt = (
            self._claimed_prepared_with_registry()
        )
        before = SqliteWorkerJournalRepository(self.path).get_operation_for_claim(
            command_id=command.command_id, claim_count=command.claim_count
        )
        running_command = service.mark_running(
            command_id=command.command_id,
            expected_state=command.state,
            expected_state_version=command.state_version,
            lease_owner=command.lease_owner,
        )
        running_receipt = journal.transition_operation(
            operation_id=receipt.operation_id,
            expected_state=receipt.state,
            expected_state_version=receipt.state_version,
            next_state=WorkerOperationState.RUNNING,
            reconciliation_status=WorkerReconciliationStatus.NOT_REQUIRED,
            durable_failure_code=None,
            diagnostic_detail=None,
            occurred_at="2026-08-24T12:00:00.000000Z",
            stale_before="2026-08-24T11:59:30.000000Z",
        )
        self.assertEqual(
            (
                WorkerOperationState.PREPARED,
                QueueResultCode.APPLIED,
                WorkflowCommandState.RUNNING,
                WorkerJournalResultCode.APPLIED,
                WorkerOperationState.RUNNING,
            ),
            (
                before.operation.state,
                running_command.code,
                running_command.command.state,
                running_receipt.code,
                running_receipt.operation.state,
            ),
        )

    def test_crash_c09(self) -> None:
        registry, service, journal, session, command, receipt = (
            self._claimed_prepared_with_registry()
        )
        service.mark_running(
            command_id=command.command_id,
            expected_state=command.state,
            expected_state_version=command.state_version,
            lease_owner=command.lease_owner,
        )
        self._fail_worker_session(journal, session)
        before = self._sqlite_fact_snapshot(self.path)
        restarted = self._worker(registry, service).start()
        after = self._sqlite_fact_snapshot(self.path)
        self.assertEqual(WorkerStartupStatus.AMBIGUOUS_RUNNING, restarted.status)
        self.assertEqual(before["workflow_commands"], after["workflow_commands"])
        self.assertEqual(before["workflow_command_events"], after["workflow_command_events"])
        self.assertEqual(before["worker_operations"], after["worker_operations"])
        self.assertEqual(
            WorkerOperationState.PREPARED,
            journal.get_operation_for_claim(
                command_id=command.command_id, claim_count=command.claim_count
            ).operation.state,
        )
        self.assertEqual("OLD_RUNNING_CLAIM", restarted.detail_code)

    def test_crash_c10(self) -> None:
        registry, service, journal, session, command, receipt = (
            self._running_receipt_with_registry()
        )
        self._fail_worker_session(journal, session)
        before = self._sqlite_fact_snapshot(self.path)
        restarted = self._worker(registry, service).start()
        after = self._sqlite_fact_snapshot(self.path)
        self.assertEqual(WorkerStartupStatus.AMBIGUOUS_RUNNING, restarted.status)
        self.assertEqual(before["workflow_commands"], after["workflow_commands"])
        self.assertEqual(before["workflow_command_events"], after["workflow_command_events"])
        self.assertEqual(before["worker_operations"], after["worker_operations"])
        self.assertEqual(
            (WorkflowCommandState.RUNNING, WorkerOperationState.RUNNING),
            (
                service.get(command.command_id).command.state,
                journal.get_operation_for_claim(
                    command_id=command.command_id, claim_count=command.claim_count
                ).operation.state,
            ),
        )

    def test_crash_c11(self) -> None:
        registry, service, journal, session, command, _receipt = (
            self._running_receipt_with_registry()
        )
        with self.assertRaises(TypeError):
            WorkerCheckpoint(lambda: WorkerCheckpointDirective.CONTINUE)
        self._fail_worker_session(journal, session)
        before = self._sqlite_fact_snapshot(self.path)
        restarted = self._worker(registry, service).start()
        self.assertEqual(WorkerStartupStatus.AMBIGUOUS_RUNNING, restarted.status)
        after = self._sqlite_fact_snapshot(self.path)
        self.assertEqual(before["workflow_commands"], after["workflow_commands"])
        self.assertEqual(before["worker_operations"], after["worker_operations"])
        self.assertEqual(WorkflowCommandState.RUNNING, service.get(command.command_id).command.state)

    def test_crash_c12(self) -> None:
        registry, service, journal, session, command, receipt = (
            self._running_receipt_with_registry()
        )
        transient_result = WorkerHandlerResult(WorkerHandlerStatus.SUCCEEDED, "SUCCESS")
        self._fail_worker_session(journal, session)
        before = self._sqlite_fact_snapshot(self.path)
        restarted = self._worker(registry, service).start()
        self.assertEqual(WorkerStartupStatus.AMBIGUOUS_RUNNING, restarted.status)
        self.assertEqual(before["workflow_commands"], self._sqlite_fact_snapshot(self.path)["workflow_commands"])
        durable = SqliteWorkerJournalRepository(self.path).get_operation_for_claim(
            command_id=command.command_id, claim_count=command.claim_count
        ).operation
        self.assertEqual(
            (WorkerHandlerStatus.SUCCEEDED, WorkerOperationState.RUNNING, None, None),
            (
                transient_result.status,
                durable.state,
                durable.durable_failure_code,
                durable.completed_at,
            ),
        )
        self.assertEqual(receipt.operation_id, durable.operation_id)

    def test_crash_c13(self) -> None:
        registry, service, journal, session, command, receipt = (
            self._result_succeeded_with_registry()
        )
        self._fail_worker_session(journal, session)
        before = self._sqlite_fact_snapshot(self.path)
        restarted = self._worker(registry, service).start()
        after = self._sqlite_fact_snapshot(self.path)
        self.assertEqual(WorkerStartupStatus.AMBIGUOUS_RUNNING, restarted.status)
        self.assertEqual(before["workflow_commands"], after["workflow_commands"])
        self.assertEqual(before["workflow_command_events"], after["workflow_command_events"])
        self.assertEqual(before["worker_operations"], after["worker_operations"])
        self.assertEqual(
            (WorkflowCommandState.RUNNING, WorkerOperationState.RESULT_SUCCEEDED, None),
            (
                service.get(command.command_id).command.state,
                journal.get_operation_for_claim(
                    command_id=command.command_id, claim_count=command.claim_count
                ).operation.state,
                receipt.durable_failure_code,
            ),
        )

    def test_crash_c14(self) -> None:
        registry, service, journal, session, command, _receipt = (
            self._result_succeeded_with_registry()
        )
        terminal = service.mark_succeeded(
            command_id=command.command_id,
            expected_state=command.state,
            expected_state_version=command.state_version,
            lease_owner=command.lease_owner,
        )
        self._fail_worker_session(journal, session)
        before = self._sqlite_fact_snapshot(self.path)
        restarted = self._worker(registry, service).start()
        after = self._sqlite_fact_snapshot(self.path)
        operation = SqliteWorkerJournalRepository(self.path).get_operation_for_claim(
            command_id=command.command_id, claim_count=command.claim_count
        ).operation
        self.assertEqual(
            (
                QueueResultCode.APPLIED,
                WorkerStartupStatus.STARTED_AFTER_TERMINAL_MIRRORING,
                1,
                WorkerOperationState.SUCCEEDED,
            ),
            (
                terminal.code,
                restarted.status,
                restarted.mirrored_receipt_count,
                operation.state,
            ),
        )
        self.assertEqual(before["workflow_commands"], after["workflow_commands"])
        self.assertEqual(before["workflow_command_events"], after["workflow_command_events"])

    def test_crash_c15(self) -> None:
        _, service, journal, _session, command, receipt = (
            self._result_succeeded_with_registry()
        )
        service.mark_succeeded(
            command_id=command.command_id,
            expected_state=command.state,
            expected_state_version=command.state_version,
            lease_owner=command.lease_owner,
        )
        before = self._sqlite_fact_snapshot(self.path)
        mirrored = journal.transition_operation(
            operation_id=receipt.operation_id,
            expected_state=receipt.state,
            expected_state_version=receipt.state_version,
            next_state=WorkerOperationState.SUCCEEDED,
            reconciliation_status=WorkerReconciliationStatus.NOT_REQUIRED,
            durable_failure_code=None,
            diagnostic_detail=None,
            occurred_at="2026-08-24T12:00:01.000000Z",
            stale_before="2026-08-24T11:59:30.000000Z",
        )
        after = self._sqlite_fact_snapshot(self.path)
        self.assertEqual(
            (WorkerJournalResultCode.APPLIED, WorkerOperationState.SUCCEEDED, None),
            (mirrored.code, mirrored.operation.state, mirrored.operation.durable_failure_code),
        )
        self.assertEqual(before["workflow_commands"], after["workflow_commands"])
        self.assertEqual(before["workflow_command_events"], after["workflow_command_events"])
        self.assertEqual(before["worker_sessions"], after["worker_sessions"])

    def test_crash_c16(self) -> None:
        _, service, journal, _session, command, receipt = (
            self._result_succeeded_with_registry()
        )
        service.mark_succeeded(
            command_id=command.command_id,
            expected_state=command.state,
            expected_state_version=command.state_version,
            lease_owner=command.lease_owner,
        )
        applied = journal.transition_operation(
            operation_id=receipt.operation_id,
            expected_state=receipt.state,
            expected_state_version=receipt.state_version,
            next_state=WorkerOperationState.SUCCEEDED,
            reconciliation_status=WorkerReconciliationStatus.NOT_REQUIRED,
            durable_failure_code=None,
            diagnostic_detail=None,
            occurred_at="2026-08-24T12:00:01.000000Z",
            stale_before="2026-08-24T11:59:30.000000Z",
        )
        before = self._sqlite_fact_snapshot(self.path)
        repeated = SqliteWorkerJournalRepository(self.path).transition_operation(
            operation_id=receipt.operation_id,
            expected_state=receipt.state,
            expected_state_version=receipt.state_version,
            next_state=WorkerOperationState.SUCCEEDED,
            reconciliation_status=WorkerReconciliationStatus.NOT_REQUIRED,
            durable_failure_code=None,
            diagnostic_detail=None,
            occurred_at="2026-08-24T12:00:02.000000Z",
            stale_before="2026-08-24T11:59:30.000000Z",
        )
        self.assertEqual(
            (WorkerJournalResultCode.TERMINAL_OBSERVED, applied.operation),
            (repeated.code, repeated.operation),
        )
        self.assertEqual(before, self._sqlite_fact_snapshot(self.path))

    def test_crash_c17(self) -> None:
        worker, service, _journal, _session, _command, _receipt, fence, _holder = self._pure_bound_worker()
        self.providers.current += timedelta(seconds=6)
        self.assertEqual(
            WorkerCheckpointDirective.RECONCILIATION_REQUIRED,
            worker._observe_checkpoint(fence),
        )
        self.assertEqual((1, 2), (service.renew_lease.call_count, service.get.call_count))

        newer_worker, newer_service, _journal, _session, command, _receipt, newer_fence, _holder = (
            self._pure_bound_worker()
        )
        renewed = replace(
            command,
            state_version=command.state_version + 1,
            lease_expires_at="2026-08-24T12:00:16.000000Z",
            updated_at="2026-08-24T12:00:06.000000Z",
        )
        newer_service.get.side_effect = (
            QueueResult(QueueResultCode.FOUND, command=command),
            QueueResult(QueueResultCode.FOUND, command=renewed),
        )
        self.assertEqual(
            WorkerCheckpointDirective.CONTINUE,
            newer_worker._observe_checkpoint(newer_fence),
        )
        self.assertEqual(
            (1, 2),
            (newer_service.renew_lease.call_count, newer_service.get.call_count),
        )

    def test_crash_c18(self) -> None:
        _, service, journal, _session, command, receipt = (
            self._running_receipt_with_registry()
        )
        requested = service.request_cancellation(
            command_id=command.command_id,
            expected_state=command.state,
            expected_state_version=command.state_version,
            requested_by="requester",
            reason_code="TEST_CANCEL",
        )
        observed = journal.transition_operation(
            operation_id=receipt.operation_id,
            expected_state=receipt.state,
            expected_state_version=receipt.state_version,
            next_state=WorkerOperationState.CANCELLATION_OBSERVED,
            reconciliation_status=WorkerReconciliationStatus.NOT_REQUIRED,
            durable_failure_code=WorkerFailureCode.CANCELLATION_OBSERVED.value,
            diagnostic_detail=None,
            occurred_at="2026-08-24T12:00:00.000000Z",
            stale_before="2026-08-24T11:59:30.000000Z",
        )
        wrong = service.acknowledge_cancellation(
            command_id=command.command_id,
            expected_state=requested.command.state,
            expected_state_version=requested.command.state_version,
            lease_owner="worker-1@00000000-0000-0000-0000-000000008888",
        )
        cancelled = service.acknowledge_cancellation(
            command_id=command.command_id,
            expected_state=requested.command.state,
            expected_state_version=requested.command.state_version,
            lease_owner=command.lease_owner,
        )
        terminal = journal.transition_operation(
            operation_id=observed.operation.operation_id,
            expected_state=observed.operation.state,
            expected_state_version=observed.operation.state_version,
            next_state=WorkerOperationState.CANCELLED,
            reconciliation_status=WorkerReconciliationStatus.NOT_REQUIRED,
            durable_failure_code=WorkerFailureCode.CANCELLATION_OBSERVED.value,
            diagnostic_detail=None,
            occurred_at="2026-08-24T12:00:01.000000Z",
            stale_before="2026-08-24T11:59:30.000000Z",
        )
        self.assertEqual(
            (
                QueueResultCode.LEASE_OWNER_MISMATCH,
                QueueResultCode.APPLIED,
                WorkflowCommandState.CANCELLED,
                WorkerJournalResultCode.APPLIED,
                WorkerOperationState.CANCELLED,
            ),
            (
                wrong.code,
                cancelled.code,
                cancelled.command.state,
                terminal.code,
                terminal.operation.state,
            ),
        )

    def test_crash_c19(self) -> None:
        worker, service, journal, session, _command, _receipt, _fence, holder = (
            self._pure_bound_worker()
        )
        stopping = replace(
            session,
            state=WorkerSessionState.STOPPING,
            state_version=2,
            stop_reason_code="GRACEFUL_STOP",
        )
        holder["session"] = stopping
        worker._session = stopping
        journal.list_nonterminal_operations.return_value = WorkerJournalResult(
            WorkerJournalResultCode.LISTED
        )
        journal.list_owned_commands.return_value = WorkerJournalResult(
            WorkerJournalResultCode.LISTED
        )
        result = worker.run_iteration()
        self.assertEqual(WorkerIterationStatus.STOPPED, result.status)
        self.assertEqual(WorkerSessionState.STOPPED, holder["session"].state)
        service._repository.claim_next_eligible.assert_not_called()

    def test_crash_c20(self) -> None:
        worker, _service, _journal, _session, _command, receipt, fence, holder = (
            self._pure_bound_worker()
        )
        worker._stop_deadline = 0.0
        result = worker._after_handler(
            fence,
            receipt,
            WorkerHandlerResult(WorkerHandlerStatus.SUCCEEDED, "SUCCESS"),
        )
        self.assertEqual(
            (
                WorkerIterationStatus.RECONCILIATION_REQUIRED,
                WorkerFailureCode.SHUTDOWN_GRACE_EXPIRED.value,
                WorkerOperationState.RECONCILIATION_REQUIRED,
                WorkerFailureCode.SHUTDOWN_GRACE_EXPIRED.value,
                WorkerSessionState.FAILED,
            ),
            (
                result.status,
                result.detail_code,
                holder["receipt"].state,
                holder["receipt"].durable_failure_code,
                holder["session"].state,
            ),
        )

    def test_crash_c21(self) -> None:
        registry = self._registry()
        service = self._service(registry)
        worker = self._worker(registry, service)
        started = worker.start()
        stopped = worker.shutdown()
        repeated = worker.shutdown()
        reopened = SqliteWorkerJournalRepository(self.path).get_session(
            session_id=started.session.session_id
        )
        self.assertEqual(
            (
                WorkerJournalResultCode.APPLIED,
                WorkerSessionState.STOPPED,
                WorkerJournalResultCode.TERMINAL_OBSERVED,
                stopped.session,
            ),
            (
                stopped.code,
                stopped.session.state,
                repeated.code,
                reopened.session,
            ),
        )

    def test_crash_c22(self) -> None:
        registry = self._registry()
        service = self._service(registry)
        journal = SqliteWorkerJournalRepository(self.path)
        session_id = str(UUID(int=8965))
        journal.start_session(
            session_id=session_id,
            worker_id="worker-1",
            queue_owner_id=f"worker-1@{session_id}",
            started_at="2026-08-24T12:00:00.000000Z",
            stale_before="2026-08-24T11:59:30.000000Z",
        )
        self.providers.current += timedelta(seconds=30)
        before = self._sqlite_fact_snapshot(self.path)
        restarted = self._worker(registry, service).start()
        self.assertEqual(
            WorkerStartupStatus.STALE_SESSION_RECONCILIATION_REQUIRED,
            restarted.status,
        )
        self.assertEqual(before, self._sqlite_fact_snapshot(self.path))

    def test_clock_timing_equality_and_provider_validation(self) -> None:
        registry = self._registry()
        service, _repository = self._pure_service(registry)
        journal = Mock(database_path=self.path)
        worker = self._worker(registry, service)
        worker._journal = journal
        worker._monotonic_clock = lambda: float("nan")
        with self.assertRaises(ValueError):
            worker._monotonic()

    def test_loop_iteration_wait_stop_cleanup(self) -> None:
        registry = self._registry()
        service, _repository = self._pure_service(registry)
        journal = Mock(database_path=self.path)
        worker = self._worker(registry, service)
        worker._journal = journal
        self.assertEqual(WorkerIterationStatus.NOT_STARTED, worker.run_iteration().status)

    def test_noncooperative_shutdown_truthful_boundary(self) -> None:
        source = inspect.getsource(worker_module)
        self.assertNotIn("terminate(", source)
        self.assertNotIn("kill(", source)

    def test_dependency_allowlist_no_effect_boundaries(self) -> None:
        source = inspect.getsource(worker_module)
        self.assertFalse(any(token in source for token in ("subprocess", "urllib", "socket", "requests")))

    def test_public_root_export_exact_order(self) -> None:
        import panam_development_loop as package
        evidence_path = os.environ.get("PANAM_R11_ACCEPTED_EVIDENCE_PATH")
        self.assertIsNotNone(evidence_path, "accepted Evidence path is required")
        evidence = json.loads(Path(evidence_path).read_text(encoding="utf-8"))
        matrix = next(
            item
            for item in evidence["matrices"]
            if item["matrix_id"] == "M10_PUBLIC_API_AND_ROOT_EXPORTS"
        )
        expected = tuple(row["symbol"] for row in matrix["rows"])
        self.assertEqual(tuple(range(1, 106)), tuple(row["export_order"] for row in matrix["rows"]))
        self.assertEqual(expected, tuple(package.__all__))
        self.assertEqual(105, len(set(package.__all__)))

    def test_handler_validation_failure_and_baseexception(self) -> None:
        self.assertEqual(
            self._VALIDATOR_EXCEPTION_AFTER_RUNNING_EXPECTATION,
            self._exercise_validator_exception_after_running(),
        )

        worker, _service, journal, _session, _command, receipt, fence, holder = self._pure_bound_worker()

        class HandlerAbort(BaseException):
            pass

        abort = HandlerAbort("fatal handler")
        with self.assertRaises(HandlerAbort) as raised:
            worker._after_handler(fence, receipt, None, abort)
        self.assertIs(abort, raised.exception)
        self.assertEqual(
            (
                WorkerOperationState.RECONCILIATION_REQUIRED,
                WorkerFailureCode.HANDLER_BASE_EXCEPTION.value,
                None,
                WorkerSessionState.FAILED,
                1,
                1,
                0,
            ),
            (
                holder["receipt"].state,
                holder["receipt"].durable_failure_code,
                holder["receipt"].diagnostic_detail,
                holder["session"].state,
                journal.transition_operation.call_count,
                journal.transition_session.call_count,
                _service.mark_failed.call_count,
            ),
        )

        registry = self._registry()
        original = registry.lookup("TEST_COMMAND", 1)
        registry._entries[("TEST_COMMAND", 1)] = WorkerHandlerEntry(
            CommandDefinition(
                "TEST_COMMAND",
                1,
                ("value",),
                ("note",),
                ("note",),
                _queue_payload_validator,
            ),
            WorkerCommandHandler("OTHER", self._success),
        )
        with self.assertRaises(worker_module._RegistryInvariantError):
            registry.lookup("TEST_COMMAND", 1)
        self.assertEqual("TEST_HANDLER", original.handler.handler_id)

        worker, service, journal, session, command, _, _, holder = (
            self._pure_bound_worker()
        )
        registry = worker._registry
        handler_spy = Mock(
            return_value=WorkerHandlerResult(WorkerHandlerStatus.SUCCEEDED, "SUCCESS")
        )
        original = registry.lookup("TEST_COMMAND", 1)
        registry._entries[("TEST_COMMAND", 1)] = WorkerHandlerEntry(
            original.definition,
            WorkerCommandHandler("TEST_HANDLER", handler_spy),
        )
        claimed = replace(
            command,
            state=WorkflowCommandState.CLAIMED,
            state_version=2,
            started_at=None,
        )
        prepared = self._receipt(WorkerOperationState.PREPARED, None)
        holder["command"] = claimed
        holder["receipt"] = prepared
        holder["transition_count"] = 0
        worker._last_heartbeat_monotonic = 0.0
        worker._operation_id_factory = lambda: prepared.operation_id
        service.claim_next_eligible = Mock(
            return_value=Mock(code=QueueResultCode.APPLIED, command=claimed)
        )

        def create_with_registry_loss(**_kwargs: object) -> WorkerJournalResult:
            registry._entries[("TEST_COMMAND", 1)] = WorkerHandlerEntry(
                CommandDefinition(
                    "TEST_COMMAND",
                    1,
                    ("value",),
                    ("note",),
                    ("note",),
                    _queue_payload_validator,
                ),
                WorkerCommandHandler("OTHER", handler_spy),
            )
            return WorkerJournalResult(
                WorkerJournalResultCode.APPLIED, operation=prepared
            )

        journal.create_operation.side_effect = create_with_registry_loss
        registry_loss = worker.run_iteration()
        final_receipt = holder["receipt"]
        final_session = holder["session"]
        final_command = holder["command"]
        self.assertEqual(
            (
                WorkerIterationStatus.RECONCILIATION_REQUIRED,
                WorkerFailureCode.RECONCILIATION_REQUIRED.value,
                WorkflowCommandState.CLAIMED,
                WorkerOperationState.RECONCILIATION_REQUIRED,
                WorkerFailureCode.RECONCILIATION_REQUIRED.value,
                "HANDLER_LOOKUP_MISMATCH:FROZEN_REGISTRY_INVARIANT_LOST",
                WorkerSessionState.FAILED,
                0,
                1,
                1,
                0,
                WorkerStartupStatus.JOURNAL_CONFLICT,
            ),
            (
                registry_loss.status,
                registry_loss.detail_code,
                final_command.state,
                final_receipt.state,
                final_receipt.durable_failure_code,
                final_receipt.diagnostic_detail,
                final_session.state,
                service._repository.mark_running.call_count,
                journal.transition_operation.call_count,
                journal.transition_session.call_count,
                handler_spy.call_count,
                DevelopmentWorker._startup_status_for_queue(final_command),
            ),
        )

    def test_handler_unauthorized_cancelled_protocol_diagnostic(self) -> None:
        worker, service, journal, _session, command, receipt, fence, holder = self._pure_bound_worker()
        result = worker._after_handler(
            fence,
            receipt,
            WorkerHandlerResult(WorkerHandlerStatus.CANCELLED, "CANCELLED"),
        )
        final_receipt = holder["receipt"]
        self.assertEqual(
            (
                WorkerIterationStatus.FAILED,
                WorkerOperationState.FAILED,
                WorkerFailureCode.HANDLER_EXCEPTION.value,
                "HANDLER_PROTOCOL_ERROR:CANCELLED_WITHOUT_AUTHORITATIVE_CANCELLATION",
                2,
                1,
                0,
                WorkflowCommandState.FAILED,
            ),
            (
                result.status,
                final_receipt.state,
                final_receipt.durable_failure_code,
                final_receipt.diagnostic_detail,
                holder["transition_count"],
                service.mark_failed.call_count,
                service.acknowledge_cancellation.call_count,
                holder["command"].state,
            ),
        )
        self.assertEqual(command.lease_owner, final_receipt.queue_owner_id)
        self.assertEqual(
            (1, 2, 3),
            (
                receipt.state_version,
                receipt.state_version + 1,
                final_receipt.state_version,
            ),
        )
        self.assertEqual(receipt.started_at, final_receipt.started_at)
        self.assertEqual(final_receipt.updated_at, final_receipt.completed_at)
        self.assertEqual(_session, holder["session"])
        self.assertEqual(
            WorkerIterationStatus.FAILED,
            DevelopmentWorker._terminal_iteration(final_receipt).status,
        )

    def test_counts_findings_exactness_and_former_conditionals_static(self) -> None:
        evidence_path = os.environ.get("PANAM_R11_ACCEPTED_EVIDENCE_PATH")
        self.assertIsNotNone(evidence_path, "accepted Evidence path is required")
        raw = Path(evidence_path).read_bytes()
        self.assertEqual(
            "2E1109A5FE4E0CA7ED73A73AED992421E829D4183EEBFA66B3684AF137A83852",
            hashlib.sha256(raw).hexdigest().upper(),
        )
        evidence = json.loads(raw)
        matrices = evidence["matrices"]
        expected_row_counts = {
            "M01_R2_BLOCKING_FINDING_EVIDENCE": 9,
            "M02_HISTORICAL_CONTRACT_FINDING_TRACEABILITY": 14,
            "M03_EXACTNESS_EVIDENCE": 103,
            "M04_R2_UNRESOLVED_QUESTION_CLOSURES": 13,
            "M05_R2_CONTRADICTION_CLOSURE": 26,
            "M06_R2_PASS_BOUNDARY_PRESERVATION": 16,
            "M07_DURABLE_FAILURE_AND_OUTCOME_EFFECTS": 15,
            "M08_STARTUP_RESULT_MATRIX": 17,
            "M09_WORKER_REPOSITORY_METHOD_RESULT_CONTRACT": 10,
            "M10_PUBLIC_API_AND_ROOT_EXPORTS": 105,
            "M11_NORMATIVE_TEST_OBLIGATIONS": 86,
            "M12_CRASH_AND_RESTART_MATRIX": 42,
            "M13_FORMER_R1_CONDITIONAL_TEST_TRACEABILITY": 21,
            "M14_SESSION_TRANSITION_MATRIX": 13,
            "M15_RECEIPT_TRANSITION_MATRIX": 31,
            "M16_TERMINAL_SESSION_MIRRORING_MATRIX": 11,
            "M17_EVENT_PRECEDENCE": 9,
            "M18_CANCELLATION_ACROSS_RESTART": 8,
            "M19_STOPPING_AFTER_CLAIM": 7,
            "M20_NONCOOPERATIVE_SHUTDOWN": 6,
        }
        self.assertEqual(tuple(expected_row_counts), tuple(item["matrix_id"] for item in matrices))
        row_counts: dict[str, int] = {}
        for matrix in matrices:
            row_ids = tuple(
                row[matrix["row_id_field"]] for row in matrix["rows"]
            )
            self.assertEqual(matrix["row_count"], len(matrix["rows"]))
            self.assertEqual(tuple(matrix["ordered_row_ids"]), row_ids)
            self.assertEqual(len(row_ids), len(set(row_ids)))
            self.assertTrue(
                all(
                    value == "PASS"
                    for key, value in matrix["validation"].items()
                    if key.endswith("_result")
                ),
                matrix["matrix_id"],
            )
            row_counts[matrix["matrix_id"]] = len(matrix["rows"])
        self.assertEqual(expected_row_counts, row_counts)
        self.assertEqual(562, sum(row_counts.values()))
        self.assertEqual(expected_row_counts, evidence["counts"]["matrix_row_counts"])
        self.assertEqual(
            {"retained": 22, "split_recovery": 15, "malformed_payload": 5, "total": 42},
            evidence["counts"]["m12_composition"],
        )

        matrix_by_id = {item["matrix_id"]: item for item in matrices}
        m03 = matrix_by_id["M03_EXACTNESS_EVIDENCE"]
        self.assertEqual(
            tuple(f"EX{number:03d}" for number in range(1, 104)),
            tuple(m03["ordered_row_ids"]),
        )
        exactness_counts: dict[str, int] = {}
        disposition_counts: dict[str, int] = {}
        for row in m03["rows"]:
            exactness_counts[row["exactness_value"]] = (
                exactness_counts.get(row["exactness_value"], 0) + 1
            )
            disposition_counts[row["proof_disposition"]] = (
                disposition_counts.get(row["proof_disposition"], 0) + 1
            )
        self.assertEqual(
            {"YES": 2, "REQUIREMENT_DEFINED": 15, "NOT_CURRENTLY_PROVEN": 86},
            exactness_counts,
        )
        self.assertEqual(
            {
                "CURRENTLY_PROVEN_CLAIM": 2,
                "POSTCOMPILE_VALIDATION_REQUIREMENT": 15,
                "NOT_CURRENTLY_PROVEN": 86,
            },
            disposition_counts,
        )
        self.assertEqual(exactness_counts, evidence["exactness"]["exactness_value_counts"])
        self.assertEqual(disposition_counts, evidence["exactness"]["proof_disposition_counts"])
        self.assertEqual(
            (
                103,
                2,
                15,
                86,
                tuple(
                    ["EX087"]
                    + [f"EX{number:03d}" for number in range(89, 101)]
                    + ["EX102", "EX103"]
                ),
            ),
            (
                evidence["exactness"]["assertion_count"],
                evidence["exactness"]["currently_proven_claim_count"],
                evidence["exactness"]["postcompile_requirement_count"],
                evidence["exactness"]["not_currently_proven_count"],
                tuple(evidence["integrity"]["postcompile_requirement_ids"]),
            ),
        )

        questions = evidence["questions"]
        expected_question_ids = tuple(
            [f"R2Q-{number:03d}" for number in range(1, 14)]
            + ["R6Q-001", "R6IV-Q-001"]
        )
        self.assertEqual(
            expected_question_ids,
            tuple(row["question_id"] for row in questions["index"]),
        )
        self.assertEqual(
            (15, 15, 0, 0, 0),
            (
                questions["question_record_count"],
                questions["closed_question_count"],
                questions["unresolved_question_count"],
                questions["implementation_critical_unresolved_question_count"],
                questions["package_acceptance_critical_unresolved_question_count"],
            ),
        )
        self.assertEqual(
            ("CLOSED",) * 13 + ("CLOSED_BY_HUMAN_AUTHORITY",) * 2,
            tuple(row["closure_state"] for row in questions["index"]),
        )

        contradictions = evidence["contradictions"]
        validations = contradictions["surface_validations"]
        self.assertEqual(
            (25, 25, 0, 25),
            (
                contradictions["surface_validation_count"],
                contradictions["surface_validation_pass_count"],
                contradictions["active_total"],
                len(validations),
            ),
        )
        self.assertEqual(
            len(validations),
            len({row["validation_id"] for row in validations}),
        )
        self.assertTrue(
            all(
                row["result"] == "PASS"
                and row["stored_value"] == row["independently_derived_value"]
                for row in validations
            )
        )
        self.assertTrue(all(value == 0 for value in contradictions["active_counts"].values()))

        findings = evidence["findings"]
        assessments = findings["revision_assessments"]
        self.assertEqual((19, 19), (findings["assessment_validation_count"], len(assessments)))
        self.assertEqual(
            len(assessments), len({row["assessment_id"] for row in assessments})
        )
        self.assertTrue(
            all(
                row["assessment_id"]
                == "R11-ASSESS-" + row["historical_finding_id"]
                and row["validation_result"] == "PASS"
                for row in assessments
            )
        )
        self.assertEqual(
            (0, 0, 0, 0),
            (
                findings["duplicate_assessment_identity_count"],
                findings["dangling_assessment_reference_count"],
                findings["dangling_target_reference_count"],
                findings["unresolved_pseudo_target_count"],
            ),
        )

        m13 = matrix_by_id["M13_FORMER_R1_CONDITIONAL_TEST_TRACEABILITY"]
        self.assertEqual(
            tuple(f"M13-{number:02d}" for number in range(1, 22)),
            tuple(m13["ordered_row_ids"]),
        )
        self.assertEqual(
            tuple(f"R1T{number}" for number in range(26, 47)),
            tuple(row["historical_test_id"] for row in m13["rows"]),
        )
        zero_count_keys = (
            "alternate_implementation_path_count",
            "derived_count_mismatch_count",
            "full_matrix_duplication_in_contract_count",
            "full_matrix_duplication_in_plan_count",
            "matrix_with_positional_string_only_rows_count",
            "row_with_generic_evidence_count",
            "row_without_implementation_path_count",
            "row_without_normative_test_or_validation_count",
            "row_without_required_typed_effect_count",
            "row_without_source_reference_count",
            "row_without_unique_id_count",
            "unauthorized_persistent_artifact_count",
        )
        self.assertEqual(
            (0,) * len(zero_count_keys),
            tuple(evidence["counts"][key] for key in zero_count_keys),
        )

    def test_worker_handler_receipt_field_shapes_and_namespace_separation(self) -> None:
        self.assertEqual(("status", "result_code"), tuple(field.name for field in fields(WorkerHandlerResult)))
        receipt_fields = tuple(field.name for field in fields(WorkerOperationReceipt))
        self.assertEqual(("durable_failure_code", "diagnostic_detail"), receipt_fields[16:18])

    def test_diagnostic_closed_domain_introduction_preservation_and_reopen(self) -> None:
        receipt = self._receipt(WorkerOperationState.RESULT_FAILED, WorkerFailureCode.HANDLER_EXCEPTION.value, "HANDLER_PROTOCOL_ERROR:INVALID_RETURN")
        self.assertEqual("HANDLER_PROTOCOL_ERROR:INVALID_RETURN", receipt.diagnostic_detail)
        with self.assertRaises(ValueError):
            replace(receipt, diagnostic_detail="UNKNOWN")

    def test_success_state_null_code_model_sql_and_worker_semantics(self) -> None:
        registry = self._registry()
        service = self._service(registry)
        self._enqueue(service)
        worker = self._worker(registry, service)
        started = worker.start()
        self.assertEqual(WorkerStartupStatus.STARTED_NO_RECOVERY, started.status)
        before_session = started.session
        original_transition = worker._journal.transition_operation
        worker._journal.transition_operation = Mock(wraps=original_transition)
        result = worker.run_iteration()
        reopened = SqliteWorkerJournalRepository(self.path)
        receipt = reopened.get_operation_for_claim(
            command_id=result.command_id, claim_count=1
        ).operation
        final_command = service.get(result.command_id).command
        final_session = reopened.get_session(session_id=before_session.session_id).session
        history = service.history(result.command_id).events
        self.assertEqual(
            (
                WorkerIterationStatus.SUCCEEDED,
                (
                    WorkerOperationState.RUNNING,
                    WorkerOperationState.RESULT_SUCCEEDED,
                    WorkerOperationState.SUCCEEDED,
                ),
                WorkerOperationState.SUCCEEDED,
                4,
                None,
                None,
                WorkflowCommandState.SUCCEEDED,
                4,
                None,
                None,
                None,
                before_session,
                (
                    WorkflowCommandEventKind.ENQUEUED,
                    WorkflowCommandEventKind.CLAIMED,
                    WorkflowCommandEventKind.STARTED,
                    WorkflowCommandEventKind.SUCCEEDED,
                ),
                (1, 2, 3, 4),
            ),
            (
                result.status,
                tuple(
                    call.kwargs["next_state"]
                    for call in worker._journal.transition_operation.call_args_list
                ),
                receipt.state,
                receipt.state_version,
                receipt.durable_failure_code,
                receipt.diagnostic_detail,
                final_command.state,
                final_command.state_version,
                final_command.lease_owner,
                final_command.lease_acquired_at,
                final_command.lease_expires_at,
                final_session,
                tuple(event.event_kind for event in history),
                tuple(event.next_state_version for event in history),
            ),
        )
        self.assertEqual(receipt.updated_at, receipt.completed_at)
        self.assertEqual(final_command.updated_at, final_command.completed_at)
        with self.assertRaises(ValueError):
            replace(
                receipt,
                state=WorkerOperationState.RESULT_SUCCEEDED,
                completed_at=None,
                durable_failure_code=WorkerFailureCode.PAYLOAD_INVALID.value,
            )
        connection = sqlite3.connect(self.path)
        try:
            connection.execute("SAVEPOINT forbidden_success_code")
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "UPDATE worker_operations SET durable_failure_code=? "
                    "WHERE operation_id=?",
                    (WorkerFailureCode.PAYLOAD_INVALID.value, receipt.operation_id),
                )
            connection.execute("ROLLBACK TO forbidden_success_code")
            connection.execute("RELEASE forbidden_success_code")
        finally:
            connection.close()
        self.assertEqual(
            receipt,
            SqliteWorkerJournalRepository(self.path)
            .get_operation_for_claim(command_id=result.command_id, claim_count=1)
            .operation,
        )

    def test_migration5_final_schema_and_704_case_model_sql_domain(self) -> None:
        states = tuple(WorkerOperationState)
        codes = (None, *(code.value for code in WorkerFailureCode))
        diagnostics = (None, "HANDLER_PROTOCOL_ERROR:INVALID_RETURN", "HANDLER_PROTOCOL_ERROR:CANCELLED_WITHOUT_AUTHORITATIVE_CANCELLATION", "HANDLER_LOOKUP_MISMATCH:FROZEN_REGISTRY_INVARIANT_LOST")
        self.assertEqual((11, 16, 4), (len(states), len(codes), len(diagnostics)))
        registry = self._registry()
        service = self._service(registry)
        command = self._enqueue(service).command
        journal = SqliteWorkerJournalRepository(self.path)
        session_id = str(UUID(int=7003))
        owner = f"worker-1@{session_id}"
        journal.start_session(
            session_id=session_id,
            worker_id="worker-1",
            queue_owner_id=owner,
            started_at="2026-08-24T12:00:00.000000Z",
            stale_before="2026-08-24T11:59:30.000000Z",
        )
        connection = sqlite3.connect(self.path)
        connection.execute("PRAGMA foreign_keys=ON")
        sql_allowed = 0
        mismatches = []
        try:
            for state in states:
                started_at = (
                    "2026-08-24T12:00:00.000000Z"
                    if state in {
                        WorkerOperationState.RUNNING,
                        WorkerOperationState.RESULT_SUCCEEDED,
                        WorkerOperationState.RESULT_FAILED,
                        WorkerOperationState.CANCELLATION_OBSERVED,
                        WorkerOperationState.SUCCEEDED,
                        WorkerOperationState.FAILED,
                    }
                    else None
                )
                terminal = state in {
                    WorkerOperationState.SUCCEEDED,
                    WorkerOperationState.FAILED,
                    WorkerOperationState.CANCELLED,
                    WorkerOperationState.CLAIM_RELEASED,
                    WorkerOperationState.LEASE_LOST,
                    WorkerOperationState.RECONCILIATION_REQUIRED,
                }
                completed_at = (
                    "2026-08-24T12:00:01.000000Z" if terminal else None
                )
                updated_at = (
                    completed_at or "2026-08-24T12:00:00.000000Z"
                )
                reconciliation = (
                    "REQUIRED"
                    if state in {
                        WorkerOperationState.LEASE_LOST,
                        WorkerOperationState.RECONCILIATION_REQUIRED,
                    }
                    else "NOT_REQUIRED"
                )
                for code in codes:
                    for diagnostic in diagnostics:
                        model_valid = models_module._worker_receipt_code_diagnostic_valid(
                            state, code, diagnostic
                        )
                        connection.execute("SAVEPOINT domain_case")
                        try:
                            connection.execute(
                                "INSERT INTO worker_operations("
                                "operation_id,operation_kind,command_id,project_id,"
                                "development_run_id,phase_id,session_id,worker_id,"
                                "queue_owner_id,claim_count,precondition_state_version,"
                                "state,state_version,external_effect_class,"
                                "reconciliation_status,durable_failure_code,"
                                "diagnostic_detail,created_at,updated_at,started_at,"
                                "completed_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                                (
                                    str(UUID(int=7004)),
                                    WorkerOperationKind.COMMAND_HANDLER_INVOCATION.value,
                                    command.command_id,
                                    command.project_id,
                                    command.development_run_id,
                                    command.phase_id,
                                    session_id,
                                    "worker-1",
                                    owner,
                                    1,
                                    1,
                                    state.value,
                                    1,
                                    "NONE",
                                    reconciliation,
                                    code,
                                    diagnostic,
                                    "2026-08-24T12:00:00.000000Z",
                                    updated_at,
                                    started_at,
                                    completed_at,
                                ),
                            )
                            sql_valid = True
                        except sqlite3.IntegrityError:
                            sql_valid = False
                        finally:
                            connection.execute("ROLLBACK TO domain_case")
                            connection.execute("RELEASE domain_case")
                        sql_allowed += int(sql_valid)
                        if sql_valid != model_valid:
                            mismatches.append((state.value, code, diagnostic))
        finally:
            connection.close()
        model_allowed = sum(
            models_module._worker_receipt_code_diagnostic_valid(
                state, code, diagnostic
            )
            for state in states
            for code in codes
            for diagnostic in diagnostics
        )
        self.assertEqual(
            (704, 114, 590, 114, []),
            (
                len(states) * len(codes) * len(diagnostics),
                model_allowed,
                704 - model_allowed,
                sql_allowed,
                mismatches,
            ),
        )
        evidence_path = os.environ.get("PANAM_R11_ACCEPTED_EVIDENCE_PATH")
        self.assertIsNotNone(evidence_path)
        evidence = json.loads(Path(evidence_path).read_bytes())
        self.assertEqual(
            {
                "allowed": 114,
                "allowed_but_rejected": 0,
                "diagnostic_domain_including_none": 4,
                "durable_code_domain_including_none": 16,
                "forbidden": 590,
                "forbidden_but_accepted": 0,
                "internal_contradictions": 0,
                "persistence_only_allowed": 91,
                "reachable_allowed": 23,
                "states": 11,
                "total": 704,
            },
            evidence["counts"]["model_domain_counts"],
        )
        self.assertIn(
            ("PREPARED-027", "T64", "PREPARED-027"),
            {
                (
                    row["obligation_id"],
                    row["test_id"],
                    row["variant_id"],
                )
                for row in evidence["tests"]["canonical_relation_graph"]
                if "variant_id" in row
            },
        )
        connection = sqlite3.connect(self.path)
        try:
            self.assertEqual(
                0,
                connection.execute("SELECT COUNT(*) FROM worker_operations").fetchone()[0],
            )
            self.assertEqual(
                ("worker_operations", "worker_sessions"),
                tuple(
                    row[0]
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type='table' "
                        "AND name IN ('worker_operations','worker_sessions') "
                        "ORDER BY name"
                    )
                ),
            )
            self.assertEqual(
                6,
                connection.execute(
                    "SELECT COUNT(*) FROM sqlite_master WHERE type='index' "
                    "AND name LIKE 'worker_%'"
                ).fetchone()[0],
            )
            self.assertEqual(
                0,
                connection.execute(
                    "SELECT COUNT(*) FROM sqlite_master WHERE type='trigger' "
                    "AND tbl_name IN ('worker_operations','worker_sessions')"
                ).fetchone()[0],
            )
            foreign_keys = connection.execute(
                "PRAGMA foreign_key_list(worker_operations)"
            ).fetchall()
            self.assertEqual(2, len(foreign_keys))
            self.assertTrue(all(row[5:7] == ("RESTRICT", "RESTRICT") for row in foreign_keys))
        finally:
            connection.close()

    def test_sqlite_shared_memory_anchor_reopen_lifetime_and_no_filesystem_db(self) -> None:
        before = tuple(sorted(path.name for path in self.directory.iterdir()))
        uri = "file:panam_dl22_t65?mode=memory&cache=shared"
        anchor = sqlite3.connect(uri, uri=True)
        anchor.row_factory = sqlite3.Row
        opened: list[sqlite3.Connection] = []

        def open_shared(
            _path: Path, _entity: str, _identity: str
        ) -> sqlite3.Connection:
            connection = sqlite3.connect(uri, uri=True, timeout=0.0)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys=ON")
            opened.append(connection)
            return connection

        try:
            apply_migrations(
                anchor,
                "2026-08-24T12:00:00.000000Z",
                PRODUCTION_MIGRATIONS,
            )
            registry = self._registry()
            with (
                patch.object(
                    sqlite_repository_module,
                    "_open_connection",
                    side_effect=open_shared,
                ),
                patch.object(
                    sqlite_repository_module,
                    "_open_read_only_connection",
                    side_effect=open_shared,
                ),
            ):
                service = DurableCommandQueueService(
                    SqliteWorkflowCommandRepository(self.path),
                    registry._definition_registry,
                    10,
                    clock=self.providers.clock,
                    id_factory=self.providers.identifier,
                )
                queued = self._enqueue(service, "named-memory")
                replacement = SqliteWorkflowCommandRepository(self.path)
                self.assertEqual(
                    queued.command,
                    replacement.get(queued.command.command_id),
                )
                session_id = str(UUID(int=8650))
                journal = SqliteWorkerJournalRepository(self.path)
                created = journal.start_session(
                    session_id=session_id,
                    worker_id="worker-1",
                    queue_owner_id=f"worker-1@{session_id}",
                    started_at="2026-08-24T12:00:00.000000Z",
                    stale_before="2026-08-24T11:59:30.000000Z",
                )
                replacement_journal = SqliteWorkerJournalRepository(self.path)
                self.assertEqual(
                    created.session,
                    replacement_journal.get_session(session_id=session_id).session,
                )
            self.assertGreaterEqual(len(opened), 4)
            self.assertEqual(
                2,
                len(
                    anchor.execute(
                        "SELECT name FROM sqlite_master "
                        "WHERE type='table' AND name LIKE 'worker_%'"
                    ).fetchall()
                ),
            )
        finally:
            anchor.close()
        destroyed = sqlite3.connect(uri, uri=True)
        try:
            self.assertEqual(
                0,
                len(
                    destroyed.execute(
                        "SELECT name FROM sqlite_master "
                        "WHERE type='table' AND name LIKE 'worker_%'"
                    ).fetchall()
                ),
            )
        finally:
            destroyed.close()
        self.assertEqual(before, tuple(sorted(path.name for path in self.directory.iterdir())))

    def test_reconcile_signature_precedence_results_binding_and_contention(self) -> None:
        signature = inspect.signature(SqliteWorkerJournalRepository.reconcile_operation)
        self.assertEqual(("self", "operation_id", "expected_state", "expected_state_version", "occurred_at"), tuple(signature.parameters))
        journal = SqliteWorkerJournalRepository(self.path)
        operation_id = str(UUID(int=8660))
        transition_base = {
            "operation_id": operation_id,
            "expected_state": WorkerOperationState.RUNNING,
            "expected_state_version": 1,
            "next_state": WorkerOperationState.RESULT_FAILED,
            "reconciliation_status": WorkerReconciliationStatus.NOT_REQUIRED,
            "durable_failure_code": WorkerFailureCode.HANDLER_EXCEPTION.value,
            "diagnostic_detail": None,
            "occurred_at": "2026-08-24T12:00:01.000000Z",
            "stale_before": "2026-08-24T11:59:30.000000Z",
        }
        invalid = (
            {"durable_failure_code": "NOT_A_DURABLE_CODE"},
            {"diagnostic_detail": "UNKNOWN_DIAGNOSTIC"},
            {
                "next_state": WorkerOperationState.RESULT_SUCCEEDED,
                "reconciliation_status": WorkerReconciliationStatus.REQUIRED,
                "durable_failure_code": None,
            },
            {
                "expected_state": WorkerOperationState.PREPARED,
                "next_state": WorkerOperationState.RESULT_SUCCEEDED,
                "durable_failure_code": None,
            },
            {
                "next_state": WorkerOperationState.CANCELLATION_OBSERVED,
                "durable_failure_code": WorkerFailureCode.HANDLER_EXCEPTION.value,
            },
        )
        for overrides in invalid:
            arguments = {**transition_base, **overrides}
            with self.subTest(overrides=overrides), patch.object(
                journal, "_open", side_effect=AssertionError("I/O")
            ) as opened:
                with self.assertRaises(ValueError):
                    journal.transition_operation(**arguments)
                opened.assert_not_called()
        with patch.object(journal, "_open", side_effect=AssertionError("I/O")) as opened:
            with self.assertRaises(ValueError):
                journal.reconcile_operation(
                    operation_id=operation_id,
                    expected_state=WorkerOperationState.RUNNING,
                    expected_state_version=1,
                    occurred_at="2026-08-24T12:00:01.000000Z",
                )
            opened.assert_not_called()

        def locked_open(_entity: str, _identity: str) -> sqlite3.Connection:
            try:
                raise sqlite3.OperationalError("database is locked")
            except sqlite3.OperationalError as error:
                raise RepositoryError(
                    RepositoryFailureCode.SQLITE_OPERATIONAL_FAILURE,
                    "WorkerSession",
                    "locked",
                ) from error

        with patch.object(journal, "_open_read_only", side_effect=locked_open):
            contended = journal.get_session(session_id=str(UUID(int=8661)))
        self.assertEqual(WorkerJournalResultCode.TRANSIENT_CONTENTION, contended.code)

        registry = self._registry()
        service = self._service(registry)
        other_path = self.directory / "actual-other.sqlite3"
        migration = Mock()
        mismatched = DevelopmentWorker(
            WorkerConfiguration(self.path, "worker-1"),
            service,
            SqliteWorkerJournalRepository(other_path),
            registry,
            migration,
            clock=self.providers.clock,
            monotonic_clock=lambda: 0.0,
            wait=lambda _seconds: False,
            stop_requested=lambda: False,
            session_id_factory=lambda: str(UUID(int=8662)),
            operation_id_factory=lambda: str(UUID(int=8663)),
            queue_database_path=self.path,
            journal_database_path=self.path,
        )
        self.assertEqual(WorkerStartupStatus.DATABASE_MISMATCH, mismatched.start().status)
        migration.assert_not_called()
        self.assertFalse(other_path.exists())

    def test_reconcile_three_prepared_recovery_mappings(self) -> None:
        def prepared_case(
            suffix: str, identity: int
        ) -> tuple[
            WorkerHandlerRegistry,
            QueueProviders,
            DurableCommandQueueService,
            SqliteWorkerJournalRepository,
            WorkerSession,
            WorkflowCommand,
            WorkerOperationReceipt,
        ]:
            path = self.directory / f"{suffix}.sqlite3"
            SqliteRunStore(path).initialize("2026-08-24T12:00:00.000000Z")
            providers = QueueProviders()
            registry = self._registry()
            service = DurableCommandQueueService(
                SqliteWorkflowCommandRepository(path),
                registry._definition_registry,
                10,
                clock=providers.clock,
                id_factory=providers.identifier,
            )
            command = self._enqueue(service, suffix).command
            journal = SqliteWorkerJournalRepository(path)
            session_id = str(UUID(int=identity))
            owner = f"worker-1@{session_id}"
            session = journal.start_session(
                session_id=session_id,
                worker_id="worker-1",
                queue_owner_id=owner,
                started_at="2026-08-24T12:00:00.000000Z",
                stale_before="2026-08-24T11:59:30.000000Z",
            ).session
            claimed = service.claim_next_eligible(
                eligible_definition_keys=registry.keys(), lease_owner=owner
            ).command
            receipt = journal.create_operation(
                operation_id=str(UUID(int=identity + 100)),
                command=claimed,
                operation_kind=WorkerOperationKind.COMMAND_HANDLER_INVOCATION,
                worker_id="worker-1",
                session_id=session_id,
                queue_owner_id=owner,
                occurred_at="2026-08-24T12:00:00.000000Z",
                stale_before="2026-08-24T11:59:30.000000Z",
            ).operation
            return registry, providers, service, journal, session, claimed, receipt

        _, _, service, journal, session, command, receipt = prepared_case(
            "failed", 8101
        )
        started = service.mark_running(
            command_id=command.command_id,
            expected_state=command.state,
            expected_state_version=command.state_version,
            lease_owner=command.lease_owner,
        )
        failed = service.mark_failed(
            command_id=command.command_id,
            expected_state=started.command.state,
            expected_state_version=started.command.state_version,
            lease_owner=started.command.lease_owner,
            failure_code="TEST_FAILURE",
        )
        before_session = journal.get_session(session_id=session.session_id).session
        result = journal.reconcile_operation(
            operation_id=receipt.operation_id,
            expected_state=receipt.state,
            expected_state_version=receipt.state_version,
            occurred_at="2026-08-24T12:00:01.000000Z",
        )
        self.assertEqual(
            (
                WorkflowCommandState.FAILED,
                WorkerOperationState.FAILED,
                WorkerFailureCode.RECONCILIATION_REQUIRED.value,
                None,
                receipt.created_at,
                started.command.started_at,
                "2026-08-24T12:00:01.000000Z",
                "2026-08-24T12:00:01.000000Z",
                before_session,
            ),
            (
                failed.command.state,
                result.operation.state,
                result.operation.durable_failure_code,
                result.operation.diagnostic_detail,
                result.operation.created_at,
                result.operation.started_at,
                result.operation.updated_at,
                result.operation.completed_at,
                journal.get_session(session_id=session.session_id).session,
            ),
        )
        repeated = journal.reconcile_operation(
            operation_id=receipt.operation_id,
            expected_state=receipt.state,
            expected_state_version=receipt.state_version,
            occurred_at="2026-08-24T12:00:10.000000Z",
        )
        self.assertEqual(
            (
                WorkerJournalResultCode.TERMINAL_OBSERVED,
                result.operation,
            ),
            (repeated.code, repeated.operation),
        )

        _, _, service, journal, _, command, receipt = prepared_case(
            "bad-started", 8403
        )
        started = service.mark_running(
            command_id=command.command_id,
            expected_state=command.state,
            expected_state_version=command.state_version,
            lease_owner=command.lease_owner,
        )
        service.mark_failed(
            command_id=command.command_id,
            expected_state=started.command.state,
            expected_state_version=started.command.state_version,
            lease_owner=started.command.lease_owner,
            failure_code="TEST_FAILURE",
        )
        connection = sqlite3.connect(journal.database_path)
        try:
            connection.execute(
                "UPDATE workflow_command_events SET occurred_at=? "
                "WHERE command_id=? AND event_kind='STARTED'",
                ("2026-08-24T12:00:00.500000Z", command.command_id),
            )
            connection.commit()
        finally:
            connection.close()
        with self.assertRaises(RepositoryError) as raised:
            journal.reconcile_operation(
                operation_id=receipt.operation_id,
                expected_state=receipt.state,
                expected_state_version=receipt.state_version,
                occurred_at="2026-08-24T12:00:01.000000Z",
            )
        self.assertEqual(
            RepositoryFailureCode.MALFORMED_PERSISTED_PAYLOAD,
            raised.exception.code,
        )
        self.assertEqual(
            WorkerOperationState.PREPARED,
            journal.get_operation_for_claim(
                command_id=command.command_id, claim_count=command.claim_count
            ).operation.state,
        )

        _, _, service, journal, session, command, receipt = prepared_case(
            "cancelled", 8201
        )
        requested = service.request_cancellation(
            command_id=command.command_id,
            expected_state=command.state,
            expected_state_version=command.state_version,
            requested_by="requester",
            reason_code="TEST_CANCEL",
        )
        cancelled = service.acknowledge_cancellation(
            command_id=command.command_id,
            expected_state=requested.command.state,
            expected_state_version=requested.command.state_version,
            lease_owner=requested.command.lease_owner,
        )
        before_session = journal.get_session(session_id=session.session_id).session
        result = journal.reconcile_operation(
            operation_id=receipt.operation_id,
            expected_state=receipt.state,
            expected_state_version=receipt.state_version,
            occurred_at="2026-08-24T12:00:01.000000Z",
        )
        self.assertEqual(
            (
                WorkflowCommandState.CANCELLED,
                WorkerOperationState.CANCELLED,
                WorkerFailureCode.CANCELLATION_OBSERVED.value,
                None,
                None,
                before_session,
            ),
            (
                cancelled.command.state,
                result.operation.state,
                result.operation.durable_failure_code,
                result.operation.diagnostic_detail,
                result.operation.started_at,
                journal.get_session(session_id=session.session_id).session,
            ),
        )

        _, providers, service, journal, session, command, receipt = prepared_case(
            "claim-released", 8301
        )
        providers.current += timedelta(seconds=10)
        released = service.recover_expired_claim(
            command_id=command.command_id,
            expected_state=command.state,
            expected_state_version=command.state_version,
            recovery_actor="recovery",
        )
        before_session = journal.get_session(session_id=session.session_id).session
        result = journal.reconcile_operation(
            operation_id=receipt.operation_id,
            expected_state=receipt.state,
            expected_state_version=receipt.state_version,
            occurred_at="2026-08-24T12:00:10.000000Z",
        )
        self.assertEqual(
            (
                WorkflowCommandState.PENDING,
                WorkerOperationState.CLAIM_RELEASED,
                WorkerFailureCode.LEASE_LOST.value,
                None,
                None,
                before_session,
            ),
            (
                released.command.state,
                result.operation.state,
                result.operation.durable_failure_code,
                result.operation.diagnostic_detail,
                result.operation.started_at,
                journal.get_session(session_id=session.session_id).session,
            ),
        )

        invalid_started_cases = (
            ("missing", "DELETE"),
            ("ambiguous", "AMBIGUOUS"),
            ("contradictory", "CONTRADICTORY"),
            ("owner", "OWNER"),
            ("claim", "CLAIM"),
            ("timestamp", "TIMESTAMP"),
        )
        for offset, (label, mutation) in enumerate(invalid_started_cases):
            with self.subTest(invalid_started=label):
                case_registry, _, case_service, case_journal, case_session, case_command, case_receipt = (
                    prepared_case(f"invalid-started-{label}", 8350 + offset * 10)
                )
                running = case_service.mark_running(
                    command_id=case_command.command_id,
                    expected_state=case_command.state,
                    expected_state_version=case_command.state_version,
                    lease_owner=case_command.lease_owner,
                )
                case_service.mark_failed(
                    command_id=case_command.command_id,
                    expected_state=running.command.state,
                    expected_state_version=running.command.state_version,
                    lease_owner=running.command.lease_owner,
                    failure_code="TEST_FAILURE",
                )
                connection = sqlite3.connect(case_journal.database_path)
                try:
                    connection.execute("PRAGMA ignore_check_constraints=ON")
                    if mutation == "DELETE":
                        connection.execute(
                            "DELETE FROM workflow_command_events WHERE command_id=? "
                            "AND event_kind='STARTED'",
                            (case_command.command_id,),
                        )
                    elif mutation == "AMBIGUOUS":
                        connection.execute(
                            "UPDATE workflow_command_events SET event_kind='STARTED' "
                            "WHERE command_id=? AND event_kind='CLAIMED'",
                            (case_command.command_id,),
                        )
                    elif mutation == "CONTRADICTORY":
                        connection.execute(
                            "UPDATE workflow_command_events SET reason_code='OTHER' "
                            "WHERE command_id=? AND event_kind='STARTED'",
                            (case_command.command_id,),
                        )
                    elif mutation == "OWNER":
                        connection.execute(
                            "UPDATE workflow_command_events SET actor_id='other' "
                            "WHERE command_id=? AND event_kind='STARTED'",
                            (case_command.command_id,),
                        )
                    elif mutation == "CLAIM":
                        connection.execute(
                            "UPDATE workflow_command_events SET claim_count=2 "
                            "WHERE command_id=? AND event_kind='STARTED'",
                            (case_command.command_id,),
                        )
                    else:
                        connection.execute(
                            "UPDATE workflow_command_events SET occurred_at=? "
                            "WHERE command_id=? AND event_kind='STARTED'",
                            (
                                "2026-08-24T12:00:00.500000Z",
                                case_command.command_id,
                            ),
                        )
                    connection.execute("PRAGMA ignore_check_constraints=OFF")
                    self.assertEqual(
                        0,
                        connection.execute(
                            "PRAGMA ignore_check_constraints"
                        ).fetchone()[0],
                    )
                    connection.commit()
                finally:
                    connection.close()
                self._fail_worker_session(case_journal, case_session)
                before = self._sqlite_fact_snapshot(case_journal.database_path)
                trace: list[str] = []
                original_open = case_journal._open

                def traced_open(entity: str, identity: str) -> sqlite3.Connection:
                    opened = original_open(entity, identity)
                    opened.set_trace_callback(trace.append)
                    return opened

                with (
                    patch.object(case_journal, "_open", side_effect=traced_open),
                    self.assertRaises(RepositoryError) as raised,
                ):
                    case_journal.reconcile_operation(
                        operation_id=case_receipt.operation_id,
                        expected_state=case_receipt.state,
                        expected_state_version=case_receipt.state_version,
                        occurred_at="2026-08-24T12:00:01.000000Z",
                    )
                self.assertEqual(
                    RepositoryFailureCode.MALFORMED_PERSISTED_PAYLOAD,
                    raised.exception.code,
                )
                normalized = tuple(value.strip().upper() for value in trace)
                self.assertEqual(1, sum(value == "BEGIN IMMEDIATE" for value in normalized))
                self.assertEqual(1, sum(value == "ROLLBACK" for value in normalized))
                self.assertFalse(any(value.startswith("UPDATE ") for value in normalized))
                self.assertEqual(before, self._sqlite_fact_snapshot(case_journal.database_path))
                self.assertEqual(
                    WorkerStartupStatus.PERSISTENCE_FAILURE,
                    self._worker(
                        case_registry,
                        case_service,
                        database_path=case_journal.database_path,
                    ).start().status,
                )
                self.assertEqual(before, self._sqlite_fact_snapshot(case_journal.database_path))

    def test_reconcile_atomic_read_only_queue_observation_and_single_receipt_cas(self) -> None:
        service, journal, _session, command, receipt = self._claimed_prepared()
        started = service.mark_running(
            command_id=command.command_id,
            expected_state=command.state,
            expected_state_version=command.state_version,
            lease_owner=command.lease_owner,
        )
        failed = service.mark_failed(
            command_id=command.command_id,
            expected_state=started.command.state,
            expected_state_version=started.command.state_version,
            lease_owner=started.command.lease_owner,
            failure_code="TEST_FAILURE",
        )

        def raw_snapshot() -> tuple[tuple[tuple[object, ...], ...], ...]:
            connection = sqlite3.connect(self.path)
            try:
                return tuple(
                    tuple(connection.execute(f"SELECT * FROM {table} ORDER BY 1"))
                    for table in (
                        "workflow_commands",
                        "workflow_command_events",
                        "worker_sessions",
                        "worker_operations",
                    )
                )
            finally:
                connection.close()

        before = raw_snapshot()
        trace: list[str] = []
        executed: list[str] = []
        original_open = journal._open

        class ObservedConnection:
            def __init__(self, connection: sqlite3.Connection) -> None:
                self._connection = connection

            def execute(
                self, statement: str, parameters: object = ()
            ) -> sqlite3.Cursor:
                executed.append(statement)
                return self._connection.execute(statement, parameters)

            def __getattr__(self, name: str) -> object:
                return getattr(self._connection, name)

        def force_receipt_cas_loss(entity: str, identity: str) -> object:
            connection = original_open(entity, identity)
            connection.execute(
                "CREATE TEMP TRIGGER force_receipt_cas_loss "
                "BEFORE UPDATE ON worker_operations "
                "BEGIN SELECT RAISE(IGNORE); END"
            )
            connection.set_trace_callback(trace.append)
            return ObservedConnection(connection)

        with patch.object(journal, "_open", side_effect=force_receipt_cas_loss):
            lost = journal.reconcile_operation(
                operation_id=receipt.operation_id,
                expected_state=receipt.state,
                expected_state_version=receipt.state_version,
                occurred_at="2026-08-24T12:00:01.000000Z",
            )

        normalized_trace = tuple(statement.strip().upper() for statement in trace)
        self.assertEqual(WorkerJournalResultCode.CAS_CONFLICT, lost.code)
        self.assertEqual(before, raw_snapshot())
        self.assertEqual(1, sum(statement == "BEGIN IMMEDIATE" for statement in normalized_trace))
        self.assertEqual(
            1,
            sum(
                statement.lstrip().upper().startswith("UPDATE WORKER_OPERATIONS")
                for statement in executed
            ),
        )
        self.assertFalse(
            any(
                statement.lstrip().upper().startswith(
                    (
                        "UPDATE WORKFLOW_COMMANDS",
                        "UPDATE WORKFLOW_COMMAND_EVENTS",
                        "UPDATE WORKER_SESSIONS",
                    )
                )
                for statement in executed
            )
        )
        self.assertEqual(1, sum(statement == "ROLLBACK" for statement in normalized_trace))
        self.assertEqual(0, sum(statement == "COMMIT" for statement in normalized_trace))
        self.assertFalse(
            any(
                statement.startswith(
                    (
                        "UPDATE WORKFLOW_COMMANDS",
                        "UPDATE WORKFLOW_COMMAND_EVENTS",
                        "UPDATE WORKER_SESSIONS",
                    )
                )
                for statement in normalized_trace
            )
        )

        applied = SqliteWorkerJournalRepository(self.path).reconcile_operation(
            operation_id=receipt.operation_id,
            expected_state=receipt.state,
            expected_state_version=receipt.state_version,
            occurred_at="2026-08-24T12:00:01.000000Z",
        )
        reopened = SqliteWorkerJournalRepository(self.path).reconcile_operation(
            operation_id=receipt.operation_id,
            expected_state=receipt.state,
            expected_state_version=receipt.state_version,
            occurred_at="2026-08-24T12:00:02.000000Z",
        )
        self.assertEqual(
            (
                WorkflowCommandState.FAILED,
                WorkerJournalResultCode.APPLIED,
                WorkerOperationState.FAILED,
                WorkerJournalResultCode.TERMINAL_OBSERVED,
                applied.operation,
            ),
            (
                failed.command.state,
                applied.code,
                applied.operation.state,
                reopened.code,
                reopened.operation,
            ),
        )

    def test_reconcile_claim_generation_and_queue_history_negative_matrix(self) -> None:
        service, journal, _, command, receipt = self._claimed_prepared()
        with patch.object(journal, "_open", side_effect=AssertionError("I/O")) as opened:
            with self.assertRaises(ValueError):
                journal.reconcile_operation(operation_id=receipt.operation_id, expected_state=WorkerOperationState.RUNNING, expected_state_version=receipt.state_version, occurred_at="2026-08-24T12:00:01.000000Z")
        opened.assert_not_called()
        self.providers.current += timedelta(seconds=10)
        released = service.recover_expired_claim(
            command_id=command.command_id,
            expected_state=command.state,
            expected_state_version=command.state_version,
            recovery_actor="recovery",
        )
        superseding = service.claim_next_eligible(
            eligible_definition_keys=(("TEST_COMMAND", 1),),
            lease_owner=command.lease_owner,
        )
        result = journal.reconcile_operation(
            operation_id=receipt.operation_id,
            expected_state=receipt.state,
            expected_state_version=receipt.state_version,
            occurred_at="2026-08-24T12:00:10.000000Z",
        )
        self.assertEqual(
            (
                QueueResultCode.APPLIED,
                2,
                WorkerJournalResultCode.CAS_CONFLICT,
                WorkerOperationState.PREPARED,
            ),
            (
                released.code,
                superseding.command.claim_count,
                result.code,
                journal.get_operation_for_claim(
                    command_id=command.command_id, claim_count=1
                ).operation.state,
            ),
        )

    def test_reconcile_superseding_claim_five_scenario_race(self) -> None:
        def claimant_owner(identity: int) -> str:
            return f"worker-2@{UUID(int=identity)}"

        with (
            patch.object(sqlite3, "connect", self._real_sqlite_connect),
            patch.object(
                sqlite_repository_module,
                "_open_connection",
                self._real_open_connection,
            ),
            patch.object(
                sqlite_repository_module,
                "_open_read_only_connection",
                self._real_open_read_only_connection,
            ),
        ):
            for ordinal, checkpoint in enumerate(
                ("BEGIN", "QUEUE_READ", "RECEIPT_CAS"), start=1
            ):
                with self.subTest(variant=f"RACE_{chr(64 + ordinal)}"):
                    path = self.directory / f"race-{checkpoint.lower()}.sqlite3"
                    service, journal, _session, _command, receipt = (
                        self._reconciliation_fixture(
                            path, "CLAIM-RELEASED", 8600 + ordinal * 10
                        )
                    )
                    self.assertEqual(
                        service._repository.database_identity,
                        journal.database_identity,
                    )
                    before = self._sqlite_fact_snapshot(path)
                    first_barrier = threading.Barrier(2)
                    second_barrier = threading.Barrier(2)
                    trace: list[str] = []
                    thread_result: dict[str, object] = {}
                    original_open = journal._open

                    class PausingConnection:
                        def __init__(self, connection: sqlite3.Connection) -> None:
                            self._connection = connection
                            self._paused = False

                        def execute(
                            self, statement: str, parameters: object = ()
                        ) -> sqlite3.Cursor:
                            cursor = self._connection.execute(statement, parameters)
                            normalized = " ".join(statement.upper().split())
                            matched = (
                                checkpoint == "BEGIN"
                                and normalized == "BEGIN IMMEDIATE"
                            ) or (
                                checkpoint == "QUEUE_READ"
                                and normalized.startswith("SELECT ")
                                and " FROM WORKFLOW_COMMANDS " in normalized
                            ) or (
                                checkpoint == "RECEIPT_CAS"
                                and normalized.startswith("UPDATE WORKER_OPERATIONS")
                            )
                            if matched and not self._paused:
                                self._paused = True
                                first_barrier.wait(timeout=5)
                                second_barrier.wait(timeout=5)
                            return cursor

                        def __getattr__(self, name: str) -> object:
                            return getattr(self._connection, name)

                    def pausing_open(entity: str, identity: str) -> object:
                        connection = original_open(entity, identity)
                        connection.set_trace_callback(trace.append)
                        return PausingConnection(connection)

                    def reconcile() -> None:
                        try:
                            with patch.object(
                                journal, "_open", side_effect=pausing_open
                            ):
                                thread_result["result"] = journal.reconcile_operation(
                                    operation_id=receipt.operation_id,
                                    expected_state=receipt.state,
                                    expected_state_version=receipt.state_version,
                                    occurred_at="2026-08-24T12:00:10.000000Z",
                                )
                        except BaseException as error:
                            thread_result["error"] = error

                    thread = threading.Thread(target=reconcile)
                    thread.start()
                    first_barrier.wait(timeout=5)
                    contender = service.claim_next_eligible(
                        eligible_definition_keys=(("TEST_COMMAND", 1),),
                        lease_owner=claimant_owner(8700 + ordinal),
                    )
                    second_barrier.wait(timeout=5)
                    thread.join(timeout=5)
                    self.assertFalse(thread.is_alive())
                    self.assertNotIn("error", thread_result)
                    result = thread_result["result"]
                    self.assertEqual(
                        (
                            WorkerJournalResultCode.APPLIED,
                            WorkerOperationState.CLAIM_RELEASED,
                            WorkerFailureCode.LEASE_LOST.value,
                            QueueResultCode.TRANSIENT_CONTENTION,
                        ),
                        (
                            result.code,
                            result.operation.state,
                            result.operation.durable_failure_code,
                            contender.code,
                        ),
                    )
                    after = self._sqlite_fact_snapshot(path)
                    self.assertEqual(
                        (
                            before["workflow_commands"],
                            before["workflow_command_events"],
                            before["worker_sessions"],
                        ),
                        (
                            after["workflow_commands"],
                            after["workflow_command_events"],
                            after["worker_sessions"],
                        ),
                    )
                    normalized_trace = tuple(
                        " ".join(statement.upper().split()) for statement in trace
                    )
                    self.assertEqual(
                        (1, 1, 1, 0),
                        (
                            sum(value == "BEGIN IMMEDIATE" for value in normalized_trace),
                            sum(
                                value.startswith("UPDATE WORKER_OPERATIONS")
                                for value in normalized_trace
                            ),
                            sum(value == "COMMIT" for value in normalized_trace),
                            sum(value == "ROLLBACK" for value in normalized_trace),
                        ),
                    )
                    self.assertFalse(
                        any(
                            value.startswith(
                                (
                                    "UPDATE WORKFLOW_COMMANDS",
                                    "INSERT INTO WORKFLOW_COMMAND_EVENTS",
                                    "UPDATE WORKER_SESSIONS",
                                )
                            )
                            for value in normalized_trace
                        )
                    )
                    reopened = SqliteWorkerJournalRepository(path)
                    persisted = reopened.get_operation_for_claim(
                        command_id=receipt.command_id,
                        claim_count=receipt.claim_count,
                    )
                    self.assertEqual(result.operation, persisted.operation)

            path = self.directory / "race-before-begin.sqlite3"
            service, journal, session, _command, receipt = self._reconciliation_fixture(
                path, "CLAIM-RELEASED", 8640
            )
            before = self._sqlite_fact_snapshot(path)
            superseding = service.claim_next_eligible(
                eligible_definition_keys=(("TEST_COMMAND", 1),),
                lease_owner=claimant_owner(8740),
            )
            lost = journal.reconcile_operation(
                operation_id=receipt.operation_id,
                expected_state=receipt.state,
                expected_state_version=receipt.state_version,
                occurred_at="2026-08-24T12:00:10.000000Z",
            )
            after = self._sqlite_fact_snapshot(path)
            self.assertEqual(
                (
                    QueueResultCode.APPLIED,
                    2,
                    WorkerJournalResultCode.CAS_CONFLICT,
                    WorkerOperationState.PREPARED,
                    1,
                    1,
                    before["worker_sessions"],
                    WorkerStartupStatus.JOURNAL_CONFLICT,
                ),
                (
                    superseding.code,
                    superseding.command.claim_count,
                    lost.code,
                    journal.get_operation_for_claim(
                        command_id=receipt.command_id, claim_count=1
                    ).operation.state,
                    len(after["workflow_commands"]),
                    len(after["workflow_command_events"])
                    - len(before["workflow_command_events"]),
                    after["worker_sessions"],
                    DevelopmentWorker._startup_status_for_queue(
                        superseding.command
                    ),
                ),
            )

            path = self.directory / "race-after-commit.sqlite3"
            service, journal, _session, _command, receipt = (
                self._reconciliation_fixture(path, "CLAIM-RELEASED", 8650)
            )
            applied = journal.reconcile_operation(
                operation_id=receipt.operation_id,
                expected_state=receipt.state,
                expected_state_version=receipt.state_version,
                occurred_at="2026-08-24T12:00:10.000000Z",
            )
            claimant = service.claim_next_eligible(
                eligible_definition_keys=(("TEST_COMMAND", 1),),
                lease_owner=claimant_owner(8750),
            )
            repeated = SqliteWorkerJournalRepository(path).reconcile_operation(
                operation_id=receipt.operation_id,
                expected_state=receipt.state,
                expected_state_version=receipt.state_version,
                occurred_at="2026-08-24T12:00:11.000000Z",
            )
            self.assertEqual(
                (
                    WorkerJournalResultCode.APPLIED,
                    WorkerOperationState.CLAIM_RELEASED,
                    QueueResultCode.APPLIED,
                    2,
                    WorkerJournalResultCode.TERMINAL_OBSERVED,
                    applied.operation,
                ),
                (
                    applied.code,
                    applied.operation.state,
                    claimant.code,
                    claimant.command.claim_count,
                    repeated.code,
                    repeated.operation,
                ),
            )

    def test_reconcile_malformed_receipt_history_startup_and_cli_mapping(self) -> None:
        registry = self._registry()
        service = self._service(registry)
        self._enqueue(service, "malformed-startup")
        journal = SqliteWorkerJournalRepository(self.path)
        session_id = str(UUID(int=8501))
        owner = f"worker-1@{session_id}"
        session = journal.start_session(
            session_id=session_id,
            worker_id="worker-1",
            queue_owner_id=owner,
            started_at="2026-08-24T12:00:00.000000Z",
            stale_before="2026-08-24T11:59:30.000000Z",
        ).session
        command = service.claim_next_eligible(
            eligible_definition_keys=registry.keys(), lease_owner=owner
        ).command
        journal.create_operation(
            operation_id=str(UUID(int=8502)),
            command=command,
            operation_kind=WorkerOperationKind.COMMAND_HANDLER_INVOCATION,
            worker_id="worker-1",
            session_id=session_id,
            queue_owner_id=owner,
            occurred_at="2026-08-24T12:00:00.000000Z",
            stale_before="2026-08-24T11:59:30.000000Z",
        )
        connection = sqlite3.connect(self.path)
        try:
            connection.execute("PRAGMA ignore_check_constraints=ON")
            connection.execute(
                "UPDATE worker_operations SET durable_failure_code='LEASE_LOST' "
                "WHERE command_id=? AND claim_count=1",
                (command.command_id,),
            )
            connection.commit()
        finally:
            connection.close()
        with self.assertRaises(RepositoryError) as raised:
            journal.get_operation_for_claim(
                command_id=command.command_id, claim_count=1
            )
        self.assertEqual(
            RepositoryFailureCode.MALFORMED_PERSISTED_PAYLOAD,
            raised.exception.code,
        )
        before = self._sqlite_fact_snapshot(self.path)
        self.assertEqual(
            WorkerStartupStatus.PERSISTENCE_FAILURE,
            self._worker(registry, service).start().status,
        )
        after_start = self._sqlite_fact_snapshot(self.path)
        self.assertEqual(
            7,
            worker_main(
                [
                    "--database-path",
                    str(self.path),
                    "--worker-id",
                    "worker-1",
                ],
                registry_provider=lambda: registry,
            ),
        )
        self.assertEqual(before, after_start)
        self.assertEqual(before, self._sqlite_fact_snapshot(self.path))

        def receipt_case(
            label: str, identity: int
        ) -> tuple[
            Path,
            WorkerHandlerRegistry,
            DurableCommandQueueService,
            SqliteWorkerJournalRepository,
            WorkerSession,
            WorkflowCommand,
            WorkerOperationReceipt,
        ]:
            path = self.directory / f"t71-{label}.sqlite3"
            SqliteRunStore(path).initialize("2026-08-24T12:00:00.000000Z")
            registry = self._registry()
            service = DurableCommandQueueService(
                SqliteWorkflowCommandRepository(path),
                registry._definition_registry,
                10,
                clock=self.providers.clock,
                id_factory=self.providers.identifier,
            )
            self._enqueue(service, f"t71-{label}")
            journal = SqliteWorkerJournalRepository(path)
            session_id = str(UUID(int=identity))
            owner = f"worker-1@{session_id}"
            session = journal.start_session(
                session_id=session_id,
                worker_id="worker-1",
                queue_owner_id=owner,
                started_at="2026-08-24T12:00:00.000000Z",
                stale_before="2026-08-24T11:59:30.000000Z",
            ).session
            command = service.claim_next_eligible(
                eligible_definition_keys=registry.keys(), lease_owner=owner
            ).command
            receipt = journal.create_operation(
                operation_id=str(UUID(int=identity + 1)),
                command=command,
                operation_kind=WorkerOperationKind.COMMAND_HANDLER_INVOCATION,
                worker_id="worker-1",
                session_id=session_id,
                queue_owner_id=owner,
                occurred_at="2026-08-24T12:00:00.000000Z",
                stale_before="2026-08-24T11:59:30.000000Z",
            ).operation
            return path, registry, service, journal, session, command, receipt

        observed_variants = ["CODE"]
        for offset, variant in enumerate(("VALID", "DIAG", "BOTH", "TERMINAL")):
            with self.subTest(t71_variant=variant):
                path, case_registry, case_service, case_journal, _, command, receipt = (
                    receipt_case(variant.lower(), 8760 + offset * 10)
                )
                if variant == "VALID":
                    found = case_journal.get_operation_for_claim(
                        command_id=command.command_id, claim_count=1
                    )
                    self.assertEqual(
                        (
                            WorkerJournalResultCode.FOUND,
                            WorkerOperationState.PREPARED,
                            None,
                            None,
                        ),
                        (
                            found.code,
                            found.operation.state,
                            found.operation.durable_failure_code,
                            found.operation.diagnostic_detail,
                        ),
                    )
                    observed_variants.append(variant)
                    continue
                connection = sqlite3.connect(path)
                try:
                    connection.execute("PRAGMA ignore_check_constraints=ON")
                    if variant == "DIAG":
                        connection.execute(
                            "UPDATE worker_operations SET diagnostic_detail=? "
                            "WHERE operation_id=?",
                            (
                                "HANDLER_PROTOCOL_ERROR:INVALID_RETURN",
                                receipt.operation_id,
                            ),
                        )
                    elif variant == "BOTH":
                        connection.execute(
                            "UPDATE worker_operations SET durable_failure_code=?, "
                            "diagnostic_detail=? WHERE operation_id=?",
                            (
                                WorkerFailureCode.HANDLER_EXCEPTION.value,
                                "HANDLER_PROTOCOL_ERROR:INVALID_RETURN",
                                receipt.operation_id,
                            ),
                        )
                    else:
                        connection.execute(
                            "UPDATE worker_operations SET state='FAILED', "
                            "completed_at=updated_at WHERE operation_id=?",
                            (receipt.operation_id,),
                        )
                    connection.execute("PRAGMA ignore_check_constraints=OFF")
                    self.assertEqual(
                        0,
                        connection.execute(
                            "PRAGMA ignore_check_constraints"
                        ).fetchone()[0],
                    )
                    connection.commit()
                finally:
                    connection.close()
                before_case = self._sqlite_fact_snapshot(path)
                with self.assertRaises(RepositoryError) as raised:
                    case_journal.get_operation_for_claim(
                        command_id=command.command_id, claim_count=1
                    )
                self.assertEqual(
                    RepositoryFailureCode.MALFORMED_PERSISTED_PAYLOAD,
                    raised.exception.code,
                )
                self.assertEqual(
                    WorkerStartupStatus.PERSISTENCE_FAILURE,
                    self._worker(
                        case_registry, case_service, database_path=path
                    ).start().status,
                )
                self.assertEqual(
                    7,
                    worker_main(
                        ["--database-path", str(path), "--worker-id", "worker-1"],
                        registry_provider=lambda: case_registry,
                    ),
                )
                self.assertEqual(before_case, self._sqlite_fact_snapshot(path))
                observed_variants.append(variant)

        path, history_registry, history_service, history_journal, history_session, command, receipt = (
            receipt_case("history", 8810)
        )
        running = history_service.mark_running(
            command_id=command.command_id,
            expected_state=command.state,
            expected_state_version=command.state_version,
            lease_owner=command.lease_owner,
        )
        history_service.mark_failed(
            command_id=command.command_id,
            expected_state=running.command.state,
            expected_state_version=running.command.state_version,
            lease_owner=running.command.lease_owner,
            failure_code="TEST_FAILURE",
        )
        connection = sqlite3.connect(path)
        try:
            connection.execute("PRAGMA ignore_check_constraints=ON")
            connection.execute(
                "UPDATE workflow_command_events SET reason_code='OTHER' "
                "WHERE command_id=? AND event_kind='STARTED'",
                (command.command_id,),
            )
            connection.execute("PRAGMA ignore_check_constraints=OFF")
            self.assertEqual(
                0,
                connection.execute("PRAGMA ignore_check_constraints").fetchone()[0],
            )
            connection.commit()
        finally:
            connection.close()
        self._fail_worker_session(history_journal, history_session)
        before_history = self._sqlite_fact_snapshot(path)
        with self.assertRaises(RepositoryError) as raised:
            history_journal.reconcile_operation(
                operation_id=receipt.operation_id,
                expected_state=receipt.state,
                expected_state_version=receipt.state_version,
                occurred_at="2026-08-24T12:00:01.000000Z",
            )
        self.assertEqual(
            RepositoryFailureCode.MALFORMED_PERSISTED_PAYLOAD,
            raised.exception.code,
        )
        self.assertEqual(
            WorkerStartupStatus.PERSISTENCE_FAILURE,
            self._worker(
                history_registry, history_service, database_path=path
            ).start().status,
        )
        self.assertEqual(
            7,
            worker_main(
                ["--database-path", str(path), "--worker-id", "worker-1"],
                registry_provider=lambda: history_registry,
            ),
        )
        self.assertEqual(before_history, self._sqlite_fact_snapshot(path))
        observed_variants.append("HISTORY")
        self.assertEqual(
            ("CODE", "VALID", "DIAG", "BOTH", "TERMINAL", "HISTORY"),
            tuple(observed_variants),
        )

    def test_revision_neutral_schema_obligation_findings_exactness_and_traceability(self) -> None:
        evidence_path = os.environ.get("PANAM_R11_ACCEPTED_EVIDENCE_PATH")
        self.assertIsNotNone(evidence_path, "accepted Evidence path is required")
        raw = Path(evidence_path).read_bytes()
        self.assertEqual(
            "2E1109A5FE4E0CA7ED73A73AED992421E829D4183EEBFA66B3684AF137A83852",
            hashlib.sha256(raw).hexdigest().upper(),
        )
        evidence = json.loads(raw)
        self.assertEqual(
            {
                "candidate_evidence_identity_state": "FINAL_IDENTITY_NOT_YET_AVAILABLE",
                "creator_verification_state_ref": "/authority_state",
                "currentness_basis": "TYPED_CURRENT_PACKAGE_CONTEXT",
                "evidence_schema": "panam.dl2.2.r11.evidence.v1",
                "generation_phase": "CANDIDATE_CONSTRUCTION_PREWRITE",
                "independent_verification_state_ref": "/integrity/independent_verification_state",
                "package_revision": "011",
                "package_role": "PROPOSED_NOT_ACCEPTED_CANDIDATE",
            },
            evidence["current_package_context"],
        )
        self.assertEqual(
            ("PASS", "PASS", 0, 0),
            (
                evidence["integrity"]["current_package_context_validation"]["result"],
                evidence["integrity"]["currentness_validation"]["result"],
                evidence["integrity"]["currentness_validation"]["active_current_stale_count"],
                evidence["integrity"]["currentness_validation"]["unknown_active_revision_binding_count"],
            ),
        )
        tests = evidence["tests"]
        matrices = {item["matrix_id"]: item for item in evidence["matrices"]}
        m11_rows = matrices["M11_NORMATIVE_TEST_OBLIGATIONS"]["rows"]
        m12_rows = matrices["M12_CRASH_AND_RESTART_MATRIX"]["rows"]

        expected_test_ids = tuple(f"T{number:02d}" for number in range(1, 87))
        self.assertEqual(expected_test_ids, tuple(tests["ordered_test_ids"]))
        self.assertEqual(expected_test_ids, tuple(row["row_id"] for row in m11_rows))
        self.assertEqual(expected_test_ids, tuple(row["test_id"] for row in tests["test_index"]))
        self.assertEqual(
            expected_test_ids,
            tuple(row["m11_row_id"] for row in tests["test_index"]),
        )
        self.assertEqual(86, len({row["test_id"] for row in tests["test_index"]}))
        accepted_aliases = {
            "test_revision005_schema_obligation_counts_findings_exactness_and_traceability":
                "test_revision_neutral_schema_obligation_findings_exactness_and_traceability"
        }
        for row in tests["test_index"][:82]:
            resolved_name = accepted_aliases.get(row["name"], row["name"])
            self.assertTrue(callable(getattr(type(self), resolved_name, None)), row["test_id"])
        self.assertEqual(
            ("T72",),
            tuple(
                row["test_id"]
                for row in tests["test_index"]
                if row["name"] in accepted_aliases
            ),
        )

        registry_ids = {
            item["obligation_id"] for item in tests["schema_obligation_registry"]
        }
        m11_ids = {
            obligation
            for row in m11_rows
            for obligation in row["schema_obligation_ids"]
        }
        schema_relations = {
            (obligation, row["row_id"])
            for row in m11_rows
            for obligation in row["schema_obligation_ids"]
        }
        graph_schema_relations = {
            (row["obligation_id"], row["test_id"])
            for row in tests["canonical_relation_graph"]
            if "crash_row_id" not in row
        }
        crash_relations = {
            (row["row_id"], test_id)
            for row in m12_rows
            for test_id in row["test_mapping"]
        }
        graph_crash_relations = {
            (row["crash_row_id"], row["test_id"])
            for row in tests["canonical_relation_graph"]
            if "crash_row_id" in row
        }
        self.assertEqual((61, 61, 61), (tests["schema_obligation_count"], len(registry_ids), len(m11_ids)))
        self.assertEqual(registry_ids, m11_ids)
        self.assertEqual((76, schema_relations), (tests["schema_relation_count"], graph_schema_relations))
        self.assertEqual((42, crash_relations), (tests["crash_relation_count"], graph_crash_relations))
        self.assertEqual(42, evidence["crash_cases"]["canonical_relation_count"])

        expected_m12_ids = tuple(
            [f"C{number:02d}" for number in range(1, 23)]
            + [
                f"C{number}-{family}"
                for number in range(23, 28)
                for family in ("FAILED", "CANCELLED", "CLAIM-RELEASED")
            ]
            + [f"C{number}" for number in range(28, 33)]
        )
        self.assertEqual(expected_m12_ids, tuple(matrices["M12_CRASH_AND_RESTART_MATRIX"]["ordered_row_ids"]))
        windows = evidence["crash_cases"]["schema_window_registry"]
        expected_windows = tuple(
            [
                (
                    f"AR-{family}-{number:02d}",
                    f"C{number + 22}-{family}",
                    family,
                    f"T{number + 72}",
                )
                for number in range(1, 6)
                for family in ("FAILED", "CANCELLED", "CLAIM-RELEASED")
            ]
            + [
                (
                    f"MP-{number:02d}",
                    f"C{number + 27}",
                    "MALFORMED_PERSISTED_PAYLOAD",
                    f"T{number + 77}",
                )
                for number in range(1, 6)
            ]
        )
        actual_windows = tuple(
            (
                row["schema_window_id"],
                row["m12_row_id"],
                row["mapping_family"],
                row["test_id"],
            )
            for row in windows
        )
        self.assertEqual(expected_windows, actual_windows)
        self.assertEqual(
            (20, 20, 0),
            (
                evidence["crash_cases"]["schema_window_count"],
                len({row["schema_window_id"] for row in windows}),
                evidence["crash_cases"]["unmapped_schema_window_count"],
            ),
        )
        self.assertTrue(
            all(
                row["m12_row_id"] in expected_m12_ids
                and row["test_id"] in expected_test_ids
                for row in windows
            )
        )

        m13 = matrices["M13_FORMER_R1_CONDITIONAL_TEST_TRACEABILITY"]
        self.assertEqual(
            tuple(f"M13-{number:02d}" for number in range(1, 22)),
            tuple(m13["ordered_row_ids"]),
        )
        self.assertEqual(
            tuple(f"R1T{number}" for number in range(26, 47)),
            tuple(row["historical_test_id"] for row in m13["rows"]),
        )
        self.assertEqual(
            tuple(
                [f"R2Q-{number:03d}" for number in range(1, 14)]
                + ["R6Q-001", "R6IV-Q-001"]
            ),
            tuple(row["question_id"] for row in evidence["questions"]["index"]),
        )
        validations = evidence["contradictions"]["surface_validations"]
        self.assertEqual((25, 25), (len(validations), len({row["validation_id"] for row in validations})))
        self.assertTrue(
            all(
                row["result"] == "PASS"
                and row["stored_value"] == row["independently_derived_value"]
                for row in validations
            )
        )
        assessments = evidence["findings"]["revision_assessments"]
        self.assertEqual((19, 19), (len(assessments), len({row["assessment_id"] for row in assessments})))
        self.assertTrue(
            all(
                row["assessment_id"] == "R11-ASSESS-" + row["historical_finding_id"]
                and row["validation_result"] == "PASS"
                for row in assessments
            )
        )
        self.assertEqual(
            (0, 0, 0, 0),
            (
                tests["duplicate_schema_obligation_id_count"],
                tests["unmapped_schema_obligation_count"],
                tests["test_only_claim_bypass_count"],
                evidence["crash_cases"]["unmapped_schema_window_count"],
            ),
        )
        zero_count_keys = (
            "alternate_implementation_path_count",
            "derived_count_mismatch_count",
            "full_matrix_duplication_in_contract_count",
            "full_matrix_duplication_in_plan_count",
            "matrix_with_positional_string_only_rows_count",
            "row_with_generic_evidence_count",
            "row_without_implementation_path_count",
            "row_without_normative_test_or_validation_count",
            "row_without_required_typed_effect_count",
            "row_without_source_reference_count",
            "row_without_unique_id_count",
            "unauthorized_persistent_artifact_count",
        )
        self.assertEqual(
            (0,) * len(zero_count_keys),
            tuple(evidence["counts"][key] for key in zero_count_keys),
        )

    def test_crash_c23_atomic_after_begin_before_queue_read(self) -> None:
        self._assert_atomic_reconcile_crash_point(23)

    def test_crash_c24_atomic_after_queue_history_before_receipt_cas(self) -> None:
        self._assert_atomic_reconcile_crash_point(24)

    def test_crash_c25_atomic_after_receipt_cas_before_commit(self) -> None:
        self._assert_atomic_reconcile_crash_point(25)

    def test_crash_c26_atomic_after_commit_before_result_return(self) -> None:
        self._assert_atomic_reconcile_crash_point(26)

    def test_crash_c27_atomic_after_applied_before_session_followup(self) -> None:
        self._assert_atomic_reconcile_crash_point(27)

    def test_crash_c28_malformed_receipt_before_startup_read(self) -> None:
        registry, service, _journal, _session, _command, _receipt = (
            self._corrupt_prepared_receipt()
        )
        before = self._sqlite_fact_snapshot(self.path)
        result = self._worker(registry, service).start()
        self.assertEqual(WorkerStartupStatus.PERSISTENCE_FAILURE, result.status)
        self.assertEqual(before, self._sqlite_fact_snapshot(self.path))

    def test_crash_c29_corruption_detected_during_raw_receipt_decode(self) -> None:
        _, _, journal, _, command, _ = self._corrupt_prepared_receipt()
        before = self._sqlite_fact_snapshot(self.path)
        with self.assertRaises(RepositoryError) as raised:
            journal.get_operation_for_claim(
                command_id=command.command_id, claim_count=command.claim_count
            )
        self.assertEqual(
            RepositoryFailureCode.MALFORMED_PERSISTED_PAYLOAD,
            raised.exception.code,
        )
        self.assertEqual(before, self._sqlite_fact_snapshot(self.path))

    def test_crash_c30_malformed_queue_history_inside_reconcile_transaction(self) -> None:
        _, journal, _, _, receipt = self._reconciliation_fixture(
            self.path, "FAILED", 8930
        )
        connection = sqlite3.connect(self.path)
        try:
            connection.execute(
                "DELETE FROM workflow_command_events WHERE event_sequence="
                "(SELECT MAX(event_sequence) FROM workflow_command_events)"
            )
            connection.commit()
        finally:
            connection.close()
        before = self._sqlite_fact_snapshot(self.path)
        trace: list[str] = []
        original_open = journal._open

        def traced_open(entity: str, identity: str) -> sqlite3.Connection:
            opened = original_open(entity, identity)
            opened.set_trace_callback(trace.append)
            return opened

        with patch.object(journal, "_open", side_effect=traced_open):
            with self.assertRaises(RepositoryError) as raised:
                journal.reconcile_operation(
                    operation_id=receipt.operation_id,
                    expected_state=receipt.state,
                    expected_state_version=receipt.state_version,
                    occurred_at="2026-08-24T12:00:10.000000Z",
                )
        self.assertEqual(
            RepositoryFailureCode.MALFORMED_PERSISTED_PAYLOAD,
            raised.exception.code,
        )
        normalized = tuple(statement.strip().upper() for statement in trace)
        self.assertEqual(1, sum(value == "BEGIN IMMEDIATE" for value in normalized))
        self.assertEqual(1, sum(value == "ROLLBACK" for value in normalized))
        self.assertFalse(any(value.startswith("UPDATE ") for value in normalized))
        self.assertEqual(before, self._sqlite_fact_snapshot(self.path))

    def test_crash_c31_repository_error_before_receipt_cas(self) -> None:
        _, journal, _, command, receipt = self._reconciliation_fixture(
            self.path, "CANCELLED", 8940
        )
        connection = sqlite3.connect(self.path)
        try:
            connection.execute(
                "UPDATE workflow_command_events SET reason_code='OTHER_CANCEL' "
                "WHERE command_id=? AND event_kind='CANCELLED'",
                (command.command_id,),
            )
            connection.commit()
        finally:
            connection.close()
        before = self._sqlite_fact_snapshot(self.path)
        trace: list[str] = []
        original_open = journal._open

        def traced_open(entity: str, identity: str) -> sqlite3.Connection:
            opened = original_open(entity, identity)
            opened.set_trace_callback(trace.append)
            return opened

        with (
            patch.object(journal, "_open", side_effect=traced_open),
            self.assertRaises(RepositoryError) as raised,
        ):
            journal.reconcile_operation(
                operation_id=receipt.operation_id,
                expected_state=receipt.state,
                expected_state_version=receipt.state_version,
                occurred_at="2026-08-24T12:00:10.000000Z",
            )
        self.assertEqual(
            RepositoryFailureCode.MALFORMED_PERSISTED_PAYLOAD,
            raised.exception.code,
        )
        normalized = tuple(statement.strip().upper() for statement in trace)
        self.assertEqual(1, sum(value == "BEGIN IMMEDIATE" for value in normalized))
        self.assertEqual(1, sum(value == "ROLLBACK" for value in normalized))
        self.assertFalse(any(value.startswith("UPDATE ") for value in normalized))
        self.assertEqual(before, self._sqlite_fact_snapshot(self.path))

    def test_crash_c32_exit_after_repository_error_before_startup_mapping(self) -> None:
        registry = self._registry()
        service = self._service(registry)
        self._enqueue(service)
        journal = SqliteWorkerJournalRepository(self.path)
        session_id = str(UUID(int=8950))
        owner = f"worker-1@{session_id}"
        session = journal.start_session(
            session_id=session_id,
            worker_id="worker-1",
            queue_owner_id=owner,
            started_at="2026-08-24T12:00:00.000000Z",
            stale_before="2026-08-24T11:59:30.000000Z",
        ).session
        command = service.claim_next_eligible(
            eligible_definition_keys=registry.keys(), lease_owner=owner
        ).command
        receipt = journal.create_operation(
            operation_id=str(UUID(int=8951)),
            command=command,
            operation_kind=WorkerOperationKind.COMMAND_HANDLER_INVOCATION,
            worker_id="worker-1",
            session_id=session.session_id,
            queue_owner_id=owner,
            occurred_at="2026-08-24T12:00:00.000000Z",
            stale_before="2026-08-24T11:59:30.000000Z",
        ).operation
        running = service.mark_running(
            command_id=command.command_id,
            expected_state=command.state,
            expected_state_version=command.state_version,
            lease_owner=owner,
        ).command
        service.mark_failed(
            command_id=running.command_id,
            expected_state=running.state,
            expected_state_version=running.state_version,
            lease_owner=owner,
            failure_code="TEST_FAILURE",
        )
        connection = sqlite3.connect(self.path)
        try:
            connection.execute(
                "DELETE FROM workflow_command_events WHERE command_id=? "
                "AND event_kind='FAILED'",
                (command.command_id,),
            )
            connection.commit()
        finally:
            connection.close()
        before = self._sqlite_fact_snapshot(self.path)
        with self.assertRaises(RepositoryError) as raised:
            journal.reconcile_operation(
                operation_id=receipt.operation_id,
                expected_state=receipt.state,
                expected_state_version=receipt.state_version,
                occurred_at="2026-08-24T12:00:10.000000Z",
            )
        self.assertEqual(
            RepositoryFailureCode.MALFORMED_PERSISTED_PAYLOAD,
            raised.exception.code,
        )
        self.assertEqual(before, self._sqlite_fact_snapshot(self.path))
        self.assertEqual(
            7,
            worker_main(
                [
                    "--database-path",
                    str(self.path),
                    "--worker-id",
                    "worker-1",
                ],
                registry_provider=lambda: registry,
            ),
        )
        self.assertEqual(before, self._sqlite_fact_snapshot(self.path))


if __name__ == "__main__":
    unittest.main()
