"""The explicit, bounded transition allowlist for DL-P1.1."""

from .models import DevelopmentRunState


class TransitionPolicy:
    """Evaluates only the two approved DL-P1.1 state edges."""

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

    def allows(
        self,
        current_state: DevelopmentRunState,
        requested_state: DevelopmentRunState,
    ) -> bool:
        return (current_state, requested_state) in self._ALLOWED
