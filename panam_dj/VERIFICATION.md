# DJ-P0 implementation evidence for human review

Date: 2026-10-26 (Europe/Prague). Authority:
`HAD-DJ-P0-IMPLEMENTATION-AUTHORIZATION-001`, followed by the separate DJ-P0
implementation execution instruction. This is implementation evidence, not a
formal milestone acceptance, independent Reviewer result or canonical handoff.

The original implementation and Focused Correction 001 results below are
historical. Current correction evidence is recorded under **Focused Correction
002** at the end of this file.

## Repository binding

- Checkout: `C:\Panam_APP_DJ`.
- Branch: `experiment/dj-panam-poc`.
- HEAD: `830a90f3bb57883b5ae420cdffcd12e9473a8643` (unchanged).
- Tracking branch: `origin/main`.
- Recorded origin fetch/push URL: `https://github.com/PHNMNL01/panam-discord-bot.git`.
- `git rev-list --left-right --count HEAD...@{upstream}`: `0 0`, exit 0,
  against the existing local tracking reference; no fetch or remote-state claim.
- Initial `git status --short`: empty; initial `git status -sb`:
  `## experiment/dj-panam-poc...origin/main`.
- Applicable instruction discovery found only root `AGENTS.md`. The explicit
  DJ-P0 milestone authorization satisfies the previously unresolved runtime
  implementation exception. No policy or architecture file was modified.
- Relevant architecture boundaries and existing bot entrypoint, voice module,
  root requirements and repository smoke-test style were inspected read-only.

## Created paths

All paths below are new and remain untracked. No pre-existing tracked file was
modified. Source/config/catalog/test artifacts and this documentation are all
inside the authorized subtree:

```text
panam_dj/README.md
panam_dj/SOURCE_FEASIBILITY.md
panam_dj/VERIFICATION.md
panam_dj/__init__.py
panam_dj/__main__.py
panam_dj/catalog.py
panam_dj/catalog/audio/dj_panam_demo.wav
panam_dj/catalog/catalog.json
panam_dj/config.example.json
panam_dj/config.py
panam_dj/discord_app.py
panam_dj/playback.py
panam_dj/requirements.txt
panam_dj/smoke.py
panam_dj/tests/__init__.py
panam_dj/tests/fakes.py
panam_dj/tests/test_boundaries.py
panam_dj/tests/test_catalog_config.py
panam_dj/tests/test_discord_app.py
panam_dj/tests/test_playback.py
```

## Initial implementation verification (historical)

All initial commands ran from `C:\Panam_APP_DJ`; Python commands used `-B` to
avoid bytecode-cache writes. These results describe the initial delivery before
independent review. They do not establish the corrected source's current state.

| Command or deterministic inspection | Actual outcome |
| --- | --- |
| `python --version` | Python 3.14.5, exit 0. |
| Installed `importlib.metadata` and `import discord` inspection | discord.py 2.7.1 imports successfully. PyNaCl and davey absent. No installation. |
| `ffmpeg -version` | FFmpeg 8.1.1 with libopus, exit 0. |
| `python -B -m unittest discover -s panam_dj/tests -t .` | **48 tests passed**, exit 0, no skips. Final runtime-source run: 2.035 seconds. Earlier verbose runs also passed. |
| `python -B -m panam_dj.smoke` | **Passed**, 103 Opus packets from the bundled WAV; source cleaned; exit 0. |
| `python -B -m panam_dj --check` | Configuration/catalog valid; missing PyNaCl and davey reported; application exit **2**, as expected for missing live prerequisites. No token read or login. |
| Python subprocess wrapper asserting the `--check` result | Confirmed application return code 2 and both missing-package messages; wrapper exit 0. The direct PowerShell tool invocation had reported shell exit 1, hence the explicit subprocess confirmation. |
| `git diff --check` | Exit 0, no whitespace errors in tracked changes. |
| `git diff --cached --check` | Exit 0, no staged changes. |
| `git diff --name-only` / `git diff --cached --name-only` | Both empty, exit 0. New untracked files do not appear in these diffs. |
| Per-file `git -c core.autocrlf=false diff --no-index --check -- NUL <new-file>` | All 20 new files checked with no diagnostics; normal difference exits 1. The verifying Python driver exited 0. The `-c` setting is process-local and writes no Git configuration. |
| Python `pathlib`/regular-expression documentation and scope inspection | All 20 paths match the manifest and Git status; all 19 text files have final newlines and no trailing whitespace; all 12 local Markdown links resolve inside `panam_dj`; no test fixtures or bytecode caches remain. Exit 0. |

The 48 tests cover FIFO and concurrent capacity enforcement; audio-thread
completion; duplicate/stale callbacks; skip while paused; stop/leave/rejoin;
guild/channel isolation; start/play/cleanup/disconnect failures; provider and
connection cancellation; guild removal and shutdown; configuration and catalog
validation; traversal/device/UNC/alternate-stream rejection; simulated link and
junction rejection; WAV corruption and tampering; all seven registered slash
commands; identity checks; permissions; explicit local FFmpeg options; output
bounds; and import/dependency separation from Panam and AI modules.

Actual NTFS symlink/junction creation was not attempted: link detection is
mocked to test rejection without requiring filesystem privilege. Discord
application calls, voice handshakes and slash-command synchronization are mocked.
The FFmpeg smoke test separately exercises the actual supported audio-source
class and installed encoder against synthetic local data.

## Failures, corrections and warnings retained

1. The first focused test run stalled in callback draining and was interrupted
   (exit 1). A single-test diagnostic with `faulthandler.dump_traceback_later(5,
   exit=True)` exited 1 and identified a Python 3.14 scheduling issue: gathering
   already-finished tasks could finish before their done callbacks removed them
   from the tracking set. Explicit removal after gathering fixed the busy loop;
   subsequent playback tests passed.
2. The next 29-test focused run passed all 18 playback tests but reported 22
   catalog fixture setup/cleanup errors (exit 1). Python 3.14 temporary-directory
   ACLs blocked the restricted Windows token. Fixtures now use UUID-named normal
   directories under `panam_dj/tests/`, with containment-checked cleanup. All 11
   catalog tests subsequently passed. The eleven empty directories from the
   failed run could not be removed under the sandbox; a narrowly approved
   unsandboxed cleanup removed only those exact empty directories (exit 0),
   without recursive deletion or permission/OS-setting changes. No fixtures remain.
3. An early full run had 44 tests with three entrypoint-test failures (exit 1):
   an overbroad environment mock also rejected argparse/gettext locale access.
   The mock now supplies synthetic non-secret defaults while rejecting credential
   lookups. All three entrypoint tests then passed.
4. Installed discord.py source inspection showed that an explicit `codec="libopus"`
   input hint would select stream copy. The factory now leaves the hint unset to
   transcode PCM. The first actual FFmpeg smoke run still failed with zero packets
   and background pipe errors (exit 1). A bounded synthetic-only FFmpeg diagnostic
   reported process exit 4294967274 and the missing `fd` protocol permission.
   The final factory permits only local `pipe,fd` protocols and passes a proper
   stderr file object rather than an integer subprocess sentinel. The smoke then
   passed and was rerun successfully after the final runtime changes.
5. The passing full suite emits a discord.py 2.7.1 `DeprecationWarning` from
   `discord.utils.escape_markdown` for a positional `re.sub` count on Python 3.14.
   This upstream warning remains visible; dependencies were not modified.
6. A read-only new-file Git whitespace probe reported its normal difference
   exit 1 and the repository's LF-to-CRLF warning. No repository-wide line-ending
   normalization or Git configuration change was made. Tracked `git diff --check` alone does
   not cover new files; final delivery additionally checks each untracked file.
7. Fetching the very large online discord.py API reference failed with a response
   size limit. Supported signatures and relevant implementation behavior were
   inspected from the installed 2.7.1 package; public changelog and interaction
   documentation were available. This did not block implementation.
8. The additional untracked-file checker initially rejected CRLF in the newly
   generated catalog manifest when automatic CRLF conversion was disabled for
   that read-only Git invocation (checker exit 1; per-file Git exit 3). Only that
   new manifest was written with LF endings. The 48-test suite and real FFmpeg
   smoke both passed again afterward; no tracked file was normalized.

## Remaining prerequisites and limitations

No deterministic test was skipped for missing voice packages: fakes isolate the
voice transport, and the real FFmpeg audio source can run without a voice
connection. **Live voice/DAVE validation was not run**: PyNaCl and davey are
missing, and live login, account creation, credentials and external activation
are outside this authorization. No existing Panam runtime smoke suites were
run: no existing module changed, and unrelated runtime/credential paths were
kept outside the test boundary.

The owner must separately approve dependency installation/provisioning, create
or select a dedicated application and token, set real application/guild IDs,
grant guild/voice permissions, and authorize the live test. The README describes
the exact future entrypoint and test sequence. DAVE compatibility is delegated
to the maintained discord.py voice implementation; an actual encrypted session
has not been established here.

Only original synthetic local audio is bundled. There is no URL fetching,
YouTube extraction, persistent queue, idle disconnect or DJ-role restriction.
URL-provider selection and appropriate music rights remain separate decisions.
The source investigation is documentation, not authorization or proof of broad
catalog availability.

## Final Git state and prohibited actions

`git status --short` reports only:

```text
?? panam_dj/
```

`git status -sb` reports the unchanged branch tracking `origin/main`, plus that
new subtree. `git status --porcelain=v1 --untracked-files=all` enumerates the
20 paths above. Tracked and staged diffs are empty; the implementation is the
new untracked content, not an empty change set.

No staging, commit, push, pull, branch creation/switch/deletion, merge, rebase,
stash, reset, clean, remote modification or PR operation was performed. No
dependency installation, credentials access, Discord login, account creation,
OpenAI call, paid call, media acquisition, deployment, OS change, Vault write or
edit outside `panam_dj/**` was performed. No milestone transition or formal
acceptance is declared.

## Focused Correction 001

This bounded correction responds to the independent `REVISION_REQUIRED` review
under the existing milestone authority and the separate Focused Correction 001
execution request. No new milestone, provider, dependency or service is added.
The correction remains subject to independent re-review and human acceptance.

### Preflight and change boundary

Before editing, read root `AGENTS.md`, inspected applicable instruction paths,
repository root, branch, HEAD, upstream, divergence, remotes, staged/tracked
diffs and the complete untracked-file list. All matched the expected state:

```powershell
git rev-parse --show-toplevel
git branch --show-current
git rev-parse HEAD
git status --short
git status -sb
git status --porcelain=v1 --untracked-files=all
git diff --name-only
git diff --cached --name-only
git rev-parse --abbrev-ref --symbolic-full-name '@{upstream}'
git rev-list --left-right --count 'HEAD...@{upstream}'
git remote -v
```

These read-only inspections succeeded (exit 0). Branch/HEAD/origin/upstream are
as recorded above, divergence is `0 0`, staged and tracked changes are empty,
and exactly the original 20 untracked files exist. A Python SHA-256 inspection
confirmed the reviewed content snapshot before any edit:

```text
5eea8f3252c9b01a8712fa68e5d03c4be5bb63d43e483ec1e461288d975488b6
```

The snapshot hashes sorted relative paths plus each file's content digest;
original text was retained in session memory for comparison, without creating
backup files. Exactly these ten existing untracked files were modified:

```text
panam_dj/__init__.py
panam_dj/config.py
panam_dj/discord_app.py
panam_dj/playback.py
panam_dj/requirements.txt
panam_dj/tests/test_catalog_config.py
panam_dj/tests/test_discord_app.py
panam_dj/tests/test_playback.py
panam_dj/README.md
panam_dj/VERIFICATION.md
```

No files were added or deleted. The other ten files, including the catalog,
configuration template, WAV, source-feasibility note and FFmpeg smoke program,
remain unchanged. Temporary fixture creation and cleanup are explicitly
authorized for this correction; fixtures stay under `panam_dj/tests/` and use
the existing containment-checked cleanup procedure.

### Findings and corrections

1. **Caller authorization freshness:** The Discord adapter binds each request to
   its original guild/channel and supplies a synchronous callback reading the
   caller's current voice membership. The playback manager invokes it under the
   guild lock before effects and after awaited preparation. All seven commands
   reject stale callers. A connection newly obtained while the caller left is
   disconnected without enqueueing or starting audio; failed disconnects retain
   ownership for cleanup. Existing guild and bot-permission checks remain.
2. **Cancellation-safe shutdown:** Both manager and client retain an owned close
   task and wait through caller cancellation, including repeated cancellation.
   Cleanup is observed to completion before cancellation is re-raised. Every
   guild is visited, successful disconnects release ownership, and the client
   runs base teardown in `finally`. Concurrent close callers share the same
   attempt. Completed shutdown is idempotent; failure retains a retryable closing
   state and remaining voice references instead of claiming they are closed.
3. **Python and junction safety:** Python 3.12+ is enforced at package import.
   `Path.is_junction()` is called directly, the conditional test omission is
   removed, and README/dependency comments state the same runtime minimum.
4. **Post-resolution connection validation:** After provider resolution, an
   existing voice client's connected state, guild and channel are checked again
   before the resolved track can enter the queue or start. Stale results are
   rejected; lifecycle reconciliation separately owns connection cleanup.

### Regression cases and actual command results

Eleven new `test_correction_*` test methods cover:

- All seven commands with a caller moving during acknowledgment (seven subcases).
- All seven commands with a caller leaving while waiting for the guild lock
  (seven subcases).
- Caller movement during source resolution and departure during voice setup.
- Manager shutdown cancellation, concurrent/repeated client-close cancellation,
  two-guild cleanup and guaranteed base teardown; normal close exceptions also
  exercise `finally` teardown.
- Retrying a failed disconnect without disconnecting the released guild twice.
- A deliberately suspending provider with both a moved and disconnected voice
  client; rejection preserves the queue and starts no stale audio.
- Simulated Python 3.11 rejection and Python 3.12 acceptance at package import.

The exact focused and full-suite commands were:

```powershell
python -B -m unittest discover -s panam_dj/tests -t . -k correction -v
python -B -m unittest discover -s panam_dj/tests -t . -v
python -B -m panam_dj.smoke
python -B -m panam_dj --check
```

| Check | Actual result |
| --- | --- |
| Focused correction tests | **11 passed**, exit 0, 0 failures/errors/skips, 0.288 seconds. |
| Complete suite | **59 passed**: the original 48 plus 11 new tests. Exit 0, 0 failures/errors/skips, 1.038 seconds. This includes the original seven-command behavior and isolation tests. |
| Local Opus smoke | **103 packets**, exit 0; source cleaned up, no Discord connection. |
| Runtime readiness | Configuration and catalog valid; exit **2** for missing PyNaCl and davey. No credential read, login or command synchronization. |

The version regressions invoke the existing interpreter with these synthetic
version values; no Python installation is changed:

```text
python -B -c "import sys; sys.version_info = (3, 11, 9, 'final', 0); import panam_dj"
python -B -c "import sys; sys.version_info = (3, 12, 0, 'final', 0); import panam_dj"
```

The first child process exits 1 with the explicit Python 3.12 requirement,
which is its expected test result. The second exits 0. These exercise the guard
using Python 3.14.5; they are not claims of executing real Python 3.11/3.12.

No correction test failed or was skipped. The full suite still emits the
existing discord.py 2.7.1 positional-`count` `DeprecationWarning`; the focused
run emitted no warning. No package, fixture-permission or external-access
failure occurred during this correction. All catalog tests ran using isolated
temporary fixtures authorized by this request.

The source and test checks ran after the last runtime edit. Subsequent changes
only document these results. Final inspection checked all 20 untracked files
against the original path inventory and all ten changed text files against the
pre-correction snapshot. No added/deleted files, leftover fixtures or bytecode
directories were found. All 12 local Markdown links resolved within the package;
all text files passed final-newline and trailing-whitespace checks. The WAV
digest still matches the original value recorded above.

`git diff --check` and `git diff --cached --check` both returned 0 with no output.
Because those checks omit untracked content, each of the 20 paths was also
checked with `git -c core.autocrlf=false diff --no-index --check -- NUL <path>`.
Every invocation returned 1 (the expected file-versus-NUL difference), with no
whitespace diagnostics or other output. The Python inventory/link/hash inspection
returned 0. Final Git inspections repeated the commands in the preflight block:
all returned 0, with unchanged branch/HEAD/remotes, `0 0` divergence, empty
staged/tracked diffs and exactly the original 20 untracked paths.

### Remaining limits and re-review boundary

PyNaCl and davey remain absent. A live Discord voice session and DAVE handshake
were not attempted. Real audio-thread/transport cleanup timing still needs a
separately authorized live acceptance test; local Opus output is not proof of
DAVE compatibility. Native NTFS junction creation is not needed for the tests:
the supported runtime guard and mocked rejection path are tested deterministically.

Cleanup cancellation is deferred while the owned cleanup is running; forced
process termination or an event loop being destroyed cannot provide the same
completion guarantee. A genuine disconnect failure is reported and retains
the reference for an explicit retry; no successful cleanup is invented.

No Git mutation, dependency installation, credentials access, Discord/OpenAI
authentication, live service, media acquisition, URL provider, deployment or
change outside `panam_dj/**` was performed. The final expected Git state remains
the same branch/HEAD, no staged/tracked diff, and `?? panam_dj/` containing the
same 20 paths. This evidence is returned for independent re-review, without a
formal acceptance or workflow-transition declaration.

## Focused Correction 002: audio source cleanup barrier

This bounded implementation responds to Independent Focused Re-Review 001 under
`HAD-DJ-P0-IMPLEMENTATION-AUTHORIZATION-001` and the separate Focused Correction
002 execution request. It is evidence for independent re-review, not acceptance.

### Preflight and scope

Read root `AGENTS.md` and checked parent/nested instruction paths. The read-only
Git commands recorded in the Correction 001 preflight block all returned 0.
Root, branch, HEAD, origin, upstream and `0 0` divergence matched the repository
binding above. Staged/tracked diffs were empty and exactly the original 20
untracked DJ paths existed. The pre-edit snapshot matched the independent review:

```text
c2fe9f153d3d34b2f85cf1427ace811c09be09984ad40010744b3a7c6184e3b5
```

Six existing untracked files were modified; no files were added or deleted:

```text
panam_dj/playback.py
panam_dj/discord_app.py
panam_dj/tests/test_discord_app.py
panam_dj/tests/test_playback.py
panam_dj/README.md
panam_dj/VERIFICATION.md
```

The remaining 14 files, including the catalog, audio, configuration and dependency
declaration, are unchanged. No dependency or service was added. The full suite's
existing temporary fixtures stay within the authorized `panam_dj/tests/` scope
and use its existing containment-checked cleanup; this implementation request
permits safe full-suite verification.

### Root cause and correction

Installed discord.py 2.7.1 invokes `after()` before `source.cleanup()`, and
disconnect does not join the audio thread. The previous manager's callback drain
and released voice references therefore did not prove source cleanup.

`OwnedSource` delegates read/is_opus/cleanup and retains a completion future.
Its thread lock allows exactly one delegated cleanup attempt; the event loop is
notified only after that attempt returns or raises. The original exception is
preserved as the completion result and re-raised to the cleanup caller. The
Discord adapter supplies a subclass of the public `discord.AudioSource` interface;
production code does not access private AudioPlayer state.

Each created source is registered before play. Successful cleanup removes its
ownership record; errors retain it and set a safe session error. Rejected starts
receive a retained, exception-observed `asyncio.to_thread` cleanup task, avoiding
synchronous cleanup on the event loop. Completed/stopped/skipped/left sources
remain tracked independently of the current track and voice connection.

Shutdown visits every guild, drains callbacks and then waits asynchronously for
source completion and retained cleanup tasks with a five-second default timeout.
Timeout does not cancel those signals or tasks. Unresolved guild IDs are returned,
ownership and errors remain available, and `closed` stays false. Subsequent close
calls reconcile late success without double cleanup or double disconnect. A
cleanup exception is not retried automatically because repeating arbitrary
partially completed cleanup is unsafe. Public close cancellation still waits for
the bounded result and superclass teardown before propagating.

Successful manager closure now requires all three conditions: no owned voice
references, no unsuccessful/incomplete source cleanup records, and no retained
failed-start cleanup tasks. Callback completion alone cannot satisfy that condition.

### Deterministic reproduction and verification

The regression was added and executed before the implementation changed:

```powershell
python -B -m unittest panam_dj.tests.test_discord_app.SourceCleanupTests.test_cleanup_barrier_waits_beyond_after_callback -v
```

Initial result: **1 test, 1 expected reproduction failure, exit 1**. The assertion
reported `close falsely succeeded before source cleanup finished`. It used the
installed AudioPlayer with a fake transport, observed `after()` while cleanup was
blocked by a threading.Event, and demonstrated `closed=True` too early. Teardown
released and joined the thread. The identical test passed after the correction
(1 test, exit 0).

Nine new regression methods cover blocked read, after-before-cleanup ordering,
cleanup errors, concurrent/repeated cleanup delegation, timeout and later
reconciliation, cancelled timeout, cancellation with two guilds and concurrent
client closes, base teardown, stale callbacks, failed-start cleanup, and the
stop/skip/leave/natural-completion paths (four subcases). They use deterministic
events rather than playback timing sleeps. Every real AudioPlayer thread is
released and joined by test teardown; all transport operations are in memory.
The intentional cleanup-exception case captures and checks the thread exception
instead of hiding an unexpected warning.

Exact verification commands and outcomes:

| Command | Actual result |
| --- | --- |
| `python -B -m unittest panam_dj.tests.test_discord_app.SourceCleanupTests -v` | Intermediate focused run: 8 tests passed, exit 0, no failures/errors/skips. |
| `python -B -m unittest discover -s panam_dj/tests -t . -v` | Initial full run: 67 tests, 1 failure, exit 1. Existing failed-start test asserted synchronous cleanup before its new off-loop task completed. |
| `python -B -m unittest panam_dj.tests.test_discord_app.SourceCleanupTests panam_dj.tests.test_playback.PlaybackTests.test_failed_queued_track_is_discarded_and_source_cleaned -v` | After updating that test to observe the retained cleanup task and adding cancelled-timeout coverage: **10 passed** (9 new + 1 existing), exit 0, no failures/errors/skips, 0.307 seconds. |
| `python -B -m unittest discover -s panam_dj/tests -t . -v` | Final full run: **68 passed** (previous 59 + 9 new), exit 0, no failures/errors/skips, 1.278 seconds. |
| `python -B -m panam_dj.smoke` | **103 Opus packets**, exit 0; source cleanup completed; no voice connection. |
| `python -B -m panam_dj --check` | Configuration/catalog valid; missing PyNaCl and davey, exit **2**. No token read or login. |

The existing test change awaits failed-start cleanup explicitly; it does not
remove the source-cleaned assertion. The two recorded failing runs are preserved
above. Final tests contain no failures or skips. Full runs still emit the existing
discord.py positional-`count` DeprecationWarning; focused runs emit no warnings.
Tests ran after the last runtime/test edit; subsequent edits document results.

Final inspection read all 20 untracked files (including WAV metadata/hash),
compared the text changes with the retained pre-edit snapshot, and confirmed
exactly the six modified paths above. The inventory, text-whitespace, local-link,
audio-hash and artifact checker (`python -B -`, using pathlib/hashlib/wave and
subprocess) returned 0: 20 expected files, 12 valid local Markdown links, unchanged
WAV digest, and no fixture or bytecode directories. Each file passed
`git -c core.autocrlf=false diff --no-index --check -- NUL <path>` with exit 1
(expected file-versus-NUL difference) and no diagnostics.

Final `git diff --check` and `git diff --cached --check` returned 0. The preflight
Git inspections were repeated, all exit 0: unchanged root/branch/HEAD/remotes,
upstream `origin/main`, divergence `0 0`, empty staged/tracked diffs, and
`git status --short` reporting only `?? panam_dj/` with exactly 20 untracked paths.

### Limits and delivery boundary

The source deadline does not impose a new deadline on Discord transport teardown.
Timeout is an explicit unresolved state, not forced cancellation of native code.
A permanently blocked source/audio thread or executor cleanup may require operator
reconciliation and can delay process termination; no successful close is claimed
in that state. The wrapper observes delegated cleanup completion, not a join of
the audio thread. Installed FFmpeg cleanup may internally suppress process-kill
errors, so a successful method return is not independent proof of OS process
termination under every failure. The normal local Opus smoke succeeded.

PyNaCl/davey provisioning, real transport cleanup, and live DAVE negotiation remain
separately authorized future checks. No dependency installation, Git mutation,
credentials access, external service, URL provider, media acquisition, deployment,
or edit outside `panam_dj/**` was performed. No formal milestone acceptance is
declared. This delivery stops at implementation evidence for independent re-review.


## DJ-P2.1 implementation evidence (2026-10-27)

Authority: `HAD-DJ-P2-1-YOUTUBE-POC-IMPLEMENTATION-001` plus the separate
implementation-execution prompt. This section supersedes earlier environment
and source-state observations for DJ-P2.1; prior verification remains historical.
No formal acceptance or workflow transition is declared.

### Preflight and environment

Repository root `C:/Panam_APP_DJ`; branch `experiment/dj-panam-poc`;
HEAD `bd1d16e41b90b095af89ad41d7cfe2c4109f669c`. Initial working tree and index
were clean. Upstream was `origin/experiment/dj-panam-poc`, locally recorded
divergence `0 0`; remote was `https://github.com/PHNMNL01/panam-discord-bot.git`.
Root AGENTS.md was read; no parent/nested DJ AGENTS.md was found. The authorized
source scope is exclusively `panam_dj/**`. No policy exception was assumed.

The initial default-Python metadata probe returned exit 1 because yt-dlp was
missing from that interpreter. No installation occurred. Inspection located the
existing `.venv`, which was used for all tests and verification:

- Python 3.14.5; discord.py 2.7.1; yt-dlp 2026.10.26; yt-dlp-ejs 0.8.0.
- PyNaCl 1.6.2 and davey 0.1.6 are present. Installed discord.py metadata declares
  `PyNaCl>=1.5.0,<1.6` for its voice extra. This mismatch was not modified and
  needs human review before a separately authorized live phase.
- EJS/Deno are not invoked by this implementation. FFmpeg was available.

The repository tests were inspected before execution. Catalog fixtures create
and remove directories only inside `panam_dj/tests/`; no credentials or live
services are used. New tests use fake metadata/process/voice boundaries and
existing authorized synthetic audio only. No new audio asset was created.

### Changed paths and behavior

Created:

- `panam_dj/sources.py`
- `panam_dj/metadata_worker.py`
- `panam_dj/youtube.example.json`
- `panam_dj/tests/test_sources.py`
- `panam_dj/tests/test_preparation.py`

Modified:

- `panam_dj/catalog.py`
- `panam_dj/config.py`
- `panam_dj/config.example.json`
- `panam_dj/discord_app.py`
- `panam_dj/playback.py`
- `panam_dj/__main__.py`
- `panam_dj/requirements.txt`
- `panam_dj/tests/test_boundaries.py`
- `panam_dj/README.md`
- `panam_dj/SOURCE_FEASIBILITY.md`
- `panam_dj/VERIFICATION.md`

Strict YouTube video URLs resolve through a bounded, metadata-only worker.
The worker rejects unverified yt-dlp versions and never downloads audio. Video
identity/title is separated from a hash-bound local media identity. Mappings
require explicit owner configuration and existing approved catalog assets.
Shipped metadata access is disabled and mappings are empty. Playback labels
identify the actual local audio and the requested video's metadata separately.

Per-guild FIFO reservations keep discovery outside the state lock; controls can
cancel pending preparation. Authorization is rechecked before and after awaited
work. Generation checks, source cleanup barriers and base client teardown remain.
Worker launch/cancellation/termination, late responses, timeout suppression and
concurrent multi-guild shutdown have focused coverage. Cancellation while a
connector suppresses cancellation now releases its resulting voice connection
without playing. Non-cancelling asyncio.wait barriers avoid Python 3.14's
shielded-future error diagnostic while still observing owned work before
propagating caller cancellation.

### Actual commands and outcomes

All Python commands below ran from `C:/Panam_APP_DJ`.

| Command | Actual result |
| --- | --- |
| `.\.venv\Scripts\python.exe -B -m unittest discover -s panam_dj/tests -t . -q` | Pre-edit baseline: 68 passed, exit 0, 1.358 s. |
| `.\.venv\Scripts\python.exe -B -m unittest panam_dj.tests.test_playback panam_dj.tests.test_catalog_config panam_dj.tests.test_discord_app panam_dj.tests.test_boundaries -q` | Initial integration preserved all 68 tests, exit 0, 1.368 s. |
| `.\.venv\Scripts\python.exe -B -m unittest panam_dj.tests.test_sources panam_dj.tests.test_preparation -v` | First focused run: 30 tests, one test-fixture error, exit 1. Equality treated integer 1 as boolean True in test branching; corrected to compare fixture identity. |
| `.\.venv\Scripts\python.exe -B -m unittest panam_dj.tests.test_sources panam_dj.tests.test_preparation -q` | Expanded focused run: 34 passed, exit 0, 1.116 s. Further expanded run: 36 passed, exit 0, 1.106 s, but exposed a shielded-future diagnostic; corrected as described above. |
| `.\.venv\Scripts\python.exe -B -m unittest panam_dj.tests.test_preparation panam_dj.tests.test_discord_app.SourceCleanupTests -q` | After cancellation-barrier correction: 25 passed, exit 0, 0.530 s, no warnings or loop diagnostics. |
| `.\.venv\Scripts\python.exe -B -m unittest discover -s panam_dj/tests -t . -v` | Intermediate: 102 passed, exit 0. Final after all runtime/test edits: **104 passed**, exit 0, 2.336 s, zero failures/errors/skips. |
| `.\.venv\Scripts\python.exe -B -m panam_dj.smoke` | Final: **103 Opus packets**, exit 0, source cleaned; no voice connection. |
| `.\.venv\Scripts\python.exe -B -m panam_dj --check` | Final: valid configuration, one local track, one placeholder guild, exit 0; no token access/network. This is not a full dependency resolver or live-readiness check. |

The final suite contains the original 68 plus 36 focused methods. Native
integration checks exercise the installed yt-dlp API without network/download
calls, a real offline Python subprocess with observed exit, and a mapped local
WAV through real FFmpeg. The mapped-asset test observes the actual FFmpeg process
exit after manager.close, separately from AudioSource cleanup completion. This
normal-path observation is not proof of termination under every OS failure.

Warnings/diagnostics are not hidden:

- Final full suite: existing discord.py positional-count DeprecationWarning and
  ResourceWarning for an unclosed FFmpeg stdout BufferedReader after process exit.
  Installed discord.py cleanup drops that pipe reference without explicitly
  closing it on this path. Runtime code was not changed to depend on private
  discord.py process/pipe internals; this remains a dependency cleanup limitation.
- Focused source runs also reported a roughly 0.23-second asyncio slow-callback
  diagnostic while importing/using real discord.py/FFmpeg inside the integration
  test. The final full suite did not emit that diagnostic.
- One intermediate 36-test run emitted a shielded-future DJError diagnostic after
  cancellation even though ownership was observed. The new lifecycle tests now
  capture loop errors and require none; the corrected focused/final runs passed.
- Git warns that LF files may become CRLF under the repository configuration.
  An extra `git -c core.autocrlf=false diff --check` returned 2 by interpreting
  README's Windows CRLF endings as trailing whitespace. Standard `git diff
  --check` uses the repository settings and returned 0; no Git setting changed.
- Two documentation patch attempts failed their context checks and wrote nothing;
  subsequent scoped documentation edits succeeded.

### Final scope checks and limits

Standard `git diff --check` and `git diff --cached --check` returned 0. An in-memory
Python inspection checks changed/untracked paths, parses Python without bytecode,
checks trailing whitespace and local Markdown links, verifies the unchanged demo
SHA-256, and checks for leftover fixture/bytecode directories. No test artifacts
remain. No source/test changes followed the final 104-test run; subsequent edits
only record this evidence.

Final branch/HEAD/upstream/remotes remain as in preflight. The index is empty.
Working-tree changes are the 11 modified and five untracked paths listed above,
all under `panam_dj/`. No tracked or untracked changes outside that scope exist.

The metadata configuration remains off. Real YouTube availability with the
restricted worker is not proven by these tests or by earlier manual experiments.
No separately approved real recording/mapping was supplied or acquired; the
playback path is implemented but real-music acceptance remains a future,
separately authorized check. No broad-coverage or YouTube retransmission claim is
made. Known remaining limits include strict URL forms, local PCM WAV assets only,
metadata failure when provider behavior changes, the original bounded voice
handshake holding its guild lock, unresolved native cleanup requiring operator
reconciliation, the pipe warning, and the PyNaCl version mismatch.

No packages, accounts, credentials, live metadata/media requests, Discord login,
Git staging/commit/push/merge/branch operations, deployment, or files outside
`panam_dj/**` were changed or used for external effects. No formal milestone
acceptance is declared. This delivery ends with implementation and local evidence
for Human Project Owner and independent reviewer assessment.
## DJ-P2.1 REVISION-001 correction evidence (2026-10-27)

Authority: `HAD-DJ-P2-1-REVISION-001-CORRECTION-001`, followed by the separate
Correction Execution prompt. This section records implementation and local
verification for Human Project Owner review, not acceptance or a workflow transition.
Historical implementation evidence above is retained; the stdout warning and
post-discovery mapping behavior described there are superseded by this revision.

### Preflight and exact revision scope

Verified `C:\Panam_APP_DJ`, remote `https://github.com/PHNMNL01/panam-discord-bot.git`,
branch `experiment/dj-panam-poc`, HEAD
`bd1d16e41b90b095af89ad41d7cfe2c4109f669c`, tracking
`origin/experiment/dj-panam-poc`, local divergence `0 0`, and an empty index.
Initial status matched the authorized 11 modified and five untracked DJ files.
Read root AGENTS.md; no nested DJ instructions were found. Inspected current
playback ownership, provider/configuration, tests, DJ architecture documentation
and actual installed discord.py 2.7.1 FFmpegAudio/AudioPlayer source before editing.

Files changed relative to that uncommitted implementation:

- `panam_dj/discord_app.py`
- `panam_dj/sources.py` (already untracked)
- `panam_dj/tests/test_discord_app.py`
- `panam_dj/tests/test_sources.py` (already untracked)
- `panam_dj/README.md`
- `panam_dj/SOURCE_FEASIBILITY.md`
- `panam_dj/VERIFICATION.md`
- Created `panam_dj/tests/test_ffmpeg_cleanup.py`.

No changes to playback.py, configuration, dependency declarations, installed
packages, original audio or any other pre-existing implementation were required.

### Corrections and demonstrated boundaries

The installed FFmpegAudio cleanup could skip communicate() after process exit,
then discard stdout without closing it. The DJ adapter now owns termination/wait,
pipe-thread joining, and explicit stdin/stdout/stderr/input closure. It leaves
shared source references valid until writer threads finish. Concurrent cleanup
is serialized; only completed closure is idempotently successful. Exceptions
reach the existing OwnedSource completion record and remain unresolved in the
manager. Manager generation, authorization, deadlines and cancellation code are
unchanged. The adapter intentionally uses the inspected FFmpegAudio internal
process/thread fields; upgrades require repeating the real-source regressions.

Ten real FFmpeg tests use the existing approved demo and the installed AudioPlayer
with an in-memory transport. They assert process exit (natural exit code 0),
stdin/stdout closure, owned BytesIO closure, no surviving pipe/AudioPlayer threads,
and no ResourceWarning for natural completion, skip, stop, leave, shutdown and
rejected play starts. A blocked stdout.close demonstrates that after() and process
exit can precede resource release: manager.close returns an unresolved guild and
closed remains false until the gate is released and a later close reconciles it.
A synthetic close exception is retained and cannot produce closed=True. A cancelled
concurrent close waits for the shared cleanup; another caller completes with one
disconnect. Gates and thread joins provide synchronization; no test sleeps are added.
ResourceWarnings are recorded and asserted absent, never filtered away as success.

Approved mapping lookup now precedes metadata lookup. Offline tests exercise
unmapped normalized URL variants, playlists, malformed IDs, forbidden hosts,
credentials, ports and schemes through a worker-launch spy and assert zero calls.
Mapped short URLs still normalize to the same video ID/canonical URL and local
media identity. Local catalog requests still bypass the metadata worker.

The worker receives an explicit allowlisted environment: only SystemRoot on
Windows, no inherited entries on other platforms. Code reads only that non-secret
OS location, never copies/enumerates the parent's environment, and fails before
launch when Windows SystemRoot is missing. A real offline subprocess imports
yt-dlp, asserts exclusion of synthetic DJ_PANAM_BOT_TOKEN, unrelated secret,
AWS secret, proxy and PYTHONPATH variables, emits fixture metadata and exits 0.
The parent verifies reaping. No real credential values were read or used.

### Commands and actual outcomes

All commands ran from `C:\Panam_APP_DJ` with the existing `.venv` and `-B`.
Disposable catalog fixtures were authorized and confined to `panam_dj/tests/`.

| Command / inspection method | Actual outcome |
| --- | --- |
| `.\.venv\Scripts\python.exe -B -m unittest discover -s panam_dj/tests -t . -q` before edits | 104 passed, exit 0, 2.330 s; positional-count DeprecationWarning and unclosed-stdout ResourceWarning reproduced. |
| `.\.venv\Scripts\python.exe -B -m unittest panam_dj.tests.test_sources panam_dj.tests.test_ffmpeg_cleanup -v` intermediate focused run | 34 passed, exit 0, 5.642 s; positional-count DeprecationWarning only. One further FFmpeg cancellation regression was then added, and the child environment test extended to import yt-dlp. |
| `.\.venv\Scripts\python.exe -B -W default -m unittest panam_dj.tests.test_ffmpeg_cleanup -v` final FFmpeg coverage | 10 passed, exit 0, 4.957 s; no failures, errors, skips or warnings. |
| `.\.venv\Scripts\python.exe -B -W default -m unittest discover -s panam_dj/tests -t . -q` initial final-suite attempt under restricted Windows token | 119 run, two errors, exit 1, 7.209 s. Both real metadata subprocess tests failed with WinError 5. Other tests passed; no ResourceWarning. |
| Inline unittest diagnostic runner exposing exception context; then the two affected tests alone | Full diagnostic: 119 run, same two errors, exit 1, 7.160 s. Focused diagnostic: two errors, exit 1. Underlying exception was PermissionError / WinError 5. |
| Standalone offline asyncio.create_subprocess_exec probe with the same explicit environment | Exit 1; traceback locates denial at windows_utils.pipe / _winapi.CreateFile for the local stdout pipe, before child process creation or environment application. No provider request. |
| `.\.venv\Scripts\python.exe -B -W default -m unittest panam_dj.tests.test_sources.MetadataProcessTests.test_real_offline_subprocess_is_reaped panam_dj.tests.test_sources.MetadataProcessTests.test_real_worker_subprocess_excludes_synthetic_parent_secrets -v` with approved sandbox escalation | Both passed, exit 0, 0.429 s; no warnings/skips. The execution context changed; the allowlist and tests were not weakened. |
| `.\.venv\Scripts\python.exe -B -W default -m unittest discover -s panam_dj/tests -t . -q` with approved sandbox escalation | **119 passed**, exit 0, 7.741 s; no failures, errors or skips. Two occurrences of installed discord.utils positional-count DeprecationWarning; no ResourceWarning or unhandled-thread diagnostic. |
| `.\.venv\Scripts\python.exe -B -W default -m panam_dj.smoke` | **103 Opus packets**, exit 0, no warnings; no voice connection. |
| `.\.venv\Scripts\python.exe -B -m panam_dj --config config.local.json --check` | Exit 0; one track and one guild valid. IDs not printed. No token read, lookup, login, sync or voice connection. |

The final suite contains the original 104 tests plus 15 new regression tests.
The remaining DeprecationWarning belongs to installed discord.py's markdown
escaping under Python 3.14; no installed package was patched or downgraded.
PyNaCl and its previously recorded dependency-range discrepancy remain unchanged.

Local evidence is limited to the provisioned Windows environment, fake Discord
transport, bounded offline subprocesses and existing demo audio. It does not prove
network metadata availability, Discord/DAVE negotiation or live acceptance. No
YouTube audio retrieval is implemented. An OS operation that cannot terminate or
close still retains unresolved ownership; no universal OS-failure guarantee is made.

Git integrity is checked separately for tracked changes and every untracked DJ
file. The initial diff check found an added blank line at the end of this evidence
section; it was removed. The first untracked-file checker incorrectly treated
git diff --no-index's normal difference exit 1 as failure; the corrected checker
also examines whitespace diagnostics and covers all six files. Final tracked
diff check exits 0; LF-to-CRLF notices remain for 11 tracked files. Final status
is 12 modified and six untracked DJ files, with no staged or unrelated changes
and the same branch/HEAD. SHA-256 comparison to preflight confirms precisely the
seven modified paths and one created path listed above; both inspected installed
Discord source files and AGENTS.md are unchanged. No staging, commits, pushes,
merges, branch operations, installations, live services, external media retrieval,
credential access or changes outside `panam_dj/**` were performed.

## DJ-P2.1 REVISION-002 targeted ownership corrections (2026-10-27)

Authority: `HAD-DJ-P2-1-REVISION-002-CORRECTION-001`, followed by the separate
Targeted Correction Execution prompt. This is implementation and deterministic
verification evidence for Human Project Owner review, not formal acceptance.

Preflight verified checkout `C:\Panam_APP_DJ`, branch `experiment/dj-panam-poc`,
HEAD `bd1d16e41b90b095af89ad41d7cfe2c4109f669c`, origin
`https://github.com/PHNMNL01/panam-discord-bot.git`, upstream divergence `0 0`,
empty index, 12 modified and six untracked DJ implementation files. SHA-256
comparison to the final-review snapshot found no baseline drift across its 31
files. Root AGENTS.md and relevant DJ source, architecture notes, existing failure
reproductions, installed discord.py constructor and asyncio exit-wait implementation
were inspected before runtime edits. No dependency or external authority changed.

### Exact revision changes

- `panam_dj/sources.py`: process exit is awaited in finally even if stdout draining
  raises. A failed kill and broken stdout cannot bypass the existing retained
  cleanup/preparation task. asyncio.Process.wait observes the transport's exit
  notification; no additional kill or cleanup attempt is issued by later close.
- `panam_dj/playback.py`: SourceConstructionError carries a partial resource through
  the factory failure, with a one-use ownership transfer. The manager registers
  that resource in the existing source registry before scheduling its off-loop
  cleanup. No functional audio read interface is needed. Failed attempts remain
  observable through the existing source completion/error record. Construction
  cancellation/interrupt causes propagate after cleanup ownership is secured.
- `panam_dj/discord_app.py`: failed construction transfers its partially initialized
  resource instead of attempting unregistered synchronous cleanup. Successful
  construction and the existing FFmpeg cleanup implementation are unchanged.
- `panam_dj/tests/test_sources.py`: two combined drain/kill-failure regressions,
  covering retained ownership, later observed exit and caller cancellation.
- `panam_dj/tests/test_ffmpeg_cleanup.py`: five construction regressions, covering
  termination failure, pipe-close failure, ordinary successful cleanup, one-use
  ownership transfer and concurrent/cancelled close during pending cleanup.
- `panam_dj/README.md` and `panam_dj/VERIFICATION.md`: scoped lifecycle/evidence updates.

All seven files already existed, including the previously untracked source/test
files. This revision creates no additional file. Local settings, private media,
mapping configuration, dependency declarations and installed packages are unchanged.
No provider capability, URL grammar, playback permission or audio retrieval was added.

### Reproduction and verification

New regressions were run against the pre-correction runtime first. They reproduced
the original false-success outcomes: manager.close returned no unresolved guilds
for failed metadata termination/draining and failed FFmpeg construction/cleanup.
The cancellation regression timed out because process.wait was never entered.
All synthetic failures are repaired/released in test teardown; no real child is
orphaned by these injected-failure tests. Actual FFmpeg tests remain in the suite.

| Exact command (from repository root) | Actual result |
| --- | --- |
| `.\.venv\Scripts\python.exe -B -W default -m unittest panam_dj.tests.test_sources.MetadataProcessTests panam_dj.tests.test_ffmpeg_cleanup.ConstructionOwnershipTests -k revision002 -v` before runtime correction | Five tests: one passed, three failures and one error; exit 1, 1.287 s. This is the expected reproduction run, not a passing verification. No warnings. |
| Same focused command immediately after runtime correction | Five passed, exit 0, 0.409 s; no failures/errors/skips/warnings. |
| Same focused command after adding one-use transfer and concurrent-close checks | **Seven passed**, exit 0, 0.377 s; no failures/errors/skips/warnings. |
| `.\.venv\Scripts\python.exe -B -W default -m unittest panam_dj.tests.test_sources panam_dj.tests.test_ffmpeg_cleanup panam_dj.tests.test_preparation -q` | **58 passed**, exit 0, 8.338 s; zero failures/errors/skips. One installed discord.utils positional-count DeprecationWarning; no ResourceWarning. |
| `.\.venv\Scripts\python.exe -B -W default -m unittest discover -s panam_dj/tests -t . -q` | **126 passed**, exit 0, 10.766 s; zero failures/errors/skips. Two occurrences of the existing positional-count DeprecationWarning; no ResourceWarning or unhandled-task/thread diagnostic. |
| `.\.venv\Scripts\python.exe -B -W default -m panam_dj.smoke` | **103 Opus packets**, exit 0; no warnings or voice connection. |
| `.\.venv\Scripts\python.exe -B -m panam_dj --check` | Default shipped configuration/catalog valid: one track, one placeholder guild; exit 0, no warnings, credential access or network. Personal configuration/media were not loaded. |

The two broader suites used approved sandbox escalation for Windows local asyncio
pipes, whose restricted-token failure had already been established in REVISION-001.
Only existing offline tests and authorized disposable DJ fixtures were executed.
The final total is the previous 119 plus seven new regression methods.

Metadata combined-failure assertions establish: close returns `(1,)`, closed is
false, the child returncode is unobserved, one preparation stays owned and the
request remains pending. Repeated close still leaves exactly one kill and one wait
attempt. After an explicit exit event, the request settles, subsequent close returns
`()`, closed becomes true and the preparation registry is empty. Cancellation of
the caller does not release that wait early.

Partial-construction assertions establish: termination or pipe-close failure leaves
one source owned, its cleanup exception recorded, closed false and the guild listed
as unresolved. Concurrent/repeated close does not issue another kill. Ordinary
construction failure with successful cleanup closes stdin/stdout/input and removes
ownership. A blocked cleanup remains owned while concurrent close callers wait;
cancelling one caller does not detach cleanup, and releasing it permits successful
closure with one disconnect and one kill/wait attempt. The one-use transfer rejects
a second claim. No incomplete resource is passed to voice.play.

The complete suite also retains actual FFmpeg/AudioPlayer coverage for normal
completion, skip, stop, leave, shutdown and rejected playback starts, and the existing
FIFO, voice authorization, stale-result/callback, metadata-only, URL/mapping,
environment-isolation and catalog/hash tests. No warning was suppressed to obtain
a passing result. These tests do not establish live provider or Discord behavior.

### Git hygiene and remaining limits

Read-only ignore checks confirm `.env`, `config.local.json`, `youtube.local.json`,
`catalog/catalog.local.json` and `catalog/audio/owner_poc.wav` remain excluded.
The personal asset/configuration paths are absent from the candidate commit set.
The existing original demo remains the only tracked audio asset, unchanged.
The tracked root `.gitignore` line 9 ignores nested `.gitignore` files, so
`panam_dj/.gitignore` is itself ignored and untracked. To include that exact existing
ignore policy in the eventual checkpoint, the human must explicitly run:

```powershell
git add -f -- panam_dj/.gitignore
```

That is a future human-controlled staging instruction, not an executed command or
permission for the agent to stage. Do not force-add private configuration or media.
The proposed checkpoint set is the 12 modified/six untracked implementation paths
in Git status plus this single ignore-policy file: 19 paths total. No other ignored
file belongs in it. This revision does not alter the ignore rules.

Remaining limits: FFmpeg cleanup still uses inspected discord.py internal fields;
upgrades require real-source regressions. Failed source cleanup remains reported
and is not automatically retried; operator reconciliation is still required for a
failed attempt. Unconfirmed metadata exit can intentionally keep cleanup pending
beyond the shutdown deadline. The installed PyNaCl declaration mismatch and
discord.py DeprecationWarning remain documented and unchanged. The POC still uses
YouTube metadata and approved local audio only; no networked acceptance is claimed.
