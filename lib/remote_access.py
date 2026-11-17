"""远程接入：把控制面 UI 安全地暴露到 Tailscale / Cloudflare。

设计原则：

- 默认只推荐 **tailnet 内** 或 **Cloudflare Access 之后** 的接入；
- 公网暴露（funnel / quick tunnel）必须显式确认，且默认要求控制面已开鉴权；
- 不内置任何密钥；只调用本机已安装并已登录的 ``tailscale`` / ``cloudflared``。

``status`` / ``plan`` 纯只读；``up`` / ``down`` 会执行外部命令，需调用方确认。
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from paths import STATE_DIR, cluster_file

MODES = (
    "tailscale-serve",
    "tailscale-funnel",
    "cloudflared-quick",
    "cloudflared-tunnel",
)
REMOTE_STATE = STATE_DIR / "remote_access.json"
CLOUDFLARED_LOG = STATE_DIR / "cloudflared.log"
PUBLIC_MODES = {"tailscale-funnel", "cloudflared-quick"}


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _read_json(path: Path, default):
    if not path.is_file():
        return default
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, (dict, list)) else default
    except (OSError, json.JSONDecodeError):
        return default


def _cluster() -> dict:
    return _read_json(cluster_file(), {})


def _auth_enabled() -> bool:
    try:
        from auth_util import load_auth_config

        return bool(load_auth_config().enabled)
    except Exception:  # noqa: BLE001
        return False


def _binary(name: str) -> str | None:
    return shutil.which(name)


def _run(cmd: list[str], *, timeout: int = 8) -> dict[str, Any]:
    try:
        completed = subprocess.run(
            cmd,
            text=True,
            capture_output=True,
            check=False,
            timeout=timeout,
        )
        return {
            "ok": completed.returncode == 0,
            "rc": completed.returncode,
            "stdout": (completed.stdout or "").strip()[:4000],
            "stderr": (completed.stderr or "").strip()[:2000],
        }
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "rc": None, "stdout": "", "stderr": str(exc)[:500]}


def _processes(pattern: str) -> list[dict[str, Any]]:
    try:
        completed = subprocess.run(
            ["ps", "-axo", "pid=,command="],
            text=True,
            capture_output=True,
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    rows = []
    for line in (completed.stdout or "").splitlines():
        line = line.strip()
        if not line or pattern not in line:
            continue
        pid_text, _, command = line.partition(" ")
        try:
            pid = int(pid_text)
        except ValueError:
            continue
        rows.append({"pid": pid, "command": command.strip()[:400]})
    return rows


def _tailscale_status() -> dict[str, Any]:
    binary = _binary("tailscale")
    if not binary:
        return {"installed": False}
    version = _run([binary, "version"], timeout=5)
    status = _run([binary, "status", "--json"], timeout=8)
    payload: dict[str, Any] = {}
    if status.get("ok") and status.get("stdout"):
        try:
            payload = json.loads(status["stdout"])
        except json.JSONDecodeError:
            payload = {}
    self_info = payload.get("Self") or {}
    dns_name = str(self_info.get("DNSName") or "").rstrip(".")
    ips = [str(ip) for ip in (self_info.get("TailscaleIPs") or [])]
    serve = _run([binary, "serve", "status", "--json"], timeout=6)
    serve_payload: dict[str, Any] = {}
    if serve.get("ok") and serve.get("stdout"):
        try:
            serve_payload = json.loads(serve["stdout"])
        except json.JSONDecodeError:
            serve_payload = {}
    return {
        "installed": True,
        "path": binary,
        "version": (version.get("stdout") or "").splitlines()[0] if version.get("stdout") else None,
        "backend_state": payload.get("BackendState"),
        "online": payload.get("BackendState") == "Running",
        "hostname": dns_name.split(".")[0] if dns_name else None,
        "dns_name": dns_name or None,
        "ips": ips,
        "serve": serve_payload or None,
        "error": None if status.get("ok") else status.get("stderr"),
    }


def _cloudflared_status() -> dict[str, Any]:
    binary = _binary("cloudflared")
    if not binary:
        return {"installed": False}
    version = _run([binary, "--version"], timeout=5)
    procs = _processes("cloudflared")
    tunnels = [p for p in procs if "tunnel" in p.get("command", "")]
    state = _read_json(REMOTE_STATE, {})
    quick = state.get("cloudflared") or {}
    return {
        "installed": True,
        "path": binary,
        "version": (version.get("stdout") or "").splitlines()[0] if version.get("stdout") else None,
        "running": bool(tunnels),
        "processes": tunnels,
        "managed": quick,
        "error": None if version.get("ok") else version.get("stderr"),
    }


def status() -> dict[str, Any]:
    """探测本机 Tailscale / Cloudflare 接入能力与当前状态。"""
    cluster = _cluster()
    ui_port = int((cluster.get("control") or {}).get("ui_port") or 8787)
    state = _read_json(REMOTE_STATE, {})
    tail = _tailscale_status()
    cf = _cloudflared_status()
    public_risk = bool((tail.get("online") and tail.get("serve")) or cf.get("running"))
    warnings = []
    if not _auth_enabled():
        warnings.append("控制面鉴权未开启：公网暴露前请先执行 gpu-control auth set-password --generate --enable。")
    if not tail.get("installed") and not cf.get("installed"):
        warnings.append("未检测到 tailscale / cloudflared；可先安装其一，或继续使用 SSH 隧道。")
    if public_risk and not _auth_enabled():
        warnings.append("检测到可能的公网/远程入口，但鉴权未开启——请立即开启鉴权或关闭入口。")
    return {
        "ok": True,
        "generated_at": _now(),
        "ui_port": ui_port,
        "auth_enabled": _auth_enabled(),
        "tailscale": tail,
        "cloudflared": cf,
        "last": state,
        "ssh_tunnel": f"ssh -N -L {ui_port}:127.0.0.1:{ui_port} <control>",
        "modes": list(MODES),
        "warnings": warnings,
    }


def _recommend(status_payload: dict[str, Any] | None = None) -> str | None:
    st = status_payload or status()
    if st.get("tailscale", {}).get("installed"):
        return "tailscale-serve"
    if st.get("cloudflared", {}).get("installed"):
        return "cloudflared-tunnel"
    return None


def plan(mode: str | None = None, *, port: int | None = None, force_public: bool = False) -> dict[str, Any]:
    """生成接入计划：命令、URL、风险与前置条件。"""
    st = status()
    port = int(port or st.get("ui_port") or 8787)
    chosen = (mode or "").strip()
    if chosen in {"", "auto"}:
        chosen = _recommend(st) or "tailscale-serve"
    if chosen not in MODES:
        return {"ok": False, "error": f"未知 mode：{chosen}", "modes": list(MODES)}
    commands: list[str] = []
    urls: list[str] = []
    notes: list[str] = []
    blocking: list[str] = []
    if chosen == "tailscale-serve":
        if not st["tailscale"].get("installed"):
            blocking.append("未安装 tailscale：https://tailscale.com/download")
        commands = [f"tailscale serve --bg http://127.0.0.1:{port}"]
        dns = (st.get("tailscale") or {}).get("dns_name")
        urls = [f"https://{dns}" if dns else "<tailnet-host>"]
        notes.append("仅在 tailnet 内可见；不会暴露到公网。")
    elif chosen == "tailscale-funnel":
        if not st["tailscale"].get("installed"):
            blocking.append("未安装 tailscale")
        commands = [f"tailscale funnel --bg http://127.0.0.1:{port}"]
        dns = (st.get("tailscale") or {}).get("dns_name")
        urls = [f"https://{dns}" if dns else "<tailnet-host>"]
        notes.append("Funnel 会暴露到公网；务必开启控制面鉴权，并建议在 Tailscale 控制台限制 ACL。")
        if not st.get("auth_enabled") and not force_public:
            blocking.append("公网暴露需要先开启控制面鉴权（或显式 force）。")
    elif chosen == "cloudflared-quick":
        if not st["cloudflared"].get("installed"):
            blocking.append("未安装 cloudflared：https://developers.cloudflare.com/cloudflare-one/")
        commands = [f"cloudflared tunnel --url http://127.0.0.1:{port} --no-autoupdate"]
        urls = ["<cloudflared 日志中的 trycloudflare.com URL>"]
        notes.append("Quick Tunnel 是临时公网 URL，无稳定性保证；仅用于临时演示。")
        if not st.get("auth_enabled") and not force_public:
            blocking.append("公网暴露需要先开启控制面鉴权（或显式 force）。")
    else:  # cloudflared-tunnel
        if not st["cloudflared"].get("installed"):
            blocking.append("未安装 cloudflared")
        commands = [
            "cloudflared tunnel login",
            "cloudflared tunnel create computedeck",
            "cloudflared tunnel route dns computedeck control.example.com",
            "cloudflared tunnel run computedeck",
        ]
        urls = ["https://control.example.com（替换为你的域名）"]
        notes.append("推荐配合 Cloudflare Access（Zero Trust）做身份校验；命名隧道可长期稳定运行。")
    return {
        "ok": not blocking,
        "mode": chosen,
        "port": port,
        "commands": commands,
        "urls": urls,
        "notes": notes,
        "blocking": blocking,
        "auth_enabled": st.get("auth_enabled"),
        "status": st,
    }


def _save_state(update: dict[str, Any]) -> dict[str, Any]:
    state = _read_json(REMOTE_STATE, {})
    if not isinstance(state, dict):
        state = {}
    state.update(update)
    state["updated_at"] = _now()
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    REMOTE_STATE.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return state


def up(
    *,
    mode: str,
    port: int | None = None,
    confirm: bool = False,
    force: bool = False,
) -> dict[str, Any]:
    """执行接入计划；必须显式 ``confirm=True``（UI 二次确认后传入）。"""
    if not confirm:
        return {"ok": False, "error": "needs_confirm", "detail": "远程接入会修改本机网络暴露面，需要确认"}
    planned = plan(mode=mode, port=port, force_public=force)
    if not planned.get("ok"):
        return {"ok": False, "error": "plan_blocked", "plan": planned}
    mode = planned["mode"]
    port = int(planned["port"])
    if mode == "tailscale-serve":
        result = _run(["tailscale", "serve", "--bg", f"http://127.0.0.1:{port}"], timeout=20)
    elif mode == "tailscale-funnel":
        result = _run(["tailscale", "funnel", "--bg", f"http://127.0.0.1:{port}"], timeout=20)
    elif mode == "cloudflared-quick":
        binary = _binary("cloudflared")
        if not binary:
            return {"ok": False, "error": "cloudflared_missing"}
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        log = CLOUDFLARED_LOG.open("a", encoding="utf-8")
        try:
            proc = subprocess.Popen(
                [binary, "tunnel", "--url", f"http://127.0.0.1:{port}", "--no-autoupdate"],
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        finally:
            log.close()
        _save_state(
            {
                "cloudflared": {
                    "mode": mode,
                    "pid": proc.pid,
                    "port": port,
                    "started_at": _now(),
                    "log": str(CLOUDFLARED_LOG),
                }
            }
        )
        return {
            "ok": True,
            "mode": mode,
            "pid": proc.pid,
            "url": None,
            "note": f"Quick Tunnel 已启动；URL 见 {CLOUDFLARED_LOG}（通常 5-10 秒后出现）。",
        }
    else:
        binary = _binary("cloudflared")
        if not binary:
            return {"ok": False, "error": "cloudflared_missing"}
        return {
            "ok": False,
            "error": "manual_setup_required",
            "detail": "命名隧道需要先 cloudflared tunnel login/create/route dns；请按计划命令执行。",
            "plan": planned,
        }
    if not result.get("ok"):
        return {"ok": False, "error": "command_failed", "result": result}
    _save_state({"last_mode": mode, "port": port, "started_at": _now()})
    return {"ok": True, "mode": mode, "port": port, "plan": planned, "result": result}


def down(*, mode: str | None = None, confirm: bool = False) -> dict[str, Any]:
    """关闭接入：重置 tailscale serve/funnel，结束托管的 cloudflared。"""
    if not confirm:
        return {"ok": False, "error": "needs_confirm"}
    results = []
    chosen = (mode or "all").strip()
    if chosen in {"all", "tailscale-serve", "tailscale-funnel", "tailscale"} and _binary("tailscale"):
        results.append({"tailscale_serve": _run(["tailscale", "serve", "reset"], timeout=15)})
        results.append({"tailscale_funnel": _run(["tailscale", "funnel", "reset"], timeout=15)})
    state = _read_json(REMOTE_STATE, {})
    managed = (state.get("cloudflared") or {}) if isinstance(state, dict) else {}
    pid = managed.get("pid")
    if pid:
        try:
            os.kill(int(pid), signal.SIGTERM)
            results.append({"cloudflared": {"ok": True, "stopped_pid": pid}})
        except (OSError, ValueError) as exc:
            results.append({"cloudflared": {"ok": False, "error": str(exc)}})
    return {"ok": True, "results": results}


def summary_line(payload: dict[str, Any] | None = None) -> str:
    st = payload or status()
    tail = st.get("tailscale") or {}
    cf = st.get("cloudflared") or {}
    bits = []
    if tail.get("installed"):
        bits.append(f"tailscale={tail.get('backend_state') or 'installed'}")
    if cf.get("installed"):
        bits.append("cloudflared=" + ("running" if cf.get("running") else "installed"))
    if not bits:
        bits.append("仅 SSH 隧道可用")
    return " · ".join(bits)
