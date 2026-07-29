# Implementation Roadmap

## Document purpose

Record the approved incremental implementation sequence after the DL-0.1 architecture specification.

## In-scope responsibilities

| Phase | Objective | Boundary |
|---|---|---|
| DL-P0 Architecture Freeze and Bootstrap | Complete architecture documentation, repository instructions, missing role specifications, and the final read-only audit. | Documentation and bootstrap only; module and test scaffolding begin in DL-P1. |
| DL-P1 Durable Domain and Persistence Foundation | Implement initial domain, state machine, SQLite state/events, artifacts, approvals, and registry-policy foundation. | No Codex or Git writes initially. |
| DL-P2 Phase Lifecycle, Command Queue and Worker | Add phase branch lifecycle, durable commands, leases, locks, operation journal, and worker control. | No free-form executor. |
| DL-P3 Planning, Feasibility and Approval Workspace | Add Planner/Feasibility contracts and Web approval workspace. | Planner cannot self-approve. |
| DL-P4 Codex Execution, Verification and Reviewer Loop | Add bounded Codex adapter, deterministic Verifier, Reviewer, and three-fix limit. | Codex has no Git authority. |
| DL-P5 Source Git Completion and Handoff | Add two source commits, source push, handoff draft/finalization, and SOURCE_COMPLETED. | No PR merge. |
| DL-P6 Vault Proposal and Knowledge Workflow | Add Curator artifact, Approval 2, Vault Writer coordination, Vault verification/commit/push. | No automatic registry change without explicit flag. |
| DL-P7 Phase Completion and Pull Request Preparation | Add final regression, Phase Merge Package, and human-confirmed PR creation. | Human approval/merge only. |
| DL-P8 Hardening, Self-Pilot and External Pilot | Add recovery hardening, isolation, operational evidence, and a controlled external pilot. | No relaxation of safety policy. |

## Approved decisions

DL-P0 includes the canonical architecture specification, Architecture Decision Records, Panam repository instructions, missing agent-role specifications, and the final read-only architecture audit. It does not create module or test scaffolding; those begin in DL-P1. DL-0.1 creates only this documentation set. DL-P1 initially implements the durable-foundation subset of the target model. Each subsequent phase preserves the one-phase-branch and sequential-milestone model.

## Approved milestone breakdown

### DL-P0 Architecture Freeze and Bootstrap

| Milestone | Canonical title | Objective | Principal boundary |
|---|---|---|---|
| DL-0.1 | Canonical Architecture Specification | Record the approved v1 architecture locally. | Documentation only. |
| DL-0.2 | Architecture Decision Records | Record approved decisions as ADRs. | No implementation code. |
| DL-0.3 | Panam Repository Instructions | Add repository-local instructions for later work. | No workflow runtime. |
| DL-0.4 | Missing Agent Role Specifications | Define absent role instructions. | No role execution automation. |
| DL-0.5 | Final Read-Only Architecture Audit | Verify the architecture set before the freeze gate. | Read-only; no redesign. |

### DL-P1 Durable Domain and Persistence Foundation

| Milestone | Canonical title | Objective | Principal boundary |
|---|---|---|---|
| DL-1.1 | Package Skeleton and Core Types | Create the Development Loop package structure and core types. | No external execution. |
| DL-1.2 | Phase and Milestone Contracts | Implement validated Phase and Milestone Contracts. | No approval execution. |
| DL-1.3 | Run and Approval Models | Model Development Runs and approval bindings. | No human UI. |
| DL-1.4 | SQLite Migration Foundation | Establish controlled SQLite migration support. | No long-running worker. |
| DL-1.5 | SQLite Repositories | Persist the foundation entities through repositories. | No Git operations. |
| DL-1.6 | State Machine v1 | Implement transition evaluation rules. | State assignment remains internal. |
| DL-1.7 | Transactional Transition Service | Persist transitions and state events transactionally. | No external side effects in transactions. |
| DL-1.8 | Project Registry Read-Only Foundation | Read and validate registered project policy. | No registry writes from prompts. |
| DL-1.9 | Foundation Query CLI | Provide read-only foundation inspection. | No workflow commands. |
| DL-1.10 | Foundation Integration Tests | Verify persistence and transition foundations. | No Codex or Git writes. |

### DL-P2 Phase Lifecycle, Command Queue and Worker

| Milestone | Canonical title | Objective | Principal boundary |
|---|---|---|---|
| DL-2.1 | Durable Command Queue | Persist idempotent queued commands and leases. | No free-form command execution. |
| DL-2.2 | Worker Process Foundation | Establish the separate Development Worker. | No production automation. |
| DL-2.3 | Phase State Machine | Implement phase lifecycle transition rules. | No PR merge authority. |
| DL-2.4 | Read-Only Git Inspector | Gather registered-repository Git evidence. | No Git writes. |
| DL-2.5 | Project and Branch Locks | Add project and branch lock coordination. | No deletion of stale work. |
| DL-2.6 | Controlled Phase Branch Creation | Create an Approval-backed phase branch. | No protected-branch writes. |
| DL-2.7 | Phase Branch Recovery | Reconcile interrupted branch operations. | No blind retry. |
| DL-2.8 | Read-Only Web Blueprint | Add read-only Development Loop Web views. | No inline side effects. |
| DL-2.9 | CyberDeck Worker Monitoring | Surface worker health and diagnostics. | No approval ownership. |

### DL-P3 Planning, Feasibility and Approval Workspace

| Milestone | Canonical title | Objective | Principal boundary |
|---|---|---|---|
| DL-3.1 | Agent Definition Provider | Load registered agent-role definitions. | No mutable role source. |
| DL-3.2 | Model Contract Validation | Validate versioned model contracts. | No state mutation by contracts. |
| DL-3.3 | OpenAI Planner Adapter | Invoke Planner through a bounded adapter. | Planner cannot self-approve. |
| DL-3.4 | Feasibility Assessor | Assess a draft Milestone Contract before Approval 1. | No execution authority. |
| DL-3.5 | Contract Versioning Loop | Manage draft/revision contract versions. | No silent scope expansion. |
| DL-3.6 | Approval 1 Backend | Persist and validate Approval 1. | No UI-only approval state. |
| DL-3.7 | Approval 1 Web UI | Present the Approval 1 decision workspace. | Web creates commands only. |
| DL-3.8 | Human Decision Framework | Handle BLOCKED and decision-required cases. | No automatic override. |
| DL-3.9 | Planning Dry Run | Exercise planning without implementation. | No Codex or Git writes. |

### DL-P4 Codex Execution, Verification and Reviewer Loop

| Milestone | Canonical title | Objective | Principal boundary |
|---|---|---|---|
| DL-4.1 | Safe Subprocess Runner | Execute structured approved processes safely. | No shell or free-form command text. |
| DL-4.2 | Execution Attempt Persistence | Persist attempt evidence and artifacts. | No self-reported evidence trust. |
| DL-4.3 | Codex Adapter Dry-Run Mode | Validate Codex request boundaries without writes. | No source modification. |
| DL-4.4 | Codex Adapter Live Mode | Run approved Codex implementation work. | No Codex Git authority. |
| DL-4.5 | Git Working Tree Evidence | Capture deterministic changed-path and diff evidence. | No commit or push. |
| DL-4.6 | Verification Engine | Run deterministic approved checks. | No AI verification substitute. |
| DL-4.7 | OpenAI Reviewer Adapter | Obtain a separate Reviewer decision. | Reviewer is not safety authority. |
| DL-4.8 | Focused Fix Loop | Enforce the three-fix correction limit. | Original contract remains unchanged. |
| DL-4.9 | Execution and Review Web Views | Display execution and review evidence. | No inline execution. |
| DL-4.10 | Failure Injection Tests | Test execution/review failure handling. | No destructive recovery actions. |

### DL-P5 Source Git Completion and Handoff

| Milestone | Canonical title | Objective | Principal boundary |
|---|---|---|---|
| DL-5.1 | Explicit Git Staging | Stage only approved source files. | Never `git add .`. |
| DL-5.2 | Handoff Agent | Create the Pending handoff draft before commit 1. | No finalization or commit 2. |
| DL-5.3 | Implementation Commit | Create approved source commit 1. | No push to main/master. |
| DL-5.4 | Handoff Finalizer | Insert commit 1 hash and final evidence. | Handoff-only change; no third commit. |
| DL-5.5 | Handoff Finalization Commit | Create source commit 2. | No unrelated source changes. |
| DL-5.6 | Controlled Source Push | Push both commits to the approved phase branch. | No force push. |
| DL-5.7 | Source Git Reconciliation | Reconcile interrupted commit/push operations. | No ambiguous retry. |
| DL-5.8 | Source Completion UI | Present SOURCE_COMPLETED evidence. | SOURCE_COMPLETED is not terminal. |

### DL-P6 Vault Proposal and Knowledge Workflow

| Milestone | Canonical title | Objective | Principal boundary |
|---|---|---|---|
| DL-6.1 | Curator Adapter | Produce a runtime-artifact Vault proposal. | Curator never writes Vault_work. |
| DL-6.2 | Proposal Validation | Validate proposal scope and named paths. | No automatic source proposal file. |
| DL-6.3 | Approval 2 Backend and UI | Bind Approval 2 to one exact proposal. | No registry change without explicit flag. |
| DL-6.4 | Restricted Vault Writer | Apply only approved Vault paths. | No source repository writes. |
| DL-6.5 | Vault Diff Verifier | Verify actual Vault changes. | No approval bypass. |
| DL-6.6 | Vault Commit and Push | Commit and push verified approved Vault work. | One commit and push per Approval 2. |
| DL-6.7 | Stale Proposal and Recovery | Reconcile stale or interrupted Vault work. | No blind overwrite. |
| DL-6.8 | Completion States | Apply COMPLETED or CLOSED_WITHOUT_VAULT rules. | SOURCE_COMPLETED remains a checkpoint. |

### DL-P7 Phase Completion and Pull Request Preparation

| Milestone | Canonical title | Objective | Principal boundary |
|---|---|---|---|
| DL-7.1 | Milestone Ordering | Enforce sequential milestones on one phase branch. | No parallel phase-milestone execution. |
| DL-7.2 | Phase Final Verification | Run final approved phase regression checks. | No merge. |
| DL-7.3 | Target Branch Comparison | Gather target-branch comparison evidence. | Read-only Git inspection. |
| DL-7.4 | Phase Merge Package | Prepare the automatic human-review package. | No PR creation by itself. |
| DL-7.5 | Merge Readiness Reviewer | Assess merge-readiness evidence. | No approval or merge authority. |
| DL-7.6 | Pull Request Preparation UI | Obtain Matej's explicit PR-creation confirmation. | Not a merge surface. |
| DL-7.7 | GitHub Pull Request Adapter | Technically create the human-confirmed Pull Request. | Never approve or merge it. |
| DL-7.8 | Post-Merge Detection | Detect human-controlled merge completion. | No automatic branch deletion. |

### DL-P8 Hardening, Self-Pilot and External Pilot

| Milestone | Canonical title | Objective | Principal boundary |
|---|---|---|---|
| DL-8.1 | Security Review | Review implementation against approved security policy. | No policy relaxation. |
| DL-8.2 | Recovery and Fault Injection Suite | Test reconciliation under failures. | No destructive recovery. |
| DL-8.3 | Artifact Redaction and Retention | Apply artifact privacy and retention controls. | No unapproved sensitive-data access. |
| DL-8.4 | Panam Self-Pilot | Pilot the loop on Panam-approved work. | Same approval gates apply. |
| DL-8.5 | CATIA External Pilot | Pilot on an approved CATIA milestone. | No raw CATIA-log access. |
| DL-8.6 | API Quality Evaluation | Evaluate agent/API quality evidence. | Not a safety-authority substitute. |
| DL-8.7 | Windows VM Migration Plan | Plan migration to an isolated Windows VM. | No host-dependent domain change. |
| DL-8.8 | Development Loop v1 Release | Prepare v1 release evidence and decision. | No implied automatic production rollout. |

## Explicit boundaries and out of scope

This roadmap is not authorization to begin a later phase or create implementation artifacts. Each milestone still requires its own approved Milestone Contract and applicable approval gate.

## Cross-references

- [Purpose and boundaries](00-purpose-and-boundaries.md)
- [Core data model](04-core-data-model.md)
- [Modular architecture](07-modular-architecture.md)

## Future considerations

Milestone-level acceptance criteria, exact file plans, and pilot-project selection belong to approved contracts for the corresponding implementation phase.
