#!/usr/bin/env python3
"""文档站：MkDocs Material 构建 / 预览。"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SITE_DIR = ROOT / "web" / "static" / "help"


def _mkdocs_bin() -> str:
    venv = ROOT / ".venv" / "bin" / "mkdocs"
    if venv.is_file():
        return str(venv)
    return "mkdocs"


def cmd_build(_: argparse.Namespace) -> int:
    cfg = ROOT / "mkdocs.yml"
    if not cfg.is_file():
        print("缺少 mkdocs.yml", file=sys.stderr)
        return 1
    cmd = [_mkdocs_bin(), "build", "-f", str(cfg), "-q"]
    print("+", " ".join(cmd))
    try:
        completed = subprocess.run(cmd, cwd=ROOT, check=False)
    except FileNotFoundError:
        print("未找到 mkdocs。先执行：uv pip install -r requirements-docs.txt", file=sys.stderr)
        return 1
    if completed.returncode == 0:
        print(f"已构建 → {SITE_DIR} （UI 访问 /help/）")
    else:
        print(
            "构建失败。先安装：uv pip install -r requirements-docs.txt",
            file=sys.stderr,
        )
    return int(completed.returncode)


def cmd_serve(args: argparse.Namespace) -> int:
    cmd = [
        _mkdocs_bin(),
        "serve",
        "-f",
        str(ROOT / "mkdocs.yml"),
        "-a",
        f"{args.host}:{args.port}",
    ]
    print("+", " ".join(cmd))
    try:
        return int(subprocess.call(cmd, cwd=ROOT))
    except FileNotFoundError:
        print("未找到 mkdocs。先执行：uv pip install -r requirements-docs.txt", file=sys.stderr)
        return 1


def cmd_clean(_: argparse.Namespace) -> int:
    if SITE_DIR.is_dir():
        shutil.rmtree(SITE_DIR)
        print(f"已删除 {SITE_DIR}")
    else:
        print("无需清理")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    p_build = sub.add_parser("build", help="构建到 web/static/help/")
    p_build.set_defaults(func=cmd_build)
    p_serve = sub.add_parser("serve", help="本地预览（默认 127.0.0.1:8000）")
    p_serve.add_argument("--host", default="127.0.0.1")
    p_serve.add_argument("--port", type=int, default=8000)
    p_serve.set_defaults(func=cmd_serve)
    p_clean = sub.add_parser("clean", help="删除构建产物")
    p_clean.set_defaults(func=cmd_clean)
    args = parser.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
