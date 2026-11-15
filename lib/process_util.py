"""跨平台进程工具：判活、命令行识别、安全终止。

控制面支持 Linux / macOS，但历史实现只读 ``/proc``，在 macOS 上会把
所有本地 pid 都当成“已退出”。这里统一用 ``os.kill(pid, 0)`` 判活，
再按平台读取命令行做身份校验，避免 pidfile 残留 / PID 复用时误杀。
"""

from __future__ import annotations

import os
import signal
import subprocess
import time
from pathlib import Path
from typing import Iterable


def _pid_is_zombie(pid: int) -> bool:
    """已退出但未被父进程回收的僵尸进程不算“在跑”。"""
    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
        tail = stat.rsplit(")", 1)[1].split()
        if tail and tail[0] == "Z":
            return True
    except (OSError, IndexError):
        pass
    try:
        completed = subprocess.run(
            ["ps", "-p", str(pid), "-o", "stat="],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return completed.returncode == 0 and completed.stdout.strip().upper().startswith("Z")


def pid_alive(pid: int | None) -> bool:
    """进程是否存在且可被当前用户探测。

    :param pid: 进程号；``None`` / 非正数返回 ``False``。
    :returns: 存在为 ``True``；``PermissionError`` 视为存在（只是无权操作）。
    """
    try:
        value = int(pid or 0)
    except (TypeError, ValueError):
        return False
    if value <= 0:
        return False
    try:
        os.kill(value, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return not _pid_is_zombie(value)


def pid_cmdline(pid: int | None) -> str:
    """读取进程命令行；Linux 走 ``/proc``，macOS/BSD 回退 ``ps``。

    :param pid: 进程号。
    :returns: 单行命令行；读取失败返回空串。
    """
    try:
        value = int(pid or 0)
    except (TypeError, ValueError):
        return ""
    if value <= 0:
        return ""
    try:
        raw = Path(f"/proc/{value}/cmdline").read_bytes()
        if raw:
            return raw.decode("utf-8", "replace").replace("\x00", " ").strip()
    except OSError:
        pass
    try:
        completed = subprocess.run(
            ["ps", "-p", str(value), "-o", "command="],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    if completed.returncode != 0:
        return ""
    return (completed.stdout or "").strip()


def pid_matches(pid: int | None, expected: Iterable[str]) -> bool:
    """命令行是否包含任一期望片段；用于确认 pidfile 指向的仍是自己的进程。"""
    needles = tuple(str(item) for item in expected if str(item).strip())
    if not needles:
        return True
    text = pid_cmdline(pid)
    return bool(text) and any(needle in text for needle in needles)


def _signal(pid: int, sig: int, *, group: bool) -> None:
    if group and hasattr(os, "getpgid"):
        try:
            if os.getpgid(pid) == pid:
                os.killpg(pid, sig)
                return
        except OSError:
            pass
    os.kill(pid, sig)


def terminate_pid(
    pid: int | None,
    *,
    expected: Iterable[str] = (),
    group: bool = True,
    timeout: float = 3.0,
) -> dict:
    """先 SIGTERM、超时再 SIGKILL；拒绝终止身份不匹配的 PID。

    :param pid: 进程号。
    :param expected: 命令行必须包含的片段（为空则不校验身份）。
    :param group: 允许时杀整个进程组（仅当该 pid 是组长）。
    :param timeout: SIGTERM 后的等待秒数。
    :returns: ``{"ok", "stopped", "pid", "reason"?, "cmdline"?}``。
    """
    if not pid_alive(pid):
        return {"ok": True, "stopped": False, "pid": pid, "reason": "not running"}
    assert pid is not None
    if expected and not pid_matches(pid, expected):
        return {
            "ok": False,
            "stopped": False,
            "pid": pid,
            "stale": True,
            "reason": "pid 不属于目标进程，已拒绝终止",
            "cmdline": pid_cmdline(pid),
        }
    try:
        _signal(pid, signal.SIGTERM, group=group)
    except (ProcessLookupError, PermissionError, OSError) as exc:
        return {"ok": False, "stopped": False, "pid": pid, "error": str(exc)}
    deadline = time.monotonic() + max(0.1, float(timeout))
    while time.monotonic() < deadline:
        if not pid_alive(pid):
            return {"ok": True, "stopped": True, "pid": pid}
        time.sleep(0.1)
    try:
        _signal(pid, signal.SIGKILL, group=group)
    except (ProcessLookupError, PermissionError, OSError) as exc:
        return {"ok": False, "stopped": False, "pid": pid, "error": str(exc)}
    for _ in range(20):
        if not pid_alive(pid):
            return {"ok": True, "stopped": True, "pid": pid, "killed": True}
        time.sleep(0.1)
    return {
        "ok": False,
        "stopped": False,
        "pid": pid,
        "error": "进程在 SIGKILL 后仍未退出",
    }
