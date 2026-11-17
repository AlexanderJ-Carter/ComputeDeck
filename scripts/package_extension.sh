#!/usr/bin/env bash
# 本地打包 ComputeDeck VS Code 扩展；产物在 extensions/vscode/*.vsix
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT/extensions/vscode"
exec npx --yes @vscode/vsce package --allow-missing-repository "$@"
