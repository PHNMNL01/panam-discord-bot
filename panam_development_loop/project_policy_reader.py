"""Bounded read-only Project Policy reader for DL-P1.8."""

from .models import (
    ProjectPolicy,
    ProjectPolicyReadOutcome,
    ProjectPolicyReadResult,
)
from .repositories import (
    ProjectPolicyRepository,
    RepositoryError,
    RepositoryFailureCode,
    _validate_query_identity,
)


class ProjectPolicyReader:
    """Validate caller identity and map authoritative reads to four outcomes."""

    def __init__(self, repository: ProjectPolicyRepository) -> None:
        self._repository = repository

    def read(self, project_id: str) -> ProjectPolicyReadResult:
        project_id = _validate_query_identity(project_id, "ProjectPolicy", "project_id")
        try:
            policy = self._repository.get(project_id)
        except RepositoryError as error:
            if error.code in {
                RepositoryFailureCode.MALFORMED_PERSISTED_PAYLOAD,
                RepositoryFailureCode.UNSUPPORTED_PERSISTED_VERSION,
            }:
                return ProjectPolicyReadResult(ProjectPolicyReadOutcome.INVALID_POLICY)
            return ProjectPolicyReadResult(ProjectPolicyReadOutcome.STORAGE_FAILURE)

        if policy is None:
            return ProjectPolicyReadResult(ProjectPolicyReadOutcome.PROJECT_NOT_REGISTERED)
        if type(policy) is not ProjectPolicy:
            return ProjectPolicyReadResult(ProjectPolicyReadOutcome.STORAGE_FAILURE)
        return ProjectPolicyReadResult(ProjectPolicyReadOutcome.VALID_POLICY, policy)
