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
  "$NODE_BIN" --check demo/app.js
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

nav_views = set(re.findall(r'data-view="([^"]+)"', html))
# only count sidebar items that exist as view panels
panels = set(re.findall(r'data-view-panel="([^"]+)"', html))
nav_hit = sorted(nav_views & panels)
assert len(nav_hit) >= 8, f"sidebar/view panels too thin: {nav_hit}"

console = re.search(r'<section[^>]*id="batch-console-wrap"[^>]*>', html)
assert console, "batch-console-wrap missing"
assert "hidden" in console.group(0) and "idle" in console.group(0), "console dock must start hidden+idle"
assert "seedConsole(" not in js, "seedConsole calls must stay removed"
assert re.search(
    r"function showSteps\s*\([\s\S]*?\n\}\n\nasync function api\s*\(",
    js,
), "showSteps must close before api() — unclosed function breaks all UI tabs"

print(f"ok: {len(ids)} ids, {len(refs)} js refs, nav={nav_hit}")
PY

echo "== VS Code 扩展清单 =="
"$PY" - <<'PY'
import json
from pathlib import Path

pkg_path = Path("extensions/vscode/package.json")
pkg = json.loads(pkg_path.read_text(encoding="utf-8"))
base = pkg_path.parent
contributes = pkg.get("contributes") or {}
commands = {row.get("command") for row in contributes.get("commands") or []}
steps = 0
for wt in contributes.get("walkthroughs") or []:
    rows = wt.get("steps") or []
    assert rows, f"walkthrough {wt.get('id')} 没有 steps"
    steps += len(rows)
    for step in rows:
        md = (step.get("media") or {}).get("markdown")
        if md:
            assert (base / md).is_file(), f"缺少 walkthrough 媒体文件: {md}"
        for event in step.get("completionEvents") or []:
            if event.startswith("onCommand:"):
                assert event.split(":", 1)[1] in commands, f"completionEvent 引用了未注册命令: {event}"
print(f"ok: {len(commands)} commands, {steps} walkthrough steps")
PY
echo "all checks passed"
