# ADR-009: Supervised Autonomy Calibration

## Status

Accepted for DL-0.2; Architecture Freeze pending.

## Context

Passing a small number of tasks does not prove that a workflow, specialist, model, or adapter is safe across wider scope. Autonomous authority must be based on observed performance under the same policies, evidence requirements, and recovery controls that govern real execution.

## Decision

Autonomy requires supervised calibration. New or materially changed workflows, node types, specialists, models, adapters, tools, and authority scopes begin at a human-supervised level. Expansion is a separate explicit human decision supported by representative execution evidence.

Calibration evaluates at minimum:

- deterministic verification and acceptance-criteria success;
- reviewer findings and human overrides;
- policy or approval violations;
- retry, budget, and no-progress behavior;
- recovery and reconciliation outcomes;
- false completion, unsafe action, and cost variance;
- evidence freshness and audit completeness.

Calibration never grants a specialist transition authority or permission to approve itself. Regression, material configuration change, or evidence of unsafe behavior can reduce autonomy and require renewed supervision.

## Rationale

Graduated authority limits exposure while producing evidence about reliability. Reversible, evidence-based expansion is safer than treating a model label or provider claim as a proxy for operational trust.

## Consequences

- Human supervision and escalation remain designed workflow paths.
- Calibration evidence must be bound to the relevant workflow, version, policy, and specialist configuration.
- Autonomy expansion cannot silently relax approvals, budgets, verification, or State Machine guards.
- Operational confidence is specific to scope and cannot be assumed transferable.

## Rejected alternatives

- **Full autonomy at first use**: rejected because no representative evidence exists.
- **Trust based on model or provider identity**: rejected because identity is not calibrated workflow performance.
- **Permanent autonomy level**: rejected because systems and evidence change.

## V1 boundary

V1 remains approval-driven and supervised. Self-pilot and external pilot milestones in DL-P8 are controlled calibration activities, not blanket autonomous authority.

## Future extension point

Later policies may define evidence-backed autonomy tiers and promotion or demotion thresholds while preserving human gates and State Machine authority.

## Related architecture documents

- [Human interaction model](../09-human-interaction-model.md)
- [Implementation roadmap](../10-implementation-roadmap.md)
- [ADR-006: Budgets, retries, and no-progress escalation](adr-006-budgets-retries-and-no-progress-escalation.md)
- [ADR-011: External orchestrators and cost boundaries](adr-011-external-orchestrators-and-cost-boundaries.md)

