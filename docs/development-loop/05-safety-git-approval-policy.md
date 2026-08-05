# Safety, Git, and Approval Policy

## Document purpose

Define repository trust, approval scope, subprocess restrictions, and Git boundaries.

## In-scope responsibilities

### Project trust and path policy

The Project Registry is the root of repository trust. User prompts and AI responses cannot introduce arbitrary repositories or host paths. The prototype rejects unregistered roots, traversal, escapes from registered roots, UNC/DFS paths, symlink or junction escapes, remote mismatches, protected branches, unexpected dirty trees, and writes to `.env`, credentials, private keys, `.git`, runtime secrets, or configured forbidden paths.

Global deny rules override project policy; project policy overrides a Milestone Contract request.

### Approval gates

| Gate | Permitted actions |
|---|---|
| Phase Start Approval | Create and initially push the approved phase branch. |
| Approval 1 | Codex implementation, approved verification, up to three focused fixes, implementation commit, handoff-finalization commit, and push of both to the approved phase branch. |
| Approval 2 | One exact Vault proposal, only named Vault paths, one Vault commit, and one Vault push. |

Capability Registry modification additionally requires an explicit approval flag.

### Git policy

Read-only Git operations may include status, diff, diff --check, branch, rev-parse, log, remote inspection, and ls-remote. Source Git writes may create the approved phase branch, stage explicit approved files, commit, and push only to that approved phase branch; source main/master are forbidden. Vault Writer is the narrow Approval-2 exception only: it may make the approved commit and push to the exact bound Vault target branch, including Vault_work/main.

Globally forbidden in every repository:

- `git add .`
- force push
- force-with-lease
- automatic merge
- automatic rebase
- reset --hard
- git clean
- automatic stash
- branch deletion
- automatic pull
- remote URL modification

Additionally forbidden for source repositories:

- push to `main` or `master`

Vault Writer remains allowed only under exact valid Approval 2 to commit and
push to the exact bound Vault target branch, including `Vault_work/main`. This
Vault-only exception does not permit force push, broad staging, automatic pull,
remote changes, or any source-repository `main`/`master` push.

Before each write, revalidate branch, remote, base commit, approval, changed paths, and evidence digest.

### Subprocess policy

All execution uses structured requests: `shell=False`, explicit executable and argument list, registered working directory, environment allowlist, timeout, bounded stdout/stderr, process-tree termination, and audit records. Free-form shell, dynamic PowerShell, and dynamic cmd text are forbidden.

## Approved decisions

Codex may inspect the registered project, modify approved paths, add approved tests, and execute approved verification. It cannot commit, push, merge, rebase, alter phase branches, alter supporting repositories, expand scope, install dependencies without a new human decision, or treat its own summary as verification evidence.

## Explicit boundaries and out of scope

No agent is a security authority. This policy does not authorize current repository changes beyond the approved run and does not automate a PR merge.

## Cross-references

- [Workflow](02-end-to-end-workflow.md)
- [Core data model](04-core-data-model.md)
- [Failure and recovery](06-failure-and-recovery.md)

## Future considerations

Project-specific protected-branch patterns, command registries, and forbidden-path lists require durable registry implementation.

### Handoff verification before source commits

Approval 1 binds both handoff-verification procedures. Fresh draft `PASSED`
evidence is required before human implementation commit 1; fresh final `PASSED`
evidence is required before human handoff-finalization commit 2. Scope, handoff,
Git, review/evidence, approval, commit, or branch changes invalidate the result.
Codex has no staging, commit, push, or transition authority.
