"""Closed DL-2.6 values. Constructing a value never establishes Human authority."""

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import base64
import hashlib
import json
import re
import threading
from typing import Protocol
from uuid import UUID

from .git_inspection_models import GitObjectIdentity, _path
from .models import LockOwner, PhaseContract, ProjectPolicy

ACTION = "CREATE_LOCAL_PHASE_BRANCH"
MAX_EVIDENCE = 8 * 1024 * 1024
_DEVICES = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(10)),
            *(f"LPT{i}" for i in range(10))}


def canonical(value):
    def encode(item):
        if isinstance(item, bytes):
            return {"base64": base64.b64encode(item).decode("ascii")}
        raise TypeError(type(item).__name__)
    result = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                        allow_nan=False, default=encode)
    if len(result.encode("ascii")) > MAX_EVIDENCE:
        raise ValueError("bounded evidence exceeded")
    return result


def digest(value):
    return hashlib.sha256(value.encode("ascii")).hexdigest()


def _uuid(value):
    if type(value) is not str or str(UUID(value)) != value:
        raise ValueError("canonical UUID required")


def _sha(value):
    if type(value) is not str or re.fullmatch("[0-9a-f]{64}", value) is None:
        raise ValueError("SHA256 required")


def validate_ref(value, *, target=False):
    # Deliberately conservative ASCII profile; rejection never rewrites a ref.
    if (type(value) is not str or len(value) > 240 or
            not value.startswith("refs/heads/phase/" if target else "refs/") or
            not re.fullmatch(r"[A-Za-z0-9_./-]+", value) or ".." in value):
        raise ValueError("unsupported exact ref")
    for part in value.split("/"):
        if (not part or part.startswith((".", "-")) or part.endswith((".", ".lock"))
                or part.split(".", 1)[0].upper() in _DEVICES):
            raise ValueError("unsafe ref component")
    return value


def instant(value):
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset().total_seconds() != 0:
        raise ValueError("UTC instant required")
    return value.astimezone(timezone.utc)


@dataclass(frozen=True)
class PhaseBranchIntent:
    effect_id: str
    authority_id: str
    phase: PhaseContract
    policy: ProjectPolicy
    source_ref: str
    source_commit: GitObjectIdentity
    target_ref: str
    owner: LockOwner
    command_schema_version: int
    command_state_version: int
    command_intent_digest: str
    exclusions: tuple[str, ...] = ()

    def __post_init__(self):
        _uuid(self.effect_id)
        if not isinstance(self.authority_id, str) or not 1 <= len(self.authority_id) <= 256:
            raise ValueError("authority identity")
        for value, cls in ((self.phase, PhaseContract), (self.policy, ProjectPolicy),
                           (self.owner, LockOwner), (self.source_commit, GitObjectIdentity)):
            if type(value) is not cls:
                raise ValueError("exact immutable contract required")
            value.__post_init__()
        if self.phase.project_id != self.policy.project_id or self.source_commit.object_type != "commit":
            raise ValueError("project/commit binding")
        validate_ref(self.source_ref)
        if not self.source_ref.startswith("refs/heads/"):
            raise ValueError("attached source required")
        validate_ref(self.target_ref, target=True)
        if self.source_ref.casefold() == self.target_ref.casefold():
            raise ValueError("source is target")
        for version in (self.command_schema_version, self.command_state_version):
            if type(version) is not int or not 1 <= version < 2**63:
                raise ValueError("command version")
        _sha(self.command_intent_digest)
        if type(self.exclusions) is not tuple or len(self.exclusions) > 128:
            raise ValueError("exclusions")
        for path in self.exclusions:
            _path(path)
            if path.casefold().split("/")[0] == ".git":
                raise ValueError("required Git metadata cannot be excluded")

    def canonical_json(self):
        self.__post_init__()
        return canonical(asdict(self))

    def digest(self):
        return digest(self.canonical_json())


@dataclass(frozen=True)
class EffectAuthorization:
    intent: PhaseBranchIntent
    valid_from: datetime
    expires_at: datetime
    action: str = ACTION
    attempts: int = 1

    def __post_init__(self):
        if type(self.intent) is not PhaseBranchIntent:
            raise ValueError("intent")
        self.intent.__post_init__()
        if (self.action != ACTION or type(self.attempts) is not int or self.attempts != 1
                or instant(self.valid_from) >= instant(self.expires_at)):
            raise ValueError("authority scope")

    def canonical_json(self):
        self.__post_init__()
        return canonical({"intent": json.loads(self.intent.canonical_json()),
                          "intent_digest": self.intent.digest(), "action": self.action,
                          "attempts": self.attempts, "valid_from": self.valid_from.isoformat(),
                          "expires_at": self.expires_at.isoformat()})


class EffectAuthorityProvider(Protocol):
    """Installed by trusted composition, independently of the operation caller.

    Must authenticate the exact context and check current invalidation/revocation.
    No production provider or caller-installable endorsement is supplied here.
    """
    def is_current(self, authorization: EffectAuthorization, now: datetime) -> bool: ...


@dataclass(frozen=True)
class ProcessEvidence:
    operation: str
    argv: tuple[str, ...]
    cwd: str
    started_at: str
    ended_at: str
    exit_code: int | None
    stdout: bytes
    stderr: bytes
    complete: bool
    cleanup: str
    reason: str
    primary_reason: str
    may_have_executed: bool

    @property
    def quiescent(self):
        return self.cleanup in {"OWNED_JOB_EMPTY", "SUSPENDED_PROCESS_TERMINATED", "NOT_STARTED"}


@dataclass(frozen=True)
class RepositoryObservation:
    state_json: str
    target_state: str
    commands: tuple[ProcessEvidence, ...]

    def __post_init__(self):
        if canonical(json.loads(self.state_json)) != self.state_json:
            raise ValueError("canonical observation")
        if self.target_state not in {"TARGET_REF_DEFINITELY_ABSENT", "TARGET_REF_PRESENT",
                "TARGET_REF_COLLISION", "TARGET_REF_OBSERVATION_FAILED_OR_UNRESOLVED"}:
            raise ValueError("target state")

    def canonical_json(self):
        self.__post_init__()
        return canonical(asdict(self))


@dataclass(frozen=True)
class PhaseBranchResult:
    code: str
    effect_id: str | None
    detail: str
    evidence_reference: str | None = None


class OperationRejected(RuntimeError):
    pass


class TargetObservationUnavailable(OperationRejected):
    target_state = "TARGET_REF_OBSERVATION_FAILED_OR_UNRESOLVED"

    def __init__(self, detail):
        super().__init__(self.target_state + ": " + detail)


_PERMIT_KEY = object()


class _LaunchPermission:
    """Process-local capability; never serialized, reconstructed or returned publicly."""
    def __init__(self, key, intent_digest):
        if key is not _PERMIT_KEY:
            raise OperationRejected("no dispatch grant")
        self._digest, self._used, self._lock = intent_digest, False, threading.Lock()

    def take(self, intent):
        with self._lock:
            if self._used:
                raise OperationRejected("dispatch already spent")
            self._used = True
            if self._digest != intent.digest():
                raise OperationRejected("dispatch intent mismatch")


def _issue_permission(intent):
    return _LaunchPermission(_PERMIT_KEY, intent.digest())
