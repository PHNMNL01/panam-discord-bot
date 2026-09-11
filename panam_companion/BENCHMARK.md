# Preregistered acceptance — 2026-09-11

Written before any live result. Route: Discord. The historical 2–3 minute delay
is an uninstrumented user report, not a measured baseline. No percentage speedup.

## Fixed sequence and method

### Initial smoke test, proposed before live GO

After explicit live GO, run S01 first in the proposed USD1 / 600-second batch, with a
target of at most 45 seconds active plus up to 15 seconds for closure. Start
manually and ask exactly: "Jaký je rozdíl mezi knihou a časopisem?" The human
confirms whether a relevant Czech answer is audible; record startup separately.
Briefly exercise mute/unmute, then Stop. Confirm local cleanup and authoritative
provider finalization before starting the benchmark session. If sound, transport
or cleanup fails, stop and diagnose before proposing any retry. No smoke result
exists yet and no live operation is authorized by this document.

S01 is a separate, unscored functional check. It does not replace Q01-Q20, any
acceptance case, or any latency threshold. Its actual session time and delegated
calls consume the same persistent batch allowance; a new session does not reset
that allowance. Continue only with the remaining budget. A successful smoke test
alone cannot establish acceptance or a VERIFIED outcome.

### Fixed latency trials

Use Q01–Q20 exactly once in order in the first warm benchmark session; do not substitute
questions after seeing outcomes. Speak naturally, allow each substantive answer,
and mark a failure if no relevant answer begins within 15 seconds. Stop for the
duration/budget guard even if this leaves trials NOT TESTED. Retests are extra
rows; they never replace failed originals. Connection/startup is a separate measure.

| ID | Exact Czech question |
|---|---|
| Q01 | Kolik dní má běžný týden? |
| Q02 | Jaké je hlavní město Francie? |
| Q03 | Kolik je sedm plus osm? |
| Q04 | Jak se anglicky řekne dobré ráno? |
| Q05 | Která planeta je nejblíže Slunci? |
| Q06 | Jakou barvu získám smícháním modré a žluté? |
| Q07 | Kolik minut má jedna hodina? |
| Q08 | Co je opak slova pomalý? |
| Q09 | Vyjmenuj tři druhy ovoce. |
| Q10 | K čemu slouží teploměr? |
| Q11 | Které roční období následuje po jaru? |
| Q12 | Kolik stran má trojúhelník? |
| Q13 | Řekni jednou větou, co je duha. |
| Q14 | Jaké zvíře mňouká? |
| Q15 | Jak se jmenuje přirozená družice Země? |
| Q16 | Kolik je dvacet děleno čtyřmi? |
| Q17 | Uveď dvě věci, které si vezmu do deště. |
| Q18 | Co znamená slovo synonymum? |
| Q19 | Který den následuje po úterý? |
| Q20 | Jak se česky řekne anglické thank you? |

Primary timing: completion of human question → first substantive **audible**
answer at the participant. A filler, greeting or acknowledgment does not count.
The human may use a stopwatch on one local monotonic clock, reporting the interval
and estimated reaction uncertainty (at least ±0.3 s for manual marks). Better
recording/loopback annotation requires separate permission to retain audio; it is
not enabled. Report the actual method and uncertainty with every live run.

The module exposes monotonic connect/session-start/first-provider-audio,
first-nonsilent-queued/frame-read and local interrupt-clear metadata in memory.
These identify startup/transport stages only. A Discord source read is neither
network delivery nor client playback. Provider audio can be silence or filler.
There is no authoritative Live utterance-end/audio-done event or audio generation
ID. Transcript intervals are on another (session) timeline, not this clock.
No proxy qualifies as an audible latency PASS.

Record rows: ID, attempt, success, timeout, question-end mark, substantive-audible
mark or measured interval, method, uncertainty, comment code. No raw conversation
recordings or generated text in evidence by default. Unperformed trials remain
NOT TESTED. Keep every row. For a complete first set, n=20 and success count/20.
Use median and nearest-rank empirical p95 (sorted value at ceil(0.95*n)); report
maximum too. Timeouts count as failures and +infinity in aggregate latency; do not
silently exclude them. Targets: median ≤3 s, p95 ≤8 s, all 20 successful and valid
audible timing. A successful-subset statistic is descriptive only. Missing or proxy
timing leaves B NOT VERIFIED, not PASS.

## Other fixed acceptance cases

- A: real human speech traverses Discord→Live and relevant Czech audio is heard.
  Separate agent-observed transport from human-confirmed sound.
- C1: say “Zapamatuj si v této relaci slovo meruňka.”, ask an unrelated short
  question, then “Jaké slovo jsem ti řekl?”. Only session-local context is expected.
- C2–C6: ask for five examples of fruit / cities / sports / colors / instruments;
  interrupt mid-answer with “Oprava, řekni jen dvě zeleniny / země / hry / barvy
  začínající na č / bicí nástroje.” respectively. Record audible stop latency,
  stale speech, corrected answer and any false VAD. Five distinct trials.
- C7: pause 5 s; try ordinary background noise; observe unwanted interruption.
- C8: Reset (ends session), Start a new session and ask about the secret word.
  New session must not inherit it. This consumes the shared remaining allowance.
- D: stop, mute/unmute, timeout, repeated start, Discord disconnect, unavailable
  microphone, missing config, provider error. Inject equivalent failures offline.
  Discord controls OS capture: microphone denial is a human Discord test, not an
  app permission result. Missing source packets cannot prove permission denial.
- E: fail-closed user/guild/voice/control/bot gates; no output with an extra
  participant; environment conflict; no secret logging; baseline isolation.
- F: reproduce pinned setup, offline checks, then explicit live startup.

After the smoke session, plan a benchmark session with Q01–Q20 and brief context/
interruptions as time permits, then a reset session for remaining C/D cases.
Each session allows at most 285 s active plus up to 15 s close, reduced when the
shared remainder is smaller. The smoke, all starts, retries and cleanup count
toward ≤600 s across all attempts. If insufficient, report PARTIAL; request another
batch before spending more. No avoidable paid provider-error tests.

## Results at offline checkpoint

| Category | Status | Evidence |
|---|---|---|
| A | NOT TESTED | No login, human audio or paid request |
| B | NOT VERIFIED | Q01–Q20 all NOT TESTED; n=0; median/p95/max unavailable |
| C | NOT TESTED | No human conversation/interruption trials |
| D | NOT VERIFIED | 43 focused offline tests pass; real microphone/transport cleanup pending |
| E | PASS (offline) | Tested config/allowlists/DAVE gating; no legacy imports; unchanged baseline |
| F | PASS (current environment) | Pinned inventory, setup instructions and offline commands; fresh reinstall not performed |

Companion API spending observed: $0 (no requests initiated by this work).
Account balance and model access NOT VERIFIED. This says nothing about developer
agent/tool consumption. No human audio/content has been saved.

`python -B -m panam_companion.benchmark panam_companion/latency-template.csv`
reports 20 NOT TESTED, n=0 and null metrics. The analyzer rejects provider/queue
proxies and missing first-attempt IDs, counts failures, and reports uncertainty.
Human stopwatch measurements require at least ±0.3 s uncertainty. Borderline
intervals remain NOT VERIFIED if their upper uncertainty bound crosses a target.
