"""Pure deterministic policy for the bounded DL-2.3 Phase graph."""

import re
from types import MappingProxyType
from typing import Mapping

from .models import (
    PhaseContract,
    PhaseState,
    PhaseStateRecord,
    PhaseTransitionEvaluationReasonCode,
    PhaseTransitionEvaluationRequest,
    PhaseTransitionEvaluationResult,
    PhaseTransitionRule,
    PhaseTransitionRuleId,
    TransitionEvaluationDecision,
    WorkflowEdgeType,
    _PHASE_CANONICAL_PAIRS,
    _PHASE_RULE_ID_PATTERN,
    _phase_contract_is_exact,
)


_VERSION_PATTERN = re.compile(r"\A[1-9][0-9]*\Z", re.ASCII)


def _exact_state_record(value: object) -> bool:
    if type(value) is not PhaseStateRecord:
        return False
    try:
        return PhaseStateRecord(
            project_id=value.project_id,
            phase_id=value.phase_id,
            phase_contract_digest=value.phase_contract_digest,
            current_state=value.current_state,
            state_version=value.state_version,
            created_at=value.created_at,
            updated_at=value.updated_at,
        ) == value
    except (AttributeError, TypeError, ValueError):
        return False


def _result(
    decision: TransitionEvaluationDecision,
    reason_code: PhaseTransitionEvaluationReasonCode,
    *,
    state: PhaseStateRecord | None = None,
    requested_state: PhaseState | None = None,
    rule: PhaseTransitionRule | None = None,
) -> PhaseTransitionEvaluationResult:
    return PhaseTransitionEvaluationResult(
        decision=decision,
        reason_code=reason_code,
        project_id=None if state is None else state.project_id,
        phase_id=None if state is None else state.phase_id,
        phase_contract_digest=None if state is None else state.phase_contract_digest,
        current_state=None if state is None else state.current_state,
        current_state_version=None if state is None else state.state_version,
        requested_state=requested_state,
        rule_id=None if rule is None else rule.rule_id,
        edge_type=None if rule is None else rule.edge_type,
    )


_RULES = tuple(
    PhaseTransitionRule(
        rule_id=rule_id,
        source_state=source_state,
        target_state=target_state,
        edge_type=(
            WorkflowEdgeType.UNCONDITIONAL
            if index == 0
            else None
        ),
        executable=index == 0,
    )
    for index, (rule_id, (source_state, target_state)) in enumerate(
        zip(PhaseTransitionRuleId, _PHASE_CANONICAL_PAIRS)
    )
)
_RULES_BY_ID = MappingProxyType({rule.rule_id: rule for rule in _RULES})
_RULES_BY_PAIR = MappingProxyType(
    {(rule.source_state, rule.target_state): rule for rule in _RULES}
)
if len(_RULES) != 8 or len(_RULES_BY_ID) != 8 or len(_RULES_BY_PAIR) != 8:
    raise RuntimeError("invalid DL-2.3 Phase transition registry")


class PhaseTransitionPolicy:
    """Evaluates Phase requests without persistence or external access."""

    _RULES_BY_ID = _RULES_BY_ID
    _RULES_BY_PAIR = _RULES_BY_PAIR

    @property
    def rules_by_id(self) -> Mapping[PhaseTransitionRuleId, PhaseTransitionRule]:
        return self._RULES_BY_ID

    @property
    def rules_by_pair(
        self,
    ) -> Mapping[tuple[PhaseState, PhaseState], PhaseTransitionRule]:
        return self._RULES_BY_PAIR

    def evaluate(self, candidate: object) -> PhaseTransitionEvaluationResult:
        if type(candidate) is not PhaseTransitionEvaluationRequest:
            return _result(
                TransitionEvaluationDecision.INVALID,
                PhaseTransitionEvaluationReasonCode.REQUEST_NOT_EVALUATION_REQUEST,
            )
        request = candidate
        if request.evaluation_version is None:
            return _result(
                TransitionEvaluationDecision.INVALID,
                PhaseTransitionEvaluationReasonCode.REQUEST_REQUIRED_FIELD_MISSING,
            )
        if type(request.evaluation_version) is not str:
            return _result(
                TransitionEvaluationDecision.INVALID,
                PhaseTransitionEvaluationReasonCode.REQUEST_WRONG_TYPE,
            )
        if _VERSION_PATTERN.fullmatch(request.evaluation_version) is None:
            return _result(
                TransitionEvaluationDecision.INVALID,
                PhaseTransitionEvaluationReasonCode.EVALUATION_VERSION_MALFORMED,
            )

        if request.phase_state is None:
            return _result(
                TransitionEvaluationDecision.INVALID,
                PhaseTransitionEvaluationReasonCode.REQUEST_REQUIRED_FIELD_MISSING,
            )
        if type(request.phase_state) is not PhaseStateRecord:
            return _result(
                TransitionEvaluationDecision.INVALID,
                PhaseTransitionEvaluationReasonCode.REQUEST_WRONG_TYPE,
            )
        if type(request.phase_state.current_state) is not PhaseState:
            return _result(
                TransitionEvaluationDecision.INVALID,
                PhaseTransitionEvaluationReasonCode.REQUEST_UNKNOWN_SOURCE_STATE,
            )
        if not _exact_state_record(request.phase_state):
            return _result(
                TransitionEvaluationDecision.INVALID,
                PhaseTransitionEvaluationReasonCode.STATE_RECORD_MALFORMED,
            )
        state = request.phase_state

        if request.rule_id is None or request.requested_state is None:
            return _result(
                TransitionEvaluationDecision.INVALID,
                PhaseTransitionEvaluationReasonCode.REQUEST_REQUIRED_FIELD_MISSING,
                state=state,
            )
        if (
            type(request.rule_id) is not str
            or not 4 <= len(request.rule_id) <= 128
            or _PHASE_RULE_ID_PATTERN.fullmatch(request.rule_id) is None
        ):
            return _result(
                TransitionEvaluationDecision.INVALID,
                PhaseTransitionEvaluationReasonCode.REQUEST_RULE_ID_MALFORMED,
                state=state,
            )
        if type(request.requested_state) is not PhaseState:
            return _result(
                TransitionEvaluationDecision.INVALID,
                PhaseTransitionEvaluationReasonCode.REQUEST_UNKNOWN_TARGET_STATE,
                state=state,
            )
        requested_state = request.requested_state
        if request.edge_type is not None and type(request.edge_type) is not WorkflowEdgeType:
            return _result(
                TransitionEvaluationDecision.INVALID,
                PhaseTransitionEvaluationReasonCode.REQUEST_WRONG_TYPE,
                state=state,
                requested_state=requested_state,
            )
        if type(request.evidence_snapshots) is not tuple:
            return _result(
                TransitionEvaluationDecision.INVALID,
                PhaseTransitionEvaluationReasonCode.REQUEST_WRONG_TYPE,
                state=state,
                requested_state=requested_state,
            )
        if (
            request.approval_snapshot is not None
            or bool(request.evidence_snapshots)
            or request.repository_binding is not None
        ):
            return _result(
                TransitionEvaluationDecision.INVALID,
                PhaseTransitionEvaluationReasonCode.REQUEST_SURPLUS_PREREQUISITE,
                state=state,
                requested_state=requested_state,
            )

        if request.evaluation_version != "1":
            return _result(
                TransitionEvaluationDecision.UNSUPPORTED,
                PhaseTransitionEvaluationReasonCode.EVALUATION_VERSION_UNSUPPORTED,
                state=state,
                requested_state=requested_state,
            )

        matched_rule = self._RULES_BY_PAIR.get((state.current_state, requested_state))
        if matched_rule is None:
            return _result(
                TransitionEvaluationDecision.UNSUPPORTED,
                PhaseTransitionEvaluationReasonCode.REGISTRY_NO_REGISTERED_TRANSITION,
                state=state,
                requested_state=requested_state,
            )

        requested_rule_id = next(
            (rule_id for rule_id in PhaseTransitionRuleId if rule_id.value == request.rule_id),
            None,
        )
        if requested_rule_id is None:
            return _result(
                TransitionEvaluationDecision.UNSUPPORTED,
                PhaseTransitionEvaluationReasonCode.REGISTRY_RULE_ID_UNSUPPORTED,
                state=state,
                requested_state=requested_state,
            )
        requested_rule = self._RULES_BY_ID[requested_rule_id]
        if requested_rule.source_state is not state.current_state:
            return _result(
                TransitionEvaluationDecision.INVALID,
                PhaseTransitionEvaluationReasonCode.REGISTRY_RULE_SOURCE_MISMATCH,
                state=state,
                requested_state=requested_state,
                rule=requested_rule,
            )
        if requested_rule.target_state is not requested_state:
            return _result(
                TransitionEvaluationDecision.INVALID,
                PhaseTransitionEvaluationReasonCode.REGISTRY_RULE_TARGET_MISMATCH,
                state=state,
                requested_state=requested_state,
                rule=requested_rule,
            )
        if requested_rule is not matched_rule:
            raise RuntimeError("Phase registry identity invariant lost")

        if not matched_rule.executable:
            return _result(
                TransitionEvaluationDecision.UNSUPPORTED,
                PhaseTransitionEvaluationReasonCode.CANONICAL_RULE_NOT_EXECUTABLE,
                state=state,
                requested_state=requested_state,
                rule=matched_rule,
            )

        if request.edge_type is not matched_rule.edge_type:
            return _result(
                TransitionEvaluationDecision.INVALID,
                PhaseTransitionEvaluationReasonCode.REGISTRY_EDGE_TYPE_MISMATCH,
                state=state,
                requested_state=requested_state,
                rule=matched_rule,
            )
        if request.phase_contract is None:
            return _result(
                TransitionEvaluationDecision.GATED,
                PhaseTransitionEvaluationReasonCode.CONTRACT_REQUIRED,
                state=state,
                requested_state=requested_state,
                rule=matched_rule,
            )
        if not _phase_contract_is_exact(request.phase_contract):
            return _result(
                TransitionEvaluationDecision.INVALID,
                PhaseTransitionEvaluationReasonCode.CONTRACT_MALFORMED,
                state=state,
                requested_state=requested_state,
                rule=matched_rule,
            )
        contract = request.phase_contract
        if (
            contract.project_id != state.project_id
            or contract.phase_id != state.phase_id
        ):
            return _result(
                TransitionEvaluationDecision.INVALID,
                PhaseTransitionEvaluationReasonCode.CONTRACT_IDENTITY_MISMATCH,
                state=state,
                requested_state=requested_state,
                rule=matched_rule,
            )
        if contract.sha256_digest() != state.phase_contract_digest:
            return _result(
                TransitionEvaluationDecision.INVALID,
                PhaseTransitionEvaluationReasonCode.CONTRACT_DIGEST_MISMATCH,
                state=state,
                requested_state=requested_state,
                rule=matched_rule,
            )
        return _result(
            TransitionEvaluationDecision.ALLOWED,
            PhaseTransitionEvaluationReasonCode.RULE_ALLOWED,
            state=state,
            requested_state=requested_state,
            rule=matched_rule,
        )
