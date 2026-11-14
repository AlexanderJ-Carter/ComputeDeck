#!/usr/bin/env bash
# Bootstrap a fresh clone for local use (does not commit secrets).
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if command -v uv >/dev/null 2>&1; then
  uv venv .venv
  uv pip install -r requirements.txt
else
  python3 -m venv .venv
  .venv/bin/pip install -U pip
  .venv/bin/pip install -r requirements.txt
fi

mkdir -p config/local/jobs state snapshots
[[ -f config/local/cluster.json ]] || cp config/examples/cluster.example.json config/local/cluster.json
[[ -f config/local/nodes.json ]] || cp config/examples/nodes.example.json config/local/nodes.json
if [[ ! -f config/local/jobs/shell-job.json ]]; then
  # Domain-specific jobs (any name) can be added later under config/local/jobs/
  cp config/examples/shell-job.example.json config/local/jobs/shell-job.json
fi

# Optional root compat symlinks (gitignored)
ln -sfn config/local/cluster.json cluster.json
ln -sfn config/local/nodes.json nodes.json

mkdir -p "$HOME/.local/bin"
ln -sfn "$ROOT/bin/gpu-control" "$HOME/.local/bin/gpu-control"

echo "Edit config/local/*.json then run:"
echo "  export PATH=\"\$HOME/.local/bin:\$PATH\""
echo "  gpu-control doctor"
echo "  gpu-control node render"
echo "  gpu-control ui --background"
