#!/usr/bin/env bash
set -euo pipefail
project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
python3 "$project_dir/scripts/check-system.py"
comfy_revision=f14bbe28697778b7c2427d4b71c7fac24b78f8f4
model_revision=8e6fcf0f23252ed188b634bd50d44f4b01fba890
model_sha256=96fe199377309001ed8cd26a944baeee8cc31a20ba7c36d1d3c0a7e1f4149db6
runtime_dir="$project_dir/.runtime"
mkdir -p "$runtime_dir"
if [[ ! -d "$runtime_dir/ComfyUI/.git" ]]; then
  git clone --no-checkout https://github.com/Comfy-Org/ComfyUI.git "$runtime_dir/ComfyUI"
  git -C "$runtime_dir/ComfyUI" checkout --detach "$comfy_revision"
fi
if [[ "$(git -C "$runtime_dir/ComfyUI" rev-parse HEAD)" != "$comfy_revision" ]]; then
  echo "ComfyUI exists at a different revision; keep or move it before reinstalling." >&2
  exit 1
fi
if [[ ! -x "$runtime_dir/venv/bin/python" ]]; then
  uv venv --python 3.12 "$runtime_dir/venv"
fi
uv pip install --python "$runtime_dir/venv/bin/python" \
  -r "$project_dir/workflows/music-runtime-requirements.lock.txt" --torch-backend cu130
model_path="$runtime_dir/ComfyUI/models/checkpoints/yue2_3b_int8_convrot.safetensors"
if [[ ! -f "$model_path" ]]; then
  curl -fL --retry 5 --continue-at - --output "$model_path.part" \
    "https://huggingface.co/Comfy-Org/YuE2/resolve/$model_revision/checkpoints/yue2_3b_int8_convrot.safetensors"
  printf '%s  %s\n' "$model_sha256" "$model_path.part" | sha256sum --check
  mv "$model_path.part" "$model_path"
fi
printf '%s  %s\n' "$model_sha256" "$model_path" | sha256sum --check
"$runtime_dir/venv/bin/python" - "$model_path" <<'PY'
import sys
from safetensors import safe_open
with safe_open(sys.argv[1], framework="pt", device="cpu") as model:
    print(f"YuE2 checkpoint ready: {len(model.keys())} tensors")
PY
"$project_dir/scripts/install-music-nodes.sh"
echo "Ready. Run scripts/start-music.sh (loopback port 8188)."
