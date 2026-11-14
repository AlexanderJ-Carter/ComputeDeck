#!/usr/bin/env python3
"""Control-host edge reverse proxy for the inference pool.

Listens on one port on the control machine and load-balances to GPU worker
endpoints discovered by gpu-control. Workers stay on the private LAN; clients
only need the control host.

This process is the data-plane edge LB. It is separate from the UI (:8787).
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import threading
import time
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

import _bootstrap  # noqa: F401
from paths import STATE_DIR, cluster_file

ENDPOINTS_FILE = STATE_DIR / "endpoints.json"
HEALTH_FILE = STATE_DIR / "endpoint_health.json"
STATUS_FILE = STATE_DIR / "ingress_edge.json"

HOP_BY_HOP = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
    "content-length",
    "host",
}


class UpstreamPool:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._urls: list[str] = []
        self._rr = 0
        self._loaded_at = 0.0
        self.reload_interval = 5.0

    def reload(self, force: bool = False) -> None:
        now = time.time()
        if not force and (now - self._loaded_at) < self.reload_interval:
            return
        catalog = _load_json(ENDPOINTS_FILE, {"endpoints": []})
        health = _load_json(HEALTH_FILE, {})
        by_key = {(r.get("node"), r.get("gpu")): r for r in health.get("results") or []}
        healthy: list[str] = []
        all_urls: list[str] = []
        for ep in catalog.get("endpoints") or []:
            url = ep.get("url")
            if not url:
                continue
            all_urls.append(url)
            h = by_key.get((ep.get("node"), ep.get("gpu"))) or {}
            if h and h.get("ok"):
                healthy.append(url)
            elif not by_key:
                healthy.append(url)
        urls = healthy or all_urls
        with self._lock:
            self._urls = urls
            if self._rr >= len(urls):
                self._rr = 0
            self._loaded_at = now

    def pick(self) -> str | None:
        self.reload()
        with self._lock:
            if not self._urls:
                return None
            url = self._urls[self._rr % len(self._urls)]
            self._rr += 1
            return url

    def snapshot(self) -> dict:
        self.reload()
        with self._lock:
            return {
                "upstream_count": len(self._urls),
                "upstreams": list(self._urls),
                "loaded_at": self._loaded_at,
            }


POOL = UpstreamPool()


def _load_json(path: Path, default):
    if not path.is_file():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def _write_status(listen: str, extra: dict | None = None) -> None:
    payload = {
        "ok": True,
        "kind": "edge",
        "listen": listen,
        "pid": os.getpid(),
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "pool": POOL.snapshot(),
    }
    if extra:
        payload.update(extra)
    STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATUS_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


class EdgeHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    timeout = 300

    def setup(self) -> None:
        super().setup()
        self._headers_sent = False

    def log_message(self, fmt: str, *args) -> None:
        print(f"[edge] {self.address_string()} {fmt % args}", flush=True)

    def end_headers(self) -> None:
        self._headers_sent = True
        super().end_headers()

    def do_GET(self) -> None:
        self._dispatch()

    def do_POST(self) -> None:
        self._dispatch()

    def do_PUT(self) -> None:
        self._dispatch()

    def do_PATCH(self) -> None:
        self._dispatch()

    def do_DELETE(self) -> None:
        self._dispatch()

    def do_HEAD(self) -> None:
        self._dispatch()

    def do_OPTIONS(self) -> None:
        self._dispatch()

    def _dispatch(self) -> None:
        if self.path.startswith("/_gpu_control/health"):
            snap = POOL.snapshot()
            body = json.dumps({"ok": True, "service": "gpu-control-edge", **snap}, ensure_ascii=False).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)
            return
        self._proxy()

    def _proxy(self) -> None:
        upstream = POOL.pick()
        if not upstream:
            self.send_error(503, "no upstream endpoints — run: gpu-control endpoints && gpu-control health")
            return

        parts = urlsplit(upstream)
        host = parts.hostname
        port = parts.port or (443 if parts.scheme == "https" else 80)
        if not host:
            self.send_error(502, "invalid upstream")
            return

        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length > 0 else None

        headers = {}
        for key, value in self.headers.items():
            if key.lower() in HOP_BY_HOP:
                continue
            headers[key] = value
        headers["Host"] = f"{host}:{port}" if port not in (80, 443) else host
        xff = self.headers.get("X-Forwarded-For")
        peer = self.client_address[0]
        headers["X-Forwarded-For"] = f"{xff}, {peer}" if xff else peer
        headers["X-Forwarded-Host"] = self.headers.get("Host") or ""
        headers["X-Forwarded-Proto"] = "http"
        headers["X-GPU-Control-Upstream"] = upstream

        path = self.path
        conn = HTTPConnection(host, port, timeout=self.timeout)
        try:
            conn.request(self.command, path, body=body, headers=headers)
            resp = conn.getresponse()
            self.send_response(resp.status, resp.reason)
            for key, value in resp.getheaders():
                if key.lower() in HOP_BY_HOP:
                    continue
                self.send_header(key, value)
            self.send_header("X-GPU-Control-Upstream", upstream)
            self.end_headers()
            if self.command == "HEAD":
                return
            while True:
                chunk = resp.read(64 * 1024)
                if not chunk:
                    break
                self.wfile.write(chunk)
                self.wfile.flush()
        except (OSError, TimeoutError) as exc:
            if not self._headers_sent:
                self.send_error(502, f"upstream error: {exc}")
        finally:
            conn.close()


def load_listen_defaults() -> tuple[str, int]:
    cluster = {}
    path = cluster_file()
    if path.is_file():
        cluster = json.loads(path.read_text(encoding="utf-8"))
    gateway = cluster.get("gateway") or {}
    ingress = gateway.get("ingress") if isinstance(gateway.get("ingress"), dict) else {}
    host = str(
        ingress.get("bind")
        or os.environ.get("GPU_CONTROL_EDGE_HOST")
        or "0.0.0.0"
    )
    port = int(
        ingress.get("port")
        or gateway.get("nginx_listen")
        or os.environ.get("GPU_CONTROL_EDGE_PORT")
        or 18080
    )
    return host, port


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    args = parser.parse_args()
    host, port = load_listen_defaults()
    if args.host:
        host = args.host
    if args.port is not None:
        port = args.port

    POOL.reload(force=True)
    listen = f"{host}:{port}"
    _write_status(listen)

    # Keep status fresh for UI.
    def status_loop() -> None:
        while True:
            time.sleep(5)
            try:
                POOL.reload()
                _write_status(listen)
            except Exception as exc:  # noqa: BLE001
                print(f"[edge] status update failed: {exc}", flush=True)

    threading.Thread(target=status_loop, daemon=True).start()

    httpd = ThreadingHTTPServer((host, port), EdgeHandler)
    # Avoid slow clients blocking the whole process forever on accept path.
    httpd.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    print(f"gpu-control edge listening on http://{host}:{port}", flush=True)
    print(f"upstreams={len(POOL.snapshot()['upstreams'])}  healthz=/_gpu_control/health", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nedge shutting down", flush=True)
    finally:
        httpd.server_close()
        if STATUS_FILE.is_file():
            STATUS_FILE.unlink()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
