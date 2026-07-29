# Failure and Recovery

## Document purpose

Define reconciliation-first recovery for interrupted Development Runs and phase operations.

## In-scope responsibilities

Recovery compares SQLite state, operation journal, process state, filesystem artifacts, local Git state, remote Git state, and Vault state when applicable. It never blindly retries an ambiguous action.

| Result | Meaning |
|---|---|
| RESUME_SAFE | Evidence confirms the next queued action remains safe. |
| ACTION_ALREADY_COMPLETED | Reality proves the interrupted operation completed; persist reconciliation evidence. |
| RETRY_SAFE | The prior action did not complete and can safely be retried. |
| HUMAN_DECISION_REQUIRED | Evidence is ambiguous or policy/approval no longer permits continuation. |
| UNRECOVERABLE | Safe automatic continuation is impossible. |

The command queue uses PENDING, CLAIMED, RUNNING, SUCCEEDED, FAILED, and CANCELLED states with leases and idempotency keys. No SQLite transaction remains open during an external operation.

## Approved decisions

Panam must not repeat an ambiguous commit or push before checking local and remote reality. It must never delete unknown local changes during recovery. CyberDeck exposes health, locks, journal, logs, diagnostics, process control, and emergency stop; Panam Web App owns human recovery decisions.

## Explicit boundaries and out of scope

Recovery does not use destructive Git cleanup, automatic stash, branch deletion, or arbitrary filesystem deletion. It does not resume an invalidated approval without a new human decision.

## Cross-references

- [State machine](03-state-machine.md)
- [Safety, Git, and approval policy](05-safety-git-approval-policy.md)
- [Human interaction model](09-human-interaction-model.md)

## Future considerations

Concrete lease duration, process supervisor, and artifact-retention settings remain implementation decisions constrained by this recovery model.
