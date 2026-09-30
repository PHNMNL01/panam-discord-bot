"""DL-2.6 deterministic SQLite and native Windows tests; fixture effects only."""
from contextlib import closing
from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from uuid import uuid4

from panam_development_loop import (
    CommandDefinition, CommandDefinitionRegistry, DurableCommandQueueService,
    SqliteWorkflowCommandRepository, SqliteWorkerJournalRepository,
    LockResource, LockOwner, LockConfiguration, LockState, ProjectLockService,
)
from panam_development_loop.models import PhaseContract, ProjectPolicy
from panam_development_loop.command_queue import _format_queue_timestamp
from panam_development_loop.git_inspection_models import GitObjectIdentity
from panam_development_loop.phase_branch_creation_models import (
    ACTION, EffectAuthorization, PhaseBranchIntent, OperationRejected, canonical,
)
from panam_development_loop.phase_branch_creation import PhaseBranchCreation
from panam_development_loop.sqlite_phase_branch_operations import SqlitePhaseBranchOperations
from panam_development_loop.sqlite_repositories import SqlitePhaseContractRepository
from panam_development_loop.sqlite_migrations import (
    PRODUCTION_MIGRATIONS, Migration, MigrationError, apply_migrations, initialize_database,
)
from panam_development_loop import git_phase_branch_adapter as ga


class SyntheticAuthority:
    def __init__(self, authorization):
        self.accepted = authorization.canonical_json()
        self.valid = True
        self.calls = 0

    def is_current(self, authorization, now):
        self.calls += 1
        return self.valid and authorization.canonical_json() == self.accepted


class SimulatedCrash(BaseException):
    pass


class Fixture(unittest.TestCase):
    def setUp(self):
        self.assertEqual("nt", os.name, "native Windows tests must not be skipped")
        self.temp = tempfile.TemporaryDirectory(prefix="dl26-owned-")
        self.base = Path(self.temp.name).resolve()
        self.assertNotIn(self.base, (Path(r"C:\Panam_APP"), Path(r"C:\Vault_work")))
        self.addCleanup(self.cleanup)
        self.root = self.base / "repo"
        self.root.mkdir()
        self.git("init", "-b", "main")
        self.git("config", "user.name", "DL26 Synthetic")
        self.git("config", "user.email", "synthetic@example.invalid")
        self.git("config", "core.autocrlf", "false")
        (self.root / "tracked.txt").write_bytes(b"base\n")
        (self.root / "excluded.txt").write_bytes(b"synthetic exclusion\n")
        self.git("add", "--", "tracked.txt", "excluded.txt")
        self.git("commit", "-m", "synthetic baseline")
        self.oid = self.git("rev-parse", "HEAD").decode().strip()
        self.db = self.base / "test.sqlite3"
        initialize_database(self.db, "fixture")
        self.now = datetime(2026, 9, 30, 10, tzinfo=timezone.utc)
        self.phase = PhaseContract("panam", "phase-test", "1")
        SqlitePhaseContractRepository(self.db).create(self.phase)
        self.sql("INSERT INTO project_policies VALUES(?,?,?)", ("panam", "1", str(self.root)))
        session = str(uuid4())
        queue_owner = "worker-test@" + session
        stamp = _format_queue_timestamp(self.now)
        result = SqliteWorkerJournalRepository(self.db).start_session(session_id=session,
            worker_id="worker-test", queue_owner_id=queue_owner, started_at=stamp,
            stale_before=_format_queue_timestamp(self.now-timedelta(seconds=30)))
        self.assertEqual("APPLIED", result.code.value)
        self.queue = DurableCommandQueueService(SqliteWorkflowCommandRepository(self.db),
            CommandDefinitionRegistry((CommandDefinition(ACTION, 1, (), (), (), lambda p: '{}'),)),
            1000, clock=lambda: self.now, id_factory=lambda: str(uuid4()))
        enqueued = self.queue.enqueue(project_id="panam", phase_id=self.phase.phase_id,
            command_kind=ACTION, command_schema_version=1, payload={}, idempotency_key="create", actor_id="test")
        claimed = self.queue.claim_next(lease_owner=queue_owner).command
        self.assertEqual(enqueued.command.command_id, claimed.command_id)
        self.command = self.queue.mark_running(command_id=claimed.command_id,
            expected_state=claimed.state, expected_state_version=claimed.state_version,
            lease_owner=queue_owner).command
        self.owner = LockOwner("worker-test", session, queue_owner, self.command.command_id, self.command.claim_count)
        self.intent = PhaseBranchIntent(str(uuid4()), "HUMAN-SYNTHETIC-DL26", self.phase,
            ProjectPolicy("panam", "1", str(self.root)), "refs/heads/main",
            GitObjectIdentity("sha1", "commit", self.oid), "refs/heads/phase/test", self.owner,
            self.command.command_schema_version, self.command.state_version, self.command.intent_digest,
            ("excluded.txt",))
        self.authorization = EffectAuthorization(self.intent, self.now-timedelta(seconds=1), self.now+timedelta(minutes=5))
        self.provider = SyntheticAuthority(self.authorization)
        self.config = LockConfiguration(lock_lease_seconds=120, heartbeat_stale_after_seconds=90.0)
        self.operations = SqlitePhaseBranchOperations(self.db, clock=lambda: self.now, configuration=self.config)
        self.locks = ProjectLockService(self.operations.locks, clock=lambda: self.now, configuration=self.config)
        self.grant = self.locks.acquire(LockResource("panam", str(self.root), self.intent.target_ref),
                                        self.owner, str(uuid4())).grant
        self.service = PhaseBranchCreation(self.operations, self.provider, clock=lambda: self.now)

    def cleanup(self):
        # TemporaryDirectory is created by this fixture, never supplied by a caller.
        if Path(self.temp.name).resolve() != self.base or not self.base.name.startswith("dl26-owned-"):
            raise AssertionError("fixture ownership lost")
        self.temp.cleanup()

    def git(self, *arguments, expected=0):
        self.assertTrue(self.root.resolve().is_relative_to(self.base))
        env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull, GIT_TERMINAL_PROMPT="0",
                   GIT_OPTIONAL_LOCKS="0", GIT_ALLOW_PROTOCOL="", GIT_CONFIG_SYSTEM=os.devnull)
        result = subprocess.run([ga._GIT, *arguments], cwd=self.root, env=env,
                                capture_output=True, shell=False, timeout=10)
        self.assertEqual(expected, result.returncode, (arguments, result.stdout, result.stderr))
        return result.stdout

    def sql(self, query, values=()):
        with closing(sqlite3.connect(self.db)) as c, c:
            return c.execute(query, values).fetchall()

    def create(self, authorization=None, grant=None):
        return self.service.create(authorization or self.authorization, grant or self.grant)

    def prepare_reserved(self):
        pre = self.service.adapter.observe(self.intent)
        self.assertTrue(self.operations.prepare(self.authorization, self.grant, pre))
        result = self.locks.reserve_effect_window(self.grant.resource, self.owner, self.grant.acquisition_id,
                                                  self.grant.fencing_token, self.grant.revision)
        self.operations.reserved(self.authorization, result.grant)
        return pre, result.grant

    def snapshot(self):
        return {p.relative_to(self.root).as_posix(): p.read_bytes()
                for p in self.root.rglob("*") if p.is_file()}


class BehaviorTest(Fixture):
    def test_success_preserves_head_index_existing_refs_config_and_worktree(self):
        before = self.snapshot()
        result = self.create()
        self.assertEqual("VERIFIED_SUCCESS", result.code, result)
        self.assertEqual(self.oid, self.git("rev-parse", self.intent.target_ref).decode().strip())
        after = self.snapshot()
        self.assertEqual({".git/refs/heads/phase/test", ".git/logs/refs/heads/phase/test"}, after.keys()-before.keys())
        for path, payload in before.items():
            self.assertEqual(payload, after[path], path)
        row = self.operations.read(self.authorization)
        self.assertEqual(["RESERVATION", "DISPATCH", "OUTCOME", "TERMINAL"], list(row["events"]))
        self.assertEqual("NONE", self.sql("SELECT DISTINCT external_effect_class FROM worker_operations")[0][0]
                         if self.sql("SELECT DISTINCT external_effect_class FROM worker_operations") else "NONE")

    def test_dirty_workspace_permitted(self):
        (self.root / "tracked.txt").write_bytes(b"staged\n")
        self.git("add", "--", "tracked.txt")
        (self.root / "tracked.txt").write_bytes(b"unstaged\n")
        (self.root / "untracked.txt").write_bytes(b"untracked\n")
        (self.root / "excluded.txt").write_bytes(b"excluded dirty\n")
        self.assertEqual("VERIFIED_SUCCESS", self.create().code)
        self.assertEqual(b"unstaged\n", (self.root / "tracked.txt").read_bytes())

    def test_excluded_content_never_opened(self):
        original = Path.open
        excluded = self.root / "excluded.txt"
        def guarded(path, *args, **kwargs):
            if path == excluded:
                raise AssertionError("excluded content opened")
            return original(path, *args, **kwargs)
        with patch.object(Path, "open", guarded):
            self.assertEqual("VERIFIED_SUCCESS", self.create().code)

    def test_missing_authority(self):
        self.assertEqual("NO_DISPATCH", self.service.create(None, self.grant).code)

    def test_missing_provider(self):
        self.service.authority_provider = None
        self.assertEqual("NO_DISPATCH", self.create().code)

    def test_revoked_authority(self):
        self.provider.valid = False
        self.assertEqual("NO_DISPATCH", self.create().code)

    def test_stale_authority(self):
        auth = replace(self.authorization, expires_at=self.now)
        self.provider = self.service.authority_provider = SyntheticAuthority(auth)
        self.assertEqual("NO_DISPATCH", self.create(auth).code)

    def test_mismatched_authority(self):
        auth = replace(self.authorization, intent=replace(self.intent, authority_id="OTHER"))
        self.assertEqual("NO_DISPATCH", self.create(auth).code)

    def test_command_version_drift(self):
        updated = self.queue.renew_lease(command_id=self.command.command_id, expected_state=self.command.state,
            expected_state_version=self.command.state_version, lease_owner=self.owner.queue_owner_id)
        self.assertEqual("APPLIED", updated.code.value)
        self.assertEqual("NO_DISPATCH", self.create().code)

    def test_identity_conflicts_survive_restart(self):
        self.prepare_reserved()
        for intent in (replace(self.intent, effect_id=str(uuid4())), replace(self.intent, target_ref="refs/heads/phase/other"),
                       replace(self.intent, owner=replace(self.owner, claim_count=2))):
            with self.subTest(intent=intent.effect_id):
                auth = replace(self.authorization, intent=intent)
                with self.assertRaises(OperationRejected):
                    self.operations.read(auth)
        self.assertEqual(1, self.sql("SELECT count(*) FROM phase_branch_operations")[0][0])

    def test_wrong_lock_bindings_and_expiry(self):
        for grant in (replace(self.grant, acquisition_id=str(uuid4())), replace(self.grant, fencing_token=2),
                      replace(self.grant, revision=2), replace(self.grant, owner=replace(self.owner, claim_count=2))):
            with self.subTest(grant=grant):
                self.assertEqual("NO_DISPATCH", self.create(grant=grant).code)
        self.now += timedelta(seconds=121)
        self.assertEqual("NO_DISPATCH", self.create().code)

    def test_invalid_target_policy(self):
        for target in ("refs/heads/main", "refs/heads/phase/CON", "refs/heads/phase/NUL.txt",
                       "refs/heads/phase/a./b", "refs/heads/phase/a..b", "refs/heads/phase/a.lock",
                       "refs/heads/phase/ space", "refs/heads/phase/x\\y", "refs/heads/phase/../x"):
            with self.subTest(target=target), self.assertRaises(ValueError):
                replace(self.intent, target_ref=target)

    def test_target_existing_same_oid(self):
        self.git("branch", "phase/test", self.oid)
        self.assertEqual("NO_DISPATCH", self.create().code)
        self.assertEqual([], self.sql("SELECT * FROM phase_branch_operations"))

    def test_target_existing_other_oid(self):
        (self.root / "tracked.txt").write_bytes(b"other\n")
        self.git("add", "--", "tracked.txt")
        self.git("commit", "-m", "other")
        other = self.git("rev-parse", "HEAD").decode().strip()
        self.git("branch", "phase/test", other)
        self.git("update-ref", "refs/heads/main", self.oid)
        self.assertEqual("NO_DISPATCH", self.create().code)

    def test_case_collision(self):
        self.git("branch", "phase/TEST", self.oid)
        self.assertEqual("TARGET_REF_COLLISION", self.service.adapter.observe(self.intent).target_state)

    def test_prefix_collision_parent(self):
        self.git("branch", "phase", self.oid)
        self.assertEqual("TARGET_REF_COLLISION", self.service.adapter.observe(self.intent).target_state)

    def test_prefix_collision_child(self):
        self.git("branch", "phase/test/child", self.oid)
        self.assertEqual("TARGET_REF_COLLISION", self.service.adapter.observe(self.intent).target_state)

    def test_packed_ref_collision(self):
        self.git("branch", "phase/TEST", self.oid)
        self.git("pack-refs", "--all")
        self.assertEqual("NO_DISPATCH", self.create().code)

    def test_broken_loose_ref_is_unresolved(self):
        target = self.root / ".git" / self.intent.target_ref
        target.parent.mkdir(parents=True)
        target.write_bytes(b"broken\n")
        self.assertEqual("NO_DISPATCH", self.create().code)

    def test_symbolic_target_is_unresolved(self):
        self.git("symbolic-ref", self.intent.target_ref, "refs/heads/main")
        self.assertEqual("TARGET_REF_OBSERVATION_FAILED_OR_UNRESOLVED", self.service.adapter.observe(self.intent).target_state)

    def test_detached_source_rejected(self):
        self.git("checkout", "--detach", self.oid)
        self.assertEqual("NO_DISPATCH", self.create().code)

    def test_failed_head_is_not_target_absence_r001(self):
        (self.root / ".git/refs/heads/main").write_bytes(b"broken\n")
        self.assertEqual("NO_DISPATCH", self.create().code)
        self.assertEqual([], self.sql("SELECT * FROM phase_branch_operations"))

    def test_exact_replay_is_read_only(self):
        self.assertEqual("VERIFIED_SUCCESS", self.create().code)
        before = self.db.read_bytes(), self.snapshot()
        with patch.object(self.service.adapter, "launch", side_effect=AssertionError("redispatch")):
            result = self.create()
        self.assertEqual("VERIFIED_SUCCESS", result.code, result)
        self.assertEqual(before, (self.db.read_bytes(), self.snapshot()))

    def test_historical_drift_blocks_replay(self):
        self.assertEqual("VERIFIED_SUCCESS", self.create().code)
        (self.root / "tracked.txt").write_bytes(b"drift\n")
        self.assertEqual("RECONCILIATION_REQUIRED", self.create().code)

    def test_dispatch_uniqueness_and_one_use_permit(self):
        self.prepare_reserved()
        permit = self.operations.consume(self.authorization)
        with self.assertRaises(OperationRejected):
            self.operations.consume(self.authorization)
        permit.take(self.intent)
        with self.assertRaises(OperationRejected):
            self.service.adapter.launch(self.intent, permit)
        self.assertEqual("MAY_HAVE_DISPATCHED_RECONCILIATION_REQUIRED", self.create().code)

    def test_real_concurrent_dispatch_consumers(self):
        self.prepare_reserved()
        barrier = threading.Barrier(2)
        winners, failures = [], []
        def consume():
            barrier.wait(timeout=5)
            try:
                winners.append(self.operations.consume(self.authorization))
            except Exception as error:
                failures.append(type(error).__name__)
        threads = [threading.Thread(target=consume) for _ in range(2)]
        for t in threads: t.start()
        for t in threads: t.join(timeout=10)
        self.assertTrue(all(not t.is_alive() for t in threads))
        self.assertEqual((1, 1), (len(winners), len(failures)))
        self.assertEqual(1, self.sql("SELECT count(*) FROM phase_branch_operation_events WHERE kind='DISPATCH'")[0][0])

    def test_uncertain_commit_never_launches(self):
        import panam_development_loop.sqlite_phase_branch_operations as module
        original = module._open_connection
        commit_evidence = []
        test = self

        class UncertainCommit:
            def __init__(self, connection): self.connection = connection
            def __getattr__(self, key): return getattr(self.connection, key)
            def commit(self):
                pending = self.connection.execute(
                    "SELECT count(*) FROM phase_branch_operation_events WHERE effect_id=? AND kind='DISPATCH'",
                    (test.intent.effect_id,)).fetchone()[0]
                if not pending:
                    return self.connection.commit()
                test.assertTrue(self.connection.in_transaction)
                commit_evidence.append("DISPATCH_COMMIT_ENTERED")
                self.connection.commit()
                test.assertFalse(self.connection.in_transaction)
                # A separate connection proves persistence before acknowledgement is lost.
                with closing(sqlite3.connect(test.db)) as reopened:
                    persisted = reopened.execute(
                        "SELECT count(*) FROM phase_branch_operation_events WHERE effect_id=? AND kind='DISPATCH'",
                        (test.intent.effect_id,)).fetchone()[0]
                test.assertEqual(1, persisted)
                commit_evidence.append("DISPATCH_PERSISTED_ON_REOPEN")
                commit_evidence.append("COMMIT_ACKNOWLEDGEMENT_LOST")
                raise sqlite3.OperationalError("lost dispatch commit acknowledgement")

        with patch.object(module, "_open_connection", side_effect=lambda *args: UncertainCommit(original(*args))), \
                patch.object(module, "_issue_permission", wraps=module._issue_permission) as issue, \
                patch.object(ga.GitPhaseBranchAdapter, "launch", side_effect=AssertionError("must not launch")) as launch:
            result = self.create()
            self.assertEqual("MAY_HAVE_DISPATCHED_RECONCILIATION_REQUIRED", result.code)
            self.assertIn("OperationalError: lost dispatch commit acknowledgement", result.detail)
            self.assertEqual(["DISPATCH_COMMIT_ENTERED", "DISPATCH_PERSISTED_ON_REOPEN",
                              "COMMIT_ACKNOWLEDGEMENT_LOST"], commit_evidence)
            issue.assert_not_called()
            launch.assert_not_called()
            restarted_operations = SqlitePhaseBranchOperations(self.db, clock=lambda: self.now,
                                                               configuration=self.config)
            restarted = PhaseBranchCreation(restarted_operations, self.provider, clock=lambda: self.now)
            row = restarted_operations.read(self.authorization)
            self.assertEqual(["RESERVATION", "DISPATCH"], list(row["events"]))
            before = self.db.read_bytes()
            self.assertEqual("MAY_HAVE_DISPATCHED_RECONCILIATION_REQUIRED",
                             restarted.inspect(self.authorization).code)
            self.assertEqual("MAY_HAVE_DISPATCHED_RECONCILIATION_REQUIRED",
                             restarted.create(self.authorization, self.grant).code)
            with self.assertRaisesRegex(OperationRejected, "dispatch CAS conflict"):
                restarted_operations.consume(self.authorization)
            self.assertEqual(before, self.db.read_bytes())
            issue.assert_not_called()
            launch.assert_not_called()
            self.assertFalse((self.root / ".git" / self.intent.target_ref).exists())
            print("DL26_R002_EVIDENCE " + json.dumps({"commit": commit_evidence,
                "permissions": issue.call_count, "launches": launch.call_count,
                "restart": "MAY_HAVE_DISPATCHED_RECONCILIATION_REQUIRED", "redispatch_rejected": True}))

    def test_authority_lost_after_dispatch_never_launches(self):
        def boundary(name):
            if name == "C5": self.provider.valid = False
        with patch("panam_development_loop.phase_branch_creation._boundary", side_effect=boundary):
            result = self.create()
        self.assertEqual("PROVEN_NO_EFFECT", result.code, result)
        self.assertFalse((self.root / ".git" / self.intent.target_ref).exists())

    def test_post_effect_drift_requires_reconciliation(self):
        def boundary(name):
            if name == "C8": (self.root / "tracked.txt").write_bytes(b"unexpected drift\n")
        with patch("panam_development_loop.phase_branch_creation._boundary", side_effect=boundary):
            self.assertEqual("RECONCILIATION_REQUIRED", self.create().code)
        self.assertEqual(LockState.RECONCILIATION_REQUIRED, self.locks.observe(self.grant.resource).grant.state)

    def test_terminalization_failure_retains_evidence(self):
        with patch.object(self.service.locks, "terminalize_effect_reservation", side_effect=OSError("unavailable")):
            result = self.create()
        self.assertEqual("MAY_HAVE_DISPATCHED_RECONCILIATION_REQUIRED", result.code)
        self.assertIn("OUTCOME", self.operations.read(self.authorization)["events"])
        self.assertEqual(LockState.EFFECT_RESERVED, self.locks.observe(self.grant.resource).grant.state)

    def test_lost_git_result_retains_unknown_and_never_repeats(self):
        original = self.service.adapter.launch
        def lost(intent, permission):
            original(intent, permission)
            raise OSError("lost result after real creation")
        with patch.object(self.service.adapter, "launch", side_effect=lost):
            self.assertEqual("RECONCILIATION_REQUIRED", self.create().code)
        record = self.operations.read(self.authorization)
        self.assertEqual("PROCESS_RESULT_LOST", record["events"]["OUTCOME"]["process"]["reason"])
        with patch.object(self.service.adapter, "launch", side_effect=AssertionError("redispatch")):
            self.assertEqual("MAY_HAVE_DISPATCHED_RECONCILIATION_REQUIRED", self.create().code)

    def test_post_observation_failure_preserves_known_process_evidence(self):
        def boundary(name):
            if name == "C8": (self.root / ".git/refs/heads/main").write_bytes(b"broken\n")
        with patch("panam_development_loop.phase_branch_creation._boundary", side_effect=boundary):
            self.assertEqual("RECONCILIATION_REQUIRED", self.create().code)
        outcome = self.operations.read(self.authorization)["events"]["OUTCOME"]
        self.assertEqual(0, outcome["process"]["exit_code"])
        self.assertIsNone(outcome["post"])
        self.assertIn("TARGET_REF_OBSERVATION_FAILED_OR_UNRESOLVED", outcome["post_error"])

    def test_wrong_existing_commit_source(self):
        auth = replace(self.authorization, intent=replace(self.intent,
            source_commit=GitObjectIdentity("sha1", "commit", "1"*40)))
        self.service.authority_provider = SyntheticAuthority(auth)
        self.assertEqual("NO_DISPATCH", self.create(auth).code)

    def test_predispatch_drift_prevents_consumption(self):
        def boundary(name):
            if name == "C3": (self.root / "tracked.txt").write_bytes(b"drift\n")
        with patch("panam_development_loop.phase_branch_creation._boundary", side_effect=boundary):
            self.assertEqual("NOT_DISPATCHED", self.create().code)
        self.assertNotIn("DISPATCH", self.operations.read(self.authorization)["events"])
        self.assertEqual(LockState.EFFECT_RESERVED, self.locks.observe(self.grant.resource).grant.state)

    def test_unborn_source_never_proves_target_absence(self):
        self.git("checkout", "--orphan", "unborn")
        auth = replace(self.authorization, intent=replace(self.intent, source_ref="refs/heads/unborn"))
        self.service.authority_provider = SyntheticAuthority(auth)
        self.assertEqual("NO_DISPATCH", self.create(auth).code)

    def test_wrong_worker_session_queue_owner_are_rejected(self):
        session = str(uuid4())
        wrong = LockOwner("different", session, "different@"+session, self.owner.command_id, self.owner.claim_count)
        auth = replace(self.authorization, intent=replace(self.intent, owner=wrong))
        self.service.authority_provider = SyntheticAuthority(auth)
        self.assertEqual("NO_DISPATCH", self.create(auth).code)

    def test_windows_alias_in_packed_refs_is_unresolved(self):
        (self.root / ".git/packed-refs").write_text(self.oid+" refs/heads/phase/test.\n", encoding="ascii")
        result = self.create()
        self.assertEqual("NO_DISPATCH", result.code)
        self.assertIn("TARGET_REF_OBSERVATION_FAILED_OR_UNRESOLVED", result.detail)


class CrashTest(Fixture):
    def crash_at(self, boundary):
        def fault(name):
            if name == boundary: raise SimulatedCrash(name)
        with patch("panam_development_loop.phase_branch_creation._boundary", side_effect=fault), self.assertRaises(SimulatedCrash):
            self.create()
        before = self.db.read_bytes()
        with patch.object(self.service.adapter, "launch", side_effect=AssertionError("restart launch")):
            result = self.service.inspect(self.authorization)
        expected = "NOT_DISPATCHED" if int(boundary[1:]) < 5 else "VERIFIED_SUCCESS" if boundary == "C10" else "MAY_HAVE_DISPATCHED_RECONCILIATION_REQUIRED"
        self.assertEqual(expected, result.code, result)
        self.assertEqual(before, self.db.read_bytes())


for _number in range(11):
    setattr(CrashTest, "test_crash_C" + str(_number), lambda self, n=_number: self.crash_at("C" + str(n)))


class NativeProcessTest(Fixture):
    def capture_native(self, code, seconds=3.0, cap=2097152):
        import time
        with patch.object(ga, "_argv", return_value=(sys.executable, "-B", "-c", code)), \
                patch.object(ga, "_PROCESS_SECONDS", seconds), patch.object(ga, "_CAPTURE_MAX", cap):
            return ga._capture(ACTION, self.intent, time.monotonic()+10)

    def assert_dead(self, pid):
        import ctypes
        from ctypes import wintypes
        k = ctypes.WinDLL("kernel32", use_last_error=True)
        k.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        k.OpenProcess.restype = wintypes.HANDLE
        k.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
        k.CloseHandle.argtypes = (wintypes.HANDLE,)
        handle = k.OpenProcess(0x1000, False, pid)
        if handle:
            try:
                code = wintypes.DWORD()
                self.assertTrue(k.GetExitCodeProcess(handle, ctypes.byref(code)))
                self.assertNotEqual(259, code.value)
            finally:
                k.CloseHandle(handle)
        else:
            self.assertEqual(87, ctypes.get_last_error())

    def test_native_timeout_kills_owned_descendant(self):
        evidence = self.capture_native("import subprocess,sys,time; p=subprocess.Popen([sys.executable,'-B','-c','import time;time.sleep(30)']); print(p.pid,flush=True); time.sleep(30)")
        self.assertEqual("COMMAND_TIMEOUT", evidence.primary_reason)
        self.assertTrue(evidence.quiescent)
        self.assertFalse(evidence.complete)
        self.assert_dead(int(evidence.stdout.strip()))

    def test_native_parent_exit_does_not_prove_tree_quiescence(self):
        evidence = self.capture_native("import subprocess,sys; p=subprocess.Popen([sys.executable,'-B','-c','import time;time.sleep(30)']); print(p.pid,flush=True)")
        self.assertFalse(evidence.complete)
        self.assertTrue(evidence.quiescent)
        self.assert_dead(int(evidence.stdout.strip()))

    def test_native_capture_is_bounded_on_both_streams(self):
        for stream in (1, 2):
            with self.subTest(stream=stream):
                evidence = self.capture_native("import os,time; os.write("+str(stream)+",b'x'*200000); time.sleep(30)", cap=4096)
                self.assertEqual("OUTPUT_OVERFLOW", evidence.reason)
                self.assertLessEqual(len(evidence.stdout), 4096)
                self.assertLessEqual(len(evidence.stderr), 4096)
                self.assertTrue(evidence.quiescent)

    def test_native_cleanup_uncertainty_preserves_timeout(self):
        original = ga._Job.active
        def active(job):
            count = original(job)
            if not count: raise OSError("injected accounting uncertainty")
            return count
        with patch.object(ga._Job, "active", active):
            evidence = self.capture_native("import time; time.sleep(30)")
        self.assertEqual("CLEANUP_FAILURE", evidence.reason)
        self.assertEqual("COMMAND_TIMEOUT", evidence.primary_reason)
        self.assertFalse(evidence.quiescent)

    def test_native_launch_failure_can_prove_no_effect(self):
        original = ga._argv
        def argv(operation, intent):
            if operation == ACTION: return (str(self.base / "missing.exe"),)
            return original(operation, intent)
        with patch.object(ga, "_argv", side_effect=argv), patch(
                "panam_development_loop.phase_branch_creation._argv", side_effect=argv):
            result = self.create()
        self.assertEqual("PROVEN_NO_EFFECT", result.code, result)
        self.assertEqual("PROVEN_NO_EFFECT", self.locks.observe(self.grant.resource).grant.terminal_outcome)

    def test_nonzero_git_exit_is_not_proven_no_effect(self):
        def boundary(name):
            if name == "C6": self.git("branch", "phase/test", self.oid)
        with patch("panam_development_loop.phase_branch_creation._boundary", side_effect=boundary):
            result = self.create()
        self.assertEqual("RECONCILIATION_REQUIRED", result.code, result)
        outcome = self.operations.read(self.authorization)["events"]["OUTCOME"]
        self.assertNotEqual(0, outcome["process"]["exit_code"])

    def test_suspend_assignment_failure_never_executes(self):
        original = ga._argv
        def argv(operation, intent):
            if operation == ACTION:
                return (sys.executable, "-B", "-c", "raise SystemExit(23)")
            return original(operation, intent)
        import time
        with patch.object(ga, "_argv", side_effect=argv), patch.object(ga._Job, "assign", side_effect=OSError("job failure")):
            evidence = ga._capture(ACTION, self.intent, time.monotonic()+10)
        self.assertFalse(evidence.may_have_executed)
        self.assertEqual("SUSPENDED_PROCESS_TERMINATED", evidence.cleanup)


class TransactionTest(Fixture):
    def test_populated_v7_coordination_queue_phase_and_session_preserved(self):
        with closing(sqlite3.connect(self.db)) as c, c:
            c.row_factory = sqlite3.Row
            c.execute("PRAGMA foreign_keys=ON")
            c.execute("DROP TABLE phase_branch_operation_events")
            c.execute("DROP TABLE phase_branch_operations")
            c.execute("DELETE FROM schema_migrations WHERE version=8")
            c.commit()
            names = [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
            before = {name: [tuple(r) for r in c.execute('SELECT * FROM '+name)] for name in names}
            apply_migrations(c, "v8", PRODUCTION_MIGRATIONS)
            for name, rows in before.items():
                actual = [tuple(r) for r in c.execute('SELECT * FROM '+name)]
                self.assertEqual(rows, actual[:len(rows)] if name == "schema_migrations" else actual)
        self.assertEqual("VERIFIED_SUCCESS", self.create().code)

    def test_database_rejects_duplicate_dispatch_event(self):
        self.prepare_reserved()
        self.operations.consume(self.authorization)
        with self.assertRaises(sqlite3.IntegrityError):
            self.sql("INSERT INTO phase_branch_operation_events SELECT effect_id,3,kind,payload_json,payload_digest,occurred_at "
                     "FROM phase_branch_operation_events WHERE kind='DISPATCH'")

    def test_malformed_event_order_blocks_dispatch(self):
        self.prepare_reserved()
        self.sql("UPDATE phase_branch_operation_events SET kind='DISPATCH' WHERE kind='RESERVATION'")
        with self.assertRaises(OperationRejected): self.operations.consume(self.authorization)

    def test_dispatch_overlap_holds_real_sqlite_writer(self):
        import panam_development_loop.sqlite_phase_branch_operations as module
        self.prepare_reserved()
        held, release, attempted = threading.Event(), threading.Event(), threading.Event()
        winners, failures = [], []
        evidence, writer = [], []
        original = module._open_connection
        test = self

        class Observed:
            def __init__(self, connection): self.connection = connection
            def __getattr__(self, key): return getattr(self.connection, key)
            def execute(self, sql, *args):
                participant = threading.current_thread()
                competing = sql == "BEGIN IMMEDIATE" and participant is second
                if competing:
                    test.assertTrue(held.is_set())
                    test.assertFalse(release.is_set())
                    test.assertTrue(writer[0].in_transaction)
                    evidence.append("B_BEGIN_IMMEDIATE_ATTEMPT_WHILE_A_HELD")
                    attempted.set()
                try:
                    cursor = self.connection.execute(sql, *args)
                except sqlite3.OperationalError as error:
                    if competing:
                        evidence.append(("B_SQLITE_RESULT", error.sqlite_errorcode, error.sqlite_errorname))
                    raise
                if sql == "BEGIN IMMEDIATE" and participant is first:
                    test.assertTrue(self.connection.in_transaction)
                    writer.append(self.connection)
                    evidence.append("A_BEGIN_IMMEDIATE_HELD")
                    held.set()
                    if not release.wait(10): raise TimeoutError("held writer")
                return cursor

        def opener(*args):
            return Observed(original(*args))

        def consume():
            try: winners.append((threading.current_thread(), self.operations.consume(self.authorization)))
            except Exception as error: failures.append((threading.current_thread(), error))

        first = threading.Thread(target=consume)
        second = threading.Thread(target=consume)
        with patch.object(module, "_open_connection", side_effect=opener), \
                patch.object(module, "_issue_permission", wraps=module._issue_permission) as issue:
            first.start()
            try:
                self.assertTrue(held.wait(5))
                second.start()
                second.join(5)
                self.assertFalse(second.is_alive())
                self.assertTrue(attempted.is_set(), "second participant never reached SQLite BEGIN IMMEDIATE")
                self.assertTrue(writer[0].in_transaction)
                self.assertEqual(0, len(winners))
                self.assertEqual(1, len(failures))
                self.assertIs(second, failures[0][0])
                self.assertIsInstance(failures[0][1], sqlite3.OperationalError)
                self.assertEqual(sqlite3.SQLITE_BUSY, failures[0][1].sqlite_errorcode)
                self.assertEqual("SQLITE_BUSY", failures[0][1].sqlite_errorname)
                issue.assert_not_called()
            finally:
                release.set()
                first.join(10)
                if second.ident is not None:
                    second.join(10)
            self.assertFalse(first.is_alive())
            self.assertFalse(second.is_alive())
            self.assertEqual(1, len(winners))
            self.assertIs(first, winners[0][0])
            issue.assert_called_once_with(self.intent)
            self.assertEqual(["A_BEGIN_IMMEDIATE_HELD", "B_BEGIN_IMMEDIATE_ATTEMPT_WHILE_A_HELD",
                              ("B_SQLITE_RESULT", sqlite3.SQLITE_BUSY, "SQLITE_BUSY")], evidence)
            self.assertEqual(1, self.sql(
                "SELECT count(*) FROM phase_branch_operation_events WHERE kind='DISPATCH'")[0][0])
            print("DL26_R001_EVIDENCE " + json.dumps({"transactions": evidence,
                "permissions": issue.call_count, "dispatch_rows": 1}))

    def test_predispatch_abort_and_dispatch_cannot_both_win(self):
        self.prepare_reserved()
        self.operations.abort_before_dispatch(self.authorization, "aborted before dispatch")
        with self.assertRaises(OperationRejected): self.operations.consume(self.authorization)
        self.assertEqual("NOT_DISPATCHED", self.service.inspect(self.authorization).code)

    def test_dispatch_winner_rejects_predispatch_abort(self):
        self.prepare_reserved()
        permit = self.operations.consume(self.authorization)
        with self.assertRaises(OperationRejected):
            self.operations.abort_before_dispatch(self.authorization, "too late")
        permit.take(self.intent)
        self.assertNotIn("OUTCOME", self.operations.read(self.authorization)["events"])

    def test_no_sqlite_transaction_spans_git_effect(self):
        original = self.service.adapter.launch
        def launch(intent, permission):
            with closing(sqlite3.connect(self.db, timeout=0)) as c:
                c.execute("BEGIN IMMEDIATE")
                c.rollback()
            return original(intent, permission)
        with patch.object(self.service.adapter, "launch", side_effect=launch):
            result = self.create()
        self.assertEqual("VERIFIED_SUCCESS", result.code, result)

    def test_reservation_remains_blocking_after_expiry(self):
        self.prepare_reserved()
        self.now += timedelta(seconds=121)
        with self.assertRaises(OperationRejected): self.operations.consume(self.authorization)
        self.assertEqual(LockState.EFFECT_RESERVED, self.locks.observe(self.grant.resource).grant.state)

    def test_outcome_is_write_once(self):
        self.prepare_reserved()
        self.operations.consume(self.authorization)
        payload = {"outcome": "UNKNOWN", "reason": "result lost"}
        self.operations.outcome(self.authorization, payload)
        with self.assertRaises(OperationRejected): self.operations.outcome(self.authorization, payload)
        self.assertEqual(payload, self.operations.read(self.authorization)["events"]["OUTCOME"])


class MigrationTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="dl26-db-owned-")
        self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name) / "fixture.sqlite3"

    def connection(self):
        c = sqlite3.connect(self.db)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA foreign_keys=ON")
        return c

    def test_fresh_v8(self):
        initialize_database(self.db, "first")
        with closing(self.connection()) as c:
            self.assertEqual(list(range(1, 9)), [r[0] for r in c.execute("SELECT version FROM schema_migrations")])
        before = self.db.read_bytes()
        initialize_database(self.db, "again")
        self.assertEqual(before, self.db.read_bytes())

    def test_populated_v7_upgrade_preserves_data_and_history(self):
        with closing(self.connection()) as c, c:
            apply_migrations(c, "v7", PRODUCTION_MIGRATIONS[:7])
            c.execute("INSERT INTO project_policies VALUES('kept','1','C:\\Synthetic')")
            c.commit()
            names = [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
            before = {name: [tuple(r) for r in c.execute('SELECT * FROM '+name)] for name in names}
            apply_migrations(c, "v8", PRODUCTION_MIGRATIONS)
            for name, rows in before.items():
                self.assertEqual(rows, [tuple(r) for r in c.execute('SELECT * FROM '+name)][:len(rows)])

    def test_migration8_failure_rolls_back_schema_and_ledger(self):
        with closing(self.connection()) as c, c:
            apply_migrations(c, "v7", PRODUCTION_MIGRATIONS[:7])
            before = tuple(c.iterdump())
            broken = PRODUCTION_MIGRATIONS[:7] + (Migration(8, PRODUCTION_MIGRATIONS[7].statements + ('INSERT INTO missing VALUES(1)',)),)
            with self.assertRaises(MigrationError): apply_migrations(c, "v8", broken)
            self.assertEqual(before, tuple(c.iterdump()))

    def test_malformed_v7_base_not_upgraded(self):
        with closing(self.connection()) as c, c:
            apply_migrations(c, "v7", PRODUCTION_MIGRATIONS[:7])
            c.execute("DROP INDEX project_lock_history_idx")
            c.commit()
            before = tuple(c.iterdump())
            with self.assertRaises(MigrationError): apply_migrations(c, "v8", PRODUCTION_MIGRATIONS)
            self.assertEqual(before, tuple(c.iterdump()))

    def test_future_history_rejected(self):
        initialize_database(self.db, "v8")
        with closing(self.connection()) as c, c:
            c.execute("INSERT INTO schema_migrations VALUES(9,'future')")
            c.commit()
            before = tuple(c.iterdump())
            with self.assertRaises(MigrationError): apply_migrations(c, "future", PRODUCTION_MIGRATIONS)
            self.assertEqual(before, tuple(c.iterdump()))

    def test_current_v8_malformed_shape_is_not_accepted_by_initialize(self):
        initialize_database(self.db, "v8")
        with closing(self.connection()) as c, c:
            c.execute("CREATE TABLE unexpected(value TEXT)")
        before = self.db.read_bytes()
        with self.assertRaises(Exception): initialize_database(self.db, "again")
        self.assertEqual(before, self.db.read_bytes())

    def test_historical_migration_payloads_one_through_seven_are_preserved(self):
        import types
        root = Path(__file__).resolve().parent
        old = subprocess.run([ga._GIT, "--no-optional-locks", "show",
            "d4b226b38aa4ae58250323c6c73695f69efd8d87:panam_development_loop/sqlite_migrations.py"],
            cwd=root, capture_output=True, check=True, timeout=10).stdout
        module = types.ModuleType("_dl26_historical_migrations")
        sys.modules[module.__name__] = module
        try:
            exec(compile(old, "accepted_migrations", "exec"), module.__dict__)
            self.assertEqual([(m.version, m.statements) for m in module.PRODUCTION_MIGRATIONS],
                             [(m.version, m.statements) for m in PRODUCTION_MIGRATIONS[:7]])
        finally:
            del sys.modules[module.__name__]


if __name__ == "__main__":
    unittest.main()
