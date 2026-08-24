"""Domain values for the bounded DL-P1.1 and DL-P1.2 slices."""

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional


class DevelopmentRunState(str, Enum):
    """The canonical states used by the bounded DL-P1.1 transition slice."""

    DRAFT = "DRAFT"
    FEASIBILITY_CHECKING = "FEASIBILITY_CHECKING"
    AWAITING_EXECUTION_APPROVAL = "AWAITING_EXECUTION_APPROVAL"
    SOURCE_COMPLETED = "SOURCE_COMPLETED"


class TransitionReasonCode(str, Enum):
    ACCEPTED = "ACCEPTED"
    RUN_NOT_FOUND = "RUN_NOT_FOUND"
    STALE_EXPECTED_STATE = "STALE_EXPECTED_STATE"
    TRANSITION_NOT_ALLOWED = "TRANSITION_NOT_ALLOWED"


class ContractVersion(str, Enum):
    """The only contract schema version supported by the DL-P1.2 slice."""

    V1 = "1"


class ProjectPolicyVersion(str, Enum):
    """The only Project Policy schema version supported by DL-P1.8."""

    V1 = "1"


class ContractValidationCode(str, Enum):
    """Stable reasons for rejecting an invalid domain contract."""

    UNSUPPORTED_VERSION = "UNSUPPORTED_VERSION"
    INVALID_IDENTITY = "INVALID_IDENTITY"
    EMPTY_REQUIRED_VALUE = "EMPTY_REQUIRED_VALUE"
    DUPLICATE_VALUE = "DUPLICATE_VALUE"
    INVALID_PATH = "INVALID_PATH"
    PATH_POLICY_COLLISION = "PATH_POLICY_COLLISION"


class ContractValidationError(ValueError):
    """Raised when a PhaseContract or MilestoneContract is invalid."""

    def __init__(self, code: ContractValidationCode, field_name: str) -> None:
        self.code = code
        self.field_name = field_name
        super().__init__(f"{code.value}: {field_name}")


def _validate_text(value: object, field_name: str, code: ContractValidationCode) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ContractValidationError(code, field_name)
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ContractValidationError(code, field_name)
    return value


def _validate_identity(value: object, field_name: str) -> str:
    return _validate_text(value, field_name, ContractValidationCode.INVALID_IDENTITY)


def _validate_required_text(value: object, field_name: str) -> str:
    return _validate_text(value, field_name, ContractValidationCode.EMPTY_REQUIRED_VALUE)


def _coerce_contract_version(value: object) -> ContractVersion:
    if isinstance(value, ContractVersion):
        return value
    if isinstance(value, str):
        try:
            return ContractVersion(value)
        except ValueError:
            pass
    raise ContractValidationError(ContractValidationCode.UNSUPPORTED_VERSION, "contract_version")


def _coerce_project_policy_version(value: object) -> ProjectPolicyVersion:
    if isinstance(value, ProjectPolicyVersion):
        return value
    if isinstance(value, str):
        try:
            return ProjectPolicyVersion(value)
        except ValueError:
            pass
    raise ContractValidationError(ContractValidationCode.UNSUPPORTED_VERSION, "policy_version")


_WINDOWS_RESERVED_DEVICE_NAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"}
)


def _is_reserved_windows_path_segment(segment: str) -> bool:
    base_name = segment.split(".", 1)[0].rstrip(" ").upper()
    return base_name in _WINDOWS_RESERVED_DEVICE_NAMES or bool(
        re.fullmatch(
            r"(?:COM|LPT)(?:[1-9]|\u00b9|\u00b2|\u00b3)",
            base_name,
        )
    )


def _validate_project_root(value: object) -> str:
    root = _validate_required_text(value, "project_root")
    if len(root) < 3:
        raise ContractValidationError(ContractValidationCode.INVALID_PATH, "project_root")
    ascii_drive_letter = ("A" <= root[0] <= "Z") or ("a" <= root[0] <= "z")
    if not ascii_drive_letter or root[1] != ":" or root[2] not in "\\/":
        raise ContractValidationError(ContractValidationCode.INVALID_PATH, "project_root")

    normalized = root.replace("/", "\\")
    remainder = normalized[3:]
    if not remainder:
        return normalized
    if remainder.endswith("\\"):
        normalized = normalized[:-1]
        remainder = remainder[:-1]

    segments = remainder.split("\\")
    if any(
        not segment
        or segment in {".", ".."}
        or segment[-1] in {" ", "."}
        or any(character in '<>:"|?*' for character in segment)
        or _is_reserved_windows_path_segment(segment)
        for segment in segments
    ):
        raise ContractValidationError(ContractValidationCode.INVALID_PATH, "project_root")
    return normalized


def _validate_values(
    value: object,
    field_name: str,
    *,
    required: bool,
    ordered: bool,
) -> tuple[str, ...]:
    if not isinstance(value, tuple):
        raise ContractValidationError(ContractValidationCode.EMPTY_REQUIRED_VALUE, field_name)
    if required and not value:
        raise ContractValidationError(ContractValidationCode.EMPTY_REQUIRED_VALUE, field_name)

    values = tuple(_validate_required_text(item, field_name) for item in value)
    if len(set(values)) != len(values):
        raise ContractValidationError(ContractValidationCode.DUPLICATE_VALUE, field_name)
    return values if ordered else tuple(sorted(values))


def _validate_path(value: object, field_name: str) -> str:
    path = _validate_required_text(value, field_name)
    if "\\" in path or path.startswith("/") or path.startswith("//"):
        raise ContractValidationError(ContractValidationCode.INVALID_PATH, field_name)
    if len(path) >= 2 and path[0].isalpha() and path[1] == ":":
        raise ContractValidationError(ContractValidationCode.INVALID_PATH, field_name)
    if any(character in path for character in "*?[]{}"):
        raise ContractValidationError(ContractValidationCode.INVALID_PATH, field_name)

    segments = path.split("/")
    if any(not segment or segment in {".", ".."} for segment in segments):
        raise ContractValidationError(ContractValidationCode.INVALID_PATH, field_name)
    return path


def _validate_paths(value: object, field_name: str, *, required: bool) -> tuple[str, ...]:
    if not isinstance(value, tuple):
        raise ContractValidationError(ContractValidationCode.EMPTY_REQUIRED_VALUE, field_name)
    if required and not value:
        raise ContractValidationError(ContractValidationCode.EMPTY_REQUIRED_VALUE, field_name)

    paths = tuple(_validate_path(item, field_name) for item in value)
    if len(set(paths)) != len(paths):
        raise ContractValidationError(ContractValidationCode.DUPLICATE_VALUE, field_name)
    return tuple(sorted(paths))


def _paths_collide(left: str, right: str) -> bool:
    return left == right or left.startswith(f"{right}/") or right.startswith(f"{left}/")


def _canonical_json(payload: dict[str, object]) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


@dataclass(frozen=True)
class PhaseContract:
    """A pure, versioned identity contract for one development phase."""

    project_id: str
    phase_id: str
    contract_version: ContractVersion | str

    def __post_init__(self) -> None:
        object.__setattr__(self, "project_id", _validate_identity(self.project_id, "project_id"))
        object.__setattr__(self, "phase_id", _validate_identity(self.phase_id, "phase_id"))
        object.__setattr__(self, "contract_version", _coerce_contract_version(self.contract_version))

    def canonical_json(self) -> str:
        return _canonical_json(
            {
                "contract_version": self.contract_version.value,
                "phase_id": self.phase_id,
                "project_id": self.project_id,
            }
        )

    def sha256_digest(self) -> str:
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class MilestoneContract:
    """A pure, versioned scope contract for one development milestone."""

    project_id: str
    phase_id: str
    milestone_id: str
    contract_version: ContractVersion | str
    objective: str
    scope: tuple[str, ...]
    exclusions: tuple[str, ...]
    acceptance_criteria: tuple[str, ...]
    allowed_paths: tuple[str, ...]
    forbidden_paths: tuple[str, ...]
    verification_plan: tuple[str, ...]
    stop_conditions: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "project_id", _validate_identity(self.project_id, "project_id"))
        object.__setattr__(self, "phase_id", _validate_identity(self.phase_id, "phase_id"))
        object.__setattr__(self, "milestone_id", _validate_identity(self.milestone_id, "milestone_id"))
        object.__setattr__(self, "contract_version", _coerce_contract_version(self.contract_version))
        object.__setattr__(self, "objective", _validate_required_text(self.objective, "objective"))
        object.__setattr__(self, "scope", _validate_values(self.scope, "scope", required=True, ordered=False))
        object.__setattr__(self, "exclusions", _validate_values(self.exclusions, "exclusions", required=False, ordered=False))
        object.__setattr__(
            self,
            "acceptance_criteria",
            _validate_values(self.acceptance_criteria, "acceptance_criteria", required=True, ordered=False),
        )
        object.__setattr__(self, "allowed_paths", _validate_paths(self.allowed_paths, "allowed_paths", required=True))
        object.__setattr__(self, "forbidden_paths", _validate_paths(self.forbidden_paths, "forbidden_paths", required=False))
        object.__setattr__(
            self,
            "verification_plan",
            _validate_values(self.verification_plan, "verification_plan", required=True, ordered=True),
        )
        object.__setattr__(
            self,
            "stop_conditions",
            _validate_values(self.stop_conditions, "stop_conditions", required=True, ordered=False),
        )

        for allowed_path in self.allowed_paths:
            for forbidden_path in self.forbidden_paths:
                if _paths_collide(allowed_path, forbidden_path):
                    raise ContractValidationError(
                        ContractValidationCode.PATH_POLICY_COLLISION,
                        "allowed_paths/forbidden_paths",
                    )

    def canonical_json(self) -> str:
        return _canonical_json(
            {
                "acceptance_criteria": list(self.acceptance_criteria),
                "allowed_paths": list(self.allowed_paths),
                "contract_version": self.contract_version.value,
                "exclusions": list(self.exclusions),
                "forbidden_paths": list(self.forbidden_paths),
                "milestone_id": self.milestone_id,
                "objective": self.objective,
                "phase_id": self.phase_id,
                "project_id": self.project_id,
                "scope": list(self.scope),
                "stop_conditions": list(self.stop_conditions),
                "verification_plan": list(self.verification_plan),
            }
        )

    def sha256_digest(self) -> str:
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ProjectPolicy:
    """The closed, immutable Project Policy v1 value read by DL-P1.8."""

    project_id: str
    policy_version: ProjectPolicyVersion | str
    project_root: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "project_id", _validate_identity(self.project_id, "project_id"))
        object.__setattr__(
            self,
            "policy_version",
            _coerce_project_policy_version(self.policy_version),
        )
        object.__setattr__(self, "project_root", _validate_project_root(self.project_root))


class ProjectPolicyReadOutcome(str, Enum):
    """Closed public outcomes for a valid-identity Project Registry read."""

    VALID_POLICY = "VALID_POLICY"
    PROJECT_NOT_REGISTERED = "PROJECT_NOT_REGISTERED"
    INVALID_POLICY = "INVALID_POLICY"
    STORAGE_FAILURE = "STORAGE_FAILURE"


@dataclass(frozen=True)
class ProjectPolicyReadResult:
    """A read outcome carrying a validated policy only when it is valid."""

    outcome: ProjectPolicyReadOutcome
    policy: ProjectPolicy | None = None

    def __post_init__(self) -> None:
        if type(self.outcome) is not ProjectPolicyReadOutcome:
            raise TypeError("outcome")
        if self.outcome is ProjectPolicyReadOutcome.VALID_POLICY:
            if type(self.policy) is not ProjectPolicy:
                raise ValueError("policy")
        elif self.policy is not None:
            raise ValueError("policy")


class ApprovalVersion(str, Enum):
    """The approval-model version supported by the DL-P1.3 slice."""

    V1 = "1"


class ApprovalKind(str, Enum):
    """Stable discriminators for the architecture-recognized approval gates."""

    PHASE_START_APPROVAL = "PHASE_START_APPROVAL"
    APPROVAL_1 = "APPROVAL_1"
    APPROVAL_2 = "APPROVAL_2"


class ApprovalTargetKind(str, Enum):
    """Stable discriminators for an approval's external target."""

    SOURCE_REPOSITORY = "SOURCE_REPOSITORY"
    VAULT = "VAULT"


class ApprovalValidationCode(str, Enum):
    """Stable reasons for rejecting an invalid approval binding."""

    UNSUPPORTED_VERSION = "UNSUPPORTED_VERSION"
    INVALID_KIND = "INVALID_KIND"
    INVALID_IDENTITY = "INVALID_IDENTITY"
    EMPTY_REQUIRED_VALUE = "EMPTY_REQUIRED_VALUE"
    INVALID_DIGEST = "INVALID_DIGEST"
    DUPLICATE_VALUE = "DUPLICATE_VALUE"
    INVALID_PATH = "INVALID_PATH"


class ApprovalValidationError(ValueError):
    """Raised when an ApprovalBinding is invalid."""

    def __init__(self, code: ApprovalValidationCode, field_name: str) -> None:
        self.code = code
        self.field_name = field_name
        super().__init__(f"{code.value}: {field_name}")


def _validate_approval_text(
    value: object,
    field_name: str,
    code: ApprovalValidationCode,
) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ApprovalValidationError(code, field_name)
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ApprovalValidationError(code, field_name)
    return value


def _coerce_approval_version(value: object) -> ApprovalVersion:
    if isinstance(value, ApprovalVersion):
        return value
    if isinstance(value, str):
        try:
            return ApprovalVersion(value)
        except ValueError:
            pass
    raise ApprovalValidationError(ApprovalValidationCode.UNSUPPORTED_VERSION, "approval_version")


def _coerce_approval_kind(value: object) -> ApprovalKind:
    if isinstance(value, ApprovalKind):
        return value
    if isinstance(value, str):
        try:
            return ApprovalKind(value)
        except ValueError:
            pass
    raise ApprovalValidationError(ApprovalValidationCode.INVALID_KIND, "approval_kind")


def _coerce_approval_target_kind(value: object) -> ApprovalTargetKind:
    if isinstance(value, ApprovalTargetKind):
        return value
    if isinstance(value, str):
        try:
            return ApprovalTargetKind(value)
        except ValueError:
            pass
    raise ApprovalValidationError(ApprovalValidationCode.INVALID_KIND, "target_kind")


def _validate_subject_digest(value: object) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ApprovalValidationError(ApprovalValidationCode.INVALID_DIGEST, "subject_digest")
    return value


def _validate_approval_values(
    value: object,
    field_name: str,
    *,
    required: bool,
) -> tuple[str, ...]:
    if not isinstance(value, tuple):
        raise ApprovalValidationError(ApprovalValidationCode.EMPTY_REQUIRED_VALUE, field_name)
    if required and not value:
        raise ApprovalValidationError(ApprovalValidationCode.EMPTY_REQUIRED_VALUE, field_name)
    values = tuple(
        _validate_approval_text(item, field_name, ApprovalValidationCode.EMPTY_REQUIRED_VALUE)
        for item in value
    )
    if len(set(values)) != len(values):
        raise ApprovalValidationError(ApprovalValidationCode.DUPLICATE_VALUE, field_name)
    return tuple(sorted(values))


def _validate_approval_paths(value: object) -> tuple[str, ...]:
    if not isinstance(value, tuple):
        raise ApprovalValidationError(ApprovalValidationCode.EMPTY_REQUIRED_VALUE, "allowed_paths")
    paths = []
    for item in value:
        path = _validate_approval_text(
            item,
            "allowed_paths",
            ApprovalValidationCode.EMPTY_REQUIRED_VALUE,
        )
        if "\\" in path or path.startswith("/") or path.startswith("//"):
            raise ApprovalValidationError(ApprovalValidationCode.INVALID_PATH, "allowed_paths")
        if len(path) >= 2 and path[0].isalpha() and path[1] == ":":
            raise ApprovalValidationError(ApprovalValidationCode.INVALID_PATH, "allowed_paths")
        if any(character in path for character in "*?[]{}"):
            raise ApprovalValidationError(ApprovalValidationCode.INVALID_PATH, "allowed_paths")
        if any(not segment or segment in {".", ".."} for segment in path.split("/")):
            raise ApprovalValidationError(ApprovalValidationCode.INVALID_PATH, "allowed_paths")
        paths.append(path)
    if len(set(paths)) != len(paths):
        raise ApprovalValidationError(ApprovalValidationCode.DUPLICATE_VALUE, "allowed_paths")
    return tuple(sorted(paths))


@dataclass(frozen=True)
class ApprovalBinding:
    """A pure, versioned binding for one human approval record."""

    approval_id: str
    approval_version: ApprovalVersion | str
    approval_kind: ApprovalKind | str
    subject_id: str
    subject_digest: str
    target_kind: ApprovalTargetKind | str
    target_id: str
    target_branch: str
    base_commit: str
    allowed_actions: tuple[str, ...]
    allowed_paths: tuple[str, ...]
    approver_id: str
    approved_at: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "approval_id",
            _validate_approval_text(
                self.approval_id,
                "approval_id",
                ApprovalValidationCode.INVALID_IDENTITY,
            ),
        )
        object.__setattr__(self, "approval_version", _coerce_approval_version(self.approval_version))
        object.__setattr__(self, "approval_kind", _coerce_approval_kind(self.approval_kind))
        object.__setattr__(
            self,
            "subject_id",
            _validate_approval_text(
                self.subject_id,
                "subject_id",
                ApprovalValidationCode.INVALID_IDENTITY,
            ),
        )
        object.__setattr__(self, "subject_digest", _validate_subject_digest(self.subject_digest))
        object.__setattr__(self, "target_kind", _coerce_approval_target_kind(self.target_kind))
        object.__setattr__(
            self,
            "target_id",
            _validate_approval_text(
                self.target_id,
                "target_id",
                ApprovalValidationCode.INVALID_IDENTITY,
            ),
        )
        object.__setattr__(
            self,
            "target_branch",
            _validate_approval_text(
                self.target_branch,
                "target_branch",
                ApprovalValidationCode.EMPTY_REQUIRED_VALUE,
            ),
        )
        object.__setattr__(
            self,
            "base_commit",
            _validate_approval_text(
                self.base_commit,
                "base_commit",
                ApprovalValidationCode.EMPTY_REQUIRED_VALUE,
            ),
        )
        object.__setattr__(
            self,
            "allowed_actions",
            _validate_approval_values(self.allowed_actions, "allowed_actions", required=True),
        )
        object.__setattr__(self, "allowed_paths", _validate_approval_paths(self.allowed_paths))
        object.__setattr__(
            self,
            "approver_id",
            _validate_approval_text(
                self.approver_id,
                "approver_id",
                ApprovalValidationCode.INVALID_IDENTITY,
            ),
        )
        object.__setattr__(
            self,
            "approved_at",
            _validate_approval_text(
                self.approved_at,
                "approved_at",
                ApprovalValidationCode.EMPTY_REQUIRED_VALUE,
            ),
        )

    def canonical_json(self) -> str:
        return _canonical_json(
            {
                "allowed_actions": list(self.allowed_actions),
                "allowed_paths": list(self.allowed_paths),
                "approval_id": self.approval_id,
                "approval_kind": self.approval_kind.value,
                "approval_version": self.approval_version.value,
                "approved_at": self.approved_at,
                "approver_id": self.approver_id,
                "base_commit": self.base_commit,
                "subject_digest": self.subject_digest,
                "subject_id": self.subject_id,
                "target_branch": self.target_branch,
                "target_id": self.target_id,
                "target_kind": self.target_kind.value,
            }
        )

    def sha256_digest(self) -> str:
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class DevelopmentRun:
    run_id: str
    milestone_contract_digest: str
    current_state: DevelopmentRunState
    state_version: int
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class AcceptedStateEvent:
    event_id: str
    run_id: str
    from_state: DevelopmentRunState
    to_state: DevelopmentRunState
    transition_reason: str
    occurred_at: str
    state_version: int


@dataclass(frozen=True)
class TransitionRequest:
    run_id: str
    expected_state: DevelopmentRunState
    expected_state_version: int
    requested_state: DevelopmentRunState


@dataclass(frozen=True)
class TransitionResult:
    accepted: bool
    reason_code: TransitionReasonCode
    run_id: str
    current_state: Optional[DevelopmentRunState]
    current_state_version: Optional[int]
    requested_state: DevelopmentRunState


class WorkflowNodeType(str, Enum):
    """Closed semantic node vocabulary for State Machine v1."""

    DETERMINISTIC = "DETERMINISTIC"
    MODEL_CALL = "MODEL_CALL"
    SPECIALIST_AGENT = "SPECIALIST_AGENT"
    HUMAN_APPROVAL = "HUMAN_APPROVAL"
    EXTERNAL_EFFECT = "EXTERNAL_EFFECT"


class WorkflowEdgeType(str, Enum):
    """Closed semantic edge vocabulary for State Machine v1."""

    UNCONDITIONAL = "UNCONDITIONAL"
    STATE_CONDITIONAL = "STATE_CONDITIONAL"
    EVIDENCE_GATED = "EVIDENCE_GATED"
    APPROVAL_GATED = "APPROVAL_GATED"
    RETRY = "RETRY"
    ESCALATION = "ESCALATION"
    TERMINAL = "TERMINAL"


class TransitionRuleId(str, Enum):
    P1_6_DRAFT_TO_FEASIBILITY_CHECKING_V1 = (
        "P1_6_DRAFT_TO_FEASIBILITY_CHECKING_V1"
    )
    P1_6_FEASIBILITY_CHECKING_TO_AWAITING_EXECUTION_APPROVAL_V1 = (
        "P1_6_FEASIBILITY_CHECKING_TO_AWAITING_EXECUTION_APPROVAL_V1"
    )


class TransitionEvaluationDecision(str, Enum):
    INVALID = "INVALID"
    UNSUPPORTED = "UNSUPPORTED"
    GATED = "GATED"
    DENIED = "DENIED"
    ALLOWED = "ALLOWED"


class TransitionEvaluationReasonCode(str, Enum):
    RULE_ALLOWED = "RULE_ALLOWED"

    REGISTRY_NO_REGISTERED_TRANSITION = "REGISTRY_NO_REGISTERED_TRANSITION"
    APPROVAL_REJECTED = "APPROVAL_REJECTED"
    APPROVAL_SCOPE_DENIED = "APPROVAL_SCOPE_DENIED"

    CONTRACT_REQUIRED = "CONTRACT_REQUIRED"
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
    APPROVAL_STALE = "APPROVAL_STALE"
    REPOSITORY_BINDING_REQUIRED = "REPOSITORY_BINDING_REQUIRED"
    EVIDENCE_REQUIRED = "EVIDENCE_REQUIRED"
    EVIDENCE_NOT_SATISFIED = "EVIDENCE_NOT_SATISFIED"
    EVIDENCE_STALE = "EVIDENCE_STALE"

    EVALUATION_VERSION_UNSUPPORTED = "EVALUATION_VERSION_UNSUPPORTED"
    REGISTRY_RULE_ID_UNSUPPORTED = "REGISTRY_RULE_ID_UNSUPPORTED"
    STATE_CONDITIONAL_RULE_DEFERRED = "STATE_CONDITIONAL_RULE_DEFERRED"
    APPROVAL_GATED_RULE_DEFERRED = "APPROVAL_GATED_RULE_DEFERRED"
    RETRY_RULE_DEFERRED = "RETRY_RULE_DEFERRED"
    ESCALATION_RULE_DEFERRED = "ESCALATION_RULE_DEFERRED"
    TERMINAL_RULE_DEFERRED = "TERMINAL_RULE_DEFERRED"

    REQUEST_NOT_EVALUATION_REQUEST = "REQUEST_NOT_EVALUATION_REQUEST"
    REQUEST_REQUIRED_FIELD_MISSING = "REQUEST_REQUIRED_FIELD_MISSING"
    REQUEST_WRONG_TYPE = "REQUEST_WRONG_TYPE"
    EVALUATION_VERSION_MALFORMED = "EVALUATION_VERSION_MALFORMED"
    REQUEST_UNKNOWN_ENUM_VALUE = "REQUEST_UNKNOWN_ENUM_VALUE"
    REQUEST_UNKNOWN_SOURCE_STATE = "REQUEST_UNKNOWN_SOURCE_STATE"
    REQUEST_UNKNOWN_TARGET_STATE = "REQUEST_UNKNOWN_TARGET_STATE"
    REQUEST_RULE_ID_MALFORMED = "REQUEST_RULE_ID_MALFORMED"
    REQUEST_CONTRADICTORY_FACTS = "REQUEST_CONTRADICTORY_FACTS"
    REQUEST_SURPLUS_PREREQUISITE = "REQUEST_SURPLUS_PREREQUISITE"
    REGISTRY_RULE_SOURCE_MISMATCH = "REGISTRY_RULE_SOURCE_MISMATCH"
    REGISTRY_RULE_TARGET_MISMATCH = "REGISTRY_RULE_TARGET_MISMATCH"
    REGISTRY_EDGE_TYPE_MISMATCH = "REGISTRY_EDGE_TYPE_MISMATCH"
    REGISTRY_NODE_METADATA_MISMATCH = "REGISTRY_NODE_METADATA_MISMATCH"
    CONTRACT_MALFORMED = "CONTRACT_MALFORMED"
    CONTRACT_BINDING_MISMATCH = "CONTRACT_BINDING_MISMATCH"
    EXPECTED_EVIDENCE_ANCHOR_MISSING = "EXPECTED_EVIDENCE_ANCHOR_MISSING"
    EXPECTED_EVIDENCE_ANCHOR_MALFORMED = "EXPECTED_EVIDENCE_ANCHOR_MALFORMED"
    REPOSITORY_BINDING_MALFORMED = "REPOSITORY_BINDING_MALFORMED"
    APPROVAL_SNAPSHOT_MALFORMED = "APPROVAL_SNAPSHOT_MALFORMED"
    APPROVAL_PROVENANCE_MISMATCH = "APPROVAL_PROVENANCE_MISMATCH"
    EVIDENCE_SNAPSHOT_MALFORMED = "EVIDENCE_SNAPSHOT_MALFORMED"
    EVIDENCE_VERDICT_READINESS_INCONSISTENT = (
        "EVIDENCE_VERDICT_READINESS_INCONSISTENT"
    )
    EVIDENCE_KIND_MISMATCH = "EVIDENCE_KIND_MISMATCH"
    EVIDENCE_ARTIFACT_IDENTITY_MISMATCH = "EVIDENCE_ARTIFACT_IDENTITY_MISMATCH"
    EVIDENCE_ARTIFACT_DIGEST_MISMATCH = "EVIDENCE_ARTIFACT_DIGEST_MISMATCH"
    EVIDENCE_AUTHORITY_MISMATCH = "EVIDENCE_AUTHORITY_MISMATCH"
    EVIDENCE_RUN_MISMATCH = "EVIDENCE_RUN_MISMATCH"
    EVIDENCE_CONTRACT_BINDING_MISMATCH = "EVIDENCE_CONTRACT_BINDING_MISMATCH"
    EVIDENCE_RULE_MISMATCH = "EVIDENCE_RULE_MISMATCH"
    EVIDENCE_REPOSITORY_IDENTITY_MISMATCH = (
        "EVIDENCE_REPOSITORY_IDENTITY_MISMATCH"
    )
    EVIDENCE_PRODUCER_MISMATCH = "EVIDENCE_PRODUCER_MISMATCH"
    ESCALATION_SNAPSHOT_MALFORMED = "ESCALATION_SNAPSHOT_MALFORMED"


class TransitionRequirement(str, Enum):
    CONTRACT = "CONTRACT"
    REPOSITORY = "REPOSITORY"
    APPROVAL = "APPROVAL"
    EVIDENCE = "EVIDENCE"
    RETRY_BUDGET = "RETRY_BUDGET"
    ESCALATION = "ESCALATION"


class TransitionRuleValidationCode(str, Enum):
    WRONG_TYPE = "WRONG_TYPE"
    RULE_ID_MALFORMED = "RULE_ID_MALFORMED"
    SOURCE_EQUALS_TARGET = "SOURCE_EQUALS_TARGET"
    REQUIREMENTS_NOT_CANONICAL_TUPLE = "REQUIREMENTS_NOT_CANONICAL_TUPLE"
    REQUIREMENT_WRONG_TYPE = "REQUIREMENT_WRONG_TYPE"
    REQUIREMENTS_NON_CANONICAL_ORDER = "REQUIREMENTS_NON_CANONICAL_ORDER"
    DUPLICATE_REQUIREMENT = "DUPLICATE_REQUIREMENT"
    EDGE_REQUIREMENTS_INCOMPATIBLE = "EDGE_REQUIREMENTS_INCOMPATIBLE"
    METADATA_INCOMPATIBLE = "METADATA_INCOMPATIBLE"
    CONFLICTS_WITH_RULE_ID = "CONFLICTS_WITH_RULE_ID"


class TransitionRuleValidationError(ValueError):
    def __init__(self, code: TransitionRuleValidationCode, field_name: str) -> None:
        self.code = code
        self.field_name = field_name
        super().__init__(f"{code.value}: {field_name}")


class SnapshotProducerKind(str, Enum):
    APPROVAL_REPOSITORY = "APPROVAL_REPOSITORY"
    FEASIBILITY_ASSESSOR = "FEASIBILITY_ASSESSOR"


class ApprovalSnapshotStatus(str, Enum):
    PRESENT = "PRESENT"
    ABSENT = "ABSENT"
    REJECTED = "REJECTED"


class EvidenceKind(str, Enum):
    FEASIBILITY_ASSESSMENT = "FEASIBILITY_ASSESSMENT"


class EvidenceVerdict(str, Enum):
    FEASIBLE = "FEASIBLE"
    FEASIBLE_WITH_NON_BLOCKING_FINDINGS = "FEASIBLE_WITH_NON_BLOCKING_FINDINGS"
    INFEASIBLE = "INFEASIBLE"
    BLOCKED = "BLOCKED"
    NEEDS_HUMAN_DECISION = "NEEDS_HUMAN_DECISION"


class EscalationTrigger(str, Enum):
    ATTEMPT_BUDGET_EXHAUSTED = "ATTEMPT_BUDGET_EXHAUSTED"
    WALL_CLOCK_BUDGET_EXHAUSTED = "WALL_CLOCK_BUDGET_EXHAUSTED"
    TIMEOUT_POLICY_STOP = "TIMEOUT_POLICY_STOP"
    TOKEN_BUDGET_EXHAUSTED = "TOKEN_BUDGET_EXHAUSTED"
    COST_BUDGET_EXHAUSTED = "COST_BUDGET_EXHAUSTED"
    ESCALATION_BUDGET_EXHAUSTED = "ESCALATION_BUDGET_EXHAUSTED"
    NO_PROGRESS_STOP = "NO_PROGRESS_STOP"
    INVALID_AUTHORITY = "INVALID_AUTHORITY"
    REPEATED_EQUIVALENT_FAILURE = "REPEATED_EQUIVALENT_FAILURE"
    STOP_CONDITION = "STOP_CONDITION"
    RECONCILIATION_REQUIRED = "RECONCILIATION_REQUIRED"


@dataclass(frozen=True)
class RepositoryEvidenceBinding:
    repository_id: str
    branch: str
    head_commit: str
    worktree_digest: str


@dataclass(frozen=True)
class ExpectedEvidenceBinding:
    assessment_authority_id: str
    artifact_path: str
    artifact_byte_count: int
    artifact_sha256: str


@dataclass(frozen=True)
class ApprovalSnapshot:
    snapshot_version: str
    producer_kind: SnapshotProducerKind
    run_id: str
    milestone_id: str
    milestone_contract_digest: str
    source_state: DevelopmentRunState
    state_version: int
    rule_id: TransitionRuleId
    status: ApprovalSnapshotStatus
    approval_binding: ApprovalBinding | None
    approval_binding_digest: str | None


@dataclass(frozen=True)
class EvidenceSnapshot:
    snapshot_version: str
    producer_kind: SnapshotProducerKind
    run_id: str
    milestone_id: str
    milestone_contract_digest: str
    source_state: DevelopmentRunState
    state_version: int
    rule_id: TransitionRuleId
    evidence_kind: EvidenceKind
    assessment_authority_id: str
    artifact_path: str
    artifact_byte_count: int
    artifact_sha256: str
    verdict: EvidenceVerdict
    ready_for_approval_1: bool
    assessed_at: str
    repository_binding: RepositoryEvidenceBinding


_RULE_ID_PATTERN = re.compile(
    r"\A[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)*_V1\Z",
    re.ASCII,
)
_REQUIREMENT_ORDER = {value: index for index, value in enumerate(TransitionRequirement)}


@dataclass(frozen=True)
class TransitionRule:
    rule_id: TransitionRuleId
    source_state: DevelopmentRunState
    target_state: DevelopmentRunState
    edge_type: WorkflowEdgeType
    requirements: tuple[TransitionRequirement, ...]
    node_type_constraint: WorkflowNodeType | None = None
    evidence_kind_requirement: EvidenceKind | None = None
    evidence_producer_kind_requirement: SnapshotProducerKind | None = None

    @staticmethod
    def _raise(code: TransitionRuleValidationCode, field_name: str) -> None:
        raise TransitionRuleValidationError(code, field_name)

    def __post_init__(self) -> None:
        if type(self.rule_id) is not TransitionRuleId:
            self._raise(TransitionRuleValidationCode.WRONG_TYPE, "rule_id")
        if not 4 <= len(self.rule_id.value) <= 128 or not _RULE_ID_PATTERN.fullmatch(
            self.rule_id.value
        ):
            self._raise(TransitionRuleValidationCode.RULE_ID_MALFORMED, "rule_id")
        if type(self.source_state) is not DevelopmentRunState:
            self._raise(TransitionRuleValidationCode.WRONG_TYPE, "source_state")
        if type(self.target_state) is not DevelopmentRunState:
            self._raise(TransitionRuleValidationCode.WRONG_TYPE, "target_state")
        if self.source_state is self.target_state:
            self._raise(
                TransitionRuleValidationCode.SOURCE_EQUALS_TARGET,
                "source_state/target_state",
            )
        if type(self.edge_type) is not WorkflowEdgeType:
            self._raise(TransitionRuleValidationCode.WRONG_TYPE, "edge_type")
        if type(self.requirements) is not tuple:
            self._raise(
                TransitionRuleValidationCode.REQUIREMENTS_NOT_CANONICAL_TUPLE,
                "requirements",
            )
        for index, requirement in enumerate(self.requirements):
            if type(requirement) is not TransitionRequirement:
                self._raise(
                    TransitionRuleValidationCode.REQUIREMENT_WRONG_TYPE,
                    f"requirements[{index}]",
                )
        ordinals = [_REQUIREMENT_ORDER[value] for value in self.requirements]
        if any(left > right for left, right in zip(ordinals, ordinals[1:])):
            self._raise(
                TransitionRuleValidationCode.REQUIREMENTS_NON_CANONICAL_ORDER,
                "requirements",
            )
        if len(set(self.requirements)) != len(self.requirements):
            self._raise(TransitionRuleValidationCode.DUPLICATE_REQUIREMENT, "requirements")
        if self.node_type_constraint is not None and type(
            self.node_type_constraint
        ) is not WorkflowNodeType:
            self._raise(TransitionRuleValidationCode.WRONG_TYPE, "node_type_constraint")
        if self.evidence_kind_requirement is not None and type(
            self.evidence_kind_requirement
        ) is not EvidenceKind:
            self._raise(
                TransitionRuleValidationCode.WRONG_TYPE,
                "evidence_kind_requirement",
            )
        if self.evidence_producer_kind_requirement is not None and type(
            self.evidence_producer_kind_requirement
        ) is not SnapshotProducerKind:
            self._raise(
                TransitionRuleValidationCode.WRONG_TYPE,
                "evidence_producer_kind_requirement",
            )

        if self.edge_type is WorkflowEdgeType.UNCONDITIONAL:
            expected_requirements = (TransitionRequirement.CONTRACT,)
        elif self.edge_type is WorkflowEdgeType.EVIDENCE_GATED:
            expected_requirements = (
                TransitionRequirement.CONTRACT,
                TransitionRequirement.REPOSITORY,
                TransitionRequirement.EVIDENCE,
            )
        else:
            expected_requirements = None
        if expected_requirements is None or self.requirements != expected_requirements:
            self._raise(
                TransitionRuleValidationCode.EDGE_REQUIREMENTS_INCOMPATIBLE,
                "requirements",
            )

        if self.node_type_constraint is not None:
            self._raise(
                TransitionRuleValidationCode.METADATA_INCOMPATIBLE,
                "node_type_constraint",
            )
        if self.edge_type is WorkflowEdgeType.UNCONDITIONAL:
            if self.evidence_kind_requirement is not None:
                self._raise(
                    TransitionRuleValidationCode.METADATA_INCOMPATIBLE,
                    "evidence_kind_requirement",
                )
            if self.evidence_producer_kind_requirement is not None:
                self._raise(
                    TransitionRuleValidationCode.METADATA_INCOMPATIBLE,
                    "evidence_producer_kind_requirement",
                )
        else:
            if self.evidence_kind_requirement is None:
                self._raise(
                    TransitionRuleValidationCode.METADATA_INCOMPATIBLE,
                    "evidence_kind_requirement",
                )
            if self.evidence_producer_kind_requirement is None:
                self._raise(
                    TransitionRuleValidationCode.METADATA_INCOMPATIBLE,
                    "evidence_producer_kind_requirement",
                )

        if self.rule_id is TransitionRuleId.P1_6_DRAFT_TO_FEASIBILITY_CHECKING_V1:
            expected = (
                DevelopmentRunState.DRAFT,
                DevelopmentRunState.FEASIBILITY_CHECKING,
                WorkflowEdgeType.UNCONDITIONAL,
                (TransitionRequirement.CONTRACT,),
                None,
                None,
                None,
            )
        else:
            expected = (
                DevelopmentRunState.FEASIBILITY_CHECKING,
                DevelopmentRunState.AWAITING_EXECUTION_APPROVAL,
                WorkflowEdgeType.EVIDENCE_GATED,
                (
                    TransitionRequirement.CONTRACT,
                    TransitionRequirement.REPOSITORY,
                    TransitionRequirement.EVIDENCE,
                ),
                None,
                EvidenceKind.FEASIBILITY_ASSESSMENT,
                SnapshotProducerKind.FEASIBILITY_ASSESSOR,
            )
        actual = (
            self.source_state,
            self.target_state,
            self.edge_type,
            self.requirements,
            self.node_type_constraint,
            self.evidence_kind_requirement,
            self.evidence_producer_kind_requirement,
        )
        field_names = (
            "source_state",
            "target_state",
            "edge_type",
            "requirements",
            "node_type_constraint",
            "evidence_kind_requirement",
            "evidence_producer_kind_requirement",
        )
        for field_name, supplied, required in zip(field_names, actual, expected):
            if supplied != required:
                self._raise(
                    TransitionRuleValidationCode.CONFLICTS_WITH_RULE_ID,
                    field_name,
                )


@dataclass(frozen=True)
class TransitionEvaluationRequest:
    evaluation_version: str | None = None
    run: DevelopmentRun | None = None
    rule_id: str | None = None
    requested_state: DevelopmentRunState | None = None
    edge_type: WorkflowEdgeType | None = None
    node_type: WorkflowNodeType | None = None
    milestone_contract: MilestoneContract | None = None
    expected_evidence: ExpectedEvidenceBinding | None = None
    repository_binding: RepositoryEvidenceBinding | None = None
    approval_snapshot: ApprovalSnapshot | None = None
    evidence_snapshots: tuple[EvidenceSnapshot, ...] = ()
    retry_budget_snapshot: object | None = None
    escalation_trigger: EscalationTrigger | None = None

    __hash__ = None


_REASONS_BY_DECISION = {
    TransitionEvaluationDecision.ALLOWED: {
        TransitionEvaluationReasonCode.RULE_ALLOWED,
    },
    TransitionEvaluationDecision.DENIED: {
        TransitionEvaluationReasonCode.REGISTRY_NO_REGISTERED_TRANSITION,
        TransitionEvaluationReasonCode.APPROVAL_REJECTED,
        TransitionEvaluationReasonCode.APPROVAL_SCOPE_DENIED,
    },
    TransitionEvaluationDecision.GATED: {
        TransitionEvaluationReasonCode.CONTRACT_REQUIRED,
        TransitionEvaluationReasonCode.APPROVAL_REQUIRED,
        TransitionEvaluationReasonCode.APPROVAL_STALE,
        TransitionEvaluationReasonCode.REPOSITORY_BINDING_REQUIRED,
        TransitionEvaluationReasonCode.EVIDENCE_REQUIRED,
        TransitionEvaluationReasonCode.EVIDENCE_NOT_SATISFIED,
        TransitionEvaluationReasonCode.EVIDENCE_STALE,
    },
    TransitionEvaluationDecision.UNSUPPORTED: {
        TransitionEvaluationReasonCode.EVALUATION_VERSION_UNSUPPORTED,
        TransitionEvaluationReasonCode.REGISTRY_RULE_ID_UNSUPPORTED,
        TransitionEvaluationReasonCode.STATE_CONDITIONAL_RULE_DEFERRED,
        TransitionEvaluationReasonCode.APPROVAL_GATED_RULE_DEFERRED,
        TransitionEvaluationReasonCode.RETRY_RULE_DEFERRED,
        TransitionEvaluationReasonCode.ESCALATION_RULE_DEFERRED,
        TransitionEvaluationReasonCode.TERMINAL_RULE_DEFERRED,
    },
    TransitionEvaluationDecision.INVALID: {
        reason
        for reason in TransitionEvaluationReasonCode
        if reason
        not in {
            TransitionEvaluationReasonCode.RULE_ALLOWED,
            TransitionEvaluationReasonCode.REGISTRY_NO_REGISTERED_TRANSITION,
            TransitionEvaluationReasonCode.APPROVAL_REJECTED,
            TransitionEvaluationReasonCode.APPROVAL_SCOPE_DENIED,
            TransitionEvaluationReasonCode.CONTRACT_REQUIRED,
            TransitionEvaluationReasonCode.APPROVAL_REQUIRED,
            TransitionEvaluationReasonCode.APPROVAL_STALE,
            TransitionEvaluationReasonCode.REPOSITORY_BINDING_REQUIRED,
            TransitionEvaluationReasonCode.EVIDENCE_REQUIRED,
            TransitionEvaluationReasonCode.EVIDENCE_NOT_SATISFIED,
            TransitionEvaluationReasonCode.EVIDENCE_STALE,
            TransitionEvaluationReasonCode.EVALUATION_VERSION_UNSUPPORTED,
            TransitionEvaluationReasonCode.REGISTRY_RULE_ID_UNSUPPORTED,
            TransitionEvaluationReasonCode.STATE_CONDITIONAL_RULE_DEFERRED,
            TransitionEvaluationReasonCode.APPROVAL_GATED_RULE_DEFERRED,
            TransitionEvaluationReasonCode.RETRY_RULE_DEFERRED,
            TransitionEvaluationReasonCode.ESCALATION_RULE_DEFERRED,
            TransitionEvaluationReasonCode.TERMINAL_RULE_DEFERRED,
        }
    },
}


@dataclass(frozen=True)
class TransitionEvaluationResult:
    decision: TransitionEvaluationDecision
    reason_code: TransitionEvaluationReasonCode
    run_id: str | None = None
    current_state: DevelopmentRunState | None = None
    current_state_version: int | None = None
    requested_state: DevelopmentRunState | None = None
    rule_id: TransitionRuleId | None = None
    edge_type: WorkflowEdgeType | None = None
    node_type: WorkflowNodeType | None = None
    missing_requirements: tuple[TransitionRequirement, ...] = ()
    side_effects_performed: bool = field(default=False, init=False)

    def __post_init__(self) -> None:
        if type(self.decision) is not TransitionEvaluationDecision:
            raise TypeError("decision")
        if type(self.reason_code) is not TransitionEvaluationReasonCode:
            raise TypeError("reason_code")
        optional_types = (
            ("run_id", self.run_id, str),
            ("current_state", self.current_state, DevelopmentRunState),
            ("current_state_version", self.current_state_version, int),
            ("requested_state", self.requested_state, DevelopmentRunState),
            ("rule_id", self.rule_id, TransitionRuleId),
            ("edge_type", self.edge_type, WorkflowEdgeType),
            ("node_type", self.node_type, WorkflowNodeType),
        )
        for field_name, value, expected_type in optional_types:
            if value is not None and type(value) is not expected_type:
                raise TypeError(field_name)
        if self.current_state_version is not None and (
            type(self.current_state_version) is not int
            or self.current_state_version < 0
        ):
            raise TypeError("current_state_version")
        if type(self.missing_requirements) is not tuple:
            raise TypeError("missing_requirements")
        if any(type(value) is not TransitionRequirement for value in self.missing_requirements):
            raise TypeError("missing_requirements")
        ordinals = [_REQUIREMENT_ORDER[value] for value in self.missing_requirements]
        if (
            any(left >= right for left, right in zip(ordinals, ordinals[1:]))
            or len(set(self.missing_requirements)) != len(self.missing_requirements)
        ):
            raise ValueError("missing_requirements")
        if self.reason_code not in _REASONS_BY_DECISION[self.decision]:
            raise ValueError("reason_code")


class TransactionalTransitionReasonCode(str, Enum):
    """Stable outcomes of the DL-P1.7 persistence boundary."""

    COMMITTED = "COMMITTED"
    EVALUATION_NOT_ALLOWED = "EVALUATION_NOT_ALLOWED"
    RUN_NOT_FOUND = "RUN_NOT_FOUND"
    STALE_PERSISTED_RUN = "STALE_PERSISTED_RUN"


@dataclass(frozen=True)
class TransactionalTransitionResult:
    """Separates pure policy evaluation from authoritative persistence."""

    committed: bool
    reason_code: TransactionalTransitionReasonCode
    evaluation_result: TransitionEvaluationResult
    persisted_run: DevelopmentRun | None
    accepted_event: AcceptedStateEvent | None

    def __post_init__(self) -> None:
        if type(self.committed) is not bool:
            raise TypeError("committed")
        if type(self.reason_code) is not TransactionalTransitionReasonCode:
            raise TypeError("reason_code")
        if type(self.evaluation_result) is not TransitionEvaluationResult:
            raise TypeError("evaluation_result")
        if self.persisted_run is not None and type(self.persisted_run) is not DevelopmentRun:
            raise TypeError("persisted_run")
        if self.accepted_event is not None and type(
            self.accepted_event
        ) is not AcceptedStateEvent:
            raise TypeError("accepted_event")

        evaluation_allowed = (
            self.evaluation_result.decision is TransitionEvaluationDecision.ALLOWED
        )
        if self.reason_code is TransactionalTransitionReasonCode.COMMITTED:
            if (
                not self.committed
                or not evaluation_allowed
                or self.persisted_run is None
                or self.accepted_event is None
            ):
                raise ValueError("committed result")
            event = self.accepted_event
            run = self.persisted_run
            if (
                self.evaluation_result.run_id != event.run_id
                or self.evaluation_result.current_state is not event.from_state
                or self.evaluation_result.requested_state is not event.to_state
                or self.evaluation_result.current_state_version is None
                or event.state_version
                != self.evaluation_result.current_state_version + 1
                or run.run_id != event.run_id
                or run.current_state is not event.to_state
                or run.state_version != event.state_version
                or run.updated_at != event.occurred_at
                or event.transition_reason
                != TransitionEvaluationReasonCode.RULE_ALLOWED.value
            ):
                raise ValueError("committed result binding")
            return

        if self.committed or self.accepted_event is not None:
            raise ValueError("non-committed result")
        if self.reason_code is TransactionalTransitionReasonCode.EVALUATION_NOT_ALLOWED:
            if evaluation_allowed or self.persisted_run is not None:
                raise ValueError("evaluation result")
        elif self.reason_code is TransactionalTransitionReasonCode.RUN_NOT_FOUND:
            if not evaluation_allowed or self.persisted_run is not None:
                raise ValueError("run-not-found result")
        elif self.reason_code is TransactionalTransitionReasonCode.STALE_PERSISTED_RUN:
            if not evaluation_allowed or self.persisted_run is None:
                raise ValueError("stale result")


# DL-2.1 durable command queue domain ---------------------------------------

_QUEUE_UUID_PATTERN = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)
_QUEUE_COMMAND_KIND_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
_QUEUE_IDEMPOTENCY_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")
_QUEUE_ACTOR_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/-]{0,127}$")
_QUEUE_CODE_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
_QUEUE_PAYLOAD_KEY_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$")
_QUEUE_TIMESTAMP_PATTERN = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{6}Z$"
)
_QUEUE_RESERVED_KEYS = frozenset(
    {
        "shell",
        "command_line",
        "argv",
        "executable",
        "executable_path",
        "script",
        "python_code",
        "import_target",
        "callback",
        "sql",
        "url",
        "http_request",
        "approved",
        "authorized",
    }
)
_SIGNED_64_MAX = 9_223_372_036_854_775_807


class WorkflowCommandState(str, Enum):
    PENDING = "PENDING"
    CLAIMED = "CLAIMED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class WorkflowCommandEventKind(str, Enum):
    ENQUEUED = "ENQUEUED"
    CLAIMED = "CLAIMED"
    LEASE_RENEWED = "LEASE_RENEWED"
    STARTED = "STARTED"
    CANCELLATION_REQUESTED = "CANCELLATION_REQUESTED"
    CANCELLED = "CANCELLED"
    EXPIRED_CLAIM_RELEASED = "EXPIRED_CLAIM_RELEASED"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


class QueueMutationKind(str, Enum):
    ENQUEUE = "ENQUEUE"
    CLAIM_NEXT = "CLAIM_NEXT"
    RENEW_LEASE = "RENEW_LEASE"
    MARK_RUNNING = "MARK_RUNNING"
    REQUEST_CANCELLATION = "REQUEST_CANCELLATION"
    ACKNOWLEDGE_CANCELLATION = "ACKNOWLEDGE_CANCELLATION"
    MARK_SUCCEEDED = "MARK_SUCCEEDED"
    MARK_FAILED = "MARK_FAILED"
    RECOVER_EXPIRED_CLAIM = "RECOVER_EXPIRED_CLAIM"


class QueueResultCode(str, Enum):
    APPLIED = "APPLIED"
    FOUND = "FOUND"
    NOT_FOUND = "NOT_FOUND"
    LISTED = "LISTED"
    HISTORY_RETURNED = "HISTORY_RETURNED"
    NO_ELIGIBLE_COMMAND = "NO_ELIGIBLE_COMMAND"
    EXISTING_IDENTICAL = "EXISTING_IDENTICAL"
    IDEMPOTENCY_CONFLICT = "IDEMPOTENCY_CONFLICT"
    CAS_CONFLICT = "CAS_CONFLICT"
    LEASE_OWNER_MISMATCH = "LEASE_OWNER_MISMATCH"
    LEASE_EXPIRED = "LEASE_EXPIRED"
    LEASE_NOT_EXPIRED = "LEASE_NOT_EXPIRED"
    CANCELLATION_ALREADY_REQUESTED = "CANCELLATION_ALREADY_REQUESTED"
    CANCELLATION_NOT_REQUESTED = "CANCELLATION_NOT_REQUESTED"
    TERMINAL_OBSERVED = "TERMINAL_OBSERVED"
    TRANSIENT_CONTENTION = "TRANSIENT_CONTENTION"
    RECONCILIATION_REQUIRED = "RECONCILIATION_REQUIRED"


def _queue_text(value: object, field_name: str, pattern: re.Pattern[str] | None = None) -> str:
    if type(value) is not str:
        raise TypeError(field_name)
    if not value or value != value.strip():
        raise ValueError(field_name)
    try:
        encoded = value.encode("utf-8")
    except UnicodeEncodeError as error:
        raise ValueError(field_name) from error
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ValueError(field_name)
    if pattern is not None and pattern.fullmatch(value) is None:
        raise ValueError(field_name)
    return value


def _queue_identity(value: object, field_name: str) -> str:
    text = _queue_text(value, field_name)
    if len(text.encode("utf-8")) > 256:
        raise ValueError(field_name)
    return text


def _queue_optional_identity(value: object, field_name: str) -> str | None:
    return None if value is None else _queue_identity(value, field_name)


def _queue_uuid(value: object, field_name: str) -> str:
    return _queue_text(value, field_name, _QUEUE_UUID_PATTERN)


def _queue_integer(value: object, field_name: str, minimum: int, maximum: int) -> int:
    if type(value) is not int:
        raise TypeError(field_name)
    if value < minimum or value > maximum:
        raise ValueError(field_name)
    return value


def _parse_queue_timestamp(value: object, field_name: str) -> datetime:
    if type(value) is not str:
        raise TypeError(field_name)
    if _QUEUE_TIMESTAMP_PATTERN.fullmatch(value) is None:
        raise ValueError(field_name)
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ")
    except ValueError as error:
        raise ValueError(field_name) from error
    return parsed.replace(tzinfo=timezone.utc)


def _queue_optional_timestamp(value: object, field_name: str) -> str | None:
    if value is None:
        return None
    _parse_queue_timestamp(value, field_name)
    return value


def _validate_payload_object(payload: object) -> dict[str, object]:
    if type(payload) is not dict:
        raise TypeError("payload")
    node_count = 0

    def visit(value: object, depth: int, field_name: str) -> None:
        nonlocal node_count
        node_count += 1
        if node_count > 1_024:
            raise ValueError("payload nodes")
        if depth > 8:
            raise ValueError("payload depth")
        if value is None or type(value) is bool:
            return
        if type(value) is int:
            if value < -9_223_372_036_854_775_808 or value > _SIGNED_64_MAX:
                raise ValueError(field_name)
            return
        if type(value) is str:
            try:
                encoded = value.encode("utf-8")
            except UnicodeEncodeError as error:
                raise ValueError(field_name) from error
            if len(encoded) > 4_096:
                raise ValueError(field_name)
            return
        if type(value) is list:
            if len(value) > 256:
                raise ValueError(field_name)
            for index, item in enumerate(value):
                visit(item, depth + 1, f"{field_name}[{index}]")
            return
        if type(value) is dict:
            if len(value) > 64:
                raise ValueError(field_name)
            for key, item in value.items():
                if type(key) is not str:
                    raise TypeError("payload key")
                if _QUEUE_PAYLOAD_KEY_PATTERN.fullmatch(key) is None:
                    raise ValueError("payload key")
                if key.lower() in _QUEUE_RESERVED_KEYS:
                    raise ValueError("reserved payload key")
                visit(item, depth + 1, f"{field_name}.{key}")
            return
        raise TypeError(field_name)

    visit(payload, 1, "payload")
    canonical = _canonical_json(payload)
    size = len(canonical.encode("utf-8"))
    if size < 2 or size > 65_536:
        raise ValueError("payload bytes")
    return payload


def _canonical_queue_payload(payload: object) -> str:
    validated = _validate_payload_object(payload)
    return _canonical_json(validated)


def _intent_digest(
    *,
    project_id: str,
    development_run_id: str | None,
    phase_id: str | None,
    command_kind: str,
    command_schema_version: int,
    payload: dict[str, object],
    priority: int,
) -> str:
    canonical = _canonical_json(
        {
            "command_kind": command_kind,
            "command_schema_version": command_schema_version,
            "development_run_id": development_run_id,
            "payload": payload,
            "phase_id": phase_id,
            "priority": priority,
            "project_id": project_id,
        }
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _validated_payload_json(value: object) -> tuple[str, dict[str, object]]:
    if type(value) is not str:
        raise TypeError("payload_json")
    try:
        parsed = json.loads(value)
    except (json.JSONDecodeError, UnicodeError) as error:
        raise ValueError("payload_json") from error
    validated = _validate_payload_object(parsed)
    if _canonical_json(validated) != value:
        raise ValueError("payload_json")
    return value, validated


@dataclass(frozen=True)
class ValidatedCommandEnvelope:
    project_id: str
    development_run_id: str | None
    phase_id: str | None
    command_kind: str
    command_schema_version: int
    payload_json: str
    intent_digest: str
    idempotency_key: str
    priority: int

    def __post_init__(self) -> None:
        project_id = _queue_identity(self.project_id, "project_id")
        development_run_id = _queue_optional_identity(
            self.development_run_id, "development_run_id"
        )
        phase_id = _queue_optional_identity(self.phase_id, "phase_id")
        command_kind = _queue_text(
            self.command_kind, "command_kind", _QUEUE_COMMAND_KIND_PATTERN
        )
        command_schema_version = _queue_integer(
            self.command_schema_version, "command_schema_version", 1, 2_147_483_647
        )
        payload_json, payload = _validated_payload_json(self.payload_json)
        intent_digest = _queue_text(
            self.intent_digest,
            "intent_digest",
            re.compile(r"^[0-9a-f]{64}$"),
        )
        idempotency_key = _queue_text(
            self.idempotency_key, "idempotency_key", _QUEUE_IDEMPOTENCY_PATTERN
        )
        priority = _queue_integer(self.priority, "priority", 0, 100)
        expected_digest = _intent_digest(
            project_id=project_id,
            development_run_id=development_run_id,
            phase_id=phase_id,
            command_kind=command_kind,
            command_schema_version=command_schema_version,
            payload=payload,
            priority=priority,
        )
        if intent_digest != expected_digest:
            raise ValueError("intent_digest")
        object.__setattr__(self, "payload_json", payload_json)


@dataclass(frozen=True)
class WorkflowCommand:
    queue_sequence: int
    command_id: str
    project_id: str
    development_run_id: str | None
    phase_id: str | None
    command_kind: str
    command_schema_version: int
    payload_json: str
    intent_digest: str
    idempotency_key: str
    priority: int
    state: WorkflowCommandState
    state_version: int
    claim_count: int
    lease_owner: str | None
    lease_acquired_at: str | None
    lease_expires_at: str | None
    cancellation_requested_at: str | None
    cancellation_requested_by: str | None
    cancellation_reason_code: str | None
    failure_code: str | None
    created_at: str
    updated_at: str
    started_at: str | None
    completed_at: str | None

    def __post_init__(self) -> None:
        _queue_integer(self.queue_sequence, "queue_sequence", 1, _SIGNED_64_MAX)
        _queue_uuid(self.command_id, "command_id")
        envelope = ValidatedCommandEnvelope(
            project_id=self.project_id,
            development_run_id=self.development_run_id,
            phase_id=self.phase_id,
            command_kind=self.command_kind,
            command_schema_version=self.command_schema_version,
            payload_json=self.payload_json,
            intent_digest=self.intent_digest,
            idempotency_key=self.idempotency_key,
            priority=self.priority,
        )
        if type(self.state) is not WorkflowCommandState:
            raise TypeError("state")
        _queue_integer(self.state_version, "state_version", 1, _SIGNED_64_MAX)
        _queue_integer(self.claim_count, "claim_count", 0, _SIGNED_64_MAX)
        lease_owner = None if self.lease_owner is None else _queue_text(
            self.lease_owner, "lease_owner", _QUEUE_ACTOR_PATTERN
        )
        lease_acquired = _queue_optional_timestamp(
            self.lease_acquired_at, "lease_acquired_at"
        )
        lease_expires = _queue_optional_timestamp(self.lease_expires_at, "lease_expires_at")
        lease_values = (lease_owner, lease_acquired, lease_expires)
        active = self.state in {WorkflowCommandState.CLAIMED, WorkflowCommandState.RUNNING}
        if active != all(value is not None for value in lease_values):
            raise ValueError("lease")
        if not active and any(value is not None for value in lease_values):
            raise ValueError("lease")
        if active and lease_acquired >= lease_expires:  # type: ignore[operator]
            raise ValueError("lease_expires_at")

        cancellation_at = _queue_optional_timestamp(
            self.cancellation_requested_at, "cancellation_requested_at"
        )
        cancellation_by = None if self.cancellation_requested_by is None else _queue_text(
            self.cancellation_requested_by,
            "cancellation_requested_by",
            _QUEUE_ACTOR_PATTERN,
        )
        cancellation_reason = (
            None
            if self.cancellation_reason_code is None
            else _queue_text(
                self.cancellation_reason_code,
                "cancellation_reason_code",
                _QUEUE_CODE_PATTERN,
            )
        )
        cancellation_values = (cancellation_at, cancellation_by, cancellation_reason)
        complete_cancellation = all(value is not None for value in cancellation_values)
        if any(value is not None for value in cancellation_values) and not complete_cancellation:
            raise ValueError("cancellation")
        if self.state is WorkflowCommandState.PENDING and complete_cancellation:
            raise ValueError("cancellation")
        if self.state is WorkflowCommandState.CANCELLED and not complete_cancellation:
            raise ValueError("cancellation")

        if self.failure_code is None:
            failure = None
        else:
            failure = _queue_text(self.failure_code, "failure_code", _QUEUE_CODE_PATTERN)
        if (self.state is WorkflowCommandState.FAILED) != (failure is not None):
            raise ValueError("failure_code")

        created = _parse_queue_timestamp(self.created_at, "created_at")
        updated = _parse_queue_timestamp(self.updated_at, "updated_at")
        started = (
            None
            if self.started_at is None
            else _parse_queue_timestamp(self.started_at, "started_at")
        )
        completed = (
            None
            if self.completed_at is None
            else _parse_queue_timestamp(self.completed_at, "completed_at")
        )
        if updated < created:
            raise ValueError("updated_at")
        if self.state in {WorkflowCommandState.PENDING, WorkflowCommandState.CLAIMED} and started:
            raise ValueError("started_at")
        if self.state in {
            WorkflowCommandState.RUNNING,
            WorkflowCommandState.SUCCEEDED,
            WorkflowCommandState.FAILED,
        } and started is None:
            raise ValueError("started_at")
        terminal = self.state in {
            WorkflowCommandState.SUCCEEDED,
            WorkflowCommandState.FAILED,
            WorkflowCommandState.CANCELLED,
        }
        if terminal != (completed is not None):
            raise ValueError("completed_at")
        if completed is not None and (completed < created or (started and completed < started)):
            raise ValueError("completed_at")
        if cancellation_at is not None and _parse_queue_timestamp(
            cancellation_at, "cancellation_requested_at"
        ) < created:
            raise ValueError("cancellation_requested_at")
        if started is not None and started < created:
            raise ValueError("started_at")
        if envelope.project_id != self.project_id:
            raise ValueError("project_id")


@dataclass(frozen=True)
class WorkflowCommandEvent:
    event_sequence: int
    event_id: str
    command_id: str
    event_kind: WorkflowCommandEventKind
    prior_state: WorkflowCommandState | None
    next_state: WorkflowCommandState
    prior_state_version: int | None
    next_state_version: int
    actor_id: str
    occurred_at: str
    lease_owner: str | None
    lease_expires_at: str | None
    claim_count: int
    reason_code: str | None

    def __post_init__(self) -> None:
        _queue_integer(self.event_sequence, "event_sequence", 1, _SIGNED_64_MAX)
        _queue_uuid(self.event_id, "event_id")
        _queue_uuid(self.command_id, "command_id")
        if type(self.event_kind) is not WorkflowCommandEventKind:
            raise TypeError("event_kind")
        if self.prior_state is not None and type(self.prior_state) is not WorkflowCommandState:
            raise TypeError("prior_state")
        if type(self.next_state) is not WorkflowCommandState:
            raise TypeError("next_state")
        if self.prior_state_version is not None:
            _queue_integer(
                self.prior_state_version, "prior_state_version", 1, _SIGNED_64_MAX - 1
            )
        _queue_integer(self.next_state_version, "next_state_version", 1, _SIGNED_64_MAX)
        _queue_text(self.actor_id, "actor_id", _QUEUE_ACTOR_PATTERN)
        occurred = _parse_queue_timestamp(self.occurred_at, "occurred_at")
        owner = None if self.lease_owner is None else _queue_text(
            self.lease_owner, "lease_owner", _QUEUE_ACTOR_PATTERN
        )
        expiry = _queue_optional_timestamp(self.lease_expires_at, "lease_expires_at")
        expiry_instant = (
            None
            if expiry is None
            else _parse_queue_timestamp(expiry, "lease_expires_at")
        )
        if (owner is None) != (expiry is None):
            raise ValueError("lease snapshot")
        _queue_integer(self.claim_count, "claim_count", 0, _SIGNED_64_MAX)
        reason = None if self.reason_code is None else _queue_text(
            self.reason_code, "reason_code", _QUEUE_CODE_PATTERN
        )
        if self.event_kind is WorkflowCommandEventKind.ENQUEUED:
            if (
                self.prior_state is not None
                or self.prior_state_version is not None
                or self.next_state is not WorkflowCommandState.PENDING
                or self.next_state_version != 1
                or self.claim_count != 0
                or owner is not None
                or reason is not None
            ):
                raise ValueError("event compatibility")
            return
        if self.prior_state is None or self.prior_state_version is None:
            raise ValueError("prior_state")
        if self.next_state_version != self.prior_state_version + 1:
            raise ValueError("next_state_version")
        before_after = (self.prior_state, self.next_state)
        kind = self.event_kind
        active_states = {WorkflowCommandState.CLAIMED, WorkflowCommandState.RUNNING}
        if kind is WorkflowCommandEventKind.CLAIMED:
            valid = before_after == (WorkflowCommandState.PENDING, WorkflowCommandState.CLAIMED)
            valid = (
                valid
                and owner is not None
                and self.actor_id == owner
                and reason is None
                and self.claim_count >= 1
                and occurred < expiry_instant  # type: ignore[operator]
            )
        elif kind is WorkflowCommandEventKind.LEASE_RENEWED:
            valid = self.prior_state in active_states and self.next_state is self.prior_state
            valid = (
                valid
                and owner is not None
                and self.actor_id == owner
                and reason is None
                and self.claim_count >= 1
                and occurred < expiry_instant  # type: ignore[operator]
            )
        elif kind is WorkflowCommandEventKind.STARTED:
            valid = before_after == (WorkflowCommandState.CLAIMED, WorkflowCommandState.RUNNING)
            valid = (
                valid
                and owner is not None
                and self.actor_id == owner
                and reason is None
                and self.claim_count >= 1
                and occurred < expiry_instant  # type: ignore[operator]
            )
        elif kind is WorkflowCommandEventKind.CANCELLATION_REQUESTED:
            valid = self.prior_state in active_states and self.next_state is self.prior_state
            valid = valid and owner is not None and reason is not None and self.claim_count >= 1
        elif kind is WorkflowCommandEventKind.CANCELLED:
            valid = self.next_state is WorkflowCommandState.CANCELLED and self.prior_state in {
                WorkflowCommandState.PENDING,
                WorkflowCommandState.CLAIMED,
                WorkflowCommandState.RUNNING,
            }
            valid = valid and reason is not None
            if self.prior_state is WorkflowCommandState.PENDING:
                valid = valid and owner is None
            elif self.prior_state is WorkflowCommandState.RUNNING:
                valid = (
                    valid
                    and owner is not None
                    and self.actor_id == owner
                    and self.claim_count >= 1
                    and occurred < expiry_instant  # type: ignore[operator]
                )
            else:
                valid = valid and owner is not None and self.claim_count >= 1
                valid = valid and (
                    occurred >= expiry_instant  # type: ignore[operator]
                    or self.actor_id == owner
                )
        elif kind is WorkflowCommandEventKind.EXPIRED_CLAIM_RELEASED:
            valid = before_after == (WorkflowCommandState.CLAIMED, WorkflowCommandState.PENDING)
            valid = (
                valid
                and owner is not None
                and reason == "LEASE_EXPIRED"
                and self.claim_count >= 1
                and occurred >= expiry_instant  # type: ignore[operator]
            )
        elif kind is WorkflowCommandEventKind.SUCCEEDED:
            valid = before_after == (WorkflowCommandState.RUNNING, WorkflowCommandState.SUCCEEDED)
            valid = (
                valid
                and owner is not None
                and self.actor_id == owner
                and reason is None
                and self.claim_count >= 1
                and occurred < expiry_instant  # type: ignore[operator]
            )
        elif kind is WorkflowCommandEventKind.FAILED:
            valid = before_after == (WorkflowCommandState.RUNNING, WorkflowCommandState.FAILED)
            valid = (
                valid
                and owner is not None
                and self.actor_id == owner
                and reason is not None
                and self.claim_count >= 1
                and occurred < expiry_instant  # type: ignore[operator]
            )
        else:  # pragma: no cover - closed enum
            valid = False
        if not valid:
            raise ValueError("event compatibility")


_EVENT_KINDS_BY_MUTATION = {
    QueueMutationKind.ENQUEUE: {WorkflowCommandEventKind.ENQUEUED},
    QueueMutationKind.CLAIM_NEXT: {WorkflowCommandEventKind.CLAIMED},
    QueueMutationKind.RENEW_LEASE: {WorkflowCommandEventKind.LEASE_RENEWED},
    QueueMutationKind.MARK_RUNNING: {WorkflowCommandEventKind.STARTED},
    QueueMutationKind.REQUEST_CANCELLATION: {
        WorkflowCommandEventKind.CANCELLATION_REQUESTED,
        WorkflowCommandEventKind.CANCELLED,
    },
    QueueMutationKind.ACKNOWLEDGE_CANCELLATION: {WorkflowCommandEventKind.CANCELLED},
    QueueMutationKind.MARK_SUCCEEDED: {WorkflowCommandEventKind.SUCCEEDED},
    QueueMutationKind.MARK_FAILED: {WorkflowCommandEventKind.FAILED},
    QueueMutationKind.RECOVER_EXPIRED_CLAIM: {
        WorkflowCommandEventKind.EXPIRED_CLAIM_RELEASED,
        WorkflowCommandEventKind.CANCELLED,
    },
}


@dataclass(frozen=True)
class QueueResult:
    code: QueueResultCode
    mutation_kind: QueueMutationKind | None = None
    command: WorkflowCommand | None = None
    event: WorkflowCommandEvent | None = None
    commands: tuple[WorkflowCommand, ...] = ()
    events: tuple[WorkflowCommandEvent, ...] = ()

    def __post_init__(self) -> None:
        if type(self.code) is not QueueResultCode:
            raise TypeError("code")
        if self.mutation_kind is not None and type(self.mutation_kind) is not QueueMutationKind:
            raise TypeError("mutation_kind")
        if self.command is not None and type(self.command) is not WorkflowCommand:
            raise TypeError("command")
        if self.event is not None and type(self.event) is not WorkflowCommandEvent:
            raise TypeError("event")
        if type(self.commands) is not tuple or any(type(v) is not WorkflowCommand for v in self.commands):
            raise TypeError("commands")
        if type(self.events) is not tuple or any(type(v) is not WorkflowCommandEvent for v in self.events):
            raise TypeError("events")

        code = self.code
        kind = self.mutation_kind
        singular_empty = self.command is None and self.event is None
        plural_empty = not self.commands and not self.events
        if code is QueueResultCode.APPLIED:
            if kind is None or self.command is None or self.event is None or not plural_empty:
                raise ValueError("APPLIED")
            if (
                self.event.command_id != self.command.command_id
                or self.event.next_state is not self.command.state
                or self.event.next_state_version != self.command.state_version
                or self.event.claim_count != self.command.claim_count
                or self.event.event_kind not in _EVENT_KINDS_BY_MUTATION[kind]
                or self.event.occurred_at != self.command.updated_at
            ):
                raise ValueError("APPLIED binding")
            if self.command.state in {
                WorkflowCommandState.CLAIMED,
                WorkflowCommandState.RUNNING,
            } and (
                self.event.lease_owner != self.command.lease_owner
                or self.event.lease_expires_at != self.command.lease_expires_at
            ):
                raise ValueError("APPLIED lease binding")
            if self.event.event_kind is WorkflowCommandEventKind.ENQUEUED and (
                self.event.occurred_at != self.command.created_at
            ):
                raise ValueError("APPLIED enqueue binding")
            if self.event.event_kind is WorkflowCommandEventKind.STARTED and (
                self.event.occurred_at != self.command.started_at
            ):
                raise ValueError("APPLIED start binding")
            if kind is QueueMutationKind.CLAIM_NEXT and (
                self.event.occurred_at != self.command.lease_acquired_at
            ):
                raise ValueError("APPLIED claim binding")
            if self.command.state in {
                WorkflowCommandState.SUCCEEDED,
                WorkflowCommandState.FAILED,
                WorkflowCommandState.CANCELLED,
            } and self.event.occurred_at != self.command.completed_at:
                raise ValueError("APPLIED completion binding")
            if self.event.event_kind in {
                WorkflowCommandEventKind.CANCELLATION_REQUESTED,
                WorkflowCommandEventKind.CANCELLED,
            } and self.event.reason_code != self.command.cancellation_reason_code:
                raise ValueError("APPLIED cancellation binding")
            if (
                kind is QueueMutationKind.REQUEST_CANCELLATION
                and (
                    self.event.occurred_at
                    != self.command.cancellation_requested_at
                    or self.event.actor_id
                    != self.command.cancellation_requested_by
                )
            ):
                raise ValueError("APPLIED cancellation-request facts")
            if self.event.event_kind is WorkflowCommandEventKind.FAILED and (
                self.event.reason_code != self.command.failure_code
            ):
                raise ValueError("APPLIED failure binding")
            if (
                kind is QueueMutationKind.REQUEST_CANCELLATION
                and self.event.event_kind is WorkflowCommandEventKind.CANCELLED
                and self.event.prior_state is not WorkflowCommandState.PENDING
            ):
                raise ValueError("APPLIED cancellation-request route")
            if kind is QueueMutationKind.ACKNOWLEDGE_CANCELLATION and (
                self.event.event_kind is not WorkflowCommandEventKind.CANCELLED
                or self.event.prior_state
                not in {WorkflowCommandState.CLAIMED, WorkflowCommandState.RUNNING}
                or self.event.lease_owner != self.event.actor_id
                or self.event.lease_expires_at is None
                or self.event.occurred_at >= self.event.lease_expires_at
            ):
                raise ValueError("APPLIED cancellation-acknowledgement route")
            if kind is QueueMutationKind.RECOVER_EXPIRED_CLAIM and (
                self.event.prior_state is not WorkflowCommandState.CLAIMED
                or self.event.lease_expires_at is None
                or self.event.occurred_at < self.event.lease_expires_at
            ):
                raise ValueError("APPLIED expired-claim route")
            return
        if self.event is not None:
            raise ValueError("non-applied event")
        if code is QueueResultCode.FOUND:
            valid = kind is None and self.command is not None and plural_empty
        elif code is QueueResultCode.NOT_FOUND:
            valid = singular_empty and plural_empty and kind not in {
                QueueMutationKind.ENQUEUE,
                QueueMutationKind.CLAIM_NEXT,
            }
        elif code is QueueResultCode.LISTED:
            valid = kind is None and self.command is None and not self.events
        elif code is QueueResultCode.HISTORY_RETURNED:
            valid = kind is None and self.command is None and not self.commands and bool(self.events)
        elif code is QueueResultCode.NO_ELIGIBLE_COMMAND:
            valid = kind is QueueMutationKind.CLAIM_NEXT and singular_empty and plural_empty
        elif code in {QueueResultCode.EXISTING_IDENTICAL, QueueResultCode.IDEMPOTENCY_CONFLICT}:
            valid = kind is QueueMutationKind.ENQUEUE and self.command is not None and plural_empty
        elif code is QueueResultCode.CAS_CONFLICT:
            valid = kind in {
                QueueMutationKind.RENEW_LEASE,
                QueueMutationKind.MARK_RUNNING,
                QueueMutationKind.REQUEST_CANCELLATION,
                QueueMutationKind.ACKNOWLEDGE_CANCELLATION,
                QueueMutationKind.MARK_SUCCEEDED,
                QueueMutationKind.MARK_FAILED,
                QueueMutationKind.RECOVER_EXPIRED_CLAIM,
            } and self.command is not None and plural_empty
        elif code in {QueueResultCode.LEASE_OWNER_MISMATCH, QueueResultCode.LEASE_EXPIRED}:
            valid = kind in {
                QueueMutationKind.RENEW_LEASE,
                QueueMutationKind.MARK_RUNNING,
                QueueMutationKind.ACKNOWLEDGE_CANCELLATION,
                QueueMutationKind.MARK_SUCCEEDED,
                QueueMutationKind.MARK_FAILED,
            } and self.command is not None and plural_empty
        elif code is QueueResultCode.LEASE_NOT_EXPIRED:
            valid = kind is QueueMutationKind.RECOVER_EXPIRED_CLAIM and self.command is not None and plural_empty
        elif code is QueueResultCode.CANCELLATION_ALREADY_REQUESTED:
            valid = kind is QueueMutationKind.REQUEST_CANCELLATION and self.command is not None and plural_empty
        elif code is QueueResultCode.CANCELLATION_NOT_REQUESTED:
            valid = kind is QueueMutationKind.ACKNOWLEDGE_CANCELLATION and self.command is not None and plural_empty
        elif code is QueueResultCode.TERMINAL_OBSERVED:
            valid = kind not in {None, QueueMutationKind.ENQUEUE, QueueMutationKind.CLAIM_NEXT} and self.command is not None and plural_empty
        elif code is QueueResultCode.TRANSIENT_CONTENTION:
            valid = kind is not None and singular_empty and plural_empty
        elif code is QueueResultCode.RECONCILIATION_REQUIRED:
            valid = kind is QueueMutationKind.RECOVER_EXPIRED_CLAIM and self.command is not None and plural_empty
        else:  # pragma: no cover - closed enum
            valid = False
        if not valid:
            raise ValueError(code.value)
