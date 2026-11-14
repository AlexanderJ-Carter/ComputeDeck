#!/usr/bin/env python3
"""Manage control-host ingress: edge proxy, LiteLLM fragment, nginx artifacts."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import subprocess
import time
from pathlib import Path

import _bootstrap  # noqa: F401
from paths import PROJECT_DIR, SCRIPTS_DIR, STATE_DIR, VENV_PYTHON, cluster_file, expand_path
from render_gateway import build_payload, write_artifacts

EDGE_PID = STATE_DIR / "ingress-edge.pid"
EDGE_LOG = STATE_DIR / "ingress-edge.log"
EDGE_STATUS = STATE_DIR / "ingress_edge.json"
LITELLM_PID = STATE_DIR / "ingress-litellm.pid"
LITELLM_LOG = STATE_DIR / "ingress-litellm.log"
LITELLM_RUNTIME = STATE_DIR / "gateway" / "litellm-runtime.yaml"
OUT_DIR = STATE_DIR / "gateway"


def _load_cluster() -> dict:
    path = cluster_file()
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _gateway_cfg(cluster: dict | None = None) -> dict:
    cluster = cluster if cluster is not None else _load_cluster()
    return dict(cluster.get("gateway") or {})


def _ingress_cfg(gateway: dict | None = None) -> dict:
    gateway = gateway if gateway is not None else _gateway_cfg()
    raw = gateway.get("ingress") if isinstance(gateway.get("ingress"), dict) else {}
    return {
        "enabled": bool(raw.get("enabled", True)),
        "bind": str(raw.get("bind") or "0.0.0.0"),
        "port": int(raw.get("port") or gateway.get("nginx_listen") or 18080),
        "kind": str(raw.get("kind") or "edge"),  # edge | litellm | both
        "public_base_url": str(raw.get("public_base_url") or "").rstrip("/"),
    }


def _pid_alive(pid: int) -> bool:
    return (Path("/proc") / str(pid)).exists()


def _read_pid(path: Path) -> int | None:
    if not path.is_file():
        return None
    text = path.read_text(encoding="utf-8").strip()
    if not text.isdigit():
        return None
    pid = int(text)
    return pid if _pid_alive(pid) else None


def _stop_pidfile(path: Path, name: str) -> dict:
    pid = _read_pid(path)
    if pid is None:
        if path.is_file():
            path.unlink()
        return {"ok": True, "name": name, "stopped": False, "reason": "not running"}
    os.kill(pid, signal.SIGTERM)
    for _ in range(30):
        if not _pid_alive(pid):
            break
        time.sleep(0.1)
    if _pid_alive(pid):
        os.kill(pid, signal.SIGKILL)
    if path.is_file():
        path.unlink()
    return {"ok": True, "name": name, "stopped": True, "pid": pid}


def cmd_render(args: argparse.Namespace) -> int:
    from subprocess import run

    run(
        [str(VENV_PYTHON), str(SCRIPTS_DIR / "render_endpoints.py"), "--write"],
        cwd=PROJECT_DIR,
        check=False,
    )
    payload = build_payload(listen=args.listen)
    written = write_artifacts(payload)
    print(json.dumps({"ok": True, "written": written}, ensure_ascii=False, indent=2))
    return 0


def cmd_status(_: argparse.Namespace) -> int:
    cluster = _load_cluster()
    gateway = _gateway_cfg(cluster)
    ingress = _ingress_cfg(gateway)
    edge_pid = _read_pid(EDGE_PID)
    litellm_pid = _read_pid(LITELLM_PID)
    edge_status = {}
    if EDGE_STATUS.is_file():
        edge_status = json.loads(EDGE_STATUS.read_text(encoding="utf-8"))
    public = ingress.get("public_base_url") or f"http://<control>:{ingress['port']}"
    control = (cluster.get("control") or {})
    if not ingress.get("public_base_url") and control.get("host"):
        public = f"http://{control['host']}:{ingress['port']}"
    payload = {
        "ok": True,
        "ingress": ingress,
        "public_base_url": public,
        "edge": {
            "running": bool(edge_pid),
            "pid": edge_pid,
            "listen": f"{ingress['bind']}:{ingress['port']}",
            "status": edge_status,
            "log": str(EDGE_LOG),
            "healthz": f"{public}/_gpu_control/health",
        },
        "litellm": {
            "running": bool(litellm_pid),
            "pid": litellm_pid,
            "port": int(gateway.get("openai_proxy_port") or gateway.get("litellm_port") or 4000),
            "dir": gateway.get("openai_proxy_dir") or gateway.get("litellm_dir"),
            "runtime_config": str(LITELLM_RUNTIME),
            "log": str(LITELLM_LOG),
        },
        "nginx": {
            "binary": bool(shutil.which("nginx")),
            "artifact": str(OUT_DIR / "nginx-inference-pool.conf"),
            "listen": int(gateway.get("nginx_listen") or ingress["port"]),
        },
        "artifacts": sorted(p.name for p in OUT_DIR.glob("*")) if OUT_DIR.is_dir() else [],
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def _start_edge(ingress: dict) -> dict:
    existing = _read_pid(EDGE_PID)
    if existing:
        return {"ok": True, "name": "edge", "already": True, "pid": existing}
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    cmd = [
        str(VENV_PYTHON),
        str(SCRIPTS_DIR / "ingress_edge.py"),
        "--host",
        str(ingress["bind"]),
        "--port",
        str(ingress["port"]),
    ]
    with EDGE_LOG.open("a", encoding="utf-8") as log:
        proc = subprocess.Popen(
            cmd,
            cwd=PROJECT_DIR,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    EDGE_PID.write_text(f"{proc.pid}\n", encoding="utf-8")
    time.sleep(0.4)
    alive = _pid_alive(proc.pid)
    return {
        "ok": alive,
        "name": "edge",
        "pid": proc.pid,
        "listen": f"{ingress['bind']}:{ingress['port']}",
        "log": str(EDGE_LOG),
        "error": None if alive else "process exited — see ingress-edge.log",
    }


def _write_litellm_runtime(gateway: dict) -> Path:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    model_list = (OUT_DIR / "litellm-model-list.yaml").read_text(encoding="utf-8")
    # Strip comments-only header remains; file already has model_list + router_settings.
    port = int(gateway.get("openai_proxy_port") or gateway.get("litellm_port") or 4000)
    # LiteLLM reads model_list from config; general_settings optional.
    body = model_list.rstrip() + "\n\n# runtime listen hint (start flags also set port)\n"
    body += f"# proxy_port: {port}\n"
    LITELLM_RUNTIME.write_text(body, encoding="utf-8")
    return LITELLM_RUNTIME


def _start_litellm(gateway: dict) -> dict:
    existing = _read_pid(LITELLM_PID)
    if existing:
        return {"ok": True, "name": "litellm", "already": True, "pid": existing}

    # Ensure fragments exist.
    if not (OUT_DIR / "litellm-model-list.yaml").is_file():
        write_artifacts(build_payload())
    runtime = _write_litellm_runtime(gateway)
    port = int(gateway.get("openai_proxy_port") or gateway.get("litellm_port") or 4000)
    proxy_dir_raw = gateway.get("openai_proxy_dir") or gateway.get("litellm_dir")
    proxy_dir = Path(expand_path(str(proxy_dir_raw))) if proxy_dir_raw else None

    litellm_bin = shutil.which("litellm")
    start_sh = proxy_dir / "start_litellm.sh" if proxy_dir else None

    STATE_DIR.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    if litellm_bin:
        cmd = [litellm_bin, "--config", str(runtime), "--port", str(port), "--host", "0.0.0.0"]
        cwd = PROJECT_DIR
    elif start_sh and start_sh.is_file():
        # Prefer generated runtime config via env if script supports it; else copy fragment beside.
        frag_dst = proxy_dir / "gpu-control-model-list.yaml"
        shutil.copy2(OUT_DIR / "litellm-model-list.yaml", frag_dst)
        cmd = ["bash", str(start_sh)]
        cwd = proxy_dir
        env["LITELLM_CONFIG"] = str(runtime)
    else:
        return {
            "ok": False,
            "name": "litellm",
            "error": "litellm not installed and openai_proxy_dir/start_litellm.sh missing",
            "hint": "pip install 'litellm[proxy]' 或配置 gateway.openai_proxy_dir",
            "runtime_config": str(runtime),
        }

    with LITELLM_LOG.open("a", encoding="utf-8") as log:
        proc = subprocess.Popen(
            cmd,
            cwd=cwd,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            env=env,
        )
    LITELLM_PID.write_text(f"{proc.pid}\n", encoding="utf-8")
    time.sleep(0.6)
    alive = _pid_alive(proc.pid)
    return {
        "ok": alive,
        "name": "litellm",
        "pid": proc.pid,
        "port": port,
        "config": str(runtime),
        "log": str(LITELLM_LOG),
        "error": None if alive else "process exited — see ingress-litellm.log",
    }


def cmd_start(args: argparse.Namespace) -> int:
    gateway = _gateway_cfg()
    ingress = _ingress_cfg(gateway)
    kind = (args.kind or ingress["kind"] or "edge").lower()
    # Always refresh artifacts first.
    write_artifacts(build_payload(listen=ingress["port"]))
    results = []
    if kind in {"edge", "both", "all"}:
        results.append(_start_edge(ingress))
    if kind in {"litellm", "both", "all"}:
        results.append(_start_litellm(gateway))
    if kind == "nginx":
        results.append(cmd_nginx_apply_inner())
    ok = all(r.get("ok") for r in results) if results else False
    print(json.dumps({"ok": ok, "kind": kind, "results": results, "status_hint": "gpu-control gateway status"}, ensure_ascii=False, indent=2))
    return 0 if ok else 1


def cmd_stop(args: argparse.Namespace) -> int:
    kind = (args.kind or "all").lower()
    results = []
    if kind in {"edge", "both", "all"}:
        results.append(_stop_pidfile(EDGE_PID, "edge"))
        if EDGE_STATUS.is_file():
            EDGE_STATUS.unlink()
    if kind in {"litellm", "both", "all"}:
        results.append(_stop_pidfile(LITELLM_PID, "litellm"))
    print(json.dumps({"ok": True, "results": results}, ensure_ascii=False, indent=2))
    return 0


def cmd_nginx_apply_inner() -> dict:
    artifact = OUT_DIR / "nginx-inference-pool.conf"
    if not artifact.is_file():
        write_artifacts(build_payload())
    nginx = shutil.which("nginx")
    if not nginx:
        return {
            "ok": True,
            "name": "nginx",
            "applied": False,
            "artifact": str(artifact),
            "hint": "nginx 未安装：可用内置 edge（gpu-control gateway start --kind edge），或安装 nginx 后 include 该 conf",
        }
    # Non-root friendly: only validate syntax if a full config exists; otherwise report path.
    return {
        "ok": True,
        "name": "nginx",
        "applied": False,
        "binary": nginx,
        "artifact": str(artifact),
        "hint": f"在 nginx.conf http{{}} 中 include {artifact}; 然后 nginx -t && nginx -s reload",
    }


def cmd_nginx(args: argparse.Namespace) -> int:
    write_artifacts(build_payload(listen=args.listen))
    result = cmd_nginx_apply_inner()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def cmd_install_litellm_fragment(_: argparse.Namespace) -> int:
    gateway = _gateway_cfg()
    if not (OUT_DIR / "litellm-model-list.yaml").is_file():
        write_artifacts(build_payload())
    runtime = _write_litellm_runtime(gateway)
    proxy_dir_raw = gateway.get("openai_proxy_dir") or gateway.get("litellm_dir")
    copied = None
    if proxy_dir_raw:
        proxy_dir = Path(expand_path(str(proxy_dir_raw)))
        proxy_dir.mkdir(parents=True, exist_ok=True)
        copied = proxy_dir / "gpu-control-model-list.yaml"
        shutil.copy2(OUT_DIR / "litellm-model-list.yaml", copied)
    print(json.dumps({
        "ok": True,
        "runtime": str(runtime),
        "copied_to": str(copied) if copied else None,
        "note": "将 model_list 合并进你的 litellm config，或 litellm --config state/gateway/litellm-runtime.yaml",
    }, ensure_ascii=False, indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)

    p_render = sub.add_parser("render", help="写出 nginx/litellm/curl 等产物")
    p_render.add_argument("--listen", type=int, default=None)
    p_render.set_defaults(func=cmd_render)

    p_status = sub.add_parser("status", help="入口状态")
    p_status.set_defaults(func=cmd_status)

    p_start = sub.add_parser("start", help="启动 Control 统一入口")
    p_start.add_argument(
        "--kind",
        choices=["edge", "litellm", "both", "nginx", "all"],
        default=None,
        help="默认读 cluster.gateway.ingress.kind",
    )
    p_start.set_defaults(func=cmd_start)

    p_stop = sub.add_parser("stop", help="停止入口进程")
    p_stop.add_argument("--kind", choices=["edge", "litellm", "both", "all"], default="all")
    p_stop.set_defaults(func=cmd_stop)

    p_nginx = sub.add_parser("nginx", help="写出 nginx conf 并给出 apply 提示")
    p_nginx.add_argument("--listen", type=int, default=None)
    p_nginx.set_defaults(func=cmd_nginx)

    p_ll = sub.add_parser("install-litellm", help="写出/安装 LiteLLM model_list 片段")
    p_ll.set_defaults(func=cmd_install_litellm_fragment)

    args = parser.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
