"""Pure, deterministic transition policy for DL-P1.1 and DL-P1.6."""

import re
from datetime import datetime
from types import MappingProxyType

from .models import (
    ApprovalBinding,
    ApprovalSnapshot,
    ApprovalSnapshotStatus,
    DevelopmentRun,
    DevelopmentRunState,
    EscalationTrigger,
    EvidenceKind,
    EvidenceSnapshot,
    EvidenceVerdict,
    ExpectedEvidenceBinding,
    MilestoneContract,
    RepositoryEvidenceBinding,
    SnapshotProducerKind,
    TransitionEvaluationDecision,
    TransitionEvaluationReasonCode,
    TransitionEvaluationRequest,
    TransitionEvaluationResult,
    TransitionRequirement,
    TransitionRule,
    TransitionRuleId,
    WorkflowEdgeType,
    WorkflowNodeType,
)


_VERSION_PATTERN = re.compile(r"\A[1-9][0-9]*\Z", re.ASCII)
_RULE_ID_PATTERN = re.compile(
    r"\A[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)*_V1\Z",
    re.ASCII,
)
_SHA256_PATTERN = re.compile(r"\A[0-9a-f]{64}\Z", re.ASCII)
_COMMIT_PATTERN = re.compile(r"\A[0-9a-f]{40}\Z", re.ASCII)
_TIMESTAMP_PATTERN = re.compile(
    r"\A([0-9]{4})-([0-9]{2})-([0-9]{2})T"
    r"([0-9]{2}):([0-9]{2}):([0-9]{2})(?:\.([0-9]{6}))?Z\Z",
    re.ASCII,
)
_ARTIFACT_PATH_PATTERN = re.compile(
    r"\A[A-Z]:\\[A-Za-z0-9 _().-]{1,255}"
    r"(?:\\[A-Za-z0-9 _().-]{1,255})*\Z",
    re.ASCII,
)


def _valid_text(value: object, *, maximum: int | None = None) -> bool:
    return (
        type(value) is str
        and bool(value)
        and (maximum is None or len(value) <= maximum)
        and value == value.strip()
        and not any(ord(character) < 32 or ord(character) == 127 for character in value)
    )


def _valid_sha256(value: object) -> bool:
    return type(value) is str and _SHA256_PATTERN.fullmatch(value) is not None


def _valid_timestamp(value: object) -> bool:
    if type(value) is not str:
        return False
    match = _TIMESTAMP_PATTERN.fullmatch(value)
    if match is None:
        return False
    year, month, day, hour, minute, second = (
        int(part) for part in match.groups()[:6]
    )
    microsecond = int(match.group(7) or "0")
    try:
        datetime(year, month, day, hour, minute, second, microsecond)
    except ValueError:
        return False
    return True


def _valid_artifact_path(value: object) -> bool:
    if (
        type(value) is not str
        or not 4 <= len(value) <= 1024
        or _ARTIFACT_PATH_PATTERN.fullmatch(value) is None
    ):
        return False
    segments = value[3:].split("\\")
    return all(
        segment not in {".", ".."}
        and not segment.endswith(".")
        and not segment.endswith(" ")
        for segment in segments
    )


def _valid_run(value: object) -> bool:
    return (
        type(value) is DevelopmentRun
        and _valid_text(value.run_id, maximum=255)
        and _valid_sha256(value.milestone_contract_digest)
        and type(value.current_state) is DevelopmentRunState
        and type(value.state_version) is int
        and value.state_version >= 0
        and _valid_timestamp(value.created_at)
        and _valid_timestamp(value.updated_at)
    )


def _run_failure(value: object) -> TransitionEvaluationReasonCode | None:
    if value is None:
        return TransitionEvaluationReasonCode.REQUEST_REQUIRED_FIELD_MISSING
    if type(value) is not DevelopmentRun:
        return TransitionEvaluationReasonCode.REQUEST_WRONG_TYPE
    if not _valid_text(value.run_id, maximum=255):
        return TransitionEvaluationReasonCode.REQUEST_WRONG_TYPE
    if not _valid_sha256(value.milestone_contract_digest):
        return TransitionEvaluationReasonCode.REQUEST_WRONG_TYPE
    if type(value.current_state) is not DevelopmentRunState:
        return TransitionEvaluationReasonCode.REQUEST_UNKNOWN_SOURCE_STATE
    if type(value.state_version) is not int or value.state_version < 0:
        return TransitionEvaluationReasonCode.REQUEST_WRONG_TYPE
    if not _valid_timestamp(value.created_at):
        return TransitionEvaluationReasonCode.REQUEST_WRONG_TYPE
    if not _valid_timestamp(value.updated_at):
        return TransitionEvaluationReasonCode.REQUEST_WRONG_TYPE
    return None


def _valid_milestone_contract(value: object) -> bool:
    if type(value) is not MilestoneContract:
        return False
    try:
        reconstructed = MilestoneContract(
            project_id=value.project_id,
            phase_id=value.phase_id,
            milestone_id=value.milestone_id,
            contract_version=value.contract_version,
            objective=value.objective,
            scope=value.scope,
            exclusions=value.exclusions,
            acceptance_criteria=value.acceptance_criteria,
            allowed_paths=value.allowed_paths,
            forbidden_paths=value.forbidden_paths,
            verification_plan=value.verification_plan,
            stop_conditions=value.stop_conditions,
        )
        return reconstructed == value
    except (AttributeError, TypeError, ValueError):
        return False


def _valid_repository_binding(value: object) -> bool:
    return (
        type(value) is RepositoryEvidenceBinding
        and _valid_text(value.repository_id)
        and _valid_text(value.branch)
        and type(value.head_commit) is str
        and _COMMIT_PATTERN.fullmatch(value.head_commit) is not None
        and _valid_sha256(value.worktree_digest)
    )


def _valid_expected_evidence(value: object) -> bool:
    return (
        type(value) is ExpectedEvidenceBinding
        and _valid_text(value.assessment_authority_id)
        and _valid_artifact_path(value.artifact_path)
        and type(value.artifact_byte_count) is int
        and value.artifact_byte_count >= 0
        and _valid_sha256(value.artifact_sha256)
    )


def _valid_approval_binding(value: object) -> bool:
    if type(value) is not ApprovalBinding:
        return False
    try:
        reconstructed = ApprovalBinding(
            approval_id=value.approval_id,
            approval_version=value.approval_version,
            approval_kind=value.approval_kind,
            subject_id=value.subject_id,
            subject_digest=value.subject_digest,
            target_kind=value.target_kind,
            target_id=value.target_id,
            target_branch=value.target_branch,
            base_commit=value.base_commit,
            allowed_actions=value.allowed_actions,
            allowed_paths=value.allowed_paths,
            approver_id=value.approver_id,
            approved_at=value.approved_at,
        )
        return reconstructed == value
    except (AttributeError, TypeError, ValueError):
        return False


def _valid_snapshot_provenance(value: object) -> bool:
    return (
        type(value.snapshot_version) is str
        and value.snapshot_version == "1"
        and type(value.producer_kind) is SnapshotProducerKind
        and _valid_text(value.run_id)
        and _valid_text(value.milestone_id)
        and _valid_sha256(value.milestone_contract_digest)
        and type(value.source_state) is DevelopmentRunState
        and type(value.state_version) is int
        and value.state_version >= 0
        and type(value.rule_id) is TransitionRuleId
    )


def _valid_approval_snapshot(value: object) -> bool:
    if type(value) is not ApprovalSnapshot or not _valid_snapshot_provenance(value):
        return False
    if type(value.status) is not ApprovalSnapshotStatus:
        return False
    if value.status is ApprovalSnapshotStatus.PRESENT:
        return (
            _valid_approval_binding(value.approval_binding)
            and _valid_sha256(value.approval_binding_digest)
            and value.approval_binding_digest
            == value.approval_binding.sha256_digest()
        )
    return value.approval_binding is None and value.approval_binding_digest is None


def _valid_evidence_snapshot(value: object) -> bool:
    return (
        type(value) is EvidenceSnapshot
        and _valid_snapshot_provenance(value)
        and type(value.evidence_kind) is EvidenceKind
        and _valid_text(value.assessment_authority_id)
        and _valid_artifact_path(value.artifact_path)
        and type(value.artifact_byte_count) is int
        and value.artifact_byte_count >= 0
        and _valid_sha256(value.artifact_sha256)
        and type(value.verdict) is EvidenceVerdict
        and type(value.ready_for_approval_1) is bool
        and _valid_timestamp(value.assessed_at)
        and _valid_repository_binding(value.repository_binding)
    )


def _result(
    decision: TransitionEvaluationDecision,
    reason: TransitionEvaluationReasonCode,
    *,
    run: DevelopmentRun | None = None,
    requested_state: DevelopmentRunState | None = None,
    rule_id: TransitionRuleId | None = None,
    edge_type: WorkflowEdgeType | None = None,
    node_type: WorkflowNodeType | None = None,
    missing: tuple[TransitionRequirement, ...] = (),
) -> TransitionEvaluationResult:
    return TransitionEvaluationResult(
        decision=decision,
        reason_code=reason,
        run_id=None if run is None else run.run_id,
        current_state=None if run is None else run.current_state,
        current_state_version=None if run is None else run.state_version,
        requested_state=requested_state,
        rule_id=rule_id,
        edge_type=edge_type,
        node_type=node_type,
        missing_requirements=missing,
    )


def _late_malformed_reason(
    request: TransitionEvaluationRequest,
    eligible: set[str] | None = None,
) -> TransitionEvaluationReasonCode | None:
    def applies(name: str) -> bool:
        return eligible is None or name in eligible

    if (
        applies("milestone_contract")
        and request.milestone_contract is not None
        and not _valid_milestone_contract(request.milestone_contract)
    ):
        return TransitionEvaluationReasonCode.CONTRACT_MALFORMED
    if (
        applies("expected_evidence")
        and request.expected_evidence is not None
        and not _valid_expected_evidence(request.expected_evidence)
    ):
        return TransitionEvaluationReasonCode.EXPECTED_EVIDENCE_ANCHOR_MALFORMED
    if (
        applies("repository_binding")
        and request.repository_binding is not None
        and not _valid_repository_binding(request.repository_binding)
    ):
        return TransitionEvaluationReasonCode.REPOSITORY_BINDING_MALFORMED
    if (
        applies("approval_snapshot")
        and request.approval_snapshot is not None
        and not _valid_approval_snapshot(request.approval_snapshot)
    ):
        return TransitionEvaluationReasonCode.APPROVAL_SNAPSHOT_MALFORMED
    if applies("evidence_snapshots"):
        for snapshot in request.evidence_snapshots:
            if not _valid_evidence_snapshot(snapshot):
                return TransitionEvaluationReasonCode.EVIDENCE_SNAPSHOT_MALFORMED
    return None


def _has_unregistered_surplus(request: TransitionEvaluationRequest) -> bool:
    supplied = (
        request.milestone_contract is not None
        or request.expected_evidence is not None
        or request.repository_binding is not None
        or request.approval_snapshot is not None
        or bool(request.evidence_snapshots)
        or request.retry_budget_snapshot is not None
    )
    if request.edge_type is WorkflowEdgeType.ESCALATION:
        return supplied
    return supplied or request.escalation_trigger is not None


def _undeclared_names(rule: TransitionRule) -> set[str]:
    names: set[str] = set()
    if TransitionRequirement.CONTRACT not in rule.requirements:
        names.add("milestone_contract")
    if TransitionRequirement.EVIDENCE not in rule.requirements:
        names.update(("expected_evidence", "evidence_snapshots"))
    if TransitionRequirement.REPOSITORY not in rule.requirements:
        names.add("repository_binding")
    if TransitionRequirement.APPROVAL not in rule.requirements:
        names.add("approval_snapshot")
    return names


def _has_registered_surplus(
    request: TransitionEvaluationRequest,
    rule: TransitionRule,
    undeclared: set[str],
) -> bool:
    return (
        ("milestone_contract" in undeclared and request.milestone_contract is not None)
        or ("expected_evidence" in undeclared and request.expected_evidence is not None)
        or ("repository_binding" in undeclared and request.repository_binding is not None)
        or ("approval_snapshot" in undeclared and request.approval_snapshot is not None)
        or ("evidence_snapshots" in undeclared and bool(request.evidence_snapshots))
        or (
            TransitionRequirement.RETRY_BUDGET not in rule.requirements
            and request.retry_budget_snapshot is not None
        )
        or (
            TransitionRequirement.ESCALATION not in rule.requirements
            and request.escalation_trigger is not None
        )
    )


_DRAFT_RULE = TransitionRule(
    rule_id=TransitionRuleId.P1_6_DRAFT_TO_FEASIBILITY_CHECKING_V1,
    source_state=DevelopmentRunState.DRAFT,
    target_state=DevelopmentRunState.FEASIBILITY_CHECKING,
    edge_type=WorkflowEdgeType.UNCONDITIONAL,
    requirements=(TransitionRequirement.CONTRACT,),
)
_FEASIBILITY_RULE = TransitionRule(
    rule_id=(
        TransitionRuleId.P1_6_FEASIBILITY_CHECKING_TO_AWAITING_EXECUTION_APPROVAL_V1
    ),
    source_state=DevelopmentRunState.FEASIBILITY_CHECKING,
    target_state=DevelopmentRunState.AWAITING_EXECUTION_APPROVAL,
    edge_type=WorkflowEdgeType.EVIDENCE_GATED,
    requirements=(
        TransitionRequirement.CONTRACT,
        TransitionRequirement.REPOSITORY,
        TransitionRequirement.EVIDENCE,
    ),
    evidence_kind_requirement=EvidenceKind.FEASIBILITY_ASSESSMENT,
    evidence_producer_kind_requirement=SnapshotProducerKind.FEASIBILITY_ASSESSOR,
)
_RULES_BY_ID = MappingProxyType(
    {
        _DRAFT_RULE.rule_id: _DRAFT_RULE,
        _FEASIBILITY_RULE.rule_id: _FEASIBILITY_RULE,
    }
)
_RULES_BY_PAIR = MappingProxyType(
    {
        (_DRAFT_RULE.source_state, _DRAFT_RULE.target_state): _DRAFT_RULE,
        (
            _FEASIBILITY_RULE.source_state,
            _FEASIBILITY_RULE.target_state,
        ): _FEASIBILITY_RULE,
    }
)
if len(_RULES_BY_ID) != 2 or len(_RULES_BY_PAIR) != 2:
    raise RuntimeError("invalid P1.6 transition registry")


class TransitionPolicy:
    """Preserves legacy boolean policy and adds pure P1.6 evaluation."""

    _ALLOWED = frozenset(
        {
            (
                DevelopmentRunState.DRAFT,
                DevelopmentRunState.FEASIBILITY_CHECKING,
            ),
            (
                DevelopmentRunState.FEASIBILITY_CHECKING,
                DevelopmentRunState.AWAITING_EXECUTION_APPROVAL,
            ),
        }
    )
    _RULES_BY_ID = _RULES_BY_ID
    _RULES_BY_PAIR = _RULES_BY_PAIR

    def allows(
        self,
        current_state: DevelopmentRunState,
        requested_state: DevelopmentRunState,
    ) -> bool:
        return (current_state, requested_state) in self._ALLOWED

    def evaluate(self, candidate: object) -> TransitionEvaluationResult:
        # Stage 1: request boundary and closed early-structural ownership.
        if type(candidate) is not TransitionEvaluationRequest:
            return _result(
                TransitionEvaluationDecision.INVALID,
                TransitionEvaluationReasonCode.REQUEST_NOT_EVALUATION_REQUEST,
            )
        request = candidate
        if request.evaluation_version is None:
            return _result(
                TransitionEvaluationDecision.INVALID,
                TransitionEvaluationReasonCode.REQUEST_REQUIRED_FIELD_MISSING,
            )
        if type(request.evaluation_version) is not str:
            return _result(
                TransitionEvaluationDecision.INVALID,
                TransitionEvaluationReasonCode.REQUEST_WRONG_TYPE,
            )
        if _VERSION_PATTERN.fullmatch(request.evaluation_version) is None:
            return _result(
                TransitionEvaluationDecision.INVALID,
                TransitionEvaluationReasonCode.EVALUATION_VERSION_MALFORMED,
            )

        run_failure = _run_failure(request.run)
        if run_failure is not None:
            return _result(TransitionEvaluationDecision.INVALID, run_failure)
        run = request.run
        assert run is not None

        if request.rule_id is None:
            return _result(
                TransitionEvaluationDecision.INVALID,
                TransitionEvaluationReasonCode.REQUEST_REQUIRED_FIELD_MISSING,
                run=run,
            )
        if (
            type(request.rule_id) is not str
            or not 4 <= len(request.rule_id) <= 128
            or _RULE_ID_PATTERN.fullmatch(request.rule_id) is None
        ):
            return _result(
                TransitionEvaluationDecision.INVALID,
                TransitionEvaluationReasonCode.REQUEST_RULE_ID_MALFORMED,
                run=run,
            )
        if request.requested_state is None:
            return _result(
                TransitionEvaluationDecision.INVALID,
                TransitionEvaluationReasonCode.REQUEST_REQUIRED_FIELD_MISSING,
                run=run,
            )
        if type(request.requested_state) is not DevelopmentRunState:
            return _result(
                TransitionEvaluationDecision.INVALID,
                TransitionEvaluationReasonCode.REQUEST_UNKNOWN_TARGET_STATE,
                run=run,
            )
        requested_state = request.requested_state
        if request.edge_type is None:
            return _result(
                TransitionEvaluationDecision.INVALID,
                TransitionEvaluationReasonCode.REQUEST_REQUIRED_FIELD_MISSING,
                run=run,
                requested_state=requested_state,
            )
        if type(request.edge_type) is not WorkflowEdgeType:
            return _result(
                TransitionEvaluationDecision.INVALID,
                TransitionEvaluationReasonCode.REQUEST_UNKNOWN_ENUM_VALUE,
                run=run,
                requested_state=requested_state,
            )
        if request.node_type is not None and type(request.node_type) is not WorkflowNodeType:
            return _result(
                TransitionEvaluationDecision.INVALID,
                TransitionEvaluationReasonCode.REQUEST_WRONG_TYPE,
                run=run,
                requested_state=requested_state,
            )
        if type(request.evidence_snapshots) is not tuple:
            return _result(
                TransitionEvaluationDecision.INVALID,
                TransitionEvaluationReasonCode.REQUEST_WRONG_TYPE,
                run=run,
                requested_state=requested_state,
            )
        if request.escalation_trigger is not None and type(
            request.escalation_trigger
        ) is not EscalationTrigger:
            return _result(
                TransitionEvaluationDecision.INVALID,
                TransitionEvaluationReasonCode.ESCALATION_SNAPSHOT_MALFORMED,
                run=run,
                requested_state=requested_state,
            )

        # Stage 2: supported evaluator schema.
        if request.evaluation_version != "1":
            return _result(
                TransitionEvaluationDecision.UNSUPPORTED,
                TransitionEvaluationReasonCode.EVALUATION_VERSION_UNSUPPORTED,
                run=run,
                requested_state=requested_state,
            )

        # Stage 3: the sole closed request-wide contradiction.
        if (
            request.retry_budget_snapshot is not None
            and request.escalation_trigger is not None
        ):
            return _result(
                TransitionEvaluationDecision.INVALID,
                TransitionEvaluationReasonCode.REQUEST_CONTRADICTORY_FACTS,
                run=run,
                requested_state=requested_state,
            )

        pair = (run.current_state, requested_state)
        matched_rule = self._RULES_BY_PAIR.get(pair)

        # Stage 4: unregistered pair structure, applicability, and support.
        if matched_rule is None:
            malformed = _late_malformed_reason(request)
            if malformed is not None:
                return _result(
                    TransitionEvaluationDecision.INVALID,
                    malformed,
                    run=run,
                    requested_state=requested_state,
                )
            if _has_unregistered_surplus(request):
                return _result(
                    TransitionEvaluationDecision.INVALID,
                    TransitionEvaluationReasonCode.REQUEST_SURPLUS_PREREQUISITE,
                    run=run,
                    requested_state=requested_state,
                )
            if request.edge_type is WorkflowEdgeType.ESCALATION:
                if request.escalation_trigger is None:
                    return _result(
                        TransitionEvaluationDecision.INVALID,
                        TransitionEvaluationReasonCode.ESCALATION_SNAPSHOT_MALFORMED,
                        run=run,
                        requested_state=requested_state,
                    )
                deferred = TransitionEvaluationReasonCode.ESCALATION_RULE_DEFERRED
            else:
                deferred_reasons = {
                    WorkflowEdgeType.RETRY: TransitionEvaluationReasonCode.RETRY_RULE_DEFERRED,
                    WorkflowEdgeType.TERMINAL: TransitionEvaluationReasonCode.TERMINAL_RULE_DEFERRED,
                    WorkflowEdgeType.STATE_CONDITIONAL: (
                        TransitionEvaluationReasonCode.STATE_CONDITIONAL_RULE_DEFERRED
                    ),
                    WorkflowEdgeType.APPROVAL_GATED: (
                        TransitionEvaluationReasonCode.APPROVAL_GATED_RULE_DEFERRED
                    ),
                }
                deferred = deferred_reasons.get(request.edge_type)
            if deferred is not None:
                return _result(
                    TransitionEvaluationDecision.UNSUPPORTED,
                    deferred,
                    run=run,
                    requested_state=requested_state,
                )
            return _result(
                TransitionEvaluationDecision.DENIED,
                TransitionEvaluationReasonCode.REGISTRY_NO_REGISTERED_TRANSITION,
                run=run,
                requested_state=requested_state,
            )

        # Stage 5: registered rule coherence and canonical applicability.
        requested_rule_id = next(
            (
                rule_id
                for rule_id in TransitionRuleId
                if rule_id.value == request.rule_id
            ),
            None,
        )
        if requested_rule_id is None:
            return _result(
                TransitionEvaluationDecision.UNSUPPORTED,
                TransitionEvaluationReasonCode.REGISTRY_RULE_ID_UNSUPPORTED,
                run=run,
                requested_state=requested_state,
            )
        requested_rule = self._RULES_BY_ID[requested_rule_id]
        if requested_rule.source_state is not run.current_state:
            return _result(
                TransitionEvaluationDecision.INVALID,
                TransitionEvaluationReasonCode.REGISTRY_RULE_SOURCE_MISMATCH,
                run=run,
                requested_state=requested_state,
                rule_id=requested_rule_id,
            )
        if requested_rule.target_state is not requested_state:
            return _result(
                TransitionEvaluationDecision.INVALID,
                TransitionEvaluationReasonCode.REGISTRY_RULE_TARGET_MISMATCH,
                run=run,
                requested_state=requested_state,
                rule_id=requested_rule_id,
            )
        if request.edge_type is not requested_rule.edge_type:
            malformed = _late_malformed_reason(request)
            if malformed is not None:
                return _result(
                    TransitionEvaluationDecision.INVALID,
                    malformed,
                    run=run,
                    requested_state=requested_state,
                    rule_id=requested_rule_id,
                )
            return _result(
                TransitionEvaluationDecision.INVALID,
                TransitionEvaluationReasonCode.REGISTRY_EDGE_TYPE_MISMATCH,
                run=run,
                requested_state=requested_state,
                rule_id=requested_rule_id,
            )
        if request.node_type is not requested_rule.node_type_constraint:
            return _result(
                TransitionEvaluationDecision.INVALID,
                TransitionEvaluationReasonCode.REGISTRY_NODE_METADATA_MISMATCH,
                run=run,
                requested_state=requested_state,
                rule_id=requested_rule_id,
                edge_type=requested_rule.edge_type,
            )
        undeclared = _undeclared_names(requested_rule)
        malformed = _late_malformed_reason(request, undeclared)
        if malformed is not None:
            return _result(
                TransitionEvaluationDecision.INVALID,
                malformed,
                run=run,
                requested_state=requested_state,
                rule_id=requested_rule_id,
                edge_type=requested_rule.edge_type,
            )
        if _has_registered_surplus(request, requested_rule, undeclared):
            return _result(
                TransitionEvaluationDecision.INVALID,
                TransitionEvaluationReasonCode.REQUEST_SURPLUS_PREREQUISITE,
                run=run,
                requested_state=requested_state,
                rule_id=requested_rule_id,
                edge_type=requested_rule.edge_type,
            )

        # Stage 6: contract structure and immutable digest binding.
        if request.milestone_contract is None:
            return _result(
                TransitionEvaluationDecision.GATED,
                TransitionEvaluationReasonCode.CONTRACT_REQUIRED,
                run=run,
                requested_state=requested_state,
                rule_id=requested_rule_id,
                edge_type=requested_rule.edge_type,
                missing=(TransitionRequirement.CONTRACT,),
            )
        if not _valid_milestone_contract(request.milestone_contract):
            return _result(
                TransitionEvaluationDecision.INVALID,
                TransitionEvaluationReasonCode.CONTRACT_MALFORMED,
                run=run,
                requested_state=requested_state,
                rule_id=requested_rule_id,
                edge_type=requested_rule.edge_type,
            )
        contract_digest = request.milestone_contract.sha256_digest()
        if contract_digest != run.milestone_contract_digest:
            return _result(
                TransitionEvaluationDecision.INVALID,
                TransitionEvaluationReasonCode.CONTRACT_BINDING_MISMATCH,
                run=run,
                requested_state=requested_state,
                rule_id=requested_rule_id,
                edge_type=requested_rule.edge_type,
            )

        # Stages 7 and 9 are structurally unreachable for both registered rules.
        if TransitionRequirement.EVIDENCE in requested_rule.requirements:
            # Stage 8: exact evidence first-match matrix.
            if request.expected_evidence is None:
                return _result(
                    TransitionEvaluationDecision.INVALID,
                    TransitionEvaluationReasonCode.EXPECTED_EVIDENCE_ANCHOR_MISSING,
                    run=run,
                    requested_state=requested_state,
                    rule_id=requested_rule_id,
                    edge_type=requested_rule.edge_type,
                )
            if not _valid_expected_evidence(request.expected_evidence):
                return _result(
                    TransitionEvaluationDecision.INVALID,
                    TransitionEvaluationReasonCode.EXPECTED_EVIDENCE_ANCHOR_MALFORMED,
                    run=run,
                    requested_state=requested_state,
                    rule_id=requested_rule_id,
                    edge_type=requested_rule.edge_type,
                )
            if request.repository_binding is None:
                return _result(
                    TransitionEvaluationDecision.GATED,
                    TransitionEvaluationReasonCode.REPOSITORY_BINDING_REQUIRED,
                    run=run,
                    requested_state=requested_state,
                    rule_id=requested_rule_id,
                    edge_type=requested_rule.edge_type,
                    missing=(TransitionRequirement.REPOSITORY,),
                )
            if not _valid_repository_binding(request.repository_binding):
                return _result(
                    TransitionEvaluationDecision.INVALID,
                    TransitionEvaluationReasonCode.REPOSITORY_BINDING_MALFORMED,
                    run=run,
                    requested_state=requested_state,
                    rule_id=requested_rule_id,
                    edge_type=requested_rule.edge_type,
                )
            if not request.evidence_snapshots:
                return _result(
                    TransitionEvaluationDecision.GATED,
                    TransitionEvaluationReasonCode.EVIDENCE_REQUIRED,
                    run=run,
                    requested_state=requested_state,
                    rule_id=requested_rule_id,
                    edge_type=requested_rule.edge_type,
                    missing=(TransitionRequirement.EVIDENCE,),
                )
            if len(request.evidence_snapshots) != 1 or any(
                not _valid_evidence_snapshot(snapshot)
                for snapshot in request.evidence_snapshots
            ):
                return _result(
                    TransitionEvaluationDecision.INVALID,
                    TransitionEvaluationReasonCode.EVIDENCE_SNAPSHOT_MALFORMED,
                    run=run,
                    requested_state=requested_state,
                    rule_id=requested_rule_id,
                    edge_type=requested_rule.edge_type,
                )
            evidence = request.evidence_snapshots[0]
            favorable = evidence.verdict in {
                EvidenceVerdict.FEASIBLE,
                EvidenceVerdict.FEASIBLE_WITH_NON_BLOCKING_FINDINGS,
            }
            if favorable is not evidence.ready_for_approval_1:
                return _result(
                    TransitionEvaluationDecision.INVALID,
                    TransitionEvaluationReasonCode.EVIDENCE_VERDICT_READINESS_INCONSISTENT,
                    run=run,
                    requested_state=requested_state,
                    rule_id=requested_rule_id,
                    edge_type=requested_rule.edge_type,
                )
            if evidence.evidence_kind is not requested_rule.evidence_kind_requirement:
                return _result(
                    TransitionEvaluationDecision.INVALID,
                    TransitionEvaluationReasonCode.EVIDENCE_KIND_MISMATCH,
                    run=run,
                    requested_state=requested_state,
                    rule_id=requested_rule_id,
                    edge_type=requested_rule.edge_type,
                )
            expected = request.expected_evidence
            repository = request.repository_binding
            if (
                evidence.artifact_path != expected.artifact_path
                or evidence.artifact_byte_count != expected.artifact_byte_count
            ):
                return _result(
                    TransitionEvaluationDecision.INVALID,
                    TransitionEvaluationReasonCode.EVIDENCE_ARTIFACT_IDENTITY_MISMATCH,
                    run=run,
                    requested_state=requested_state,
                    rule_id=requested_rule_id,
                    edge_type=requested_rule.edge_type,
                )
            if evidence.artifact_sha256 != expected.artifact_sha256:
                return _result(
                    TransitionEvaluationDecision.INVALID,
                    TransitionEvaluationReasonCode.EVIDENCE_ARTIFACT_DIGEST_MISMATCH,
                    run=run,
                    requested_state=requested_state,
                    rule_id=requested_rule_id,
                    edge_type=requested_rule.edge_type,
                )
            if evidence.assessment_authority_id != expected.assessment_authority_id:
                return _result(
                    TransitionEvaluationDecision.INVALID,
                    TransitionEvaluationReasonCode.EVIDENCE_AUTHORITY_MISMATCH,
                    run=run,
                    requested_state=requested_state,
                    rule_id=requested_rule_id,
                    edge_type=requested_rule.edge_type,
                )
            if evidence.run_id != run.run_id:
                return _result(
                    TransitionEvaluationDecision.INVALID,
                    TransitionEvaluationReasonCode.EVIDENCE_RUN_MISMATCH,
                    run=run,
                    requested_state=requested_state,
                    rule_id=requested_rule_id,
                    edge_type=requested_rule.edge_type,
                )
            if (
                evidence.milestone_id != request.milestone_contract.milestone_id
                or evidence.milestone_contract_digest != contract_digest
            ):
                return _result(
                    TransitionEvaluationDecision.INVALID,
                    TransitionEvaluationReasonCode.EVIDENCE_CONTRACT_BINDING_MISMATCH,
                    run=run,
                    requested_state=requested_state,
                    rule_id=requested_rule_id,
                    edge_type=requested_rule.edge_type,
                )
            if evidence.rule_id is not requested_rule_id:
                return _result(
                    TransitionEvaluationDecision.INVALID,
                    TransitionEvaluationReasonCode.EVIDENCE_RULE_MISMATCH,
                    run=run,
                    requested_state=requested_state,
                    rule_id=requested_rule_id,
                    edge_type=requested_rule.edge_type,
                )
            if evidence.source_state is not run.current_state:
                return _result(
                    TransitionEvaluationDecision.GATED,
                    TransitionEvaluationReasonCode.EVIDENCE_STALE,
                    run=run,
                    requested_state=requested_state,
                    rule_id=requested_rule_id,
                    edge_type=requested_rule.edge_type,
                )
            if evidence.state_version != run.state_version:
                return _result(
                    TransitionEvaluationDecision.GATED,
                    TransitionEvaluationReasonCode.EVIDENCE_STALE,
                    run=run,
                    requested_state=requested_state,
                    rule_id=requested_rule_id,
                    edge_type=requested_rule.edge_type,
                )
            if evidence.repository_binding.repository_id != repository.repository_id:
                return _result(
                    TransitionEvaluationDecision.INVALID,
                    TransitionEvaluationReasonCode.EVIDENCE_REPOSITORY_IDENTITY_MISMATCH,
                    run=run,
                    requested_state=requested_state,
                    rule_id=requested_rule_id,
                    edge_type=requested_rule.edge_type,
                )
            if evidence.repository_binding.branch != repository.branch:
                return _result(
                    TransitionEvaluationDecision.GATED,
                    TransitionEvaluationReasonCode.EVIDENCE_STALE,
                    run=run,
                    requested_state=requested_state,
                    rule_id=requested_rule_id,
                    edge_type=requested_rule.edge_type,
                )
            if evidence.repository_binding.head_commit != repository.head_commit:
                return _result(
                    TransitionEvaluationDecision.GATED,
                    TransitionEvaluationReasonCode.EVIDENCE_STALE,
                    run=run,
                    requested_state=requested_state,
                    rule_id=requested_rule_id,
                    edge_type=requested_rule.edge_type,
                )
            if evidence.repository_binding.worktree_digest != repository.worktree_digest:
                return _result(
                    TransitionEvaluationDecision.GATED,
                    TransitionEvaluationReasonCode.EVIDENCE_STALE,
                    run=run,
                    requested_state=requested_state,
                    rule_id=requested_rule_id,
                    edge_type=requested_rule.edge_type,
                )
            if evidence.producer_kind is not requested_rule.evidence_producer_kind_requirement:
                return _result(
                    TransitionEvaluationDecision.INVALID,
                    TransitionEvaluationReasonCode.EVIDENCE_PRODUCER_MISMATCH,
                    run=run,
                    requested_state=requested_state,
                    rule_id=requested_rule_id,
                    edge_type=requested_rule.edge_type,
                )
            if not favorable:
                return _result(
                    TransitionEvaluationDecision.GATED,
                    TransitionEvaluationReasonCode.EVIDENCE_NOT_SATISFIED,
                    run=run,
                    requested_state=requested_state,
                    rule_id=requested_rule_id,
                    edge_type=requested_rule.edge_type,
                )

        # Stage 10: all declared prerequisites passed.
        return _result(
            TransitionEvaluationDecision.ALLOWED,
            TransitionEvaluationReasonCode.RULE_ALLOWED,
            run=run,
            requested_state=requested_state,
            rule_id=requested_rule_id,
            edge_type=requested_rule.edge_type,
        )
