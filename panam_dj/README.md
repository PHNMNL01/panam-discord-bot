# DJ Panam: DJ-P2.1 metadata and approved-audio POC

DJ Panam is independent of Panam AI, Companion and Development Loop runtime.
DJ-P0 established local playback. DJ-P2.1 is authorized by
`HAD-DJ-P2-1-YOUTUBE-POC-IMPLEMENTATION-001`, within `panam_dj/**` only.
Checkout: `C:\Panam_APP_DJ`; branch: `experiment/dj-panam-poc`; implementation
baseline: `bd1d16e41b90b095af89ad41d7cfe2c4109f669c`.
This implementation is submitted for Human Project Owner review, not acceptance.

The product goal remains music URLs with broad coverage. This POC recognizes
YouTube URLs for metadata and plays only separately approved local assets. It
does not retrieve YouTube audio or provide general remote-media streaming.
See [source feasibility](SOURCE_FEASIBILITY.md) and [verification](VERIFICATION.md).

## Offline use

Use the provisioned repository interpreter, not an unrelated system Python:

```powershell
.\.venv\Scripts\python.exe -B -m unittest discover -s panam_dj/tests -t . -v
.\.venv\Scripts\python.exe -B -m panam_dj --check
.\.venv\Scripts\python.exe -B -m panam_dj.smoke
```

No installation is authorized. [requirements.txt](requirements.txt) declares
`discord.py[voice]>=2.7.1,<3` and `yt-dlp==2026.10.26`. Python 3.12+ and FFmpeg
with libopus are required. EJS and Deno are not needed by this restricted metadata
worker; neither is invoked. No dependency updater runs automatically.

The default entrypoint and `--check` validate configuration/catalog and package
presence without reading a token or making network requests. They do not prove
live readiness or validate all transitive version constraints. See VERIFICATION.md
for the installed PyNaCl mismatch against discord.py's voice-extra declaration.

Tests fake Discord transport and metadata. Integration tests use the real yt-dlp
API with a fake extractor and forbidden network/download methods, an offline
Python child printing fixture JSON, and a test-only mapping of the existing
synthetic WAV through real FFmpeg. No actual YouTube worker request is executed.
The smoke test encodes that existing WAV to Opus, with no voice connection.
Catalog tests create and remove fixtures only beneath `panam_dj/tests/`.
Missing optional test dependencies produce explicit skips; smoke/check exit 2
when their prerequisites are unavailable. No new music sample is shipped.

## Configuration and approved media mappings

[config.example.json](config.example.json) contains placeholder Discord IDs and
points to [youtube.example.json](youtube.example.json). Metadata lookup is disabled
and mappings are empty. Older four-key DJ-P0 configurations remain valid: an
omitted `youtube` key disables metadata. All configuration paths remain confined
beneath the DJ package; symlinks, junctions and traversal are rejected.

After separate authorization for networked metadata access, the owner can prepare
a mapping file with `version: 1`, `metadata_enabled: true`, and a `mappings` array.
Every mapping requires exactly these fields:

```json
{
  "video_id": "abcdefghijk",
  "catalog_id": "owner_approved_recording",
  "approved": true,
  "authorization": "Reference to the separate source and mapping approval"
}
```

This illustrates the schema; it does not approve this video or any recording.
Each catalog ID must already exist in the approved local catalog. There are at
most 100 mappings; video IDs must be unique. No mapping is supplied by default.
The owner must establish recording rights, provide a valid hash-bound local PCM
WAV, arrange any required listener-accessible credits, and approve the mapping.
A public link, metadata result or approval boolean does not establish rights.
No real recording has been acquired or authorized by this implementation.

Supported inputs are HTTPS `youtube.com`, `www.youtube.com`, or `m.youtube.com`
`/watch?v=<11-character ID>`, those hosts' `/shorts/<ID>`, and `youtu.be/<ID>`.
Optional bounded `si` and numeric `t` query parameters are accepted and discarded;
playback starts at the beginning. Playlist parameters (including on watch URLs),
unknown/duplicate query keys, fragments, percent escapes, URL credentials, ports,
unknown hosts, embed/live paths and search text are rejected. Local catalog IDs
continue to work and do not launch metadata lookup.

Requested video identity/title/URL are separate from playable catalog ID/digest.
The queue displays the actual local recording title, `approved local audio;
YouTube metadata only`, and the requested title. It never claims the local audio
was fetched from YouTube. Unmapped videos are rejected before metadata discovery:
the mapping is also the metadata authorization allowlist, and rejection never
launches a worker, creates a voice connection or queues audio. Failed metadata lookup
never silently substitutes another source, even if a local mapping exists.

## Architecture and bounds

| Component | Responsibility |
| --- | --- |
| [config.py](config.py) | Strict non-secret settings and confined paths. |
| [catalog.py](catalog.py) | Stable track metadata, distinct LocalMedia identity, verified WAV catalog. |
| [sources.py](sources.py) | Strict URLs, mapping validation and metadata-process ownership. |
| [metadata_worker.py](metadata_worker.py) | Restricted yt-dlp metadata operation. |
| [playback.py](playback.py) | Guild isolation, FIFO reservations, playback generations and cleanup. |
| [discord_app.py](discord_app.py) | Seven guild-only commands, voice permissions and FFmpeg source. |
| [__main__.py](__main__.py) | Offline checks by default, explicit authorized live entrypoint. |

The worker uses `YoutubeDL.extract_info` with `download=False`, `process=False`
and only the YouTube extractor. Download skipping and simulation are enabled.
CLI configuration is not loaded. Caches, credential sources, environment proxies,
plugins, JS runtimes, remote components and HLS/DASH discovery are disabled.
Only ID, an 80-character title and duration leave the worker. Format URLs,
thumbnails and descriptions are discarded. Live, age-restricted, restricted,
playlist, redirect-result and over-ten-minute metadata are rejected. Disabling
JS and other features may reduce availability; this POC fails closed.

Each lookup uses a short-lived Python child, not a service. At most two execute
concurrently per bot. Socket timeout is three seconds with zero configured
retries; parent lookup deadline is eight seconds with an 8 KiB output cap.
The child's explicit environment contains only `SystemRoot` on Windows (required
for system DLL loading), or no inherited entries on other platforms. No parent
environment enumeration/copy occurs: bot tokens, other secrets, PATH, home, proxy
and Python variables are excluded. The interpreter path is absolute. Missing
Windows `SystemRoot` fails before launch. An offline child imports yt-dlp and
verifies the exclusion using synthetic secret values, without metadata requests.
The manager's ten-second resolution deadline also covers waiting for a worker
slot. No FFmpeg/Deno children are launched by the metadata worker. Cancellation
and timeout kill/reap the worker and drain its bounded pipe. Cancellation during
process creation still observes the created handle and cleans it. If termination
cannot complete, ownership remains unresolved; a deadline is not permission to
abandon a process. Cleanup may therefore outlast the lookup deadline.
Process-exit observation runs in a finally block independently of stdout draining.
A broken stdout pipe plus failed termination keeps the cleanup/preparation pending
until asyncio's process-exit notification arrives. Repeated close calls observe
the same task; they do not issue another kill or restart cleanup. The original
drain failure propagates only after exit accounting completes.

Each guild has a state lock and a separate fair resolution lock. Requests reserve
capacity before asynchronous work; slow discovery does not hold the state lock.
Resolution/admission remains FIFO, including failed and cancelled requests.
The total of current, queued and unresolved requests is at most queue_limit + 1.
Cancelled requests retain their slot until cleanup completes. Queue limits remain
1-100 waiting entries (default 20), plus one current/preparing entry. `/queue`
shows unresolved request count separately from already resolved waiting tracks.

Voice membership is checked under the guild lock after acknowledgment, lock
waits, before discovery, after discovery and after voice connection creation.
A stale caller cannot enqueue or start audio. An in-flight request binds its
voice channel even before a voice client exists. No automatic voice move occurs.
Discord connection creation still holds the guild lock during the existing
bounded handshake; this change removes network metadata waits from that lock.
Once a request is accepted into the playback queue, its requester need not stay
for the entire waiting time. External bot moves/disconnections clear its queue.

Playback generation checks reject duplicate/stale callbacks. Stop/leave cancel
pending resolutions; skip cancels the oldest preparation when no track plays.
Cancellation cannot make a late result restart a cleared session. Owned request
jobs, worker cleanup and audio-source cleanup are retained independently of the
command caller. Shutdown visits all guilds and waits up to five seconds for owned
preparation/source work. Pending work is not abandoned on timeout: closed remains
false, affected guild IDs are returned, and later close reconciles late completion.
Concurrent closes share one task; caller cancellation waits for the cleanup
attempt. Base Discord client teardown remains in a finally block.

The AudioSource wrapper observes exactly one cleanup attempt across threads.
Discord calls after() before source cleanup, so callbacks/disconnection alone
are not cleanup barriers. Failed-start sources receive an owned off-loop cleanup
task. Cleanup exceptions remain recorded and are not automatically retried.
Construction failures carry the partially initialized resource across the factory
boundary in a one-use ownership transfer. The manager registers it before starting
the same retained off-loop cleanup used for rejected playback starts. No working
read/is_opus interface is needed for this cleanup-only resource. Failed cleanup
remains recorded in the source registry and prevents successful closure; caller
cancellation cannot detach it. A successful cleanup releases the partial resource
without ever sending it to Discord. No constructor cleanup attempt precedes this
registration, and the ownership transfer cannot be consumed twice.
The DJ `ManagedFFmpegOpusAudio` adapter corrects the inspected discord.py 2.7.1
stdout ownership gap without patching installed packages. Cleanup terminates and
waits for FFmpeg, joins its pipe threads, explicitly closes stdin/stdout/stderr
and the owned in-memory input, and only then reports success. It keeps references
valid while writer threads exit and avoids competing `communicate()` operations.
Failed termination/closure propagates through the existing ownership barrier;
pending cleanup can outlast the manager deadline and cannot report `closed=True`.
This adapter deliberately depends on the inspected FFmpegAudio process/thread
fields. Rerun the actual-process regressions before dependency upgrades. They
check process exit, pipe closure and thread termination for natural completion,
skip/stop/leave/shutdown and rejected starts, including blocked/failed pipe close
and concurrent cancelled shutdown. ResourceWarnings are asserted absent, not
suppressed. This evidence does not prove every OS failure or live Discord/DAVE.

## Commands and audio boundary

| Command | Behavior |
| --- | --- |
| `/play` | Resolve a catalog ID or mapped YouTube URL, connect if needed, and play/enqueue. |
| `/skip` | Advance current playback, or cancel the oldest preparation when nothing plays. |
| `/pause` | Pause playing audio; reject empty/preparing-only or already paused state. |
| `/resume` | Resume paused audio; reject invalid transitions. |
| `/queue` | Display current audio, waiting tracks and unresolved request count. |
| `/stop` | Clear queue, stop audio, cancel preparations, keep voice connection. |
| `/leave` | Stop, clear, cancel preparations and disconnect; safe to repeat. |

Commands are guild-scoped, require current voice membership, reject DMs/stage
channels and suppress mentions. Responses are ephemeral; owners must arrange
required music attribution where listeners can access it. No global command sync,
message-content intent, AI calls, database, recording or automatic restart exists.

The original [catalog](catalog/catalog.json) contains only the existing synthetic
[demo WAV](catalog/audio/dj_panam_demo.wav), SHA-256
`1e9564c6823f2c20b82d62d2a631521a1de61a0025f33d55e4854a9a33a4a668`.
New entries require explicit source authorization and a SHA-256 digest. Audio is
regular-file, 16-bit PCM WAV, mono/stereo, 8-48 kHz, at most ten minutes and 64 MiB.
LocalMedia binds the catalog ID and digest separately from video metadata.
Containment, links/junctions, digest, format and frame length are checked at load
and again at playback. Only verified bytes enter FFmpeg, with forced WAV demuxing
and pipe/fd protocols. User URLs never become FFmpeg input or options. The owner
controls catalog directories; this is not an OS sandbox against hostile writers.

## Separate live decisions

No live test is authorized by this implementation. Future execution requires:

1. Compatible declared packages and FFmpeg; review the recorded PyNaCl mismatch.
2. A dedicated Discord application/token and allowed guild/channel configuration.
   Tokens use only `DJ_PANAM_BOT_TOKEN`; no .env loader or other Panam token is used.
3. Specific permission for metadata network calls and separately licensed local
   audio/mappings/attribution. YouTube extraction and retransmission stay excluded.
4. Permission for Discord login, this application's guild command synchronization,
   and bounded voice testing. The application ID is checked before command sync.

The live entrypoint remains `python -B -m panam_dj --config config.local.json
--connect` after those decisions; the example configuration cannot log in.
discord.py owns voice transport and DAVE. Local Opus packets, offline fakes and
user-reported earlier live results do not establish DJ-P2.1 live acceptance.
