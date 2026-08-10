# DL-P1.5 SQLite Repositories — Pre-Commit Handoff

## Status and authority boundary

**DRAFT — PRE-COMMIT HANDOFF**

This is the provenance handoff for the verified and Reviewer-approved DL-P1.5
implementation candidate. The candidate and this draft are intentionally
uncommitted and unstaged. This artifact is not an implementation commit, source
push, `SOURCE_COMPLETED` decision, Vault action, or milestone completion record.

| Lifecycle field | Current fact |
|---|---|
| Implementation commit | **PENDING** |
| Handoff-finalization commit | **PENDING** |
| Source push | **PENDING** |
| Source synchronization verification | **PENDING** |
| Source lifecycle | **NOT YET SOURCE_COMPLETED** |
| Vault proposal | **PENDING** |
| Approval 2 | **PENDING** |
| Vault write | **PENDING** |
| Vault commit/push | **PENDING** |
| Vault lifecycle | **PENDING** |
| Overall milestone | **NOT COMPLETED** |

Handoff draft authority is `HAD-DL-P1.5-HANDOFF-DRAFT-001`. It authorizes
creation of exactly this file and grants no Git write, source-completion, Vault,
Approval 2, milestone-completion, or lifecycle-transition authority.

## Milestone identity and objective

- Milestone: `DL-P1.5 SQLite Repositories`.
- Canonical roadmap identity: `DL-1.5 SQLite Repositories`.
- Phase: `DL-P1 Durable Domain and Persistence Foundation`.
- Objective: persist the currently modeled `PhaseContract`,
  `MilestoneContract`, and `ApprovalBinding` foundation entities through typed
  repositories while preserving existing DevelopmentRun and accepted
  state-event persistence.
- Architecture classification: `IMPLEMENTATION_WITHIN_FROZEN_ARCHITECTURE`.
- Controlling Architecture Freeze authority:
  `HAD-ARCHITECTURE-FREEZE-RECONCILIATION-001`.

No new architecture decision was introduced by DL-P1.5.

## Source baseline and candidate binding

- Repository: `C:\Panam_APP`.
- Branch: `phase/panam-dl-p1-1-durable-state-transition-kernel-poc`.
- Baseline and current HEAD: `b6b18a26b7b188ee6401b4ab012e632c69fd4cfe`.
- Upstream: `origin/phase/panam-dl-p1-1-durable-state-transition-kernel-poc`.
- Live remote HEAD at handoff preflight:
  `b6b18a26b7b188ee6401b4ab012e632c69fd4cfe`.
- Divergence: `0 ahead / 0 behind`.
- Remote: `https://github.com/PHNMNL01/panam-discord-bot.git`.
- Staged paths: none.

The implementation candidate aggregate SHA-256 is
`8A981D6B9A1602DE61ECD2D350D38E58D33E3F549B573D52FD446C7E8C54A07D`.
It is SHA-256 over lexically sorted UTF-8 manifest lines in the form
`relative-path<TAB>byte-size<TAB>SHA-256<LF>`.

| Candidate path | Change | Bytes | SHA-256 |
|---|---|---:|---|
| `panam_development_loop/__init__.py` | UPDATE | 1,790 | `7D2B328F441B364B2211608E4FA786B639474586C7C2F878356E7C7F123C4062` |
| `panam_development_loop/repositories.py` | CREATE | 2,231 | `040D6FCDC0E66E495C157C9DBC8D36999828EC97B88591E679DAF76827DD57C7` |
| `panam_development_loop/sqlite_migrations.py` | UPDATE | 11,387 | `BFD51E8D6172C650A6762CEC8BB4FB1DBFC09A9894366C0AE0C1805535111329` |
| `panam_development_loop/sqlite_repositories.py` | CREATE | 17,615 | `D5000DC2A0499F40D5E4FB4AEE3E6102EA177A20F75B778972FA2DFE686B0D04` |
| `panam_development_loop_poc_test.py` | UPDATE | 58,585 | `B39B77E00E6A9C05A0B662185100CE24AD991319B1A7E696EEB3E3A4E4493358` |

After this draft was created, the expected and authorized dirty set became
exactly these five implementation paths plus
`docs/handoffs/dl-p1-5-sqlite-repositories.md`. The two repository modules and
this handoff are untracked; no path is staged.

## Contract, feasibility, and Approval 1

### Corrected contract

- Path:
  `C:\Panam_Runtime\development-runs\dl-p1-5\planning\MILESTONE-CONTRACT-DRAFT.md`.
- Status: `DRAFT — NOT APPROVED`.
- Bytes: `52,296`.
- SHA-256:
  `F8688F0F6E4574C44C38182417CA2662E6C8C2D9AFFB29E2D26E5658B6EC2554`.
- Correction authority: `HAD-DL-P1.5-PLANNER-CORRECTION-001`.
- Correction result: `CORRECTED`.

The contract status itself remained draft. Human Approval 1 separately approved
implementation against these exact bytes.

### Historical first Feasibility Assessment

- Path:
  `C:\Panam_Runtime\development-runs\dl-p1-5\feasibility\FEASIBILITY-ASSESSMENT.md`.
- Bytes: `28,547`.
- SHA-256:
  `E54BC843CFB281271E61AA6C8C59989651654153932D4AFF32B7388483CC24FC`.
- Verdict: `INFEASIBLE`.
- Readiness: `READY_FOR_APPROVAL_1: false`.
- Finding: `F-P1.5-001 — EXACT MIGRATION 2 REJECTED BY CURRENT REGISTRY VALIDATOR`.

This failed assessment is immutable historical evidence. The original contract
could not register its exact migration 2 because the then-current validator
split `base_commit` and treated `COMMIT` as transaction control. DL-P1.5 did not
proceed as uninterrupted success.

### Planner correction and fresh Feasibility

The original contract was 43,791 bytes with SHA-256
`25623396011810BDFFEF537EA963634509654D6DEC8B7495E98612BC11929348`.
Under `HAD-DL-P1.5-PLANNER-CORRECTION-001`, the Planner made a bounded contract
correction defining the identifier-token boundary and direct regression tests
while preserving the three-table schema, five-path surface, and migration
safety rules.

Fresh Feasibility evidence:

- Path:
  `C:\Panam_Runtime\development-runs\dl-p1-5\feasibility\FEASIBILITY-ASSESSMENT-2.md`.
- Bytes: `24,900`.
- SHA-256:
  `64E3E506CBABDE2EE1A136F95A8DB4A25F6456F167FF66BA9338931AD97EE6D2`.
- Verdict: `FEASIBLE_WITH_NON_BLOCKING_FINDINGS`.
- Readiness: `READY_FOR_APPROVAL_1: true`.
- Finding status: `F-P1.5-001 = RESOLVED_AT_CONTRACT_LEVEL`.

### Approval 1

- Authority: `HA1-DL-P1.5-SQLITE-REPOSITORIES-001`.
- Status: `APPROVED_FOR_IMPLEMENTATION`.
- Approved base:
  `b6b18a26b7b188ee6401b4ab012e632c69fd4cfe`.
- Approved implementation surface: exactly the five candidate paths above.
- `PRODUCTION_MIGRATION_2_REQUIRED: true`.
- Approved application tables: `phases`, `milestone_contracts`, `approvals`.
- Model changes: forbidden.
- Existing `SqliteRunStore` changes: forbidden.
- Architecture changes: not authorized.

## Implementation result

- Primary implementation attempt: `1/1`.
- Result: `IMPLEMENTED`.
- Focused implementation corrections consumed: `0/3`.
- Focused correction budget remaining: `3/3`.
- No implementation subagent was used.

The earlier Planner correction was a planning-contract correction and did not
consume the implementation correction budget. The primary implementation
completed without a focused implementation correction.

## Repository architecture and public API

DL-P1.5 adds the transport-independent ports:

- `PhaseContractRepository`;
- `MilestoneContractRepository`;
- `ApprovalBindingRepository`.

Each port exposes only bounded domain-facing insert-only `create` and
stable-identity `get` operations. The ports return domain values or `None` for a
missing lookup and expose no SQLite implementation type.

The concrete adapters are:

- `SqlitePhaseContractRepository`;
- `SqliteMilestoneContractRepository`;
- `SqliteApprovalBindingRepository`.

Each logical operation owns one connection and closes it deterministically.
Repository connections enable `PRAGMA foreign_keys = ON`; writes use
`BEGIN IMMEDIATE`, commit only on success, and roll back on failure. The
adapters provide insert-only persistence and bounded error translation without
exposing `sqlite3.Connection`, `sqlite3.Row`, SQL text, or native SQLite errors
through the public repository boundary. No Unit of Work or cross-repository
transaction framework was introduced.

The public failure contract is one `RepositoryError` carrying one of exactly:

- `INVALID_IDENTITY`;
- `DUPLICATE_ENTITY`;
- `RELATIONSHIP_VIOLATION`;
- `MALFORMED_PERSISTED_PAYLOAD`;
- `UNSUPPORTED_PERSISTED_VERSION`;
- `SQLITE_OPERATIONAL_FAILURE`;
- `SCHEMA_MISMATCH`.

## Persistence semantics

- `PhaseContract` identity is `(project_id, phase_id)`.
- `MilestoneContract` identity is
  `(project_id, phase_id, milestone_id)`.
- `ApprovalBinding` identity is `approval_id`.
- Writes are immutable and insert-only; duplicate identity or digest conflicts
  fail deterministically rather than replacing stored records.
- Missing lookups return `None` and do not create or repair a database.
- Structured tuple fields use deterministic compact JSON arrays with
  non-ASCII-safe round trips.
- Reconstructed domain values are validated and checked against their stored
  SHA-256 digests.
- Malformed persisted payloads and unsupported persisted model versions fail
  closed under distinct bounded error codes.
- Schema, relationship, locking, and operational failures are translated into
  bounded repository semantics while retaining an underlying cause when one
  exists.

The approved relationship is:

```text
milestone_contracts(project_id, phase_id)
    -> phases(project_id, phase_id)
```

It is a composite foreign key enforced on repository connections. A milestone
write without its parent phase fails deterministically. This relationship is
not milestone sequencing logic.

No update, upsert, replace, delete, revoke, list, workflow-execution, or
live-authority-validation API was added.

## Production migration 2

- Production migration version: `2`.
- Production registry: `[1, 2]`.
- Exact new application tables: `phases`, `milestone_contracts`, `approvals`.
- Migration 1: unchanged.
- Fresh database: applies migration 1 and then migration 2.
- Valid existing version-1 database: applies migration 2 only; migration 1 is
  not replayed.
- Existing DevelopmentRun/state-event rows and predecessor history are
  preserved.
- Migration 2 and its version-2 ledger insertion share one transaction.
- A failed migration 2 rolls back its schema work and ledger insertion.
- An already-current version-2 database is idempotent.
- The active migration path does not use `executescript()`.

No migration 3 behavior or fourth application table was implemented.

## Validator correction for F-P1.5-001

The pre-correction validator extracted alphabetic substrings, so the legitimate
identifier `base_commit` contributed the standalone token `COMMIT` and caused
the exact production migration 2 to fail registry validation.

The implementation makes the approved bounded refinement: it recognizes
ordinary identifier/keyword tokens using a whole-token boundary sufficient to
accept `base_commit`, `commit_hash`, and `rollback_reason`. Actual complete
forbidden operation tokens remain rejected, including `BEGIN`, `COMMIT`,
`ROLLBACK`, `SAVEPOINT`, `RELEASE`, `VACUUM`, `ATTACH`, and `DETACH` under
tested case and ordinary whitespace variations.

This is a local false-positive correction, not a general SQL parser or a broad
migration-policy redesign.

## Preserved predecessor behavior

The following production paths are unchanged:

- `panam_development_loop/models.py`;
- `panam_development_loop/sqlite_store.py`;
- `panam_development_loop/transition_service.py`;
- `panam_development_loop/transition_policy.py`.

Existing DevelopmentRun persistence, accepted state-event persistence, event
ordering, accepted transition behavior, and migration-1 behavior remain
preserved. No DL-P1.6 broader State Machine behavior was implemented.

## Deterministic verification evidence

### Implementer self-check

| Check | Command | Result |
|---|---|---|
| Focused P1.5 tests | `python -B -m unittest panam_development_loop_poc_test.SqliteMigrationTest panam_development_loop_poc_test.SqliteRepositoryTest panam_development_loop_poc_test.SqliteMigrationTwoTest` | `28/28 passed` |
| Full P1.1–P1.5 regression | `python -B panam_development_loop_poc_test.py` | `45/45 passed` |
| Patch hygiene | `git diff --check` | exit `0` |

The Implementer reported no repository SQLite, WAL, SHM, bytecode, cache, or
test-output residue.

### Independent Verifier

- Result: `PASSED`.
- Timestamp: `2026-08-10T12:12:41.2599084+02:00`.
- Bound candidate aggregate:
  `8A981D6B9A1602DE61ECD2D350D38E58D33E3F549B573D52FD446C7E8C54A07D`.
- Material findings: none.
- Focused tests: `28/28 passed`.
- Full regression: `45/45 passed`.
- `git diff --check`: exit `0`.
- Repository test residue: none.

This is deterministic independent evidence and is distinct from the
Implementer's self-check.

## Advisory Reviewer evidence

- Reviewer authority: `HAD-DL-P1.5-REVIEWER-001`.
- Result: `APPROVED`.
- Timestamp: `2026-08-10T12:33:41.1125734+02:00`.
- Material findings: none.
- Focused correction recommended: no.
- Correction budget after review: `3/3 remaining`.

The Reviewer result is advisory interpretation, not deterministic verification
authority and not a State Machine transition.

## Dependencies, safety, and architecture boundary

- Standard library only.
- Persistence uses `sqlite3`.
- No SQLAlchemy, Alembic, ORM, third-party SQL parser, repository framework, or
  new dependency was added.
- No product Git capability was introduced.
- No architecture change was introduced; the ports, SQLite adapters, migration
  2, and bounded validator correction instantiate the frozen architecture.

## Explicitly deferred and out of scope

DL-P1.5 did not implement:

- DL-P1.6 broader State Machine v1;
- DL-P1.7 expanded transactional transition service;
- DL-P1.8 Project Registry;
- DL-P1.9 Foundation Query CLI;
- DL-P1.10 broader foundation integration;
- Development Worker or long-running worker;
- command queue;
- Git adapter or Git product operations;
- Vault adapter or Vault lifecycle;
- external agent execution or Codex adapter;
- Web/Discord workflow orchestration.

Schema repair/fingerprinting, downgrade, deletion, generalized concurrency,
caller-owned connections, cross-repository transactions, and a Unit of Work
also remain deferred.

## Environment observations

The following observations are non-blocking environment evidence, not source
defects:

- ordinary sandbox execution encountered the known Windows
  `TemporaryDirectory` access restriction;
- a permitted equivalent execution passed;
- a sandboxed live-remote query could be blocked by network policy while the
  permitted read-only query succeeded;
- configured LF/CRLF working-copy notices occurred while
  `git diff --check` remained clean.

The Vault repository has a pre-existing unrelated modified status entry at
`.obsidian/workspace.json`. It is not a DL-P1.5 source modification and was not
read, modified, reset, cleaned, staged, or otherwise reconciled by this role.
DL-P1.5 Vault work has not begun.

## Repository and Vault impact

| Field | Current fact |
|---|---|
| Source implementation paths changed | Exactly the five bound candidate paths |
| Handoff path created | `docs/handoffs/dl-p1-5-sqlite-repositories.md` |
| AI_Agents files changed by this role | None |
| Runtime artifacts changed by this role | None |
| Vault files changed by this role | None |
| Capability Registry changed | No |
| Git durable state changed by this role | No |

No reusable capability classification is made by this handoff. Any future
Capability Registry or Vault proposal requires its separate evidence and human
approval lifecycle.

## Current Git state and future commit 1

At draft creation, source HEAD remains
`b6b18a26b7b188ee6401b4ab012e632c69fd4cfe`, divergence remains
`0 ahead / 0 behind`, and staged paths remain empty. The dirty set is exactly:

1. `panam_development_loop/__init__.py` — UPDATE;
2. `panam_development_loop/repositories.py` — CREATE;
3. `panam_development_loop/sqlite_migrations.py` — UPDATE;
4. `panam_development_loop/sqlite_repositories.py` — CREATE;
5. `panam_development_loop_poc_test.py` — UPDATE;
6. `docs/handoffs/dl-p1-5-sqlite-repositories.md` — CREATE.

Only as an expected future lifecycle step, after independent draft-handoff
verification and separate Human Git Authority, implementation commit 1 is
expected to contain those exact six paths. No staging or commit is authorized
or claimed here, and no commit SHA is invented.

## Known limitations and unresolved questions

The deferred capabilities above remain unsupported. No unresolved
implementation finding or blocking handoff question is known. Older repository
text that says Architecture Freeze is pending is known stale documentation
drift; the controlling human authority establishes `ARCHITECTURE_FROZEN`, and
DL-P1.5 does not alter that documentation.

## Next safe gate and freshness

The next safe gate is a fresh independent **Verifier in draft-handoff
verification mode**. It must bind this draft's exact on-disk bytes and detached
SHA-256, the five-file candidate aggregate and individual digests, branch,
unchanged HEAD, live remote, exact six-path dirty set, empty staging state,
Verifier/Reviewer chronology, correction count, and all pending lifecycle
fields. Only the State Machine may consume a passing result.

This handoff and its evidence are invalidated by any relevant change to the
contract, either Feasibility artifact, Approval 1, handoff authority,
Architecture Freeze authority, governing instructions, repository identity,
branch, HEAD, upstream/live remote, divergence, candidate paths or bytes, this
handoff's bytes, deterministic verification, Reviewer evidence, correction
history, staging state, or lifecycle facts.

The Handoff Agent grants no authority to invoke the Verifier, stage, commit,
push, finalize this handoff, declare `SOURCE_COMPLETED`, start Vault work, or
perform any lifecycle transition.

## Provenance chronology

1. Original Planner contract proposed the DL-P1.5 repository milestone.
2. First Feasibility returned `INFEASIBLE` and
   `READY_FOR_APPROVAL_1: false` with `F-P1.5-001`.
3. `HAD-DL-P1.5-PLANNER-CORRECTION-001` authorized a bounded contract
   correction.
4. Planner result `CORRECTED` defined the identifier-token validator boundary.
5. Fresh Feasibility returned `FEASIBLE_WITH_NON_BLOCKING_FINDINGS`,
   `READY_FOR_APPROVAL_1: true`, and
   `F-P1.5-001 = RESOLVED_AT_CONTRACT_LEVEL`.
6. `HA1-DL-P1.5-SQLITE-REPOSITORIES-001` approved implementation against the
   exact corrected contract and source baseline.
7. The primary implementation attempt produced the exact five-file candidate;
   no focused implementation correction was required.
8. Independent Verifier returned `PASSED` with no material findings.
9. Advisory Reviewer returned `APPROVED` with no material findings and no
   correction recommendation.
10. `HAD-DL-P1.5-HANDOFF-DRAFT-001` authorized this exact pre-commit handoff
    draft.
11. Independent draft-handoff verification, Git authority, both source commits,
    source push/synchronization, source completion, and the separate Vault
    lifecycle all remain pending.
