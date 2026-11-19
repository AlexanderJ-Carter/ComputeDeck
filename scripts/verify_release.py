#!/usr/bin/env python3
"""校验本地发布目录：SHA256SUMS、manifest.json、关键版本字段。"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify(out: Path) -> dict:
    problems: list[str] = []
    sums = out / "SHA256SUMS"
    manifest = out / "manifest.json"
    checked = 0
    if not sums.is_file():
        problems.append("缺少 SHA256SUMS")
    else:
        for line in sums.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            parts = line.split(None, 1)
            if len(parts) != 2:
                problems.append(f"SHA256SUMS 行格式错误：{line}")
                continue
            expected, name = parts[0].lower(), parts[1].lstrip("*")
            target = out / name
            if not target.is_file():
                problems.append(f"缺少文件：{name}")
                continue
            actual = _sha256(target)
            if actual != expected:
                problems.append(f"校验失败：{name}")
            checked += 1
    info: dict = {}
    if not manifest.is_file():
        problems.append("缺少 manifest.json")
    else:
        try:
            info = json.loads(manifest.read_text(encoding="utf-8"))
        except ValueError as exc:
            problems.append(f"manifest.json 不是合法 JSON：{exc}")
        for key in ("product", "version", "extension_version"):
            if not info.get(key):
                problems.append(f"manifest.json 缺少 {key}")
    return {
        "ok": not problems,
        "dir": str(out),
        "checked": checked,
        "version": info.get("version"),
        "extension_version": info.get("extension_version"),
        "commit": info.get("commit"),
        "problems": problems,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dir", help="发布目录，例如 dist/1.2.7")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    payload = verify(Path(args.dir).expanduser())
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"verify: {'OK' if payload['ok'] else 'FAILED'} · {payload['dir']}")
        print(f"  version={payload['version']} extension={payload['extension_version']} files={payload['checked']}")
        for item in payload["problems"]:
            print(f"  ! {item}")
    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
