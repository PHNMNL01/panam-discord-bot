# ADR-004: Evidence-driven Completion and Freshness

## Status

Accepted for DL-0.2; Architecture Freeze pending.

## Context

An agent can claim that work is correct while the branch, diff, test result, or approved contract has changed. Completion decisions must therefore depend on independently gathered evidence that identifies exactly what repository state was inspected.

## Decision

Completion and checkpoint transitions are evidence-driven. Agent output is a claim, not verification evidence. Deterministic verification evidence must be fresh and bound to the exact inspected repository state.

At minimum, a verification record binds:

- repository identity;
- branch;
- HEAD;
- working-tree or diff digest;
- verification command;
- exit code;
- timestamp;
- evidence digest;
- Milestone Contract digest.

The evidence record also identifies the run and verification attempt. A relevant change to repository identity, branch, HEAD, working tree or diff, verification command or policy, Milestone Contract, approval binding, or referenced verification artifact invalidates prior evidence for the affected decision. Invalidated or unverifiable evidence cannot authorize completion and must be regenerated or sent for human decision.

SOURCE_COMPLETED, COMPLETED, and other evidence-dependent transitions retain the distinct meanings established in DL-0.1.

## Rationale

Exact-state binding prevents stale test results, summaries, or reviews from being reused after material changes. Independent deterministic evidence makes completion reproducible and auditable.

## Consequences

- Verification must capture both command results and the Git state they inspected.
- Cached evidence requires a positive freshness check before reuse.
- Reviewer judgment must reference the same approved contract, diff, and deterministic evidence set.
- Any correction that changes relevant Git state requires affected verification to run again.

## Rejected alternatives

- **Trust the implementer's summary**: rejected because it is a claim from the producing specialist.
- **Bind evidence only to HEAD**: rejected because a dirty working tree may differ while HEAD remains unchanged.
- **Reuse the latest passing result by timestamp alone**: rejected because recency does not prove state equivalence.

## V1 boundary

V1 requires exact Git-state binding and explicit invalidation for Development Loop verification. Detailed storage schema, retention, and hashing format are later implementation decisions.

## Future extension point

Additional evidence types, reproducible environments, attestations, or signed provenance may strengthen the same binding without replacing deterministic inspection.

## Related architecture documents

- [API and agent contracts](../08-api-agent-contracts.md)
- [Core data model](../04-core-data-model.md)
- [Safety, Git, and approval policy](../05-safety-git-approval-policy.md)
- [ADR-012: Codex adapter and verification hierarchy](adr-012-codex-adapter-and-verification-hierarchy.md)

## DL-0.5A correction

Fresh deterministic draft and final handoff-verification evidence is required
before the respective human source commits. Evidence invalidates on relevant
bound-state or artifact change; source Git remains phase-branch-only and the
Approval-2 Vault exception remains Vault-only. This is future normative
architecture, not runtime implementation.
