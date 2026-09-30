"""Dedicated DL-2.6 append-only evidence; no migration, retry or recovery API."""

from dataclasses import asdict
from datetime import timedelta
import json
from pathlib import Path
import sqlite3

from .command_queue import _format_queue_timestamp
from .models import LockState, PhaseContract, WorkflowCommandState
from .phase_branch_creation_models import ACTION, OperationRejected, canonical, digest, _issue_permission
from .sqlite_repositories import (
    SqliteProjectLockRepository, SqliteWorkflowCommandRepository,
    _canonical_database_path, _lock_decode_snapshot, _lock_snapshot,
    _open_connection, _open_read_only_connection, _validate_current_schema,
)


class SqlitePhaseBranchOperations:
    """One immutable row per command; every later fact is inserted exactly once.

    This is trusted infrastructure. Direct database mutation and caller-selected
    dependencies are not supported application interfaces.
    """
    def __init__(self, database_path: Path, *, clock, configuration):
        self.path, _ = _canonical_database_path(database_path)
        self.clock, self.configuration = clock, configuration
        self.locks = SqliteProjectLockRepository(self.path)

    def _transaction(self, operation, *, read_only=False):
        connection = None
        try:
            opener = _open_read_only_connection if read_only else _open_connection
            connection = opener(self.path, "PhaseBranchOperation", "dl26")
            connection.execute("BEGIN" if read_only else "BEGIN IMMEDIATE")
            _validate_current_schema(connection, "PhaseBranchOperation", "dl26")
            if connection.execute("PRAGMA foreign_key_check(phase_branch_operation_events)").fetchone() is not None:
                raise OperationRejected("orphan effect evidence")
            result = operation(connection)
            if read_only:
                connection.rollback()
            else:
                connection.commit()
            return result
        finally:
            if connection is not None:
                if connection.in_transaction:
                    connection.rollback()
                connection.close()

    def _load(self, connection, authorization):
        intent = authorization.intent
        rows = connection.execute("SELECT * FROM phase_branch_operations WHERE effect_id=? OR command_id=?",
            (intent.effect_id, intent.owner.command_id)).fetchall()
        if not rows:
            return None
        if len(rows) != 1:
            raise OperationRejected("identity conflict")
        row = dict(rows[0])
        if (row["effect_id"] != intent.effect_id or row["command_id"] != intent.owner.command_id
                or row["intent_digest"] != intent.digest()
                or row["authorization_json"] != authorization.canonical_json()):
            raise OperationRejected("immutable effect/command conflict")
        for key in ("authorization_json", "grant_json", "pre_json"):
            if canonical(json.loads(row[key])) != row[key]:
                raise OperationRejected("noncanonical persisted evidence")
        _lock_decode_snapshot(row["grant_json"])
        events = connection.execute("SELECT * FROM phase_branch_operation_events WHERE effect_id=? ORDER BY revision",
                                    (intent.effect_id,)).fetchall()
        decoded = {}
        previous = row["created_at"]
        for revision, event in enumerate(events, 1):
            payload = json.loads(event["payload_json"])
            if (event["revision"] != revision or canonical(payload) != event["payload_json"]
                    or digest(event["payload_json"]) != event["payload_digest"]
                    or event["occurred_at"] < previous):
                raise OperationRejected("event integrity")
            previous = event["occurred_at"]
            kind = event["kind"]
            allowed = {(): {"RESERVATION", "OUTCOME"},
                       ("RESERVATION",): {"DISPATCH", "OUTCOME"},
                       ("RESERVATION", "DISPATCH"): {"OUTCOME"},
                       ("RESERVATION", "DISPATCH", "OUTCOME"): {"TERMINAL"},
                       ("RESERVATION", "OUTCOME"): {"TERMINAL"}}
            if kind not in allowed.get(tuple(decoded), set()):
                raise OperationRejected("event order")
            decoded[kind] = payload
        row["events"], row["revision"] = decoded, len(events)
        return row

    def read(self, authorization):
        return self._transaction(lambda c: self._load(c, authorization), read_only=True)

    def _current(self, connection, authorization, grant):
        intent = authorization.intent
        now_dt = self.clock()
        now = _format_queue_timestamp(now_dt)
        stale = _format_queue_timestamp(now_dt - timedelta(
            seconds=self.configuration.heartbeat_stale_after_seconds))
        if (grant.resource.project_id != intent.policy.project_id or
                grant.resource.project_root != intent.policy.project_root or
                grant.resource.branch_ref != intent.target_ref or grant.owner != intent.owner):
            raise OperationRejected("lock intent binding")
        if self.locks._resource_valid(connection, grant.resource) is not None:
            raise OperationRejected("registered repository changed")
        policy = connection.execute("SELECT policy_version,project_root FROM project_policies WHERE project_id=?",
                                    (intent.policy.project_id,)).fetchone()
        if (policy["policy_version"], policy["project_root"]) != (intent.policy.policy_version.value, intent.policy.project_root):
            raise OperationRejected("policy identity changed")
        phase = connection.execute("SELECT * FROM phases WHERE project_id=? AND phase_id=?",
                                   (intent.phase.project_id, intent.phase.phase_id)).fetchone()
        if phase is None:
            raise OperationRejected("missing phase contract")
        decoded_phase = PhaseContract(phase["project_id"], phase["phase_id"], phase["contract_version"])
        if decoded_phase != intent.phase or phase["contract_digest"] != intent.phase.sha256_digest():
            raise OperationRejected("phase contract changed")
        current, _ = self.locks._load(connection, intent.policy.project_id)
        if (current != grant or grant.state not in {LockState.HELD, LockState.EFFECT_RESERVED}
                or not grant.updated_at <= now < grant.lease_expires_at
                or not self.locks._owner_valid(connection, intent.owner, intent.policy.project_id, now, stale)):
            raise OperationRejected("stale acquisition/fence/revision/owner")
        command = SqliteWorkflowCommandRepository(self.path)._load_command(connection, intent.owner.command_id)
        if (command.state is not WorkflowCommandState.RUNNING or command.command_kind != ACTION
                or command.phase_id != intent.phase.phase_id
                or command.command_schema_version != intent.command_schema_version
                or command.state_version != intent.command_state_version
                or command.intent_digest != intent.command_intent_digest):
            raise OperationRejected("command binding changed")
        return now

    def check(self, authorization, grant):
        return self._transaction(lambda c: self._current(c, authorization, grant), read_only=True)

    def prepare(self, authorization, grant, observation):
        def operation(connection):
            existing = self._load(connection, authorization)
            if existing is not None:
                return False
            now = self._current(connection, authorization, grant)
            if grant.state is not LockState.HELD or observation.target_state != "TARGET_REF_DEFINITELY_ABSENT":
                raise OperationRejected("precondition")
            intent = authorization.intent
            connection.execute("INSERT INTO phase_branch_operations VALUES(?,?,?,?,?,?,?)",
                (intent.effect_id, intent.owner.command_id, intent.digest(), authorization.canonical_json(),
                 _lock_snapshot(grant), observation.canonical_json(), now))
            return True
        return self._transaction(operation)

    def _append(self, connection, row, kind, payload):
        text = canonical(payload)
        stamp = _format_queue_timestamp(self.clock())
        connection.execute("INSERT INTO phase_branch_operation_events VALUES(?,?,?,?,?,?)",
                           (row["effect_id"], row["revision"] + 1, kind, text, digest(text), stamp))

    def reserved(self, authorization, grant):
        def operation(connection):
            row = self._load(connection, authorization)
            if row is None or row["events"]:
                raise OperationRejected("reservation conflict")
            previous = _lock_decode_snapshot(row["grant_json"])
            self._current(connection, authorization, grant)
            if (grant.state is not LockState.EFFECT_RESERVED or grant.acquisition_id != previous.acquisition_id
                    or grant.fencing_token != previous.fencing_token or grant.revision != previous.revision + 1):
                raise OperationRejected("reservation binding")
            self._append(connection, row, "RESERVATION", asdict(grant))
        self._transaction(operation)

    def consume(self, authorization):
        def operation(connection):
            row = self._load(connection, authorization)
            if row is None or tuple(row["events"]) != ("RESERVATION",):
                raise OperationRejected("dispatch CAS conflict")
            grant = _lock_decode_snapshot(canonical(row["events"]["RESERVATION"]))
            now = self._current(connection, authorization, grant)
            if not authorization.valid_from <= self.clock() < authorization.expires_at:
                raise OperationRejected("authority expired")
            self._append(connection, row, "DISPATCH", {"intent_digest": authorization.intent.digest(),
                "reservation_revision": grant.revision, "meaning": "GIT_EFFECT_MAY_HAVE_LAUNCHED", "checked_at": now})
        self._transaction(operation)
        # Only a definitely successful commit can reach this issuance.
        return _issue_permission(authorization.intent)

    def outcome(self, authorization, payload):
        def operation(connection):
            row = self._load(connection, authorization)
            if row is None or "OUTCOME" in row["events"] or "DISPATCH" not in row["events"]:
                raise OperationRejected("outcome conflict")
            if payload["outcome"] not in {"VERIFIED_SUCCESS", "PROVEN_NO_EFFECT", "UNKNOWN"}:
                raise OperationRejected("invalid outcome")
            if payload["outcome"] == "VERIFIED_SUCCESS" and "DISPATCH" not in row["events"]:
                raise OperationRejected("no dispatch")
            self._append(connection, row, "OUTCOME", payload)
            return "dl26:" + authorization.intent.effect_id + ":" + digest(canonical(payload))
        return self._transaction(operation)

    def abort_before_dispatch(self, authorization, reason):
        """CAS-close unconsumed work; never terminalize or release its lock."""
        if type(reason) is not str or not 1 <= len(reason) <= 256:
            raise ValueError("abort reason")
        def operation(connection):
            row = self._load(connection, authorization)
            if row is None or "DISPATCH" in row["events"] or "OUTCOME" in row["events"]:
                raise OperationRejected("predispatch abort CAS conflict")
            self._append(connection, row, "OUTCOME", {"outcome": "UNKNOWN", "predispatch_abort": reason})
        self._transaction(operation)

    def terminal_chain(self, authorization, row=None):
        def operation(connection):
            record = self._load(connection, authorization)
            if record is None or "OUTCOME" not in record["events"] or "RESERVATION" not in record["events"]:
                return None
            outcome = record["events"]["OUTCOME"]
            reference = "dl26:" + authorization.intent.effect_id + ":" + digest(canonical(outcome))
            reserved = _lock_decode_snapshot(canonical(record["events"]["RESERVATION"]))
            _, history = self.locks._load(connection, reserved.resource.project_id)
            matches = [e for e in history if e.kind == "TERMINALIZED"
                and e.grant.acquisition_id == reserved.acquisition_id
                and e.grant.fencing_token == reserved.fencing_token
                and e.grant.revision == reserved.revision + 1
                and e.grant.owner == reserved.owner and e.grant.resource == reserved.resource
                and e.grant.evidence_reference == reference and e.grant.terminal_outcome == outcome["outcome"]]
            if len(matches) != 1:
                return None
            chain = {"reference": reference, "outcome": outcome["outcome"], "grant": asdict(matches[0].grant)}
            if "TERMINAL" in record["events"] and record["events"]["TERMINAL"] != json.loads(canonical(chain)):
                raise OperationRejected("stored terminal disagreement")
            return chain
        return self._transaction(operation, read_only=True)

    def acknowledge_terminal(self, authorization):
        chain = self.terminal_chain(authorization)
        if chain is None:
            raise OperationRejected("terminalization disagreement")
        def operation(connection):
            row = self._load(connection, authorization)
            if "TERMINAL" in row["events"]:
                raise OperationRejected("already terminal")
            self._append(connection, row, "TERMINAL", chain)
        self._transaction(operation)
        return chain
