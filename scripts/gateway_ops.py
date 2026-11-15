#!/usr/bin/env python3
"""Control 统一入口：内置 Edge、可选 LiteLLM、可选 nginx 产物。

配套角色（看板 / status 共用）
------------------------------
- **Edge**：默认主入口（无需 root / nginx）
- **nginx**：Edge 的系统级替代；未装且 kind=edge 时记为「不必装」，不是故障
- **LiteLLM**：可选 OpenAI 兼容层；看 ``openai_proxy_dir`` / ``litellm`` 二进制
"""

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

import sys

sys.path.insert(0, str(PROJECT_DIR / "lib"))
from ingress_tls import normalize_ingress, probe_tls, public_base_url  # noqa: E402

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
    return normalize_ingress(raw, gateway=gateway)


def _pid_alive(pid: int) -> bool:
    return (Path("/proc") / str(pid)).exists()


def _read_pid(path: Path) -> int | None:
    if not path.is_file():
        return None
    text = path.read_text(encoding="utf-8").strip().splitlines()
    if not text or not text[0].isdigit():
        return None
    pid = int(text[0])
    return pid if _pid_alive(pid) else None


def _local_ipv4s() -> set[str]:
    ips = {"127.0.0.1"}
    try:
        import socket

        hostname = socket.gethostname()
        for info in socket.getaddrinfo(hostname, None, socket.AF_INET):
            ips.add(info[4][0])
    except OSError:
        pass
    try:
        out = subprocess.check_output(["hostname", "-I"], text=True, stderr=subprocess.DEVNULL)
        ips.update(p for p in out.split() if p)
    except (OSError, subprocess.CalledProcessError):
        pass
    return ips


def _on_control_host(cluster: dict | None = None) -> bool:
    """Edge / UI 只能跑在 cluster.control.host；共享盘上从 worker 误启会污染 pid。"""
    if os.environ.get("GPU_CONTROL_ALLOW_EDGE_ANYWHERE", "").strip() in {"1", "true", "yes"}:
        return True
    cluster = cluster if cluster is not None else _load_cluster()
    host = str(((cluster.get("control") or {}).get("host") or "")).strip()
    if not host:
        return True
    if host in {"127.0.0.1", "localhost", "0.0.0.0"}:
        return True
    return host in _local_ipv4s()


def _port_open(bind: str, port: int, timeout: float = 0.4) -> bool:
    import socket

    targets = ["127.0.0.1"]
    if bind and bind not in {"0.0.0.0", "::", ""}:
        targets.append(bind)
    for host in targets:
        try:
            with socket.create_connection((host, int(port)), timeout=timeout):
                return True
        except OSError:
            continue
    return False


def _edge_runtime(ingress: dict) -> tuple[bool, int | None, dict]:
    """综合 pid / 状态文件 / 端口判断 Edge 是否在跑（兼容容器 /proc 不可见）。"""
    edge_status: dict = {}
    if EDGE_STATUS.is_file():
        try:
            edge_status = json.loads(EDGE_STATUS.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            edge_status = {}
    pid = _read_pid(EDGE_PID)
    if pid:
        return True, pid, edge_status
    status_pid = edge_status.get("pid") if isinstance(edge_status.get("pid"), int) else None
    if status_pid and _pid_alive(status_pid):
        return True, status_pid, edge_status
    if _port_open(str(ingress.get("bind") or "0.0.0.0"), int(ingress["port"])):
        # 端口已监听：视为运行中（即使当前环境看不到 /proc）
        return True, status_pid or pid, edge_status
    return False, None, edge_status


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


def _litellm_probe(gateway: dict) -> dict:
    """探测 LiteLLM / openai_proxy_dir 是否可用（不启动进程）。"""
    proxy_dir_raw = gateway.get("openai_proxy_dir") or gateway.get("litellm_dir")
    proxy_dir = Path(expand_path(str(proxy_dir_raw))) if proxy_dir_raw else None
    litellm_bin = shutil.which("litellm")
    start_sh = proxy_dir / "start_litellm.sh" if proxy_dir else None
    env_file = proxy_dir / ".env" if proxy_dir else None
    cfg_file = proxy_dir / "litellm_config.yaml" if proxy_dir else None
    reasons: list[str] = []
    if litellm_bin:
        ready = True
        mode = "binary"
    elif start_sh and start_sh.is_file():
        mode = "script"
        if env_file and not env_file.is_file():
            ready = False
            reasons.append("缺 .env（复制 .env.example）")
        else:
            ready = True
        if cfg_file and not cfg_file.is_file():
            reasons.append("缺 litellm_config.yaml")
    else:
        ready = False
        mode = "missing"
        reasons.append("无 litellm 二进制且无 start_litellm.sh")
    return {
        "ready": ready,
        "mode": mode,
        "binary": litellm_bin,
        "dir": str(proxy_dir) if proxy_dir else None,
        "start_script": str(start_sh) if start_sh and start_sh.is_file() else None,
        "env_ok": bool(env_file and env_file.is_file()) if proxy_dir else None,
        "reasons": reasons,
    }


def _companions(
    *,
    ingress: dict,
    edge_pid: int | None,
    litellm_pid: int | None,
    litellm_port: int,
    litellm_probe: dict,
    nginx_bin: str | None,
    artifact: Path,
) -> list[dict]:
    """看板用配套设施清单：角色清晰，可选组件不标成故障。"""
    kind = (ingress.get("kind") or "edge").lower()
    edge_primary = kind in {"edge", "both", "all"}
    ll_wanted = kind in {"litellm", "both", "all"}
    rows: list[dict] = []

    if edge_pid:
        edge_state, edge_label, edge_ok = "running", "运行中", True
    elif edge_primary:
        edge_state, edge_label, edge_ok = "stopped", "应启动", False
    else:
        edge_state, edge_label, edge_ok = "idle", "备用", True
    rows.append(
        {
            "id": "edge",
            "name": "Edge LB",
            "role": "primary" if edge_primary else "optional",
            "required": edge_primary,
            "state": edge_state,
            "label": edge_label,
            "ok": edge_ok,
            "severity": "error" if not edge_ok else "ok",
            "detail": f"内置反代 · {ingress['bind']}:{ingress['port']}",
            "action": None if edge_pid else "gpu-control gateway ensure",
        }
    )

    if nginx_bin:
        nginx_row = {
            "id": "nginx",
            "name": "nginx",
            "role": "alternative",
            "required": False,
            "state": "installed",
            "label": "已装",
            "ok": True,
            "severity": "ok",
            "detail": f"{nginx_bin} · conf {artifact.name}",
            "action": None,
        }
    else:
        nginx_row = {
            "id": "nginx",
            "name": "nginx",
            "role": "alternative",
            "required": False,
            "state": "not_needed" if edge_primary else "missing",
            "label": "不必装" if edge_primary else "未装",
            "ok": True,
            "severity": "ok" if edge_primary else "info",
            "detail": (
                "本集群主入口为内置 Edge，无需系统 nginx"
                if edge_primary
                else "可选：装 nginx 后 include 生成的 conf"
            ),
            "action": None if edge_primary else "sudo apt-get install -y nginx",
        }
    rows.append(nginx_row)

    dir_bit = litellm_probe.get("dir") or ""
    if litellm_pid:
        ll_state, ll_label, ll_ok, sev = "running", "运行中", True, "ok"
        detail = f"LiteLLM :{litellm_port}" + (f" · {dir_bit}" if dir_bit else "")
    elif litellm_probe.get("ready"):
        ll_state = "ready"
        ll_label = "就绪未启" if ll_wanted else "就绪"
        ll_ok, sev = True, "ok"
        detail = f"LiteLLM :{litellm_port} 可启动" + (f" · {dir_bit}" if dir_bit else "")
    else:
        ll_state = "blocked"
        ll_label = "缺依赖" if ll_wanted else "可选·未配"
        ll_ok = not ll_wanted
        sev = "warn" if ll_wanted else "info"
        detail = "；".join(litellm_probe.get("reasons") or ["未安装 / 未配置"])
    rows.append(
        {
            "id": "litellm",
            "name": "AI Gateway",
            "role": "optional",
            "required": ll_wanted,
            "state": ll_state,
            "label": ll_label,
            "ok": ll_ok,
            "severity": sev,
            "detail": detail,
            "action": (
                None
                if litellm_pid
                else (
                    "gpu-control gateway start --kind litellm"
                    if litellm_probe.get("ready")
                    else None
                )
            ),
        }
    )
    return rows


def _tls_companion(tls_status: dict, ingress: dict) -> dict:
    domain = ingress.get("domain") or "（未配域名）"
    detail_bits = [domain]
    if tls_status.get("not_after"):
        detail_bits.append(f"到期 {tls_status['not_after']}")
    if tls_status.get("reasons"):
        detail_bits.append("；".join(tls_status["reasons"]))
    sev = "ok"
    if tls_status.get("enabled") and not tls_status.get("ready"):
        sev = "warn"
    return {
        "id": "tls",
        "name": "HTTPS / TLS",
        "role": "optional",
        "required": bool(tls_status.get("enabled")),
        "state": tls_status.get("state") or "off",
        "label": tls_status.get("label") or "未启用",
        "ok": bool(tls_status.get("ok", True)),
        "severity": sev,
        "detail": " · ".join(detail_bits),
        "action": None,
    }


def status_payload() -> dict:
    """统一入口状态快照（供 CLI / overview / 看板，不启子进程）。"""
    cluster = _load_cluster()
    gateway = _gateway_cfg(cluster)
    ingress = _ingress_cfg(gateway)
    edge_running, edge_pid, edge_status = _edge_runtime(ingress)
    litellm_pid = _read_pid(LITELLM_PID)
    if not edge_running and EDGE_STATUS.is_file():
        EDGE_STATUS.unlink(missing_ok=True)
        edge_status = {}
    control = cluster.get("control") or {}
    tls_status = probe_tls(ingress.get("tls") or {})
    public = public_base_url(
        ingress,
        control_host=str(control.get("host") or ""),
        tls_ready=bool(tls_status.get("ready")),
    )
    litellm_port = int(gateway.get("openai_proxy_port") or gateway.get("litellm_port") or 4000)
    litellm_public = ""
    if control.get("host"):
        litellm_public = f"http://{control['host']}:{litellm_port}"
    nginx_bin = shutil.which("nginx")
    artifact = OUT_DIR / "nginx-inference-pool.conf"
    ll_probe = _litellm_probe(gateway)
    companions = _companions(
        ingress=ingress,
        edge_pid=edge_pid if edge_running else None,
        litellm_pid=litellm_pid,
        litellm_port=litellm_port,
        litellm_probe=ll_probe,
        nginx_bin=nginx_bin,
        artifact=artifact,
    )
    companions.append(_tls_companion(tls_status, ingress))
    if edge_running:
        for row in companions:
            if row["id"] == "edge":
                row["state"] = "running"
                row["label"] = "运行中"
                row["ok"] = True
                row["severity"] = "ok"
                row["action"] = None
                scheme = "https" if tls_status.get("ready") else "http"
                row["detail"] = f"{scheme} · {ingress['bind']}:{ingress['port']}"
    healthy = all(c.get("ok") for c in companions if c.get("required"))
    kind = (ingress.get("kind") or "edge").lower()
    on_control = _on_control_host(cluster)
    return {
        "ok": healthy,
        "on_control_host": on_control,
        "ingress": ingress,
        "public_base_url": public,
        "preferred": kind,
        "companions": companions,
        "tls": tls_status,
        "domain": ingress.get("domain") or "",
        "domains": ingress.get("domains") or [],
        "edge": {
            "running": edge_running,
            "pid": edge_pid,
            "listen": f"{ingress['bind']}:{ingress['port']}",
            "status": edge_status,
            "log": str(EDGE_LOG),
            "healthz": f"{public}/_gpu_control/health",
            "url": public,
            "role": "primary",
            "scheme": "https" if tls_status.get("ready") else "http",
        },
        "litellm": {
            "running": bool(litellm_pid),
            "pid": litellm_pid,
            "port": litellm_port,
            "url": litellm_public or f"http://127.0.0.1:{litellm_port}",
            "dir": ll_probe.get("dir"),
            "ready": ll_probe.get("ready"),
            "mode": ll_probe.get("mode"),
            "start_script": ll_probe.get("start_script"),
            "env_ok": ll_probe.get("env_ok"),
            "reasons": ll_probe.get("reasons") or [],
            "runtime_config": str(LITELLM_RUNTIME),
            "log": str(LITELLM_LOG),
            "role": "optional",
        },
        "nginx": {
            "binary": bool(nginx_bin),
            "path": nginx_bin,
            "artifact": str(artifact),
            "artifact_exists": artifact.is_file(),
            "listen": int(gateway.get("nginx_listen") or ingress["port"]),
            "role": "alternative",
            "needed": kind == "nginx",
            "hint": (
                "本集群用内置 Edge，无需安装 nginx"
                if kind in {"edge", "both", "all"}
                else "include state/gateway/nginx-inference-pool.conf 后 reload"
            ),
        },
        "artifacts": sorted(p.name for p in OUT_DIR.glob("*")) if OUT_DIR.is_dir() else [],
    }


def cmd_status(_: argparse.Namespace) -> int:
    print(json.dumps(status_payload(), ensure_ascii=False, indent=2))
    return 0


def _append_litellm_start(kind: str, gateway: dict, results: list[dict]) -> None:
    """在可用时启动 AI Gateway，不让 Edge 依赖它。

    :param kind: ``litellm`` 时强制启动；其它 kind 仅在探测就绪时可选启动。
    """
    if kind == "litellm":
        results.append(_start_litellm(gateway))
        return
    probe = _litellm_probe(gateway)
    if probe.get("ready"):
        row = _start_litellm(gateway)
        row["optional"] = True
        results.append(row)
    else:
        results.append(
            {
                "ok": True,
                "name": "litellm",
                "optional": True,
                "skipped": True,
                "reason": "；".join(probe.get("reasons") or ["未安装 / 未配置"]),
                "hint": "装好 litellm[proxy] 或配置 gateway.openai_proxy_dir 后会自动随 entry 启动",
            }
        )


def _results_ok(results: list[dict]) -> bool:
    required = [row for row in results if not row.get("optional")]
    return all(row.get("ok") for row in required) if required else False


def cmd_ensure(args: argparse.Namespace) -> int:
    """按 cluster.gateway.ingress 规整配套：渲染产物并拉起应运行的主入口。"""
    cluster = _load_cluster()
    if not _on_control_host(cluster):
        control_host = ((cluster.get("control") or {}).get("host") or "?")
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": f"当前主机不是 Control（{control_host}），拒绝 ensure",
                    "hint": "在 Control 上执行，或走 UI「规整并启动」；调试可设 GPU_CONTROL_ALLOW_EDGE_ANYWHERE=1",
                    "local_ips": sorted(_local_ipv4s()),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 2
    gateway = _gateway_cfg(cluster)
    ingress = _ingress_cfg(gateway)
    kind = (args.kind or ingress["kind"] or "edge").lower()
    write_artifacts(build_payload(listen=ingress["port"]))
    results: list[dict] = []
    if kind in {"edge", "both", "all"}:
        results.append(_start_edge(ingress))
    if kind in {"edge", "litellm", "both", "all"}:
        _append_litellm_start(kind, gateway, results)
    if kind == "nginx":
        results.append(cmd_nginx_apply_inner())
    status = status_payload()
    ok = _results_ok(results) if results else status.get("ok", False)
    print(
        json.dumps(
            {
                "ok": ok,
                "kind": kind,
                "results": results,
                "companions": status.get("companions"),
                "public_base_url": status.get("public_base_url"),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if ok else 1


def _start_edge(ingress: dict) -> dict:
    cluster = _load_cluster()
    if not _on_control_host(cluster):
        control_host = ((cluster.get("control") or {}).get("host") or "?")
        return {
            "ok": False,
            "name": "edge",
            "error": f"当前主机不是 Control（{control_host}）",
            "hint": "请在 Control 启动 Edge；GPU_CONTROL_ALLOW_EDGE_ANYWHERE=1 可绕过",
        }
    running, existing, _ = _edge_runtime(ingress)
    if running and existing:
        EDGE_PID.write_text(f"{existing}\n", encoding="utf-8")
        return {"ok": True, "name": "edge", "already": True, "pid": existing}
    if running:
        return {"ok": True, "name": "edge", "already": True, "pid": existing, "via": "port"}
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
    time.sleep(0.5)
    running, edge_pid, _ = _edge_runtime(ingress)
    alive = running or _pid_alive(proc.pid)
    return {
        "ok": alive,
        "name": "edge",
        "pid": edge_pid or proc.pid,
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
        # Support both common conventions used by custom start scripts.
        env["LITELLM_CONFIG"] = str(runtime)
        env["LITELLM_CONFIG_PATH"] = str(runtime)
        env["LITELLM_PORT"] = str(port)
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
    if kind in {"edge", "litellm", "both", "all"}:
        _append_litellm_start(kind, gateway, results)
    if kind == "nginx":
        results.append(cmd_nginx_apply_inner())
    ok = _results_ok(results) if results else False
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

    p_ensure = sub.add_parser("ensure", help="规整配套并拉起配置的主入口（推荐）")
    p_ensure.add_argument(
        "--kind",
        choices=["edge", "litellm", "both", "nginx", "all"],
        default=None,
        help="默认读 cluster.gateway.ingress.kind",
    )
    p_ensure.set_defaults(func=cmd_ensure)

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
