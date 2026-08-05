# End-to-End Workflow

## Document purpose

Describe the approved phase and milestone flow without assigning direct side effects to Web routes or agents.

## In-scope responsibilities

```mermaid
flowchart LR
  P[Planner] --> F[Feasibility Assessor]
  F --> A1[Approval 1]
  A1 --> C[Codex]
  C --> V[Verifier]
  V -->|PASSED| R[Reviewer]
  V -->|FAILED, max 3 fixes| C
  R -->|NEEDS_FIX, max 3 fixes| C
  R -->|BLOCKED or NEEDS_HUMAN_DECISION| HD[Human-decision stop]
  R -->|APPROVED| H[Handoff Agent]
  H --> DV[Verifier draft-handoff verification]
  DV -->|PASSED| IS[Human explicit implementation staging]
  DV -->|correctable FAILED within approved budget| H
  DV -->|BLOCKED or NEEDS_HUMAN_DECISION| HD
  IS --> I[Human implementation commit 1]
  I --> HF[Handoff Finalizer]
  HF --> FV[Verifier final-handoff verification]
  FV -->|PASSED| HS[Human explicit handoff-only staging]
  FV -->|correctable FAILED within approved budget| HF
  FV -->|BLOCKED or NEEDS_HUMAN_DECISION| HD
  HS --> HC[Human handoff-finalization commit 2]
  HC --> SP[Human controlled source push]
  SP --> SV[Source synchronization and reconciliation verification]
  SV -->|PASSED| S[SOURCE_COMPLETED]
  SV -->|FAILED, BLOCKED, or NEEDS_HUMAN_DECISION| HD
  S --> K[Knowledge Curator proposal artifact]
  K --> A2[Approval 2]
  A2 --> W[Vault Writer, verify, commit, push]
  W --> X[COMPLETED]
```

### Phase workflow

1. A Phase Contract is drafted.
2. Phase Start Approval authorizes creation and initial push of the approved phase branch.
3. Panam creates the phase branch and marks the phase ACTIVE.
4. Approved Milestone Contracts execute sequentially on that branch.
5. At phase end, Panam performs final regression verification and prepares a Phase Merge Package.
6. Panam prepares the Phase Merge Package automatically. After Matej explicitly confirms Pull Request creation, Panam may technically create the Pull Request. Panam never approves or merges it; merge remains human-controlled.

### Development Run workflow

1. Planner and Feasibility Assessor produce evidence for the Milestone Contract.
2. Approval 1 authorizes implementation, verification, up to three focused fixes, the two source commits, and their push.
3. Codex implements; Verifier gathers deterministic evidence; Reviewer returns a decision.
4. A NEEDS_FIX decision permits a focused fix that preserves the original Milestone Contract. After three failed focused fixes, the run is BLOCKED.
5. After Verifier `PASSED`, Reviewer `APPROVED`, and policy success, the Handoff Agent prepares the draft handoff with the implementation-commit field Pending.
6. Verifier performs fresh deterministic draft-handoff verification. Only `PASSED` permits explicit staging of the exact approved implementation scope and draft, followed by human implementation commit 1. A correctable `FAILED` result returns to Handoff Agent within the remaining approved correction budget; `BLOCKED` or `NEEDS_HUMAN_DECISION` stops for a human decision.
7. Handoff Finalizer then modifies only the handoff with commit-1 facts and final evidence. Verifier performs fresh deterministic final-handoff verification. Only `PASSED` permits explicit handoff-only staging and human handoff-finalization commit 2; correctable `FAILED` returns to Handoff Finalizer within the remaining approved correction budget, while `BLOCKED` or `NEEDS_HUMAN_DECISION` stops for a human decision.
8. A human performs a controlled source push only to the approved phase branch. Fresh source synchronization and reconciliation verification must pass before the run reaches `SOURCE_COMPLETED`.
9. After `SOURCE_COMPLETED`, Knowledge Curator creates a runtime-artifact proposal, not a source-repository proposal file.
10. Approval 2 authorizes only that exact proposal and named Vault paths. Vault changes are verified, committed, and pushed before `COMPLETED`.

## Approved decisions

SOURCE_COMPLETED is a durable source-side checkpoint: source code, handoff, both source commits, and source push are complete. COMPLETED additionally requires approved and pushed Vault changes. CLOSED_WITHOUT_VAULT is a terminal milestone outcome requiring Matej's explicit decision after source completion.

## Explicit boundaries and out of scope

There is no automatic PR approval, merge, branch deletion, or next-milestone start. Long-running operations never execute inside Flask request handlers.

## Cross-references

- [State machine](03-state-machine.md)
- [Handoff and Git policy](05-safety-git-approval-policy.md)
- [Human interaction model](09-human-interaction-model.md)

## Future considerations

The Phase Merge Package is a v1 output; branch integration remains human-controlled.

## DL-0.5A commit gates

The draft handoff and exact approved implementation scope remain uncommitted
until independent review is `APPROVED` and draft verification is fresh `PASSED`.
The human then creates implementation commit 1. Handoff Finalizer modifies only
the handoff; fresh final verification is required before the human creates the
handoff-only finalization commit. Both commits may be pushed only by a human to
the approved source phase branch.
