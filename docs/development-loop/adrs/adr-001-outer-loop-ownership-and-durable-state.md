# ADR-001: Outer-loop Ownership and Durable State

## Status

Accepted for DL-0.2; Architecture Freeze pending.

## Context

Long-running development work crosses process restarts, agent sessions, approvals, Git operations, and human decisions. A model or agent session is temporary and can lose context, duplicate work, or report an inaccurate state. Panam therefore needs an owner for the complete durable execution graph while still allowing specialists to perform bounded work.

## Decision

Panam owns the durable outer execution graph. Specialist agents execute only bounded inner loops or node actions under contracts supplied by Panam. An agent session is a temporary execution context and is never the durable workflow source of truth.

Durable truth is divided by subject:

| Subject | Durable authority |
|---|---|
| Workflow state, commands, approvals, and history | SQLite |
| Code, branches, commits, and inspected repository state | Git |
| Approved project knowledge | `Vault_work` |
| Role and workflow instructions | `AI_Agents` |
| Large evidence, outputs, and logs | Filesystem artifacts referenced by durable records |

Specialist output returns claims and artifacts to Panam. It does not assign workflow state, extend its own authority, or become durable simply because an agent reports success.

## Rationale

Separating durable orchestration from temporary execution makes workflows resumable, auditable, and independent of a particular model, provider, framework, or process lifetime. It also preserves the source-of-truth boundaries established in DL-0.1.

## Consequences

- Panam must persist sufficient workflow, command, approval, event, and artifact references to reconstruct a run.
- Specialists must receive bounded inputs and return structured outputs associated with run and input digests.
- Restarting or replacing a specialist cannot erase, advance, or redefine workflow state.
- Git and `Vault_work` reality must be inspected rather than inferred from SQLite or agent memory.

## Rejected alternatives

- **Agent session as workflow owner**: rejected because sessions are temporary and non-authoritative.
- **Specialist self-report as durable state**: rejected because output is a claim requiring independent policy and evidence evaluation.
- **One database as authority for every subject**: rejected because Git, `Vault_work`, and `AI_Agents` retain their established authority.

## V1 boundary

V1 has one Panam-owned Development Loop graph and bounded specialist executions. This ADR creates no database schema, State Machine, Development Worker, or agent runtime.

## Future extension point

Additional workflow graphs and specialist adapters may be added without transferring outer-graph ownership or durable state to a provider, framework, model, prompt, or agent session.

## Related architecture documents

- [DL-0.1 canonical specification](../README.md)
- [Actors and responsibilities](../01-actors-and-responsibilities.md)
- [Core data model](../04-core-data-model.md)
- [ADR-003: Typed nodes, edges, and transition authority](adr-003-typed-nodes-edges-and-transition-authority.md)

