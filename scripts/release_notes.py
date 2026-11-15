#!/usr/bin/env python3
"""从 CHANGELOG.md 抽出指定版本的发版说明。

用法::

    python scripts/release_notes.py 0.22.0
    python scripts/release_notes.py --check 0.22.0
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHANGELOG = ROOT / "CHANGELOG.md"
VERSION_FILE = ROOT / "VERSION"
PYPROJECT = ROOT / "pyproject.toml"


def _read_pyproject_version() -> str:
    text = PYPROJECT.read_text(encoding="utf-8")
    match = re.search(r'^version\s*=\s*"([^"]+)"', text, re.M)
    if not match:
        raise SystemExit("pyproject.toml 缺少 version")
    return match.group(1)


def extract_notes(version: str) -> str:
    text = CHANGELOG.read_text(encoding="utf-8")
    pattern = re.compile(
        rf"^## \[{re.escape(version)}\][^\n]*\n(.*?)(?=^## |\Z)",
        re.M | re.S,
    )
    match = pattern.search(text)
    if not match:
        raise SystemExit(f"CHANGELOG.md 中未找到 ## [{version}]")
    body = match.group(1).strip()
    if not body:
        raise SystemExit(f"CHANGELOG.md 版本 {version} 正文为空")
    return body + "\n"


def check_versions(version: str) -> None:
    file_ver = VERSION_FILE.read_text(encoding="utf-8").strip()
    py_ver = _read_pyproject_version()
    errors: list[str] = []
    if file_ver != version:
        errors.append(f"VERSION={file_ver!r} ≠ {version!r}")
    if py_ver != version:
        errors.append(f"pyproject version={py_ver!r} ≠ {version!r}")
    extract_notes(version)
    if errors:
        raise SystemExit("版本不一致：\n- " + "\n- ".join(errors))
    print(f"ok: VERSION / pyproject / CHANGELOG 均为 {version}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("version", help="语义化版本，如 0.22.0（不要带 v）")
    parser.add_argument(
        "--check",
        action="store_true",
        help="只校验 VERSION / pyproject / CHANGELOG 是否一致",
    )
    args = parser.parse_args()
    version = args.version.lstrip("v")
    if args.check:
        check_versions(version)
        return
    sys.stdout.write(extract_notes(version))


if __name__ == "__main__":
    main()
