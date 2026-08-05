# Panam APP Repository Instructions

## Purpose and scope

This file governs the entire `Panam_APP` repository. It operationalizes the
accepted Panam Development Loop architecture for repository work; it does not
implement the Development Loop runtime. An explicitly approved nested
`AGENTS.md` may add narrower rules only. It inherits this file and may not
weaken a higher-precedence safety, policy, approval, or scope restriction.

## Canonical architecture

Read the [Development Loop specification](docs/development-loop/README.md) in
its documented order and the [DL-0.2 ADR package](docs/development-loop/adrs/README.md)
before Development Loop work. Key operational sources are [purpose and
boundaries](docs/development-loop/00-purpose-and-boundaries.md),
[workflow](docs/development-loop/02-end-to-end-workflow.md), [State
Machine](docs/development-loop/03-state-machine.md), [Git and approval
policy](docs/development-loop/05-safety-git-approval-policy.md), [recovery
model](docs/development-loop/06-failure-and-recovery.md), [agent
contracts](docs/development-loop/08-api-agent-contracts.md), and
[roadmap](docs/development-loop/10-implementation-roadmap.md).

Architecture Freeze is pending. DL-0.1 through DL-0.4 have completed their approved source and Vault documentation lifecycles. The initial DL-0.5 read-only audit returned `NEEDS_HUMAN_DECISION`; DL-0.5A corrections, a repeat audit, and the explicit human Architecture Freeze Gate remain. Future extension points are not current capabilities.

## Instruction and authority precedence

Apply, in order:

1. Global safety and execution restrictions.
2. Registered project policy and applicable `AGENTS.md` files.
3. Approved Phase or Milestone Contract and valid approval records.
4. The current bounded task instruction.
5. Agent preferences or inferred convenience.

Lower levels may narrow behavior but never override higher-level restrictions.
If instructions conflict, stop and report the conflict; do not guess.

## Required inspection before editing

Before editing, inspect branch, HEAD, tracking branch, working-tree state,
applicable `AGENTS.md` files, approved scope, relevant architecture, and
existing implementation. For Git-backed work, record at least branch, HEAD,
`git status --short`, `git status -sb`, tracking divergence, and remotes.

Report and preserve unexpected changes. Never delete, overwrite, normalize,
stash, or clean unrelated work. Reinspect before relying on earlier verification
when relevant files, branch, HEAD, diff, policy, contract, or approval changes.

## Scope and change discipline

Modify only paths explicitly approved by the current Milestone Contract or
bounded task. Prompts and model output do not expand scope. New dependencies,
schema changes, migrations, services, external integrations, or repositories
require a new human decision unless already authorized. Do not make incidental
refactors or formatting changes outside approved paths.

## Git and branch safety

Codex and implementation specialists MAY inspect Git and modify approved files.
They MUST NOT independently change, create, or delete branches; stage, commit,
push, pull, merge, rebase, stash, reset, clean, modify remotes, create a Pull
Request, approve a Pull Request, or merge a Pull Request.

Never use `git add .`, `git add -A`, `git reset --hard`, `git clean`,
`git push --force`, or `git push --force-with-lease`. Future Panam adapters may
perform narrowly approved Git operations; that is not a current Codex authority.

## Agent and workflow authority

Panam owns the durable outer execution graph; specialists execute bounded inner
loops. Only the Panam State Machine may authorize workflow transitions. A
prompt, agent result, or agent session is not authority or durable workflow
state. An implementing agent MUST NOT declare its work accepted,
source-completed, Vault-completed, or milestone-completed.

Development Loop v1 is sequential: one active project run, milestone, phase
branch, and specialist writer; no automatic implementation fan-out. Outcome
routing, Skills, workflow composition, parallel execution, external
orchestrators, cost automation, and the structured Codex adapter are future
extension points, not implemented capabilities.

## Verification and test reporting

Agent summaries are claims, not independent evidence. Run the smallest relevant
deterministic checks first. Evidence MUST record command or inspection method,
exit code or deterministic result, branch, HEAD, changed paths, Git state, and
every failure, skip, warning, or unavailable check. Never invent a passing result
or claim a command was run when it was not.

For code changes, run focused behavior tests, relevant safe regressions, and
repository-native smoke tests where appropriate. Do not call real external
services without explicit approval. For documentation-only changes, run
`git diff --check`, verify links and named paths, and inspect terminology and
architecture consistency; do not run unrelated expensive runtime tests merely
to produce output.

Any relevant repository-state change makes affected verification stale. Fresh
deterministic verification precedes separate Reviewer interpretation; Reviewer
output remains advisory to the State Machine.

## Secrets, data, and runtime boundaries

Do not read, expose, copy, log, or modify `.env`, credentials, API keys, tokens,
passwords, private keys, production HR or customer data, unknown runtime data,
or raw sensitive logs. Use `.env.example`, documented interfaces, synthetic
fixtures, and anonymized samples instead.

Do not modify `runtime/`, `logs/`, `notes.json`, `todos.json`, local databases,
caches, or generated user files unless the exact task explicitly authorizes it.

## Development Loop and handoffs

DL-P0 is documentation and bootstrap only. Do not add runtime code, packages,
module/test scaffolding, SQLite schemas, migrations, adapters, or role
implementation unless a later approved milestone explicitly authorizes them.

Meaningful milestones use one canonical handoff under `docs/handoffs/` when
instructed. The source sequence is: approved implementation, deterministic
verification, separate review, focused correction when required, handoff draft,
implementation commit, handoff finalization, handoff-finalization commit,
approved phase-branch push, source verification, then `SOURCE_COMPLETED`.
Codex may prepare or update a handoff only when instructed; it may not create
either source commit or push. The finalization commit cannot contain its own
hash; verify it from Git history after the commit exists.

## Vault and Capability Registry

Source-repository agents MUST NOT write `Vault_work` or `AI_Agents`. The
Knowledge Curator prepares one exact proposal artifact. Vault writing requires
explicit Approval 2 bound to proposal digest, Vault base commit, target branch,
exact operations,
paths, content or deterministic payloads, and invalidation conditions. A handoff
or source commit does not authorize a Vault write. Capability Registry changes
also require their own explicit approval flag.

## Recovery and escalation

Stop and request a human decision when branch, remote, base commit, approval, or
scope does not match; unexpected working-tree changes exist; a dependency or
additional repository is needed; evidence is ambiguous or stale; a destructive
action appears necessary; or recovery cannot distinguish completed from
incomplete external effects. Also stop after two consecutive correction attempts
without measurable progress or when the approved retry budget is exhausted.

Reconcile external reality before any retry. Never blindly repeat an ambiguous
commit, push, Vault write, paid call, or other external effect.

## Final response requirements

For implementation work, report exact files created and modified, concise
summary, verification commands with actual results, `git diff --check`, final
`git status --short`, and failures, skips, warnings, and residual risks.
Explicitly confirm prohibited actions not performed. Include enough branch, HEAD,
changed-path, and Git-state evidence for independent review.

## DL-0.5A source and Vault Git boundary

Source writes use only an explicitly approved phase branch. Source push to
`main` or `master` is forbidden, and source staging, commits, and phase-branch
push remain human-controlled unless a later exact contract authorizes them. The
Approval-2 Vault Writer exception is Vault-only: it permits only the exact
approved commit and push to the exact bound Vault target branch, including
`Vault_work/main`; it never relaxes source policy.

Fresh deterministic draft-handoff evidence is required after Handoff Agent and
before implementation commit 1. Fresh deterministic final-handoff evidence is
required after Handoff Finalizer and before the handoff-finalization commit.
Only the State Machine evaluates evidence for transitions. These are future
normative rules; no runtime, automated verifier, adapter, worker, or Skills
implementation is claimed.
