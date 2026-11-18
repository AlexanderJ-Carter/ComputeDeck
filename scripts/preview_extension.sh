#!/usr/bin/env bash
# Start extension preview against a live ComputeDeck control plane.
#
# Usage:
#   ./scripts/preview_extension.sh
#   ./scripts/preview_extension.sh --api http://127.0.0.1:8787
#   CONTROL_HOST=codelab-617722461 ./scripts/preview_extension.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
API_BASE="${API_BASE:-http://127.0.0.1:8787}"
CONTROL_HOST="${CONTROL_HOST:-}"
EXT="${ROOT}/extensions/vscode"
TOKEN_FILE="${TMPDIR:-/tmp}/computedeck-preview-token"
USER_DATA="${TMPDIR:-/tmp}/computedeck-ext-preview-profile"
ISSUE_OUT="${TMPDIR:-/tmp}/computedeck-issue.out"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --api)
      API_BASE="${2:?}"
      shift
      ;;
    --host)
      CONTROL_HOST="${2:?}"
      shift
      ;;
    -h|--help)
      sed -n '2,8p' "$0" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *)
      echo "unknown arg: $1" >&2
      exit 2
      ;;
  esac
  shift
done

echo "== probe control ${API_BASE} =="
curl -fsS "${API_BASE}/api/health" >/dev/null
curl -fsS "${API_BASE}/api/auth/status" | head -c 220
echo

echo "== issue preview token from control =="
if [[ -n "${CONTROL_HOST}" ]]; then
  ssh -o BatchMode=yes -o ConnectTimeout=12 "${CONTROL_HOST}" \
    'cd ~/gpu-control && .venv/bin/python bin/gpu-control auth issue-token' \
    >"${ISSUE_OUT}"
else
  (
    cd "${ROOT}"
    if [[ -x .venv/bin/python ]]; then
      .venv/bin/python bin/gpu-control auth issue-token
    else
      python3 bin/gpu-control auth issue-token
    fi
  ) >"${ISSUE_OUT}"
fi

TOKEN="$(sed -n 's/^token=//p' "${ISSUE_OUT}" | head -1 | tr -d '[:space:]')"
rm -f "${ISSUE_OUT}"
if [[ -z "${TOKEN}" ]]; then
  echo "!! failed to issue token" >&2
  exit 1
fi
umask 077
printf '%s' "${TOKEN}" >"${TOKEN_FILE}"
echo "token saved to ${TOKEN_FILE} (not printed)"

echo "== open web control UI =="
if command -v open >/dev/null 2>&1; then
  open "${API_BASE}/"
elif command -v xdg-open >/dev/null 2>&1; then
  xdg-open "${API_BASE}/" >/dev/null 2>&1 || true
fi

mkdir -p "${USER_DATA}/User"
cat >"${USER_DATA}/User/settings.json" <<EOF
{
  "computedeck.apiBase": "${API_BASE}",
  "computedeck.pollIntervalSec": 10,
  "computedeck.statusBar": true,
  "computedeck.mcpPath": "${ROOT}/bin/gpu-control-mcp"
}
EOF

CURSOR_BIN="${CURSOR_BIN:-}"
if [[ -z "${CURSOR_BIN}" ]]; then
  if [[ -x "/Applications/Cursor.app/Contents/Resources/app/bin/cursor" ]]; then
    CURSOR_BIN="/Applications/Cursor.app/Contents/Resources/app/bin/cursor"
  elif command -v cursor >/dev/null 2>&1; then
    CURSOR_BIN="$(command -v cursor)"
  elif command -v code >/dev/null 2>&1; then
    CURSOR_BIN="$(command -v code)"
  else
    echo "!! Cursor / VS Code CLI not found" >&2
    exit 1
  fi
fi

echo "== launch Extension Development Host =="
echo "open ComputeDeck view in the new window; Command Palette: ComputeDeck"
export GPU_CONTROL_API_TOKEN
GPU_CONTROL_API_TOKEN="$(cat "${TOKEN_FILE}")"
exec "${CURSOR_BIN}" \
  --user-data-dir "${USER_DATA}" \
  --extensions-dir "${USER_DATA}/extensions" \
  --extensionDevelopmentPath "${EXT}" \
  "${ROOT}"
