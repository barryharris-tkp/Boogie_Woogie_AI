#!/usr/bin/env bash
set -euo pipefail
project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ ! -x "$project_dir/.runtime/venv/bin/python" ]]; then
  echo "Music runtime missing. Run scripts/setup-music.sh first." >&2
  exit 1
fi
"$project_dir/scripts/install-music-nodes.sh"
export HF_HUB_OFFLINE=1 HF_HUB_DISABLE_TELEMETRY=1 DO_NOT_TRACK=1
cd "$project_dir/.runtime/ComfyUI"
exec "$project_dir/.runtime/venv/bin/python" main.py \
  --listen 127.0.0.1 --port "${MUSIC_PORT:-8188}" \
  --disable-all-custom-nodes --whitelist-custom-nodes boogie_yue2 --disable-api-nodes \
  --preview-method none --reserve-vram 2 --cache-none "$@"
