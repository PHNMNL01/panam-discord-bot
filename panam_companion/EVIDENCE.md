# Experiment evidence and resumable checkpoint

Final live-batch state, 2026-09-13: **PARTIAL — practical Discord conversation
accepted by the human; testing ended at their request.** Authentic audio is
human-confirmed. Numerical latency and the full predefined benchmark are not
verified. All owned bot/Live/panel processes are closed; no further live calls.
See the final BENCHMARK.md table. The dated records below preserve earlier states.
Current closure authorization supersedes the earlier no-commit/no-push checkpoint:
the human explicitly authorized this phase's Companion-only commit(s) and exactly
one non-force push to origin/experiment/realtime-voice-discord-poc. No other branch,
PR, merge, tag, deployment or live operation is authorized.
Automatic approval review initially rejected the attached document as Git
authority. The human then directly confirmed the exact staging/commit/single-push
exception to AGENTS.md. Execution results and final SHA are reported after the
commit containing this document; see the recorded gate history below.

Initial implementation stage, 2026-09-11: no real provider request, Discord login,
microphone capture or human audio occurred then. Initial master-task GO authorized
offline work; the distinct live GO arrived later on 2026-09-13.

## Repository and tools

- Root `C:\Panam_APP_astra`, branch `experiment/realtime-voice-discord-poc`.
- Fixed baseline `2b7473f48d448628e956c0e03ce676b3e0009003`, initially clean;
  branch has no upstream. Origin `https://github.com/PHNMNL01/panam-discord-bot.git`.
- Independent clone and isolated CPython 3.14.5 AMD64 `.venv` from prior bootstrap.
  Current metadata records 53 distributions. No new installation was necessary.
- Tools actually used: PowerShell/explicit experiment Python, Git, public web docs,
  file patches, read-only helper agents. CUA inventory exposed only the Codex
  in-app browser, no native-app surface. No account browser session was used.
  Browser microphone/Windows-localhost capability is NOT VERIFIED and is unnecessary
  for the chosen Discord route. Physical audio confirmation requires the human.
- Main model/reasoning: **HUMAN-SELECTED / NOT TOOL-VERIFIED**. The user's “Astra
  Ultra” document does not prove the selected runtime setting. No exact token or
  active/wait elapsed-time totals were exposed/recorded; none are invented.
- Helper1 `/root/discord_research`: read-only installed/upstream DAVE/codec research,
  including a permitted synthetic library-only Opus smoke. Recommended isolated
  DAVE receive adapter, no fallback. Model/reasoning NOT TOOL-VERIFIED.
- Helper2 `/root/final_review`: read-only correctness/security review. Found owned
  voice-client loss on cancelled handshake and default WebSocket redirect following.
  Both fixed and regression-tested. Model/reasoning NOT TOOL-VERIFIED. No recursive
  helpers, helper writes, credentials, account sessions or live calls.

## Activity and actual results

1. Confirmed branch/HEAD/status/environment and applicable instructions. Preserved
   all existing source. Original deployment/runtime paths were not inspected or used.
2. Read current Live protocol/prompt/lifecycle/delegation/pricing documents and actual
   installed schemas. Normal documentation pages worked; some Markdown/direct links
   returned internal errors and some search queries were empty. No API access inferred.
3. Source evidence identified the legacy Opus/PCM mismatch and missing receive-DAVE
   step. Chose new isolated Discord adapter, stateful PCM conversion and raw Live
   WebSocket. No alternate-model substitution or library patch.
4. Chose client-owned Luna backend for per-request reservations and stale-result
   handling. Found no confirmed foreground cancellation contract; requests finish
   serially or become explicitly uncertain. Web search and all tools disabled.
5. Preregistered Q01–Q20, acceptance cases, percentile rule and human audible-timing
   method before any live measurement. No observations or latency samples yet.
6. First focused suite: 32 tests, **31 passed / 1 error**, caused by the import-side-
   effect test reloading the SafeError class in the test process. Replaced reload
   with a clean child interpreter. This was a test harness error, not a live failure.
7. A subsequent sandboxed test execution produced **35 errors** from temporary-file
   access/cleanup denial. A narrowly scoped `require_escalated` rerun of only the
   offline suite succeeded: **32 tests, OK, exit0**. No sandbox policy changed.
8. Added review fixes and further benchmark/redirect/cancelled-handshake tests:
   **38 tests, OK, exit0**, in the same scoped execution. `pip check` reported
   **No broken requirements found**, exit0. Exact commands are in README.
9. `python -B -m panam_companion --check` reported missing local config as expected;
   it did not authenticate. A user-filled `.env` has not been created or read.
10. Follow-up static review confirmed both fixes and flagged a timestamp-labeling
    nit. Preserved original startup timestamps while retaining later segment
    markers. Added transcript readiness, backend timeout and resolved-model tests:
    **42 tests, OK, exit0**. The resolved-model test exposed an unnecessary 8-second
    finalization wait: a local protocol error had ended the receiver too early.
11. Kept the receiver alive to drain `session.closed` after local protocol/audio
    errors; added malformed-audio finalization regression. Final focused suite:
    **43 tests, OK, exit0**. Real account, audio and transport behavior still untested.
12. Empty latency template analyzer: **exit0**, 20 NOT TESTED, n=0, median/p95/max
    null, overall NOT VERIFIED. Default `.env`, `.venv` and runtime counter paths
    confirmed ignored. No template secrets or user configuration were staged.
13. Missing-config CLI check explicitly captured its native **exit2**. A no-index
    whitespace-check wrapper initially failed because it classified 17 normal
    LF→CRLF conversion warnings as errors. A raw-LF check then identified CRLF in
    the two generated version records; only those new files were normalized to LF.
    Staged `git diff --cached --check` passed with Git's normal text conversion.
    No baseline line endings or Git configuration changed. Two documentation
    patches with unused nonmatching anchors were rejected without partial writes;
    removing the stale hunks resolved that editing error.

The focused suite exercises native Opus shape, resampling continuity, bounded
queues, generation invalidation, local mute, pacing, protocol errors/disconnect,
duration guard, serial/stale backend results, no context carryover, file-lock and
crash reservations, config conflicts, receive/control/broadcast gates, DAVE call
ordering, rejected redirects and ownership through cancelled voice handshake.
It cannot establish acoustic quality, real encryption negotiation, actual permissions,
microphone denial behavior, provider finalization or audible timings.

## Human assistance and next checkpoint

Human assistance so far: **permission** — explicit master-task GO, then an explicit
`git add`/local-commit exception for these 19 files after automatic review rejected
the original authorization evidence. No human code repair/design choice,
credentials, physical audio confirmation or budget extension.
The next required intervention combines credentials/configuration, readiness
permission and hardware confirmation for the predefined live batch. No secret
should be supplied in conversation.

Before any live command, human fills `panam_companion/.env` with separate test
identities/key/token; safe `--check` can report configured/missing/conflicting and
IDs only. Confirm exact bot/guild/voice/control/user destinations. Then explicitly
approve GPT-Live-1/Marin, Luna low/default/no-tools, Discord+OpenAI audio flow,
≤5 min/session, ≤10 min total and USD1 total. Credentials alone are not GO.

After GO: log in once, Start manually, execute fixed questions and context/
interruption/lifecycle cases while collecting human audible observations. Stop
at the allowance even if incomplete. Retain failed trials and report PARTIAL when
necessary. No unattended continuation or scheduled task exists.

## Final local review fields

All 19 new source/documentation files are reviewed and secret-pattern checked.
Source AST parsing passed, the focused suite passed43, and baseline tracked diff
is empty. Runtime artifacts stay ignored. Automatic approval review rejected
the exact `git add` twice: first citing AGENTS.md's prohibition and interpreting
GO as insufficient; then rejecting the attached specification and retrieved
prior approval context as untrusted authorization evidence. No bypass attempted.
The second attempt followed a fresh check of the master's explicit commit grant
and the preceding question that explicitly included local commits. The human then
explicitly approved `git add` and local `git commit` for all 19 new Companion files
on this branch as an exception to AGENTS.md, excluding secrets/runtime and push.

Implementation committed under that exact approval:
`3f9027ce04a08c343ff33463e55f45ff98cb1e06`
(`Add isolated Discord GPT-Live companion experiment`). Its parent is the fixed
baseline. The later live-test GO remains outstanding.
The first staging attempt after the direct exception succeeded, exit0. Its only
warnings were the expected Windows LF→CRLF conversion notices. The exact staged
list contains all 19 additions below, with no modifications/deletions, credentials
or runtime paths; staged whitespace check passed.
Post-commit `git status --short` was empty and `git diff --check` passed, exit0.
The complete baseline-to-HEAD diff contains only these19 additions. No existing
application, requirements, instruction or Development Loop path changed.
`.env`, `.venv` and runtime budget paths were confirmed ignored. This evidence
update is a follow-up documentation commit; use `git log -2 --oneline` for its
identity without attempting to embed its own hash.

Exact proposed added paths, all under `panam_companion/`:

- `.env.example`, `__init__.py`, `__main__.py`
- `config.py`, `budget.py`, `audio.py`, `live.py`, `discord_adapter.py`
- `benchmark.py`, `latency-template.csv`
- `requirements.txt`, `constraints.txt`, `environment.json`
- `tests/__init__.py`, `tests/test_companion.py`
- `README.md`, `RESEARCH.md`, `BENCHMARK.md`, `EVIDENCE.md`

No push, PR, tag, branch change, original-application change, baseline requirement
edit, account change, global install, policy/firewall change or Development Loop
action performed. No application process or paid session was started. Test child
processes completed. Only ignored test fixtures/counters may remain under the
module's runtime directory; they contain synthetic data. There is no unattended
task or running demonstration to stop.

## Assisted configuration preparation — 2026-09-11

Resumed on `experiment/realtime-voice-discord-poc` at
`747f9207325953441566aadb360cf9b399d35dca`, clean tracked worktree, no upstream,
unchanged origin. The human explicitly limited this stage to readiness; live GO
is outstanding. No additional helpers were started.

Actual browser capability: Codex in-app browser through CUA; no native app or
other browser surface was exposed. The portal was opened without reading a
credential screen and handed to the human. The human confirmed return to the
Applications list and supplied the name PANAM. A stale tab handle failed once;
browser inventory located the human's current Applications tab and restored
read-only access. The list shows PANAM, application ID 1508847632498299025.
The human subsequently identified it as the original application and forbade
reuse/modification. It was not opened or changed. The human authorized a separate
Companion application/bot and invitation only to their designated test guild
1485632710931255406, with voice channel 1485632711367327806 and minimum POC
permissions. They supplied control channel 1508898355051237578 and consenting
user 164441949099524096. These four IDs are human-supplied, not API-verified.
Only the new bot's user ID remains missing.

The agent submitted creation of "Panam Companion POC" once, under that explicit
approval. The portal remained on the Applications URL without a confirmed new
application. No duplicate request was made. The browser was handed back for the
human to resolve any CAPTCHA/MFA or report a non-secret error. The human confirmed
completion of a human-approval step and return to General Information. The agent
then verified "Panam Companion POC", application ID 1547935699158827089, with
zero server/user installations reported by the portal (approximate daily counts).
No token page was opened or captured. An invite is being prepared for the exact
test guild with `bot applications.commands`, `integration_type=0`, locked guild
selection, and permission bitfield 3146752 (View Channel, Connect, Speak), using
the documented [bot authorization flow](https://docs.discord.com/developers/topics/oauth2)
and [permission bits](https://docs.discord.com/developers/topics/permissions).
Invitation completion and effective channel permissions are not yet verified.
The invite first opened a Discord desktop handoff. Selecting its visible
"Continue to Discord" control redirected the browser to `/login`. No login DOM
or screenshot was read; the human was asked to authenticate and return to the
invite, or report if installation already completed in the desktop app. No second
installation was attempted.

The actual `.env.example` and configuration implementation were inspected.
Five ID meanings and official copy paths were explained to the human. A local
`.env` was created once from the secret-free template using exclusive file
creation; the operation would preserve an existing file without reading it.
`git check-ignore -- panam_companion/.env` confirmed exclusion. Subsequent human
edits are not inspected. The human has not yet confirmed configuration readiness;
no `--check`, bot login, provider request or microphone capture was run in this
readiness stage.

S01 was proposed and preregistered before results: short end-to-end speech,
mute/unmute and Stop, consuming the same USD1 / 600-second proposed live batch.
The existing 20 questions, scoring, uncertainty requirements and thresholds are
unchanged. Status is NOT TESTED, not an acceptance substitute.

Additional human assistance: **credentials/account access** — the human completed
the requested portal handoff and confirmed a safe page (whether login/MFA was
necessary is not observed); **configuration identification** — identified the
original PANAM and supplied four test destination IDs; **permission** — authorized
separate app/bot creation and a test-guild-only invitation; **account verification**
— completed the human-approval step during app creation. No secret values were
received or observed. No human code
repair, physical audio result or budget extension occurred.
Waiting on account/resource clarification is separate from active preparation;
elapsed time is not estimated.

Documentation-only verification: `git diff --check` passed (exit0), with the
existing Windows LF-to-CRLF conversion warnings on the three edited documents.
The baseline comparison outside `panam_companion/` was empty. Only README.md,
BENCHMARK.md and EVIDENCE.md changed; no runtime/source logic or test thresholds
changed, so no unrelated test rerun was performed.

Staging these three documents succeeded. Staged whitespace check passed and the
exact index contained only their modifications. Automatic approval review then
rejected the local commit: it treated the earlier permission for 19 new files as
not covering later modifications to these three documents. No commit occurred
and no workaround was attempted. The human then explicitly approved staging and
a local commit of subsequent changes to only README.md, BENCHMARK.md and
EVIDENCE.md, including this rejection record, without secrets/runtime or push.
One attempted evidence patch had a nonmatching text anchor and was rejected
without changing files; correcting the anchor resolved it.

## Installed identity and local configuration diagnosis — 2026-09-11

Resumed at `8b76764a5ed44c746cbbb5e8aea9164fe383a7e5`, clean tracked tree,
same experimental branch/origin, no upstream and no changes outside Companion.
That commit completed the explicitly approved three-document follow-up.

The human confirmed installation. The agent navigated the existing user-owned
Discord browser tab to guild 1485632710931255406 / control 1508898355051237578,
opened the member list, and found Panam Companion POC (APP). Its profile showed
Panam Companion POC#9456 and one mutual server. The profile's overflow action was
only Copy App ID, so it was not used as proof of a bot user ID. Instead, the agent
opened the installed member's context menu, whose visible Copy User ID action
had DOM id `user-context-devmode-copy-id-1547935699158827089`.
Thus PANAM_TEST_BOT_ID=1547935699158827089 comes from the installed user identity,
not an inference from General Information. No clipboard, token, cookie, browser
internal state or API authentication was used. The menu was dismissed afterward.
Two transient/stale tab attempts were unavailable; using the existing user-owned
tab resolved access. Resetting the tool session recovered the documented right-
click API; it did not change Discord configuration or account state.

Human-supplied test destinations match the earlier four IDs. The `.env` file's
existence alone was checked before readiness confirmation; contents were not
displayed or overwritten. The human explicitly confirmed configuration readiness
and authorized only `python -B -m panam_companion --check`. Its first execution
returned exit2 with the original generic configuration error, no network work.

The agent improved only local validation diagnostics in config.py. Validation
requirements and precedence are unchanged. Errors report fixed known field names
and safe status flags; unknown keys and all values remain suppressed. Three
synthetic tests cover missing IDs, secret/unknown-key redaction and duplicate IDs.
`python -B -m unittest panam_companion.tests.test_companion.ConfigTests -q`
passed all 7 tests, exit0. No human code/design repair was involved.

The same documented `--check` was then repeated, exit2. It reported `missing` for
all five test IDs; neither secret field failed its local shape check. This does
not verify either credential remotely. The human was given the exact five ID
assignments and asked to fill only those fields, preserving the existing secrets,
then confirm before another `--check`. The human then confirmed completion of the
ID fields and explicitly requested that same offline check. It passed, exit0,
with bot=1547935699158827089, guild=1485632710931255406,
voice=1485632711367327806, control=1508898355051237578,
user=164441949099524096. API access, token validity at Discord, effective
permissions and sound remain NOT VERIFIED; no live connection was attempted.

Current OpenAI public pricing was checked again: Live $0.05/min, billed per
second; Luna $0.20/M input, $1.20/M output, 1.25x input for cache writes. Sources:
[pricing](https://developers.openai.com/api/docs/pricing) and
[Luna](https://developers.openai.com/api/docs/models/gpt-5.6-luna). The proposed
USD1 batch, $0.90 conservative reservation and $0.10 margin remain unchanged.
No live GO, bot login, microphone capture or paid API call occurred.

The human separately authorized exactly one local commit of the current tested
diagnostic fix, excluding documentation and any further implementation changes.
Fresh review found an empty index and precisely the previously tested changes in
config.py and tests/test_companion.py. Only those two paths were staged; staged
blob identities matched the reviewed versions, secret-pattern findings were zero,
and staged whitespace check passed. Commit
`7ac6800f8ba943c82a4a720f8d1bcd9b45220d95` contains only those two files.
No push occurred. README.md, BENCHMARK.md and EVIDENCE.md remain unstaged outside
that commit, including this updated readiness record. Baseline comparison outside
Companion is empty; `.env` remains ignored. Ordinary LF-to-CRLF Git warnings were
the only whitespace notices. The unchanged seven-test result remains applicable.

## First authorized live-batch preflight — 2026-09-13

The human explicitly approved Discord + OpenAI, USD1, 5 minutes per session,
10 minutes aggregate, with the smoke test first. This is the first live GO.
Preflight found branch experiment/realtime-voice-discord-poc at
7ac6800f8ba943c82a4a720f8d1bcd9b45220d95; only the three known documentation
edits were present. No baseline path outside Companion changed. The documented
offline `--check` passed, exit0, with all five expected IDs. The actual batch
budget.json was absent; no prior Companion Live reservation was present.

Attempt PRE01 used the documented command
`python -B -m panam_companion --live-approved --metadata` in the prepared venv.
The agent entered CONNECT under the explicit live GO. Startup exited with a
generic suppressed error (tool-reported exit1), before readiness, voice or any
OpenAI session. No unattended process remained and no Start was requested.

The agent added `safe_startup_failure` in __main__.py: an allowlist maps known
exception type names to fixed diagnostic flags; unknown names, messages, args,
HTTP response bodies and tokens are suppressed. Two new synthetic tests check
Discord LoginFailure classification and suppression of unknown exception names
and bodies. `python -B -m unittest
panam_companion.tests.test_companion.StartupDiagnosticTests -q` passed 2/2, exit0.
`git diff --check` passed with ordinary LF-to-CRLF conversion warnings.
No limits, permissions, authentication source, provider configuration, recording
policy or test thresholds changed. This diagnostic fix is not committed; the
earlier authorization was for exactly one commit and has already been used.

Attempt PRE02 repeated the same documented startup once with the new diagnosis.
After CONNECT it reported `discord_token_rejected=true`, then exited (tool exit1).
This is evidence that Discord rejected the configured token, not evidence about
the OpenAI key. Further authentication attempts are paused pending local token
correction. The human was asked to correct only PANAM_DISCORD_BOT_TOKEN from
their dedicated bot, or explicitly approve a test-app-only token reset before a
human handoff. No credential page, token value or original application was read.

Both owned CLI processes ended; no voice connection, OpenAI request, recording,
audible smoke result or benchmark sample occurred. Companion API spending is $0
and aggregate Live time is 0/600 seconds. No budget counter was reset or deleted.
The approved batch remains available once this prerequisite is resolved. Human
assistance so far in this stage is permission; credential correction is pending.
No new helper, Git mutation, original Panam or Development Loop action occurred.

The human explicitly approved Reset Token only for Panam Companion POC,
application 1547935699158827089. The agent navigated the existing browser tab
directly to that application's `/bot` page without reading its DOM or screenshot,
marked it for handoff, and requested human-only reset/MFA and local replacement
of PANAM_DISCORD_BOT_TOKEN. The human must return to General Information and
confirm completion before agent browser inspection/authentication resumes.
The agent did not perform the reset or inspect any secret. Completion is pending.
Public OpenAI pricing was rechecked on 2026-09-13 at the same pricing URL: Live
$0.05/minute billed per second, Luna Standard $0.20/M input, $0.25/M cache writes,
$1.20/M output; the approved batch estimate remains unchanged.

The human confirmed new token storage and reported return to General Information.
The browser metadata still showed `/bot`; the agent navigated away without reading
that page, then resumed inspection only in the test Discord channel. The offline
check passed again, exit0. PRE03 used the same documented live command and CONNECT;
it reached "Bot připraven", proving successful bot authentication, exact bot/guild
identity gates and guild-only command registration. No extra approval was sought
for this unchanged batch after the credential prerequisite was corrected.

The human confirmed presence in General (voice ID 1485632711367327806) with
microphone and headphones. The agent selected the command specifically belonging
to Panam Companion POC. Before dispatching Start, a 45-second in-session tool
watchdog was armed to send Ctrl+C to only the owned CLI session. No persistent
scheduled task or external helper was created. The CLI reported "poslouchá".
The agent supplied exactly the preregistered S01 question and requested a human
audible verdict. The human responded "ano" to hearing a relevant Czech answer.

The watchdog ended S01 before mute/unmute or Discord Stop controls were exercised.
The CLI reported finalized=true, fault=null, provider_seconds=27.0, 418 accepted
audio frames, zero crypto failures and zero input drops, followed by "Ukončeno".
The process exited (tool exit1 following Ctrl+C). Its single long metadata JSON
line was partly wrapped/redrawn by Windows PTY output; no damaged event text was
reconstructed as precise evidence. The intact summary fields and validated budget
counters were used. Runtime budget inspection returned seconds=33, remaining=567,
reserved_usd=0.0275, uncertain=false, observed_voice_seconds=27.0 and backend_calls=0.
No counter was reset. Safe summary metadata was written to ignored
runtime/smoke-2026-09-13.json; no human audio or conversation content was retained.

The human has no stopwatch and cannot currently provide audible-latency timings.
The agent offered a local panel for two explicit human marks per question; no
measurement method or acceptance threshold has been relaxed. Q01-Q20 remain
unperformed while the marking prerequisite is resolved. Live is closed, with no
owned bot process remaining. Human assistance now includes credential reset/entry,
hardware preparation and an audible S01 verdict, not code repair.

## Human annotation panel — 2026-09-13

The human accepted a local two-mark panel because no stopwatch was available.
No new live call or Q01-Q20 trial was started during its preparation. Pre-edit Git
inspection matched branch experiment/realtime-voice-discord-poc, HEAD
7ac6800f8ba943c82a4a720f8d1bcd9b45220d95, no upstream, expected origin and the five
known modified files. No new helpers, Git mutations or production actions occurred.

Added timing_panel.py, timing-panel.html/js and tests/test_timing_panel.cjs inside
Companion. The read-only server binds 127.0.0.1, serves exactly two static assets,
checks Host/Origin and disables browser microphone, camera, remote connections,
framing and form submission. It imports no configuration or Live/Discord modules.
No .env read, secret, audio capture or provider request exists in the panel.
Its own local browser storage contains only fixed IDs/status codes and human marks;
practice is isolated from the 20 immutable original-trial slots. The analyzer now
recognizes the actual `human_monotonic_panel` method with unchanged uncertainty
and pass/fail thresholds. Added a focused analyzer regression for this method.

`node --test panam_companion/tests/test_timing_panel.cjs` could not spawn its test
worker under the sandbox (EPERM); this was a runner restriction, not a test verdict.
Running the same six node:test cases in-process with
`node panam_companion/tests/test_timing_panel.cjs` passed 6/6, exit0. Tests cover
exact preregistered questions, monotonic intervals, duplicate/late marks, failure
preservation, reload/incomplete rows, and completion without adding trials.
`python -B -m unittest panam_companion.tests.test_companion.BenchmarkTests -q`
passed 4/4, exit0. Seven HTTP checks against the owned local server passed: the two
allowed assets, denied .env/traversal paths, bad Host/Origin, and rejected POST.
CSP and Permissions-Policy checks passed. Requests to denied paths never opened
those files. No generic directory/file server was used.

The in-app browser reached the loopback panel. The agent exercised practice only:
one attempt ended before the second mark while another check ran; the disabled
button safely rejected the delayed action. A repeated synthetic E/A practice
passed, all actual Q01-Q20 stayed NOT_TESTED. Practice status wording was corrected
to distinguish successful marks from incomplete practice, then the 6+4 tests and
browser E/A check passed again. The page was refreshed to clean practice state and
handed to the human. Their actual control/hardware readiness reply is pending.
Only the unpaid loopback panel process is running for this explicitly requested
handoff; all bot/Live sessions remain closed. Remaining batch: 567 seconds,
reserved USD0.0275, uncertain=false. No live timing results have been fabricated.

The human confirmed panel readiness. The existing Discord participant showed
Muted; before paid Start the agent requested hardware unmute and received
"Mikrofon zapnuty". This is physical audio cooperation, not human code repair.
The documented live command and CONNECT started only the dedicated bot. S02 then
exercised Start/Mute/Unmute in the permitted guild/control channel and received
the expected listening/muted/listening states. The agent instructed Q01-Q20.
A read-only panel inspection subsequently still showed practice and zero original
rows started. The agent dispatched Discord Stop; authoritative Live finalization
was true, fault null, then the bot process was closed by Ctrl+C (tool exit1).

S02 used 171 locally accounted seconds, 147 provider voice seconds, 342 input
frames, no input drops/crypto failures and no backend calls. Aggregate Budget
inspection returned 204 seconds, USD0.17, 396 seconds remaining and uncertain=false.
No counter was edited/reset. The metadata record controls-2026-09-13.json contains
only safe flags/counts and marks human-spoken question IDs pending reconciliation.
UI control and handoff waiting consumed much of this session; agent overhead is
not hidden or treated as measured response latency. The human was asked to report
any Q01-Q20 already spoken so unmeasured originals are not silently replaced.
Only the requested unpaid loopback panel remains running. A request to show its
existing tab in Codex returned queued; actual foreground visibility is not proven.

## Closure at the human's request — 2026-09-13

The human reported pressing E/A but seeing no recorded result, confirmed natural
conversation comparable to a human Discord participant and subjectively immediate
responses, and asked to consider the test finished. The agent closed the live
batch rather than spend its remaining allowance. This is a human functional and
usability verdict, not numerical timing evidence or completion of each fixed case.
No per-question IDs/count were supplied; the report was not converted to20 passes.

Read-only final panel inspection returned initial practice, disabled Start/E/A,
no marks and all20 UI rows NOT_TESTED. During S02 its earlier observed state was
completed practice. Source inspection confirms E/A handlers silently ignore
disabled measurement actions. This is consistent with failure to transition from
practice to measured trials; actual key focus/history cannot be reconstructed.
The agent owns that coordination/measurement shortfall. No new implementation
change or live retest was made after the human ended testing. No timings, success
percentages or millisecond values were invented. No human code repair occurred.

Safe observed CSV, conservatively unresolved final CSV, and batch-closure JSON
were written with exclusive-create mode in ignored Companion runtime. Original
case slots in the final CSV are NOT_VERIFIED with occurrence/marks unknown,
preserving uncertainty rather than falsely declaring them all unperformed or
successful. The final analyzer returned NOT VERIFIED,20 unverified,n=0 and null
median/p95/max (exit0); its zero attributable successes is not zero conversation
success. No existing counter, human file, secret, raw audio or transcript was read
or overwritten. Budget remains204 seconds/USD0.17/uncertain=false,396 unused.

The owned timing server's terminal was stopped by Ctrl+C (tool exit1) and its
agent-created browser tab was closed. The user-owned Discord tab was preserved.
Both bot processes were already exited; provider finalization was confirmed for
S01 and S02. No paid/background demonstration remains running.

Final offline verification:
- `python -B -m unittest panam_companion.tests.test_companion -q`:49 tests PASS,exit0.
- `node panam_companion/tests/test_timing_panel.cjs`:6 tests PASS,exit0.
- Final latency CSV analyzer: NOT VERIFIED,exit0, as described above.
Earlier HTTP panel checks passed7/7; no additional live/provider tests were run.

Git remains branch experiment/realtime-voice-discord-poc at
7ac6800f8ba943c82a4a720f8d1bcd9b45220d95, no upstream and unchanged origin.
The prior one-commit diagnostics approval was consumed by that commit. No further
staging/commit/push was performed. Remaining source/docs are intentionally
uncommitted; therefore the master's committed-deliverables condition is not met.
Modified: README.md, BENCHMARK.md, EVIDENCE.md, __main__.py, benchmark.py,
tests/test_companion.py. New: timing_panel.py, timing-panel.html, timing-panel.js,
tests/test_timing_panel.cjs. All are within panam_companion. Runtime CSV/JSON and
human configuration remain ignored. No original Panam or Development Loop action,
new helper, global install, permission/billing change, recording, or Git push.

Final Git whitespace check passed, exit0; only the known LF-to-CRLF notices were
emitted. The fixed-baseline diff outside Companion and staged-path list were empty.
All10 current source/doc changed files were checked for common OpenAI/Discord
token shapes with zero findings; this scan did not open .env or runtime files.
The three closure artifacts and .env were independently confirmed Git-ignored.

## Authorized commit/push phase closure — 2026-09-13

The human's phase-closure request explicitly authorized remaining Companion
implementation/tests/docs, strictly necessary offline instrumentation correction,
local commit(s) and one push to the experimental branch. The earlier single-commit
limit and uncommitted checkpoint are historical, superseded only within this
specific closure scope. Applicable root AGENTS.md was read; no nested Companion
AGENTS.md exists. No new helpers were created or invoked. The human was away; no
new login, secret entry, audio confirmation or code repair was requested.

Preflight: branch experiment/realtime-voice-discord-poc, HEAD
7ac6800f8ba943c82a4a720f8d1bcd9b45220d95, no upstream, origin
https://github.com/PHNMNL01/panam-discord-bot.git. The expected6 modified and4 new
Companion files were present, with no unexpected paths and no staged changes.
Baseline2b7473f48d448628e956c0e03ce676b3e0009003 is an ancestor. The four prior
experiment commits belong to the accepted Companion implementation and evidence.
Initial read-only `git ls-remote` was denied by the sandbox's network path; a
narrow approved network retry returned exit0 with no remote experimental branch.
No push was attempted at that point, and no divergent branch was found.

Instrumentation diagnosis: observed completed practice + disabled E/A matches
the controller's silent ignored-key behavior. Exact historical focus/action
sequence cannot be reconstructed, so this is not an exclusive causal claim.
The agent corrected only the existing local panel's feedback/readiness flow:
visible mode banner, explicit Q01-ready state before paid Start and clear warning
for inactive/out-of-order/duplicate marks. Practice and actual scoring remain
separate. No timing targets, question set, provider code, budget or permissions
changed. Added six deterministic tests that execute the actual page controller
with synthetic DOM/clock/storage, complementing the six timing-engine tests.

Closure verification, all offline/local:
- Full `python -B -m unittest panam_companion.tests.test_companion -q`:49 PASS,exit0.
- `node panam_companion/tests/test_timing_panel.cjs`:12 PASS,exit0.
- `python -m pip --disable-pip-version-check check`: no broken requirements,exit0.
- Browser-only synthetic practice/measurement/reload flow on127.0.0.1:8767 passed;
  no microphone/Discord/OpenAI was used and these marks are not live evidence.
The temporary local server was then stopped (Ctrl+C/tool exit1) and its tab closed.
Loopback checks confirmed ports8766/8767 closed; the experiment process lock was
available. Narrow Budget inspection confirmed204 accounted seconds,174 provider
voice seconds,USD0.17,uncertain=false. No additional live usage occurred and no
budget counter was reset. The timing correction is not live-validated.

Phase result: technical feasibility PROVEN for the tested Discord + GPT-Live path;
human-observed natural conversation and subjectively immediate response PASS;
formal latency NOT PROVEN, benchmark PARTIAL. Remaining prescribed cases stay
unfinished in BENCHMARK.md. Acoustic wake word, persistent memory, tools, deployment
and local fallback remain out of scope. Original Panam and Development Loop remain
unchanged. Human assistance and the measurement failure remain attributed honestly.

This closure is intended as one commit containing exactly the6 modified and4 new
files listed at the preceding checkpoint, all under panam_companion. The commit
containing this document is identified by Git history; its own SHA and the remote
push result are reported after execution, not invented inside its self-referential
content. Staged review, secret exclusion and final branch/remote/clean-tree checks
must pass before the authorized single push. Runtime state and .env stay ignored.

Pre-stage audit passed: all4 prior experiment commits descend linearly from the
fixed baseline and change only Companion paths. A value-suppressing scan covered
25 distinct historical experiment blobs and23 current Companion source/doc files,
finding zero common OpenAI/Discord/GitHub token or private-key patterns. The only
Python Authorization dictionary entries build Bearer headers from runtime key
variables; no literal credential/header value is stored. The blank .env.example
was inspected; the human .env was not opened. .env/runtime are ignored/untracked,
the staged set was empty, and the baseline diff outside Companion was empty.
Git whitespace check passed with only the known LF-to-CRLF conversion notices.

### Automatic approval rejection at the Git gate

The explicit-path `git add --` request for the10 reviewed Companion files was
rejected before execution by automatic approval review. Its stated reason was
that these files include documentation/additional implementation beyond the
earlier narrowly approved diagnostic fix, AGENTS.md forbids staging, and the pasted
phase document does not itself establish authorization. No workaround/indirect
staging, retry, commit or push followed. A read-only check confirmed the index
remained empty, HEAD remained7ac6800f8ba943c82a4a720f8d1bcd9b45220d95 and exactly
the same10 Companion paths remained modified/new.

The agent requested direct human confirmation of an AGENTS.md exception for
those10 paths, the final local commit and one non-force push solely to
origin/experiment/realtime-voice-discord-poc at PHNMNL01/panam-discord-bot, with
upstream setup and explicit exclusion of .env/runtime/other branches/PR/live tests.
This request concerns the automatic approval gate; offline verification passed.
Until confirmation is accepted, closure delivery is uncommitted/unpushed and the
working tree is intentionally dirty. No remote SHA/upstream success is claimed.

The human subsequently answered the scoped gate directly: they approve exactly
the described staging, commit and one push as an exception to AGENTS.md. This
covers only the same10 Companion files, origin's experimental branch, upstream
setup and the stated exclusions. The agent resumed Git preparation on that new
direct approval. The rejected attempt remains in this evidence; it was not bypassed.
