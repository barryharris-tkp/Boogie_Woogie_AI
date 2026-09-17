#!/usr/bin/env bash
set -euo pipefail
project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
custom_nodes="$project_dir/.runtime/ComfyUI/custom_nodes"
source_dir="$project_dir/runtime_nodes/boogie_yue2"
target_dir="$custom_nodes/boogie_yue2"
if [[ ! -d "$custom_nodes" || ! -f "$source_dir/__init__.py" ]]; then
  echo "Pinned ComfyUI runtime or Boogie YuE2 node source is missing." >&2
  exit 1
fi
if [[ -L "$target_dir" ]]; then
  if [[ "$(readlink -f -- "$target_dir")" != "$source_dir" ]]; then
    echo "A different boogie_yue2 node is already installed; refusing to replace it." >&2
    exit 1
  fi
elif [[ -e "$target_dir" ]]; then
  echo "A different boogie_yue2 node is already installed; refusing to replace it." >&2
  exit 1
else
  ln -s -- "$source_dir" "$target_dir"
fi
