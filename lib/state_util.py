"""Atomic JSON state helpers and advisory cross-process locks.

The control plane is used by the UI, CLI and watchdog at the same time. A
partial write of leases/queue/node_state is the difference between "one task
failed" and "the scheduler lost its state", so writes go through a temp file +
``os.replace`` and read-modify-write paths take an advisory ``flock``.
"""

from __future__ import annotations

import contextlib
import copy
import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any, Iterator

try:  # POSIX (Linux/macOS). Windows falls back to no cross-process lock.
    import fcntl
except ImportError:  # pragma: no cover - non-POSIX
    fcntl = None  # type: ignore[assignment]


def atomic_write_text(path: Path, text: str, *, mode: int = 0o644) -> None:
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
    atomic_write_text(path, json.dumps(data, ensure_ascii=False, indent=indent) + "\n", mode=mode)


def read_json(path: Path, default: Any = None) -> Any:
    """Read JSON; on corruption move the bad file aside and return a deep copy of default."""
    path = Path(path)
    if not path.is_file():
        return copy.deepcopy(default)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        bad = path.with_name(f"{path.name}.corrupt-{int(time.time())}")
        with contextlib.suppress(OSError):
            path.replace(bad)
        print(f"[state] {path} is unreadable ({exc}); moved to {bad.name}", file=__import__("sys").stderr)
        return copy.deepcopy(default)


@contextlib.contextmanager
def file_lock(path: Path, *, timeout: float = 15.0) -> Iterator[None]:
    """Advisory exclusive lock next to *path* (``<name>.lock``)."""
    if fcntl is None:
        yield
        return
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
    """Atomic read-modify-write under a cross-process lock."""
    path = Path(path)
    with file_lock(path, timeout=timeout):
        data = read_json(path, default)
        result = mutator(data)
        atomic_write_json(path, data if result is None else result, indent=indent)
        return data if result is None else result
