# Local music workflows

These are ComfyUI **API prompt graphs**, built from the pinned official `Text to Music (YuE2)` blueprint and native nodes. They are sent inside `{"prompt": graph, "client_id": "..."}` to `POST http://127.0.0.1:8188/prompt`.

- `yue2-api.json`: direct text-to-music generation.
- `yue2-planned-api.json`: first composes melody and chords with `BoogieYuE2GenerateABC`, then generates audio. This is the studio default; the wrapper preserves the native score and completion report.

## Input contract

| Node | Inputs the application updates |
|---|---|
| `1` | `ckpt_name` (installed default: `yue2_3b_int8_convrot.safetensors`) |
| `2` | `style`, `lyrics`, `seed`, `max_duration` |
| `5` | `seed`, optionally `steps` (official default: 32) |
| `7` | `filename_prefix` (use a safe song ID, such as `boogie/song_<uuid>`) |
| `8`, planned graph only | Mirror `style`, `lyrics`, and `seed` from node `2` |

Style describes genre, instruments, vocal character, language, and tempo. Lyrics use section markers such as `[Verse]` and `[Chorus]`. The duration is an upper bound: generation may stop sooner, or be cut off at the limit. Inspect the resulting file and review endings before broadcast acceptance.

Keep the node links intact. Node `2`'s actual generated seconds feed node `4`'s latent length. One generation at a time is the supported station configuration.

## Output contract

1. Save `prompt_id` returned by `/prompt` before polling, so a restart does not accidentally submit the same job twice.
2. Poll `GET /history/<prompt_id>` until the entry exists and inspect `status.status_str` (`success` or `error`).
3. Read `outputs["7"].audio[0]`: `filename`, `subfolder`, `type`.
4. Fetch `GET /view?filename=<encoded>&subfolder=<encoded>&type=output`, or read the contained file under `.runtime/ComfyUI/output/`.
5. Preserve the original 48 kHz stereo FLAC and its request record. Probe actual duration before packaging a video.

The files use ComfyUI's supported `SaveAudio` node for a simple stable FLAC API contract; the newer `SaveAudioAdvanced` UI node also supports other formats.

## Runtime

Run `scripts/setup-music.sh`, then `scripts/start-music.sh`. Python/CUDA packages and models live under `.runtime/`. The server binds loopback only, disables cloud API nodes and all custom nodes except the whitelisted `boogie_yue2` completion bridge, and reserves 2 GB VRAM for the desktop and other applications. Set `MUSIC_PORT` to change the default 8188 port. Configuration is isolated from other ComfyUI installations.

For background operation, `scripts/start-music-service.sh start` creates a transient user service. Use the same script with `status` or `stop` to inspect or stop it. It restarts on failure during this session, writes `.runtime/music.log`, and is not enabled at login.

## First local verification

On the RTX 4080 SUPER, a real country/pop request with melody/chord planning produced 61.96 seconds of 48 kHz stereo FLAC in 16.97 seconds of ComfyUI execution (18.21 seconds including client polling). It ended naturally before its 120-second cap. GPU memory sampled every two seconds peaked at 8,707 MiB **including the desktop and other processes**; that sampling can miss short peaks. Request, audio probe, waveform checks, and GPU measurements are in `.runtime/country-pop-planned-result.json`. `.runtime/sample-manifest.json` identifies the audio and original lyrics for import.

This verifies model execution and the output/download contract. Musical quality still needs listening review, and simultaneous broadcasting and unattended 24/7 operation need separate testing.

Pinned identities and first-run evidence are retained under `.runtime/`. YuE2 weights declare CC-BY-NC-4.0; this installation does not establish permission for a monetized stream.
