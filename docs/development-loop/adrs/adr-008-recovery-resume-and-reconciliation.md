# ADR-008: Recovery, Resume, and Reconciliation

## Status

Accepted for DL-0.2; Architecture Freeze pending.

## Context

An interruption may occur after an external effect but before Panam persists its result. The last persisted enum state alone cannot reveal whether a commit, push, process, artifact write, or Vault action completed. Blind retry can duplicate irreversible or externally visible effects.

## Decision

Recovery is reconciliation-first. It compares durable workflow state and history with the operation journal, process state, filesystem artifacts, local and remote Git reality, and `Vault_work` reality when applicable.

A recovery assessment must identify:

- the last verified checkpoint;
- the next safe action;
- actions safe to retry;
- actions forbidden to repeat;
- external effects requiring reconciliation.

It must also record the evidence and authority used for that assessment. Recovery classifies the situation using the DL-0.1 outcomes `RESUME_SAFE`, `ACTION_ALREADY_COMPLETED`, `RETRY_SAFE`, `HUMAN_DECISION_REQUIRED`, or `UNRECOVERABLE`.

Ambiguous commits, pushes, paid external calls, or Vault writes are forbidden to repeat until reality is reconciled. Unknown local changes are preserved. Invalid or stale approvals are not resumed. The State Machine alone authorizes the resulting recovery transition.

## Rationale

Reconciliation distinguishes "state not persisted" from "effect not completed." Explicit safe and forbidden actions make restart behavior deterministic and prevent duplicated or destructive effects.

## Consequences

- External operations require durable identities, idempotency data where possible, and before/after observations.
- Recovery UI must present evidence and a concrete proposed next action, not only a status enum.
- Destructive cleanup, automatic stash, and blind retry remain prohibited.
- Human decision is required when evidence or authority cannot establish a unique safe continuation.

## Rejected alternatives

- **Resume from the last enum state**: rejected because the external effect may already have occurred.
- **Retry every failed command**: rejected because failure reporting may be ambiguous.
- **Agent memory as recovery log**: rejected because sessions are temporary and non-authoritative.

## V1 boundary

V1 recovery covers the Development Loop's commands, Git and Vault operations, artifacts, and specialist processes under the sequential execution model.

## Future extension point

New specialist nodes must add node-specific reconciliation evidence and forbidden-repeat rules before they may perform effects.

## Related architecture documents

- [Failure and recovery](../06-failure-and-recovery.md)
- [State Machine](../03-state-machine.md)
- [Safety, Git, and approval policy](../05-safety-git-approval-policy.md)
- [ADR-001: Outer-loop ownership and durable state](adr-001-outer-loop-ownership-and-durable-state.md)

## DL-0.5A correction

Fresh deterministic draft and final handoff-verification evidence is required
before the respective human source commits. Evidence invalidates on relevant
bound-state or artifact change; source Git remains phase-branch-only and the
Approval-2 Vault exception remains Vault-only. This is future normative
architecture, not runtime implementation.
