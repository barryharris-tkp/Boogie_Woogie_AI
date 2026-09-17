# Boogie Woogie AI

A local music studio that turns a song idea into original lyrics, music, matching cover artwork, and videos ready for continuous playback or streaming.

**YuE2 generates music locally through ComfyUI. Qwen writes lyrics locally through Ollama. Only optional OpenAI cover artwork needs a cloud API key.** You operate everything through the studio; the ComfyUI node editor is not required.

## Install with Codex or Claude Code

Give your agent this request after cloning or opening this repository:

> Read AGENTS.md and INSTALL.md. Check this computer's hardware and software, explain any blockers, and install Boogie Woogie AI using the pinned runtime. Preserve existing services and data. Verify the local studio and engines. Leave paid cover generation and public broadcasting off.

- [Agent instructions](AGENTS.md) — installation workflow, checks, project map, and boundaries.
- [Installation guide](INSTALL.md) — prerequisites, commands, downloads, troubleshooting, and private remote access.
- [Claude Code entry point](CLAUDE.md) — points to the same instructions.

The bundled installer targets **Linux x86_64 with NVIDIA and a systemd user session**. The tested computer has an RTX 4080 SUPER with 16 GB VRAM and 64 GB RAM. Installation checks require a 16 GB GPU, at least 32 GB installed RAM, and 40 GiB free space for setup headroom. Smaller systems, AMD, Apple Silicon, native Windows, and WSL are not validated by this installer. These checks do not guarantee generation speed or music quality.

```bash
python3 scripts/check-system.py
```

Once prerequisites are installed, run from the repository root:

```bash
uv sync --frozen --python 3.11
ollama pull qwen3.5:4b
./scripts/setup-music.sh
./scripts/start-station.sh
```

Open [the local studio](http://127.0.0.1:8765). For a first installation, follow [INSTALL.md](INSTALL.md) to start Ollama and verify all engines. Downloads require internet access; music and lyric generation then run locally.

## Models and required downloads

| Component | Official source | Used here |
|---|---|---|
| YuE2 music weights | [Comfy-Org/YuE2 on Hugging Face](https://huggingface.co/Comfy-Org/YuE2/tree/main) · [original YuE2-3B](https://huggingface.co/m-a-p/YuE2-3B) | `yue2_3b_int8_convrot.safetensors`; installer downloads the pinned 3.96 GB file and verifies SHA256 |
| ComfyUI music runtime | [Comfy-Org/ComfyUI](https://github.com/Comfy-Org/ComfyUI) | Isolated, pinned checkout; native YuE2 nodes plus this project's completion bridge |
| Qwen lyric writer | [Qwen3.5-4B on Hugging Face](https://huggingface.co/Qwen/Qwen3.5-4B) · [Qwen repository](https://github.com/QwenLM/Qwen3.5) · [Ollama model](https://ollama.com/library/qwen3.5:4b) | `ollama pull qwen3.5:4b`; CPU inference leaves GPU memory for music |
| Ollama | [Linux installation](https://docs.ollama.com/linux) | Local model server on port 11434 |
| uv / Python | [uv installation](https://docs.astral.sh/uv/getting-started/installation/) | uv manages Python 3.11 for the studio and Python 3.12 for music |
| FFmpeg / ffprobe | [Downloads](https://ffmpeg.org/download.html) | Audio inspection, cover/video packaging, and RTMP output; libx264, AAC, drawtext required |
| NVIDIA driver | [Drivers](https://www.nvidia.com/en-us/drivers/) · [CUDA compatibility](https://docs.nvidia.com/deploy/cuda-compatibility/minor-version-compatibility.html) | Driver 580+ for the pinned CUDA 13 PyTorch runtime; a separate CUDA Toolkit installation is not required |
| Git | [Downloads](https://git-scm.com/downloads) | Repository and pinned ComfyUI checkout |
| OpenAI artwork (optional) | [API keys](https://platform.openai.com/api-keys) · [Image generation guide](https://developers.openai.com/api/docs/guides/image-generation) | One server-side API key; paid cloud images |
| OBS / Tailscale (optional) | [OBS](https://obsproject.com/download) · [Tailscale](https://tailscale.com/download) | Browser-source streaming or private remote access |

Model files and third-party runtimes are downloaded separately, not committed to this repository. YuE2 weights declare **CC BY-NC 4.0**; do not assume this installation permits a monetized station. Qwen's model card declares Apache 2.0. Review [third-party sources and licenses](THIRD_PARTY.md) before distribution or commercial use.

## What the studio does

- **Create songs:** describe the subject, genre, and vocal qualities; generate or edit original lyrics and the music brief.
- **Control the model:** melody/chord planning, ABC score editing, independent seeds, sampling controls, acoustic rendering, and new takes. See [the control reference](YUE2_CONTROLS.md).
- **Let songs finish:** natural ending mode chooses its own length up to four minutes. A take that reaches the limit without a confirmed ending is retained for review and kept out of the queue. Timed clips use a chosen cap.
- **Match artwork to music:** each wide or vertical MP4 contains the song and its cover on one timeline. Missing or failed cloud artwork falls back to a local cover.
- **Manage the library:** each tile's **⋮** menu contains downloads, queue actions, new takes, artwork, and **Delete song** with confirmation. Active generation and broadcasting guard against unsafe deletion.
- **Play continuously:** ready songs loop through the local queue. Generate a library ahead of time.
- **Broadcast:** explicitly start an RTMP/RTMPS stream, or use `/player.html` as an OBS browser source. Platform streaming access and ingest details are required.

The built-in broadcaster uses a **snapshot of the queue**; restart the broadcast to apply queue changes. It retries disconnects with increasing delays up to 60 seconds, restarting the frozen playlist. A studio restart does not automatically restart a broadcast. Only one destination is configured at a time.

Audience-comment voting, automatic playlist replenishment, singer-reference analysis, crossfades, and proven unattended 24/7 operation remain future work. See [the feature plan](FEATURE_PLAN.md) and [historical runtime verification](RUNTIME_VERIFICATION.md). A successful installation is not a sustained-stream or subjective-quality benchmark.

## OpenAI API key controls

Open **Studio settings → OpenAI cover art**. The status card shows **API key saved on this PC**, **API key set for this session**, **API key supplied by your system**, or **No API key saved**.

- **Add / Replace API key** opens a masked field. **Remember this key on this PC** saves it to the ignored `.env.local` file with owner-only permissions. Uncheck for temporary use.
- **Delete API key** asks for confirmation, removes the saved value, and stops using it for new requests. It does not revoke the key in your OpenAI account or cancel a request already sent.
- **Save settings** changes artwork preferences independently of the key.

The key's value is never returned to the browser or stored in song records. Saved means configured, not verified: account permissions are checked when an actual image request is made. Merely saving, replacing, or deleting a key makes no paid call. Generated images incur charges; spending controls are estimates, not an account billing limit.

A key explicitly set in `.env.local` takes precedence over an inherited `OPENAI_API_KEY`. Deleting writes an empty local entry so an inherited key does not silently return after restart. A session-only replacement leaves the previous saved key in place; the status card explains that it returns after restart. No OpenAI key is needed for local music or lyrics.

## Start, stop, and storage

```bash
./scripts/start-station.sh
./scripts/stop-station.sh
```

The launcher creates transient user services with restart on failure. Login autostart is not enabled. Stopping the studio also stops its broadcaster and music runtime; Ollama and other applications remain running. Keep ports 8765, 8188, and 11434 on loopback. Optional private [Tailscale setup](INSTALL.md#private-access-with-tailscale) is documented separately.

| Location | Contents |
|---|---|
| `station/`, `web/dist/` | Python API and static studio UI; no frontend build step |
| `data/` | Private database, original songs, artwork, videos, and optional network configuration |
| `.env.local` | Optional saved OpenAI key; ignored by Git |
| `.venv/` | Studio environment locked by `uv.lock` |
| `.runtime/` | Downloaded ComfyUI, music environment, checkpoint, working files, and logs |
| `workflows/`, `runtime_nodes/` | Versioned workflow graphs, dependency pins, and completion bridge |

Back up `data/` with the studio stopped. Back up credentials separately if desired. Never commit personal media, keys, logs, runtime downloads, or local network configuration. [Release preparation](RELEASING.md) describes the checks before the first GitHub publication.
