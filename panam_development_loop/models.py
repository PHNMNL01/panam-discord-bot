"""Domain values for the bounded DL-P1.1 and DL-P1.2 slices."""

import hashlib
import json
from dataclasses import dataclass
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
