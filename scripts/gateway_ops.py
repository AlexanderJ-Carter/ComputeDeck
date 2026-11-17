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
import fcntl
import functools
import json
import os
import re
import secrets
import shutil
import subprocess
import time
from pathlib import Path
from urllib.parse import quote, urlparse

import _bootstrap  # noqa: F401
from paths import LOCAL_DIR, PROJECT_DIR, SCRIPTS_DIR, STATE_DIR, VENV_PYTHON, cluster_file, expand_path
from process_util import pid_alive, pid_matches, terminate_pid
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
LITELLM_START_STATE = STATE_DIR / "gateway" / "litellm-start.json"
OUT_DIR = STATE_DIR / "gateway"
LITELLM_MASTER_KEY_FILE = LOCAL_DIR / "secrets" / "litellm_master_key"


def litellm_master_key_path() -> Path:
    return LITELLM_MASTER_KEY_FILE


def read_litellm_master_key() -> str | None:
    path = LITELLM_MASTER_KEY_FILE
    if not path.is_file():
        return None
    key = path.read_text(encoding="utf-8").strip()
    return key or None


def write_litellm_master_key(key: str) -> Path:
    text = str(key or "").strip()
    if not text:
        raise ValueError("master_key 不能为空")
    path = LITELLM_MASTER_KEY_FILE
    _write_secret(path, text + "\n")
    return path


def clear_litellm_master_key() -> bool:
    path = LITELLM_MASTER_KEY_FILE
    if path.is_file():
        path.unlink()
        return True
    return False


def generate_litellm_master_key() -> str:
    return "sk-cd-" + secrets.token_urlsafe(24)


DATABASE_URL_ENV = "DATABASE_URL"
LITELLM_PG_BASE = Path.home() / ".local" / "share" / "computedeck" / "litellm-pg"
LITELLM_PG_DATA = Path(os.environ.get("GPU_CONTROL_LITELLM_PG_DATA") or (LITELLM_PG_BASE / "data"))
LITELLM_PG_LOG = LITELLM_PG_BASE / "postgres.log"
_DEFAULT_PG_PORT = 5433


def _env_pg_port() -> int:
    raw = os.environ.get("GPU_CONTROL_LITELLM_PG_PORT", str(_DEFAULT_PG_PORT))
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        return _DEFAULT_PG_PORT


LITELLM_PG_PORT = _env_pg_port()
LITELLM_DB_PASSWORD_FILE = LOCAL_DIR / "secrets" / "litellm_db_password"
LITELLM_DB_URL_FILE = LOCAL_DIR / "secrets" / "litellm_database_url"


def _safe_port(parsed) -> int | None:
    """``urlparse().port`` 对非法端口会抛 ValueError；解析失败按无端口处理。"""
    try:
        return parsed.port
    except ValueError:
        return None


def _mask_db_url(url: str) -> str:
    """日志 / 状态里只显示库地址，绝不回显密码。"""
    try:
        parsed = urlparse(str(url).strip())
    except ValueError:
        return "(invalid)"
    if not parsed.scheme:
        return "(invalid)"
    port_num = _safe_port(parsed)
    hostport = parsed.netloc.rsplit("@", 1)[-1] if parsed.netloc else ""
    has_port = ("]:" in hostport) if hostport.startswith("[") else (":" in hostport)
    if has_port and port_num is None:
        return "(invalid)"
    user = parsed.username or ""
    auth = f"{user}@" if user else ""
    host = parsed.hostname or "?"
    port = f":{port_num}" if port_num else ""
    dbname = (parsed.path or "").lstrip("/")
    return f"{parsed.scheme}://{auth}{host}{port}/{dbname}"


def _write_secret(path: Path, text: str) -> None:
    """以 0600 原子创建/覆盖敏感文件，避免 write-then-chmod 的可读窗口。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.parent.chmod(0o700)
    except OSError:
        pass
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(text)
    try:
        path.chmod(0o600)
    except OSError:
        pass


def _litellm_db_url(gateway: dict | None = None) -> tuple[str, str | None]:
    """读取 LiteLLM 管理库连接串：配置内联 > 文件 > 进程环境。"""
    gateway = gateway if gateway is not None else {}
    raw = str(gateway.get("litellm_database_url") or "").strip()
    if raw:
        return raw, "config"
    file_raw = str(gateway.get("litellm_database_url_file") or "").strip()
    if file_raw:
        path = Path(expand_path(file_raw))
        if not path.is_absolute():
            path = PROJECT_DIR / path
        try:
            raw = path.read_text(encoding="utf-8").strip()
        except OSError:
            raw = ""
        if raw:
            return raw, "file"
    raw = str(os.environ.get(DATABASE_URL_ENV) or "").strip()
    return (raw, "env") if raw else ("", None)


def _pg_bin_dir() -> Path | None:
    """定位 pgserver 自带的 Postgres 二进制（无需 root 安装系统 Postgres）。"""
    candidates: list[Path] = []
    env = str(os.environ.get("GPU_CONTROL_PG_BIN") or "").strip()
    if env:
        candidates.append(Path(expand_path(env)))
    home_patterns = [
        Path.home() / ".local" / "lib",
        PROJECT_DIR / ".venv" / "lib",
    ]
    for root in home_patterns:
        candidates.extend(root.glob("python3*/site-packages/pgserver/pginstall/bin"))
    which = shutil.which("pg_ctl")
    if which:
        candidates.append(Path(which).parent)
    for candidate in candidates:
        if (candidate / "pg_ctl").is_file() and (candidate / "postgres").is_file():
            return candidate
    return None


def _is_embedded_db_url(url: str) -> bool:
    try:
        parsed = urlparse(str(url or "").strip())
    except ValueError:
        return False
    host = (parsed.hostname or "").lower()
    return host in {"127.0.0.1", "localhost"} and _safe_port(parsed) == LITELLM_PG_PORT


def _pg_conf_port() -> int | None:
    """读取数据目录里 initdb 时写下的端口（未初始化返回 None）。"""
    conf = LITELLM_PG_DATA / "postgresql.conf"
    if not conf.is_file():
        return None
    for line in conf.read_text(encoding="utf-8", errors="ignore").splitlines():
        match = re.match(r"^\s*port\s*=\s*(\d+)\s*$", line)
        if match:
            return int(match.group(1))
    return None


def _litellm_pg_running() -> bool:
    pg_ctl = _pg_bin_dir()
    if not pg_ctl or not (LITELLM_PG_DATA / "PG_VERSION").is_file():
        return False
    result = subprocess.run(
        [str(pg_ctl / "pg_ctl"), "-D", str(LITELLM_PG_DATA), "status"],
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def _litellm_pg_schema_tables() -> int | None:
    """已建表数量；用于判断 LiteLLM 的 Prisma schema 是否已导入。"""
    if not _litellm_pg_running():
        return None
    pg = _pg_bin_dir()
    if not pg:
        return None
    result = subprocess.run(
        [
            str(pg / "psql"),
            "-h",
            str(LITELLM_PG_BASE),
            "-p",
            str(_pg_conf_port() or LITELLM_PG_PORT),
            "-U",
            "litellm",
            "-d",
            "litellm",
            "-tAc",
            "select count(*) from information_schema.tables where table_schema='public'",
        ],
        capture_output=True,
        text=True,
    )
    try:
        return int(result.stdout.strip())
    except (TypeError, ValueError):
        return None


@functools.cache
def _prisma_ready() -> bool | None:
    """LiteLLM 使用的解释器能否 import prisma（预生成客户端是否就绪）。"""
    litellm_bin = _resolve_litellm_bin()
    if not litellm_bin:
        return None
    try:
        binary = Path(litellm_bin)
        if binary.stat().st_size > 1_000_000:
            return None
        first = binary.read_text(encoding="utf-8", errors="ignore").splitlines()[0]
    except (OSError, IndexError):
        return None
    if not first.startswith("#!"):
        return None
    python = first[2:].strip().split()[0] if first[2:].strip() else ""
    if not python:
        return None
    try:
        result = subprocess.run(
            [python, "-c", "import prisma"],
            capture_output=True,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.returncode == 0


def litellm_pg_status() -> dict:
    bin_dir = _pg_bin_dir()
    initialized = (LITELLM_PG_DATA / "PG_VERSION").is_file()
    running = _litellm_pg_running()
    prisma_ready = _prisma_ready()
    if not bin_dir:
        hint = "未找到 pgserver：/usr/bin/python3 -m pip install --user pgserver==0.1.4"
    elif prisma_ready is False:
        hint = "LiteLLM 解释器无法 import prisma：Admin 登录会回退为“Not connected to DB”"
    else:
        hint = None
    return {
        "initialized": initialized,
        "running": running,
        "data_dir": str(LITELLM_PG_DATA),
        "log": str(LITELLM_PG_LOG),
        "port": _pg_conf_port() or LITELLM_PG_PORT,
        "configured_port": LITELLM_PG_PORT,
        "bin_dir": str(bin_dir) if bin_dir else None,
        "url_configured": LITELLM_DB_URL_FILE.is_file(),
        "schema_tables": _litellm_pg_schema_tables() if running else None,
        "prisma_ready": prisma_ready,
        "hint": hint,
    }


def _litellm_pg_start() -> dict:
    pg = _pg_bin_dir()
    if not pg:
        raise RuntimeError(
            "未找到 pgserver 自带的 Postgres；先执行 "
            "/usr/bin/python3 -m pip install --user pgserver==0.1.4"
        )
    if not (LITELLM_PG_DATA / "PG_VERSION").is_file():
        raise RuntimeError("嵌入式 Postgres 未初始化：先执行 gpu-control gateway litellm-db init")
    conf_port = _pg_conf_port()
    if conf_port is not None and conf_port != LITELLM_PG_PORT:
        raise RuntimeError(
            f"数据目录端口 {conf_port} 与当前 GPU_CONTROL_LITELLM_PG_PORT={LITELLM_PG_PORT} 不一致；"
            "请使用相同端口，或备份并重新执行 gateway litellm-db init"
        )
    LITELLM_PG_BASE.mkdir(parents=True, exist_ok=True)
    try:
        LITELLM_PG_BASE.chmod(0o700)
    except OSError:
        pass
    # 串行化启动：两个同时到达的 gateway start 不应互相抢 data 目录。
    lock_path = LITELLM_PG_BASE / ".start.lock"
    with lock_path.open("w", encoding="utf-8") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        except OSError:
            pass
        if _litellm_pg_running():
            return litellm_pg_status()
        result = subprocess.run(
            [str(pg / "pg_ctl"), "-D", str(LITELLM_PG_DATA), "-l", str(LITELLM_PG_LOG), "-w", "start"],
            capture_output=True,
            text=True,
        )
        status = litellm_pg_status()
        if result.returncode != 0 or not status["running"]:
            raise RuntimeError(
                (result.stderr or result.stdout or "pg_ctl start failed").strip()
                + f"（日志：{LITELLM_PG_LOG}）"
            )
        return status


def ensure_litellm_pg(gateway: dict | None = None) -> dict:
    """嵌入式库模式：LiteLLM 启动前确保 Postgres 在跑。"""
    gateway = gateway if gateway is not None else _gateway_cfg()
    status = litellm_pg_status()
    if not status["initialized"]:
        raise RuntimeError("嵌入式 Postgres 未初始化：先执行 gpu-control gateway litellm-db init")
    if not status["running"]:
        status = _litellm_pg_start()
    return status


def _litellm_pg_apply_schema() -> dict:
    pg = _pg_bin_dir()
    if not pg:
        raise RuntimeError("未找到 pgserver 的 psql")
    candidates = [
        # 默认只信仓库内固定版本；需要新 schema 时显式放到 config/local 覆盖。
        PROJECT_DIR / "config" / "examples" / "litellm-schema-1.86.7.sql",
        LOCAL_DIR / "litellm-schema.sql",
    ]
    schema = next((p for p in candidates if p.is_file()), None)
    if schema is None:
        raise RuntimeError(
            "未找到 LiteLLM schema SQL；把生成的 schema.sql 放到 config/local/litellm-schema.sql 再执行"
        )
    result = subprocess.run(
        [
            str(pg / "psql"),
            "-h",
            str(LITELLM_PG_BASE),
            "-p",
            str(_pg_conf_port() or LITELLM_PG_PORT),
            "-U",
            "litellm",
            "-d",
            "litellm",
            "-v",
            "ON_ERROR_STOP=1",
            "-q",
            "-f",
            str(schema),
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout).strip())
    return {"ok": True, "schema": str(schema), **litellm_pg_status()}


def maybe_start_embedded_db(gateway: dict | None = None) -> dict | None:
    """显式 embedded 模式时，LiteLLM 启动前自动拉起库；其它模式返回 None。"""
    gateway = gateway if gateway is not None else {}
    mode = str(gateway.get("litellm_database_mode") or "").strip().lower()
    if mode != "embedded":
        return None
    return ensure_litellm_pg(gateway)


def litellm_database(gateway: dict | None = None) -> dict:
    """LiteLLM Admin UI / 虚拟密钥需要 Postgres；不接库时只保留 Master Key 代理。"""
    gateway = gateway if gateway is not None else {}
    raw, source = _litellm_db_url(gateway)
    scheme_ok = (not raw) or raw.startswith(("postgres://", "postgresql://"))
    configured = bool(raw) and scheme_ok
    mode = str(gateway.get("litellm_database_mode") or "").strip().lower()
    if mode not in {"embedded", "external"}:
        mode = ""
    embedded = mode == "embedded"
    pg = litellm_pg_status() if embedded else None
    if not raw:
        hint = "未接数据库：Admin UI 不可登录；请用 Master Key 调 /v1（配 gateway.litellm_database_url 可启用管理台）"
    elif not scheme_ok:
        hint = "DATABASE_URL 需为 postgres:// 或 postgresql://（LiteLLM 只带 Postgres schema）"
    elif embedded and pg and not pg["running"]:
        hint = "嵌入式 Postgres 未运行；启动 LiteLLM 时会自动拉起，或手动执行 gpu-control gateway litellm-db start"
    else:
        hint = None
    return {
        "configured": configured,
        "source": source,
        "url_masked": _mask_db_url(raw) if raw else "",
        "scheme_ok": scheme_ok,
        "admin_ui": configured,
        "mode": mode or "external",
        "server_running": pg["running"] if pg else None,
        "embedded": embedded,
        "server": pg,
        "hint": hint,
    }


def litellm_master_key_status() -> dict:
    configured = bool(read_litellm_master_key())
    return {
        "configured": configured,
        "path": str(LITELLM_MASTER_KEY_FILE),
        "login_hint": "API：Authorization: Bearer <Master Key>；Admin UI（需 DATABASE_URL）：用户名 admin，密码 = Master Key"
        if configured
        else "未配置 Master Key：API 与 Admin UI 都无法登录",
    }


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


def _pid_alive(pid: int | None) -> bool:
    """保留内部名字，实际走跨平台实现（Linux / macOS）。"""
    return pid_alive(pid)


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


def _edge_health_at(host: str, port: int, tls: bool) -> bool:
    """探测单个主机上的 Edge 健康身份。"""
    import http.client
    import ssl

    conn = None
    try:
        if tls:
            context = ssl.create_default_context()
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
            conn = http.client.HTTPSConnection(host, port, timeout=0.8, context=context)
        else:
            conn = http.client.HTTPConnection(host, port, timeout=0.8)
        conn.request("GET", "/_gpu_control/health")
        response = conn.getresponse()
        body = response.read(8192)
        if response.status != 200:
            return False
        data = json.loads(body.decode("utf-8", "replace"))
        return data.get("service") == "gpu-control-edge"
    except (OSError, ValueError, json.JSONDecodeError):
        return False
    finally:
        if conn is not None:
            try:
                conn.close()
            except OSError:
                pass


def _edge_health_ok(ingress: dict) -> bool:
    """端口监听者是否真的是内置 Edge（避免 SSH 隧道 / 其它服务冒充）。"""
    port = int(ingress["port"])
    tls = bool((ingress.get("tls") or {}).get("enabled"))
    hosts = ["127.0.0.1"]
    bind = str(ingress.get("bind") or "").strip()
    if bind and bind not in {"0.0.0.0", "::", ""}:
        hosts.append(bind)
    return any(_edge_health_at(host, port, tls) for host in dict.fromkeys(hosts))


def _edge_runtime(ingress: dict) -> tuple[bool, int | None, dict]:
    """综合 pid / 状态文件 / 健康身份判断 Edge 是否在跑。

    端口监听本身只说明“有东西在听”，可能是 SSH 隧道或别的服务；
    只有 pid/状态文件命中，或 ``/_gpu_control/health`` 返回 Edge 身份，
    才认为 Edge 在运行。``edge_status["port_conflict"]`` 会保留给上层提示。
    """
    edge_status: dict = {}
    if EDGE_STATUS.is_file():
        try:
            edge_status = json.loads(EDGE_STATUS.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            edge_status = {}
    pid = _read_pid(EDGE_PID)
    if pid and _pid_alive(pid) and pid_matches(pid, ("ingress_edge.py",)):
        return True, pid, {**edge_status, "pid_owned": True}
    status_pid = edge_status.get("pid") if isinstance(edge_status.get("pid"), int) else None
    if status_pid and _pid_alive(status_pid) and pid_matches(status_pid, ("ingress_edge.py",)):
        return True, status_pid, edge_status
    bind = str(ingress.get("bind") or "0.0.0.0")
    if _port_open(bind, int(ingress["port"])):
        if _edge_health_ok(ingress):
            # 端口身份通过，但没有本机存活 pidfile：可能是容器 / SSH 隧道。
            return True, None, {**edge_status, "port_verified": True}
        return False, None, {**edge_status, "port_conflict": True}
    return False, None, edge_status


def _stop_pidfile(path: Path, name: str, *, expected: tuple[str, ...] = ()) -> dict:
    """按 pidfile 停止进程；pid 身份不匹配时只清理文件，绝不误杀。"""
    pid = _read_pid(path)
    if pid is None:
        if path.is_file():
            path.unlink()
        return {"ok": True, "name": name, "stopped": False, "reason": "not running"}
    result = terminate_pid(pid, expected=expected, group=True)
    # 身份不匹配说明 pidfile 过期，可以清理；权限不足 / 杀不掉时保留文件，
    # 避免进程仍在跑却失去追踪。
    if path.is_file() and (result.get("ok") or result.get("stale")):
        path.unlink()
    return {"ok": bool(result.get("ok")), "name": name, **result}


def _log_tail(path: Path, n: int = 40, max_bytes: int = 64 * 1024) -> str:
    """只读取日志尾部固定窗口，避免看板轮询时整文件读入内存。"""
    if not path.is_file():
        return ""
    try:
        size = path.stat().st_size
        with path.open("rb") as handle:
            if size > max_bytes:
                handle.seek(-max_bytes, os.SEEK_END)
            raw = handle.read(max_bytes)
    except OSError:
        return ""
    text = raw.decode("utf-8", "replace")
    text = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", text)
    lines = text.splitlines()
    if size > max_bytes and lines:
        # 第一行很可能是被截断的半行，丢掉。
        lines = lines[1:]
    return "\n".join(lines[-n:])


def _resolve_litellm_bin(gateway: dict | None = None) -> str | None:
    """在瘦 PATH 下也能找到已安装的 litellm CLI（禁止靠启动脚本隐式 pip）。"""
    gateway = gateway if gateway is not None else {}
    configured = str(gateway.get("litellm_bin") or "").strip()
    candidates: list[Path] = []
    if configured:
        candidates.append(Path(expand_path(configured)))
    which = shutil.which("litellm")
    if which:
        candidates.append(Path(which))
    home_local = Path.home() / ".local" / "bin" / "litellm"
    candidates.append(home_local)
    candidates.append(PROJECT_DIR / ".venv" / "bin" / "litellm")
    candidates.append(Path(sys.executable).resolve().parent / "litellm")
    seen: set[str] = set()
    for path in candidates:
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        try:
            if path.is_file() and os.access(path, os.X_OK):
                return str(path)
        except OSError:
            continue
    return None


def _litellm_path_env(base: dict | None = None) -> dict:
    """为兜底脚本补上 ~/.local/bin 与当前解释器 bin，避免 command -v 再次失败。"""
    env = dict(base if base is not None else os.environ)
    extras = [
        str(Path.home() / ".local" / "bin"),
        str(Path(sys.executable).resolve().parent),
        str(PROJECT_DIR / ".venv" / "bin"),
    ]
    old = env.get("PATH") or ""
    parts = [p for p in extras if p and Path(p).is_dir()]
    env["PATH"] = os.pathsep.join(parts + ([old] if old else []))
    return env


def _litellm_phase(
    *,
    probe: dict,
    pid: int | None,
    listen_ok: bool,
    pid_owned: bool = False,
) -> str:
    """LiteLLM 运行阶段。

    ``running`` 只表示“本工具启动且端口在听”；端口有服务但没有 pidfile
    归属时返回 ``unmanaged``，端口被非 LiteLLM 占用时返回
    ``port_conflict``，避免把 SSH 隧道或别的服务当成 LiteLLM。
    """
    if pid and pid_owned:
        return "running" if listen_ok else "starting"
    if listen_ok:
        return "unmanaged" if probe.get("ready") else "port_conflict"
    if not probe.get("ready"):
        return "missing"
    return "ready"


def _litellm_start_state() -> dict:
    """最近一次启动尝试（用于把失败原因保留到 status）。"""
    if not LITELLM_START_STATE.is_file():
        return {}
    try:
        data = json.loads(LITELLM_START_STATE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _record_litellm_start(result: dict) -> dict:
    """落盘最近一次启动结果；失败信息可在看板诊断里直接看到。"""
    payload = {
        "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "ok": bool(result.get("ok")),
        "phase": result.get("phase"),
        "error": result.get("error"),
        "hint": result.get("hint"),
        "pid": result.get("pid"),
        "port": result.get("port"),
        "mode": result.get("mode"),
        "binary": result.get("binary"),
    }
    LITELLM_START_STATE.parent.mkdir(parents=True, exist_ok=True)
    try:
        LITELLM_START_STATE.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    except OSError:
        pass
    return payload


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
    """探测 LiteLLM 是否可启动（不启子进程；不触发 pip）。"""
    proxy_dir_raw = gateway.get("openai_proxy_dir") or gateway.get("litellm_dir")
    proxy_dir = Path(expand_path(str(proxy_dir_raw))) if proxy_dir_raw else None
    litellm_bin = _resolve_litellm_bin(gateway)
    start_sh = proxy_dir / "start_litellm.sh" if proxy_dir else None
    env_file = proxy_dir / ".env" if proxy_dir else None
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
            reasons.append("将走外部 start 脚本（已补 PATH；请勿依赖脚本内 pip install）")
    else:
        ready = False
        mode = "missing"
        reasons.append("无 litellm 二进制（which / ~/.local/bin / venv）且无 start_litellm.sh")
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
    edge_port_open: bool,
    edge_port_conflict: bool,
    edge_port_verified: bool,
    litellm_pid: int | None,
    litellm_port: int,
    litellm_probe: dict,
    litellm_phase: str,
    litellm_start: dict,
    nginx_bin: str | None,
    artifact: Path,
) -> list[dict]:
    """看板用配套设施清单：角色清晰，可选组件不标成故障。"""
    kind = (ingress.get("kind") or "edge").lower()
    edge_primary = kind in {"edge", "both", "all"}
    ll_wanted = kind in {"litellm", "both", "all"}
    rows: list[dict] = []

    if edge_pid:
        edge_state, edge_label, edge_ok, edge_sev = "running", "运行中", True, "ok"
    elif edge_port_verified:
        edge_state, edge_label, edge_ok, edge_sev = "running", "端口可达", True, "warn"
    elif edge_port_conflict:
        edge_state, edge_label, edge_ok, edge_sev = "port_conflict", "端口被占用", False, "error"
    elif edge_primary:
        edge_state, edge_label, edge_ok, edge_sev = "stopped", "应启动", False, "error"
    else:
        edge_state, edge_label, edge_ok, edge_sev = "idle", "备用", True, "ok"
    if edge_port_verified and not edge_pid:
        detail = f"健康检查通过（{ingress['bind']}:{ingress['port']}），但没有本机 pidfile（可能经 SSH 隧道 / 容器）"
    elif edge_port_conflict:
        detail = f"端口 {ingress['bind']}:{ingress['port']} 被其它进程占用，非内置 Edge"
    else:
        detail = f"内置反代 · {ingress['bind']}:{ingress['port']}"
    rows.append(
        {
            "id": "edge",
            "name": "Edge LB",
            "role": "primary" if edge_primary else "optional",
            "required": edge_primary,
            "state": edge_state,
            "label": edge_label,
            "ok": edge_ok,
            "severity": edge_sev,
            "detail": detail,
            "action": (
                f"lsof -nP -iTCP:{ingress['port']} -sTCP:LISTEN"
                if edge_port_conflict
                else (None if edge_ok else "gpu-control gateway ensure")
            ),
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
    bin_bit = litellm_probe.get("binary") or ""
    master_ok = bool(read_litellm_master_key())
    if litellm_phase == "running":
        ll_state, ll_label, ll_ok, sev = "running", "运行中", True, "ok"
        detail = f"LiteLLM :{litellm_port}" + (f" · {bin_bit}" if bin_bit else "")
        if not master_ok:
            detail += " · 未配 Master Key（Admin UI 不可登录）"
            sev = "warn"
    elif litellm_phase == "starting":
        ll_state, ll_label, ll_ok, sev = "starting", "启动中", True, "warn"
        detail = f"进程 {litellm_pid} 尚未监听 :{litellm_port}"
    elif litellm_phase == "unmanaged":
        ll_state, ll_label, ll_ok, sev = "running", "外部运行", True, "warn"
        detail = f"端口 :{litellm_port} 有服务，但没有 gpu-control pidfile 归属"
        if not master_ok:
            detail += " · 未配 Master Key"
    elif litellm_phase == "port_conflict":
        ll_state, ll_label, ll_ok = "port_conflict", "端口冲突", not ll_wanted
        sev = "error" if ll_wanted else "warn"
        detail = f"端口 :{litellm_port} 被其它进程占用，且不像已安装的 LiteLLM"
    elif litellm_phase == "failed":
        ll_state, ll_label, ll_ok = "failed", "启动失败", not ll_wanted
        sev = "warn" if ll_wanted else "info"
        detail = str(litellm_start.get("error") or "上次启动失败，详见执行输出 / 日志")
    elif litellm_probe.get("ready"):
        ll_state = "ready"
        ll_label = "就绪未启" if ll_wanted else "就绪"
        ll_ok, sev = True, "ok"
        detail = f"LiteLLM :{litellm_port} 可启动"
        if bin_bit:
            detail += f" · {bin_bit}"
        elif dir_bit:
            detail += f" · {dir_bit}"
    else:
        ll_state = "blocked"
        ll_label = "缺依赖" if ll_wanted else "可选·未配"
        ll_ok = not ll_wanted
        sev = "warn" if ll_wanted else "info"
        detail = "；".join(litellm_probe.get("reasons") or ["未安装 / 未配置"])
    action = None
    if litellm_phase in {"ready", "failed", "port_conflict"} and litellm_probe.get("ready"):
        action = "gpu-control gateway start --kind litellm"
    if litellm_phase == "unmanaged":
        action = f"lsof -nP -iTCP:{litellm_port} -sTCP:LISTEN"
    rows.append(
        {
            "id": "litellm",
            "name": "LiteLLM",
            "role": "optional",
            "required": ll_wanted,
            "state": ll_state,
            "label": ll_label,
            "ok": ll_ok,
            "severity": sev,
            "detail": detail,
            "action": action,
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
    edge_port_open = _port_open(str(ingress.get("bind") or "0.0.0.0"), int(ingress["port"]))
    edge_port_conflict = bool(edge_status.get("port_conflict"))
    litellm_pid = _read_pid(LITELLM_PID)
    litellm_pid_owned = bool(litellm_pid and pid_matches(litellm_pid, ("litellm",)))
    if not edge_running and not edge_port_open and EDGE_STATUS.is_file():
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
    litellm_listen_ok = _port_open("127.0.0.1", litellm_port)
    litellm_phase = _litellm_phase(
        probe=ll_probe,
        pid=litellm_pid,
        listen_ok=litellm_listen_ok,
        pid_owned=litellm_pid_owned,
    )
    litellm_start = _litellm_start_state()
    if (
        litellm_phase == "ready"
        and litellm_start.get("ok") is False
        and litellm_start.get("phase") == "failed"
    ):
        litellm_phase = "failed"
    companions = _companions(
        ingress=ingress,
        edge_pid=edge_pid if edge_running else None,
        edge_port_open=edge_port_open,
        edge_port_conflict=edge_port_conflict,
        edge_port_verified=bool(edge_status.get("port_verified")),
        litellm_pid=litellm_pid,
        litellm_port=litellm_port,
        litellm_probe=ll_probe,
        litellm_phase=litellm_phase,
        litellm_start=litellm_start,
        nginx_bin=nginx_bin,
        artifact=artifact,
    )
    companions.append(_tls_companion(tls_status, ingress))
    if edge_running:
        scheme = "https" if tls_status.get("ready") else "http"
        for row in companions:
            if row["id"] == "edge":
                row["state"] = "running"
                row["label"] = "运行中" if edge_pid else "端口可达"
                row["ok"] = True
                row["severity"] = "ok" if edge_pid else "warn"
                row["action"] = None
                row["detail"] = (
                    f"{scheme} · {ingress['bind']}:{ingress['port']}"
                    if edge_pid
                    else f"{scheme} · 健康检查通过，无本机 pidfile（SSH 隧道 / 容器）"
                )
    healthy = all(c.get("ok") for c in companions if c.get("required"))
    kind = (ingress.get("kind") or "edge").lower()
    litellm_upstream = str(gateway.get("litellm_upstream") or "nodes").strip().lower()
    if litellm_upstream in {"ingress", "lb", "edge-lb"}:
        litellm_upstream = "edge"
    if litellm_upstream not in {"nodes", "edge"}:
        litellm_upstream = "nodes"
    edge_active = bool(edge_running or edge_port_open)
    edge_front = bool(str(ingress.get("proxy_to") or "").strip())
    litellm_active = litellm_phase in {"running", "starting", "ready", "unmanaged"}
    duplicate_node_lb = bool(
        edge_active and not edge_front and litellm_active and litellm_upstream == "nodes"
    )
    edge_url = f"http://127.0.0.1:{int(ingress.get('port') or 18080)}"
    if litellm_upstream == "edge" and not edge_active:
        layering_summary = "LiteLLM 配置为经 Edge，但 Edge 未运行"
    elif litellm_upstream == "nodes" and litellm_active and edge_active and edge_front:
        layering_summary = "LiteLLM 是唯一节点 LB；Edge 仅反代到 LiteLLM"
    elif litellm_upstream == "nodes" and litellm_active:
        layering_summary = "LiteLLM 是唯一节点 LB（直连节点端口）"
    elif duplicate_node_lb:
        layering_summary = "Edge 与 LiteLLM 都在对节点做负载均衡（重复）"
    elif edge_active:
        layering_summary = "Edge 是唯一节点 LB；LiteLLM 为可选 OpenAI 层"
    else:
        layering_summary = "未检测到节点 LB（Edge 未运行）"
    layering = {
        "edge_active": edge_active,
        "edge_front": edge_front,
        "edge_proxy_to": ingress.get("proxy_to") or None,
        "edge_url": edge_url,
        "litellm_active": litellm_active,
        "litellm_upstream": litellm_upstream,
        "duplicate_node_lb": duplicate_node_lb,
        "summary": layering_summary,
        "recommendation": (
            f"让 Edge 只做前置代理：cluster.gateway.ingress.proxy_to=\"http://127.0.0.1:{litellm_port}\"，或停用 Edge"
            if duplicate_node_lb
            else None
        ),
    }
    on_control = _on_control_host(cluster)
    return {
        "layering": layering,
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
            "pid_owned": bool(edge_status.get("pid_owned")),
            "port_open": edge_port_open,
            "port_conflict": edge_port_conflict,
            "port_verified": bool(edge_status.get("port_verified")),
            "listen": f"{ingress['bind']}:{ingress['port']}",
            "status": edge_status,
            "log": str(EDGE_LOG),
            "healthz": f"{public}/_gpu_control/health",
            "url": public,
            "role": "front" if edge_front else "primary",
            "proxy_to": ingress.get("proxy_to") or None,
            "scheme": "https" if tls_status.get("ready") else "http",
        },
        "litellm": {
            "running": litellm_phase == "running",
            "upstream": litellm_upstream,
            "edge_url": edge_url if litellm_upstream == "edge" else None,
            "available": litellm_listen_ok,
            "phase": litellm_phase,
            "listen_ok": litellm_listen_ok,
            "pid": litellm_pid,
            "pid_owned": litellm_pid_owned,
            "port": litellm_port,
            "url": litellm_public or f"http://127.0.0.1:{litellm_port}",
            "ui_url": f"{(litellm_public or f'http://127.0.0.1:{litellm_port}').rstrip('/')}/ui",
            "dir": ll_probe.get("dir"),
            "ready": ll_probe.get("ready"),
            "mode": ll_probe.get("mode"),
            "binary": ll_probe.get("binary"),
            "start_script": ll_probe.get("start_script"),
            "env_ok": ll_probe.get("env_ok"),
            "reasons": ll_probe.get("reasons") or [],
            "runtime_config": str(LITELLM_RUNTIME),
            "log": str(LITELLM_LOG),
            "log_tail": _log_tail(LITELLM_LOG),
            "last_start": litellm_start,
            "role": "optional",
            "database": litellm_database(gateway),
            **litellm_master_key_status(),
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


def _litellm_pg_init() -> dict:
    """初始化嵌入式 Postgres + litellm 库，并把 DATABASE_URL 写入配置（并发安全）。"""
    LITELLM_PG_BASE.mkdir(parents=True, exist_ok=True)
    try:
        LITELLM_PG_BASE.chmod(0o700)
    except OSError:
        pass
    # 单独的 init 锁：内部还会调用 _litellm_pg_start()，不能复用 start 锁。
    with (LITELLM_PG_BASE / ".init.lock").open("w", encoding="utf-8") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        except OSError:
            pass
        return _litellm_pg_init_locked()


def _litellm_pg_init_locked() -> dict:
    pg = _pg_bin_dir()
    if not pg:
        raise RuntimeError(
            "未找到 pgserver；先执行 /usr/bin/python3 -m pip install --user pgserver==0.1.4"
        )
    password = (
        LITELLM_DB_PASSWORD_FILE.read_text(encoding="utf-8").strip()
        if LITELLM_DB_PASSWORD_FILE.is_file()
        else ""
    )
    if not password:
        password = secrets.token_urlsafe(24)
        _write_secret(LITELLM_DB_PASSWORD_FILE, password + "\n")
    if not (LITELLM_PG_DATA / "PG_VERSION").is_file():
        pwfile = LITELLM_PG_BASE / ".pw"
        _write_secret(pwfile, password)
        try:
            result = subprocess.run(
                [
                    str(pg / "initdb"),
                    "-D",
                    str(LITELLM_PG_DATA),
                    "-U",
                    "litellm",
                    f"--pwfile={pwfile}",
                    "--auth-local=trust",
                    "--auth-host=scram-sha-256",
                    "--encoding=UTF8",
                    "--locale=C",
                ],
                capture_output=True,
                text=True,
            )
        finally:
            pwfile.unlink(missing_ok=True)
        if result.returncode != 0:
            # PG_VERSION 可能已写入；清掉半成品，保证下次 init 可重来。
            shutil.rmtree(LITELLM_PG_DATA, ignore_errors=True)
            raise RuntimeError((result.stderr or result.stdout).strip())
        socket_dir = str(LITELLM_PG_BASE).replace("'", "''")
        with (LITELLM_PG_DATA / "postgresql.conf").open("a", encoding="utf-8") as conf:
            conf.write(
                f"\nport = {LITELLM_PG_PORT}\n"
                "listen_addresses = '127.0.0.1'\n"
                "unix_socket_permissions = 0700\n"
                f"unix_socket_directories = '{socket_dir}'\n"
            )
    _litellm_pg_start()
    port = _pg_conf_port() or LITELLM_PG_PORT
    exists = subprocess.run(
        [
            str(pg / "psql"),
            "-h",
            str(LITELLM_PG_BASE),
            "-p",
            str(port),
            "-U",
            "litellm",
            "-d",
            "postgres",
            "-tAc",
            "SELECT 1 FROM pg_database WHERE datname='litellm'",
        ],
        capture_output=True,
        text=True,
    )
    if "1" not in (exists.stdout or ""):
        created = subprocess.run(
            [
                str(pg / "createdb"),
                "-h",
                str(LITELLM_PG_BASE),
                "-p",
                str(port),
                "-U",
                "litellm",
                "litellm",
            ],
            capture_output=True,
            text=True,
        )
        if created.returncode != 0:
            raise RuntimeError((created.stderr or created.stdout).strip())

    url = (
        f"postgresql://litellm:{quote(password, safe='')}"
        f"@127.0.0.1:{port}/litellm"
    )
    _write_secret(LITELLM_DB_URL_FILE, url + "\n")

    # 写入 cluster.gateway，后续 gateway start/UI 启动直接复用。
    # 若解析到的是仓库内 cluster.json（不是 config/local），写一份 local 覆盖，避免改到受版本管理的文件。
    try:
        cfg_path = cluster_file()
        cluster = json.loads(cfg_path.read_text(encoding="utf-8"))
        local_path = (LOCAL_DIR / "cluster.json").resolve()
        if cfg_path.resolve() != local_path:
            if local_path.is_file():
                try:
                    cluster = json.loads(local_path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    pass
            cfg_path = local_path
            cfg_path.parent.mkdir(parents=True, exist_ok=True)
        gateway = cluster.setdefault("gateway", {})
        gateway["litellm_database_mode"] = "embedded"
        try:
            url_ref = str(LITELLM_DB_URL_FILE.relative_to(PROJECT_DIR))
        except ValueError:
            url_ref = str(LITELLM_DB_URL_FILE)
        gateway["litellm_database_url_file"] = url_ref
        cfg_path.write_text(json.dumps(cluster, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except FileNotFoundError as exc:
        raise RuntimeError("缺少 cluster.json；先按 config/examples 初始化集群配置") from exc
    except (OSError, ValueError, KeyError) as exc:
        raise RuntimeError(f"写入 cluster.json 失败：{exc}") from exc

    schema_result: dict | None = None
    try:
        count = _litellm_pg_schema_tables()
        if count is None or count == 0:
            schema_result = _litellm_pg_apply_schema()
    except RuntimeError as exc:
        schema_result = {"ok": False, "error": str(exc)}
    return {
        **litellm_pg_status(),
        "url_file": str(LITELLM_DB_URL_FILE),
        "schema": schema_result,
        "next": "gpu-control gateway stop --kind litellm && gpu-control gateway start --kind litellm",
    }


def _litellm_pg_stop() -> dict:
    pg = _pg_bin_dir()
    if not pg or not _litellm_pg_running():
        return litellm_pg_status()
    result = subprocess.run(
        [str(pg / "pg_ctl"), "-D", str(LITELLM_PG_DATA), "-m", "fast", "-w", "stop"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout).strip())
    return litellm_pg_status()


def cmd_litellm_db(args: argparse.Namespace) -> int:
    action = getattr(args, "db_action", None) or "status"
    try:
        if action == "init":
            payload = _litellm_pg_init()
        elif action == "start":
            payload = _litellm_pg_start()
        elif action == "stop":
            payload = _litellm_pg_stop()
        elif action == "apply-schema":
            payload = _litellm_pg_apply_schema()
        elif action == "url":
            raw, _ = _litellm_db_url(_gateway_cfg())
            payload = {
                "url_masked": _mask_db_url(raw) if raw else "",
                "url_file": str(LITELLM_DB_URL_FILE),
                "note": "默认只显示脱敏地址；需要完整连接串时加 --reveal",
            }
            if getattr(args, "reveal", False):
                payload["url"] = raw
        else:
            payload = litellm_pg_status()
    except (RuntimeError, OSError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2))
        return 1
    print(json.dumps({"ok": True, **payload}, ensure_ascii=False, indent=2))
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
    running, existing, edge_status = _edge_runtime(ingress)
    if running and existing:
        EDGE_PID.write_text(f"{existing}\n", encoding="utf-8")
        return {"ok": True, "name": "edge", "already": True, "pid": existing}
    if running:
        return {"ok": True, "name": "edge", "already": True, "pid": existing, "via": "health"}
    if edge_status.get("port_conflict"):
        port = ingress["port"]
        return {
            "ok": False,
            "name": "edge",
            "error": f"端口 {ingress['bind']}:{port} 已被其它进程占用（不是内置 Edge）",
            "hint": f"lsof -nP -iTCP:{port} -sTCP:LISTEN",
        }
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
    port = int(gateway.get("openai_proxy_port") or gateway.get("litellm_port") or 4000)
    body = model_list.rstrip() + "\n\n# runtime listen hint (start flags also set port)\n"
    body += f"# proxy_port: {port}\n"
    # Admin UI / 虚拟密钥依赖 master_key；由控制面 secrets 注入环境变量。
    if read_litellm_master_key():
        body += (
            "\ngeneral_settings:\n"
            "  master_key: os.environ/LITELLM_MASTER_KEY\n"
        )
    LITELLM_RUNTIME.write_text(body, encoding="utf-8")
    return LITELLM_RUNTIME


def _start_litellm(gateway: dict) -> dict:
    port = int(gateway.get("openai_proxy_port") or gateway.get("litellm_port") or 4000)
    existing = _read_pid(LITELLM_PID)
    existing_owned = bool(existing and pid_matches(existing, ("litellm",)))
    listen_before = _port_open("127.0.0.1", port)
    probe = _litellm_probe(gateway)
    master_ok = bool(read_litellm_master_key())

    def finish(result: dict) -> dict:
        _record_litellm_start(result)
        return result

    if existing and existing_owned and listen_before:
        return finish(
            {
                "ok": True,
                "name": "litellm",
                "already": True,
                "pid": existing,
                "port": port,
                "phase": "running",
                "listen_ok": True,
                "pid_owned": True,
                "master_key_configured": master_ok,
            }
        )
    if existing and not existing_owned:
        # pidfile 残留 / PID 复用：只清理文件，绝不 kill 陌生进程。
        if LITELLM_PID.is_file():
            LITELLM_PID.unlink()
    elif existing and existing_owned and not listen_before:
        # 半截启动（例如外部脚本卡在 pip）：清掉再起，避免假 already。
        terminate_pid(existing, expected=("litellm",), group=True)
        if LITELLM_PID.is_file():
            LITELLM_PID.unlink()

    if listen_before:
        # 端口已有服务，但不是本工具 pidfile 里记录的进程：不要重复拉起。
        phase = "unmanaged" if probe.get("ready") else "port_conflict"
        return finish(
            {
                "ok": phase == "unmanaged",
                "name": "litellm",
                "already": True,
                "port": port,
                "phase": phase,
                "listen_ok": True,
                "pid_owned": False,
                "error": None
                if phase == "unmanaged"
                else f"端口 :{port} 已被其它进程占用，未启动 LiteLLM",
                "hint": f"lsof -nP -iTCP:{port} -sTCP:LISTEN 查看占用者",
            }
        )

    # Ensure fragments exist.
    if not (OUT_DIR / "litellm-model-list.yaml").is_file():
        write_artifacts(build_payload())
    runtime = _write_litellm_runtime(gateway)
    proxy_dir_raw = gateway.get("openai_proxy_dir") or gateway.get("litellm_dir")
    proxy_dir = Path(expand_path(str(proxy_dir_raw))) if proxy_dir_raw else None

    # 有二进制则永远直接拉起；有 openai_proxy_dir 不再优先外部自装脚本。
    litellm_bin = _resolve_litellm_bin(gateway)
    start_sh = proxy_dir / "start_litellm.sh" if proxy_dir else None

    STATE_DIR.mkdir(parents=True, exist_ok=True)
    env = _litellm_path_env(os.environ)
    master_key = read_litellm_master_key()
    if master_key:
        env["LITELLM_MASTER_KEY"] = master_key
    # Admin UI / 虚拟密钥依赖 Postgres；未接库时不注入 DATABASE_URL，
    # 只保留 Master Key 代理能力（LiteLLM 用错误 Key 时会报 Not connected to DB）。
    db_url, db_source = _litellm_db_url(gateway)
    db_ok = bool(db_url) and db_url.startswith(("postgres://", "postgresql://"))
    db_mode = str(gateway.get("litellm_database_mode") or "").strip().lower()
    # 只有显式 embedded 才由 gateway 拉起库；避免把 127.0.0.1:5433 上的外部 Postgres 误接管。
    if db_mode == "embedded":
        try:
            maybe_start_embedded_db(gateway)
        except (RuntimeError, OSError) as exc:
            return finish(
                {
                    "ok": False,
                    "name": "litellm",
                    "error": f"嵌入式数据库未就绪：{exc}",
                    "hint": "gpu-control gateway litellm-db init（或在配置里改为外部 DATABASE_URL）",
                    "phase": "db_unavailable",
                    "port": port,
                }
            )
    if db_ok:
        env[DATABASE_URL_ENV] = db_url
        # 允许控制面 / Admin 通过 /model/new 增加持久化模型别名。
        env.setdefault("STORE_MODEL_IN_DB", "True")
    else:
        env.pop(DATABASE_URL_ENV, None)
    # LiteLLM model_list 用 os.environ/<auth_env> 引用上游推理密钥；启动时注入，
    # 避免依赖操作者当前 shell 是否 export。
    cluster_doc = _load_cluster()
    api = cluster_doc.get("api") or {}
    auth_env = str(api.get("auth_env") or "INFERENCE_API_KEY").strip() or "INFERENCE_API_KEY"
    key_file = api.get("api_key_file")
    if key_file and auth_env not in env:
        key_path = Path(expand_path(str(key_file)))
        if key_path.is_file():
            try:
                env[auth_env] = key_path.read_text(encoding="utf-8").strip()
            except OSError:
                pass
    mode = "binary"
    if litellm_bin:
        cmd = [litellm_bin, "--config", str(runtime), "--port", str(port), "--host", "0.0.0.0"]
        cwd = PROJECT_DIR
    elif start_sh and start_sh.is_file():
        mode = "script"
        if proxy_dir is not None:
            frag_dst = proxy_dir / "gpu-control-model-list.yaml"
            shutil.copy2(OUT_DIR / "litellm-model-list.yaml", frag_dst)
        cmd = ["bash", str(start_sh)]
        cwd = proxy_dir or PROJECT_DIR
        env["LITELLM_CONFIG"] = str(runtime)
        env["LITELLM_CONFIG_PATH"] = str(runtime)
        env["LITELLM_PORT"] = str(port)
    else:
        return finish(
            {
                "ok": False,
                "name": "litellm",
                "error": "litellm not installed and openai_proxy_dir/start_litellm.sh missing",
                "hint": "安装 litellm[proxy] 到 PATH/~/.local/bin，或设 gateway.litellm_bin；勿依赖启停路径 pip",
                "runtime_config": str(runtime),
                "phase": "missing",
                "port": port,
                "mode": "missing",
            }
        )

    with LITELLM_LOG.open("a", encoding="utf-8") as log:
        log.write(f"\n# gpu-control start mode={mode} bin={litellm_bin or start_sh}\n")
        proc = subprocess.Popen(
            cmd,
            cwd=cwd,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            env=env,
        )
    LITELLM_PID.write_text(f"{proc.pid}\n", encoding="utf-8")
    # 给进程一点时间绑定端口；仍可能 starting。
    listen_ok = False
    for _ in range(15):
        time.sleep(0.2)
        if not _pid_alive(proc.pid):
            break
        if _port_open("127.0.0.1", port):
            listen_ok = True
            break
    alive = _pid_alive(proc.pid)
    phase = "running" if listen_ok else ("starting" if alive else "failed")
    result = {
        "ok": alive or listen_ok,
        "name": "litellm",
        "pid": proc.pid,
        "port": port,
        "mode": mode,
        "binary": litellm_bin,
        "phase": phase,
        "listen_ok": listen_ok,
        "pid_owned": alive,
        "config": str(runtime),
        "log": str(LITELLM_LOG),
        "master_key_configured": bool(master_key),
        "database_configured": db_ok,
        "database_source": db_source if db_ok else None,
        "database_mode": db_mode or ("embedded" if _is_embedded_db_url(db_url) else "external"),
        "admin_ui": db_ok,
        "error": None if (alive or listen_ok) else "process exited — see ingress-litellm.log",
        "hint": None
        if master_key
        else "未配置 Master Key：统一入口页可生成；没有数据库时 Admin UI 不可登录",
    }
    return finish(result)


def restart_litellm(gateway: dict | None = None) -> dict:
    """停掉再拉起 LiteLLM，使新 Master Key 生效。"""
    gateway = gateway if gateway is not None else _gateway_cfg()
    stopped = _stop_pidfile(LITELLM_PID, "litellm", expected=("litellm",))
    started = _start_litellm(gateway)
    return {"ok": bool(started.get("ok")), "stopped": stopped, "started": started}

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
        results.append(_stop_pidfile(EDGE_PID, "edge", expected=("ingress_edge.py",)))
        if EDGE_STATUS.is_file():
            EDGE_STATUS.unlink()
    if kind in {"litellm", "both", "all"}:
        results.append(_stop_pidfile(LITELLM_PID, "litellm", expected=("litellm",)))
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

    p_db = sub.add_parser("litellm-db", help="LiteLLM 管理数据库（嵌入式 Postgres）")
    p_db.add_argument(
        "db_action",
        nargs="?",
        default="status",
        choices=["status", "init", "start", "stop", "url", "apply-schema"],
        help="默认 status",
    )
    p_db.add_argument("--reveal", action="store_true", help="url 动作时输出完整连接串")
    p_db.set_defaults(func=cmd_litellm_db)

    args = parser.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
