#!/usr/bin/env bash
# Offline sanity checks: syntax + unit tests + static UI audit.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="${PYTHON:-$ROOT/.venv/bin/python}"
cd "$ROOT"

echo "== py_compile =="
"$PY" -m py_compile web/server.py bin/gpu-control lib/*.py scripts/*.py examples/*.py mcp_server/*.py

echo "== unit tests =="
"$PY" -m unittest discover -s tests -v
# tests/test_api_catalog_mcp.py 会核对 mcp_tools_summary ↔ mcp_server @mcp.tool

NODE_BIN="${NODE_BIN:-$(command -v node || true)}"
if [[ -n "$NODE_BIN" ]] && "$NODE_BIN" -e 'process.exit(Number(process.versions.node.split(".")[0]) >= 16 ? 0 : 1)' 2>/dev/null; then
  echo "== javascript syntax =="
  "$NODE_BIN" --check web/static/app.js
  "$NODE_BIN" --check web/static/api-docs.js
  "$NODE_BIN" --check web/static/theme-boot.js
  "$NODE_BIN" --check web/static/xterm-fit.js
  "$NODE_BIN" --check extensions/vscode/extension.js
else
  echo "== javascript syntax skipped (need Node >= 16; set NODE_BIN) =="
fi

echo "== static UI audit =="
"$PY" - <<'PY'
import re
from pathlib import Path

html = Path("web/static/index.html").read_text(encoding="utf-8")
js = Path("web/static/app.js").read_text(encoding="utf-8")
ids = re.findall(r'\bid="([^"]+)"', html)
assert len(ids) == len(set(ids)), "duplicate HTML ids"
refs = set(re.findall(r'getElementById\("([^"]+)"\)', js))
refs |= set(re.findall(r'\$\("([^"]+)"\)', js))
refs |= set(re.findall(r'\bon\("([^"]+)"', js))
missing = sorted(x for x in refs if x not in ids)
assert not missing, f"JS references missing ids: {missing}"
assert "fonts.googleapis.com" not in html and "cdn.jsdelivr.net" not in html
print(f"ok: {len(ids)} ids, {len(refs)} js refs")
PY
echo "all checks passed"
