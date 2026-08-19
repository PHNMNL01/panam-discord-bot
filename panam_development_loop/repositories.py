"""Transport-independent repository ports for DL-P1.5 foundation entities."""

from enum import Enum
from typing import Protocol

from .models import ApprovalBinding, MilestoneContract, PhaseContract, ProjectPolicy


class RepositoryFailureCode(str, Enum):
    """Stable failure categories exposed by the repository boundary."""

    INVALID_IDENTITY = "INVALID_IDENTITY"
    DUPLICATE_ENTITY = "DUPLICATE_ENTITY"
    RELATIONSHIP_VIOLATION = "RELATIONSHIP_VIOLATION"
    MALFORMED_PERSISTED_PAYLOAD = "MALFORMED_PERSISTED_PAYLOAD"
    UNSUPPORTED_PERSISTED_VERSION = "UNSUPPORTED_PERSISTED_VERSION"
    SQLITE_OPERATIONAL_FAILURE = "SQLITE_OPERATIONAL_FAILURE"
    SCHEMA_MISMATCH = "SCHEMA_MISMATCH"


class RepositoryError(RuntimeError):
    """A deterministic repository failure without infrastructure leakage."""

    def __init__(
        self,
        code: RepositoryFailureCode,
        entity_name: str,
        identity: str,
    ) -> None:
        self.code = code
        self.entity_name = entity_name
        self.identity = identity
        super().__init__(f"{code.value}: {entity_name}: {identity}")


def _validate_query_identity(value: object, entity_name: str, field_name: str) -> str:
    identity = f"{field_name}={value!r}"
    if not isinstance(value, str) or not value or value != value.strip():
        raise RepositoryError(RepositoryFailureCode.INVALID_IDENTITY, entity_name, identity)
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise RepositoryError(RepositoryFailureCode.INVALID_IDENTITY, entity_name, identity)
    return value


class PhaseContractRepository(Protocol):
    def create(self, contract: PhaseContract) -> PhaseContract: ...

    def get(self, project_id: str, phase_id: str) -> PhaseContract | None: ...


class MilestoneContractRepository(Protocol):
    def create(self, contract: MilestoneContract) -> MilestoneContract: ...

    def get(
        self,
        project_id: str,
        phase_id: str,
        milestone_id: str,
    ) -> MilestoneContract | None: ...


class ApprovalBindingRepository(Protocol):
    def create(self, binding: ApprovalBinding) -> ApprovalBinding: ...

    def get(self, approval_id: str) -> ApprovalBinding | None: ...


class ProjectPolicyRepository(Protocol):
    """Get-only authoritative Project Registry port for DL-P1.8."""

    def get(self, project_id: str) -> ProjectPolicy | None: ...
