#!/usr/bin/env bash
set -euo pipefail
project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$project_dir"
if [[ ! -x .venv/bin/python ]]; then
  uv_bin="$(command -v uv || true)"
  if [[ -z "$uv_bin" && -x "$HOME/.local/bin/uv" ]]; then
    uv_bin="$HOME/.local/bin/uv"
  fi
  if [[ -z "$uv_bin" ]]; then
    echo "Install uv before starting the station: https://docs.astral.sh/uv/" >&2
    exit 1
  fi
  "$uv_bin" sync --frozen --python 3.11
fi
if [[ ! -f web/dist/index.html ]]; then
  echo "Studio frontend missing: web/dist/index.html" >&2
  exit 1
fi
mkdir -p .runtime
./scripts/start-music-service.sh start
if ! systemctl --user is-active --quiet boogie-studio.service; then
  systemd-run --user --unit=boogie-studio --collect \
    --property="WorkingDirectory=$project_dir" \
    --property=Restart=on-failure --property=RestartSec=5 \
    --property=TimeoutStopSec=30 --property=UMask=0077 \
    --property="StandardOutput=append:$project_dir/.runtime/studio.log" \
    --property="StandardError=append:$project_dir/.runtime/studio.log" \
    "$project_dir/.venv/bin/python" -m uvicorn station.app:app \
    --host 127.0.0.1 --port 8765 --no-access-log
fi
for attempt in {1..30}; do
  if curl --connect-timeout 1 --max-time 2 -fsS http://127.0.0.1:8765/ >/dev/null 2>&1; then
    echo "Boogie Woogie AI: http://127.0.0.1:8765"
    echo "Services run for this session; login autostart is not enabled."
    exit 0
  fi
  sleep 1
done
echo "Studio did not become ready. Check .runtime/studio.log and systemctl --user status boogie-studio.service." >&2
exit 1
