# Actors and Responsibilities

## Document purpose

Assign canonical ownership to human, interface, worker, and agent roles.

## In-scope responsibilities

| Actor | Responsibilities | Boundary |
|---|---|---|
| Human | Approves Phase Start Approval, Approval 1, Approval 2, recovery decisions, PR creation, and merge. | Retains final approval and merge authority. |
| Planner | Proposes a Milestone Contract: objective, scope, exclusions, criteria, paths, verification, and stop conditions. | Cannot approve its own proposal. |
| Feasibility Assessor | Evaluates a proposed or draft Milestone Contract before Approval 1. | Does not execute or approve. |
| Codex | Implements approved scope, approved tests, and approved verification commands; returns a structured report. | Cannot perform Git writes or cross-repository writes. |
| Verifier | Deterministically checks Git reality, paths, diff, tests, and repository policy. | AI summaries are not verification evidence. |
| Reviewer | Separately assesses contract, diff, verification evidence, Codex result, and correction history. | Not a safety authority; returns an advisory decision. |
| Handoff Agent | After Verifier PASSED, Reviewer APPROVED, and policy success, creates the handoff draft before implementation commit 1 and records the implementation-commit field as Pending. | Does not finalize the handoff or create commit 2. |
| Handoff Finalizer | After implementation commit 1 exists, inserts its real hash, finalizes evidence and next expected step, and prepares the handoff-only change for source commit 2. | Does not create a third source commit. |
| Knowledge Curator | Prepares a Vault proposal as a runtime artifact. | Never writes `Vault_work`. |
| Vault Writer | Changes only Approval-2-approved Vault paths. | Requires exact proposal approval. |
| Panam Web App | Owns human workspaces, approval surfaces, evidence display, recovery decisions, and Phase Merge Packages. | Creates commands; does not run side effects. |
| CyberDeck | Owns worker health, queue/process/lock visibility, journal, logs, diagnostics, and emergency stop. | Does not become a canonical approval surface. |
| Development Worker | Claims durable commands and calls adapters for planning, execution, verification, Git, handoff, curation, Vault work, and reconciliation. | Never keeps a SQLite transaction open during an external operation. |
| Discord | Sends notifications and brief status. | Not canonical for approval or detailed workflow editing. |

## Approved decisions

Planner and Reviewer are separate OpenAI-backed roles with separate instructions and contracts. Panam makes final workflow decisions from deterministic policy, Verifier evidence, Reviewer decision, approval validity, and current Git reality.

## Explicit boundaries and out of scope

No role may directly assign workflow state; only the [State Machine](03-state-machine.md) may approve transitions. No AI role is a security authority.

## Cross-references

- [Workflow](02-end-to-end-workflow.md)
- [API and agent contracts](08-api-agent-contracts.md)
- [Human interaction model](09-human-interaction-model.md)

## Future considerations

A manual ChatGPT adapter is a fallback; API-backed Planner and Reviewer automation are the primary target.

## DL-0.5A handoff verification

Verifier gains two deterministic modes, not a new role. Draft-handoff mode runs
after Handoff Agent and before implementation commit 1; final-handoff mode runs
after Handoff Finalizer and before the handoff-finalization commit. Handoff
roles prepare only their bounded artifact, Reviewer remains advisory, and the
State Machine alone authorizes transitions.
