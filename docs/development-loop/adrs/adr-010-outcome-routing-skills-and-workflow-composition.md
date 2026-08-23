# ADR-010: Outcome Routing, Skills, and Workflow Composition

## Status

Accepted for DL-0.2; Architecture Freeze pending.

## Context

Users should eventually be able to state a governed outcome without choosing a provider or manually assembling execution steps. Reusable workflows and graph composition could support that goal, but treating prompts or provider choices as workflows would lose contracts, policy, budgets, approvals, and recovery semantics.

## Decision

Outcome-based routing, versioned Skills, and workflow composition are target abstractions only. They are not implemented Development Loop v1 capabilities.

A future Skill is a versioned reusable workflow package, not merely a prompt. It may package:

- identity and version;
- input contract;
- workflow graph and node definitions;
- allowed tools and specialist policy;
- approvals and verification policy;
- attempt, time, token, cost, and stop budgets;
- outputs;
- recovery policy.

Panam may eventually resolve an approved outcome to one registered workflow or compose multiple compatible subgraphs. Routing selects governed workflow definitions, not an authority provider. Composition must preserve typed boundaries, approval scope, evidence bindings, budgets, recovery rules, and State Machine transition authority.

Panam Development Loop remains the first canonical workflow graph.

## Rationale

Separating outcomes, workflows, specialists, and providers permits reuse and provider substitution without allowing prompts or routing systems to become workflow authorities.

## Consequences

- Future Skill identity and version must be included in contracts and evidence.
- Routing or composition cannot silently expand tools, paths, cost, or lifecycle authority.
- Subgraph inputs, outputs, failure behavior, and recovery joins must be explicit.
- Documentation must not claim a current Skill Registry, outcome router, or multi-workflow composer.

## Rejected alternatives

- **Prompt templates as Skills**: rejected because they omit durable graph and governance contracts.
- **Provider name as workflow selection**: rejected because providers are implementation choices, not outcome semantics.
- **Unvalidated graph concatenation**: rejected because policies, types, budgets, and recovery behavior may conflict.

## V1 boundary

V1 implements neither outcome routing, a Skill Registry, reusable Skills, multi-agent routing, nor graph composition. Development Loop executes its single canonical sequential graph.

## Future extension point

A later approved architecture may define Skill packaging, registry trust, compatibility rules, outcome resolution, and subgraph composition under the existing durable outer graph.

## Related architecture documents

- [Purpose and boundaries](../00-purpose-and-boundaries.md)
- [API and agent contracts](../08-api-agent-contracts.md)
- [ADR-002: Execution-graph model](adr-002-execution-graph-model.md)
- [ADR-003: Typed nodes, edges, and transition authority](adr-003-typed-nodes-edges-and-transition-authority.md)

