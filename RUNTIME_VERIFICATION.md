# Runtime verification

Historical measurements from the original workstation. Referenced `.runtime/` evidence files are private local artifacts and are not distributed with this repository. These results are not a fresh-host installation or a sustained 24/7 acceptance test.

## Local music result — September 14, 2026

Both requests used the actual installed Comfy-Org YuE2 INT8 checkpoint on this PC. No remote music API, prerecorded replacement, or synthetic test tone was used.

| Request | Audio duration | ComfyUI execution | Client elapsed | Sampled peak total VRAM |
|---|---:|---:|---:|---:|
| Direct country/pop smoke test | 30.00 s | 9.51 s | 10.13 s | 8,117 MiB |
| Country/pop with melody/chord planning | 61.96 s | 16.97 s | 18.21 s | 8,707 MiB |

The 30-second test hit its explicit duration cap. The planned song emitted its end token before the 120-second cap. GPU samples were taken every two seconds and include desktop/other GPU processes; brief peaks may be missed. Client elapsed time includes submission and polling.

**Verified output:** FLAC, 48,000 Hz, stereo; decoded samples are finite and non-silent. The planned sample has an RMS amplitude of 0.14064 and 10 full-scale samples among 5,948,160 interleaved samples. This is a technical signal check, not a listening-quality rating. The saved audio downloaded through ComfyUI's `/view` endpoint matched its local file byte for byte.

The original song is **Where the River Bends**. Its complete request, sample path, and import metadata are in `.runtime/sample-manifest.json`. Detailed history and GPU samples are in:

- `.runtime/country-pop-smoke-result.json`
- `.runtime/country-pop-planned-result.json`

## Installed identities

- Hardware: NVIDIA RTX 4080 SUPER, 16 GB VRAM; Intel i9-14900F; approximately 64 GB installed system RAM.
- ComfyUI: `f14bbe28697778b7c2427d4b71c7fac24b78f8f4` (0.35.0), official repository, no local source changes or custom nodes.
- Model repository revision: `8e6fcf0f23252ed188b634bd50d44f4b01fba890`.
- Checkpoint: `yue2_3b_int8_convrot.safetensors`, 3,960,938,800 bytes.
- SHA256: `96fe199377309001ed8cd26a944baeee8cc31a20ba7c36d1d3c0a7e1f4149db6`, matched the Hugging Face artifact metadata.
- Music environment: Python 3.12.14, PyTorch 2.14.0+cu130. All 103 resolved packages are pinned in `workflows/music-runtime-requirements.lock.txt`; installer dry run confirmed no changes needed.
- Native workflow: `YuE2GenerateABC` → `YuE2GenerateMusic` → 32-step `dpm_2` / `sgm_uniform`, CFG 1 → audio VAE → FLAC.
- Runtime bound to `127.0.0.1:8188`, custom/cloud API nodes disabled. Transient user service with restart on failure, no login autostart.

## Application verification — September 14, 2026

| Check | Result |
|---|---|
| Complete studio generation | Original description → CPU lyric writing → YuE2 → fallback cover → both MP4 layouts → ready library and queue, in 84.09 seconds |
| Generated studio track | `Neon Dust on Windshield Wipers`, 120 seconds; music generation/download 36.25 seconds. It reached the requested cap and is flagged for ending review |
| Improved short-song lyric budget | One actual 60-second country brief produced 46 sung words in 13.139 seconds, within the explicit 36–48-word target; this updated brief was not music-benchmarked |
| Concurrent local output | 90.273-second FLV stream-copy output overlapped the complete studio job, with no FFmpeg warnings; H.264 1280×720 and AAC stereo 48 kHz |
| OpenAI cover integration | Two real `gpt-image-2.5-sunburst`, high-quality 1024×1024 requests completed; estimated combined usage $0.106080, no uncertain requests |
| Cover visual correction | First request interpreted an imported song description as a request for written lyrics. Every cover prompt now explicitly requests artwork only. The second image and both final video frames were visually checked: no invented lettering, correct locally rendered song title |
| Final paired media | `Where the River Bends`, revision 3: 61.960-second landscape 1280×720 and portrait 720×1280 videos, H.264/AAC stereo 48 kHz |
| Revision integrity | Cover URLs for revisions 1, 2, and 3 return distinct saved images; audio hashes are identical across all three. Prior video assets remain available |
| Local HTTP/player controls | Main assets return 200; all media types support 206 byte-range requests; start, pause, restart, skip and stop passed against the running API |
| Credentials | Key was submitted through Settings and explicitly persisted; `.env.local` is mode 0600 and ignored by Git. State exposes only configured/persisted flags |
| Service restart | Both local services remain active, API key/library/queue survive the studio restart, and public broadcasting remains off |
| Automated checks | **70 tests passed**, plus 9 subtests; JavaScript and shell syntax checks passed. Two upstream test-client deprecation warnings remain |

Automated checks cover queue admission only after successful packaging, cancelled/interrupted jobs, concurrent artwork reservations and limits, ambiguous paid requests, immutable media revisions, safe request validation, single-process storage ownership, playback timing, and original audio preservation. Broadcast tests write only to local files: failed child → 2-second backoff → healthy child, subsequent 4-second backoff, and Stop interrupting retries. They do not establish public ingest behavior.

Evidence files in `.runtime/`: `studio-pipeline-verification.json`, `composer-budget-verification.json`, `stream-verification.json`, `http-verification.json`, `cover-verification.json`, `corrected-cover-verification.json`, and `final-media-verification.json`. Final video frame checks are saved as `current-landscape-frame.png` and `current-portrait-frame.png`.

The browser interface was opened for the user; automated browser interaction/visual QA was not performed. Optional WebMCP tools were not verified in a supporting browser context.

## Remaining acceptance checks

- Listen to original country/pop candidates and compare musical quality, lyric accuracy, endings, and artifacts.
- Listen to and watch the finished videos in the actual browser/OBS player, including track transitions and audio-device behavior. Packaged output and API timing checks do not establish audible browser playback quality.
- Exercise actual platform ingest credentials and a real broadcast; test disconnect and recovery behavior.
- Run generation and broadcasting together long enough to measure queue supply, dropped frames, temperatures, memory, and rejection rate. These short tests do not establish 24/7 reliability.

The direct RTMP output loops a frozen prepared playlist. Automatic library replenishment, live audience adaptation, crossfades, and multiple simultaneous destinations remain planned functionality. Reconnection handles FFmpeg process exits; it does not monitor a platform's audience-facing stream health or restore a broadcast after an application restart.


## Natural ending and creative control update — September 15, 2026

The original AI Crossroads take (`84c7f95e3f2546cd9e0228b2f3fef4e4`) had reached its exact 240-second generation cap. The studio now separates desired lyric length from the model safety ceiling and reads native semantic/score completion metadata through the owned `boogie_yue2` bridge.

A new sibling take (`39e5ca0ddf8d4c83987b575de17dce85`, Comfy prompt `ed6ad494-a0cc-4c9b-943f-74f5857853a0`) reused the original lyrics, style, music/planning seed, decoder seed 42, and cover. It used natural mode with a 900-second ceiling and reached a model end token at **331.64 seconds**: 8,291 semantic frames, `semantic_truncated=false`, `abc_truncated=false`, and 3,666 characters of saved ABC score. Provider generation took **128.82 seconds**; generation and both video packages completed in approximately 196 seconds. Ten-second GPU observations during the run peaked at 12,623 MiB total device memory; sampling began after generation started, so this is not a continuous whole-run peak.

FFprobe verified the FLAC and both packaged MP4 audio streams retain the full 331.64 seconds, 48 kHz, and stereo. Landscape is 1280×720 and portrait 720×1280; video duration is 331.625 seconds because of frame rounding. All original asset hashes are unchanged. The new cover is byte-identical to the original; artwork request counts and estimated charges are unchanged. The new take entered the queue. Eight earlier capped takes remain in the library for review and were removed from the station queue; the two other earlier ready songs remain available. This confirms native completion and full-duration packaging; it is not a subjective listening evaluation or proof that every requested lyric was sung.

The installed runtime advertises 45 samplers and 9 schedules. Options are loaded into the studio and validated before submission; only the default YuE2 acoustic combination is verified by this live run. The studio exposes editable full style/lyrics, full/melody/direct planning, manual/saved ABC, stage seeds, music/score sampling, score token budget, and acoustic rendering controls. Full settings and a browser-safe submitted graph are retained per new take. Existing older takes use their known legacy defaults when making another take.

Validation: **191 tests passed, plus 32 subtests** across application, providers, controls, media, and the runtime bridge. A subsequent core check passed 39 tests after review-job status labels were corrected. JavaScript syntax, unique HTML IDs, and all 91 static UI references passed. No browser interaction or visual QA was performed in this update. The private Tailscale URL still serves the new controls; AI Server T30 retrieved a 1,024-byte range of the new landscape video over valid HTTPS (HTTP 206). No broadcast was started.

Evidence: `.runtime/natural-ending-verification.json`, `.runtime/natural-ending-gpu.jsonl`, and the saved song record/assets. See [YuE2 controls](YUE2_CONTROLS.md) for source references, practical ranges, and unsupported capabilities.


## Automatic length and four-minute policy — September 15, 2026

Song ending now appears above Desired song length. Natural mode disables and grays the selector, showing “Automatic · up to 4 minutes”; timed mode enables it and restores the prior selection. New natural songs use a conservative 180-second lyric brief with a maximum 240-second music budget. All new generation paths, including inherited and queued legacy requests, respect the four-minute maximum. Newly submitted oversized API values are rejected. Existing songs, completion metadata, media, and queue entries remain unchanged. The earlier 331.64-second verification take predates this policy.

Validation: 124 core/control tests and 60 provider tests passed. The actual JavaScript toggle function passed natural/timed/selection-restoration checks; static HTML field order and JavaScript syntax passed. After the studio restart, live API options advertised default and maximum 240 seconds, oversized requests returned 422 without creating jobs, and library/queue/artwork-usage snapshots remained identical. Tailscale HTTPS returned the updated 240-second default. No new music or paid artwork was generated for this UI/policy change.


## Library actions and deletion

Each library card now has a top-right three-dot menu containing queue/new-take/cover actions, audio/wide-video/vertical-video/cover downloads, and a permanent-delete confirmation. Delete removes the selected song's queue entry, completed job records, and studio song directory including prior media revisions. It stops local playback only when deleting that song. Artwork usage records remain intact. Active jobs, pending new takes using the source artwork, shared media references, and running/reconnecting broadcasts prevent deletion.

A staged directory rename and SQLite transaction keep failed deletions recoverable. Startup restores staged files when the song record survives or retries cleanup after a committed deletion. Broadcast startup shares the station lock with deletion to prevent a playlist/deletion race.

Verification: 55 core/deletion tests passed using temporary libraries, including confirmation/origin guards, own-only file removal, queue/playback cleanup, spending retention, shared/dependent jobs, symlink rejection, rollback, and interrupted-cleanup recovery. JavaScript syntax, static dialog references, action-menu downloads, and escaped titles passed. Live checks confirmed the route, rejected an unconfirmed deletion, served the current assets with no-store caching, and preserved all 14 existing songs and their queue. No actual user songs were deleted. Browser visual testing was not performed.
