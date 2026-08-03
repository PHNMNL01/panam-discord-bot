# Knowledge Handoff

A Knowledge Handoff is an evidence package between implementation agents,
reviewers, curators, and future conversations. It is not the Vault and does not
authorize Vault or Capability Registry changes.

## Metadata

| Field | Value |
| --- | --- |
| Project | Panam APP |
| Repository | `C:\Panam_APP` |
| Phase | DL-P0 Architecture Freeze and Bootstrap |
| Milestone ID | DL-0.3 |
| Milestone name | Panam Repository Instructions |
| Workflow state | `PREPARING_HANDOFF` |
| Repository instructions implementation | `APPROVED` |
| Handoff draft | `PREPARED` |
| Focused correction attempts used | 0 of maximum 3 |
| Date | 2026-08-03 |
| Authoring agent | Codex |
| Related branch | `phase/development-loop-architecture` |
| Base HEAD | `610161e45edaeec5fcec19e134e615dccfd6bbbd` |
| Implementation commit | Pending |
| Handoff-finalization commit | Pending |
| Source push | Pending |
| Source milestone state | Not yet `SOURCE_COMPLETED` |
| Vault proposal | Pending |
| Approval 2 | Pending |
| Vault lifecycle | Pending |
| Architecture Freeze | Pending; not complete |
| Previous handoff | `docs/handoffs/dl-0-2-architecture-decision-records.md` |
| Next expected step | Approved implementation commit, then handoff finalization, source push, and source verification |

## Task

Create canonical root repository instructions for Panam APP and add them to the
Development Loop reading order. This DL-P0 milestone is documentation/bootstrap
only: it translates the accepted DL-0.1 architecture and DL-0.2 ADRs into
operational repository rules. It does not implement the Development Loop.

DL-0.3 follows the [DL-0.1 specification](../development-loop/README.md) and
the [DL-0.2 ADR package](../development-loop/adrs/README.md). The independent
repository-instructions review result is `APPROVED`; no focused correction was
required.

## What changed

Created root `AGENTS.md` as the single repository-wide instruction source and
added it as item 12 in the Development Loop reading order, before the ADR
package. It establishes scope, authority, evidence, data, lifecycle, recovery,
and reporting rules for later repository work without creating a competing
instruction hierarchy.

### Root instruction summary

- Repository-wide scope with explicitly approved nested-instruction inheritance.
- Precedence: global safety; registered policy and applicable `AGENTS.md`;
  approved contracts and approvals; bounded task; then agent convenience.
- Mandatory pre-edit branch, HEAD, tracking, worktree, scope, instructions, and
  relevant-implementation inspection; unrelated changes are preserved.
- Approved-path-only changes; prompts cannot expand scope; new dependencies,
  schemas, services, integrations, or repositories need human authorization.
- Codex may inspect Git and modify approved files, but has no independent branch,
  staging, commit, push, Pull Request, merge, Vault, or State Machine authority.
- Deterministic verification precedes Reviewer interpretation; summaries are
  claims, evidence is state-bound, and relevant change makes evidence stale.
- Secrets, sensitive data, runtime data, production data, local state, logs,
  caches, and generated user files remain protected.
- V1 remains sequential with one active project run, milestone, phase branch,
  and specialist writer; Skills, routing, parallelism, external orchestrators,
  cost automation, and structured Codex integration remain future-only.
- Handoff, Approval 2, Vault, Capability Registry, recovery, escalation, and
  final evidence boundaries are explicit.

## Files changed

| Change | Path | Responsibility |
| --- | --- | --- |
| Created | `AGENTS.md` | Canonical root repository instructions. |
| Modified | `docs/development-loop/README.md` | Adds `../../AGENTS.md` to reading order as item 12. |
| Created | `docs/handoffs/dl-0-3-panam-repository-instructions.md` | This PREPARING_HANDOFF draft. |

## Tests performed

| Check | Command or method | Result | Notes |
| --- | --- | --- | --- |
| Pre-edit branch and base inspection | `git branch --show-current`; `git rev-parse HEAD` | Passed | `phase/development-loop-architecture`; `610161e45edaeec5fcec19e134e615dccfd6bbbd`. |
| Pre-edit worktree and tracking inspection | `git status --short`; `git status -sb`; `git rev-list --left-right --count HEAD...origin/phase/development-loop-architecture` | Passed | Only expected DL-0.3 implementation paths were changed; divergence was `0 0`. |
| Required-file inspection | Read-only PowerShell `Test-Path` checks | Passed | Root instructions and this approved handoff path exist. |
| Required-AGENTS-section inspection | Read-only PowerShell heading check | Passed | All required operational sections are present. |
| Markdown-link resolution | Read-only PowerShell resolution check | Passed | Links from `AGENTS.md`, the Development Loop README, and this handoff resolve. |
| Reading-order link inspection | Read-only content check | Passed | README links `[Panam Repository Instructions](../../AGENTS.md)` as item 12. |
| Instruction and architecture-boundary inspection | Read-only content check | Passed | State Machine authority, claim/evidence boundary, Approval 2, sequential v1, pending Freeze, and future-only extensions are explicit. |
| Changed-path and filesystem inspection | `git status --short` plus approved-path check | Passed | Only `AGENTS.md`, `docs/development-loop/README.md`, and this handoff are changed. |
| Whitespace and tracked-diff validation | `git diff --check`; read-only trailing-whitespace check | Passed | `git diff --check` exit `0`; no trailing whitespace in all three changed files. |

Skipped tests:

- No runtime, unit, integration, dependency, database, schema, migration,
  external-service, Git-write, or agent-execution tests apply. DL-0.3 is
  documentation/bootstrap-only and implements no runtime behavior.

### Acceptance-criteria results

- One root `AGENTS.md` exists and applies repository-wide.
- Nested instructions inherit and may only narrow higher-level rules.
- DL-0.1/DL-0.2 sources, pre-edit inspection, approved-path discipline, Git
  restrictions, State Machine authority, deterministic evidence, data boundaries,
  handoff/Vault rules, recovery stops, and pending Freeze status are explicit.
- The Development Loop README links the root instructions in canonical order.
- Only the two approved implementation paths and this approved handoff changed;
  all inspected relative links resolve; `git diff --check` passed.

## Architecture impact

- Public APIs, runtime entry points, dependencies, modules, tests, databases,
  migrations, and adapters: none created or changed.
- Repository governance: root instructions now make the accepted DL-0.1 and
  DL-0.2 authority, scope, verification, and recovery constraints operational.
- Workflow authority: Panam remains durable outer-graph owner and the State
  Machine remains sole transition authority; Codex output and Reviewer results
  remain bounded inputs, not state or safety authority.
- Data and operations: `.env`, secrets, sensitive data, runtime data,
  production data, logs, caches, local state, `Vault_work`, and `AI_Agents`
  remain outside this source-repository operation.

## New decisions

No new architecture decision is created. DL-0.3 records repository-local
operational instructions that implement the accepted DL-0.1 specification and
DL-0.2 ADR constraints without changing their substance.

## Possible reusable capability candidates

None. The instructions explicitly preserve future-only status for a Skill
Registry, outcome routing, reusable Skills, workflow composition, parallelism,
external-orchestrator adapters, cost automation, and structured Codex adapter
integration.

## Known limitations

- This is an instruction and handoff artifact, not Development Loop runtime.
- Implementation commit, handoff-finalization commit, source push, source
  verification, Vault proposal, Approval 2, and Vault lifecycle remain pending.
- `SOURCE_COMPLETED` and `COMPLETED` are not claimed.
- Git reported only its normal LF-to-CRLF working-copy warning for the tracked
  README; whitespace validation itself passed.

## Unresolved questions

No DL-0.3 blocking ambiguity remains after the approved review. Later milestones
must determine agent-role specifications, final architecture-audit evidence, and
the explicit human Architecture Freeze Gate without treating this draft as a
commit, push, Vault approval, or completion authorization.

## Evidence classification

### Verified implementation facts

- Pre-edit inspection found the required branch, base HEAD, `0 0` tracking
  divergence, and only the expected implementation paths.
- `AGENTS.md` exists at repository root and the Development Loop README contains
  the valid relative reading-order link to `../../AGENTS.md`.
- The root instructions contain the required repository scope, precedence,
  inspection, path, Git, authority, verification, data, handoff, Vault,
  recovery, reporting, and nested-inheritance rules.
- This draft is the only additional changed path; no runtime, dependency,
  SQLite, role, Vault, `AI_Agents`, `Panam_Runtime`, or Capability Registry path
  changed.
- Relative links resolve, changed-path inspection passed, and `git diff --check`
  returned exit code `0`.

### Project-owner-confirmed operational facts

- Independent repository-instructions review result: `APPROVED`.
- Focused correction attempts used: 0 of maximum 3.
- Current workflow state: `PREPARING_HANDOFF`.

The review found root scope and nested inheritance correct; precedence preserves
global safety, repository policy, contracts, approvals, and bounded tasks;
Codex has no independent Git, Pull Request, Vault, or State Machine authority;
agent summaries remain claims; deterministic verification precedes Reviewer
interpretation; unrelated changes and sensitive data are protected; sequential
v1 constraints remain intact; future extensions remain future-only; and
handoff, Approval 2, Vault, Capability Registry, recovery, reporting, and the
README reading-order boundaries are preserved.

### Proposed future behavior

Architecture Freeze is not complete. After DL-0.3 completes its complete source
and Vault lifecycle, the remaining dependency chain is:

```text
DL-0.4 missing agent role specifications
-> DL-0.5 final read-only architecture audit
-> explicit human Architecture Freeze Gate
```

The source sequence after this draft remains approved implementation commit,
handoff finalization, handoff-finalization commit, approved phase-branch push,
source verification, then possible State-Machine evaluation of
`SOURCE_COMPLETED`; the Vault lifecycle remains separately approval-bound.

### Unresolved assumptions

The actual implementation-commit and handoff-finalization-commit hashes must be
observed from Git after their respective commits exist. No approval, external
effect, or future lifecycle state may be inferred from this handoff draft.

## Repository and vault impact

| Field | Value |
| --- | --- |
| Source repository documentation changed | Yes: `AGENTS.md`, Development Loop README, and this handoff only. |
| Runtime implementation changed | No |
| Dependency files changed | No |
| SQLite schema or migration changed | No |
| Agent-role implementation changed | No |
| `AI_Agents` changed | No |
| `Panam_Runtime` changed | No |
| Vault files changed | No |
| Capability Registry changed | No |
| Staging, commit, push, Pull Request, or merge | No |
| Implementation commit | Pending |
| Handoff-finalization commit | Pending |
| Source push | Pending |
| Source milestone state | Not yet `SOURCE_COMPLETED` |
| Vault proposal / Approval 2 / Vault lifecycle | Pending / Pending / Pending |
| Architecture Freeze | Pending; not complete |

## Review checklist

- [x] DL-P0 and DL-0.3 identity, objective, and documentation-only boundary.
- [x] DL-0.1 and DL-0.2 relationship and canonical source links.
- [x] Exact created and modified paths, including this handoff draft.
- [x] Root-instruction sections, precedence, nested inheritance, Git boundary,
  State Machine authority, evidence hierarchy, data protections, sequential v1,
  handoff, Vault, Approval 2, Capability Registry, recovery, and reporting.
- [x] Independent `APPROVED` review and 0-of-3 focused-correction result.
- [x] Actual branch, base HEAD, validation commands, path/link results, and
  `git diff --check` result.
- [x] Pending commit, push, source-completion, Vault, Approval 2, and Freeze
  lifecycle fields; no completion claim.
- [x] Explicit out-of-scope confirmation: no runtime, dependency, schema,
  migration, agent-role, Vault, `AI_Agents`, `Panam_Runtime`, Capability
  Registry, stage, commit, push, Pull Request, or merge action.
