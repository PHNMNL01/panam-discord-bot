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
