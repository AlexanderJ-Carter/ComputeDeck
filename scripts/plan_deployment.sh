#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
GPU_COUNT="${1:-8}"
ANSIBLE="${PROJECT_DIR}/.venv/bin/ansible-playbook"
PYTHON="${PROJECT_DIR}/.venv/bin/python"

if [[ ! -x "$ANSIBLE" ]]; then
    echo "Missing Ansible venv at ${PROJECT_DIR}/.venv" >&2
    exit 1
fi

case "$GPU_COUNT" in
    4|8|12|16) ;;
    *)
        echo "Usage: $0 [4|8|12|16]" >&2
        exit 1
        ;;
esac

cd "$PROJECT_DIR"

echo "Collecting GPU resources..."
"$ANSIBLE" -i inventory.ini playbooks/collect-resources.yml

echo "Parsing resources..."
"$PYTHON" scripts/parse_resources.py

echo "Selecting nodes for ${GPU_COUNT} GPUs..."
"$PYTHON" scripts/select_nodes.py --gpus "$GPU_COUNT"
