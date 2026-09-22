"""Registry-backed, return-only DL-2.4 application boundary."""

from datetime import datetime, timezone
import ntpath
from typing import Protocol

from .git_inspection_models import (
    CONTRACT_SHA256, PROFILE, GitInspectionRequest, GitInspectionResult,
    InspectionEndorsement, InspectionReason as Reason, InspectionStatus as Status,
    TrustedGitInspectionContext,
)
from .models import ProjectPolicyReadOutcome
from .project_policy_reader import ProjectPolicyReader
from .repositories import RepositoryError


class GitInspectionPort(Protocol):
    def observe(self, policy, context: TrustedGitInspectionContext): ...


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _binding(policy) -> tuple[str, str, str]:
    return policy.project_id, policy.policy_version, policy.project_root


class GitInspector:
    """An authorized outer composition installs reader and independent endorsement.

    inspect() cannot replace the root, endorsement, executable or operation profile.
    This class neither authenticates a production provider nor manufactures approval.
    An untrusted caller must never control application composition/dependency injection.
    """

    def __init__(self, reader: ProjectPolicyReader, endorsement: InspectionEndorsement | None,
                 adapter: GitInspectionPort | None = None):
        if type(reader) is not ProjectPolicyReader:
            raise ValueError("existing ProjectPolicyReader required")
        if endorsement is not None and type(endorsement) is not InspectionEndorsement:
            raise ValueError("exact independent endorsement required")
        if adapter is None:
            from .git_read_only_adapter import GitReadOnlyAdapter
            adapter = GitReadOnlyAdapter()
        self._reader, self._endorsement, self._adapter = reader, endorsement, adapter

    def inspect(self, request: GitInspectionRequest) -> GitInspectionResult:
        started = _now()
        project_id = request.project_id if type(request) is GitInspectionRequest else "<invalid>"
        policy_binding = context_identity = None

        def result(reason: Reason, status: Status = Status.REJECTED, observations=(), commands=()):
            return GitInspectionResult(
                status, reason, project_id, policy_binding, context_identity, started, _now(),
                observations, commands,
                ("LOCAL_ONLY; no live remote-ref freshness",
                 "Unstaged content assessed only for exact approved tracked paths",
                 "Practical Windows checks; not a hardened OS sandbox",
                 "No temporal expiry; identity/state freshness only",
                 "Independent endorsement provisioning is outside this module"),
            )

        if type(request) is not GitInspectionRequest or request.version != "1":
            return result(Reason.INVALID_REQUEST)
        context = request.context
        if type(context) is not TrustedGitInspectionContext:
            return result(Reason.INVALID_CONTEXT)
        try:
            # Recheck even dataclass instances assembled by untrusted deserializers.
            context.__post_init__()
            context_identity = context.identity()
        except (ValueError, TypeError, AttributeError):
            return result(Reason.INVALID_CONTEXT)
        if context.context_version != "1":
            return result(Reason.UNSUPPORTED_CONTEXT_VERSION)
        if context.inspection_profile_id != PROFILE:
            return result(Reason.UNSUPPORTED_PROFILE)
        if context.project_id != project_id:
            return result(Reason.PROJECT_MISMATCH)
        endorsement = self._endorsement
        if (endorsement is None or endorsement.implementation_contract_sha256 != CONTRACT_SHA256
                or endorsement.human_authority_id != context.authority_or_policy_binding
                or endorsement.project_id != project_id or endorsement.context_id != context.context_id
                or endorsement.context_sha256 != context_identity.sha256
                or endorsement.context_byte_length != context_identity.byte_length):
            return result(Reason.UNENDORSED_CONTEXT)
        try:
            registered = self._reader.read(project_id)
        except (ValueError, TypeError, RepositoryError):
            return result(Reason.INVALID_REQUEST)
        failures = {
            ProjectPolicyReadOutcome.PROJECT_NOT_REGISTERED: Reason.PROJECT_NOT_REGISTERED,
            ProjectPolicyReadOutcome.INVALID_POLICY: Reason.INVALID_POLICY,
            ProjectPolicyReadOutcome.STORAGE_FAILURE: Reason.STORAGE_FAILURE,
        }
        if registered.outcome in failures:
            return result(failures[registered.outcome])
        policy = registered.policy
        policy_binding = _binding(policy)
        if policy.project_id != project_id:
            return result(Reason.PROJECT_MISMATCH)
        if policy.policy_version != context.project_policy_version:
            return result(Reason.POLICY_VERSION_MISMATCH)
        if ntpath.normcase(ntpath.normpath(policy.project_root)) != ntpath.normcase(ntpath.normpath(context.registered_project_root_binding)):
            return result(Reason.ROOT_MISMATCH)
        observations, commands, reason = self._adapter.observe(policy, context)
        try:
            current = self._reader.read(project_id)
        except (ValueError, TypeError, RepositoryError):
            return result(Reason.STATE_DRIFT, Status.DRIFT, observations, commands)
        if (current.outcome is not ProjectPolicyReadOutcome.VALID_POLICY or current.policy != policy
                or context.identity() != context_identity or self._endorsement != endorsement):
            return result(Reason.STATE_DRIFT, Status.DRIFT, observations, commands)
        if reason is Reason.OBSERVED:
            return result(reason, Status.COMPLETE, observations, commands)
        if reason is Reason.STATE_DRIFT:
            return result(reason, Status.DRIFT, observations, commands)
        return result(reason, Status.UNAVAILABLE, observations, commands)
