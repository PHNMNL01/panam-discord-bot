"""Standalone DL-2.5 coordination. A grant or reservation is never approval.

Deployment requires an offline v6 -> v7 upgrade; mixed-version workers are not
supported. Consumers must independently validate effect authority and evidence.
No worker, Git, reconciliation executor, or external-effect adapter is installed.
"""

from typing import Callable
from datetime import datetime

from .models import (
    LockConfiguration, LockOperation, LockOwner, LockRequest, LockResource,
    LockResult, LockTerminalEvidence,
)
from .repositories import ProjectLockRepository


class ProjectLockService:
    """Closed operations; the repository samples the clock inside its transaction.

    Terminal evidence is supplied by a trusted outer consumer that has verified
    it independently. This service records it; it does not verify external work.
    Reconciliation-required acquisitions cannot be cleared by this API.
    """

    def __init__(self, repository: ProjectLockRepository, *,
                 clock: Callable[[], datetime],
                 configuration: LockConfiguration = LockConfiguration()) -> None:
        if not callable(getattr(repository, "execute", None)) or not callable(clock):
            raise TypeError("repository/clock")
        if type(configuration) is not LockConfiguration:
            raise TypeError("configuration")
        self._repository, self._clock, self._configuration = repository, clock, configuration

    def _call(self, operation, resource, owner=None, acquisition_id=None,
              fencing_token=None, expected_revision=None, evidence=None):
        request = LockRequest(operation, resource, owner, acquisition_id,
                              fencing_token, expected_revision, evidence)
        return self._repository.execute(request, self._configuration, self._clock)

    def acquire(self, resource: LockResource, owner: LockOwner,
                acquisition_id: str) -> LockResult:
        return self._call(LockOperation.ACQUIRE, resource, owner, acquisition_id)

    def renew(self, resource: LockResource, owner: LockOwner, acquisition_id: str,
              fencing_token: int, expected_revision: int) -> LockResult:
        return self._call(LockOperation.RENEW, resource, owner, acquisition_id,
                          fencing_token, expected_revision)

    def release(self, resource: LockResource, owner: LockOwner, acquisition_id: str,
                fencing_token: int, expected_revision: int) -> LockResult:
        return self._call(LockOperation.RELEASE, resource, owner, acquisition_id,
                          fencing_token, expected_revision)

    def check_current_ownership(self, resource: LockResource, owner: LockOwner,
                                acquisition_id: str, fencing_token: int,
                                expected_revision: int) -> LockResult:
        return self._call(LockOperation.CHECK, resource, owner, acquisition_id,
                          fencing_token, expected_revision)

    def reserve_effect_window(self, resource: LockResource, owner: LockOwner,
                              acquisition_id: str, fencing_token: int,
                              expected_revision: int) -> LockResult:
        return self._call(LockOperation.RESERVE, resource, owner, acquisition_id,
                          fencing_token, expected_revision)

    def terminalize_effect_reservation(self, resource: LockResource, owner: LockOwner,
                                       acquisition_id: str, fencing_token: int,
                                       expected_revision: int,
                                       evidence: LockTerminalEvidence) -> LockResult:
        return self._call(LockOperation.TERMINALIZE, resource, owner, acquisition_id,
                          fencing_token, expected_revision, evidence)

    def observe(self, resource: LockResource) -> LockResult:
        """Read-only snapshot, including retained history; expiry never frees it."""
        return self._call(LockOperation.OBSERVE, resource)
