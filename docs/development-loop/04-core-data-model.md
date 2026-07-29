# Core Data Model

## Document purpose

Define the target durable entities and their ownership boundaries.

## In-scope responsibilities

SQLite is the durable source of truth for workflow state. State and state-event records are persisted transactionally; filesystem artifacts are referenced rather than embedded when large.

| Entity | Responsibility |
|---|---|
| projects | Registered trusted project identity. |
| project_policies | Branch, remote, path, command, and deny policy. |
| phases | Phase Contract, branch lifecycle, and phase status. |
| milestones | Milestone identity and sequencing. |
| milestone_contracts | Versioned approved scope, paths, verification, and stop conditions. |
| development_runs | One execution of one approved Milestone Contract. |
| state_events | Immutable transition evidence and reasons. |
| approvals | Phase Start Approval, Approval 1, Approval 2 bindings. |
| artifacts | Referenced raw and validated inputs/outputs with digests. |
| workflow_commands | Durable command queue records. |
| operations | External-operation journal. |
| execution_attempts | Initial and focused Codex attempts. |
| verification_runs / verification_checks | Deterministic verification evidence. |
| review_results | Reviewer decision and evidence binding. |
| handoff_records | Handoff Agent draft, Handoff Finalizer evidence, and the two source-commit references. |
| vault_proposals | Runtime-only Curator proposal and named Vault paths. |
| project_locks | Project/branch ownership and leases. |
| schema_migrations | Applied durable-schema versions. |

## Approved decisions

Every approval binds subject identity, subject digest, repository or Vault base commit, allowed actions, approver, and timestamp. A change to protected approved content invalidates the approval.

Contracts and agent input/output models are versioned, schema-validated, associated with a run and input digests, and retained as raw plus validated artifacts. They cannot directly mutate workflow state.

## Explicit boundaries and out of scope

DL-0.1 creates no schema. DL-P1 implements only the initial durable-foundation subset. Git remains authoritative for code and remote state; SQLite must record observed Git evidence, not replace Git.

## Cross-references

- [State machine](03-state-machine.md)
- [API and agent contracts](08-api-agent-contracts.md)
- [Failure and recovery](06-failure-and-recovery.md)

## Future considerations

Artifact retention, encryption, and detailed migration mechanics require later policy and implementation work.
