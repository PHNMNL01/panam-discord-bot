# Research and route decision

Access date: **2026-09-11**. Baseline: `2b7473f48d448628e956c0e03ce676b3e0009003`.
Observed package versions are in `environment.json`. Sources below are primary
provider documentation or actual installed/upstream library sources. Documentation
access and offline codec tests are not evidence of account/model/transport access.

## Live protocol and budget

[GPT-Live guide](https://developers.openai.com/api/docs/guides/live) and
[model page](https://developers.openai.com/api/docs/models/gpt-live-1) identify
`gpt-live-1`, independent from Realtime and the coding agent's model. The marketing
[announcement](https://openai.com/index/introducing-gpt-live-1-in-the-api/) is only
background, not the protocol authority.

The [WebSocket guide](https://developers.openai.com/api/docs/guides/voice-websockets?api=live)
specifies `wss://api.openai.com/v1/live/sessions`, bearer authentication, no model
query parameter, `session.start` then `session.started`. We use mono PCM16 LE
24 kHz, raw base64 JSON input and streamed output, with stateful conversion from
Discord 48 kHz stereo. Twenty-millisecond input pacing supplies silence between
Discord packets. No complete speech file, external transcoder, STT or TTS request.
The installed SDK's `openai/resources/live/live.py` and `types/live/` confirm the
same event and configuration shapes. Raw `websockets` avoids its default five
retries; an experiment subclass rejects redirects. Proxy discovery is disabled.

The [session lifecycle guide](https://developers.openai.com/api/docs/guides/live-conversations)
documents input mute acknowledgments, cumulative `usage.seconds`, and final
`session.closed`. Transcript fragments do not delimit turns; output audio lacks
timing/generation IDs and an audio-done event. Consequently, local timestamps and
buffer clearing are explicitly proxies. No invented `response.cancel`, output
clear, VAD event or Realtime event is sent. Socket closure without the final event
is unconfirmed and locks the allowance. Session context is never forked/restored.

[Delegation guidance](https://developers.openai.com/api/docs/guides/live-delegation)
favors managed Responses for simplicity, but client delegation permits budget,
context and stale-result controls. Our $1 batch and correction handling justify
client mode: one serial OpenAI text backend, bounded transcript and output, no
tools. Delegation metadata is not task text; the application retains recent
transcript fragments and waits for a bounded settling heuristic. Results are
appended using the original delegation ID. Speech interruption invalidates
results; it does not assert remote computation cancellation.

[Prompting guidance](https://developers.openai.com/api/docs/guides/live-prompting)
informs a short Czech voice/persona/delegation prompt, with reasoning instructions
kept in the backend. The backend is
[GPT-5.6 Luna](https://developers.openai.com/api/docs/models/gpt-5.6-luna), low
reasoning and default service tier. Its availability to this account is untested.
`background=false` avoids asynchronous retained jobs. The
[background guide](https://developers.openai.com/api/docs/guides/background)
documents cancellation for background responses; no such confirmed cancellation
is assumed for this foreground request. A request may finish with its result
discarded; ambiguous timeout prevents further sessions.

[Pricing](https://developers.openai.com/api/docs/pricing): Live $0.05/min billed per
second, backend extra. Luna Standard short-context prices are $0.20/M input and
$1.20/M output; cache writes cost 1.25× input. Estimate with no cache discount and
generous token overhead: fewer than 8,000 input-equivalent tokens × $0.25/M plus
384 × $1.20/M ≈ $0.00246/call. Reserve $0.01/call ×40 +$0.50 voice = $0.90;
proposed $1 approval leaves $0.10 margin. Counter reservations are local estimates,
not billing enforcement. Backend input stays far below the long-context threshold.

## Discord evidence

[Official voice documentation](https://docs.discord.com/developers/topics/voice-connections)
requires DAVE E2EE from March 1, 2026. Installed `discord.py` 2.7.1 supports DAVE
negotiation/encryption through `davey` 0.1.6. Installed
`discord-ext-voice-recv` 0.5.2a179 decrypts RTP transport but passes remaining bytes
straight to Opus when decoding is enabled: it has no DAVE decryption call.
The upstream [DAVE receive PR #58](https://github.com/imayhaveborkedit/discord-ext-voice-recv/pull/58)
was open when inspected; we did not install or claim it merged.

Local evidence: `discord/voice_state.py` (DAVE readiness), `voice_client.py`
(outbound DAVE wrapper), `ext/voice_recv/reader.py` (transport decrypt),
`ext/voice_recv/opus.py` (conditional decode). Baseline
`panam_discord_voice.py:422` asks for Opus while line435 reads PCM; installed
`VoiceData` supplies empty PCM on that path. Flipping the flag alone would expose
the DAVE incompatibility. All baseline files remain intact.

Selected solution: a new Opus sink filters the sender, rejects passthrough/not-ready
DAVE, decrypts using `DaveSession.decrypt`, then uses an owned Opus decoder.
[Davey upstream](https://github.com/Snazzah/davey) and installed
`davey/__init__.pyi` document this API. Decode failures drop frames, never fall
back to unencrypted speech. Outgoing packet construction requires DAVE and current
membership on every frame. Private library integration is pinned and tested with
synthetic input; real MLS handshake/transition correctness remains unverified.

The [discord.py audio source contract](https://github.com/Rapptz/discord.py/blob/v2.7.1/discord/player.py)
accepts 3,840-byte PCM frames every 20 ms. Windows bundled Opus successfully
encoded/decoded a synthetic frame locally. Therefore missing FFmpeg is not a
blocker. The player and receive extension have asynchronous stop behavior: keep
owned references through connection cancellation, join owned threads, and clear
queues. Receive-library debug logging can contain transport secrets; standalone
runtime logging is disabled before login, and exceptions are fixed-message only.

**Decision:** continue Discord first. An isolated adapter is feasible enough to
test; no reproduced failure justifies fallback yet. Native codec success and static
review are not a live DAVE test. If live evidence establishes an unresolved route
blocker, retain this evidence and use the authorized independent local fallback
within the same remaining budget; never substitute a different voice model.
