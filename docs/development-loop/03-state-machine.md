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
→ REVIEWING → PREPARING_HANDOFF → COMMITTING_IMPLEMENTATION
→ FINALIZING_HANDOFF → COMMITTING_HANDOFF → PUSHING_SOURCE → SOURCE_COMPLETED
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
