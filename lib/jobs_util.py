"""已注册 Job、默认任务与可安装模板。"""

from __future__ import annotations

import json
from pathlib import Path

from paths import (
    CONFIG_DIR,
    JOBS_DIR,
    LOCAL_JOBS_DIR,
    PROJECT_DIR,
    cluster_file,
    list_job_files,
)


EXAMPLES_DIR = CONFIG_DIR / "examples"


def registered_job_names() -> list[str]:
    return [path.stem for path in list_job_files()]


def job_source(path: Path) -> str:
    try:
        path.resolve().relative_to(LOCAL_JOBS_DIR.resolve())
        return "local"
    except ValueError:
        pass
    try:
        path.resolve().relative_to(JOBS_DIR.resolve())
        return "builtin"
    except ValueError:
        return "other"


def load_job_summaries() -> list[dict]:
    rows = []
    for path in list_job_files():
        job = json.loads(path.read_text(encoding="utf-8"))
        name = job.get("name") or path.stem
        rows.append({
            "name": name,
            "description": job.get("description") or "",
            "kind": job.get("kind") or "service",
            "gpus_per_node": job.get("gpus_per_node"),
            "distributed": bool(job.get("distributed")),
            "runtime": job.get("runtime") or "process",
            "model": job.get("model") or "",
            "python": job.get("python") or "",
            "script": job.get("script") or "",
            "extra_args": job.get("extra_args") or [],
            "docker": job.get("docker") or None,
            "actions": job.get("actions") or ["start", "stop", "restart", "status", "wait"],
            "path": str(path),
            "source": job_source(path),
        })
    return rows


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
    # 默认优先通用壳；业务 Job 在 local，不假定 http-serve/train。
    if "shell-job" in names:
        return "shell-job"
    return names[0]


def _template_stem(path: Path) -> str:
    name = path.name
    if name.endswith(".example.json"):
        return name[: -len(".example.json")]
    if name.endswith(".json"):
        return path.stem
    return path.stem


def list_job_templates() -> list[dict]:
    """Templates from config/examples/*job* and *.example.json that look like jobs."""
    registered = set(registered_job_names())
    found: dict[str, Path] = {}
    if EXAMPLES_DIR.is_dir():
        for path in sorted(EXAMPLES_DIR.glob("*.json")):
            stem = _template_stem(path)
            # Skip non-job examples.
            if stem in {"cluster", "nodes", "models", "mcp.cursor"} or stem.startswith("mcp"):
                continue
            if stem.endswith(".cursor"):
                continue
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue
            if not isinstance(data, dict):
                continue
            if "script" not in data and "docker" not in data and "runtime" not in data:
                continue
            found[stem] = path
    # Also expose builtins as templates for copying into local.
    if JOBS_DIR.is_dir():
        for path in sorted(JOBS_DIR.glob("*.json")):
            found.setdefault(path.stem, path)

    rows = []
    for stem, path in sorted(found.items()):
        data = json.loads(path.read_text(encoding="utf-8"))
        rows.append({
            "id": stem,
            "name": data.get("name") or stem,
            "description": data.get("description") or "",
            "gpus_per_node": data.get("gpus_per_node"),
            "runtime": data.get("runtime") or "process",
            "path": str(path),
            "installed": stem in registered or (data.get("name") or stem) in registered,
        })
    return rows


def install_job_template(template_id: str, *, name: str | None = None) -> dict:
    """Copy a template into config/local/jobs/<name>.json."""
    templates = {t["id"]: t for t in list_job_templates()}
    if template_id not in templates:
        raise FileNotFoundError(f"unknown template: {template_id}")
    src = Path(templates[template_id]["path"])
    data = json.loads(src.read_text(encoding="utf-8"))
    dest_name = (name or data.get("name") or template_id).strip()
    if not dest_name:
        raise ValueError("job name required")
    data["name"] = dest_name
    LOCAL_JOBS_DIR.mkdir(parents=True, exist_ok=True)
    dest = LOCAL_JOBS_DIR / f"{dest_name}.json"
    dest.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {
        "ok": True,
        "name": dest_name,
        "path": str(dest),
        "from": str(src),
        "relative": str(dest.relative_to(PROJECT_DIR)),
    }


def ensure_builtin_jobs_visible() -> None:
    """No-op helper for docs; builtins live in jobs/ and are listed automatically."""
    JOBS_DIR.mkdir(parents=True, exist_ok=True)
