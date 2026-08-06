"""Domain values for the DL-P1.1 durable-state proof of concept."""

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
