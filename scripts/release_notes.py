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
VSCODE_PKG = ROOT / "extensions" / "vscode" / "package.json"


def _read_pyproject_version() -> str:
    text = PYPROJECT.read_text(encoding="utf-8")
    match = re.search(r'^version\s*=\s*"([^"]+)"', text, re.M)
    if not match:
        raise SystemExit("pyproject.toml 缺少 version")
    return match.group(1)


def _read_vscode_version() -> str | None:
    if not VSCODE_PKG.is_file():
        return None
    text = VSCODE_PKG.read_text(encoding="utf-8")
    match = re.search(r'^  "version"\s*:\s*"([^"]+)"', text, re.M)
    if not match:
        raise SystemExit("extensions/vscode/package.json 缺少 version")
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


def check_versions(version: str, *, allow_extension_mismatch: bool = False) -> None:
    file_ver = VERSION_FILE.read_text(encoding="utf-8").strip()
    py_ver = _read_pyproject_version()
    errors: list[str] = []
    if file_ver != version:
        errors.append(f"VERSION={file_ver!r} ≠ {version!r}")
    if py_ver != version:
        errors.append(f"pyproject version={py_ver!r} ≠ {version!r}")
    vs_ver = _read_vscode_version()
    if vs_ver is not None and vs_ver != version:
        message = f"extensions/vscode/package.json version={vs_ver!r} ≠ {version!r}"
        if allow_extension_mismatch:
            print(f"warn: {message}（本地打包允许；发版前 prepare_release 会收束）", file=sys.stderr)
        else:
            errors.append(message)
    extract_notes(version)
    if errors:
        raise SystemExit("版本不一致：\n- " + "\n- ".join(errors))
    extra = " / VS Code 扩展" if vs_ver is not None and vs_ver == version else ""
    suffix = f"；VS Code 扩展单独为 {vs_ver}" if allow_extension_mismatch and vs_ver != version else ""
    print(f"ok: VERSION / pyproject{extra} / CHANGELOG 均为 {version}{suffix}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("version", help="语义化版本，如 0.22.0（不要带 v）")
    parser.add_argument(
        "--check",
        action="store_true",
        help="只校验 VERSION / pyproject / CHANGELOG 是否一致",
    )
    parser.add_argument(
        "--allow-extension-mismatch",
        action="store_true",
        help="本地打包时允许扩展版本独立；发版 CI 不要使用",
    )
    args = parser.parse_args()
    version = args.version.lstrip("v")
    if args.check:
        check_versions(version, allow_extension_mismatch=args.allow_extension_mismatch)
        return
    sys.stdout.write(extract_notes(version))


if __name__ == "__main__":
    main()
