# ADR-007: Sequential Execution and Concurrency Policy

## Status

Accepted for DL-0.2; Architecture Freeze pending.

## Context

Parallel writers and overlapping milestones complicate approvals, Git-state bindings, recovery, cost control, and attribution. DL-0.1 establishes one long-lived phase branch with sequential milestones and a project/branch locking model.

## Decision

Panam Development Loop v1 permits:

```text
one active project run
one active milestone
one phase branch
one specialist writer
sequential execution
no automatic implementation fan-out
```

Only one write-capable specialist action may operate on the approved project and phase branch at a time. Deterministic read-only inspection may be internally structured as needed, but it may not create competing state transitions, stale evidence, or simultaneous writers. Project and branch locks and durable command ownership enforce this policy.

## Rationale

Sequential execution keeps repository state, approval scope, evidence, and recovery understandable during v1. It minimizes race conditions while the durable State Machine and reconciliation foundations are being established.

## Consequences

- Milestone ordering is explicit and the next milestone does not start automatically.
- Specialist agents do not fan out implementation work.
- Evidence is gathered against a stable, attributable writer sequence.
- Throughput is intentionally traded for safety and auditability in v1.

## Rejected alternatives

- **Multiple implementation agents on one working tree**: rejected because writes and evidence would race.
- **Parallel active milestones on the phase branch**: rejected because scope, approval, and checkpoint ownership would become ambiguous.
- **Provider-managed concurrency**: rejected because a provider is not the outer workflow authority.

## V1 boundary

The sequential policy is mandatory for v1 and is not merely a suggested deployment default.

## Future extension point

Parallel execution may be considered only after explicit graph semantics, isolated workspaces, conflict policy, evidence joins, cost budgets, locks, and State Machine authorization are designed and approved. It is not a current capability.

## Related architecture documents

- [Purpose and boundaries](../00-purpose-and-boundaries.md)
- [End-to-end workflow](../02-end-to-end-workflow.md)
- [Implementation roadmap](../10-implementation-roadmap.md)
- [ADR-002: Execution-graph model](adr-002-execution-graph-model.md)

