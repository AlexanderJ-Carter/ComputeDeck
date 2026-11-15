"""原子 JSON 状态读写与跨进程 advisory flock。

UI / CLI / watchdog 并发写 leases、queue、node_state；写走临时文件 + ``os.replace``，
读改写路径加 ``flock``。仅支持 POSIX（Linux / macOS）。
"""

from __future__ import annotations

import contextlib
import copy
import fcntl
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Iterator


def atomic_write_text(path: Path, text: str, *, mode: int = 0o644) -> None:
    """原子写入文本（临时文件 + ``os.replace``）。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    finally:
        with contextlib.suppress(OSError):
            os.unlink(tmp)


def atomic_write_json(path: Path, data: Any, *, indent: int = 2, mode: int = 0o644) -> None:
    """原子写入 JSON 文档。"""
    atomic_write_text(path, json.dumps(data, ensure_ascii=False, indent=indent) + "\n", mode=mode)


def read_json(path: Path, default: Any = None) -> Any:
    """读 JSON；损坏则旁路备份坏文件并返回 ``default`` 的深拷贝。

    :param path: 目标文件。
    :param default: 缺失或损坏时的回落值。
    """
    path = Path(path)
    if not path.is_file():
        return copy.deepcopy(default)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        bad = path.with_name(f"{path.name}.corrupt-{int(time.time())}")
        with contextlib.suppress(OSError):
            path.replace(bad)
        print(f"[state] {path} 不可读 ({exc})；已移至 {bad.name}", file=sys.stderr)
        return copy.deepcopy(default)


@contextlib.contextmanager
def file_lock(path: Path, *, timeout: float = 15.0) -> Iterator[None]:
    """在 *path* 旁建立排他 advisory 锁（``<name>.lock``）。

    :param timeout: 获取锁超时秒数。
    :raises TimeoutError: 超时仍未拿到锁。
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_name(path.name + ".lock")
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o644)
    deadline = time.monotonic() + max(0.1, timeout)
    try:
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"lock timeout: {lock_path}")
                time.sleep(0.05)
        yield
    finally:
        with contextlib.suppress(OSError):
            fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def update_json(
    path: Path,
    mutator,
    *,
    default: Any = None,
    indent: int = 2,
    timeout: float = 15.0,
) -> Any:
    """跨进程锁下的原子读改写。

    :param mutator: 接收当前数据并返回新数据；返回 ``None`` 表示就地修改。
    :returns: 最终写入的数据对象。
    """
    path = Path(path)
    with file_lock(path, timeout=timeout):
        data = read_json(path, default)
        result = mutator(data)
        atomic_write_json(path, data if result is None else result, indent=indent)
        return data if result is None else result
