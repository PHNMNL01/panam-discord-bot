"""Deterministic read-only Foundation Query application layer."""

from dataclasses import dataclass
from enum import Enum
import json
from typing import TypeAlias

from .models import (
    AcceptedStateEvent,
    ApprovalBinding,
    DevelopmentRun,
    MilestoneContract,
    PhaseContract,
    ProjectPolicy,
    ProjectPolicyReadOutcome,
)
from .project_policy_reader import ProjectPolicyReader
from .repositories import (
    ApprovalBindingRepository,
    DevelopmentRunInspectionRepository,
    MilestoneContractRepository,
    PhaseContractRepository,
    RepositoryError,
    RepositoryFailureCode,
)


class FoundationQueryOutcome(str, Enum):
    SUCCESS = "SUCCESS"
    INVALID_CLI_INPUT = "INVALID_CLI_INPUT"
    NOT_FOUND = "NOT_FOUND"
    INVALID_PERSISTED_FOUNDATION_DATA = "INVALID_PERSISTED_FOUNDATION_DATA"
    STORAGE_FAILURE = "STORAGE_FAILURE"


ScalarValue: TypeAlias = str | int


@dataclass(frozen=True)
class StructuredObject:
    fields: tuple[tuple[str, "PresentationValue"], ...]


@dataclass(frozen=True)
class StructuredCollection:
    items: tuple[StructuredObject, ...]


PresentationValue: TypeAlias = ScalarValue | tuple[str, ...] | StructuredCollection


@dataclass(frozen=True)
class FoundationQueryResult:
    operation: str
    outcome: FoundationQueryOutcome
    fields: tuple[tuple[str, PresentationValue], ...] = ()


class FoundationQueryService:
    """Dispatches only the five accepted, side-effect-free foundation queries."""

    def __init__(
        self,
        phase_repository: PhaseContractRepository,
        milestone_repository: MilestoneContractRepository,
        approval_repository: ApprovalBindingRepository,
        run_repository: DevelopmentRunInspectionRepository,
        project_policy_reader: ProjectPolicyReader,
    ) -> None:
        self._phase_repository = phase_repository
        self._milestone_repository = milestone_repository
        self._approval_repository = approval_repository
        self._run_repository = run_repository
        self._project_policy_reader = project_policy_reader

    def phase(self, project_id: str, phase_id: str) -> FoundationQueryResult:
        return self._read(
            "phase",
            lambda: self._phase_repository.get(project_id, phase_id),
            _phase_fields,
        )

    def milestone(
        self,
        project_id: str,
        phase_id: str,
        milestone_id: str,
    ) -> FoundationQueryResult:
        return self._read(
            "milestone",
            lambda: self._milestone_repository.get(project_id, phase_id, milestone_id),
            _milestone_fields,
        )

    def approval(self, approval_id: str) -> FoundationQueryResult:
        return self._read(
            "approval",
            lambda: self._approval_repository.get(approval_id),
            _approval_fields,
        )

    def run(self, run_id: str) -> FoundationQueryResult:
        try:
            run = self._run_repository.get_run(run_id)
            if run is None:
                return FoundationQueryResult("run", FoundationQueryOutcome.NOT_FOUND)
            history = self._run_repository.get_history(run_id)
        except RepositoryError as error:
            return _repository_error_result("run", error)
        return FoundationQueryResult("run", FoundationQueryOutcome.SUCCESS, _run_fields(run, history))

    def project_policy(self, project_id: str) -> FoundationQueryResult:
        try:
            result = self._project_policy_reader.read(project_id)
        except RepositoryError as error:
            return _repository_error_result("project-policy", error)
        if result.outcome is ProjectPolicyReadOutcome.VALID_POLICY:
            assert result.policy is not None
            return FoundationQueryResult(
                "project-policy",
                FoundationQueryOutcome.SUCCESS,
                _project_policy_fields(result.policy),
            )
        mapping = {
            ProjectPolicyReadOutcome.PROJECT_NOT_REGISTERED: FoundationQueryOutcome.NOT_FOUND,
            ProjectPolicyReadOutcome.INVALID_POLICY: FoundationQueryOutcome.INVALID_PERSISTED_FOUNDATION_DATA,
            ProjectPolicyReadOutcome.STORAGE_FAILURE: FoundationQueryOutcome.STORAGE_FAILURE,
        }
        return FoundationQueryResult("project-policy", mapping[result.outcome])

    @staticmethod
    def _read(
        operation: str,
        reader: object,
        formatter: object,
    ) -> FoundationQueryResult:
        try:
            value = reader()  # type: ignore[operator]
        except RepositoryError as error:
            return _repository_error_result(operation, error)
        if value is None:
            return FoundationQueryResult(operation, FoundationQueryOutcome.NOT_FOUND)
        return FoundationQueryResult(
            operation,
            FoundationQueryOutcome.SUCCESS,
            formatter(value),  # type: ignore[operator]
        )


def _repository_error_result(operation: str, error: RepositoryError) -> FoundationQueryResult:
    if error.code is RepositoryFailureCode.INVALID_IDENTITY:
        outcome = FoundationQueryOutcome.INVALID_CLI_INPUT
    elif error.code in {
        RepositoryFailureCode.MALFORMED_PERSISTED_PAYLOAD,
        RepositoryFailureCode.UNSUPPORTED_PERSISTED_VERSION,
    }:
        outcome = FoundationQueryOutcome.INVALID_PERSISTED_FOUNDATION_DATA
    else:
        outcome = FoundationQueryOutcome.STORAGE_FAILURE
    return FoundationQueryResult(operation, outcome)


def _phase_fields(contract: PhaseContract) -> tuple[tuple[str, PresentationValue], ...]:
    return (
        ("project_id", contract.project_id),
        ("phase_id", contract.phase_id),
        ("contract_version", contract.contract_version.value),
        ("contract_digest", contract.sha256_digest()),
    )


def _milestone_fields(contract: MilestoneContract) -> tuple[tuple[str, PresentationValue], ...]:
    return (
        ("project_id", contract.project_id),
        ("phase_id", contract.phase_id),
        ("milestone_id", contract.milestone_id),
        ("contract_version", contract.contract_version.value),
        ("objective", contract.objective),
        ("scope", contract.scope),
        ("exclusions", contract.exclusions),
        ("acceptance_criteria", contract.acceptance_criteria),
        ("allowed_paths", contract.allowed_paths),
        ("forbidden_paths", contract.forbidden_paths),
        ("verification_plan", contract.verification_plan),
        ("stop_conditions", contract.stop_conditions),
        ("contract_digest", contract.sha256_digest()),
    )


def _approval_fields(binding: ApprovalBinding) -> tuple[tuple[str, PresentationValue], ...]:
    return (
        ("approval_id", binding.approval_id),
        ("approval_version", binding.approval_version.value),
        ("approval_kind", binding.approval_kind.value),
        ("subject_id", binding.subject_id),
        ("subject_digest", binding.subject_digest),
        ("target_kind", binding.target_kind.value),
        ("target_id", binding.target_id),
        ("target_branch", binding.target_branch),
        ("base_commit", binding.base_commit),
        ("allowed_actions", binding.allowed_actions),
        ("allowed_paths", binding.allowed_paths),
        ("approver_id", binding.approver_id),
        ("approved_at", binding.approved_at),
        ("binding_digest", binding.sha256_digest()),
    )


def _run_fields(
    run: DevelopmentRun,
    history: list[AcceptedStateEvent],
) -> tuple[tuple[str, PresentationValue], ...]:
    return (
        ("run_id", run.run_id),
        ("milestone_contract_digest", run.milestone_contract_digest),
        ("current_state", run.current_state.value),
        ("state_version", run.state_version),
        ("created_at", run.created_at),
        ("updated_at", run.updated_at),
        (
            "state_events",
            StructuredCollection(tuple(_state_event_object(event) for event in history)),
        ),
    )


def _state_event_object(event: AcceptedStateEvent) -> StructuredObject:
    return StructuredObject(
        (
            ("event_id", event.event_id),
            ("run_id", event.run_id),
            ("from_state", event.from_state.value),
            ("to_state", event.to_state.value),
            ("transition_reason", event.transition_reason),
            ("occurred_at", event.occurred_at),
            ("state_version", event.state_version),
        )
    )


def _project_policy_fields(policy: ProjectPolicy) -> tuple[tuple[str, PresentationValue], ...]:
    return (
        ("result_category", ProjectPolicyReadOutcome.VALID_POLICY.value),
        ("project_id", policy.project_id),
        ("policy_version", policy.policy_version.value),
        ("project_root", policy.project_root),
    )


def render_text(result: FoundationQueryResult) -> str:
    """Render an already successful result with contract-stable field order."""

    lines: list[str] = []
    for field_name, value in result.fields:
        if isinstance(value, StructuredCollection):
            lines.append(f"{field_name}:")
            for item in value.items:
                for index, (nested_name, nested_value) in enumerate(item.fields):
                    prefix = "- " if index == 0 else "  "
                    lines.append(f"{prefix}{nested_name}: {_scalar_text(nested_value)}")
        elif isinstance(value, tuple):
            lines.append(f"{field_name}:")
            lines.extend(f"- {item}" for item in value)
        else:
            lines.append(f"{field_name}: {_scalar_text(value)}")
    return "\n".join(lines) + "\n"


def render_json(result: FoundationQueryResult) -> str:
    """Render an already successful result as one stable UTF-8 JSON object."""

    payload = {field_name: _json_value(value) for field_name, value in result.fields}
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"


def render_error(outcome: FoundationQueryOutcome, *, as_json: bool) -> str:
    if as_json:
        return json.dumps({"error_code": outcome.value}, separators=(",", ":")) + "\n"
    return f"error_code: {outcome.value}\n"


def exit_code_for(outcome: FoundationQueryOutcome) -> int:
    return {
        FoundationQueryOutcome.SUCCESS: 0,
        FoundationQueryOutcome.INVALID_CLI_INPUT: 2,
        FoundationQueryOutcome.NOT_FOUND: 3,
        FoundationQueryOutcome.INVALID_PERSISTED_FOUNDATION_DATA: 4,
        FoundationQueryOutcome.STORAGE_FAILURE: 5,
    }[outcome]


def _scalar_text(value: PresentationValue) -> str:
    if isinstance(value, (str, int)):
        return str(value)
    raise TypeError("non-scalar presentation value")


def _json_value(value: PresentationValue) -> object:
    if isinstance(value, StructuredCollection):
        return [
            {field_name: _json_value(nested_value) for field_name, nested_value in item.fields}
            for item in value.items
        ]
    if isinstance(value, tuple):
        return list(value)
    return value
