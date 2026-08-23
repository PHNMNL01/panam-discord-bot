# ADR-002: Execution-graph Model

## Status

Accepted for DL-0.2; Architecture Freeze pending.

## Context

The Development Loop contains forward progress, approval gates, focused corrections, recovery paths, and terminal outcomes. Calling the whole workflow a "loop" obscures state, branching, and authority; treating a loop as separate from a graph would also make recovery and verification ambiguous.

## Decision

A Panam workflow is an explicit directed cyclic execution graph consisting of durable state, typed nodes, and typed edges. A loop is a cyclic path inside that graph, never an alternative representation of the graph.

Panam Development Loop is the first canonical workflow graph. Its graph includes the DL-0.1 phase and Development Run paths, approval gates, focused correction loop, recovery and reconciliation paths, source completion checkpoint, Vault path, and terminal outcomes.

The graph definition expresses possible structure. It does not itself authorize traversal; only the State Machine may authorize an edge for a particular run.

## Rationale

An explicit graph makes branching, cycles, invariants, recovery, evidence requirements, and terminal states inspectable. It also provides a stable vocabulary for later reusable workflows without weakening the existing State Machine boundary.

## Consequences

- Every execution position and allowed transition must be representable in the workflow graph.
- Cycles such as focused correction must have explicit entry, continuation, exit, budget, and escalation rules.
- Graph definition, durable run state, and transition authorization remain separate concerns.
- Later documentation must not use "loop" to mean an unstructured prompt cycle or a competing workflow model.

## Rejected alternatives

- **Linear pipeline only**: rejected because correction and recovery introduce governed cycles and branches.
- **Implicit prompt-driven flow**: rejected because it is not durable or auditable.
- **Loop as an alternative to graph**: rejected because a loop is only one cyclic path within the complete workflow graph.

## V1 boundary

V1 defines and eventually implements one canonical Development Loop graph with the sequential policy recorded in DL-0.1. This ADR does not add a graph framework or orchestration dependency.

## Future extension point

Panam may later register more workflow graphs, route an outcome to one graph, or compose governed subgraphs. Those capabilities require later contracts and approvals and are not currently implemented.

## Related architecture documents

- [End-to-end workflow](../02-end-to-end-workflow.md)
- [State Machine](../03-state-machine.md)
- [Implementation roadmap](../10-implementation-roadmap.md)
- [ADR-010: Outcome routing, Skills, and workflow composition](adr-010-outcome-routing-skills-and-workflow-composition.md)

