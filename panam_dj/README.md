# DJ Panam: DJ-P0 offline MVP

This is an independent Discord music bot prototype under
`HAD-DJ-P0-IMPLEMENTATION-AUTHORIZATION-001`. It is separate from Panam AI,
Companion and the Development Loop. Its entrypoint, configuration, catalog,
dependency declaration and tests are entirely inside `panam_dj/`.

The authorized checkout is `C:\Panam_APP_DJ`, branch
`experiment/dj-panam-poc`, baseline
`830a90f3bb57883b5ae420cdffcd12e9473a8643`. The implementation and evidence are
provided for Human Project Owner review; they do not declare formal acceptance.

URL-based playback and broad music availability remain the long-term goal.
This prototype establishes local playback infrastructure. It does not satisfy
the URL requirement. See the separate [source feasibility investigation](SOURCE_FEASIBILITY.md).

## Offline use

From the repository root, using an existing Python 3.12+ environment:

```powershell
python -B -m unittest discover -s panam_dj/tests -t . -v
python -B -m panam_dj --check
python -B -m panam_dj.smoke
```

The deterministic suite uses fake Discord voice clients, audio sources and
mocked Discord API calls. The optional smoke command uses the installed
FFmpeg binary to encode the bundled synthetic WAV locally, then cleans up the
audio source. It does not construct a voice connection or access a token.

The default entrypoint is also an offline check. `--check` validates configuration
and catalog and reports dependency availability; exit 2 means a prerequisite
or configuration is missing, exit 0 means its checks passed. This does not prove
live voice readiness. Adapter tests explicitly skip if discord.py cannot be
imported. The standard-library queue and catalog tests can run independently:

```powershell
python -B -m unittest panam_dj.tests.test_playback panam_dj.tests.test_catalog_config -v
```

No installation is authorized by this milestone. [requirements.txt](requirements.txt)
declares only the existing repository family, `discord.py[voice]>=2.7.1,<3`.
Package import explicitly rejects Python below 3.12 before configuration or
application initialization. Junction checks use the required `Path.is_junction`
API directly; they are never silently disabled on an older interpreter.
The inspected discord.py 2.7.1 metadata requires `PyNaCl>=1.5.0,<1.6` and
`davey>=0.1.0` for its voice extra, plus its normal base dependencies. Both voice
packages are absent in the inspected environment. FFmpeg is a separate
executable prerequisite; its build must include the libopus encoder.

## Architecture and state

| Component | Responsibility |
| --- | --- |
| [config.py](config.py) | Strict, non-secret JSON configuration and confined paths. |
| [catalog.py](catalog.py) | Immutable track metadata, asynchronous `SourceProvider` port, approved local WAV catalog. |
| [playback.py](playback.py) | Discord-independent FIFO queues, per-guild locks, playback generations and cleanup. |
| [discord_app.py](discord_app.py) | Guild-only slash commands, voice permissions/connections, FFmpeg Opus source. |
| [__main__.py](__main__.py) | Offline preflight by default; explicit future live entrypoint. |
| [smoke.py](smoke.py) | Local FFmpeg integration smoke test. |

Each allowlisted guild has a stable session and lock. Commands, provider
resolution and connection creation are serialized within that guild. Separate
guilds do not share queues or voice clients. The queue limit counts waiting
tracks; the current track is additional. The default is 20 waiting tracks and
the configurable range is 1 to 100. No automatic move between voice channels
is permitted, and every command requires the caller to be in the bot's channel
when there is an active session. Stage channels and DMs are rejected.
The Discord adapter supplies a synchronous live-membership check that the
manager invokes under the guild lock before effects and again after awaited
preparation. A caller who moves or leaves while acknowledgment, the lock,
source resolution or connection setup is pending receives a rejection. A newly
created connection for a stale caller is released without queueing a track.
After source resolution, the manager also revalidates the existing voice
connection and rejects a result whose connection moved or disconnected.

An audio-thread completion is scheduled on the owning asyncio loop. Each
playback has a generation number: late or duplicate callbacks cannot advance a
replacement track after skip, stop or leave. Failed tracks are discarded and
the bounded queue advances; `/queue` exposes a safe last-error message. Discord
invokes source cleanup after successful `VoiceClient.play`; the manager retains
an owned wrapper until that cleanup has actually finished successfully. The
wrapper is a supported `discord.AudioSource`, delegates audio operations, and
publishes a thread-safe result only after the underlying cleanup returns or
raises. A lock permits only one delegated cleanup attempt. The `after` callback
is not evidence of cleanup: discord.py calls it first. Sources whose start fails
receive one retained, observed `asyncio.to_thread` cleanup task, so cleanup does
not block the event loop. Cancellation during connection cleans the newly registered
voice client. Shutdown clears queues and attempts every guild's disconnect,
even if one fails. A failed disconnect retains the reference for a retry.
Manager and Discord-client shutdown each retain one shared cleanup task. Every
close caller observes its result; caller cancellation is propagated only after
the cleanup attempt and base-client teardown finish. Concurrent closes share
that task, and repeated successful closes do not disconnect again. After visiting
all guilds, close waits asynchronously up to five seconds for outstanding source
cleanup and failed-start cleanup tasks. This is a source-cleanup deadline, not
a deadline for the separate Discord transport teardown.

A failed disconnect or incomplete source cleanup leaves the manager in a
closing state, refusing new playback while retaining unresolved ownership.
`close()` returns the affected guild IDs and records a safe error in the session.
Cleanup exceptions remain attached to their owned source; a failed attempt is
not silently retried or treated as success. A later close can reconcile a timed-out
source that subsequently finishes, and retry a failed voice disconnect. It never
duplicates a source cleanup attempt. The manager is marked closed only once all
voice references are released, every source cleanup succeeds, and all retained
failed-start cleanup tasks finish. This also covers sources retired by stop,
skip, leave or natural completion while their cleanup is still pending.

Arbitrary blocked native cleanup cannot be forcibly stopped safely. Timeout
reports unresolved ownership; it does not terminate the owning audio thread or
executor call. Operator reconciliation may be necessary. Successful wrapper
cleanup observes the underlying method's return; it cannot detect an FFmpeg
termination error that the installed library itself suppresses. Audio-thread
termination follows cleanup and is not claimed to be a joined-thread barrier.

An external voice move or detected disconnection clears the affected queue and
releases that connection. Removal from a guild explicitly stops its session.
An empty queue stays connected until `/leave` or shutdown. There is no persistent
queue, idle timer, role-based DJ policy, voice recording or automatic restart.

## Commands

All seven commands are registered and synchronized only to configured guilds.
There is no global command synchronization or message-content intent.

| Command | Behavior |
| --- | --- |
| `/play track:demo` | Resolve an approved ID, join the caller's regular voice channel if necessary, and play or append to FIFO. Autocomplete lists approved IDs. |
| `/skip` | Stop the current track, including a paused track, and advance once. |
| `/pause` | Pause the current track; reject empty or already paused playback. |
| `/resume` | Resume a paused track; reject invalid transitions. |
| `/queue` | Show current state, waiting count and up to ten waiting titles. |
| `/stop` | Stop and clear the queue; retain the voice connection. |
| `/leave` | Stop, clear and disconnect. Safe to repeat when already empty. |

Responses are ephemeral, bounded and suppress mentions. Any member of the
active voice channel can control its queue; a more restrictive DJ role policy
would be a separate product decision.

## Authorized local audio

[catalog/catalog.json](catalog/catalog.json) initially contains only `demo`:
[the original synthetic WAV](catalog/audio/dj_panam_demo.wav). It consists of
two seconds of 440/660 Hz triangle tones, stereo, 48 kHz, 16-bit PCM, with short
edge fades. It contains no samples or third-party recordings. Its SHA-256 is:

```text
1e9564c6823f2c20b82d62d2a631521a1de61a0025f33d55e4854a9a33a4a668
```

New catalog entries require an owner-approved recording, explicit `approved:
true`, authorization/provenance text, a unique ID and its SHA-256. A manifest
flag records that decision; it does not establish media rights by itself.
Only 16-bit PCM WAV, mono/stereo, 8–48 kHz, up to ten minutes and 64 MiB is
accepted. At most 100 catalog tracks are accepted. No downloader is present.

Paths must be portable relative paths under the catalog directory. Absolute,
UNC, drive-relative, traversal, backslash, device-name, alternate-stream, symlink
and junction paths are rejected. The file must be regular; its hash, WAV format
and declared frame length are checked on load and again before playback.
The exact verified bytes are piped to FFmpeg with forced WAV demuxing and only
local `pipe,fd` protocols enabled. User text never becomes a filename, shell
command or FFmpeg argument. Config/catalog directories remain owner-controlled;
this is not an OS sandbox against concurrent hostile local filesystem writers.

## DAVE and prerequisites for a separately approved live test

discord.py added DAVE in 2.7.0, and 2.7.1 adds missing-davey diagnostics.
The adapter uses the maintained `VoiceChannel.connect`, `VoiceClient.play`
and `FFmpegOpusAudio` interfaces. FFmpeg supplies Opus frames; discord.py and
its voice extra own transport encryption and DAVE. There is no custom encryption,
voice receive extension or protocol bypass. See the
[official changelog](https://github.com/Rapptz/discord.py/blob/master/docs/whats_new.rst)
and [Discord voice protocol documentation](https://github.com/discord/discord-api-docs/blob/main/developers/topics/voice-connections.mdx).
Offline tests cannot prove a successful live DAVE handshake.

Before a live test the owner must separately approve and arrange:

1. A compatible isolated Python 3.12+ environment containing the declared voice extra and
   FFmpeg with libopus. No packages or OS settings were changed here.
2. A dedicated Discord application and bot token, distinct from existing Panam
   applications. Provision the token as `DJ_PANAM_BOT_TOKEN`; do not put it in
   the JSON, repository or chat. No `.env` loader or fallback Panam token is used.
3. An owner-prepared `panam_dj/config.local.json` based on
   [config.example.json](config.example.json), with the dedicated application ID
   and test guild allowlist. The example IDs are placeholders; the example
   configuration is blocked from live login by default.
4. A guild installation with `bot` and `applications.commands` scopes and View
   Channel, Connect and Speak permissions for the chosen regular voice channel.
   Enable the Guilds and Guild Voice States intents; no privileged intent is used.
5. Explicit authorization for bot login, guild command synchronization and a
   controlled voice test. Startup verifies the application identity before
   synchronizing this app's commands in the configured guilds. Synchronization
   replaces this dedicated app's guild command set.

Only after that separate decision is the intended live command:

```powershell
python -B -m panam_dj --config config.local.json --connect
```

Verify audible demo playback, all seven commands, two-channel isolation,
concurrent requests, disconnect/reconnect and shutdown in that live test.
The current implementation does not guarantee catalog rights or an independent
token's identity before an authorized login; the owner must provision the
separate application, and the adapter then verifies the configured ID.

See [verification evidence](VERIFICATION.md) for outcomes, limitations, changed
paths and Git state. No formal milestone acceptance is claimed.
