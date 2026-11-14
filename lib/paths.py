"""Project path resolution for GitHub-friendly layout.

Lookup order for configs:
1. ``config/local/<name>``  (machine-specific, gitignored)
2. ``config/<name>``
3. repo root ``<name>``      (legacy / symlink compat)
"""

from __future__ import annotations

import os
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
CONFIG_DIR = PROJECT_DIR / "config"
LOCAL_DIR = CONFIG_DIR / "local"
STATE_DIR = PROJECT_DIR / "state"
JOBS_DIR = PROJECT_DIR / "jobs"
LOCAL_JOBS_DIR = LOCAL_DIR / "jobs"
PLAYBOOKS_DIR = PROJECT_DIR / "playbooks"
SCRIPTS_DIR = PROJECT_DIR / "scripts"
WEB_DIR = PROJECT_DIR / "web"
BIN_DIR = PROJECT_DIR / "bin"
SNAPSHOT_DIR = PROJECT_DIR / "snapshots"
VERSION_FILE = PROJECT_DIR / "VERSION"
INVENTORY_FILE = PROJECT_DIR / "inventory.ini"
RESOURCES_FILE = PROJECT_DIR / "resources.json"
VENV_DIR = PROJECT_DIR / ".venv"
VENV_PYTHON = VENV_DIR / "bin" / "python"
ANSIBLE = VENV_DIR / "bin" / "ansible-playbook"
CLI = BIN_DIR / "gpu-control"


def ansible_cmd(*args: str) -> list[str]:
    """Run ansible-playbook via venv python (avoids broken shebang paths)."""
    return [str(VENV_PYTHON), str(ANSIBLE), *args]


def ensure_runtime_dirs() -> None:
    LOCAL_DIR.mkdir(parents=True, exist_ok=True)
    LOCAL_JOBS_DIR.mkdir(parents=True, exist_ok=True)
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)


def resolve_config(name: str, *, write: bool = False) -> Path:
    """Resolve a config filename. Prefer local overrides."""
    candidates = [LOCAL_DIR / name, CONFIG_DIR / name, PROJECT_DIR / name]
    for path in candidates:
        if path.exists():
            return path
    if write:
        LOCAL_DIR.mkdir(parents=True, exist_ok=True)
        return LOCAL_DIR / name
    return candidates[0]


def cluster_file(*, write: bool = False) -> Path:
    return resolve_config("cluster.json", write=write)


def nodes_file(*, write: bool = False) -> Path:
    return resolve_config("nodes.json", write=write)


def job_file(name: str) -> Path | None:
    """Find job JSON by stem name."""
    for folder in (LOCAL_JOBS_DIR, JOBS_DIR):
        path = folder / f"{name}.json"
        if path.is_file():
            return path
    return None


def list_job_files() -> list[Path]:
    found: dict[str, Path] = {}
    for folder in (JOBS_DIR, LOCAL_JOBS_DIR):
        if not folder.is_dir():
            continue
        for path in sorted(folder.glob("*.json")):
            if path.name.endswith(".example.json"):
                continue
            found[path.stem] = path
    return [found[k] for k in sorted(found)]


def expand_path(value: str) -> str:
    """Expand ``$VAR`` and ``~`` in config path strings."""
    return os.path.expanduser(os.path.expandvars(value))
