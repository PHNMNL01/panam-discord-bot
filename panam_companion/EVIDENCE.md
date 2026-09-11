# Experiment evidence and resumable checkpoint

State: **PARTIAL — offline implementation; awaiting grouped live readiness GO.**
Date: 2026-09-11. No real provider request, Discord login, microphone capture or
human audio occurred in this implementation stage. Account balance/access unknown.
Initial master-task GO authorized offline work; it is not the later live GO.

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
