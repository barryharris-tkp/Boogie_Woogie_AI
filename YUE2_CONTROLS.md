# YuE2 controls and natural endings

Research checked **September 15, 2026**. The studio now exposes the native song, score, sampling, and acoustic controls described below, within the practical ranges validated by `station/music_controls.py`. Internal-only and unsupported features are identified separately. The completion bridge is implemented; model math and upstream ComfyUI files remain unchanged.

Installed ComfyUI revision: `f14bbe28697778b7c2427d4b71c7fac24b78f8f4`. Upstream YuE reference inspected at `0edaf2f4053ef4731334b8329834b107977f9637`.

## Why songs cut off at the selected duration

The installed `YuE2GenerateMusic` converts `max_duration` directly to `round(seconds × 25)` semantic-token iterations. That is a **hard generation budget**, not a desired song length. Sampling stops naturally if it emits `MUSIC_END` first. Otherwise it retains the generated tokens, records truncation, and still synthesizes a playable file. `max_duration=60` therefore can cut an unfinished phrase at exactly 60 seconds. [Installed node](https://github.com/Comfy-Org/ComfyUI/blob/f14bbe28697778b7c2427d4b71c7fac24b78f8f4/comfy_extras/nodes_yue2.py#L65), [sampling loop](https://github.com/Comfy-Org/ComfyUI/blob/f14bbe28697778b7c2427d4b71c7fac24b78f8f4/comfy/text_encoders/yue2.py#L151)

The real completion signal is already available in `conditioning[0][1]["yue2_truncated"]`. It is true when semantic sampling reaches its budget. Native `YuE2GenerateMusic` exposes only conditioning and actual seconds, so the studio's ordinary audio-output history does not receive this boolean. The ABC generator also discards its internal truncation boolean; its returned token count equaling `max_abc_tokens` identifies a cap hit because an emitted end token is excluded from the returned IDs. [Metadata](https://github.com/Comfy-Org/ComfyUI/blob/f14bbe28697778b7c2427d4b71c7fac24b78f8f4/comfy/text_encoders/yue2.py#L233)

Long style, lyrics, and ABC consume the same **24,576-token context**. ComfyUI may lower the effective music budget to fit them. Duration alone cannot reliably diagnose truncation. The default music minimum is 200 tokens (8 seconds), during which EOS is suppressed; a budget of 8 seconds or less cannot finish naturally under this wrapper. [Budget and minimum](https://github.com/Comfy-Org/ComfyUI/blob/f14bbe28697778b7c2427d4b71c7fac24b78f8f4/comfy/text_encoders/yue2.py#L242)

### Implemented studio behavior

The studio now limits every new generation to **240 seconds** at the user’s request. Natural mode disables the desired-length selector and uses an automatic, conservative 180-second lyric brief; timed clips use the selected length up to 240 seconds. Previously saved longer takes are retained. The 331.64-second test below was performed before this policy change.

1. Natural mode chooses the duration automatically with a four-minute maximum. A conservative three-minute lyric brief leaves room for an intro, breathing, instrumental passages, and an ending.
2. Return and retain ABC text, ABC completion, semantic completion, requested generation budget, and actual duration as small per-job metadata. The bridge below exposes the completion signals without changing model math. Comfy's context-reduced effective budget is not separately present in its output metadata.
3. A capped result should remain a reviewable draft, clearly marked incomplete. Do not automatically present or enqueue it as a completed song.
4. Library offers editing for a new take within four minutes, retaining the original attempt and its seed/settings/cover. A saved score can be explicitly reused through the score action. This is regeneration, not a guaranteed seamless extension.
5. There are no automatic generation retries. If a new attempt hits another limit, it remains for review; the user can revise lyrics, score, or budget. A fade-out does not complete missing lyrics or musical phrases.
6. Keep the latent duration connected to the generator's **actual seconds output** and package the full generated audio. Changing only the latent length fails a model shape check; padding the video cannot extend the song. [Length requirement](https://github.com/Comfy-Org/ComfyUI/blob/f14bbe28697778b7c2427d4b71c7fac24b78f8f4/comfy/ldm/yue2/model.py#L62)

A larger budget gives natural endings room but does not guarantee one. The new AI Crossroads take reached EOS at 331.64 seconds with a 900-second safety ceiling on this 16 GB card. That validates this song and configuration, not maximum-context memory use or every sampler combination.

### Implemented completion bridge

Source: [runtime_nodes/boogie_yue2/__init__.py](runtime_nodes/boogie_yue2/__init__.py). The API workflows now include:

- **Node 8, `BoogieYuE2GenerateABC`**, replaces the native ABC node in the planned workflow. It uses the same `clip.tokenize`, `clip.generate`, and `clip.decode` calls and the same sampling settings. Output 0 is ABC text; output 1 is `abc_truncated`, calculated from the raw generated token count before decoding. Counting the music conditioning's re-tokenized ABC would not be an exact completion test.
- **Node 9, `BoogieYuE2Report`**, consumes `conditioning=["2",0]`, `clip=["1",1]`, `planning_generated`, and `abc_truncated`. Generated plans link the last input to `["8",1]`; supplied ABC and direct mode use `planning_generated=false`, `abc_truncated=false` because no planning generation was performed. The score is decoded from the actual music conditioning.
- Comfy history returns `outputs["9"]["text"][0]` containing JSON `{schema_version:1, semantic_truncated:boolean, frames:integer, seconds:number, abc:string, abc_truncated:boolean}`. The semantic boolean is copied exactly from native `yue2_truncated`; seconds are native frames / 25. Missing, ambiguous, or invalid metadata raises an error. A false truncation flag means a model end token was reached; it does not establish that the song sounds satisfactory or sang every lyric.

`scripts/install-music-nodes.sh` links only this owned package into the pinned runtime. Setup and startup invoke it; startup keeps all other custom nodes disabled using `--disable-all-custom-nodes --whitelist-custom-nodes boogie_yue2`. Installation does not restart the running service. The bridge was loaded in the coordinated music-service restart on September 15, 2026.

Verification: `python -m unittest discover -s runtime_nodes/tests -v` passes 13 CPU-only tests covering exact cap flags, early end, supplied/direct scores, invalid metadata, sampling argument parity, and workflow links. Shell syntax checks pass. Live registration passed after restart. The new AI Crossroads run returned 8,291 semantic frames / 331.64 seconds, `semantic_truncated=false`, `abc_truncated=false`, and a 3,666-character saved ABC score. The complete application test suite passed 191 tests plus 32 subtests.

## Creative controls already supported by the installed model

### Song and composition

| Studio control | Actual model mechanism | Constraint / meaning |
|---|---|---|
| Genre, language, mood, instruments, arrangement, vocal character, production style | `style` text | These are prompt preferences, not separate enforced model fields. Let the user edit the complete style text. |
| Lyrics and section structure | `lyrics` text | Editable words with `[Verse]`, `[Chorus]`, etc. No forced phoneme-to-note alignment input. |
| Compose melody and chords | `YuE2GenerateABC.mode="full"`, then matching music mode | The current planned workflow's mode. |
| Compose melody only | ABC and music `mode="melody"` | Leaves harmony freer; does not strip chords from a supplied score automatically. |
| Direct generation | Empty `abc` | Native music node silently changes the effective mode to `off` whenever ABC is empty. |
| Use/edit a composition | Supply `abc` string and matching full/melody mode | Bypasses ABC planning. Export, inspect, edit, and reuse the score in the studio. |
| Tempo, meter, key, melody notes, chord progression, instrumental melody | Native ABC plus consistent style text | Symbolic conditioning provides stronger control; generated audio is still not guaranteed to reproduce every event exactly. |
| Variation / reproducibility | Seeds on ABC, music, and acoustic sampler nodes | Comfy permits independent stage seeds; lock the score when varying only a performance. Changed settings can change audio even with the same seed. |

The bounded native ABC format uses `Vocal` and `Ins` monophonic voices, a tempo/meter/key header, and quoted chord symbols. Preserve exported rhythm units and section boundaries. Arbitrary ABC syntax, polyphonic stacks, phoneme annotations, and MusicXML/MIDI pasted into this field are not established inputs. [Official ABC guide](https://github.com/multimodal-art-projection/YuE/blob/0edaf2f4053ef4731334b8329834b107977f9637/skills/yue2-music/references/abc-editing.md)

### Planning and music sampling

These ranges are the installed node schema, not recommendations to use extreme values. [Installed schemas](https://github.com/Comfy-Org/ComfyUI/blob/f14bbe28697778b7c2427d4b71c7fac24b78f8f4/comfy_extras/nodes_yue2.py#L9)

| Setting | ABC planning default / range | Music default / range |
|---|---|---|
| Temperature | 0.7 / 0–5 | 1.0 / 0–5 |
| Top-p | 0.9 / 0.01–1 | 0.95 / 0.01–1 |
| Top-k | 30 / 1–32,768 | 100 / 1–32,768 |
| Repetition penalty | 1.005 / 0.01–10 | 1.2 / 0.01–10 |
| Repetition window | 100 / 1–20,000 exposed | Hardcoded to 50 by the installed music encoder |
| Generation budget | 8,192 ABC tokens / 1–20,000 | 360 seconds / 0.04–900, then context-limited |
| Minimum before EOS | `min(32, ABC budget)` | `min(200, effective music budget)` |
| Semantic text guidance (CFG) | No ABC CFG | Underlying tokenizer defaults 1.0 in planned modes and 1.01 in direct mode; **not exposed by native music node** |

Temperature zero chooses the highest-scoring next token. Top-p/top-k restrict the candidate tokens; repetition penalty changes recently used token scores. These are generation behavior controls, not direct knobs for emotion, vocal volume, or recording quality. Semantic guidance can be exposed by a small wrapper passing `cfg_scale` to the existing tokenizer; it should be labeled separately from acoustic CFG. [Tokenizer and sampling](https://github.com/Comfy-Org/ComfyUI/blob/f14bbe28697778b7c2427d4b71c7fac24b78f8f4/comfy/text_encoders/yue2.py#L29)

### Acoustic rendering and decoding

The existing official Comfy blueprint uses **32 steps, `dpm_2`, `sgm_uniform`, acoustic CFG 1, denoise 1**. These belong to the flow-based **acoustic synthesis stage**, not the VAE. [Pinned blueprint](https://github.com/Comfy-Org/ComfyUI/blob/f14bbe28697778b7c2427d4b71c7fac24b78f8f4/blueprints/Text%20to%20Music%20%28YuE2%29.json)

- `KSampler` exposes noise seed, steps (1–10,000), acoustic CFG (0–100), sampler, scheduler, and denoise (0–1). Its generic schema defaults are not YuE2 defaults; retain the blueprint values when resetting the studio.
- Live `/object_info/KSampler` advertises 45 samplers and 9 schedules in this installation. They can populate an experimental section, but availability does not prove that every combination is useful or valid for YuE2. The verified combination above should remain the reset preset.
- Acoustic CFG operates on acoustic conditioning. It does **not** change the earlier semantic token guidance; exposing it as a single generic “prompt strength” would be misleading.
- `VAEDecodeAudio` has only samples and VAE inputs. The tiled version adds tile size (default 512, range 32–8,192) and overlap (default 64, range 0–1,024) for memory management. There is no VAE diffusion-step setting.
- Checkpoint/precision and decoder choice are distinct from creative instructions. Only the installed INT8 checkpoint is verified locally. The author's listening and benchmark decoders are different; a decoder comparison should reuse the same acoustic latents.

Sources: [KSampler schema](https://github.com/Comfy-Org/ComfyUI/blob/f14bbe28697778b7c2427d4b71c7fac24b78f8f4/nodes.py#L1597), [audio decoder nodes](https://github.com/Comfy-Org/ComfyUI/blob/f14bbe28697778b7c2427d4b71c7fac24b78f8f4/comfy_extras/nodes_audio.py#L98), [official decoder guidance](https://huggingface.co/m-a-p/YuE2-Vae).

## Reference audio, continuation, and selective editing

**A reference-song route exists through transcription:** `AudioEncoderLoader` loads SheetSage2; `SheetSage2AudioToABC` converts a recording to melody/full ABC; YuE2 then uses that score plus lyrics and the desired style. SheetSage2 weights are not part of the currently installed text-to-song setup. This route preserves musical structure, not a guaranteed singer identity. [Installed reference nodes](https://github.com/Comfy-Org/ComfyUI/blob/f14bbe28697778b7c2427d4b71c7fac24b78f8f4/comfy_extras/nodes_audio_encoder.py#L53)

The reviewed official request fields are `style`, `lyrics`, `cot`, `seed`, `abc`, `cfg_scale`, and `id`. There is no direct `reference_audio`, `reference_singer`, negative-prompt, edit-interval, or continuation field. Reusing a saved symbolic plan supports further generation stages; it is not an append-to-the-existing-audio endpoint. Score edits regenerate the song and do not promise unchanged audio outside edited bars. [Official generation/cover interface](https://github.com/multimodal-art-projection/YuE/blob/0edaf2f4053ef4731334b8329834b107977f9637/skills/yue2-music/references/generation-and-covers.md)

No supported seamless continuation, waveform inpainting, vocal cloning, or real-time audio chunk streaming API was found in the installed nodes or current official pipeline. Generic KSampler partial-step controls and tiled VAE decoding do not establish those music features.

## Comfy runtime versus direct upstream pipeline

**The studio can expose these controls without requiring the user to open ComfyUI.** Keep the proven loopback runtime behind the studio, add a small control/completion metadata bridge, and provide score generation, inspection, and reuse as first-class studio actions.

The official `YuE2Pipeline` offers separate `plan`, `generate_semantic`, `synthesize`, and `decode` calls, exact saved plan tokens, semantic/latent artifacts, model identities, and separate ABC/semantic truncation flags. That is useful for deeper reproducibility. Its current defaults differ: ABC budget 4,096, semantic budget 9,000, 32-step **midpoint** acoustic solver; only midpoint is accepted. Its sampling config exposes minimum tokens and repetition windows, and `cfg_scale` accepts 0–20. Unlike Comfy's music encoder, the reference sampler rejects an oversized prefix+budget instead of silently reducing it. [Protocol](https://github.com/multimodal-art-projection/YuE/blob/0edaf2f4053ef4731334b8329834b107977f9637/src/yue2/protocol.py), [pipeline](https://github.com/multimodal-art-projection/YuE/blob/0edaf2f4053ef4731334b8329834b107977f9637/src/yue2/pipeline.py), [sampler](https://github.com/multimodal-art-projection/YuE/blob/0edaf2f4053ef4731334b8329834b107977f9637/src/yue2/sampling.py)

Switching runtimes would need fresh dependency, quantization, performance, and audio validation. It does not by itself add seamless continuation or guarantee natural endings. The immediate fix is honest completion tracking and separate target/budget controls, not replacing a working engine.
