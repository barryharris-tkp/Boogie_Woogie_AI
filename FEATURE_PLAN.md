# Boogie Woogie AI — feature plan

Status: the first local version is built and running. Real YuE2 music, local lyric composition, OpenAI artwork, paired video packaging, saved library, queue controls, and playback have passed live application checks. The broadcast controller has passed local stream and reconnect tests. Public platform ingest and a 24-hour endurance run remain unverified. See `RUNTIME_VERIFICATION.md` for measured results.

## Available now

- Local studio at `http://127.0.0.1:8765`, with Studio, Library, Broadcast, and Settings pages.
- Comfy-Org YuE2 INT8 with full/melody/direct planning, editable ABC scores, independent seeds, music/score sampling, and acoustic rendering controls, plus local Qwen lyric writing on CPU.
- Natural ending detection from native model metadata; automatic length up to four minutes, with the length selector disabled in natural mode. Timed clips enable the selector. Incomplete takes stay available for independent review and are excluded from the station queue.
- New takes from saved lyrics/style/settings, reusing artwork and preserving original assets.
- Original lossless audio, saved song records, OpenAI covers, fallback artwork, and immutable landscape/portrait video revisions.
- Playback queue, pause/restart/skip controls, downloads, and an OBS browser-source player.
- Per-song three-dot action menus with audio/video/cover downloads and confirmed permanent deletion, including queue and saved-media cleanup.
- Explicitly started RTMP/RTMPS broadcasts of a frozen queue, with exponential reconnect after an encoder disconnect. Stop cancels reconnects; application restarts leave broadcasts off.
- Persistent job recovery, artwork spending records and request limits, masked key settings, and optional explicit local key persistence.

## Still to build or evaluate

- Audience comment connectors, voting, artist-reference interpretation, and near-term song selection.
- Automatic library replenishment and dynamically changing the direct RTMP playlist while it is on air.
- Quality-based candidate selection, crossfades, and multiple simultaneous broadcast destinations.
- Comparative listening/model benchmarks and actual platform/endurance acceptance tests.

## Product direction

- Generate high-quality original country, pop, and other requested music locally on the MSI PC.
- Broadcast continuous music with matching visuals to YouTube Live and, after integration verification, TikTok and other platforms.
- Add audience-directed music later: a local language model interprets live comments, groups related artist/song/style requests, and uses audience demand to guide upcoming original songs.
- Add OpenAI-generated song covers based on each song's description, mood, genre, and lyrical themes.

## Local and cloud responsibilities

- Local now: music generation, lyric writing, saved media, playback, and broadcast encoding. Comment interpretation, audience request counts, and musical quality screening are planned.
- OpenAI API: image generation from a prepared song-cover prompt. This requires internet access, API credentials, and metered API usage; it is not a locally hosted image model.
- Streaming platforms: comments and broadcast distribution.
- Hardware inspected in this conversation: RTX 4080 SUPER with 16 GB VRAM, Intel i9-14900F, and approximately 64 GB installed RAM. One live generation overlapped a 90-second local stream-copy test; sustained simultaneous operation remains untested.

## Song covers and synchronization

### Artwork preparation

1. Give each song a unique ID and revision. Keep its title, description, lyrics, audio, artwork, and final playback assets associated with that revision.
2. Convert the description into an artwork prompt using consistent station branding. Save the prompt and model/settings with the result.
3. Use OpenAI's current image API. As checked on September 14, 2026, GPT Image 2.5 Sunburst is the proposed quality-first option; GPT Image 2.5 Flare is an optional faster alternative. Keep model selection configurable and verify account availability during setup.
4. Generate a square cover with a composition that also works inside landscape and portrait broadcast layouts. Render titles and station labels locally so they remain consistent and editable.
5. Cache the completed cover and reuse it on replays. Generate covers for accepted songs to avoid spending on discarded candidates.
6. Track image spending, bound retries, and provide a configurable daily budget. Reaching the budget uses fallback artwork rather than interrupting playback.

### Playback synchronization

- Package each finished song and its selected cover into one local video asset with a shared audio/video timeline. Create landscape and portrait versions when required by the selected broadcast destinations.
- Preserve the original lossless audio and cover as source assets.
- Admit an item to the broadcast queue only after audio, artwork or fallback, and the packaged playback asset are validated and ready.
- Let one playback controller own start, stop, skip, seek, and restart. The picture follows the audio because both belong to the same asset.
- If crossfades are added, use the same transition timeline for the outgoing and incoming music and artwork.
- Delayed image responses update only their matching song/revision. They must never replace artwork belonging to a different song already on air.
- If artwork is unavailable, play another ready song or use station artwork attached to the correct track. A cloud failure must not stop playback.

## Audience requests — later phase

- Group requests by artist, song, genre, mood, instruments, and vocal qualities without treating these categories as identical.
- Count demand within rolling windows and limit repeated votes from one account.
- Treat comments as audience input, not executable instructions or authority to change system settings.
- Translate winning preferences into original-song briefs. Reference-audio analysis or reproducing a particular singer's voice requires a separate capability evaluation; it is not established by YuE2's documented controls.
- Keep a reserve library, but commit only the next one or two songs so new audience preferences can influence the near-term queue.
- YouTube has an official live-chat API. TikTok LIVE comment access remains a separate integration check.

## Build sequence

1. Benchmark local music candidates for country/pop quality, accepted audio produced per hour, memory use, and stability.
2. Build dependable playback and broadcasting, with a song record that supports artwork from the start.
3. Add OpenAI covers, packaging, fallback behavior, and synchronized playback.
4. Add live-comment interpretation and audience voting.

## Verification before unattended use

- Confirm the correct cover appears during normal playback, skips, restarts, and any crossfades.
- Exercise delayed/failed image requests, stale revisions, budget exhaustion, and broadcast recovery.
- Run sustained music generation and broadcasting together while measuring dropped frames, GPU memory, queue depth, and accepted music throughput.
- Verify streaming access and upload capacity for the actual destinations.
- Monetization intent remains unresolved. YuE2's noncommercial model license must be considered before selecting it for production.

## Sources

- [GPT Image 2.5 Sunburst](https://developers.openai.com/api/docs/models/gpt-image-2.5-sunburst)
- [GPT Image 2.5 Flare](https://developers.openai.com/api/docs/models/gpt-image-2.5-flare)
- [OpenAI image generation guide](https://developers.openai.com/api/docs/guides/image-generation)
- [YuE2 model and runtime information](https://huggingface.co/m-a-p/YuE2-3B)
- [YouTube live-chat streaming API](https://developers.google.com/youtube/v3/live/docs/liveChatMessages/streamList)
- [OBS hardware encoding](https://obsproject.com/kb/hardware-encoding)
