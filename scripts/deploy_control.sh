#!/usr/bin/env bash
# 把当前工作树同步到 control 主机并重启 UI（不改 config/local / state）。
#
# 用法::
#   CONTROL_HOST=codelab-617722461 ./scripts/deploy_control.sh
#   CONTROL_HOST=user@host CONTROL_DIR=/opt/ComputeDeck ./scripts/deploy_control.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HOST="${CONTROL_HOST:?请设置 CONTROL_HOST，例如 codelab-617722461}"
DIR="${CONTROL_DIR:-/home/sankuai/gpu-control}"

cd "$ROOT"
echo "== sync → ${HOST}:${DIR} =="
# 禁止 macOS 把 AppleDouble（._*）打进归档
export COPYFILE_DISABLE=1
export COPY_EXTENDED_ATTRIBUTES_DISABLE=1
TAR_FLAGS=(-cf -)
if tar --help 2>&1 | grep -q -- '--no-xattrs'; then
  TAR_FLAGS=(--no-xattrs --no-mac-metadata -cf -)
fi
tar \
  "${TAR_FLAGS[@]}" \
  --exclude='.venv' \
  --exclude='state' \
  --exclude='config/local' \
  --exclude='snapshots' \
  --exclude='__pycache__' \
  --exclude='.git' \
  --exclude='uv.lock' \
  --exclude='web/static/help' \
  --exclude='.DS_Store' \
  --exclude='demo' \
  --exclude='._*' \
  --exclude='**/._*' \
  . \
| ssh -o BatchMode=yes "$HOST" "mkdir -p '$DIR' && cd '$DIR' && tar xf - && find . -name '._*' -type f -delete"

echo "== restart UI =="
ssh -o BatchMode=yes "$HOST" "bash -s" <<REMOTE
set -euo pipefail
cd '$DIR'
find . -name '._*' -type f -delete 2>/dev/null || true
if [[ -f state/ui.pid ]]; then kill "\$(cat state/ui.pid)" 2>/dev/null || true; fi
pkill -f '[.]venv/bin/python web/server.py' 2>/dev/null || true
pkill -f '[p]ython web/server.py' 2>/dev/null || true
sleep 1
test -x .venv/bin/python
: > state/ui.log
nohup .venv/bin/python web/server.py >> state/ui.log 2>&1 </dev/null &
echo \$! > state/ui.pid
disown || true
sleep 2
curl -fsS http://127.0.0.1:8787/api/health
echo
echo "pid=\$(cat state/ui.pid)"
REMOTE

echo "== done =="
echo "本机隧道示例: ssh -N -L 8787:127.0.0.1:8787 ${HOST}"
