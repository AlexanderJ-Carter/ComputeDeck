"""Resolve registered jobs and the cluster default job name."""

from __future__ import annotations

import json

from paths import cluster_file, list_job_files


def registered_job_names() -> list[str]:
    return [path.stem for path in list_job_files()]


def default_job_name(fallback: str | None = None) -> str | None:
    """Prefer cluster.default_job when registered; else first job file."""
    names = registered_job_names()
    if not names:
        return fallback
    path = cluster_file()
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            data = {}
        preferred = (data.get("default_job") or "").strip()
        if preferred and preferred in names:
            return preferred
        watchdog_jobs = ((data.get("watchdog") or {}).get("jobs")) or []
        for name in watchdog_jobs:
            if name in names:
                return name
    return names[0]
