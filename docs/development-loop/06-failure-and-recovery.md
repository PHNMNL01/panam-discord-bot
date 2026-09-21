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

## DL-0.5A handoff-evidence recovery

Relevant handoff, diff, Git, review, authority, policy, implementation commit
or branch changes invalidate affected readiness and require fresh dependent
verification. Retain historical result bytes, including failed generations;
'invalidate' does not mean deleting or rewriting evidence. Draft readiness
cannot authorize a post-commit effect, and changed final bytes require new
candidate verification.

For an activated v0.2 manual pilot, classify subject, contract, report and
baseline defects separately. A wrong binding does not authorize rewriting a
correct subject. Required missing evidence blocks use; a genuinely optional
diagnostic is recorded without inventing a subject defect. No required check
may be downgraded by an output record.

Preserve consumed budgets. Reconcile ambiguous external effects before any
retry. Failed byte capture retains its reservation and quarantine bytes.
A separately authorized new capture may retain already completed output;
it must not repeat a producer, commit, push or Vault effect. Summary repair
renders a new view from validated stored evidence and never overwrites history.

Current workflow acceptance is external to the immutable as-of handoff.
Advancing workflow alone does not require a handoff edit or another commit.
No automatic recovery, new state or effect authority is implemented here.
