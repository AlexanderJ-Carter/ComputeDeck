#!/usr/bin/env python3
"""经并行 SSH 采集各节点 CPU / 内存 / load 指标。"""

from __future__ import annotations

import json
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import _bootstrap  # noqa: F401
from paths import STATE_DIR, VENV_PYTHON, expand_path, nodes_file
from state_util import atomic_write_json

REMOTE = r"""
python3 - <<'PY'
import json, os
load1=load5=load15=0.0
try:
    with open('/proc/loadavg') as f:
        a,b,c,_=f.read().split()[:4]
        load1,load5,load15=float(a),float(b),float(c)
except Exception:
    pass
cores=os.cpu_count() or 1
mem_total=mem_avail=mem_used=0
try:
    info={}
    with open('/proc/meminfo') as f:
        for line in f:
            k,v,*_=line.split()
            info[k.rstrip(':')]=int(v)
    mem_total=info.get('MemTotal',0)//1024
    mem_avail=info.get('MemAvailable', info.get('MemFree',0))//1024
    mem_used=max(0, mem_total-mem_avail)
except Exception:
    pass
# rough cpu busy percent from /proc/stat sample
def read_cpu():
    with open('/proc/stat') as f:
        parts=f.readline().split()
    vals=list(map(int, parts[1:]))
    idle=vals[3]+ (vals[4] if len(vals)>4 else 0)
    total=sum(vals)
    return idle, total
try:
    i1,t1=read_cpu(); import time as _t; _t.sleep(0.15); i2,t2=read_cpu()
    didle=i2-i1; dtotal=max(1,t2-t1)
    cpu_pct=round(100.0*(1.0-didle/dtotal),1)
except Exception:
    cpu_pct=None
print(json.dumps({
  'load1':load1,'load5':load5,'load15':load15,'cores':cores,
  'mem_total_mb':mem_total,'mem_used_mb':mem_used,'mem_avail_mb':mem_avail,
  'cpu_percent':cpu_pct,
}, separators=(',',':')))
PY
""".strip()


def ssh_defaults():
    doc = json.loads(nodes_file().read_text(encoding="utf-8"))
    defaults = doc.get("defaults") or {}
    return doc.get("nodes") or {}, defaults


def probe_one(name: str, host: str, user: str, port: int, key: str) -> dict:
    started = time.perf_counter()
    cmd = [
        "ssh",
        "-i",
        key,
        "-p",
        str(port),
        "-o",
        "BatchMode=yes",
        "-o",
        "StrictHostKeyChecking=no",
        "-o",
        "ConnectTimeout=6",
        f"{user}@{host}",
        REMOTE,
    ]
    try:
        completed = subprocess.run(cmd, text=True, capture_output=True, check=False, timeout=20)
        latency_ms = int((time.perf_counter() - started) * 1000)
        if completed.returncode != 0:
            return {
                "node": name,
                "host": host,
                "ok": False,
                "latency_ms": latency_ms,
                "error": (completed.stderr or completed.stdout or f"rc={completed.returncode}")[:200],
            }
        # find JSON line
        payload = None
        for line in (completed.stdout or "").splitlines():
            line = line.strip()
            if line.startswith("{") and line.endswith("}"):
                payload = json.loads(line)
                break
        if not payload:
            return {
                "node": name,
                "host": host,
                "ok": False,
                "latency_ms": latency_ms,
                "error": "no metrics json",
            }
        payload.update({"node": name, "host": host, "ok": True, "latency_ms": latency_ms})
        return payload
    except Exception as exc:  # noqa: BLE001
        return {
            "node": name,
            "host": host,
            "ok": False,
            "latency_ms": int((time.perf_counter() - started) * 1000),
            "error": str(exc),
        }


def main() -> int:
    nodes, defaults = ssh_defaults()
    user = str(defaults.get("ansible_user") or "root")
    port = int(defaults.get("ansible_port") or 22)
    key = expand_path(str(defaults.get("ansible_ssh_private_key_file") or ""))
    if not key or not Path(key).is_file():
        print(json.dumps({"ok": False, "error": f"ssh key missing: {key}"}, ensure_ascii=False))
        return 1

    results = []
    with ThreadPoolExecutor(max_workers=max(1, len(nodes))) as pool:
        futs = [
            pool.submit(probe_one, name, str(meta.get("ansible_host") or ""), user, port, key)
            for name, meta in sorted(nodes.items())
        ]
        for fut in as_completed(futs):
            results.append(fut.result())
    results.sort(key=lambda r: r.get("node") or "")
    ok = sum(1 for r in results if r.get("ok"))
    report = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "ok_count": ok,
        "total": len(results),
        "nodes": {r["node"]: r for r in results},
        "results": results,
    }
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    out = STATE_DIR / "host_metrics.json"
    atomic_write_json(out, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if ok == len(results) and results else 1


if __name__ == "__main__":
    raise SystemExit(main())
