#!/usr/bin/env python3
"""Control-host edge reverse proxy for the inference pool.

Borrowing practical ideas from LiteLLM / Portkey / Kong AI gateways —
kept small and local (no Redis / virtual keys):

- health-aware upstream pool
- round-robin or least-connections
- short retries onto another upstream on connect failure
- per-upstream cooldown after failures
- optional sticky routing via request header hash

Clients hit one Control port; GPU workers stay on the private LAN.
"""

from __future__ import annotations

import argparse
import hashlib
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


def _load_json(path: Path, default):
    if not path.is_file():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def load_ingress_settings() -> dict:
    cluster = {}
    path = cluster_file()
    if path.is_file():
        cluster = json.loads(path.read_text(encoding="utf-8"))
    gateway = cluster.get("gateway") or {}
    ingress = gateway.get("ingress") if isinstance(gateway.get("ingress"), dict) else {}
    return {
        "bind": str(ingress.get("bind") or os.environ.get("GPU_CONTROL_EDGE_HOST") or "0.0.0.0"),
        "port": int(
            ingress.get("port")
            or gateway.get("nginx_listen")
            or os.environ.get("GPU_CONTROL_EDGE_PORT")
            or 18080
        ),
        "strategy": str(ingress.get("strategy") or "round_robin").lower(),
        "retries": max(0, int(ingress.get("retries") or 2)),
        "cooldown_sec": max(0, int(ingress.get("cooldown_sec") or 30)),
        "connect_timeout_sec": float(ingress.get("connect_timeout_sec") or 2),
        "read_timeout_sec": float(ingress.get("read_timeout_sec") or 300),
        "sticky_header": str(ingress.get("sticky_header") or "").strip(),
        "reload_interval_sec": float(ingress.get("reload_interval_sec") or 5),
    }


class UpstreamPool:
    def __init__(self, settings: dict) -> None:
        self.settings = settings
        self._lock = threading.Lock()
        self._urls: list[str] = []
        self._rr = 0
        self._inflight: dict[str, int] = {}
        self._cooldown_until: dict[str, float] = {}
        self._loaded_at = 0.0
        self.reload_interval = float(settings.get("reload_interval_sec") or 5)

    def configure(self, settings: dict) -> None:
        self.settings = settings
        self.reload_interval = float(settings.get("reload_interval_sec") or 5)

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
            self._inflight = {u: self._inflight.get(u, 0) for u in urls}
            if self._rr >= len(urls):
                self._rr = 0
            self._loaded_at = now

    def _available(self, now: float) -> list[str]:
        return [u for u in self._urls if self._cooldown_until.get(u, 0) <= now]

    def mark_bad(self, url: str) -> None:
        cool = float(self.settings.get("cooldown_sec") or 0)
        if cool <= 0 or not url:
            return
        with self._lock:
            self._cooldown_until[url] = time.time() + cool

    def begin(self, url: str) -> None:
        with self._lock:
            self._inflight[url] = self._inflight.get(url, 0) + 1

    def end(self, url: str) -> None:
        with self._lock:
            self._inflight[url] = max(0, self._inflight.get(url, 0) - 1)

    def pick(self, sticky_key: str | None = None, *, exclude: set[str] | None = None) -> str | None:
        self.reload()
        now = time.time()
        exclude = exclude or set()
        with self._lock:
            candidates = [u for u in self._available(now) if u not in exclude]
            if not candidates:
                candidates = [u for u in self._urls if u not in exclude]
            if not candidates:
                return None

            strategy = self.settings.get("strategy") or "round_robin"
            if sticky_key and candidates:
                digest = hashlib.sha1(sticky_key.encode("utf-8")).hexdigest()
                idx = int(digest[:8], 16) % len(candidates)
                return candidates[idx]

            if strategy in {"least_conn", "least-connections", "least_busy"}:
                return min(candidates, key=lambda u: (self._inflight.get(u, 0), u))

            url = candidates[self._rr % len(candidates)]
            self._rr += 1
            return url

    def snapshot(self) -> dict:
        self.reload()
        now = time.time()
        with self._lock:
            cooled = sorted(
                u for u, until in self._cooldown_until.items() if until > now and u in self._urls
            )
            return {
                "upstream_count": len(self._urls),
                "available": len(self._available(now)),
                "cooldown": len(cooled),
                "strategy": self.settings.get("strategy"),
                "retries": self.settings.get("retries"),
                "loaded_at": self._loaded_at,
                # Keep status payload small for UI polling / disk writes.
                "upstreams_sample": self._urls[:8],
            }


SETTINGS = load_ingress_settings()
POOL = UpstreamPool(SETTINGS)


def _write_status(listen: str, extra: dict | None = None) -> None:
    payload = {
        "ok": True,
        "kind": "edge",
        "listen": listen,
        "pid": os.getpid(),
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "settings": {
            "strategy": SETTINGS.get("strategy"),
            "retries": SETTINGS.get("retries"),
            "cooldown_sec": SETTINGS.get("cooldown_sec"),
            "connect_timeout_sec": SETTINGS.get("connect_timeout_sec"),
            "read_timeout_sec": SETTINGS.get("read_timeout_sec"),
            "sticky_header": SETTINGS.get("sticky_header") or None,
        },
        "pool": POOL.snapshot(),
    }
    if extra:
        payload.update(extra)
    STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATUS_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


class EdgeHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def setup(self) -> None:
        super().setup()
        self._headers_sent = False
        self.timeout = float(SETTINGS.get("read_timeout_sec") or 300)

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
            body = json.dumps(
                {"ok": True, "service": "gpu-control-edge", **snap},
                ensure_ascii=False,
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)
            return
        self._proxy()

    def _proxy(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length > 0 else None
        sticky_header = SETTINGS.get("sticky_header") or ""
        sticky_key = self.headers.get(sticky_header) if sticky_header else None
        retries = int(SETTINGS.get("retries") or 0)
        tried: set[str] = set()
        last_err = "no upstream"

        for attempt in range(retries + 1):
            upstream = POOL.pick(sticky_key, exclude=tried)
            if not upstream:
                break
            tried.add(upstream)
            ok, err = self._forward(upstream, body)
            if ok:
                return
            last_err = err or "upstream error"
            POOL.mark_bad(upstream)

        if not self._headers_sent:
            self.send_error(502, f"all upstreams failed: {last_err}")

    def _forward(self, upstream: str, body: bytes | None) -> tuple[bool, str | None]:
        parts = urlsplit(upstream)
        host = parts.hostname
        port = parts.port or (443 if parts.scheme == "https" else 80)
        if not host:
            return False, "invalid upstream"

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

        connect_timeout = float(SETTINGS.get("connect_timeout_sec") or 2)
        read_timeout = float(SETTINGS.get("read_timeout_sec") or 300)
        POOL.begin(upstream)
        conn = HTTPConnection(host, port, timeout=connect_timeout)
        try:
            conn.connect()
            conn.sock.settimeout(read_timeout)
            conn.request(self.command, self.path, body=body, headers=headers)
            resp = conn.getresponse()
            # Retry-worthy before headers are committed to client.
            if resp.status >= 500 and not self._headers_sent:
                resp.read()
                return False, f"HTTP {resp.status}"
            self.send_response(resp.status, resp.reason)
            for key, value in resp.getheaders():
                if key.lower() in HOP_BY_HOP:
                    continue
                self.send_header(key, value)
            self.send_header("X-GPU-Control-Upstream", upstream)
            self.end_headers()
            if self.command != "HEAD":
                while True:
                    chunk = resp.read(64 * 1024)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    self.wfile.flush()
            return True, None
        except (OSError, TimeoutError) as exc:
            return False, str(exc)
        finally:
            POOL.end(upstream)
            conn.close()


def main() -> int:
    global SETTINGS, POOL
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    args = parser.parse_args()

    SETTINGS = load_ingress_settings()
    if args.host:
        SETTINGS["bind"] = args.host
    if args.port is not None:
        SETTINGS["port"] = args.port
    POOL = UpstreamPool(SETTINGS)
    POOL.reload(force=True)

    host = SETTINGS["bind"]
    port = int(SETTINGS["port"])
    listen = f"{host}:{port}"
    _write_status(listen)

    def status_loop() -> None:
        while True:
            time.sleep(10)
            try:
                # Hot-reload settings lightly (strategy / timeouts) without restart.
                fresh = load_ingress_settings()
                fresh["bind"] = SETTINGS["bind"]
                fresh["port"] = SETTINGS["port"]
                SETTINGS.update(fresh)
                POOL.configure(SETTINGS)
                POOL.reload()
                _write_status(listen)
            except Exception as exc:  # noqa: BLE001
                print(f"[edge] status update failed: {exc}", flush=True)

    threading.Thread(target=status_loop, daemon=True).start()

    httpd = ThreadingHTTPServer((host, port), EdgeHandler)
    httpd.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    snap = POOL.snapshot()
    print(f"gpu-control edge listening on http://{host}:{port}", flush=True)
    print(
        f"strategy={SETTINGS.get('strategy')} retries={SETTINGS.get('retries')} "
        f"upstreams={snap.get('upstream_count')} healthz=/_gpu_control/health",
        flush=True,
    )
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
