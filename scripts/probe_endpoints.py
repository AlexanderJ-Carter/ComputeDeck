#!/usr/bin/env python3
"""Probe inference-pool endpoint health.

Default mode ``local``: Ansible asks each node to curl 127.0.0.1 (Codelab-safe).
Optional mode ``direct``: control plane fan-out HTTP to node IPs (needs open ports).
"""

from __future__ import annotations

import argparse
import json
import subprocess
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import _bootstrap  # noqa: F401
from state_util import atomic_write_json
from paths import (
    INVENTORY_FILE,
    PLAYBOOKS_DIR,
    PROJECT_DIR,
    SCRIPTS_DIR,
    STATE_DIR,
    VENV_PYTHON,
    ansible_cmd,
    cluster_file,
    expand_path,
)

ENDPOINTS_FILE = STATE_DIR / "endpoints.json"
OUT_FILE = STATE_DIR / "endpoint_health.json"
PROBE_DIR = STATE_DIR / "endpoint_probe"


def load_json(path: Path, default):
    if not path.is_file():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def ensure_endpoints() -> dict:
    if not ENDPOINTS_FILE.is_file():
        subprocess.run(
            [str(VENV_PYTHON), str(SCRIPTS_DIR / "render_endpoints.py"), "--write"],
            cwd=PROJECT_DIR,
            check=False,
            capture_output=True,
            text=True,
        )
    return load_json(ENDPOINTS_FILE, {"endpoints": []})


def probe_direct(endpoints: list[dict], *, key: str | None, timeout: float, workers: int) -> list[dict]:
    def one(ep: dict) -> dict:
        url = ep.get("health") or ep.get("url")
        started = datetime.now(timezone.utc)
        headers = {"Authorization": f"Bearer {key}"} if key else {}
        req = urllib.request.Request(url, headers=headers, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = resp.read(512)
                latency_ms = int((datetime.now(timezone.utc) - started).total_seconds() * 1000)
                ok = 200 <= resp.status < 300
                detail = f"http_{resp.status}"
                try:
                    payload = json.loads(body.decode("utf-8", errors="replace"))
                    if isinstance(payload, dict) and "ok" in payload:
                        ok = bool(payload.get("ok")) and ok
                        detail = "ok" if payload.get("ok") else "health.ok=false"
                except json.JSONDecodeError:
                    pass
                return {
                    "node": ep.get("node"),
                    "gpu": ep.get("gpu"),
                    "url": ep.get("url"),
                    "ok": ok,
                    "latency_ms": latency_ms,
                    "detail": detail,
                    "mode": "direct",
                }
        except Exception as exc:  # noqa: BLE001 — probe must never crash the batch
            latency_ms = int((datetime.now(timezone.utc) - started).total_seconds() * 1000)
            return {
                "node": ep.get("node"),
                "gpu": ep.get("gpu"),
                "url": ep.get("url"),
                "ok": False,
                "latency_ms": latency_ms,
                "detail": type(exc).__name__,
                "mode": "direct",
            }

    results = []
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futs = [pool.submit(one, ep) for ep in endpoints]
        for fut in as_completed(futs):
            results.append(fut.result())
    return results


def probe_local(cluster: dict) -> list[dict]:
    """Probe using per-host ports from inventory (heterogeneous-safe).

    Does not force a global ports_per_node via ``-e`` (that would override inventory).
    """
    api = cluster.get("api") or {}
    # refresh catalog so by_node / urls match current nodes.json
    subprocess.run(
        [str(VENV_PYTHON), str(SCRIPTS_DIR / "render_endpoints.py"), "--write"],
        cwd=PROJECT_DIR,
        check=False,
        capture_output=True,
        text=True,
    )
    if PROBE_DIR.is_dir():
        for stale in PROBE_DIR.glob("*.json"):
            stale.unlink()
    extra = {
        "default_base_port": int(api.get("base_port", 8000)),
        "default_ports_per_node": int(api.get("ports_per_node", 1)),
        "health_path": api.get("health_path", "/health"),
        "api_key_file": api.get("api_key_file") or "",
    }
    cmd = ansible_cmd(
        "-i",
        str(INVENTORY_FILE),
        str(PLAYBOOKS_DIR / "probe-endpoints.yml"),
        "-e",
        json.dumps(extra),
    )
    subprocess.run(cmd, cwd=PROJECT_DIR, check=False, capture_output=True, text=True)
    catalog = {(e["node"], e["gpu"]): e for e in (ensure_endpoints().get("endpoints") or [])}
    results = []
    for path in sorted(PROBE_DIR.glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        node = payload.get("node")
        for item in payload.get("results") or []:
            ep = catalog.get((node, item.get("gpu")), {})
            results.append({
                "node": node,
                "gpu": item.get("gpu"),
                "port": item.get("port") or ep.get("port"),
                "url": ep.get("url") or f"http://{payload.get('host')}:{item.get('port')}",
                "ok": bool(item.get("ok")),
                "latency_ms": item.get("latency_ms"),
                "detail": item.get("detail"),
                "mode": "local",
            })
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["local", "direct"], default="local")
    parser.add_argument("--timeout", type=float, default=2.0)
    parser.add_argument("--workers", type=int, default=16)
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    catalog = ensure_endpoints()
    cluster = load_json(cluster_file(), {})
    api = dict(cluster.get("api") or {})
    if api.get("api_key_file"):
        api["api_key_file"] = expand_path(str(api["api_key_file"]))
        cluster = {**cluster, "api": api}

    key_file = api.get("api_key_file") or catalog.get("api_key_file")
    if key_file:
        key_file = expand_path(str(key_file))
    key = Path(key_file).read_text(encoding="utf-8").strip() if key_file and Path(key_file).is_file() else None

    if args.mode == "direct":
        results = probe_direct(
            catalog.get("endpoints") or [],
            key=key,
            timeout=args.timeout,
            workers=args.workers,
        )
    else:
        results = probe_local(cluster)

    results.sort(key=lambda r: (r.get("node") or "", r.get("gpu") if r.get("gpu") is not None else -1))
    ok_count = sum(1 for r in results if r.get("ok"))
    payload = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "mode": args.mode,
        "ok_count": ok_count,
        "fail_count": len(results) - ok_count,
        "total": len(results),
        "results": results,
    }

    if args.write:
        OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(OUT_FILE, payload)

    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if args.json or args.write:
        print(text)
    else:
        print(f"endpoint health ({args.mode}): {ok_count}/{len(results)} ok")
        for r in results:
            mark = "Y" if r["ok"] else "N"
            print(f"  [{mark}] {r['node']} gpu{r['gpu']} {r['latency_ms']}ms {r['detail']}")
    return 0 if ok_count == len(results) and results else 1


if __name__ == "__main__":
    raise SystemExit(main())
