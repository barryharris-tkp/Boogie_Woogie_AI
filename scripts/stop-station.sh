#!/usr/bin/env bash
set -euo pipefail
# Stop the studio first so its shutdown handler can stop its own FFmpeg broadcast.
for station_unit in boogie-studio.service boogie-music.service; do
  if systemctl --user cat "$station_unit" >/dev/null 2>&1; then
    systemctl --user stop "$station_unit"
  fi
done
echo "Boogie Woogie AI studio and music services stopped."
