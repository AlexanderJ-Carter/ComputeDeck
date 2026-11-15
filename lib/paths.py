"""项目路径解析与配置查找。

查找顺序：``config/local/<name>`` → ``config/<name>`` → 仓库根 ``<name>``。
供 CLI、脚本与 ``lib.*`` 统一引用。
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
    """组装经 venv Python 调用的 ansible-playbook 命令行（避免 shebang 路径失效）。"""
    return [str(VENV_PYTHON), str(ANSIBLE), *args]


def ensure_runtime_dirs() -> None:
    """创建 local / jobs / state / snapshots 运行目录。"""
    LOCAL_DIR.mkdir(parents=True, exist_ok=True)
    LOCAL_JOBS_DIR.mkdir(parents=True, exist_ok=True)
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)


def resolve_config(name: str, *, write: bool = False) -> Path:
    """解析配置文件路径，优先 local 覆盖。

    :param name: 文件名，如 ``cluster.json``。
    :param write: 为真且文件不存在时，返回将写入的 ``config/local/`` 路径。
    :returns: 已存在路径，或按 ``write`` 决定的落点。
    """
    candidates = [LOCAL_DIR / name, CONFIG_DIR / name, PROJECT_DIR / name]
    for path in candidates:
        if path.exists():
            return path
    if write:
        LOCAL_DIR.mkdir(parents=True, exist_ok=True)
        return LOCAL_DIR / name
    return candidates[0]


def cluster_file(*, write: bool = False) -> Path:
    """``cluster.json`` 路径。"""
    return resolve_config("cluster.json", write=write)


def nodes_file(*, write: bool = False) -> Path:
    """``nodes.json`` 路径。"""
    return resolve_config("nodes.json", write=write)


def job_file(name: str) -> Path | None:
    """按 stem 查找 Job JSON（先 local 后内置）。

    :param name: Job 名（不含 ``.json``）。
    :returns: 文件路径；未找到为 ``None``。
    """
    for folder in (LOCAL_JOBS_DIR, JOBS_DIR):
        path = folder / f"{name}.json"
        if path.is_file():
            return path
    return None


def list_job_files() -> list[Path]:
    """列出已注册 Job 文件；同名时 local 覆盖内置。"""
    found: dict[str, Path] = {}
    for folder in (JOBS_DIR, LOCAL_JOBS_DIR):
        if not folder.is_dir():
            continue
        for path in sorted(folder.glob("*.json")):
            name = path.name
            # 跳过 macOS AppleDouble（._*.json）与隐藏文件
            if name.startswith(".") or name.endswith(".example.json"):
                continue
            found[path.stem] = path
    return [found[k] for k in sorted(found)]


def expand_path(value: str) -> str:
    """展开配置路径中的 ``$VAR`` 与 ``~``。"""
    return os.path.expanduser(os.path.expandvars(value))
