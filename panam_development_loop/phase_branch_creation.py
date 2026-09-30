"""Standalone one-attempt DL-2.6 composition. No worker or Phase State effects."""
from dataclasses import asdict
from datetime import datetime, timezone
import json

from .git_phase_branch_adapter import GitPhaseBranchAdapter, _argv
from .models import LockState, LockTerminalEvidence, LockTerminalOutcome
from .phase_branch_creation_models import (
    ACTION, EffectAuthorization, OperationRejected, PhaseBranchResult, ProcessEvidence,
    RepositoryObservation, canonical, instant,
)
from .project_locks import ProjectLockService
from .sqlite_repositories import _lock_decode_snapshot


def _boundary(name):
    """Private deterministic fault-injection boundary; no production callback API."""


def _saved_observation(payload):
    value = json.loads(payload)
    # Stored command evidence is retained in the DB; comparison uses state only.
    return RepositoryObservation(value["state_json"], value["target_state"], ())


class PhaseBranchCreation:
    """Trusted composition installs storage, current-authority provider and clock.

    The caller supplies an authorization candidate, never a command/executable.
    An existing operation is read-only on every later invocation, even when it
    has not consumed dispatch. Only this invocation's new prepare may proceed.
    """
    def __init__(self, operations, authority_provider, *, clock=None):
        self.operations, self.authority_provider = operations, authority_provider
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.locks = ProjectLockService(operations.locks, clock=self.clock,
                                       configuration=operations.configuration)
        self.adapter = GitPhaseBranchAdapter()

    def _authorized(self, authorization):
        if type(authorization) is not EffectAuthorization:
            raise OperationRejected("missing immutable effect authority")
        authorization.__post_init__()
        now = instant(self.clock())
        if (not authorization.valid_from <= now < authorization.expires_at
                or self.authority_provider is None
                or self.authority_provider.is_current(authorization, now) is not True):
            raise OperationRejected("authority absent/stale/mismatched")

    def inspect(self, authorization):
        """Read-only historical interpretation. Never completes unfinished work."""
        intent = authorization.intent
        row = self.operations.read(authorization)
        if row is None:
            return PhaseBranchResult("NOT_DISPATCHED", intent.effect_id, "no operation")
        chain = self.operations.terminal_chain(authorization)
        if chain and chain["outcome"] in {"VERIFIED_SUCCESS", "PROVEN_NO_EFFECT"}:
            # Registry/phase drift must not redirect historical Git observations.
            observation = self.locks.observe(_lock_decode_snapshot(row["grant_json"]).resource)
            if observation.code.value not in {"OBSERVED"}:
                raise OperationRejected("historical resource unavailable")
            current = self.adapter.observe(intent)
            saved = _saved_observation(canonical(row["events"]["OUTCOME"]["post"]))
            if not self.adapter.unchanged(saved, current):
                return PhaseBranchResult("RECONCILIATION_REQUIRED", intent.effect_id, "historical repository drift")
            return PhaseBranchResult(chain["outcome"], intent.effect_id, "historical terminal; no dispatch", chain["reference"])
        code = "MAY_HAVE_DISPATCHED_RECONCILIATION_REQUIRED" if "DISPATCH" in row["events"] else "NOT_DISPATCHED"
        return PhaseBranchResult(code, intent.effect_id, "retained operation; restart continuation prohibited")

    def create(self, authorization, grant):
        effect_id = None
        prepared = dispatched = False
        reserved = None
        try:
            if type(authorization) is not EffectAuthorization:
                raise OperationRejected("missing effect authorization")
            intent = authorization.intent
            effect_id = intent.effect_id
            authorization.__post_init__()
            existing = self.operations.read(authorization)
            if existing is not None:
                prepared = True
                dispatched = "DISPATCH" in existing["events"]
                return self.inspect(authorization)
            self._authorized(authorization)
            self.operations.check(authorization, grant)
            pre = self.adapter.observe(intent)
            if pre.target_state != "TARGET_REF_DEFINITELY_ABSENT":
                return PhaseBranchResult("NO_DISPATCH", effect_id, pre.target_state)
            _boundary("C0")
            prepared = self.operations.prepare(authorization, grant, pre)
            if not prepared:
                return self.inspect(authorization)
            _boundary("C1")
            _boundary("C2")
            result = self.locks.reserve_effect_window(grant.resource, grant.owner, grant.acquisition_id,
                                                     grant.fencing_token, grant.revision)
            if result.code.value != "EFFECT_RESERVED":
                raise OperationRejected("reservation failed: " + result.code.value)
            reserved = result.grant
            _boundary("C3")
            self.operations.reserved(authorization, reserved)
            self._authorized(authorization)
            fresh = self.adapter.observe(intent)
            if not self.adapter.unchanged(pre, fresh):
                raise OperationRejected("pre-dispatch drift")
            self.operations.check(authorization, reserved)
            self._authorized(authorization)
            _boundary("C4")
            # Any exception during consume is ambiguous; it never yields a permit.
            dispatched = True
            permission = self.operations.consume(authorization)
            _boundary("C5")
            try:
                fresh = self.adapter.observe(intent)
                if not self.adapter.unchanged(pre, fresh):
                    raise OperationRejected("pre-launch drift")
                self.operations.check(authorization, reserved)
                self._authorized(authorization)
            except Exception:
                permission.take(intent)  # Irrevocably destroy the unused permit.
                stamp = self.clock().isoformat()
                process = ProcessEvidence(ACTION, _argv(ACTION, intent), intent.policy.project_root,
                    stamp, stamp, None, b"", b"", False, "NOT_STARTED", "PRELAUNCH_REJECTED", "PRELAUNCH_REJECTED", False)
            else:
                _boundary("C6")
                try:
                    process = self.adapter.launch(intent, permission)
                except Exception:
                    stamp = self.clock().isoformat()
                    process = ProcessEvidence(ACTION, _argv(ACTION, intent), intent.policy.project_root,
                        stamp, stamp, None, b"", b"", False, "FAILED", "PROCESS_RESULT_LOST", "PROCESS_RESULT_LOST", True)
            _boundary("C8")
            post_error = None
            try:
                post = self.adapter.observe(intent)
            except Exception as error:
                post = None
                post_error = type(error).__name__ + ": " + str(error)[:256]
            outcome = "UNKNOWN"
            if (process.operation == ACTION and process.argv == _argv(ACTION, intent)
                    and process.cwd == intent.policy.project_root and process.quiescent and post is not None):
                if (process.complete and process.reason == "OBSERVED" and process.exit_code == 0
                        and process.may_have_executed and self.adapter.expected_creation(intent, pre, post)):
                    outcome = "VERIFIED_SUCCESS"
                elif not process.may_have_executed and self.adapter.unchanged(pre, post):
                    outcome = "PROVEN_NO_EFFECT"
            payload = {"outcome": outcome, "process": asdict(process),
                       "post": None if post is None else asdict(post), "post_error": post_error}
            reference = self.operations.outcome(authorization, payload)
            _boundary("C9")
            terminal = self.locks.terminalize_effect_reservation(reserved.resource, reserved.owner,
                reserved.acquisition_id, reserved.fencing_token, reserved.revision,
                LockTerminalEvidence(LockTerminalOutcome(outcome), reference))
            expected_code = "RECONCILIATION_REQUIRED" if outcome == "UNKNOWN" else "RELEASED"
            if terminal.code.value != expected_code:
                raise OperationRejected("terminalization failed: " + terminal.code.value)
            _boundary("C10")
            chain = self.operations.acknowledge_terminal(authorization)
            return PhaseBranchResult("RECONCILIATION_REQUIRED" if outcome == "UNKNOWN" else outcome,
                                     effect_id, "durable evidence and lock agree", chain["reference"])
        except Exception as error:
            # Never guess whether an interrupted DB/process action completed, and
            # never issue cleanup/recovery writes from this generic error path.
            code = ("MAY_HAVE_DISPATCHED_RECONCILIATION_REQUIRED" if dispatched else
                    "NOT_DISPATCHED" if prepared else "NO_DISPATCH")
            return PhaseBranchResult(code, effect_id, type(error).__name__ + ": " + str(error)[:256])
