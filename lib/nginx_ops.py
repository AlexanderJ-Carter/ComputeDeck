"""生成后的 nginx 配置：安全写入、语法检查、reload 与回滚。

控制面只负责渲染配置；这个模块补上“渲染之后”的一步，避免
``state/gateway/nginx-inference-pool.conf`` 和系统 nginx 实际加载的配置长期不一致。
"""

from __future__ import annotations

import hashlib
import os
import shlex
import subprocess
import tempfile
import time
from pathlib import Path

DEFAULT_TEST_CMD = "nginx -t"
DEFAULT_RELOAD_CMD = "nginx -s reload"

BACKUP_KEEP = 5


def normalize_settings(gateway: dict | None) -> dict:
    """读取 nginx 应用配置，兼容 ``gateway.nginx.*`` 与旧的平铺键。"""
    gateway = gateway if isinstance(gateway, dict) else {}
    raw = gateway.get("nginx") if isinstance(gateway.get("nginx"), dict) else {}

    def pick(*names, default=None):
        for name in names:
            if name in raw and raw[name] not in (None, ""):
                return raw[name]
            if name in gateway and gateway[name] not in (None, ""):
                return gateway[name]
        return default

    try:
        interval = int(pick("sync_interval_sec", "nginx_sync_interval_sec", default=15) or 15)
    except (TypeError, ValueError):
        interval = 15
    return {
        "apply_path": str(pick("apply_path", "conf_path", "nginx_apply_path", default="") or "").strip(),
        "ui_url": str(pick("ui_url", "nginx_ui_url", "nginxui_url", default="") or "").strip(),
        "test_cmd": str(pick("test_cmd", "nginx_test_cmd", default=DEFAULT_TEST_CMD) or "").strip(),
        "reload_cmd": str(pick("reload_cmd", "nginx_reload_cmd", default=DEFAULT_RELOAD_CMD) or "").strip(),
        "auto_sync": _truthy(pick("auto_sync", "nginx_auto_sync", default=False)),
        "interval_sec": max(5, interval),
        "backup": _truthy(pick("backup", "nginx_backup", default=True)),
        "timeout_sec": 30,
    }


def _truthy(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def normalize_text(text: str) -> str:
    value = str(text or "")
    return value if not value or value.endswith("\n") else value + "\n"


def sha256_text(text: str) -> str:
    return hashlib.sha256(normalize_text(text).encode("utf-8")).hexdigest()


def file_sha256(path: Path | str) -> str | None:
    target = Path(path)
    try:
        return hashlib.sha256(target.read_bytes()).hexdigest()
    except OSError:
        return None


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        mode = path.stat().st_mode & 0o777
    except OSError:
        mode = 0o644
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _backup(path: Path) -> Path | None:
    if not path.is_file():
        return None
    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup = path.with_name(f"{path.name}.bak-{stamp}")
    try:
        backup.write_bytes(path.read_bytes())
    except OSError:
        return None
    _prune_backups(path)
    return backup


def _prune_backups(path: Path) -> None:
    backups = sorted(path.parent.glob(f"{path.name}.bak-*"), key=lambda p: p.stat().st_mtime)
    for old in backups[:-BACKUP_KEEP]:
        try:
            old.unlink()
        except OSError:
            pass


def run_command(command: str, *, timeout: int = 30) -> dict:
    """按 shell 词法拆分后直接执行，不经过 shell。"""
    argv = shlex.split(str(command or ""))
    if not argv:
        return {"ok": False, "command": command, "error": "命令为空"}
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        return {"ok": False, "command": command, "error": f"找不到命令：{argv[0]}"}
    except subprocess.TimeoutExpired:
        return {"ok": False, "command": command, "error": f"命令超时（{timeout}s）"}
    return {
        "ok": proc.returncode == 0,
        "command": command,
        "returncode": proc.returncode,
        "stdout": (proc.stdout or "").strip()[-2000:],
        "stderr": (proc.stderr or "").strip()[-2000:],
    }


def apply_text(
    target: Path | str,
    text: str,
    settings: dict,
    *,
    dry_run: bool = False,
) -> dict:
    """把渲染结果写到 target；校验失败时恢复旧配置。

    ``target`` 必须是 nginx 主配置已经 include 的文件。测试与 reload 命令
    可以在 ``gateway.nginx.test_cmd`` / ``reload_cmd`` 里覆盖（例如加 sudo）。
    """
    path = Path(target).expanduser()
    text = normalize_text(text)
    digest = sha256_text(text)
    previous = _read_text(path)
    previous_hash = sha256_text(previous) if previous is not None else None
    result = {
        "ok": True,
        "path": str(path),
        "changed": previous != text,
        "applied": False,
        "reloaded": False,
        "dry_run": bool(dry_run),
        "sha256": digest,
        "previous_sha256": previous_hash,
        "backup": None,
        "test": None,
        "reload": None,
        "error": None,
        "rolled_back": False,
    }
    if not result["changed"]:
        return result
    if dry_run:
        return result

    backup = _backup(path) if settings.get("backup", True) else None
    result["backup"] = str(backup) if backup else None
    _atomic_write(path, text)

    test_cmd = str(settings.get("test_cmd") or "").strip()
    if test_cmd:
        test = run_command(test_cmd, timeout=int(settings.get("timeout_sec") or 30))
        result["test"] = test
        if not test.get("ok"):
            if previous is None:
                try:
                    path.unlink()
                except OSError:
                    pass
            else:
                _atomic_write(path, previous)
            result.update(
                ok=False,
                applied=False,
                rolled_back=True,
                error=f"nginx 配置校验失败：{test.get('stderr') or test.get('error') or '未知错误'}",
            )
            return result

    reload_cmd = str(settings.get("reload_cmd") or "").strip()
    if reload_cmd:
        reload_result = run_command(reload_cmd, timeout=int(settings.get("timeout_sec") or 30))
        result["reload"] = reload_result
        if not reload_result.get("ok"):
            result.update(
                ok=False,
                applied=True,
                reloaded=False,
                error=f"配置已写入但 reload 失败：{reload_result.get('stderr') or reload_result.get('error')}",
            )
            return result
        result["reloaded"] = True

    result["applied"] = True
    return result


def status_for(path: Path | str, expected_text: str | None = None) -> dict:
    """比较磁盘上的配置与当前渲染结果，供 gateway status 使用。"""
    target = Path(path).expanduser()
    applied_hash = file_sha256(target)
    expected_hash = sha256_text(expected_text) if expected_text is not None else None
    return {
        "path": str(target),
        "exists": applied_hash is not None,
        "applied_hash": applied_hash,
        "expected_hash": expected_hash,
        "in_sync": bool(applied_hash and expected_hash and applied_hash == expected_hash),
    }
