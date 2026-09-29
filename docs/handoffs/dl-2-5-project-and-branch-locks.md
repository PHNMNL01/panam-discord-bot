# DL-2.5 Project and Branch Locks

## 1. Milestone Identity

- Phase: DL-P2 - Phase Lifecycle, Command Queue and Worker.
- Milestone: DL-2.5.
- Canonical title: Project and Branch Locks.
- Lifecycle state at handoff creation: `IMPLEMENTATION_ACCEPTED_AND_SOURCE_PUBLISHED`.
- Milestone completion: `NOT_YET_VAULT_BACKED_COMPLETED`.

This is the canonical milestone record of the accepted implementation and its
published source. It does not declare Vault-backed completion or authorize the
next milestone.

## 2. Accepted Source Identity

| Binding | Accepted value |
|---|---|
| Implementation commit | `b61a4b516374ff3a26f83dcdb3e93b7b1c0ff5c5` |
| Implementation parent | `823ecbc60ab39d97bb66a79749057e236ba91b58` |
| Implementation tree | `16e5c4590b2c49580983fcb0d00b4aa556c5054e` |
| Branch | `phase/panam-dl-p2-phase-lifecycle-command-queue-worker` |
| Remote repository | `origin`: `https://github.com/PHNMNL01/panam-discord-bot.git` |
| Remote implementation publication | `PUBLISHED_AND_VERIFIED` |

Implementation publication was verified by
`RE-DL-P2-DL-2.5-EXACT-COMMIT-REMOTE-PUBLICATION-001`. Before this handoff was
created, a fresh exact-ref remote query again matched the implementation commit.
The source identities above describe the implementation, not this later
handoff-only commit.

## 3. Implemented Capability

The accepted standalone service/storage capability provides:

- One active write acquisition per registered project/repository, with
  subordinate branch binding.
- Exact registered project identity and registered-root binding, including
  conservative Windows repository/root alias and branch-collision protection.
- Ownership bound to worker, session, queue owner, command, command claim and
  acquisition identity.
- Monotonic per-project fencing and transactional/CAS acquisition, renewal and
  release.
- Explicit UTC lease semantics, no automatic stale/expired-owner takeover,
  durable `RECONCILIATION_REQUIRED` handling, and read-only current-ownership
  observation.
- A non-expiring `EFFECT_RESERVED` coordination state, explicit effect
  terminalization, and append-only coordination provenance.
- SQLite migration 7. Worker runtime and Git external-effect integration remain
  deferred.

The public entry point is
[`ProjectLockService`](../../panam_development_loop/project_locks.py), backed by
[`SqliteProjectLockRepository`](../../panam_development_loop/sqlite_repositories.py).
Operations include acquire, renew, release, check_current_ownership, observe,
reserve_effect_window and terminalize_effect_reservation. Terminal evidence must
be independently verified by a trusted consumer; this service records it rather
than verifying external work. It exposes no operation to clear
reconciliation-required ownership.

## 4. Persistence

[Migration 7](../../panam_development_loop/sqlite_migrations.py) adds:

| Table | Responsibility |
|---|---|
| `project_locks` | Project/root binding, ownership, lease/state, fencing high-water and revision, reservation and terminal evidence. |
| `project_branch_locks` | Subordinate branch/conflict key bound to the project acquisition, fence and revision. |
| `project_lock_events` | Ordered acquisition and coordination history with ownership snapshots and provenance. |

Historical migrations 1-6 are preserved. Migration 7 is additive at the accepted
baseline and requires a coordinated offline v6 -> v7 upgrade. Mixed-version
v6/v7 runtime compatibility is not provided. Malformed migration history,
unsupported future versions and unexpected schema shapes remain fail closed.

## 5. Critical Safety Invariants

**NO_DELETION_OF_STALE_WORK**

- A lock is coordination evidence, not approval, and grants no State Machine
  transition authority.
- Expiry proves neither that prior work disappeared nor that prior external
  effects did not occur. Expired unresolved ownership remains blocking;
  automatic stale takeover is prohibited in v1.
- `EFFECT_RESERVED` does not become reclaimable merely because ordinary lease
  time passes. Its retained reservation is not proof that the owner remains
  healthy or fresh.
- Unknown or ambiguous effect completion becomes or remains
  `RECONCILIATION_REQUIRED`.
- No Git external effect occurs inside the DL-2.5 capability. Its transactions
  are short storage operations; SQLite transactions must not remain open across
  future external effects.

These boundaries preserve the
[canonical architecture](../development-loop/README.md) and
[recovery model](../development-loop/06-failure-and-recovery.md).

## 6. Verification

The following are accepted execution results, not new test runs performed by
this documentation-only handoff lifecycle.

| Verification | Accepted result |
|---|---|
| `python -B -m unittest -q panam_development_loop_project_locks_test` | 56 PASS; 0 FAIL; 0 ERROR; 0 SKIP; exit 0 |
| `python -B -m unittest -q panam_development_loop_poc_test` | 334 PASS; 0 FAIL; 0 ERROR; 0 SKIP; exit 0 |
| Original independent review, `RE-DL-P2-DL-2.5-INDEPENDENT-REVIEW-001` | `VERIFIED` |
| Final source verification, `git diff --check` | PASS; exit 0 |

Both suites passed again during
`RE-DL-P2-DL-2.5-LOCAL-SOURCE-FREEZE-COMMIT-002`.

DL-2.5 original finding R001 (nullable required terminal outcome) is
`RESOLVED_BY_AUTHORIZED_HARDENING_AND_INDEPENDENTLY_REVERIFIED`.
Migration-7 terminal states explicitly require a non-NULL permitted outcome.
Independent raw SQLite insert/update probes exercised 160 cases: 28 accepted,
132 rejected, and zero unexpected outcomes.

DL-2.5 original finding R002 (race tests lacked guaranteed transaction overlap)
is `RESOLVED_BY_AUTHORIZED_TEST_HARDENING_AND_INDEPENDENTLY_REVERIFIED`.
The permanent test holds a real writer after successful BEGIN IMMEDIATE.
A separate real-SQLite probe observed A begin successfully, B receive
SQLITE_BUSY/TRANSIENT_CONTENTION while A remained held, and A commit. Only one
ownership generation, matching branch binding and acquisition event committed.

Focused re-review `RE-DL-P2-DL-2.5-FOCUSED-INDEPENDENT-RE-REVIEW-001` retained the
overall result **INCONCLUSIVE**. Current R001 behavioral verification was PASS;
current R002 behavioral verification was PASS. The sole unresolved review
evidence limitation, RR001, was unavailable exact independent historical
pre-hardening comparison evidence at the uncommitted intermediate boundary.

Human disposition of RR001:
`ACCEPTED_EVIDENCE_LIMITATION_NO_IMPLEMENTATION_DEFECT_ESTABLISHED`, under
`HAD-DL-P2-DL-2.5-FINAL-IMPLEMENTATION-ACCEPTANCE-001`.
The Human accepted the implementation without requiring reconstruction of that
historical snapshot. No independent byte-identical pre/post-hardening snapshot
is claimed, and the focused re-review is not relabeled VERIFIED.

Migration verification also covered fresh initialization, v6 upgrade and data
preservation, repeated initialization, rollback, malformed/future history
rejection, and intended exact-v7 schema acceptance.

## 7. Source Freeze / EOL Reconciliation

Human-accepted source initially existed as working-tree raw bytes, with
`core.autocrlf=true` present. The first source-freeze execution stopped before
staging because its raw-byte/index-byte equality rule conflicted with configured
Git EOL conversion; it created no commit and consumed no commit budget.

`HAD-DL-P2-DL-2.5-SOURCE-FREEZE-EOL-RECONCILIATION-001` retained the raw input
freeze and authorized the canonical Git representation. During replacement
execution, raw working-tree identities remained unchanged. Git path-aware
clean/EOL hashing predicted the index blobs; all staged IDs and all committed
IDs matched those predictions. Aggregate committed-blob verification:
**9/9 MATCH**. The authoritative `git diff --cached --check` passed.

This reconciled accepted working-tree byte identity with canonical Git blob
identity without rewriting accepted source or changing repository configuration.

## 8. Known Deferred Items

Historical **DL-2.4 R001** remains `ACCEPTED_NONBLOCKING_DEFERRED`; no repair was
performed. It is distinct from the resolved DL-2.5 R001. See the
[DL-2.4 handoff](dl-2-4-read-only-git-inspector.md).

Worker runtime integration is deferred. Controlled branch creation remains
DL-2.6. Reconciliation execution is not implemented by DL-2.5, and the capability
does not perform Git branch mutation or grant Git mutation authority. Lock
possession remains separate from Human approval. No DL-2.6 authority is inherited.

## 9. DL-2.6 Readiness

A later authorized consumer can use these primitives to establish registered
project/resource binding, exclusive current project ownership, subordinate
target-branch binding, current fencing identity, current lease/freshness and
ownership observation. It can reserve `EFFECT_RESERVED` before a future
external effect and enforce fail-closed behavior after ownership loss or
reconciliation-required behavior after an ambiguous effect outcome.

The consumer still needs its own approved contract, effect authority, integration
and external-evidence verification. **DL-2.6 is NOT implemented by this milestone.**

## 10. Governance and Authority Boundary

Human authority and execution are separate. Evidence, locks and Git state are
not approval. State Machine transition authority remains separate; this handoff
does not assign runtime workflow state.

The implementation, original independent review, post-review hardening, focused
re-review, source-freeze commit and implementation remote-publication budgets
are each **1/1 CONSUMED, 0 REMAINING**. No autonomous execution authority transfers
into DL-2.6.

Governing records:

- Milestone Contract: `HAD-DL-P2-DL-2.5-MILESTONE-CONTRACT-DECISION-001`.
- Final implementation acceptance:
  `HAD-DL-P2-DL-2.5-FINAL-IMPLEMENTATION-ACCEPTANCE-001`.
- Source-freeze commit authority:
  `HAD-DL-P2-DL-2.5-LOCAL-SOURCE-FREEZE-COMMIT-AUTHORIZATION-001`, amended by
  `HAD-DL-P2-DL-2.5-SOURCE-FREEZE-EOL-RECONCILIATION-001`.
- Implementation publication authority:
  `HAD-DL-P2-DL-2.5-EXACT-COMMIT-REMOTE-PUBLICATION-AUTHORIZATION-001`.
- This handoff lifecycle authority:
  `HAD-DL-P2-DL-2.5-CANONICAL-HANDOFF-LIFECYCLE-AUTHORIZATION-001`.
- This handoff execution:
  `RE-DL-P2-DL-2.5-CANONICAL-HANDOFF-LIFECYCLE-001`; its one lifecycle budget
  becomes **1/1 CONSUMED, 0 REMAINING** at the first persistent handoff write.

The bounded Human authority permits this handoff-only commit and its exact phase
publication; it does not create general Git, Vault or next-milestone authority.

## 11. Excluded Workspace State

`docs/PANAM-ARCHITECTURE-WATCHLIST.md`

`UNTRACKED / EXCLUDED / DO_NOT_READ`

## 12. Next Lifecycle Step

Vault lifecycle recording remains required before DL-2.5 may be treated as
`VAULT_BACKED_COMPLETED`. That state is not established by this handoff.

No Vault write authority is created by this handoff. No DL-2.6 execution
authority is created by this handoff. The next step requires a separate Human
Vault lifecycle authority.
