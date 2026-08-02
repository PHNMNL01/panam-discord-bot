# DL-0.2 Architecture Decision Records

## Package purpose and status

This is the canonical Architecture Decision Record package for **DL-0.2 Architecture Decision Records**. It converts the major conclusions of the [DL-0.1 canonical architecture specification](../README.md) into explicit decisions that later milestones must not silently reinterpret.

The governing statement is:

```text
Panam owns the durable outer execution graph.

Specialist agents execute bounded inner loops.

Only the Panam State Machine may authorize workflow transitions.
```

Every ADR in this package has status **Accepted for DL-0.2; Architecture Freeze pending**. Acceptance records the decision for subsequent DL-P0 work; it does not claim that the explicit human Architecture Freeze Gate has occurred.

## Canonical terminology

- **Graph**: the complete explicit workflow model consisting of state, nodes, and edges.
- **Loop**: a cyclic path inside a workflow graph. A loop is not an alternative to a graph.
- **Skill**: a future versioned reusable workflow package containing contracts, graph definitions, policies, approvals, tools, budgets, and recovery rules. A Skill is not merely a prompt.
- **Specialist**: a bounded executor that performs work inside a node or inner loop.
- **State Machine**: the sole authority allowed to authorize workflow state transitions.
- **Agent session**: a temporary execution context that is never the durable workflow source of truth.

## ADR index

| ADR | Status | Decision summary | Boundary |
|---|---|---|---|
| [ADR-001: Outer-loop ownership and durable state](adr-001-outer-loop-ownership-and-durable-state.md) | Accepted for DL-0.2; Architecture Freeze pending | Panam owns the durable outer graph and keeps workflow state outside specialist and agent sessions. | Mandatory v1 |
| [ADR-002: Execution-graph model](adr-002-execution-graph-model.md) | Accepted for DL-0.2; Architecture Freeze pending | Workflows are explicit directed cyclic execution graphs; Development Loop is the first canonical graph. | Mandatory v1; composition is future |
| [ADR-003: Typed nodes, edges, and transition authority](adr-003-typed-nodes-edges-and-transition-authority.md) | Accepted for DL-0.2; Architecture Freeze pending | Nodes and edges are typed, and only the State Machine authorizes transitions. | Mandatory v1 |
| [ADR-004: Evidence-driven completion and freshness](adr-004-evidence-driven-completion-and-freshness.md) | Accepted for DL-0.2; Architecture Freeze pending | Completion requires fresh deterministic evidence bound to an exact Git state. | Mandatory v1 |
| [ADR-005: Approval-bound authority](adr-005-approval-bound-authority.md) | Accepted for DL-0.2; Architecture Freeze pending | Authority derives from policy, contract, approval, and matching repository state rather than prompts. | Mandatory v1 |
| [ADR-006: Budgets, retries, and no-progress escalation](adr-006-budgets-retries-and-no-progress-escalation.md) | Accepted for DL-0.2; Architecture Freeze pending | Every autonomous loop is budgeted; retries stop on limits or no measurable progress. | Mandatory v1 |
| [ADR-007: Sequential execution and concurrency policy](adr-007-sequential-execution-and-concurrency-policy.md) | Accepted for DL-0.2; Architecture Freeze pending | V1 permits one active project run, milestone, phase branch, and specialist writer with sequential execution. | Mandatory v1; parallelism is future |
| [ADR-008: Recovery, resume, and reconciliation](adr-008-recovery-resume-and-reconciliation.md) | Accepted for DL-0.2; Architecture Freeze pending | Recovery reconciles durable records with reality and identifies safe and forbidden next actions. | Mandatory v1 |
| [ADR-009: Supervised autonomy calibration](adr-009-supervised-autonomy-calibration.md) | Accepted for DL-0.2; Architecture Freeze pending | Autonomous authority is earned through supervised, evidence-based calibration. | Mandatory operating constraint; expansion is future |
| [ADR-010: Outcome routing, Skills, and workflow composition](adr-010-outcome-routing-skills-and-workflow-composition.md) | Accepted for DL-0.2; Architecture Freeze pending | Outcome routing, versioned Skills, and graph composition are target abstractions, not implemented v1 capabilities. | Future extension only |
| [ADR-011: External orchestrators and cost boundaries](adr-011-external-orchestrators-and-cost-boundaries.md) | Accepted for DL-0.2; Architecture Freeze pending | External orchestrators remain bounded specialist nodes; expensive execution requires cost preflight approval. | Mandatory boundary; adapters are future |
| [ADR-012: Codex adapter and verification hierarchy](adr-012-codex-adapter-and-verification-hierarchy.md) | Accepted for DL-0.2; Architecture Freeze pending | Codex uses a future supported structured boundary, and deterministic checks precede model judgment. | Mandatory hierarchy; integration is future |

## Source-decision mapping

This table maps every source decision into the consolidated package. The numbered decisions are the DL-0.2 source set, not new workflow states.

| # | Source decision | Canonical ADR |
|---:|---|---|
| 1 | Panam owns the durable outer loop. | [ADR-001](adr-001-outer-loop-ownership-and-durable-state.md) |
| 2 | Specialist agents execute bounded inner loops. | [ADR-001](adr-001-outer-loop-ownership-and-durable-state.md) |
| 3 | Workflows are explicit directed cyclic execution graphs. | [ADR-002](adr-002-execution-graph-model.md) |
| 4 | The State Machine is the sole transition authority. | [ADR-003](adr-003-typed-nodes-edges-and-transition-authority.md) |
| 5 | Nodes and edges have explicit types. | [ADR-003](adr-003-typed-nodes-edges-and-transition-authority.md) |
| 6 | Completion is evidence-driven. | [ADR-004](adr-004-evidence-driven-completion-and-freshness.md) |
| 7 | Verification evidence is fresh and bound to an exact Git state. | [ADR-004](adr-004-evidence-driven-completion-and-freshness.md) |
| 8 | Approvals authorize exact actions, scope, branch, paths, and digest. | [ADR-005](adr-005-approval-bound-authority.md) |
| 9 | Every autonomous loop has attempt, wall-clock, token, cost, and stop budgets. | [ADR-006](adr-006-budgets-retries-and-no-progress-escalation.md) |
| 10 | No measurable progress causes human escalation. | [ADR-006](adr-006-budgets-retries-and-no-progress-escalation.md) |
| 11 | V1 is sequential, with one active milestone and one writer. | [ADR-007](adr-007-sequential-execution-and-concurrency-policy.md) |
| 12 | Workflow state lives outside model and agent sessions. | [ADR-001](adr-001-outer-loop-ownership-and-durable-state.md) |
| 13 | Recovery identifies the next safe action and actions forbidden to repeat. | [ADR-008](adr-008-recovery-resume-and-reconciliation.md) |
| 14 | Autonomy requires supervised calibration. | [ADR-009](adr-009-supervised-autonomy-calibration.md) |
| 15 | Users may eventually specify outcomes rather than providers. | [ADR-010](adr-010-outcome-routing-skills-and-workflow-composition.md) |
| 16 | Reusable workflows may eventually become versioned Skills. | [ADR-010](adr-010-outcome-routing-skills-and-workflow-composition.md) |
| 17 | Development Loop is the first canonical workflow graph. | [ADR-002](adr-002-execution-graph-model.md) |
| 18 | Later Panam may route to one workflow or compose multiple subgraphs. | [ADR-010](adr-010-outcome-routing-skills-and-workflow-composition.md) |
| 19 | External orchestrators remain bounded specialist nodes. | [ADR-011](adr-011-external-orchestrators-and-cost-boundaries.md) |
| 20 | Expensive external execution requires cost preflight approval. | [ADR-011](adr-011-external-orchestrators-and-cost-boundaries.md) |
| 21 | Codex integration should use a supported SDK or app-server boundary. | [ADR-012](adr-012-codex-adapter-and-verification-hierarchy.md) |
| 22 | Deterministic verification precedes model-based judgment. | [ADR-012](adr-012-codex-adapter-and-verification-hierarchy.md) |

## V1 decisions and future extension points

Mandatory v1 decisions govern durable ownership, graph semantics, typed transitions, evidence, approval authority, budgets, sequential execution, recovery, supervised operation, external-node boundaries, and verification order. Panam Development Loop is the first canonical workflow graph.

Outcome-based routing, a Skill Registry, reusable versioned Skills, graph composition, parallel execution, external-orchestrator adapters, and the Codex SDK or app-server integration are future extension points. This package defines constraints for them but does not present them as implemented capabilities.

## DL-P0 dependency chain

```text
DL-0.2 ADR decisions
-> DL-0.3 repository instructions
-> DL-0.4 missing agent role specifications
-> DL-0.5 final read-only architecture audit
-> explicit human Architecture Freeze Gate
```

Architecture Freeze remains pending until that chain ends in the explicit human gate.
