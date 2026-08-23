# DL-P1.3 Run and Approval Models

## Status

Handoff draft

- Implementation commit: d14c5feb07f666b4c7dfa5096668e5f0b6ad4593
- Handoff finalization commit: Pending
- Source push: Pending
- SOURCE_COMPLETED: Not reached
- Approval 2: Not reached
- Vault write: Not started
- Milestone COMPLETED: Not reached

This is an uncommitted handoff-only finalization change. It records the verified
DL-P1.3 implementation commit and does not authorize a Git operation, lifecycle
transition, Approval 2, Vault work, or any future effect.

## Implementation commit 1 provenance

- Commit: `d14c5feb07f666b4c7dfa5096668e5f0b6ad4593`.
- Tree: `968e7e8f8cfac198992f93ec3b5494ccd26a4662`.
- Parent: `f490556c8facecc87162ee03713d7f2cde811ac1`.
- Message: `Implement DL-P1.3 run and approval models`.
- Exact committed scope:
  - `docs/handoffs/dl-p1-3-run-and-approval-models.md`
  - `panam_development_loop/__init__.py`
  - `panam_development_loop/models.py`
  - `panam_development_loop_poc_test.py`

The later handoff-finalization commit remains pending and is not known yet.

## Milestone objective

DL-P1.3 delivers the smallest pure-domain approval-binding representation while
retaining the existing immutable `DevelopmentRun` value and all completed
DL-P1.1 and DL-P1.2 behavior.

## Approved scope

The approved contract is
`C:\Panam_Runtime\development-runs\dl-p1-3\planning\plan-20260807-121200\MILESTONE-CONTRACT-DRAFT.md`
(5826 bytes, SHA-256
`65dbf230018f79ee024a6bb6b39aedd2d32f2143971dd27e982ca26071bb9480`).
Feasibility was `FEASIBLE`.

The approved implementation paths are exactly:

- `panam_development_loop/models.py`
- `panam_development_loop/__init__.py`
- `panam_development_loop_poc_test.py`

DL-P1.3 excludes SQLite/schema/migration/repository work, `DevelopmentRun`
field changes, approval persistence and run linkage, State Machine and
TransitionService/policy changes, approval issuance or execution, live Git or
Vault validation, filesystem/network effects, UI, CLI, workers, registry work,
dependencies, and architecture or roadmap changes.

## Implementation

### ApprovalBinding

`ApprovalBinding` is a frozen, versioned, validated, data-only domain model.
Its exact fields are `approval_id`, `approval_version`, `approval_kind`,
`subject_id`, `subject_digest`, `target_kind`, `target_id`, `target_branch`,
`base_commit`, `allowed_actions`, `allowed_paths`, `approver_id`, and
`approved_at`.

The model supports version `"1"`, approval kinds `PHASE_START_APPROVAL`,
`APPROVAL_1`, and `APPROVAL_2`, and target kinds `SOURCE_REPOSITORY` and
`VAULT`. Approval-specific validation rejects unsupported versions and kinds,
invalid identity or required text, control characters, invalid lower-case
SHA-256 subject digests, wrong collection types, duplicate actions or paths,
and non-declarative paths. Actions must be a nonempty tuple; paths are tuple
valued and may be empty. Valid action and path tuples are lexicographically
normalized after duplicate rejection.

Allowed paths are declarative relative POSIX paths. Backslash, rooted, drive,
UNC, wildcard-like, empty-segment, and traversal forms are rejected. The
required `base_commit` and `approved_at` fields remain opaque strings; this
milestone does not inspect live Git or timestamps.

Every field participates in compact, lexicographically key-sorted canonical
JSON. The `sha256_digest()` is the SHA-256 digest of the canonical JSON UTF-8
bytes. Semantically unordered actions and paths therefore have stable canonical
representation and digest while meaningful content differences remain bound.

This model does not grant authority, execute approval authority, persist itself,
inspect live Git or Vault, mutate workflow state, invoke `TransitionService` or
the State Machine, or perform an external effect.

### Compatibility and public API

`DevelopmentRun` remains frozen and retains exactly these six fields:

- `run_id`
- `milestone_contract_digest`
- `current_state`
- `state_version`
- `created_at`
- `updated_at`

Existing `PhaseContract` and `MilestoneContract` behavior remains compatible.
The package public API now exports only the approved approval-domain symbols:
`ApprovalBinding`, `ApprovalKind`, `ApprovalTargetKind`,
`ApprovalValidationCode`, `ApprovalValidationError`, and `ApprovalVersion`.

### Focused tests

The POC tests add four `ApprovalBindingTest` cases covering:

- immutable valid binding construction and canonical ordering;
- retained immutable six-field `DevelopmentRun` shape;
- deterministic rejection of invalid version, kind, identity, required values,
  digest, collection type, duplicates, and traversal path;
- digest sensitivity and absence of approval/execution methods or filesystem
  side effects.

## Verification

Independent verification result: `PASSED`.

- Evidence directory:
  `C:\Panam_Runtime\development-runs\dl-p1-3\verification\verification-20260807-134835`
- Verification report: 3783 bytes, SHA-256
  `e9879efeaccab3c94a18920b8f10f5a8356d4f88c1d7ce483ced52934694caff`
- Tests: 17 passed on the fresh permitted rerun.
  - DL-P1.1: 5 passed.
  - DL-P1.2: 8 passed.
  - DL-P1.3: 4 passed.
- `git diff --check`: `PASSED`.

The implementation source aggregate is
`3a692c51a6b27d66ab15b4faf0a7ac01fb31b05db0f4fb27a9b02c2ea19fe30a`.
It uses lexicographic relative-path order and, per path,
`UTF-8(relative_path) + LF + raw file bytes + LF`.

The deterministic implementation diff is 44326 bytes with SHA-256
`c0e8676cbb50dd163cfa1608ca1b5016886052740cd8f6ad2a9792a538d0fe0f`.

## Review

Independent Reviewer result: `APPROVED`.

- Review directory:
  `C:\Panam_Runtime\development-runs\dl-p1-3\review\review-20260807-140349`
- Review report: 5187 bytes, SHA-256
  `3e00c96c45b4bbc7dd865d9f0c1ffa364ec4a25161cf88f23de2aeef174b0991`
- Findings: none.
- Focused corrections: 0.

The Reviewer confirmed contract conformance, the data-only authority boundary,
`DevelopmentRun` compatibility, bounded public exports, meaningful focused
tests, no later-milestone scope leakage, and sufficient verifier evidence.

## Recovery provenance

The following history is retained as execution provenance, not source-quality
findings:

- Primary implementation execution 1: `BLOCKED` before source write.
- Recovery execution 1: `BLOCKED` before source write.
- Candidate preparation: `RECOVERY_READY`.
- Recovery-write execution 1: `BLOCKED` before source write.
- Recovery-write execution 2: `IMPLEMENTED`.
- Independent verification: `PASSED`.
- Review: `APPROVED`.
- Focused corrections: 0.

The final candidate bytes were bound before the successful literal recovery
write and independently verified afterwards. The earlier write-mechanism
blockers are process history, not implementation defects.

## Intentional deferrals

The following milestones remain outside DL-P1.3:

- DL-P1.4: SQLite Migration Foundation.
- DL-P1.5: SQLite Repositories and approval persistence.
- DL-P1.6: State Machine v1.
- DL-P1.7: Transactional Transition Service.

Also deferred are live approval validation, executable authority, human UI,
worker integration, and registry integration. The approval model's declarative
fields do not constitute a permission bypass or live authorization mechanism.

## Limitations / residual risks

- Live authorization is intentionally deferred.
- Persistence is intentionally deferred.
- The LF-to-CRLF warning for `models.py` is informational under the current
  independently bound raw-byte source aggregate and deterministic diff.
- The normal sandbox temporary-directory restriction required a fresh permitted
  elevated rerun; that rerun passed all 17 tests without an assertion failure.
- No current source defect was found.

## Git state

- Baseline HEAD: `f490556c8facecc87162ee03713d7f2cde811ac1`.
- Branch: `phase/panam-dl-p1-1-durable-state-transition-kernel-poc`.
- Local, tracking, and direct remote HEAD were all
  `f490556c8facecc87162ee03713d7f2cde811ac1` before this draft.
- Divergence before this draft: `0 0`.
- Implementation changes: uncommitted.
- Handoff: uncommitted.
- Staged paths: none.

## Next lifecycle action

Fresh deterministic Verifier mode: `final-handoff`.

The draft-handoff verification passed before implementation commit 1. The
final-handoff verification has not yet happened. It must bind this exact
handoff-only change, implementation commit 1, repository state, and lifecycle
placeholders before any human-authorized handoff-finalization staging or commit.

Next safe lifecycle action: `HUMAN DL-P1.3 FINAL-HANDOFF VERIFICATION DECISION`.
