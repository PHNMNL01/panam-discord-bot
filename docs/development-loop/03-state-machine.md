# State Machine

## Document purpose

Define the sole authority for phase and Development Run transitions.

## In-scope responsibilities

Only the State Machine approves transitions. UI routes, agents, adapters, and Git operations submit evidence or commands; none directly assign workflow state.

### Phase lifecycle

```text
DRAFT → AWAITING_START_APPROVAL → CREATING_BRANCH → ACTIVE
→ PREPARING_MERGE → READY_FOR_PR → PR_CREATED → MERGED → CLOSED
```

BLOCKED and CANCELLED are additional phase states. One phase has one long-lived phase branch, and milestones are sequential.

### Development Run successful path

```text
DRAFT → FEASIBILITY_CHECKING → AWAITING_EXECUTION_APPROVAL
→ APPROVED_FOR_IMPLEMENTATION → PREPARING_RUN → IMPLEMENTING → VERIFYING
→ REVIEWING → PREPARING_HANDOFF → VERIFYING_HANDOFF_DRAFT → COMMITTING_IMPLEMENTATION
→ FINALIZING_HANDOFF → VERIFYING_FINAL_HANDOFF → COMMITTING_HANDOFF → PUSHING_SOURCE → SOURCE_COMPLETED
→ PREPARING_VAULT_PROPOSAL → AWAITING_VAULT_APPROVAL → WRITING_TO_VAULT
→ VERIFYING_VAULT → COMMITTING_VAULT → PUSHING_VAULT → COMPLETED
```

Additional Development Run states: NEEDS_FIX, AWAITING_HUMAN_DECISION, RECONCILING, BLOCKED, FAILED, CANCELLED, and CLOSED_WITHOUT_VAULT.

### Correction rule

The initial implementation is followed by at most three automatic focused-fix attempts. Each uses the unchanged Milestone Contract. A fourth automatic attempt is forbidden; the State Machine moves the run to BLOCKED.

## Approved decisions

State changes and state-event records are transactionally persisted in SQLite. State transition evaluation considers deterministic policy, evidence, approval validity, and actual Git/Vault reality.

## Explicit boundaries and out of scope

The state machine does not replace adapters, execute commands itself, or trust an AI agent's self-reported state. It does not authorize a pull-request merge.

## Cross-references

- [Core data model](04-core-data-model.md)
- [Failure and recovery](06-failure-and-recovery.md)
- [Human interaction model](09-human-interaction-model.md)

## Future considerations

Transition guards and exact event payload schemas are implemented after architecture freeze, not in DL-0.1.

## DL-0.5A deterministic handoff-gate guards

`VERIFYING_HANDOFF_DRAFT` binds baseline HEAD, approved diff and paths, handoff
digest, Reviewer/evidence results, correction count, status, lifecycle fields,
and absence of premature claims. `VERIFYING_FINAL_HANDOFF` binds implementation
commit/tree/message/scope, finalized handoff digest, handoff-only diff, status,
and no invented finalization hash. Relevant change invalidates evidence; only
the State Machine may evaluate the gate.

### Handoff-verification result transitions

`VERIFYING_HANDOFF_DRAFT` has these result transitions:

- fresh `PASSED` → `COMMITTING_IMPLEMENTATION`;
- correctable `FAILED` within the remaining approved correction budget →
  `PREPARING_HANDOFF`;
- `BLOCKED` → `BLOCKED`, and `NEEDS_HUMAN_DECISION` →
  `AWAITING_HUMAN_DECISION`.

`VERIFYING_FINAL_HANDOFF` has these result transitions:

- fresh `PASSED` → `COMMITTING_HANDOFF`;
- correctable `FAILED` within the remaining approved correction budget →
  `FINALIZING_HANDOFF`;
- `BLOCKED` → `BLOCKED`, and `NEEDS_HUMAN_DECISION` →
  `AWAITING_HUMAN_DECISION`.

`PUSHING_SOURCE` may reach `SOURCE_COMPLETED` only after fresh source
synchronization evidence confirms that local `HEAD` equals the approved remote
branch `HEAD`, divergence is `0 0`, the worktree and index are clean, both
expected commits exist, and no unexpected path or commit exists.
