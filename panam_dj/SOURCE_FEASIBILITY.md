# Future music sources: separate feasibility note

Investigated on 2026-10-26 using public documentation only. No media was
downloaded, no provider account was accessed, and no external integration was
implemented or activated. URL-based playback with broad music availability
remains an unresolved product requirement, not something the local demo replaces.

| Source family | Feasibility finding and next decision |
| --- | --- |
| Owner-controlled or expressly licensed direct audio URLs | Engineering inference: feasible as a future provider if the owner approves specific origins and the rights to relay audio into Discord. Requires bounded download/decoding, DNS/IP and redirect checks against private-network access, timeouts, size/duration limits and lifecycle tests. None of that URL-fetching code exists in DJ-P0. |
| A licensed catalog service offering server-side audio delivery | Evaluate a named provider's playback API, permitted Discord redistribution, territory rules, availability, quotas and cost. Broad availability cannot be promised before a suitable provider and rights are established. New dependencies, accounts and calls require separate authorization. |
| YouTube | YouTube's API policies restrict separating audio from video, background playback and downloading audiovisual content without approval. The documented APIs therefore do not establish an approved audio-extraction path for this bot. No extractor, scraping library or bypass is implemented. [YouTube developer policies](https://developers.google.com/youtube/terms/developer-policies?authuser=2&hl=en). |
| Spotify | Spotify's policy prohibits an application playing one source to multiple simultaneous listeners; the Web Playback SDK also warns against non-interactive broadcasting. A Spotify track link or metadata result is not evidence that the bot may relay its audio. This is not an approved source for Discord retransmission. [Spotify Developer Policy](https://developer.spotify.com/policy), [Web Playback SDK reference](https://developer.spotify.com/documentation/web-playback-sdk/reference). |

The present `SourceProvider.resolve(query)` returns transport-independent
`Track` metadata. The playback manager also receives an injected audio-source
factory. A later approved URL provider can implement those boundaries without
coupling queue management to a particular service. That future work must define
source lifetime/expiry, authorization evidence, preparation failures and cancellation
before changing the current local-only behavior.

The next human decision is which rights-authorized URL source or licensed
provider to assess, and what coverage is necessary to meet the music requirement.
No source integration approval is inferred from this research.
