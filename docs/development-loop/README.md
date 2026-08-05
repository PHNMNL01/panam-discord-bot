# Panam Development Loop v1 Architecture Specification

## Purpose

This is the canonical repository-local specification for **Panam Development Loop** v1: an approval-driven, auditable orchestrator for one approved software milestone at a time.

Architecture status: **canonical DL-0.1 specification capturing approved architecture decisions. The initial DL-0.5 read-only audit returned `NEEDS_HUMAN_DECISION`; DL-0.5A corrections and a repeat audit remain before the explicit human Architecture Freeze Gate. No runtime implementation is claimed by this documentation package.**

## Document index and reading order

1. [Purpose and boundaries](00-purpose-and-boundaries.md)
2. [Actors and responsibilities](01-actors-and-responsibilities.md)
3. [End-to-end workflow](02-end-to-end-workflow.md)
4. [State machine](03-state-machine.md)
5. [Core data model](04-core-data-model.md)
6. [Safety, Git, and approval policy](05-safety-git-approval-policy.md)
7. [Failure and recovery](06-failure-and-recovery.md)
8. [Modular architecture](07-modular-architecture.md)
9. [API and agent contracts](08-api-agent-contracts.md)
10. [Human interaction model](09-human-interaction-model.md)
11. [Implementation roadmap](10-implementation-roadmap.md)
12. [Panam Repository Instructions](../../AGENTS.md)
13. [DL-0.2 Architecture Decision Records](adrs/README.md)

## Canonical terminology

- **Development Host**: the neutral execution host. The first prototype uses Matej's Windows PC locally; a future Windows VM is the intended isolated deployment.
- **Panam Web App**: the primary human workflow cockpit.
- **CyberDeck**: the technical operations surface.
- **Development Worker**: the separate process that claims durable commands and performs long-running work.
- **Phase Contract** / **Milestone Contract**: approved, versioned scope and policy inputs.
- **Development Run**: one execution of one approved Milestone Contract.
- **Verifier**, **Reviewer**, **Handoff Agent**, **Handoff Finalizer**, **Knowledge Curator**, **Vault Writer**: bounded roles described in this specification.
- **Phase Start Approval**, **Approval 1**, **Approval 2**: the three human-controlled gates.
- **SOURCE_COMPLETED**: durable source-side checkpoint. **COMPLETED** and **CLOSED_WITHOUT_VAULT** are terminal milestone outcomes defined in the state machine.

## Sources of truth

| Subject | Source of truth |
|---|---|
| Development Loop workflow state | SQLite |
| Code, branches, commits, and remote state | Git |
| Approved derived project knowledge | `Vault_work` |
| Agent-role definitions and workflow instructions | `AI_Agents` |
| Large run outputs | Filesystem artifacts referenced from SQLite |

## Boundaries

This specification records approved v1 decisions. It does not create Python modules, schemas, agents, approval records, or worker processes. Implementation sequencing is in [the roadmap](10-implementation-roadmap.md).

## Future considerations

The physical Development Host may change, but the domain model must not depend on a particular PC, VM, or VDS.

## DL-0.5A correction status

DL-0.5A corrects stale current-state wording, source-versus-Vault Git policy,
and deterministic handoff-verification gates. It does not grant Architecture
Freeze or implement orchestration runtime.
