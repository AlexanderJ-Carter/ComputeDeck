#!/usr/bin/env python3
"""对本机 Web UI 做非破坏性冒烟验收。用法: ./.venv/bin/python scripts/smoke_ui.py"""

from __future__ import annotations

import base64
import json
import os
import socket
import struct
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlencode

ROOT = Path(__file__).resolve().parents[1]
BASE = os.environ.get("GPU_CONTROL_UI", "http://127.0.0.1:8787")
results: list[tuple[bool, str, str]] = []


def _load_token() -> str:
    """Bearer token from env, or config/local/secrets/ui_api_token if present."""
    env = (os.environ.get("GPU_CONTROL_UI_TOKEN") or "").strip()
    if env:
        return env
    path = ROOT / "config" / "local" / "secrets" / "ui_api_token"
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


TOKEN = _load_token()


def _headers(extra: dict | None = None) -> dict:
    headers = dict(extra or {})
    if TOKEN:
        headers["Authorization"] = f"Bearer {TOKEN}"
    return headers


def report(name: str, cond: bool, detail: str = "") -> None:
    results.append((cond, name, detail))
    mark = "PASS" if cond else "FAIL"
    suffix = f" · {detail[:160]}" if detail and not cond else ""
    print(f"{mark} {name}{suffix}")


def get(path: str, timeout: int = 60):
    req = urllib.request.Request(BASE + path, headers=_headers())
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status, json.loads(resp.read().decode())


def get_text(path: str, timeout: int = 30) -> tuple[int, str]:
    req = urllib.request.Request(BASE + path, headers=_headers())
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status, resp.read().decode()


def get_status(path: str, timeout: int = 15) -> int:
    """Status code without auth header — used to prove the auth gate."""
    req = urllib.request.Request(BASE + path)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status
    except urllib.error.HTTPError as exc:
        return exc.code


def post(path: str, body: dict | None = None, timeout: int = 180):
    data = json.dumps(body or {}).encode()
    req = urllib.request.Request(
        BASE + path,
        data=data,
        headers=_headers({"Content-Type": "application/json"}),
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status, json.loads(resp.read().decode())


def first_node() -> str:
    _, ov = get("/api/overview")
    nodes = ov.get("nodes") or []
    if not nodes:
        raise RuntimeError("overview 无节点")
    return nodes[0]["name"]


def check_ws_terminal(node: str) -> None:
    key = base64.b64encode(os.urandom(16)).decode()
    qs = urlencode({"node": node, "cols": "80", "rows": "24"})
    host = BASE.split("://", 1)[1].split("/", 1)[0]
    auth = f"Authorization: Bearer {TOKEN}\r\n" if TOKEN else ""
    req = (
        f"GET /ws/terminal?{qs} HTTP/1.1\r\nHost: {host}\r\n"
        f"Upgrade: websocket\r\nConnection: Upgrade\r\n{auth}"
        f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n"
    ).encode()
    hostname, _, port_s = host.partition(":")
    port = int(port_s or "80")
    sock = socket.create_connection((hostname, port), timeout=10)
    sock.sendall(req)
    buf = b""
    while b"\r\n\r\n" not in buf:
        buf += sock.recv(4096)
    head = buf.split(b"\r\n\r\n", 1)[0]
    report("WS handshake 101", b"101" in head, head.split(b"\r\n", 1)[0].decode())

    def mask(data: bytes) -> bytes:
        m = os.urandom(4)
        return m + bytes(b ^ m[i % 4] for i, b in enumerate(data))

    def frame_text(text: str) -> bytes:
        raw = text.encode()
        n = len(raw)
        hdr = bytes([0x81, 0x80 | n]) if n < 126 else bytes([0x81, 0x80 | 126]) + struct.pack("!H", n)
        return hdr + mask(raw)

    sock.settimeout(2.5)
    try:
        while True:
            sock.recv(8192)
    except socket.timeout:
        pass

    sock.sendall(frame_text(json.dumps({"type": "input", "data": "echo TERM_OK; exit\n"})))
    out = b""
    sock.settimeout(3)
    try:
        while True:
            out += sock.recv(8192)
    except socket.timeout:
        pass
    sock.close()
    report("WS PTY input", b"TERM_OK" in out, f"bytes={len(out)}")


def main() -> int:
    gets = [
        "/api/health",
        "/api/overview",
        "/api/setup",
        "/api/doctor",
        "/api/quality",
        "/api/metrics",
        "/api/endpoints/lb?strategy=least_latency",
        "/api/gateway",
        "/api/jobs",
        "/api/jobs/templates",
        "/api/toolkits",
        "/api/leases",
        "/api/queue",
        "/api/config/cluster",
        "/api/config/nodes",
        "/api/setup/guide",
    ]
    for path in gets:
        try:
            status, _ = get(path)
            report(f"GET {path}", status == 200)
        except Exception as exc:  # noqa: BLE001
            report(f"GET {path}", False, str(exc))

    try:
        _, tk = get("/api/toolkits")
        ids = {t.get("id") for t in tk.get("toolkits") or []}
        report("工具栈含 ray/vllm", {"ray", "vllm"} <= ids, f"ids={sorted(ids)}")
        _, tpl = get("/api/jobs/templates")
        names = {t.get("id") for t in tpl.get("templates") or []}
        report("模板含 ray-cluster/vllm-serve", {"ray-cluster", "vllm-serve"} <= names, f"ids={sorted(names)}")
        report("模板无场景 decider", "decider" not in names, f"ids={sorted(names)}")
    except Exception as exc:  # noqa: BLE001
        report("新增模板/工具栈", False, str(exc))

    try:
        _, html = get_text("/")
        report(
            "页面中文导航",
            all(word in html for word in ("看板", "节点矩阵", "工具栈", "终端")),
            "缺少看板/节点矩阵/工具栈/终端",
        )
        report(
            "无外部 CDN 依赖",
            "fonts.googleapis.com" not in html and "cdn.jsdelivr.net" not in html,
            "页面仍引用外部字体或 CDN",
        )
        report(
            "本地 xterm 引用",
            "/static/vendor/xterm/" in html,
            "未使用本地 xterm",
        )
        _, css = get_text("/static/style.css")
        report("style.css", "--bg" in css or "[data-theme" in css or len(css) > 100)
        _, tokens = get_text("/static/tokens.css")
        report("tokens.css", "--accent" in tokens and "--sp-" in tokens)
        _, js = get_text("/static/app.js")
        report("app.js 终端", "connectTerminal" in js and 'type: "input"' in js)
        report("app.js 安全转义", "function escapeHtml" in js and "safeHref" in js)
        _, fit = get_text("/static/xterm-fit.js")
        report("xterm-fit.js", "fitTerminal" in fit)
        _, vendored = get_text("/static/vendor/xterm/xterm-6.1.0.min.js")
        report("vendor xterm js", "Terminal" in vendored)
        _, vendored_css = get_text("/static/vendor/xterm/xterm-6.1.0.css")
        report("vendor xterm css", ".xterm" in vendored_css)
        _, auth = get("/api/auth/status")
        if auth.get("enabled"):
            code = get_status("/api/overview")
            report("鉴权拦截", code == 401, f"no-token status={code}")
        _, light = get("/api/overview?light=1")
        report(
            "overview light 载荷",
            isinstance(light.get("summary"), dict) and "cached" in light,
            "缺少 summary/cached",
        )
    except Exception as exc:  # noqa: BLE001
        report("页面资源", False, str(exc))

    for action in ["connect", "control-bootstrap", "model-check", "worker-check", "refresh", "health"]:
        try:
            status, payload = post("/api/actions/oneclick", {"action": action}, timeout=300)
            report(f"oneclick {action}", status == 200 and "steps" in payload)
        except Exception as exc:  # noqa: BLE001
            report(f"oneclick {action}", False, str(exc))

    try:
        node = first_node()
        status, payload = post("/api/exec", {"preset": "gpu-list", "node": node})
        rows = payload.get("results") or []
        report("exec gpu-list", status == 200 and bool(rows))
        cordoned = False
        try:
            status, payload = post("/api/node/cordon", {"name": node, "reason": "smoke"})
            cordon_rc = (payload.get("result") or {}).get("rc")
            cordoned = status == 200 and cordon_rc == 0
            report("cordon", cordoned, f"rc={cordon_rc}")
        finally:
            if cordoned:
                status, payload = post("/api/node/uncordon", {"name": node})
                uncordon_rc = (payload.get("result") or {}).get("rc")
                report("uncordon", status == 200 and uncordon_rc == 0, f"rc={uncordon_rc}")
        status, payload = post("/api/setup/test-ssh", {})
        report("test-ssh", status == 200 and payload.get("ok") is True)
        status, payload = post("/api/metrics/refresh", {})
        report("metrics refresh", status == 200 and int((payload.get("metrics") or {}).get("ok_count") or 0) >= 1)
        status, payload = post("/api/gateway/render", {})
        report(
            "gateway render",
            status == 200 and "nginx" in (payload.get("snippets") or {}) and bool(payload.get("written")),
        )
        check_ws_terminal(node)
    except Exception as exc:  # noqa: BLE001
        report("节点相关验收", False, str(exc))

    for cmd in [["doctor"], ["get", "nodes"], ["models", "show"]]:
        proc = subprocess.run(
            [str(ROOT / "bin" / "gpu-control"), *cmd],
            cwd=ROOT,
            text=True,
            capture_output=True,
        )
        report("CLI " + " ".join(cmd), proc.returncode == 0, (proc.stderr or proc.stdout)[:120])

    failed = [item for item in results if not item[0]]
    print("---")
    print(f"total={len(results)} pass={len(results) - len(failed)} fail={len(failed)}")
    for _, name, detail in failed:
        print(f"FAILDETAIL {name} => {detail}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
