"""Pure, non-persistent DL-2.4 inspection values (independent of workflow state)."""

from dataclasses import asdict, dataclass
from enum import Enum
import hashlib
import json
import re


PROFILE = "WINDOWS_LOCAL_ATTACHED_V1"
CONTRACT_SHA256 = "52e196303d56c07c888452eab652af80e35844525b9538091a733c68d7991655"


class InspectionStatus(str, Enum):
    COMPLETE = "COMPLETE"
    REJECTED = "REJECTED"
    UNAVAILABLE = "UNAVAILABLE"
    DRIFT = "DRIFT"


class InspectionReason(str, Enum):
    OBSERVED = "OBSERVED"
    INVALID_REQUEST = "INVALID_REQUEST"
    PROJECT_NOT_REGISTERED = "PROJECT_NOT_REGISTERED"
    INVALID_POLICY = "INVALID_POLICY"
    STORAGE_FAILURE = "STORAGE_FAILURE"
    INVALID_CONTEXT = "INVALID_CONTEXT"
    UNSUPPORTED_CONTEXT_VERSION = "UNSUPPORTED_CONTEXT_VERSION"
    UNENDORSED_CONTEXT = "UNENDORSED_CONTEXT"
    PROJECT_MISMATCH = "PROJECT_MISMATCH"
    POLICY_VERSION_MISMATCH = "POLICY_VERSION_MISMATCH"
    ROOT_MISMATCH = "ROOT_MISMATCH"
    REPOSITORY_MISMATCH = "REPOSITORY_MISMATCH"
    REMOTE_MISMATCH = "REMOTE_MISMATCH"
    BRANCH_MISMATCH = "BRANCH_MISMATCH"
    BASELINE_MISMATCH = "BASELINE_MISMATCH"
    SCOPE_MISMATCH = "SCOPE_MISMATCH"
    UNSUPPORTED_PROFILE = "UNSUPPORTED_PROFILE"
    UNSUPPORTED_LAYOUT = "UNSUPPORTED_LAYOUT"
    UNSUPPORTED_CONFIGURATION = "UNSUPPORTED_CONFIGURATION"
    DETACHED = "DETACHED"
    UNBORN = "UNBORN"
    GIT_UNAVAILABLE = "GIT_UNAVAILABLE"
    COMMAND_TIMEOUT = "COMMAND_TIMEOUT"
    OUTPUT_OVERFLOW = "OUTPUT_OVERFLOW"
    MALFORMED_OUTPUT = "MALFORMED_OUTPUT"
    REQUIRED_EVIDENCE_UNAVAILABLE = "REQUIRED_EVIDENCE_UNAVAILABLE"
    PROCESS_LIMIT = "PROCESS_LIMIT"
    INSPECTION_DEADLINE = "INSPECTION_DEADLINE"
    CLEANUP_FAILURE = "CLEANUP_FAILURE"
    STATE_DRIFT = "STATE_DRIFT"


def _text(value: str) -> None:
    if type(value) is not str or not value or value != value.strip() or len(value) > 4096 or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise ValueError("nonempty bounded text required")


def _tuple(value: tuple, kind: type) -> None:
    if type(value) is not tuple or any(type(item) is not kind for item in value):
        raise ValueError("exact immutable tuple required")


def _path(value: str) -> None:
    _text(value)
    if (value.startswith(("/", "\\")) or "\\" in value or ":" in value
            or any(part in ("", ".", "..") for part in value.split("/"))
            or any(c in value for c in '*?[]<>|"')
            or any(part.endswith((".", " ")) for part in value.split("/"))):
        raise ValueError("exact relative path required")
    if any(re.fullmatch(r"(?i)(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?", p) for p in value.split("/")):
        raise ValueError("reserved Windows path")


@dataclass(frozen=True, slots=True)
class ByteIdentity:
    domain: str
    locator: str
    sha256: str
    byte_length: int

    def __post_init__(self) -> None:
        _text(self.domain)
        _text(self.locator)
        if type(self.sha256) is not str or not re.fullmatch("[0-9a-f]{64}", self.sha256):
            raise ValueError("SHA-256 required")
        if type(self.byte_length) is not int or self.byte_length < 0:
            raise ValueError("byte length required")

    @classmethod
    def of(cls, domain: str, locator: str, payload: bytes) -> "ByteIdentity":
        return cls(domain, locator, hashlib.sha256(payload).hexdigest(), len(payload))


@dataclass(frozen=True, slots=True)
class GitObjectIdentity:
    algorithm: str
    object_type: str
    oid: str

    def __post_init__(self) -> None:
        length = {"sha1": 40, "sha256": 64}.get(self.algorithm)
        if (length is None or self.object_type not in ("commit", "tree", "blob")
                or type(self.oid) is not str or not re.fullmatch("[0-9a-f]{" + str(length) + "}", self.oid)):
            raise ValueError("typed exact Git object required")


@dataclass(frozen=True, slots=True)
class RepositoryExpectation:
    git_directory_binding: str
    object_format: str

    def __post_init__(self) -> None:
        _text(self.git_directory_binding)
        if self.object_format not in ("sha1", "sha256"):
            raise ValueError("unsupported object format")


@dataclass(frozen=True, slots=True)
class RemoteBinding:
    name: str
    urls: tuple[str, ...]
    push_urls: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _text(self.name)
        _tuple(self.urls, str)
        _tuple(self.push_urls, str)
        if not self.urls:
            raise ValueError("remote requires explicit URL")
        for url in self.urls + self.push_urls:
            _text(url)


@dataclass(frozen=True, slots=True)
class BaselineConstraint:
    mode: str
    head: GitObjectIdentity | None

    def __post_init__(self) -> None:
        if self.mode == "REQUIRED":
            if type(self.head) is not GitObjectIdentity or self.head.object_type != "commit":
                raise ValueError("required baseline needs typed commit")
        elif self.mode != "NOT_REQUIRED_FOR_THIS_PROFILE" or self.head is not None:
            raise ValueError("explicit baseline mode required")


@dataclass(frozen=True, slots=True)
class TrustedGitInspectionContext:
    context_id: str
    context_version: str
    authority_or_policy_binding: str
    project_id: str
    project_policy_version: str
    registered_project_root_binding: str
    expected_repository_identity: RepositoryExpectation
    expected_configured_remote_bindings: tuple[RemoteBinding, ...]
    additional_remotes: str
    expected_branch_constraint: str
    expected_baseline_constraint: BaselineConstraint
    approved_read_path_scope: tuple[str, ...]
    approved_exclusions: tuple[str, ...]
    inspection_profile_id: str

    def __post_init__(self) -> None:
        for name in ("context_id", "context_version", "authority_or_policy_binding", "project_id",
                     "project_policy_version", "registered_project_root_binding",
                     "expected_branch_constraint", "inspection_profile_id"):
            _text(getattr(self, name))
        if type(self.expected_repository_identity) is not RepositoryExpectation or type(self.expected_baseline_constraint) is not BaselineConstraint:
            raise ValueError("exact immutable expectations required")
        _tuple(self.expected_configured_remote_bindings, RemoteBinding)
        _tuple(self.approved_read_path_scope, str)
        _tuple(self.approved_exclusions, str)
        if self.additional_remotes not in ("FORBIDDEN", "REPORT_ONLY"):
            raise ValueError("explicit additional-remote policy required")
        if len({r.name for r in self.expected_configured_remote_bindings}) != len(self.expected_configured_remote_bindings):
            raise ValueError("duplicate remote")
        if len(self.approved_read_path_scope) > 32 or len(self.approved_exclusions) > 128:
            raise ValueError("scope limit")
        for path in self.approved_read_path_scope + self.approved_exclusions:
            _path(path)
        for branch_part in self.expected_branch_constraint.split("/"):
            if (branch_part in ("", ".", "..") or branch_part.startswith((".", "-"))
                    or branch_part.endswith((".", ".lock")) or ".." in branch_part
                    or any(c in branch_part for c in ' ~^:?*[\\') or "@{" in branch_part):
                raise ValueError("exact attached branch required")
        if self.expected_branch_constraint == "@":
            raise ValueError("exact attached branch required")
        if len(set(p.casefold() for p in self.approved_read_path_scope)) != len(self.approved_read_path_scope):
            raise ValueError("ambiguous scope")
        for path in self.approved_read_path_scope:
            if path.split("/")[0].casefold() == ".git" or any(
                path.casefold() == excluded.casefold() or path.casefold().startswith(excluded.casefold() + "/")
                for excluded in self.approved_exclusions
            ):
                raise ValueError("contradictory scope")

    def canonical_bytes(self) -> bytes:
        return json.dumps(asdict(self), sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")

    def identity(self) -> ByteIdentity:
        return ByteIdentity.of("git-inspection-context-v1", self.context_id, self.canonical_bytes())


@dataclass(frozen=True, slots=True)
class InspectionEndorsement:
    """Independently installed composition input; never an inspect-call parameter.

    Its supplier is the trusted outer application/human authority boundary.
    Constructing this value or hashing a context does not confer that authority.
    Production provisioning/authentication of that supplier is outside DL-2.4.
    """

    human_authority_id: str
    implementation_contract_sha256: str
    project_id: str
    context_id: str
    context_sha256: str
    context_byte_length: int

    def __post_init__(self) -> None:
        for value in (self.human_authority_id, self.project_id, self.context_id):
            _text(value)
        for digest in (self.implementation_contract_sha256, self.context_sha256):
            if type(digest) is not str or not re.fullmatch("[0-9a-f]{64}", digest):
                raise ValueError("exact SHA-256 required")
        if type(self.context_byte_length) is not int or self.context_byte_length <= 0:
            raise ValueError("context byte length required")


@dataclass(frozen=True, slots=True)
class GitInspectionRequest:
    project_id: str
    context: TrustedGitInspectionContext | None
    version: str = "1"

    def __post_init__(self) -> None:
        _text(self.project_id)
        if self.context is not None and type(self.context) is not TrustedGitInspectionContext:
            raise ValueError("exact context required")
        _text(self.version)


@dataclass(frozen=True, slots=True)
class CommandObservation:
    operation: str
    argv: tuple[str, ...]
    cwd: str
    started_at: str
    ended_at: str
    exit_code: int | None
    stdout: bytes
    stderr: bytes
    complete_capture: bool
    cleanup: str
    reason: InspectionReason
    primary_reason: InspectionReason = InspectionReason.OBSERVED

    def __post_init__(self) -> None:
        _tuple(self.argv, str)
        if type(self.stdout) is not bytes or type(self.stderr) is not bytes:
            raise ValueError("immutable capture bytes required")

    @property
    def stdout_identity(self) -> ByteIdentity:
        return ByteIdentity.of("git-command-stdout", self.operation, self.stdout)

    @property
    def stderr_identity(self) -> ByteIdentity:
        return ByteIdentity.of("git-command-stderr", self.operation, self.stderr)


@dataclass(frozen=True, slots=True)
class IndexEntry:
    path: str
    mode: str
    oid: str
    stage: int


@dataclass(frozen=True, slots=True)
class PathChange:
    path: str
    status: str


@dataclass(frozen=True, slots=True)
class GitObservation:
    root: str
    object_format: str
    branch: str | None
    head: GitObjectIdentity | None
    tree: GitObjectIdentity | None
    index: tuple[IndexEntry, ...]
    staged: tuple[PathChange, ...]
    unstaged_in_scope: tuple[PathChange, ...]
    untracked_names: tuple[str, ...]
    conflicts: tuple[str, ...]
    remotes: tuple[RemoteBinding, ...]
    tracking_configuration: tuple[tuple[str, str], ...]
    upstream: str | None
    local_divergence: tuple[int, int] | None
    approved_file_identities: tuple[ByteIdentity, ...]
    staged_diff: bytes
    unstaged_diff: bytes
    metadata_identity: ByteIdentity
    content_unassessed_paths: tuple[str, ...]
    tracking_scope: str = "LOCAL_REFS_ONLY_NOT_REMOTE_FRESHNESS"

    def __post_init__(self) -> None:
        for name, kind in (("index", IndexEntry), ("staged", PathChange),
                           ("unstaged_in_scope", PathChange), ("untracked_names", str),
                           ("conflicts", str), ("remotes", RemoteBinding),
                           ("approved_file_identities", ByteIdentity), ("content_unassessed_paths", str)):
            _tuple(getattr(self, name), kind)
        _tuple(self.tracking_configuration, tuple)
        for pair in self.tracking_configuration:
            _tuple(pair, str)
            if len(pair) != 2:
                raise ValueError("key/value pair required")
        if self.local_divergence is not None:
            _tuple(self.local_divergence, int)
            if len(self.local_divergence) != 2 or min(self.local_divergence) < 0:
                raise ValueError("local divergence pair required")
        if type(self.staged_diff) is not bytes or type(self.unstaged_diff) is not bytes:
            raise ValueError("immutable diff bytes required")


@dataclass(frozen=True, slots=True)
class GitInspectionResult:
    status: InspectionStatus
    reason: InspectionReason
    project_id: str
    policy_binding: tuple[str, str, str] | None
    context_identity: ByteIdentity | None
    started_at: str
    ended_at: str
    observations: tuple[GitObservation, ...] = ()
    commands: tuple[CommandObservation, ...] = ()
    limitations: tuple[str, ...] = ()
    version: str = "1"
    authority_granted: bool = False

    def __post_init__(self) -> None:
        _tuple(self.observations, GitObservation)
        _tuple(self.commands, CommandObservation)
        _tuple(self.limitations, str)
        if self.policy_binding is not None:
            if type(self.policy_binding) is not tuple or len(self.policy_binding) != 3 or any(not isinstance(v, str) for v in self.policy_binding):
                raise ValueError("policy tuple required")
        if self.authority_granted is not False:
            raise ValueError("inspection grants no authority")
