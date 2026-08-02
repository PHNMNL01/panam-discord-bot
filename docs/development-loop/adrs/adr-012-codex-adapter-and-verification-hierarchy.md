# ADR-012: Codex Adapter and Verification Hierarchy

## Status

Accepted for DL-0.2; Architecture Freeze pending.

## Context

Codex is the primary implementation specialist in the Development Loop, but terminal scraping, unstructured prompts, or self-reported success would create a fragile and non-auditable integration. Model judgment is valuable for semantic review but cannot replace deterministic repository inspection.

## Decision

The preferred future Codex boundary is a supported structured machine interface, such as an official SDK or app-server, behind a Panam port and adapter. Requests and responses are versioned, schema-validated, bounded by the approved Milestone Contract, associated with run and input digests, and retained as raw and validated artifacts.

Codex is a bounded specialist. It may modify only approved paths and run approved verification, but it cannot commit, push, merge, rebase, alter branches, write `Vault_work` or `AI_Agents`, install dependencies without a new human decision, expand scope, or authorize transitions.

Verification follows this hierarchy:

1. inspect actual repository identity, branch, HEAD, working tree or diff, and allowed paths;
2. run approved deterministic checks and capture exact-state evidence;
3. evaluate deterministic policy and evidence freshness;
4. provide the approved contract, actual diff, Codex result, and deterministic evidence to the separate Reviewer;
5. let the State Machine evaluate policy, approval validity, evidence, Reviewer advice, budgets, and current Git reality.

Deterministic verification always precedes model-based Reviewer interpretation. Reviewer output is advisory and is not a safety or transition authority.

## Rationale

A structured supported boundary is more stable, testable, and observable than terminal-text coupling. Deterministic-first verification gives model review grounded facts and preserves independent completion evidence.

## Consequences

- The adapter must isolate provider protocol from Panam domain contracts.
- Codex output cannot substitute for Verifier evidence.
- Reviewer input must bind to the same contract and Git state as deterministic verification.
- Adapter or model changes require compatibility checks and supervised calibration.

## Rejected alternatives

- **Terminal scraping as the canonical integration**: rejected as brittle and weakly structured.
- **Codex declares its own success**: rejected because implementation output is a claim.
- **Reviewer before deterministic checks**: rejected because model judgment would lack authoritative repository evidence.
- **Codex with Git lifecycle authority**: rejected because it is a specialist, not the outer workflow owner.

## V1 boundary

DL-0.2 documents the preferred boundary and hierarchy only. It does not integrate a Codex SDK or app-server, implement an adapter, or modify runtime dependencies.

## Future extension point

An approved later milestone may select a supported interface and implement the port, adapter, contract tests, evidence capture, and recovery behavior without changing Codex's specialist boundary.

## Related architecture documents

- [Actors and responsibilities](../01-actors-and-responsibilities.md)
- [API and agent contracts](../08-api-agent-contracts.md)
- [Implementation roadmap](../10-implementation-roadmap.md)
- [ADR-004: Evidence-driven completion and freshness](adr-004-evidence-driven-completion-and-freshness.md)
