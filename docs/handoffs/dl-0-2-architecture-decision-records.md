# Knowledge Handoff

A Knowledge Handoff is an evidence package between implementation agents, mapping agents, curators, reviewers, and future conversations. It is not the vault and does not authorize Vault or Capability Registry changes.

## Metadata

| Field | Value |
| --- | --- |
| Project | Panam APP |
| Repository | `C:\Panam_APP` |
| Milestone ID | DL-0.2 |
| Milestone name | Architecture Decision Records |
| Workflow state | `FINALIZING_HANDOFF` |
| Architecture implementation | `APPROVED` |
| Handoff content | `FINALIZED` |
| Focused correction attempts used | 1 of maximum 3 |
| Date | 2026-08-02 |
| Authoring agent | Codex |
| Related branch | `phase/development-loop-architecture` |
| Base HEAD | `90e98a6f74ea506c7bdabe7ffc64986e6e7573d9` |
| Related implementation commit | `bde6fb67acb39673a4ecc5c4cb8c58503421e0b5` |
| Implementation commit message | `Add DL-0.2 architecture decision records` |
| Verified implementation commit scope | 15 documentation files, 1,094 insertions |
| Supplied implementation summary | 15 documentation files, 1,694 insertions; not corroborated by Git |
| Handoff-finalization commit | Not yet created |
| Source push | Pending |
| Source milestone state | Not yet `SOURCE_COMPLETED` |
| Vault proposal | Pending |
| Approval 2 | Pending |
| Vault lifecycle | Pending |
| Architecture Freeze | Pending; not complete |
| Previous handoff | `docs/handoffs/dl-0-1-canonical-architecture-specification.md` |
| Next expected step | Create the documentation-only handoff-finalization commit, then push and verify both source commits |

## Task

Create the canonical DL-0.2 Architecture Decision Record package. This documentation-only milestone converts the major conclusions of the [DL-0.1 canonical architecture specification](../development-loop/README.md) into explicit accepted decisions that later milestones must not silently reinterpret.

The package is governed by:

```text
Panam owns the durable outer execution graph.

Specialist agents execute bounded inner loops.

Only the Panam State Machine may authorize workflow transitions.
```

The milestone does not implement any documented architecture. DL-0.2 is not `COMPLETED`; it is in `FINALIZING_HANDOFF` with architecture implementation approved, implementation commit created, and handoff-finalization, source push, and Vault lifecycle work pending.

## What changed

Created one canonical ADR index and twelve consolidated ADRs under `docs/development-loop/adrs/`. Added the ADR package to the existing Development Loop documentation reading order. The package preserves DL-0.1 terminology and maps all twenty-two source decisions to canonical ADRs.

The initial focused architecture review returned `NEEDS_FIX` with three findings:

1. ADR-003 did not lock the exact minimum v1 node and edge types.
2. ADR-006 did not contain the complete autonomous-loop budget contract or the two-consecutive-attempt no-progress threshold.
3. ADR-005 used ambiguous Pull Request authority wording.

Focused correction attempt 1, the only correction attempt used out of the maximum three, resolved all three findings without redesigning the package:

- ADR-003 now defines the five canonical minimum v1 node types and seven canonical minimum v1 edge types, preserves the State Machine as sole transition authority, and includes the evidence-gated `PREPARING_HANDOFF` example.
- ADR-006 now defines the seven minimum budget-policy concepts, preserves one primary Codex execution plus at most three focused corrections, and stops automatic retry after two consecutive attempts without measurable progress.
- ADR-005 now states human-confirmed Pull Request creation and human-controlled merge, with any technical Panam PR creation limited to a future adapter after explicit human confirmation.

The final focused architecture review result is `APPROVED`. All DL-0.2 acceptance criteria were rechecked and passed. Architecture Freeze is not complete.

Implementation and review history:

1. The initial ADR implementation was completed.
2. Focused architecture review returned `NEEDS_FIX`.
3. Three narrow findings were identified in ADR-003, ADR-005, and ADR-006.
4. Focused correction attempt 1 resolved all three findings.
5. Final focused architecture review returned `APPROVED`.
6. The approved package was committed as `bde6fb67acb39673a4ecc5c4cb8c58503421e0b5` with message `Add DL-0.2 architecture decision records`.

Git inspection of that commit reports 15 documentation files and 1,094 insertions. The supplied finalization brief reported 1,694 insertions; because both `git show --stat` and summed `git show --numstat` report 1,094, this handoff preserves the discrepancy rather than asserting the unsupported count.

### ADR summary

| ADR | Decision summary |
| --- | --- |
| ADR-001 | Panam owns the durable outer execution graph; specialists and agent sessions remain bounded and non-durable. |
| ADR-002 | Workflows are explicit directed cyclic execution graphs; loops are cyclic paths within graphs. |
| ADR-003 | V1 nodes and edges have canonical minimum semantic types; only the State Machine authorizes transitions. |
| ADR-004 | Completion requires fresh deterministic evidence bound to an exact inspected Git state. |
| ADR-005 | Authority derives from matching policy, contract, approval, and repository state rather than prompts. |
| ADR-006 | Autonomous and repeated loops have minimum budget contracts and stop on limits or two unchanged attempts. |
| ADR-007 | V1 has one active project run, milestone, phase branch, and specialist writer with sequential execution. |
| ADR-008 | Recovery reconciles durable records with reality and identifies safe actions and forbidden repeats. |
| ADR-009 | Autonomous authority requires supervised, evidence-based calibration. |
| ADR-010 | Outcome routing, versioned Skills, and workflow composition are future target abstractions only. |
| ADR-011 | External orchestrators remain bounded specialist nodes; expensive execution requires cost preflight approval. |
| ADR-012 | Codex should use a future supported structured adapter; deterministic verification precedes model judgment. |

### Source-decision mapping

| # | Source decision | Canonical ADR |
| ---: | --- | --- |
| 1 | Panam owns the durable outer loop. | ADR-001 |
| 2 | Specialist agents execute bounded inner loops. | ADR-001 |
| 3 | Workflows are explicit directed cyclic execution graphs. | ADR-002 |
| 4 | The State Machine is the sole transition authority. | ADR-003 |
| 5 | Nodes and edges have explicit types. | ADR-003 |
| 6 | Completion is evidence-driven. | ADR-004 |
| 7 | Verification evidence is fresh and bound to an exact Git state. | ADR-004 |
| 8 | Approvals authorize exact actions, scope, branch, paths, and digest. | ADR-005 |
| 9 | Every autonomous loop has attempt, wall-clock, token, cost, and stop budgets. | ADR-006 |
| 10 | No measurable progress causes human escalation. | ADR-006 |
| 11 | V1 is sequential, with one active milestone and one writer. | ADR-007 |
| 12 | Workflow state lives outside model and agent sessions. | ADR-001 |
| 13 | Recovery identifies the next safe action and actions forbidden to repeat. | ADR-008 |
| 14 | Autonomy requires supervised calibration. | ADR-009 |
| 15 | Users may eventually specify outcomes rather than providers. | ADR-010 |
| 16 | Reusable workflows may eventually become versioned Skills. | ADR-010 |
| 17 | Development Loop is the first canonical workflow graph. | ADR-002 |
| 18 | Later Panam may route to one workflow or compose multiple subgraphs. | ADR-010 |
| 19 | External orchestrators remain bounded specialist nodes. | ADR-011 |
| 20 | Expensive external execution requires cost preflight approval. | ADR-011 |
| 21 | Codex integration should use a supported SDK or app-server boundary. | ADR-012 |
| 22 | Deterministic verification precedes model-based judgment. | ADR-012 |

## Files changed

Canonical milestone paths:

| Change | Path | Responsibility |
| --- | --- | --- |
| Modified | `docs/development-loop/README.md` | Adds the DL-0.2 ADR package to the canonical Development Loop reading order. |
| Created | `docs/development-loop/adrs/README.md` | Canonical DL-0.2 index, terminology, statuses, summaries, decision mapping, boundaries, and dependency chain. |
| Created | `docs/development-loop/adrs/adr-001-outer-loop-ownership-and-durable-state.md` | Durable outer ownership, bounded specialists, and sources of truth. |
| Created | `docs/development-loop/adrs/adr-002-execution-graph-model.md` | Directed cyclic workflow graph and loop semantics. |
| Created | `docs/development-loop/adrs/adr-003-typed-nodes-edges-and-transition-authority.md` | Canonical v1 node/edge types and State Machine authority. |
| Created | `docs/development-loop/adrs/adr-004-evidence-driven-completion-and-freshness.md` | Exact-Git-state evidence binding and invalidation. |
| Created | `docs/development-loop/adrs/adr-005-approval-bound-authority.md` | Exact approval authority and Pull Request boundary. |
| Created | `docs/development-loop/adrs/adr-006-budgets-retries-and-no-progress-escalation.md` | Minimum loop budget contract and no-progress escalation. |
| Created | `docs/development-loop/adrs/adr-007-sequential-execution-and-concurrency-policy.md` | Mandatory sequential v1 execution policy. |
| Created | `docs/development-loop/adrs/adr-008-recovery-resume-and-reconciliation.md` | Reconciliation-first recovery and forbidden repeats. |
| Created | `docs/development-loop/adrs/adr-009-supervised-autonomy-calibration.md` | Supervised autonomy calibration. |
| Created | `docs/development-loop/adrs/adr-010-outcome-routing-skills-and-workflow-composition.md` | Future-only outcomes, Skills, and graph composition. |
| Created | `docs/development-loop/adrs/adr-011-external-orchestrators-and-cost-boundaries.md` | External-orchestrator and cost-preflight boundaries. |
| Created | `docs/development-loop/adrs/adr-012-codex-adapter-and-verification-hierarchy.md` | Future Codex interface and deterministic-first verification. |

Handoff artifact, listed separately:

| Change | Path | Responsibility |
| --- | --- | --- |
| Created in implementation commit; modified for finalization | `docs/handoffs/dl-0-2-architecture-decision-records.md` | Final source-handoff content for the approved DL-0.2 documentation package. |

## Tests performed

| Check | Command or method | Files or suites involved | Result | Notes |
| --- | --- | --- | --- | --- |
| Pre-handoff branch inspection | `git branch --show-current` | Repository state | Passed | Returned `phase/development-loop-architecture`. |
| Pre-handoff HEAD inspection | `git rev-parse HEAD` | Repository state | Passed | Returned base HEAD `90e98a6f74ea506c7bdabe7ffc64986e6e7573d9`. |
| Pre-handoff tracking inspection | `git rev-parse --abbrev-ref --symbolic-full-name "@{u}"` | Repository state | Passed | Returned `origin/phase/development-loop-architecture`. |
| Pre-handoff status | `git status --short` | Repository state | Passed | Only modified `docs/development-loop/README.md` and untracked `docs/development-loop/adrs/` were present. |
| Pre-handoff diff check | `git diff --check` | Tracked documentation diff | Passed | Exit code 0. |
| Pre-handoff diff summary | `git diff --stat`; `git diff --name-only` | Tracked documentation diff | Passed | Reported the one-line parent README addition; untracked ADRs required supplemental inspection. |
| ADR inventory and count | Read-only PowerShell filesystem inspection | `docs/development-loop/adrs/` | Passed | Twelve ADR files plus one ADR index were present. |
| Required ADR sections | Read-only PowerShell content check | Twelve ADRs | Passed | Every ADR contains Title, Status, Context, Decision, Rationale, Consequences, Rejected alternatives, V1 boundary, Future extension point, and Related architecture documents. |
| Decision mapping | Read-only PowerShell index check | ADR index | Passed | All twenty-two source decisions are mapped. |
| Focused correction assertions | Read-only PowerShell exact-term checks | ADR-003, ADR-005, ADR-006 | Passed | Five node types, seven edge types, seven budget fields, two-attempt threshold, sole authority, evidence-gated example, and PR wording were present. |
| Relative-link validation | Read-only PowerShell path resolution | Development Loop and handoff Markdown | Passed | All inspected relative Markdown links resolved after the handoff draft was created. |
| Post-handoff repository refresh | Required Git and supplemental filesystem commands | Full uncommitted scope | Passed | Branch and HEAD remained unchanged; 12 ADRs, 14 new Markdown files including the handoff, required handoff sections/status fields, documentation-only scope, and no positive freeze-completion claim were confirmed. |
| Implementation commit identity | `git rev-parse HEAD`; `git show --stat --oneline --decorate HEAD` | Implementation commit | Passed | Confirmed HEAD `bde6fb67acb39673a4ecc5c4cb8c58503421e0b5`, message, 15 documentation files, and 1,094 insertions. |
| Implementation commit insertion total | Summed `git show --numstat --format= HEAD` | Implementation commit | Passed | Independently confirmed 15 files, 1,094 insertions, and 0 deletions; supplied 1,694-insertion summary was not corroborated. |
| Pre-finalization worktree | `git status --short` | Repository state | Passed | Working tree was clean before this finalization-only edit. |
| Finalization-only diff | `git diff --check`; `git diff --stat`; `git diff --name-only`; `git status --short` | Handoff finalization | Passed | Diff check returned exit code 0 and the only changed path was this handoff. |

Skipped tests:

- No runtime, unit, integration, database, agent-execution, Git-write, or Vault tests apply because DL-0.2 is documentation-only and implements no runtime behavior.

## Architecture impact

- Public APIs and runtime entry points: none implemented or changed.
- State and persistence: no SQLite schema or State Machine implementation was created.
- Workflow authority: Panam owns the durable outer execution graph; the State Machine remains the sole transition authority.
- Execution: Development Loop remains the first canonical workflow graph, sequential in v1, with one active milestone and one specialist writer.
- Evidence: completion is bound to fresh deterministic evidence for an exact Git state; specialist output remains a claim.
- Approvals: exact policy, contract, approval, and matching-state bindings govern authority.
- Recovery: future implementation must identify last verified checkpoint, next safe action, safe retries, forbidden repeats, and effects requiring reconciliation.
- Future abstractions: Skills, outcome routing, workflow composition, parallelism, external-orchestrator adapters, and the Codex structured adapter remain unimplemented extension points.

## New decisions

The handoff records the accepted DL-0.2 decisions represented by the twelve ADRs and the twenty-two-decision mapping above. The focused correction additionally locks:

- the five canonical minimum v1 node types;
- the seven canonical minimum v1 edge types;
- the evidence-gated conditions for transition to `PREPARING_HANDOFF`;
- the seven minimum autonomous-loop budget-policy concepts;
- the two-consecutive-attempt no-progress threshold;
- human-confirmed Pull Request creation and human-controlled merge.

These are documentation decisions. Exact schema representation, numeric budget values, adapters, and runtime enforcement remain later implementation work.

## Possible reusable capability candidates

No reusable capability candidate is asserted by this milestone. ADR-010 defines a future Skill as a versioned governed workflow package, not merely a prompt, but DL-0.2 creates no Skill Registry, Skill package, routing implementation, or Capability Registry entry.

## Known limitations

- DL-0.2 documents decisions but implements no workflow runtime, schema, State Machine, Development Worker, adapter, router, or registry.
- Exact storage enums for node and edge types remain later implementation details.
- Exact schema types and configured numeric budget values remain later policy and implementation decisions.
- Implementation commit `bde6fb67acb39673a4ecc5c4cb8c58503421e0b5` exists and contains the approved documentation package.
- The handoff-finalization commit had not yet been created when this document was finalized. Its hash must be verified from Git history after the documentation-only finalization commit is created.
- Source push, Vault proposal, Approval 2, and the Vault lifecycle remain Pending.
- The source milestone state is not yet `SOURCE_COMPLETED`.
- Architecture Freeze is not complete.

The remaining Architecture Freeze dependency chain is:

```text
DL-0.3 repository instructions
-> DL-0.4 missing agent role specifications
-> DL-0.5 final read-only architecture audit
-> explicit human Architecture Freeze Gate
```

## Unresolved questions

The following are non-blocking future implementation decisions rather than DL-0.2 failures:

- exact node, edge, budget, approval, evidence, and recovery schema representation;
- configured wall-clock, token, cost, and timeout values;
- structured Codex SDK or app-server selection;
- future Skill packaging, registry trust, routing, and graph-composition rules;
- external-orchestrator adapter selection and cost-estimation mechanics;
- criteria for any future concurrency extension beyond sequential v1.

## Evidence classification

### Verified implementation facts

- The implementation began from base HEAD `90e98a6f74ea506c7bdabe7ffc64986e6e7573d9`.
- The repository was inspected for handoff finalization on branch `phase/development-loop-architecture` at HEAD `bde6fb67acb39673a4ecc5c4cb8c58503421e0b5`.
- The canonical ADR index, twelve ADR files, parent README change, and draft handoff are present in the implementation commit.
- The implementation commit message is `Add DL-0.2 architecture decision records`.
- Git reports 15 documentation files, 1,094 insertions, and 0 deletions for the implementation commit.
- The index maps all twenty-two source decisions.
- Required ADR sections, links, terminology, exact focused-correction terms, documentation-only scope, and whitespace were checked as recorded in this handoff.
- Focused correction attempt 1 resolved all three focused-review findings; the supplied final architecture review result is `APPROVED`.
- At the time of the implementation and initial handoff-draft operation, no commit or push had been performed. The implementation was subsequently committed as `bde6fb67acb39673a4ecc5c4cb8c58503421e0b5`.
- No handoff-finalization commit hash is claimed. Source push, Vault proposal, Approval 2, and Vault write remain pending.

### Project-owner-confirmed operational facts

- The DL-0.2 final focused architecture review result is `APPROVED`.
- One focused correction attempt was used out of the maximum three.
- The current workflow state for this handoff operation is `FINALIZING_HANDOFF`.

### Proposed future behavior

- DL-0.3, DL-0.4, and DL-0.5 remain future documentation milestones before the explicit human Architecture Freeze Gate.
- SDK/app-server Codex integration, Skills, routing, workflow composition, external-orchestrator adapters, and parallel execution remain future-only.
- The next source-side action is the documentation-only handoff-finalization commit. After it exists, both source commits must be pushed and verified before `SOURCE_COMPLETED` may be authorized.

### Unresolved assumptions

- Exact later implementation choices remain subject to approved Milestone Contracts and their evidence.
- Pending source and Vault lifecycle fields must be finalized only from actual later evidence.
- The handoff-finalization commit hash must be observed from Git history after that commit exists; it cannot reliably be embedded in the commit that creates it. Later source evidence or a Vault proposal may record the observed hash without modifying this handoff again.

## Repository and vault impact

| Field | Value |
| --- | --- |
| Source repository documentation changed | Yes: the implementation commit contains the listed Development Loop documentation and the draft handoff; this finalization modifies only the handoff. |
| Runtime implementation changed | No |
| Dependency files changed | No |
| Agent repository files changed | No |
| Vault files changed | No |
| Capability Registry changed | No |
| Implementation commit | `bde6fb67acb39673a4ecc5c4cb8c58503421e0b5` |
| Implementation commit message | `Add DL-0.2 architecture decision records` |
| Verified implementation commit scope | 15 documentation files, 1,094 insertions |
| Handoff-finalization commit | Not yet created; verify from Git history after creation |
| Source push | Pending |
| Source milestone state | Not yet `SOURCE_COMPLETED` |
| Vault proposal created | No; status Pending |
| Approval 2 | Pending |
| Vault lifecycle | Pending |
| Architecture Freeze | Pending; not complete |
| Human review required | Yes: handoff review and later explicit Architecture Freeze Gate. |

A Knowledge Handoff does not authorize Vault writes or Capability Registry changes.

## Review checklist

- [x] Milestone identity, goal, documentation-only scope, and DL-0.1 relationship recorded.
- [x] Exact created and modified paths recorded.
- [x] All twelve ADRs summarized.
- [x] All twenty-two source decisions mapped.
- [x] Focused review and correction attempt 1 recorded.
- [x] Final architecture review result recorded as `APPROVED`.
- [x] Architecture Freeze explicitly recorded as incomplete.
- [x] Runtime, dependency, agent, Vault, and Capability Registry non-changes recorded.
- [x] Implementation commit hash, message, and verified scope recorded.
- [x] Handoff-finalization commit recorded as not yet created; no hash invented.
- [x] Source push recorded as Pending.
- [x] Source milestone recorded as not yet `SOURCE_COMPLETED`.
- [x] Vault proposal and Approval 2 recorded as Pending.
- [x] Post-draft repository and supplemental verification refreshed.
- [x] Finalization-only diff verified.
