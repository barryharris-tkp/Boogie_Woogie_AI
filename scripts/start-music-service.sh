#!/usr/bin/env bash
set -euo pipefail
project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
action="${1:-start}"
case "$action" in
  start)
    if systemctl --user is-active --quiet boogie-music.service; then
      echo "Music service is already running at http://127.0.0.1:${MUSIC_PORT:-8188}"
      exit 0
    fi
    systemd-run --user --unit=boogie-music --collect \
      --property=Restart=on-failure --property=RestartSec=5 \
      --property="StandardOutput=append:$project_dir/.runtime/music.log" \
      --property="StandardError=append:$project_dir/.runtime/music.log" \
      --setenv="MUSIC_PORT=${MUSIC_PORT:-8188}" \
      "$project_dir/scripts/start-music.sh"
    echo "Starting music service. Check: scripts/start-music-service.sh status"
    ;;
  status)
    systemctl --user status boogie-music.service --no-pager
    curl -fsS "http://127.0.0.1:${MUSIC_PORT:-8188}/system_stats"
    printf '\n'
    ;;
  stop)
    systemctl --user stop boogie-music.service
    ;;
  *)
    echo "Usage: $0 [start|status|stop]" >&2
    exit 2
    ;;
esac
