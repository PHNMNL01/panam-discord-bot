# DL-2.4 Read-Only Git Inspector candidate

This callable candidate gathers local evidence for a registered project. It
returns immutable values; it grants no approval and changes no workflow state.
Implementation evidence and independent verification are separate lifecycle
steps. No acceptance, source completion or deployment is claimed here.

## API and authority

Import `GitInspector` from `panam_development_loop.git_inspector` and the
versioned values from `panam_development_loop.git_inspection_models`. Existing
package-root exports are unchanged. The public call is
`inspector.inspect(GitInspectionRequest(project_id, context))`.

An authorized outer composition installs the existing `ProjectPolicyReader`
with its get-only repository and one independently approved
`InspectionEndorsement`. The endorsement binds Human authority, implementation
contract, project, context ID and exact canonical context SHA-256/byte length.
It is not an inspection request field. An untrusted caller must not control
composition, dependency injection or endorsement installation. Constructing an
endorsement value, claiming an issuer or computing a digest does not establish
Human authority. Production authentication/provisioning is outside this slice.

The Registry's unchanged three-field Policy v1 selects the root. Context root
and repository-directory values are comparison bindings only. Context v1 is a
deeply immutable, non-persistent value. Its exact canonical JSON covers the
13 required semantic bindings plus explicit additional-remote treatment.
`expected_branch_constraint` is an exact attached local branch name;
`expected_baseline_constraint` is either REQUIRED with a typed commit OID or
explicit NOT_REQUIRED_FOR_THIS_PROFILE. No observed value supplies a missing
expectation. Identity/state freshness is enforced; temporal expiry is not part
of this version.

## Supported profile and content scope

`WINDOWS_LOCAL_ATTACHED_V1` accepts an ordinary non-bare local fixed-drive
Windows worktree. The profile authorizes Git control metadata (local config,
HEAD/index/refs/object metadata and necessary approved-path objects), directory
names/stat observations, and only the exact approved tracked worktree paths
for content/diff reads. Status/path names do not grant content permission.
Untracked contents and excluded contents are never requested. Exclusions are
case-insensitive exact paths or directory prefixes; scope overlap is invalid.

Staged metadata covers the index. Unstaged content evidence covers only
`approved_read_path_scope`; `content_unassessed_paths` explicitly lists other
tracked paths. COMPLETE means the requested bounded observations and policy
comparisons completed, not that the whole worktree is clean or acceptable for
any action. Untracked names include ignored files because no ignore file is
read. Conflicts remain explicit index-stage metadata. Detached/unborn states
are diagnostic only; configured but unavailable upstream evidence cannot
complete. Divergence describes local refs, never live remote freshness.

Unsupported layouts include UNC/network/device roots, reparse points/junctions,
symlinks/hardlinks, linked worktrees, nested repositories, submodules, alternates,
shallow/partial clones, split indexes and replacement refs. Root ancestors and
all enumerated entries are checked physically. The bound is 8,192 metadata
entries, 32 exact approved tracked paths and 2 MiB per approved file/control
payload. Unsupported or over-limit observations fail closed.

The adapter rejects any worktree/index `.gitattributes` and local info attributes
before content operations. Local config accepts only a conservative literal
subset of core repository layout flags, user identity, local branch tracking,
remote URL/fetch metadata and object format. Unknown sections/keys, includes,
escapes, continuation, helper/filter/credential/diff/fsmonitor configuration
are rejected before Git. This deliberate first-profile limitation prevents
implicit excluded-file reads and helper execution. It does not silently treat
unknown configuration as benign.

## Fixed operation and process contract

The adapter owns a closed operation table. Every operation uses the registered
cwd, an argv array with no shell, the fixed implementation-approved executable
`C:\Program Files\Git\cmd\git.exe`, strict UTF-8 metadata parsing (binary diff
capture excepted), and explicit allowed exits. There is no public command,
executable, root, environment or resource override. Dependency injection is a
trusted application/test boundary, not an untrusted request API.

| Operation | Command body / output / allowed exits |
| --- | --- |
| ROOT | `rev-parse --show-toplevel`; one path; 0 |
| FORMAT | `rev-parse --show-object-format`; sha1/sha256; 0 |
| BRANCH | `symbolic-ref -q HEAD`; attached ref; 0 or detached 1 |
| HEAD | `rev-parse --verify HEAD^{commit}`; typed OID; 0 or diagnostic 128 |
| TREE | `rev-parse --verify HEAD^{tree}`; typed OID; 0 |
| INDEX | `ls-files --stage -z`; NUL mode/OID/stage/path; 0 |
| STAGED | `diff-index --cached --raw --no-abbrev -z ... HEAD --`; NUL changes; 0 |
| UNSTAGED | `diff-files --raw --no-abbrev -z ... -- <scope>`; NUL changes; 0 |
| STAGED_DIFF | `diff --cached --binary ... -- <scope>`; bounded bytes; 0 |
| UNSTAGED_DIFF | `diff --binary ... -- <scope>`; bounded bytes; 0 |
| UPSTREAM | `rev-parse --symbolic-full-name @{upstream}`; local ref; 0 or absence 128 |
| DIVERGENCE | `rev-list --left-right --count HEAD...@{upstream}`; two counts; 0 |

All diff operations disable renames, external diff and textconv; payload diffs
also disable color. Scope arguments are exact literal paths. Empty scope skips
content operations instead of expanding to the repository. Remote metadata
comes from the vetted local config without any transport operation.

Every call disables optional locks, lazy fetch and replacement objects. The
environment retains only Windows system locations and implementation-owned
Git/locale controls. Global/system config and attributes are disabled; no
inherited GIT, SSH, credential, pager, HOME or PATH injection is accepted.
Protocol transport, hooks, fsmonitor, untracked cache, conversion, split/sparse
index behavior and automatic maintenance are disabled or rejected. No network
operation exists in the table. Local Git 2.54.0.windows.1 and Python 3.14.5 are
the authorized implementation/test tool identities, not a cross-version claim.

Each Git process has a 10-second ceiling including a reserved cleanup interval,
2 MiB per captured stream, at most 32 invocations and a 60-second inspection
deadline. Incremental pipe readers retain only bounded bytes. A suspended
Windows process is assigned to an anonymous kill-on-close Job before resume;
inheritance is restricted to the standard handles. Native process creation uses
the argv quoting semantics of `subprocess` with `shell=False`. The private
transport is specific to this fixed Git table, not a general process service.

The Job owns descendants. Timeout, overflow, read failure and parent exit with
surviving descendants terminate the owned tree, wait boundedly for an empty Job,
and finish capture. Cleanup is explicit; cleanup failure blocks completeness
and retains the primary cause. Killing just the parent is never cleanup proof.

## Freshness, evidence and limitations

Two snapshots bind root topology/control metadata, branch/HEAD/tree, index,
scoped changes/diff bytes, approved raw identities and local configuration.
The application re-reads Registry policy and compares the installed context
and endorsement binding. Required drift invalidates the returned observations;
there is no retry or repair. Command evidence contains actual argv/cwd,
timestamps, exit, bounded captures, capture identities and cleanup outcome.
Git OIDs, worktree bytes, absent files, canonical context and command captures
have separate identity domains.

These are practical Windows checks, not hardened OS isolation or a global
filesystem lock. Before/after equality cannot eliminate an adversarial
change-and-restore race, replacement of a trusted installed executable or a
kernel/file-system operation that blocks outside process control. The caller
must provide a stable trusted execution environment. Unsupported layouts and
ambiguous required evidence cannot be promoted to compliant completion.

No DB initialization/migration, policy/Registry schema change, evidence kind,
transition, queue command, worker handler, provider, CLI or persistence is added.
Tests use only disposable synthetic repositories/SQLite fixtures. Fixture
creation/staging/commits/branches are setup effects; inspector calls must leave
their Git/control/worktree/database bytes unchanged. Required tests include
native junctions, exclusively locked excluded content, helper rejection,
timeout, overflow, descendant cleanup and the parent-exit cleanup race. Full
Development Loop regression retains the existing R11 evidence assertions.

The later independent Verifier, Reviewer and HV02 draft/final handoff lifecycle
remain unchanged. This implementation creates no canonical handoff.
