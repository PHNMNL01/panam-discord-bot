# DL-2.6 controlled local phase-branch creation

This standalone capability creates one previously absent local phase branch at
an approved existing commit. It does not complete Phase Start, activate a phase,
publish a branch, or confer Human or State Machine authority. Implementation and
its own tests remain subject to separate independent verification and acceptance.

## Entry point and trusted composition

Use `PhaseBranchCreation` from
`panam_development_loop.phase_branch_creation`, with a
`SqlitePhaseBranchOperations` store and an independently installed
`EffectAuthorityProvider`. Supply an `EffectAuthorization` and an existing,
current branch-bound DL-2.5 `LockGrant` to `create`.

The provider authenticates the exact immutable context and checks current
invalidation/revocation. Constructing a context, naming an approver, owning a
lock, or reading Git does not establish authority. Caller-controlled dependency
injection is unsupported. No production approval provider, UI, command submitter
or worker handler is installed.

`PhaseBranchIntent` references accepted phase, project-policy, typed Git-object
and lock-owner values. It binds the effect/authority identities, source and
target, command schema and state versions, command intent digest, claim, worker,
session, queue owner and exclusions. `EffectAuthorization` binds that intent,
the single allowed action, one attempt and a UTC validity interval. Its exact
canonical representation is retained. Authority is checked during preconditions,
before dispatch consumption and immediately before launch.

The registry remains authoritative for the root. The current durable phase
contract, RUNNING command, command version, claim, session freshness, queue
ownership and lock acquisition/fence/revision must match. The service does not
create missing ownership or advance a command or phase to satisfy prerequisites.

## Supported Git profile

The only effect is `CREATE_LOCAL_PHASE_BRANCH` with fixed semantic argv:

```
git branch --no-track -- <exact-target-short-name> <approved-full-commit-oid>
```

The executable is `C:\Program Files\Git\cmd\git.exe`. The adapter adds fixed
controls disabling optional locks, replacement objects, lazy fetch, transport,
hooks, helpers and automatic maintenance. It uses the registered cwd, no shell,
and an environment allowlist. There is no request-level executable, option,
environment or command override. There is no checkout, tracking setup, force,
existing-ref update, commit, staging or network operation.

Targets must be exact `refs/heads/phase/...` names. The initial profile accepts a
conservative ASCII subset, rejects unsafe Windows device/alias components and
never repairs, trims or normalizes the approved name. Git ref validation is also
required. Exact, case-fold and ancestor/descendant ref collisions prevent launch.
An existing target is a collision even at the approved OID.

The source must be the expected attached branch at the exact approved typed
commit. Detached, unborn, unresolved and mismatching sources are unsupported.
Target observations compare bounded raw loose/packed refs with successful Git
enumeration; broken, unresolved or ambiguous representations fail closed.
Symbolic targets are unsupported. Historical DL-2.4 R001 remains
`ACCEPTED_NONBLOCKING_DEFERRED`; DL-2.6 does not use failed HEAD resolution as
absence evidence and does not change the DL-2.4 profile.

The adapter reuses private DL-2.4 root/configuration/inventory and Windows Job
helpers without changing that module. Its closed effect transport and ref
observations are DL-2.6-specific. The ordinary fixed-drive, non-bare Windows
layout and conservative configuration restrictions continue to apply. Network
roots, reparse points, linked worktrees, nested repositories, submodules,
alternates, replacement refs, shallow/partial clones, split indexes and
unsupported helper/attribute configuration are rejected.

## Durable operation and migration 8

Migration 8 adds only `phase_branch_operations` and
`phase_branch_operation_events`. Migrations 1-7 are preserved. Upgrade is
coordinated and offline; mixed v7/v8 runtime compatibility is unsupported.
The v7 base is validated under the migration write lock. Migration-8 DDL,
ledger insertion and candidate validation are one transaction. Exact schema
gates include the new tables and reject altered definitions or extra triggers.

An immutable operation row binds one stable effect ID to one unique command,
the authorization, original grant and bounded pre-effect evidence. Subsequent
reservation, dispatch, outcome and terminal facts are inserted once in an ordered
event stream with revision, canonical payload and SHA-256 identity. There are no
update, delete, reset-dispatch or recovery methods. Unresolved evidence is retained.

One command cannot obtain another effect ID on a new claim or restart. Reusing
an effect ID with changed authorization or intent is rejected. The effect ID is
not the lock acquisition ID.

## Coordination and dispatch

The live sequence is:

1. Validate authority, registered identities, owner and source/target evidence.
2. Persist the new immutable operation and pre-effect evidence.
3. Reserve and commit DL-2.5 `EFFECT_RESERVED`; retain its exact binding.
4. Revalidate authority, repository stability and ownership.
5. In a short SQLite write transaction, validate current durable command/session/
   reservation state and CAS-insert the unique dispatch fact.
6. Commit and close that transaction; only its successful caller receives a
   private, process-local, single-use launch permission.
7. Revalidate launch conditions and perform at most one Git attempt.
8. Persist observed process/post-effect evidence, then terminalize the reservation.
9. Report a fully terminal result only when durable evidence and lock history agree.

Before consumption, this operation has no launch permission. After consumption,
the meaning is `GIT_EFFECT_MAY_HAVE_LAUNCHED`, including the window before any
process was created. An uncertain commit never yields a permit. A consumed permit
cannot be reissued. Predispatch abort uses its own transactional check: it rejects
consumed work, while an abort winner prevents consumption. Abort retains the lock
and evidence; it does not perform recovery or release. Post-dispatch outcome
recording is separate. No SQLite transaction spans Git execution.

The existing DL-2.5 public APIs and vocabulary are unchanged. Reservation is not
a perpetual fresh lease; current ownership still gates dispatch. Historical-owner
terminalization may account for completed work after lease expiry. Unknown work
remains blocking rather than becoming reclaimable.

## Process containment and evidence

The native Windows process is created suspended and assigned to a non-breakaway,
anonymous kill-on-close Job before resume. Inherited handles are restricted.
Incremental pipe readers cap each stream at 2 MiB. A process has a 10-second
ceiling, including cleanup reserve; each bounded observation has a 30-second
deadline. Inventory is limited by the reused 8,192-entry profile. Serialized
operation/evidence payloads are limited to 8 MiB.

Parent exit is not proof that descendants exited. Timeout, overflow and read
failure cause bounded owned-tree termination and Job accounting checks. Capture
completion and cleanup uncertainty are recorded separately from the primary
failure. Unproven quiescence prevents verified success or no-effect. Private
CPython Windows API behavior is a supported-runtime assumption, not a portable
cross-platform claim.

`VERIFIED_SUCCESS` requires the authorized consumed dispatch, trustworthy
terminal/quiescent process evidence, exact new target at the approved OID,
sufficient attribution, no forbidden observed mutation, durable outcome evidence
and matching DL-2.5 success terminalization. Exit zero or ref presence alone is
insufficient.

`PROVEN_NO_EFFECT` requires positive evidence that execution did not occur and
cannot occur later, unchanged relevant repository observations, durable evidence
and matching terminalization. The implementation deliberately uses this narrow
pre-execution proof. It does not infer no-effect from a nonzero exit or an absent
target after possible execution.

Other potentially dispatched outcomes remain unknown/reconciliation-required.
Lost process results and failed post-observations are retained when persistence
remains available. A storage or terminalization failure leaves the existing
dispatch/reservation blocking. There is no branch deletion, compensation or retry.

## Dirty workspace and drift

Dirty staged, unstaged and untracked state is permitted. The adapter reads no
worktree file contents, including excluded files. It compares names/stat metadata,
HEAD, index, configuration and relevant Git control/ref evidence. It does not
claim cleanliness or byte equality for unassessed content.

Expected change is limited to the approved target ref, optional new target reflog
and necessary parent-directory metadata. Existing controls and other inventory
entries must remain stable. Arbitrary `.git` changes are not exempted. A new
reflog must describe a single creation from the zero OID to the approved commit.

Managed writers must obey DL-2.5 coordination. Human Git, unrelated processes,
hostile change-and-restore races, executable replacement and kernel/filesystem
stalls are not physically fenced. Detected prelaunch drift prevents execution;
post-dispatch drift that defeats attribution requires reconciliation.

## Restart and historical reads

`inspect` performs read-only reconstruction; it does not finish incomplete work.
Before dispatch, authoritative evidence may establish `NOT_DISPATCHED`, but does
not grant continuation. After dispatch, incomplete evidence yields
`MAY_HAVE_DISPATCHED_RECONCILIATION_REQUIRED`.

C0-C4 retain no-dispatch evidence; C5-C8 conservatively retain possible dispatch;
C9 retains outcome evidence while terminalization is incomplete. At C10, retained
matching lock history can prove a terminal result despite a lost acknowledgement.
An exact historical success/no-effect result is returned only after fresh bounded
drift assessment. No restart path reconstructs a launch permission or writes a
recovery record. Later unrelated repository changes may make a historical replay
unavailable; this is an intentional availability tradeoff.

## Verification and deferred lifecycle

Focused verification lives in `panam_development_loop_phase_branch_creation_test`.
It uses only newly owned temporary Git repositories and SQLite databases. Native
tests exercise actual Windows processes and Job cleanup; fault injection exercises
durable C0-C10 boundaries. Relevant regressions are
`panam_development_loop_project_locks_test` and `panam_development_loop_poc_test`.
The latter requires `PANAM_DL24_TEST_WORKSPACE` to name an owned temporary test
directory. The existing R11 regression assertions also require
`PANAM_R11_ACCEPTED_EVIDENCE_PATH` to identify the accepted evidence JSON with
SHA-256 `2e1109a5fe4e0ca7ed73a73aed992421e829d4183eebfa66b3684af137a83852`.
This is a read-only historical test input, not effect authority. Do not substitute
a generated artifact or weaken those assertions. Run with Python `-B` to avoid
repository bytecode artifacts.

The development profile uses Python 3.14.5 and Git 2.54.0.windows.1 on Windows.
No runtime dependency was added. Tests do not mutate canonical Panam refs or use
network access. Test results are execution evidence, not independent acceptance.

Production worker integration, WorkerOperationReceipt Git support, phase
transitions, recovery/reconciliation execution, remote publication, final handoff,
source publication and Vault lifecycle remain separate. Initial push is not
silently assigned to DL-2.7. No general-purpose Git/subprocess runner is provided.
