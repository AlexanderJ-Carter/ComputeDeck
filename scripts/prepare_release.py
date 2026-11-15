#!/usr/bin/env python3
"""准备一次控制面发版（版本号 · CHANGELOG · 校验），不推送远程。

用法::

    # 预览：把 Unreleased 落到下一个 patch
    python scripts/prepare_release.py --bump patch

    # 写入 VERSION / pyproject / CHANGELOG，并跑 check.sh
    python scripts/prepare_release.py --bump minor --apply --check

    # 指定版本
    python scripts/prepare_release.py --version 0.22.0 --apply

发版主产物是 GHCR 镜像 + GitHub Release；不发 PyPI / npm。
"""

from __future__ import annotations

import argparse
import datetime as dt
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHANGELOG = ROOT / "CHANGELOG.md"
VERSION_FILE = ROOT / "VERSION"
PYPROJECT = ROOT / "pyproject.toml"

_SEMVER = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")
# 正式版 X.Y.Z，或先导测试版 X.Y.Z-beta.N / -rc.N
_VERSION = re.compile(
    r"^(\d+)\.(\d+)\.(\d+)(?:-(?:alpha|beta|rc)\.\d+)?$"
)
_UNRELEASED = re.compile(
    r"^## \[Unreleased\]\s*\n(?P<body>.*?)(?=^## |\Z)",
    re.M | re.S,
)
_PY_VERSION = re.compile(r'^version\s*=\s*"[^"]*"', re.M)


def _is_prerelease(version: str) -> bool:
    return "-" in version


def _read_version() -> str:
    return VERSION_FILE.read_text(encoding="utf-8").strip()


def _bump(version: str, kind: str) -> str:
    match = _SEMVER.match(version)
    if not match:
        raise SystemExit(f"VERSION 不是 X.Y.Z: {version!r}")
    major, minor, patch = (int(p) for p in match.groups())
    if kind == "major":
        return f"{major + 1}.0.0"
    if kind == "minor":
        return f"{major}.{minor + 1}.0"
    if kind == "patch":
        return f"{major}.{minor}.{patch + 1}"
    raise SystemExit(f"未知 bump: {kind}")


def _anchor_slug(version: str, date: str) -> str:
    """Keep a Changelog 本地锚点（与历史条目风格一致）。"""
    compact = version.replace(".", "")
    return f"#{compact}---{date}"


def _upsert_changelog_anchor(text: str, version: str, date: str) -> str:
    """在文末版本锚点表中插入或更新 ``[version]``。"""
    line = f"[{version}]: {_anchor_slug(version, date)}"
    pattern = re.compile(rf"^\[{re.escape(version)}\]:\s*.*$", re.M)
    if pattern.search(text):
        return pattern.sub(line, text, count=1)
    marker = "[Unreleased]: #unreleased"
    if marker in text:
        return text.replace(marker, marker + "\n" + line, 1)
    return text.rstrip() + "\n\n" + line + "\n"


def _fold_changelog(text: str, version: str, date: str) -> str:
    match = _UNRELEASED.search(text)
    if not match:
        raise SystemExit("CHANGELOG.md 缺少 ## [Unreleased]")
    body = match.group("body").strip()
    if not body:
        raise SystemExit("Unreleased 为空，没有可发布内容")
    if re.search(rf"^## \[{re.escape(version)}\]", text, re.M):
        raise SystemExit(f"CHANGELOG 已有 ## [{version}]")
    stub = (
        "## [Unreleased]\n\n"
        "---\n\n"
        f"## [{version}] - {date}\n\n"
        f"{body}\n"
    )
    folded = _UNRELEASED.sub(stub, text, count=1)
    return _upsert_changelog_anchor(folded, version, date)


def _write_version_files(version: str) -> None:
    VERSION_FILE.write_text(version + "\n", encoding="utf-8")
    py = PYPROJECT.read_text(encoding="utf-8")
    if not _PY_VERSION.search(py):
        raise SystemExit("pyproject.toml 缺少 version")
    PYPROJECT.write_text(_PY_VERSION.sub(f'version = "{version}"', py, count=1), encoding="utf-8")


def _run_checks(version: str) -> None:
    py = ROOT / ".venv" / "bin" / "python"
    python = str(py) if py.is_file() else sys.executable
    subprocess.run(
        [python, str(ROOT / "scripts" / "release_notes.py"), "--check", version],
        cwd=ROOT,
        check=True,
    )
    subprocess.run(["bash", str(ROOT / "scripts" / "check.sh")], cwd=ROOT, check=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--version", help="目标版本 X.Y.Z")
    group.add_argument(
        "--bump",
        choices=("major", "minor", "patch"),
        help="相对当前 VERSION 递增",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="写入文件；默认只打印预览",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="apply 后跑 release_notes --check 与 check.sh",
    )
    parser.add_argument(
        "--date",
        default=dt.date.today().isoformat(),
        help="CHANGELOG 日期，默认今天",
    )
    args = parser.parse_args()

    current = _read_version()
    # bump 基于正式版核心；若当前已是预发布，取其 X.Y.Z 再递增
    core = current.split("-", 1)[0]
    target = args.version.lstrip("v") if args.version else _bump(core, args.bump)
    if not _VERSION.match(target):
        raise SystemExit(
            f"目标版本须为 X.Y.Z 或 X.Y.Z-beta.N / -rc.N: {target!r}"
        )

    old_changelog = CHANGELOG.read_text(encoding="utf-8")
    new_changelog = _fold_changelog(old_changelog, target, args.date)

    print(f"当前 VERSION: {current}")
    print(f"目标版本:     {target}")
    if _is_prerelease(target):
        print("类型:         先导测试版（GitHub Release 将标 prerelease，不刷 latest）")
    print(f"CHANGELOG:    Unreleased → [{target}] - {args.date}")
    if not args.apply:
        print("\n预览模式（未写入）。确认后加 --apply；校验加 --check。")
        print("主产物：GitHub Release + ghcr.io/alexanderj-carter/computedeck")
        print("不发 PyPI / npm。首次镜像需在 GitHub Packages 设为 Public。")
        return

    CHANGELOG.write_text(new_changelog, encoding="utf-8")
    _write_version_files(target)
    print("已写入: VERSION · pyproject.toml · CHANGELOG.md")

    if args.check:
        _run_checks(target)
        print("校验通过。")

    print(
        "\n下一步（需你明确允许才 push）:\n"
        f"  git add -A && git commit -m 'chore: release {target}'\n"
        f"  git tag -a v{target} -m 'v{target}'\n"
        f"  git push origin main && git push origin v{target}\n"
        "  # Actions Release 通过后：Packages → computedeck → Package settings → Public\n"
        f"  docker pull ghcr.io/alexanderj-carter/computedeck:{target}"
    )


if __name__ == "__main__":
    main()
