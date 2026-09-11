# Panam Companion — isolated Discord GPT-Live POC

**Offline implementation; live operation and audible latency NOT VERIFIED.**
Read [BENCHMARK.md](BENCHMARK.md) before live tests. Current evidence and activity
are in [EVIDENCE.md](EVIDENCE.md); protocol/route decisions in [RESEARCH.md](RESEARCH.md).

This module starts independently with `python -m panam_companion`. It imports no
legacy Panam application. It does not implement or invoke the Development Loop.
No baseline entrypoint or dependency declaration is changed. Manual activation,
Czech speech, feminine Panam persona, and session-local context are intended.
**Acoustic wake word and persistent memory: NOT IMPLEMENTED.** A future wake-word
detector would belong before input admission; a future memory adapter would require
separate consent and explicit startup context. Neither is hidden behind a prompt.

## Windows setup

Use PowerShell in `C:\Panam_APP_astra`, on the existing experimental branch.
The existing `.venv` is ready; do not rebuild it just to run this experiment.
Python is CPython 3.14.5 x64. `requirements.txt` lists runtime requirements and
`constraints.txt` pins transitive versions from the actual 53-package environment
recorded in `environment.json`. The existing OpenAI SDK 3.13.0 was inspected for
schema compatibility; runtime uses the documented WebSocket protocol directly.
No FFmpeg, global install, driver, PATH/firewall change or separate STT/TTS service.

For a genuinely fresh isolated checkout with CPython 3.14.5 already installed:

```powershell
Set-Location C:\Panam_APP_astra
py -3.14 -m venv .venv
New-Item -ItemType Directory -Force .venv\bootstrap\tmp, .venv\bootstrap\pip-cache | Out-Null
$env:TEMP = (Resolve-Path .venv\bootstrap\tmp).Path
$env:TMP = $env:TEMP
$env:PIP_CACHE_DIR = (Resolve-Path .venv\bootstrap\pip-cache).Path
$env:PIP_CONFIG_FILE = 'NUL'
.\.venv\Scripts\python.exe -m pip --isolated install --index-url https://pypi.org/simple --only-binary=:all: --keyring-provider disabled --disable-pip-version-check -r panam_companion\requirements.txt -c panam_companion\constraints.txt
.\.venv\Scripts\python.exe -m pip check
```

Do not execute the `venv` creation command over the prepared experiment. The
temporary/cache variables above are process-local. A clean reinstall has not been
performed in this checkout; current-environment compatibility is what was tested.

Create a **separate** Discord application/test bot in the
[Developer Portal](https://discord.com/developers/applications) and invite it only
to a dedicated test guild with `bot` and `applications.commands` scopes. Human
setup must grant only View Channel, Connect and Speak for its voice channel and
the permissions needed for interactions in the control channel. The human needs
Use Application Commands. Do not grant Administrator. No message-content or
members privileged intent is used. Enable Discord Developer Mode to copy IDs.
Do not reuse a production bot token. Bot identity and its entire guild list must
match the test configuration before guild-only command synchronization.

The five required IDs in `.env.example` and `config.py` are:

| Field | Exact resource | Where to copy it in Discord |
|---|---|---|
| `PANAM_TEST_BOT_ID` | The separate test bot's user account | Right-click that bot's profile in the test guild, Copy User ID |
| `PANAM_TEST_GUILD_ID` | Dedicated test server | Right-click the server icon, Copy Server ID |
| `PANAM_TEST_VOICE_CHANNEL_ID` | Ordinary test voice channel | Right-click that voice channel, Copy Channel ID |
| `PANAM_TEST_CONTROL_CHANNEL_ID` | Test text channel for `/panam ovladani` | Right-click that text channel, Copy Channel ID |
| `PANAM_TEST_USER_ID` | The sole consenting human tester's account | Right-click the tester's profile, Copy User ID |

Enable User Settings → Advanced → Developer Mode to expose those copy actions
([official instructions](https://support.discord.com/hc/en-us/articles/206346498-Where-can-I-find-my-User-Server-Message-ID)).
All five must be distinct positive numeric IDs. Names, invitation codes, message
IDs and tokens are not substitutes. The code checks the bot's actual user ID;
do not enter an unverified application/client ID merely because it is visible
in the Developer Portal. Confirm that existing resources are exclusively for
this POC before selecting them. Application/bot creation, invitations, permission
changes and key/account operations require separate human approval.

Create/use an experiment OpenAI project key in the
[API dashboard](https://platform.openai.com/api-keys) with access to Live and
Responses. Account credit/model access must be checked by the human. No billing
changes or key/account operations are performed by this module.

Copy the template once and fill it **locally**:

```powershell
if (-not (Test-Path -LiteralPath panam_companion\.env)) {
    Copy-Item -LiteralPath panam_companion\.env.example -Destination panam_companion\.env
}
notepad panam_companion\.env
.\.venv\Scripts\python.exe -B -m panam_companion --check
```

Never send secrets in chat or arguments. The application reads only this exact
file, with interpolation disabled. It never discovers root `.env`, loads legacy
settings, or changes the process environment. Missing IDs fail closed. Ambient
OpenAI/Discord credential or endpoint variables (and any PANAM configuration
variables) produce a value-free conflict error; use a clean shell. Do not print
the environment. `--check` validates local presence/schema/versions and prints
non-secret destination IDs only; it does not authenticate or test access.
Both `.env` and nested `runtime/` are already ignored by baseline Git rules.
During assisted setup, the human confirms that local configuration is ready
before the agent runs `--check`. A successful offline check is not live GO.

## Offline checks

```powershell
.\.venv\Scripts\python.exe -B -m unittest discover -s panam_companion/tests -v
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -B -m panam_companion.benchmark panam_companion\latency-template.csv
git diff --check
```

Tests use synthetic audio, mock providers and temporary fixtures under
`panam_companion/runtime/offline-tests`. They never log in, capture a microphone
or invoke live providers. On this machine Codex's filesystem sandbox sometimes
denied newly created temporary directories; a scoped approval for this exact test
command succeeded. This is not a recommendation to change system permissions.

## Explicit live startup — only after the grouped readiness GO

```powershell
Set-Location C:\Panam_APP_astra
.\.venv\Scripts\python.exe -B -m panam_companion --live-approved --metadata
```

Check the printed destination IDs and type `CONNECT`. This logs in the test bot,
then registers `/panam ovladani` only in the configured test guild. No voice
channel or OpenAI connection starts yet. An API key alone never starts a session.
The proposed batch starts with the short S01 smoke test in `BENCHMARK.md`, then
the unchanged acceptance sequence using the remaining shared allowance.

Join the configured voice channel as the sole consenting user. Select your
microphone/output device and preferably headphones in Discord. **Discord can
already receive your microphone audio while you are in its voice channel, before
Panam Start.** The bot does not receive/decode conversation or forward it to
OpenAI until Start and Live readiness. Use Discord's own mute when you want to
stop microphone transmission to Discord itself.

In the configured control channel use `/panam ovladani`:

- **Start:** join voice, require DAVE encryption and exact membership, reserve
  the batch allowance, start Live, then start filtered receive and streamed output.
- **Ztlumit:** immediately block/clear local input and output and acknowledge
  provider input mute. No user audio is forwarded to OpenAI. Session time still
  costs money. Discord's own client microphone is controlled in Discord.
- **Zapnout mikrofon:** await provider unmute acknowledgment before accepting
  fresh frames; old buffered input is discarded.
- **Stop:** stop capture/playback immediately, finalize Live, settle/discard
  bounded backend work and disconnect voice. The bot gateway stays for controls.
- **Reset:** Stop, forget in-memory transcripts/context. A subsequent manual
  Start creates a fresh context and consumes the same batch allowance.
- **Stav:** show connection/mute state, elapsed seconds and aggregate reservation.
- **Vypnout bota:** Stop plus logout. Ctrl+C also performs cleanup.

Only the exact configured user/guild/control channel may control the test.
Wrong speakers, bots, unmapped users and extra participants are rejected. Output
is additionally gated per packet, since Discord broadcasts voice to the channel.
Cached membership changes cannot retract a frame already sent; live audible stop
and membership-event latency still need measurement.

Optional `--captions` displays bounded transcript fragments in the **local
terminal only**, without application disk logging. Do not enable captions in a
recorded terminal or share its output if that would persist private conversation.
The default keeps transcript context only in memory. `--metadata` prints only
fixed event codes, numeric timings and counts at Stop; never audio or transcripts.
Do not interpret these queue/source timings as audible latency.

## Budget and failure behavior

Proposed batch approval is **USD 1 total**, not the reported unverified account
balance. At current pricing, 10 minutes of Live is $0.50. A maximum of 40 serial
backend calls reserves $0.01 each, leaving $0.10 headroom. Each backend request
uses at most 6,000 UTF-8 bytes of transcript plus a short fixed instruction,
384 output tokens including reasoning, `low` reasoning, `default` service tier,
`store=false`, no background mode and no tools. Its expected worst-case token
estimate is below $0.003; the larger reservation is deliberately not refunded.
There are no top-ups, automatic retries or alternate providers.

Before any paid attempt, reserve up to 300 s in an atomic local counter. Stop
active conversation at 285 s (earlier if the remaining allowance is shorter),
leaving 15 s for bounded finalization. Track the maximum cumulative provider
seconds, never sum snapshots. Reconcile a confirmed close with the larger of
wall time or reported duration. A crash, missing final event, ambiguous backend
timeout, or overrun retains the reservation and blocks new starts across restart.
An OS file lock prevents another local instance using the same experiment.
These are conservative application guards, **not a provider-side hard cap**.

Interruptions clear local output and invalidate pending results. A running
foreground backend request has no confirmed cancellation API, so it may finish
within its 10 s timeout; its stale result is discarded. Corrections are queued
serially. Stop attempts to observe completion, then closes owned transports/tasks;
an ambiguous backend termination blocks further sessions and keeps its full cost
reservation. It never claims the provider canceled computation merely because
speech stopped. There are no side-effecting tools to cancel.

If DAVE is not ready, access is denied, a provider rejects configuration, a queue
overflows or a transport disconnects: stop, inspect the **safe** status and report
the failure. Do not repeatedly retry an unexplained failure. Missing credentials
or Live access is a prerequisite failure, not evidence to switch to local UI.
An uncertain `runtime/budget.json` requires reconciling provider session/usage
with the human before a code-reviewed recovery; there is intentionally no reset
command. **Do not delete/edit counters to regain allowance.** Malformed counters
also fail closed. A process dying cannot prove remote finalization.

## Known limits

The Discord receive library is prerelease; the adapter uses pinned private DAVE
state. Natural interruption uses a basic energy gate with 40 ms onset and 250 ms
hangover. It can misclassify noise; Live audio carries no response generation ID,
so local queue invalidation cannot prove every late provider sample is from the
correct utterance. These are explicit live acceptance risks. The 300 ms playback
buffer fails closed on overrun instead of building a long speech backlog. Actual
DAVE transitions, chunk cadence, sound, Czech quality and latency remain untested.

No local fallback UI was built: research did not establish Discord infeasibility.
The core Live module is separable if an evidenced fallback becomes necessary.
No session forking, provider recording, filesystem/tool access by Panam, web search,
legacy memory, wake word or deployment pipeline. `store=false` is not a claim
about all provider operational/abuse-monitoring retention.
