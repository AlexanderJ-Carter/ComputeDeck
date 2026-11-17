#!/usr/bin/env bash
# 把当前工作树同步到 control 主机并重启 UI（不改 config/local / state）。
#
# 用法::
#   CONTROL_HOST=control.example.com ./scripts/deploy_control.sh
#   CONTROL_HOST=user@host CONTROL_DIR=/opt/ComputeDeck ./scripts/deploy_control.sh
set -euo pipefail

SSH_OPTS=(-o BatchMode=yes -o ConnectTimeout=10 -o ServerAliveInterval=15 -o ServerAliveCountMax=3)

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HOST="${CONTROL_HOST:?请设置 CONTROL_HOST，例如 control.example.com}"
DIR="${CONTROL_DIR:-}"
if [[ -z "$DIR" ]]; then
  # 默认沿用远端家目录下的 gpu-control；不在仓库里写死某台机器的绝对路径。
  DIR="$(ssh "${SSH_OPTS[@]}" "$HOST" 'cd "$HOME/gpu-control" 2>/dev/null && pwd || printf "%s" "$HOME/gpu-control"')"
fi
[[ -n "$DIR" ]] || { echo "无法确定远端目录，请设置 CONTROL_DIR" >&2; exit 1; }

cd "$ROOT"

if [[ "${SKIP_BACKUP:-0}" != "1" ]]; then
  echo "== backup config/local + state → ${HOST}:~ =="
  ssh "${SSH_OPTS[@]}" "$HOST" "set -e; cd '$DIR'; ts=\$(date +%Y%m%d-%H%M%S); \
    tar czf \"\$HOME/gpu-control-backup-\$ts.tar.gz\" \
      --exclude='state/*.log' --exclude='state/snapshots' config/local state 2>/dev/null || true; \
    ls -lh \"\$HOME/gpu-control-backup-\$ts.tar.gz\" 2>/dev/null | tail -1"
fi

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
| ssh "${SSH_OPTS[@]}" "$HOST" "mkdir -p '$DIR' && cd '$DIR' && tar xf - && find . -name '._*' -type f -delete"

# 文档站是构建产物，主工作树同步时排除；控制机没有 MkDocs 时单独带上。
if [[ -f web/static/help/index.html ]]; then
  echo "== sync docs → ${HOST}:${DIR}/web/static/help =="
  tar "${TAR_FLAGS[@]}" --exclude='._*' -C web/static/help . \
    | ssh "${SSH_OPTS[@]}" "$HOST" \
      "mkdir -p '$DIR/web/static/help' && tar xf - -C '$DIR/web/static/help' && find '$DIR/web/static/help' -name '._*' -type f -delete"
fi

echo "== restart UI =="
ssh "${SSH_OPTS[@]}" "$HOST" "bash -s" <<REMOTE
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
if [[ "${RESTART_GATEWAY:-0}" == "1" ]]; then
  echo "== restart gateway =="
  ./bin/gpu-control gateway ensure 2>&1 | tail -20 || true
fi
if [[ "${RESTART_WATCHDOG:-0}" == "1" ]]; then
  echo "== restart watchdog =="
  if [[ -f state/watchdog.pid ]]; then kill "\$(cat state/watchdog.pid)" 2>/dev/null || true; fi
  rm -f state/watchdog.pid state/watchdog.lock
  nohup ./bin/gpu-control watchdog --background --job "\$(python3 -c 'import json; d=json.load(open(\"config/local/cluster.json\")); print(d.get(\"default_job\") or \"shell-job\")')" >> state/watchdog.log 2>&1 </dev/null &
  disown || true
fi
REMOTE

echo "== done =="
echo "本机隧道示例: ssh -N -L 8787:127.0.0.1:8787 ${HOST}"
echo "若本次改了网关/LiteLLM：RESTART_GATEWAY=1 CONTROL_HOST=${HOST} $0"
echo "若本次改了 watchdog：RESTART_WATCHDOG=1 CONTROL_HOST=${HOST} $0"
