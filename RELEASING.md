# Preparing the first GitHub release

The repository is prepared locally. Creating a remote, selecting visibility and a project license, committing, and pushing are separate owner-directed steps.

## Suggested GitHub About description

Local AI music studio with YuE2/ComfyUI music, Qwen lyrics, optional OpenAI cover art, synchronized videos, and continuous playback. Agent-assisted Linux/NVIDIA installation.

Use the README's **Models and required downloads** section for the clickable YuE2 and Qwen Hugging Face links, source repositories, and prerequisites. The About field is a short summary, not the installation guide.

## Before publishing

1. Select repository name, visibility, and the application source license with the owner. No application license is selected yet. Preserve the separate third-party/model license notices in `THIRD_PARTY.md`.
2. Review `git status --short` and `git ls-files --cached --others --exclude-standard`. Confirm that `.env.local`, `data/`, `.runtime/`, `.venv/`, model weights, logs, and personal exports are excluded. The only credential template allowed is `.env.example`, with an empty value.
3. Scan only candidate source files for accidentally embedded credentials or private machine information. Report file/line and finding type, never a matched secret. Do not read ignored key files as part of this check. Re-run after any new staging changes.
4. Run relevant tests and the read-only hardware check. Record current verification honestly: passing on the original workstation is not a completed installation on a fresh computer. A fresh-host full download and inference acceptance test remains separate.
5. Review staged changes before the owner-authorized commit/push. Never stage with force or include `data/` as example content. If personal samples are desired later, choose and review them separately.

No OpenAI request, song generation, public broadcast, GitHub creation, or push is needed merely to prepare a source release.
