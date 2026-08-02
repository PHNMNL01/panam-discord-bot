# ADR-011: External Orchestrators and Cost Boundaries

## Status

Accepted for DL-0.2; Architecture Freeze pending.

## Context

An external service may orchestrate its own agents and internal graph, incur variable cost, and perform remote effects. Integrating such a service as a peer workflow authority would fragment lifecycle control, approval scope, evidence, and recovery.

## Decision

Panam treats an external orchestrator as one bounded specialist node even when that orchestrator has its own internal graph. Its internal execution is an implementation detail behind a versioned request/response contract.

An external orchestrator must not independently:

- change Panam workflow state;
- authorize lifecycle transitions;
- approve its own costs;
- expand scope;
- write to Git or `Vault_work` outside the approved contract.

Panam supplies exact inputs, scope, allowed effects, budgets, evidence requirements, idempotency or reconciliation identifiers, and stop conditions. The external result is a claim and artifact set evaluated by Panam policy, verification, and the State Machine.

Expensive external execution requires a cost preflight approval bound to the provider or service class, priced action or estimate, maximum cost, currency, scope, contract digest, and matching repository state. Material estimate change or budget overrun stops execution for a new human decision.

## Rationale

The bounded-node model preserves one durable outer graph while allowing specialized external capabilities. Preflight approval prevents a service from creating its own economic authority.

## Consequences

- External adapters require timeouts, cost metering, bounded output, audit records, and reconciliation behavior.
- Nested external activity does not appear as authoritative Panam transitions.
- Provider substitution is possible only when the same node contract and approved bounds remain satisfied.
- Unknown cost or ambiguous remote completion causes a human-decision or reconciliation path.

## Rejected alternatives

- **External orchestrator as co-owner of the workflow**: rejected because transition and durable-state authority would split.
- **Provider-controlled scope or cost expansion**: rejected because a specialist cannot approve itself.
- **Treat remote success as completion evidence**: rejected because external output is still a claim.

## V1 boundary

V1 adds no external-orchestrator adapter or automatic cost execution. The constraints apply to any later integration proposal.

## Future extension point

Approved adapters may support specific external orchestrators after contracts, cost preflight, verification, and reconciliation are designed and calibrated.

## Related architecture documents

- [Safety, Git, and approval policy](../05-safety-git-approval-policy.md)
- [API and agent contracts](../08-api-agent-contracts.md)
- [ADR-005: Approval-bound authority](adr-005-approval-bound-authority.md)
- [ADR-009: Supervised autonomy calibration](adr-009-supervised-autonomy-calibration.md)

