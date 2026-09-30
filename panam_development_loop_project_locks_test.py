"""DL-2.5 isolated behavioral, migration, concurrency and rollback verification."""
from contextlib import closing
import hashlib
import json
import sqlite3
import tempfile
import threading
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone, tzinfo
from pathlib import Path
from unittest.mock import patch
from uuid import UUID

import panam_development_loop as package
import panam_development_loop.sqlite_repositories as sr
from panam_development_loop import (
    CommandDefinition, CommandDefinitionRegistry, DurableCommandQueueService,
    SqliteWorkflowCommandRepository, SqliteWorkerJournalRepository,
    LockResource, LockOwner, LockConfiguration, LockState, LockResultCode as C,
    LockTerminalEvidence, LockTerminalOutcome as T, ProjectLockService,
    SqliteProjectLockRepository,
)
from panam_development_loop.command_queue import _format_queue_timestamp
from panam_development_loop.sqlite_migrations import (
    PRODUCTION_MIGRATIONS, Migration, MigrationError, apply_migrations, initialize_database,
)


def uid(n):
    return str(UUID(int=n))


class LockFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='dl25-')
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'locks.sqlite3'
        initialize_database(self.path, 'fixture')
        self.now = datetime(2026, 9, 25, 10, tzinfo=timezone.utc)
        self.next_id = 100
        self.sql("INSERT INTO project_policies VALUES('panam','1','C:\\Synthetic\\Panam')")
        self.resource = LockResource('panam', r'C:\Synthetic\Panam')
        self.branch = replace(self.resource, branch_ref='refs/heads/phase/Test')
        self.repo = SqliteProjectLockRepository(self.path)
        self.service = ProjectLockService(self.repo, clock=lambda: self.now)
        self.a = self.owner('worker-a', 1)
        self.b = self.owner('worker-b', 2)

    def identifier(self):
        self.next_id += 1
        return uid(self.next_id)

    def sql(self, statement, parameters=()):
        with closing(sqlite3.connect(self.path)) as connection, connection:
            return connection.execute(statement, parameters).fetchall()

    def owner(self, worker, number, project='panam'):
        session_id = uid(number)
        owner_id = worker + '@' + session_id
        stamp = _format_queue_timestamp(self.now)
        journal = SqliteWorkerJournalRepository(self.path)
        result = journal.start_session(session_id=session_id, worker_id=worker,
            queue_owner_id=owner_id, started_at=stamp,
            stale_before=_format_queue_timestamp(self.now-timedelta(seconds=30)))
        self.assertEqual('APPLIED', result.code.value)
        queue = DurableCommandQueueService(SqliteWorkflowCommandRepository(self.path),
            CommandDefinitionRegistry((CommandDefinition('LOCK_TEST', 1, (), (), (), lambda p: '{}'),)),
            1000, clock=lambda: self.now, id_factory=self.identifier)
        result = queue.enqueue(project_id=project, command_kind='LOCK_TEST', command_schema_version=1,
                               payload={}, idempotency_key=worker, actor_id='test')
        claim = queue.claim_next(lease_owner=owner_id)
        self.assertEqual(result.command.command_id, claim.command.command_id)
        return LockOwner(worker, session_id, owner_id, claim.command.command_id, claim.command.claim_count)

    def fresh(self):
        self.sql("UPDATE worker_sessions SET last_heartbeat_at=? WHERE state IN ('ACTIVE','STOPPING')",
                 (_format_queue_timestamp(self.now),))

    def acquire(self, resource=None, owner=None, acquisition=10):
        return self.service.acquire(resource or self.resource, owner or self.a, uid(acquisition))

    def act(self, name, grant, **changes):
        arguments = dict(resource=grant.resource, owner=grant.owner, acquisition_id=grant.acquisition_id,
                         fencing_token=grant.fencing_token, expected_revision=grant.revision)
        arguments.update(changes)
        return getattr(self.service, name)(**arguments)

    def dump(self):
        with closing(sqlite3.connect(self.path)) as connection, connection:
            return tuple(connection.iterdump())


class ProjectLockBehaviorTest(LockFixture):
    def test_project_project_exclusion(self):
        self.assertEqual(C.ACQUIRED, self.acquire().code)
        self.assertEqual(C.CONFLICT, self.acquire(owner=self.b, acquisition=11).code)

    def test_project_branch_exclusion(self):
        self.acquire()
        self.assertEqual(C.CONFLICT, self.acquire(self.branch, self.b, 11).code)

    def test_branch_project_exclusion(self):
        self.acquire(self.branch)
        self.assertEqual(C.CONFLICT, self.acquire(owner=self.b, acquisition=11).code)

    def test_branch_branch_exclusion(self):
        self.acquire(self.branch)
        other = replace(self.branch, branch_ref='refs/heads/other')
        self.assertEqual(C.CONFLICT, self.acquire(other, self.b, 11).code)

    def test_independent_projects(self):
        self.acquire()
        self.sql("INSERT INTO project_policies VALUES('other','1','D:\\Other')")
        owner = self.owner('other-worker', 3, 'other')
        result = self.acquire(LockResource('other', r'D:\Other'), owner, 11)
        self.assertEqual(C.ACQUIRED, result.code)
        self.assertEqual(1, result.grant.fencing_token)

    def test_alias_registration_before_and_after_acquisition(self):
        self.sql("INSERT INTO project_policies VALUES('alias','1','c:/synthetic/PANAM')")
        before = self.dump()
        self.assertEqual(C.RESOURCE_ALIAS_CONFLICT, self.acquire().code)
        self.assertEqual(before, self.dump())
        self.sql("DELETE FROM project_policies WHERE project_id='alias'")
        grant = self.acquire().grant
        self.sql("INSERT INTO project_policies VALUES('alias','1','c:/synthetic/PANAM')")
        self.assertEqual(C.RESOURCE_ALIAS_CONFLICT, self.act('renew', grant).code)

    def test_root_binding_normalization_and_rebinding_rejected(self):
        normalized = replace(self.resource, project_root='c:/synthetic/PANAM')
        grant = self.acquire(normalized).grant
        self.assertEqual(C.CURRENT, self.act('check_current_ownership', grant, resource=self.resource).code)
        self.sql("UPDATE project_policies SET project_root='C:\\Moved' WHERE project_id='panam'")
        self.assertEqual(C.RESOURCE_CHANGED, self.act('check_current_ownership', grant).code)
        self.assertEqual(C.RESOURCE_CHANGED, self.acquire(LockResource('panam', r'C:\Moved'), self.b, 11).code)

    def test_missing_and_invalid_registry(self):
        self.assertEqual(C.INVALID_RESOURCE, self.acquire(LockResource('absent', r'C:\Absent')).code)
        self.sql("UPDATE project_policies SET policy_version='bad'")
        self.assertEqual(C.UNAVAILABLE, self.acquire().code)

    def test_branch_validation_exact_and_no_git_needed(self):
        self.assertEqual(C.ACQUIRED, self.acquire(self.branch).code)
        for name in ('main','refs/tags/main','refs/heads/','refs/heads/a..b','refs/heads/a@{b',
                     'refs/heads/a.lock','refs/heads/.hidden','refs/heads/a b','refs/heads/-a',
                     'refs/heads/a/','refs/heads/a//b','refs/heads/a\\b','refs/heads/a\x7f'):
            with self.subTest(name=name), self.assertRaises((ValueError, TypeError)):
                replace(self.branch, branch_ref=name)

    def test_branch_case_collision_even_after_release(self):
        grant = self.acquire(self.branch).grant
        other = replace(self.branch, branch_ref='refs/heads/phase/test')
        self.assertEqual(C.BRANCH_COLLISION, self.acquire(other, self.b, 11).code)
        self.assertEqual(C.RELEASED, self.act('release', grant).code)
        self.assertEqual(C.BRANCH_COLLISION, self.acquire(other, self.b, 11).code)

    def test_atomic_branch_owner_fence_revision(self):
        grant = self.acquire(self.branch).grant
        row = self.sql('SELECT acquisition_id,fencing_token,revision FROM project_branch_locks')[0]
        self.assertEqual((grant.acquisition_id,grant.fencing_token,grant.revision), row)
        self.now += timedelta(seconds=1)
        renewed = self.act('renew', grant)
        self.assertEqual(C.RENEWED, renewed.code)
        self.assertEqual(renewed.grant.revision, self.sql('SELECT revision FROM project_branch_locks')[0][0])

    def test_acquisition_idempotence_and_conflicting_reuse(self):
        first = self.acquire(self.branch)
        before = self.dump()
        repeated = self.acquire(self.branch)
        self.assertEqual(C.EXISTING, repeated.code)
        self.assertEqual(first.grant, repeated.grant)
        self.assertEqual(before, self.dump())
        self.assertEqual(C.ACQUISITION_CONFLICT, self.acquire(self.branch, self.b).code)
        self.assertEqual(C.ACQUISITION_CONFLICT, self.acquire().code)
        self.act('release', first.grant)
        self.assertEqual(C.ACQUISITION_CONFLICT, self.acquire(self.branch).code)

    def test_current_owner_renewal_and_revision(self):
        grant = self.acquire().grant
        self.now += timedelta(seconds=10)
        renewed = self.act('renew', grant)
        self.assertEqual(C.RENEWED, renewed.code)
        self.assertEqual(grant.acquired_at, renewed.grant.acquired_at)
        self.assertEqual(grant.fencing_token, renewed.grant.fencing_token)
        self.assertEqual(grant.revision+1, renewed.grant.revision)
        self.assertGreater(renewed.grant.lease_expires_at, grant.lease_expires_at)
        self.assertEqual(C.CAS_CONFLICT, self.act('renew', grant).code)

    def test_wrong_owner_old_session_old_claim(self):
        grant = self.acquire().grant
        for owner in (self.b, replace(self.a, session_id=uid(99), queue_owner_id='worker-a@'+uid(99)),
                      replace(self.a, claim_count=2)):
            before = self.dump()
            for method in ('renew','release','reserve_effect_window','check_current_ownership'):
                self.assertEqual(C.INVALID_OWNER, self.act(method, grant, owner=owner).code)
            self.assertEqual(before, self.dump())

    def test_release_idempotence_and_no_fence_reuse_after_reopen(self):
        first = self.acquire().grant
        released = self.act('release', first)
        self.assertEqual(C.RELEASED, released.code)
        self.assertEqual(C.ALREADY_RELEASED, self.act('release', released.grant).code)
        self.service = ProjectLockService(SqliteProjectLockRepository(self.path), clock=lambda: self.now)
        second = self.acquire(owner=self.b, acquisition=11)
        self.assertEqual(C.ACQUIRED, second.code)
        self.assertGreater(second.grant.fencing_token, first.fencing_token)
        before = self.dump()
        for name in ('renew','release'):
            self.assertIn(self.act(name, first).code, {C.INVALID_OWNER,C.STALE_FENCE})
            self.assertEqual(C.STALE_FENCE, self.act(name, second.grant, fencing_token=first.fencing_token).code)
        self.assertEqual(before, self.dump())

    def test_same_session_new_acquisition_old_token_rejected(self):
        first = self.acquire().grant
        self.act('release', first)
        second = self.acquire(acquisition=11).grant
        self.assertEqual(2, second.fencing_token)
        for name in ('renew','release'):
            self.assertEqual(C.STALE_FENCE, self.act(name, first).code)

    def test_before_equal_and_after_expiry_observation_does_not_mutate(self):
        grant = self.acquire().grant
        for seconds, code in ((59,C.CURRENT),(60,C.CURRENT_LEASE_LOST),(61,C.CURRENT_LEASE_LOST)):
            self.now = datetime(2026,9,25,10,tzinfo=timezone.utc)+timedelta(seconds=seconds)
            self.fresh()
            before = self.dump()
            result = self.act('check_current_ownership', grant)
            self.assertEqual(code, result.code)
            self.assertEqual(_format_queue_timestamp(self.now), result.observed_at)
            self.assertEqual(before, self.dump())

    def test_expiry_no_takeover_provenance_and_no_late_release(self):
        grant = self.acquire(self.branch).grant
        self.now += timedelta(seconds=60)
        self.fresh()
        expired = self.act('release', grant)
        self.assertEqual(C.CURRENT_LEASE_LOST, expired.code)
        self.assertEqual(LockState.RECONCILIATION_REQUIRED, expired.grant.state)
        self.assertEqual(C.RECONCILIATION_REQUIRED, self.acquire(self.branch,self.b,11).code)
        observed = self.service.observe(self.branch)
        self.assertEqual(grant.owner, observed.grant.owner)
        self.assertEqual(grant.lease_expires_at, observed.grant.lease_expires_at)
        self.assertEqual(('ACQUIRED','RECONCILIATION_REQUIRED'), tuple(e.kind for e in observed.events))
        self.assertEqual(C.RECONCILIATION_REQUIRED,self.act('release',expired.grant).code)

    def test_expired_conflicting_acquire_records_reconciliation(self):
        self.acquire()
        self.now += timedelta(seconds=61)
        self.fresh()
        self.assertEqual(C.CURRENT_LEASE_LOST,self.acquire(owner=self.b,acquisition=11).code)
        self.assertEqual(1,self.sql('SELECT fence_high_water FROM project_locks')[0][0])

    def test_stopping_session_rules(self):
        grant = self.acquire().grant
        stamp = _format_queue_timestamp(self.now)
        self.sql("UPDATE worker_sessions SET state='STOPPING',stop_reason_code='STOP_REQUESTED' WHERE session_id=?",(self.a.session_id,))
        self.assertEqual(C.INVALID_OWNER,self.act('renew',grant).code)
        self.assertEqual(C.INVALID_OWNER,self.acquire(acquisition=12).code)
        self.assertEqual(C.RELEASED,self.act('release',grant).code)

    def test_stale_heartbeat_no_renewal_or_cleanup(self):
        grant = self.acquire().grant
        self.now += timedelta(seconds=30)
        before = self.dump()
        self.assertEqual(C.INVALID_OWNER,self.act('renew',grant).code)
        self.assertEqual(LockState.RECONCILIATION_REQUIRED,self.service.observe(self.resource).effective_state)
        self.assertEqual(before,self.dump())

    def test_command_claim_and_lease_validated(self):
        forged = replace(self.a, command_id=self.b.command_id)
        self.assertEqual(C.INVALID_OWNER,self.acquire(owner=forged).code)
        self.now += timedelta(seconds=1000)
        self.fresh()
        self.assertEqual(C.INVALID_OWNER,self.acquire().code)

    def test_backward_time_and_forward_jump(self):
        grant = self.acquire().grant
        self.now -= timedelta(seconds=1)
        self.assertEqual(C.CLOCK_INVALID,self.act('renew',grant).code)
        self.now += timedelta(days=1)
        self.fresh()
        self.assertEqual(C.CURRENT_LEASE_LOST,self.act('renew',grant).code)

    def test_clock_is_sampled_inside_transaction(self):
        def clock():
            connection = sqlite3.connect(self.path,timeout=0)
            try:
                with self.assertRaises(sqlite3.OperationalError):
                    connection.execute('BEGIN IMMEDIATE')
            finally:
                connection.close()
            return self.now
        service = ProjectLockService(self.repo,clock=clock)
        self.assertEqual(C.ACQUIRED,service.acquire(self.resource,self.a,uid(10)).code)

    def test_clock_failure_rolls_back(self):
        before=self.dump()
        service=ProjectLockService(self.repo,clock=lambda: datetime(2026,1,1))
        self.assertEqual(C.CLOCK_INVALID,service.acquire(self.resource,self.a,uid(10)).code)
        self.assertEqual(before,self.dump())

    def test_configuration_and_public_exports(self):
        self.assertEqual((60,5),(LockConfiguration().lock_lease_seconds,LockConfiguration().lock_renewal_margin_seconds))
        for kwargs in ({'lock_lease_seconds':86401},{'lock_lease_seconds':True},{'lock_renewal_margin_seconds':60}):
            with self.assertRaises((TypeError,ValueError)):
                LockConfiguration(**kwargs)
        expected=('LockState','LockOperation','LockResultCode','LockTerminalOutcome','LockResource',
                  'LockOwner','LockConfiguration','LockTerminalEvidence','LockRequest','LockGrant','LockEvent',
                  'LockResult','ProjectLockRepository','ProjectLockService','SqliteProjectLockRepository')
        self.assertEqual(expected,tuple(package.__all__[120:]))
        self.assertEqual(len(package.__all__),len(set(package.__all__)))

    def test_observe_not_found_and_branch_mismatch(self):
        self.assertEqual(C.NOT_FOUND,self.service.observe(self.resource).code)
        grant=self.acquire(self.branch).grant
        self.assertEqual(C.INVALID_RESOURCE,self.act('check_current_ownership',grant,resource=self.resource).code)
        self.assertEqual(C.CAS_CONFLICT,self.act('check_current_ownership',grant,expected_revision=2).code)

    def test_corrupt_binding_and_history_fail_closed(self):
        self.acquire(self.branch)
        self.sql("UPDATE project_branch_locks SET revision=99")
        self.assertEqual(C.UNAVAILABLE,self.service.observe(self.branch).code)
        self.sql("UPDATE project_branch_locks SET revision=1")
        self.sql("DELETE FROM project_lock_events")
        self.assertEqual(C.UNAVAILABLE,self.service.observe(self.branch).code)

    def test_fence_exhaustion_no_mutation(self):
        grant=self.acquire().grant
        released=self.act('release',grant).grant
        impossible=replace(released,fencing_token=9223372036854775807)
        before=self.dump()
        with patch.object(self.repo,'_load',return_value=(impossible,())):
            self.assertEqual(C.FENCE_EXHAUSTED,self.acquire(acquisition=11).code)
        self.assertEqual(before,self.dump())


class ProjectLockReservationTest(LockFixture):
    def reserved(self):
        grant=self.acquire(self.branch).grant
        result=self.act('reserve_effect_window',grant)
        self.assertEqual(C.EFFECT_RESERVED,result.code)
        self.assertEqual(grant.fencing_token,result.grant.fencing_token)
        self.assertEqual(grant.revision+1,result.grant.revision)
        return result.grant

    def test_reservation_blocks_after_expiry_and_cannot_ordinary_release(self):
        grant=self.reserved()
        self.now+=timedelta(seconds=120)
        self.fresh()
        self.assertEqual(C.CONFLICT,self.acquire(self.branch,self.b,11).code)
        self.assertEqual(C.INVALID_STATE,self.act('release',grant).code)
        self.assertEqual(LockState.EFFECT_RESERVED,self.service.observe(self.branch).grant.state)

    def test_reservation_requires_branch_and_fresh_cas(self):
        grant=self.acquire().grant
        self.assertEqual(C.INVALID_RESOURCE,self.act('reserve_effect_window',grant).code)
        self.act('release',grant)
        grant=self.acquire(self.branch,acquisition=11).grant
        self.assertEqual(C.CAS_CONFLICT,self.act('reserve_effect_window',grant,expected_revision=99).code)
        self.now+=timedelta(seconds=60)
        self.fresh()
        self.assertEqual(C.CURRENT_LEASE_LOST,self.act('reserve_effect_window',grant).code)

    def test_verified_success_terminalization_after_expiry(self):
        grant=self.reserved()
        self.now+=timedelta(seconds=120)
        result=self.act('terminalize_effect_reservation',grant,evidence=LockTerminalEvidence(T.VERIFIED_SUCCESS,'verified:test-1'))
        self.assertEqual(C.RELEASED,result.code)
        self.assertEqual('VERIFIED_SUCCESS',result.grant.terminal_outcome)
        self.fresh()
        self.assertEqual(C.ACQUIRED,self.acquire(self.branch,self.b,11).code)

    def test_proven_no_effect_terminalization(self):
        grant=self.reserved()
        result=self.act('terminalize_effect_reservation',grant,evidence=LockTerminalEvidence(T.PROVEN_NO_EFFECT,'proof:test-2'))
        self.assertEqual(C.RELEASED,result.code)
        self.assertEqual('proof:test-2',result.events[-1].grant.evidence_reference)

    def test_unknown_effect_requires_reconciliation(self):
        grant=self.reserved()
        result=self.act('terminalize_effect_reservation',grant,evidence=LockTerminalEvidence(T.UNKNOWN,'incident:test-3'))
        self.assertEqual(C.RECONCILIATION_REQUIRED,result.code)
        self.assertEqual(C.RECONCILIATION_REQUIRED,self.acquire(self.branch,self.b,11).code)
        self.assertEqual(C.RECONCILIATION_REQUIRED,self.act('terminalize_effect_reservation',result.grant,
            evidence=LockTerminalEvidence(T.VERIFIED_SUCCESS,'late:claim')).code)

    def test_terminalization_wrong_owner_fence_revision(self):
        grant=self.reserved()
        evidence=LockTerminalEvidence(T.PROVEN_NO_EFFECT,'proof:test')
        for change,code in (({'owner':self.b},C.INVALID_OWNER),({'fencing_token':99},C.STALE_FENCE),
                            ({'expected_revision':1},C.CAS_CONFLICT)):
            self.assertEqual(code,self.act('terminalize_effect_reservation',grant,evidence=evidence,**change).code)
        self.assertEqual(LockState.EFFECT_RESERVED,self.service.observe(self.branch).grant.state)


class ProjectLockConstraintTest(LockFixture):
    def assert_state_outcome_matrix(self, operation):
        self.assertEqual(C.ACQUIRED, self.acquire().code)
        allowed = {
            'HELD': {None},
            'EFFECT_RESERVED': {None},
            'RELEASED': {'RELEASED', 'VERIFIED_SUCCESS', 'PROVEN_NO_EFFECT'},
            'RECONCILIATION_REQUIRED': {'UNKNOWN', 'LEASE_EXPIRED', 'OWNER_LOST'},
        }
        outcomes = (None, 'RELEASED', 'VERIFIED_SUCCESS', 'PROVEN_NO_EFFECT',
                    'UNKNOWN', 'LEASE_EXPIRED', 'OWNER_LOST', 'INVALID')
        with closing(sqlite3.connect(self.path)) as connection:
            connection.row_factory = sqlite3.Row
            connection.execute('PRAGMA foreign_keys=ON')
            template = dict(connection.execute('SELECT * FROM project_locks').fetchone())
            before = tuple(connection.iterdump())
            for state, valid_outcomes in allowed.items():
                for outcome in outcomes:
                    with self.subTest(operation=operation, state=state, outcome=outcome):
                        row = dict(template, state=state, terminal_outcome=outcome,
                                   reserved_at=template['updated_at'] if state == 'EFFECT_RESERVED' else None)
                        connection.execute('SAVEPOINT constraint_case')
                        try:
                            if operation == 'insert':
                                # Remove only this synthetic fixture's rows within
                                # the savepoint; all other valid FK bindings remain.
                                connection.execute('DELETE FROM project_lock_events')
                                connection.execute('DELETE FROM project_locks')
                                statement = ('INSERT INTO project_locks(' + ','.join(row)
                                             + ') VALUES(' + ','.join('?' for _ in row) + ')')
                                parameters = tuple(row.values())
                            else:
                                statement = ('UPDATE project_locks SET state=?,terminal_outcome=?,reserved_at=? '
                                             'WHERE project_id=?')
                                parameters = (state, outcome, row['reserved_at'], template['project_id'])
                            if outcome in valid_outcomes:
                                connection.execute(statement, parameters)
                                actual = connection.execute(
                                    'SELECT state,terminal_outcome,reserved_at FROM project_locks').fetchone()
                                self.assertEqual((state, outcome, row['reserved_at']), tuple(actual))
                            else:
                                with self.assertRaises(sqlite3.IntegrityError) as rejected:
                                    connection.execute(statement, parameters)
                                self.assertEqual('SQLITE_CONSTRAINT_CHECK', rejected.exception.sqlite_errorname)
                        finally:
                            connection.execute('ROLLBACK TO constraint_case')
                            connection.execute('RELEASE constraint_case')
                        self.assertEqual(before, tuple(connection.iterdump()))

    def test_raw_insert_state_outcome_constraint(self):
        self.assert_state_outcome_matrix('insert')

    def test_raw_update_state_outcome_constraint(self):
        self.assert_state_outcome_matrix('update')


class ProjectLockRaceTest(LockFixture):
    def race(self,*calls):
        barrier=threading.Barrier(len(calls))
        results=[]
        errors=[]
        def run(call):
            try:
                barrier.wait(timeout=10)
                results.append(call())
            except BaseException as error:
                errors.append(error)
        threads=[threading.Thread(target=run,args=(call,)) for call in calls]
        for thread in threads: thread.start()
        for thread in threads: thread.join(timeout=15)
        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual([],errors)
        return results

    def test_acquisition_race(self):
        results=self.race(lambda:self.acquire(),lambda:self.acquire(owner=self.b,acquisition=11))
        self.assertEqual(1,sum(r.code is C.ACQUIRED for r in results))
        self.assertTrue(all(r.code in {C.ACQUIRED,C.CONFLICT,C.TRANSIENT_CONTENTION} for r in results))
        self.assertEqual(1,self.sql('SELECT count(*) FROM project_lock_events')[0][0])

    def test_project_branch_race(self):
        results=self.race(lambda:self.acquire(),lambda:self.acquire(self.branch,self.b,11))
        self.assertEqual(1,sum(r.code is C.ACQUIRED for r in results))
        self.assertEqual(1,self.sql('SELECT count(*) FROM project_locks')[0][0])

    def test_acquisition_overlap_holds_real_writer_transaction(self):
        held = threading.Event()
        release = threading.Event()
        results = {}
        errors = []
        opener = sr._open_connection

        class HeldWriterConnection:
            def __init__(self, connection):
                self.connection = connection

            def __getattr__(self, name):
                return getattr(self.connection, name)

            def execute(self, statement, *args):
                cursor = self.connection.execute(statement, *args)
                if statement == 'BEGIN IMMEDIATE':
                    if not self.connection.in_transaction:
                        raise AssertionError('writer transaction was not entered')
                    held.set()
                    if not release.wait(timeout=10):
                        raise TimeoutError('held writer was not released')
                return cursor

        def open_connection(*args):
            connection = opener(*args)
            if threading.current_thread() is writer_a:
                return HeldWriterConnection(connection)
            return connection

        def run(label, call):
            try:
                results[label] = call()
            except BaseException as error:
                errors.append(error)

        writer_a = threading.Thread(target=run, args=('a', lambda: self.acquire(self.branch)))
        other_branch = replace(self.branch, branch_ref='refs/heads/other')
        writer_b = threading.Thread(target=run, args=(
            'b', lambda: self.acquire(other_branch, self.b, 11)))
        with patch.object(sr, '_open_connection', side_effect=open_connection):
            writer_a.start()
            try:
                self.assertTrue(held.wait(timeout=10), 'Writer A did not enter BEGIN IMMEDIATE')
                self.assertTrue(writer_a.is_alive())
                self.assertNotIn('a', results)
                writer_b.start()
                writer_b.join(timeout=5)
                self.assertFalse(writer_b.is_alive(), 'Writer B did not finish during overlap')
                self.assertEqual([], errors)
                self.assertEqual(C.TRANSIENT_CONTENTION, results['b'].code)
                self.assertFalse(release.is_set())
                self.assertTrue(writer_a.is_alive())
                for table in ('project_locks', 'project_branch_locks', 'project_lock_events'):
                    self.assertEqual([(0,)], self.sql('SELECT count(*) FROM ' + table))
            finally:
                release.set()
                writer_a.join(timeout=15)
                if writer_b.ident is not None:
                    writer_b.join(timeout=15)
            self.assertFalse(writer_a.is_alive())
            self.assertFalse(writer_b.is_alive())
            self.assertEqual([], errors)

        self.assertEqual(C.ACQUIRED, results['a'].code)
        self.assertEqual(
            [(uid(10), self.a.queue_owner_id, 1, 1, 1, 'HELD')],
            self.sql('SELECT acquisition_id,queue_owner_id,fence_high_water,fencing_token,revision,state '
                     'FROM project_locks'))
        self.assertEqual(
            [(self.branch.branch_ref, self.branch.branch_ref.casefold(), uid(10), 1, 1)],
            self.sql('SELECT branch_ref,conflict_key,acquisition_id,fencing_token,revision '
                     'FROM project_branch_locks'))
        self.assertEqual(
            [('ACQUIRED', uid(10), 1, 1)],
            self.sql('SELECT kind,acquisition_id,fencing_token,revision FROM project_lock_events'))
        observed = self.service.observe(self.branch)
        self.assertEqual(results['a'].grant, observed.grant)
        self.assertEqual(1, len(observed.events))
        self.assertEqual(observed.grant, observed.events[0].grant)

    def test_release_reacquire_and_old_fence_race(self):
        first=self.acquire().grant
        results=self.race(lambda:self.act('release',first),lambda:self.acquire(owner=self.b,acquisition=11))
        self.assertTrue(all(r.code in {C.RELEASED,C.ACQUIRED,C.CONFLICT,C.TRANSIENT_CONTENTION} for r in results))
        observed=self.service.observe(self.resource).grant
        if observed.state is LockState.HELD and observed.acquisition_id==first.acquisition_id:
            self.act('release',observed)
        if self.service.observe(self.resource).grant.state is LockState.RELEASED:
            self.assertEqual(C.ACQUIRED,self.acquire(owner=self.b,acquisition=11).code)
        second=self.service.observe(self.resource).grant
        self.now+=timedelta(seconds=1)
        self.race(lambda:self.act('release',first),lambda:self.act('renew',second))
        final=self.service.observe(self.resource).grant
        self.assertEqual(second.acquisition_id,final.acquisition_id)
        self.assertEqual(LockState.HELD,final.state)

    def test_reserve_release_race(self):
        grant=self.acquire(self.branch).grant
        results=self.race(lambda:self.act('reserve_effect_window',grant),lambda:self.act('release',grant))
        self.assertEqual(1,sum(r.code in {C.RELEASED,C.EFFECT_RESERVED} for r in results))
        current=self.service.observe(self.branch).grant
        if current.state is LockState.EFFECT_RESERVED:
            self.assertEqual(C.CONFLICT,self.acquire(self.branch,self.b,11).code)
        self.assertEqual(2,len(self.service.observe(self.branch).events))

    def test_expired_renew_and_conflicting_acquire_race(self):
        grant=self.acquire().grant
        self.now+=timedelta(seconds=60)
        self.fresh()
        results=self.race(lambda:self.act('renew',grant),lambda:self.acquire(owner=self.b,acquisition=11))
        self.assertNotIn(C.ACQUIRED,[r.code for r in results])
        self.assertEqual(LockState.RECONCILIATION_REQUIRED,self.service.observe(self.resource).grant.state)

    def test_busy_is_distinct_and_not_retried(self):
        connection=sqlite3.connect(self.path,timeout=0)
        connection.execute('BEGIN IMMEDIATE')
        try:
            self.assertEqual(C.TRANSIENT_CONTENTION,self.acquire().code)
        finally:
            connection.rollback()
            connection.close()
        self.assertEqual([],self.sql('SELECT * FROM project_locks'))


class ProjectLockRollbackTest(LockFixture):
    def fail_after(self,needle,call,commit=False):
        before=self.dump()
        opener=sr._open_connection
        class Proxy:
            def __init__(self,connection): self.connection=connection
            def __getattr__(self,name): return getattr(self.connection,name)
            def execute(self,sql,*args):
                cursor=self.connection.execute(sql,*args)
                if needle and needle in sql:
                    raise sqlite3.OperationalError('injected failure after write')
                return cursor
            def commit(self):
                if commit: raise sqlite3.OperationalError('injected precommit failure')
                self.connection.commit()
        with patch.object(sr,'_open_connection',side_effect=lambda *a:Proxy(opener(*a))):
            self.assertEqual(C.UNAVAILABLE,call().code)
        self.assertEqual(before,self.dump())

    def test_rollback_fencing_and_ownership_write(self):
        self.fail_after('INSERT INTO project_locks',lambda:self.acquire(self.branch))
        self.assertEqual(1,self.acquire(self.branch).grant.fencing_token)

    def test_rollback_subordinate_branch_write(self):
        self.fail_after('INSERT INTO project_branch_locks',lambda:self.acquire(self.branch))
        self.assertEqual([],self.sql('SELECT * FROM project_locks'))
        self.assertEqual([],self.sql('SELECT * FROM project_branch_locks'))

    def test_rollback_event_write(self):
        self.fail_after('INSERT INTO project_lock_events',lambda:self.acquire(self.branch))

    def test_rollback_commit_failure(self):
        self.fail_after(None,lambda:self.acquire(self.branch),commit=True)

    def test_rollback_renew_release_reserve_and_terminalize(self):
        grant=self.acquire(self.branch).grant
        self.now+=timedelta(seconds=1)
        for method in ('renew','release','reserve_effect_window'):
            with self.subTest(method=method):
                self.fail_after('INSERT INTO project_lock_events',lambda:self.act(method,grant))
        grant=self.act('reserve_effect_window',grant).grant
        self.fail_after('INSERT INTO project_lock_events',lambda:self.act('terminalize_effect_reservation',grant,
            evidence=LockTerminalEvidence(T.VERIFIED_SUCCESS,'proof:rollback')))

    def test_rollback_expiry_transition(self):
        grant=self.acquire().grant
        self.now+=timedelta(seconds=60)
        self.fresh()
        self.fail_after('INSERT INTO project_lock_events',lambda:self.act('renew',grant))


class ProjectLockMigrationTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='dl25-migration-')
        self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/'migration.sqlite3'

    def connection(self):
        connection=sqlite3.connect(self.path)
        connection.row_factory=sqlite3.Row
        connection.execute('PRAGMA foreign_keys=ON')
        return connection

    def test_fresh_exact_schema_and_idempotence(self):
        initialize_database(self.path,'first')
        with closing(self.connection()) as c, c:
            self.assertEqual(list(range(1,9)),[r[0] for r in c.execute('SELECT version FROM schema_migrations ORDER BY version')])
            sr._validate_current_schema(c,'test','test')
            before=tuple(c.iterdump())
        initialize_database(self.path,'second')
        with closing(self.connection()) as c, c: self.assertEqual(before,tuple(c.iterdump()))

    def test_v6_upgrade_preserves_records(self):
        with closing(self.connection()) as c, c:
            apply_migrations(c,'v6',PRODUCTION_MIGRATIONS[:6])
            c.execute("INSERT INTO project_policies VALUES('kept','1','C:\\Kept')")
            c.commit()
            originals={r[0]:list(c.execute('SELECT * FROM '+r[0])) for r in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
            apply_migrations(c,'v7',PRODUCTION_MIGRATIONS)
            for table,rows in originals.items():
                actual=list(c.execute('SELECT * FROM '+table))
                self.assertEqual([tuple(r) for r in rows],[tuple(r) for r in actual[:len(rows)]])
            sr._validate_current_schema(c,'test','test')

    def test_migrations_one_to_six_match_authorized_head(self):
        expected=(
            '3393683d0d7f16736ff25880c7d07daaf1fa092d150fc37b41b47f7b2dccfa0c',
            'e8adb22d6a5e1d91449736e2f2be96ee8d8ae7f1955094022e0c9172b7f6f4ef',
            '763d829a3794f37a44c801aa40d11fc0e2726a234602804237fc5ebc0579ac50',
            '18fb10a377856b02263e89a21bdc0bc757cd642ad5fa52822ea4db8dd3aed4b8',
            '942a75bf24fb40116838265a525f138a0aa36d537f815141fa61d11215c1746d',
            '9e2aa1fee1720dc01295e5da247cbddace9e146c1bee26e289730426a9fc32ec')
        self.assertEqual(expected,tuple(hashlib.sha256('\0'.join(m.statements).encode()).hexdigest() for m in PRODUCTION_MIGRATIONS[:6]))

    def test_migration_statement_and_ledger_failure_roll_back(self):
        with closing(self.connection()) as c, c:
            apply_migrations(c,'v6',PRODUCTION_MIGRATIONS[:6])
            before=tuple(c.iterdump())
            broken=PRODUCTION_MIGRATIONS[:6]+(Migration(7,PRODUCTION_MIGRATIONS[6].statements+('INSERT INTO missing VALUES(1)',)),)
            with self.assertRaises(MigrationError): apply_migrations(c,'v7',broken)
            self.assertEqual(before,tuple(c.iterdump()))
            c.execute("CREATE TRIGGER block7 BEFORE INSERT ON schema_migrations WHEN NEW.version=7 BEGIN SELECT RAISE(ABORT,'injected'); END")
            c.commit()
            before=tuple(c.iterdump())
            with self.assertRaises(MigrationError): apply_migrations(c,'v7',PRODUCTION_MIGRATIONS)
            self.assertEqual(before,tuple(c.iterdump()))

    def test_malformed_future_and_schema_rejected(self):
        initialize_database(self.path,'first')
        with closing(self.connection()) as c, c:
            c.execute('UPDATE schema_migrations SET version=9 WHERE version=8')
            c.commit()
            with self.assertRaises(MigrationError): apply_migrations(c,'later',PRODUCTION_MIGRATIONS)
            c.execute('DELETE FROM schema_migrations WHERE version=9')
            c.execute('DELETE FROM schema_migrations WHERE version=3')
            c.commit()
            with self.assertRaises(MigrationError): apply_migrations(c,'later',PRODUCTION_MIGRATIONS)

    def test_schema_gate_rejects_missing_index_and_extra_trigger(self):
        initialize_database(self.path,'first')
        service=ProjectLockService(SqliteProjectLockRepository(self.path),clock=lambda:datetime.now(timezone.utc))
        with closing(self.connection()) as c, c:
            c.execute('DROP INDEX project_lock_history_idx')
        self.assertEqual(C.UNSUPPORTED_SCHEMA,service.observe(LockResource('x',r'C:\X')).code)
        with closing(self.connection()) as c, c:
            c.execute(PRODUCTION_MIGRATIONS[6].statements[-1])
            c.execute("CREATE TRIGGER unexpected AFTER INSERT ON project_locks BEGIN SELECT 1; END")
        self.assertEqual(C.UNSUPPORTED_SCHEMA,service.observe(LockResource('x',r'C:\X')).code)


if __name__=='__main__':
    unittest.main()
