# API and Agent Contracts

## Document purpose

Define transport-independent request/response contract requirements for all Development Loop agents and adapters.

## In-scope responsibilities

Every request and response is versioned, schema validated, associated with a Development Run, associated with input digests, and stored as both raw and validated artifacts. Contract processing cannot directly mutate workflow state.

Target contracts:

- Phase Contract and Milestone Contract;
- Planner request/response;
- Feasibility request/response;
- Codex execution request/response;
- Verification request/response;
- Reviewer request/response;
- FocusedFixContract;
- Handoff Agent request/response and Handoff Finalizer request/response;
- Vault Curator request/response;
- Vault Writer request/response;
- Phase Merge Package;
- structured error contract.

### Role-specific requirements

Planner proposes objective, scope, exclusions, acceptance criteria, allowed/forbidden paths, verification plan, and stop conditions. Reviewer receives the approved Milestone Contract, actual Git diff, changed paths, deterministic evidence, Codex result, and correction history, then returns exactly APPROVED, NEEDS_FIX, BLOCKED, or NEEDS_HUMAN_DECISION.

Verifier evidence is generated independently of Codex and checks branch, base commit, tree state, path policy, `git diff --check`, approved tests, approved lint/type checks, repository policy, and changed-path evidence.

The Handoff Agent contract creates the draft after Verifier PASSED, Reviewer APPROVED, and policy success, with implementation commit Pending. The Handoff Finalizer contract runs only after implementation commit 1 exists; it inserts that hash, finalizes evidence and next expected step, and prepares the handoff-only change for commit 2.

## Approved decisions

Reviewer output is advisory. Panam's State Machine evaluates it alongside policy, approval validity, deterministic evidence, and actual Git reality. A focused-fix contract preserves the original Milestone Contract and cannot expand scope.

## Explicit boundaries and out of scope

Contracts are not direct database commands and not a permission bypass. They cannot select arbitrary paths, commands, repositories, Vault notes, or state transitions.

## Cross-references

- [Actors and responsibilities](01-actors-and-responsibilities.md)
- [Core data model](04-core-data-model.md)
- [Safety, Git, and approval policy](05-safety-git-approval-policy.md)

## Future considerations

Exact schema language and artifact encoding are deferred to DL-P1 and later adapter milestones.
