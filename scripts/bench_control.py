#!/usr/bin/env python3
"""控制面基准：API 延迟、静态资源压缩比、并发吞吐。

用法::

    python scripts/bench_control.py --base http://127.0.0.1:8787
    python scripts/bench_control.py --base http://127.0.0.1:8787 --n 30 --concurrency 8 --json

说明：这是控制面自身的基准，不是 GPU 推理性能测试。测量环境、Python 版本和
集群规模都会影响结果，对外引用时请带上这些信息。
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

DEFAULT_PATHS = [
    "/api/health",
    "/api/auth/status",
    "/api/overview?light=1",
    "/api/gateway/status",
    "/api/jobs",
]


def _token(args: argparse.Namespace) -> str:
    raw = str(args.token or "").strip()
    if raw:
        return raw
    env = str(os.environ.get("GPU_CONTROL_API_TOKEN") or "").strip()
    if env:
        return env
    path = Path(args.token_file).expanduser()
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _fetch(url: str, token: str, *, accept_encoding: str = "identity") -> tuple[int, int, float]:
    request = urllib.request.Request(url, headers={"Accept-Encoding": accept_encoding})
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    start = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            body = response.read()
            status = response.status
    except urllib.error.HTTPError as exc:
        body = exc.read()
        status = exc.code
    elapsed = (time.perf_counter() - start) * 1000
    return status, len(body), elapsed


def _percentiles(values: list[float]) -> dict[str, float]:
    if not values:
        return {"p50": 0.0, "p95": 0.0, "p99": 0.0, "min": 0.0, "max": 0.0, "avg": 0.0}
    ordered = sorted(values)
    def pick(pct: float) -> float:
        index = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * pct)))
        return ordered[index]
    return {
        "p50": round(pick(0.50), 1),
        "p95": round(pick(0.95), 1),
        "p99": round(pick(0.99), 1),
        "min": round(ordered[0], 1),
        "max": round(ordered[-1], 1),
        "avg": round(statistics.fmean(ordered), 1),
    }


def bench(base: str, path: str, token: str, n: int) -> dict:
    status, size, _ = _fetch(base + path, token)
    samples: list[float] = []
    for _ in range(n):
        _, _, elapsed = _fetch(base + path, token)
        samples.append(elapsed)
    return {
        "path": path,
        "status": status,
        "bytes": size,
        "requests": n,
        **_percentiles(samples),
    }


def bench_concurrency(base: str, path: str, token: str, n: int, concurrency: int) -> dict:
    def one(_):
        return _fetch(base + path, token)[2]

    start = time.perf_counter()
    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
        samples = list(pool.map(one, range(n)))
    wall = max(1e-9, time.perf_counter() - start)
    return {
        "path": path,
        "requests": n,
        "concurrency": concurrency,
        "rps": round(n / wall, 1),
        "wall_sec": round(wall, 3),
        **_percentiles(samples),
    }


def static_compression(base: str, path: str = "/static/app.js") -> dict:
    _, raw_size, _ = _fetch(base + path, "", accept_encoding="identity")
    _, gz_size, _ = _fetch(base + path, "", accept_encoding="gzip")
    ratio = round(gz_size / raw_size * 100, 1) if raw_size else 0.0
    return {
        "path": path,
        "raw_bytes": raw_size,
        "gzip_bytes": gz_size,
        "gzip_ratio_pct": ratio,
        "saved_bytes": max(0, raw_size - gz_size),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="http://127.0.0.1:8787")
    parser.add_argument("--token", default="")
    parser.add_argument("--token-file", default="config/local/secrets/ui_api_token")
    parser.add_argument("--n", type=int, default=20, help="每个接口的顺序采样次数")
    parser.add_argument("--concurrency", type=int, default=8)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    base = args.base.rstrip("/")
    token = _token(args)
    rows = [bench(base, path, token, max(1, args.n)) for path in DEFAULT_PATHS]
    concurrent = bench_concurrency(base, "/api/health", token, max(1, args.n * args.concurrency), args.concurrency)
    static = static_compression(base)
    payload = {
        "base": base,
        "auth": bool(token),
        "samples_per_path": args.n,
        "api": rows,
        "concurrency": concurrent,
        "static": static,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    print(f"base={base} auth={'yes' if token else 'no'} samples={args.n}")
    print(f"{'path':28} {'code':>5} {'bytes':>9} {'p50':>8} {'p95':>8} {'p99':>8}")
    for row in rows:
        print(
            f"{row['path']:28} {row['status']:>5} {row['bytes']:>9} "
            f"{row['p50']:>7.1f}ms {row['p95']:>7.1f}ms {row['p99']:>7.1f}ms"
        )
    print(
        f"\nhealth concurrency={concurrent['concurrency']} rps={concurrent['rps']} "
        f"p50={concurrent['p50']}ms p95={concurrent['p95']}ms"
    )
    print(
        f"static {static['path']}: {static['raw_bytes']} → {static['gzip_bytes']} bytes "
        f"({static['gzip_ratio_pct']}%, saved {static['saved_bytes']})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
