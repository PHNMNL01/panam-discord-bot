# ADR-003: Typed Nodes, Edges, and Transition Authority

## Status

Accepted for DL-0.2; Architecture Freeze pending.

## Context

A graph without explicit node and edge semantics allows executors, adapters, or UI code to reinterpret what actions and transitions mean. DL-0.1 already establishes that no role directly assigns state and that transition evaluation considers policy, evidence, approval validity, and external reality.

## Decision

Workflow nodes and edges have explicit types and versioned contracts. The canonical minimum v1 semantic node types are:

```text
DETERMINISTIC
MODEL_CALL
SPECIALIST_AGENT
HUMAN_APPROVAL
EXTERNAL_EFFECT
```

The canonical minimum v1 semantic edge types are:

```text
UNCONDITIONAL
STATE_CONDITIONAL
EVIDENCE_GATED
APPROVAL_GATED
RETRY
ESCALATION
TERMINAL
```

Only the Panam State Machine may authorize workflow state transitions. A UI, prompt, model, framework, provider, specialist, adapter, command handler, or agent session may submit a request, claim, command, or evidence, but may not traverse an edge or assign state.

For each proposed transition the State Machine evaluates the registered graph and policy, current durable state, approved contract, valid approval record, fresh evidence, budgets, locks, and actual Git or Vault reality as applicable. Authorized state and its state event are persisted transactionally.

For example, the evidence-gated transition into handoff requires all of:

```text
Reviewer decision = APPROVED
AND verification evidence is valid
AND inspected Git state still matches
-> PREPARING_HANDOFF
```

The fact that the Reviewer merely finished is not sufficient transition evidence.

## Rationale

Typed contracts prevent accidental responsibility drift. A single transition authority makes safety invariants enforceable and keeps state changes deterministic, reviewable, and recoverable.

## Consequences

- Node inputs, outputs, permitted tools, evidence requirements, and side-effect class must be explicit.
- Edge guards and transition reasons must be auditable.
- Reviewer decisions remain advisory inputs rather than transitions.
- External operation completion does not advance state until the State Machine accepts corresponding evidence.

## Rejected alternatives

- **Agents assign their next state**: rejected because agents are not workflow authorities.
- **Adapters update status after side effects**: rejected because adapters report observations and results only.
- **Untyped generic nodes and edges**: rejected because they permit silent semantic reinterpretation.

## V1 boundary

V1 uses the canonical minimum semantic type sets above together with the phase and Development Run states and correction rules specified in DL-0.1. Exact storage enums, schema representation, and State Machine implementation remain later implementation details.

## Future extension point

Later node or edge types require a versioned architectural extension and audited State Machine support; no new type or extension may create another transition authority.

## Related architecture documents

- [Actors and responsibilities](../01-actors-and-responsibilities.md)
- [State Machine](../03-state-machine.md)
- [API and agent contracts](../08-api-agent-contracts.md)
- [ADR-002: Execution-graph model](adr-002-execution-graph-model.md)
