# Install Boogie Woogie AI

This guide is for a person or an AI agent installing a **new Linux x86_64 / NVIDIA system**. Run commands as the desktop user from the cloned repository root. Do not run the studio as root. Read [AGENTS.md](AGENTS.md) for the agent workflow and [README.md](README.md#models-and-required-downloads) for official downloads.

## 1. Inspect before downloading

```bash
python3 scripts/check-system.py
# Machine-readable form for agents:
python3 scripts/check-system.py --json
```

The script is read-only, makes no cloud requests, and returns exit code 1 if prerequisites fail. Install Python 3 through the operating system first if it is missing. It checks:

- Linux x86_64, an NVIDIA GPU with at least 16,000 MiB VRAM and compute capability 7.5+, and driver branch 580 or newer for CUDA 13.
- At least 30 GiB visible RAM (approximately 32 GB installed); the tested system has 64 GB.
- At least 40 GiB free on the repository volume. This is conservative setup headroom, not a measured minimum. Allow more for a growing library; check free space on Ollama's model-storage volume too.
- Git, curl, sha256sum, uv, Ollama, FFmpeg, ffprobe, systemctl, systemd-run, FFmpeg codecs/filters, and the current user's systemd session.

A warning about free GPU memory may reflect an already loaded music model. Identify its owner before stopping anything. With several GPUs, verify the selected device is suitable; memory across GPUs is not added together. Windows/WSL, macOS, AMD, smaller GPUs, and CPU-only music generation need a separately validated setup; do not run this installer while promising they will work.

These thresholds are this project's installation policy, not universal YuE2 requirements. The tested profile is RTX 4080 SUPER 16 GB / i9-14900F / 64 GB RAM. Hardware checks cannot establish song quality, speed, or continuous-stream reliability.

## 2. Install prerequisites

Use the distribution's supported package manager for Git, curl, coreutils (`sha256sum`), Python 3, and FFmpeg. FFmpeg must include **libx264**, **AAC**, and **drawtext**. Install [uv](https://docs.astral.sh/uv/getting-started/installation/) and [Ollama](https://docs.ollama.com/linux) from their official instructions, then make sure both are on PATH.

Use a working NVIDIA driver supported by the distribution. [NVIDIA documents driver 580+ for CUDA 13 compatibility](https://docs.nvidia.com/deploy/cuda-compatibility/minor-version-compatibility.html). Driver changes or a required reboot are separate system changes: explain them before acting. The pinned PyTorch packages supply the CUDA runtime; do not install an entire CUDA Toolkit just for this app.

A systemd **user** session must work (`systemctl --user show -p Version --value`). A root shell or a disconnected SSH session may not have the desktop user's service bus. Do not replace it with a privileged public server. The standard launcher uses ports **8765** (studio), **8188** (ComfyUI), and **11434** (Ollama). Identify existing listeners before starting; do not kill unrelated services or reuse an arbitrary ComfyUI installation.

## 3. Start the local lyric engine

If Ollama is already serving on loopback port 11434, reuse it without changing its other models. Otherwise start the service provided by its installation, or run the following in a separate terminal:

```bash
OLLAMA_HOST=127.0.0.1:11434 ollama serve
```

In your installation terminal:

```bash
ollama pull qwen3.5:4b
ollama list
curl --fail --silent --show-error http://127.0.0.1:11434/api/tags
```

The exact local model must be `qwen3.5:4b`. The studio requests CPU inference (`num_gpu: 0`) so Qwen does not compete with YuE2 for VRAM. Model tags may be updated upstream; record the local model ID from `ollama list` when collecting installation evidence. The [Qwen Hugging Face model](https://huggingface.co/Qwen/Qwen3.5-4B) is a source/reference link; the app uses the Ollama download, so do not download both copies unnecessarily.

## 4. Install the isolated studio and music runtime

After preflight passes:

```bash
uv sync --frozen --python 3.11
./scripts/setup-music.sh
./scripts/start-station.sh
```

`setup-music.sh` runs preflight before its large downloads. It installs into `.runtime/`, leaves other ComfyUI installations alone, and refuses an existing checkout at a different revision. It pins:

| Item | Identity |
|---|---|
| ComfyUI | `f14bbe28697778b7c2427d4b71c7fac24b78f8f4` |
| YuE2 repository revision | `8e6fcf0f23252ed188b634bd50d44f4b01fba890` |
| Checkpoint | `checkpoints/yue2_3b_int8_convrot.safetensors` from [Comfy-Org/YuE2](https://huggingface.co/Comfy-Org/YuE2/tree/8e6fcf0f23252ed188b634bd50d44f4b01fba890) |
| Checkpoint bytes | `3,960,938,800` |
| SHA256 | `96fe199377309001ed8cd26a944baeee8cc31a20ba7c36d1d3c0a7e1f4149db6` |
| Music environment | Python 3.12; packages in `workflows/music-runtime-requirements.lock.txt`, PyTorch CUDA 13 wheels |

The installer resumes a partial checkpoint download and verifies its checksum. It installs the project's small completion bridge as the only whitelisted custom node; cloud API nodes are disabled. SheetSage2 and the BF16 checkpoint are not needed for this text-to-song setup. Do not download the entire Hugging Face repository.

Do not upgrade pins or substitute packages to silence an installation failure. Record the failing step with secrets removed, then investigate compatibility. Initial downloads need GitHub, Hugging Face, Python/PyPI, PyTorch, and Ollama connectivity. Runtime music and lyrics are local; cover art needs OpenAI connectivity.

## 5. Verify readiness

```bash
curl --fail --silent --show-error http://127.0.0.1:8765/api/health
curl --fail --silent --show-error http://127.0.0.1:8188/system_stats
```

The studio health report should show `comfy`, `ollama`, `ollama_model_available`, and `ffmpeg` as true (`ffmpeg` covers both FFmpeg and ffprobe). Confirm the completion bridge is loaded by inspecting `http://127.0.0.1:8188/object_info`: `YuE2GenerateMusic`, `BoogieYuE2GenerateABC`, and `BoogieYuE2Report` must be present. A healthy HTTP server alone does not prove inference works.

```bash
.runtime/venv/bin/python -c 'import torch; print("CUDA available:", torch.cuda.is_available()); print("GPU:", torch.cuda.get_device_name(0) if torch.cuda.is_available() else "none")'
uv run --frozen --python 3.11 pytest -q
.runtime/venv/bin/python -m unittest discover -s runtime_nodes/tests -v
```

The tests use temporary data and dummy credentials; they do not generate paid artwork or use the user's song library. The runtime-node tests stub native nodes and do not generate music either.

Open [Studio](http://127.0.0.1:8765). For an end-to-end acceptance test, generate one short original song with **OpenAI cover art off** and **automatic queueing off**. Verify the lyric result, completed job, non-silent audio, matching fallback cover, wide/vertical video downloads, and actual ending status. A timed test can prove the pipeline but not a natural ending. Run this acceptance test only as part of an authorized installation/test; never unexpectedly generate into an existing user's library while doing maintenance. Report anything not exercised, including listening quality and public broadcasting.

## 6. Optional OpenAI artwork

Local songs work without a key. To enable cloud covers, the owner enters an existing OpenAI key directly into **Studio settings → OpenAI cover art → Add API key**. Agents must not request a key in chat or read one from unrelated projects.

**Remember this key on this PC** saves to `.env.local` (owner-only permissions, ignored by Git). Settings show whether the key is saved, session-only, or inherited from the system. Replace and Delete are available on the same card. Deleting removes the stored value and writes an explicit blank override so an inherited key cannot return after restart. Removing a key here does not revoke it at OpenAI.

You can use [.env.example](.env.example) as a reference for manual configuration; do not copy its blank value over an existing saved key. The app reads `.env.local` directly for this credential only. Other shell settings are not automatically loaded from that file. [OpenAI recommends keeping keys on the server](https://developers.openai.com/api/reference/overview#authentication); the studio never sends the saved value back to the browser.

Saving is not a billable validity test. Account/model access is checked on a real cover request, which costs money. Leave it off during automated installation unless explicitly authorized. Existing spending reservations remain even after a key is deleted.

## Private access with Tailscale

This is optional; configure it only when the owner requests remote access. Install [Tailscale](https://tailscale.com/download), sign in to the intended tailnet, and inspect `tailscale serve status` first. Keep the app and model servers on loopback. Do not enable Funnel or router port forwarding.

1. Determine the machine's actual MagicDNS HTTPS hostname. Choose an unused Serve port; these examples use **8443**.
2. Create or merge `data/network.json` with that exact origin (no trailing slash), preserving other approved entries. Example only:

   ```json
   {"allowed_origins": ["https://YOUR_DEVICE.YOUR_TAILNET.ts.net:8443"]}
   ```

3. With generation, playback, and broadcast idle, restart **only** the studio to load the allowlist:

   ```bash
   systemctl --user restart boogie-studio.service
   tailscale serve --bg --https=8443 http://127.0.0.1:8765
   ```

4. Open the actual HTTPS address from another approved tailnet device and verify page, API, playback, and downloads. HTTPS/Serve availability and tailnet access policy depend on the account configuration. The app trusts access through this private network; it has no separate multi-user login. Grant access only to trusted operators who may create/delete songs and spend on artwork.

To disable just this mapping, use `tailscale serve --https=8443 off`. Do not reset unrelated Serve mappings. The network file is private and ignored by Git. Tailnet configuration persists independently of the transient studio services.

## Operations and troubleshooting

- **Start / stop:** `./scripts/start-station.sh` / `./scripts/stop-station.sh`. Restart-on-failure is configured; login autostart is not. Start again after reboot. Stopping the station leaves Ollama running.
- **Inspect:** `systemctl --user status boogie-studio.service boogie-music.service`; local logs are `.runtime/studio.log` and `.runtime/music.log`. Redact secrets before sharing logs.
- **Missing music engine:** inspect the ComfyUI service and checkpoint checksum; do not silently fall back to a remote paid music provider.
- **Missing Qwen:** verify `ollama list` contains the exact tag and port 11434 is reachable; do not stop another application's Ollama model without checking its use.
- **Out of memory:** check GPU processes and available system RAM. The runtime reserves 2 GB VRAM for the desktop. Reduce competing workloads; do not promise that quantization alone guarantees a fit.
- **Ending needs review:** the music or score budget ran out. Shorten lyrics/arrangement, then create a new take. This is not an installation failure; increasing the app's four-minute ceiling is not a repair.
- **Stale settings interface:** reload the page; the server sends current HTML/JS/CSS with no-store headers. Preserve any unsaved lyrics first.
- **Existing checkout update:** stop idle studio activity, preserve `data/` and `.env.local`, run `uv sync --frozen --python 3.11`, then restart. Do not delete a working runtime to apply a frontend-only change.
- **Backup/uninstall:** stop the station before copying its SQLite database and media. Preserve `data/` and `.env.local` unless removal is explicitly wanted. Downloaded runtimes can be recreated; personal songs cannot.
