#!/usr/bin/env python3
"""检查静态站点内部链接与本地资源是否存在。

用法::

    python scripts/check_site.py /tmp/site

覆盖 demo 根路径和 MkDocs 的 `/docs/`；外链、mailto、纯锚点跳过。
"""

from __future__ import annotations

import argparse
import sys
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit


class LinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        for name, value in attrs:
            if name in {"href", "src"} and value:
                self.links.append(value)


def _target_for(root: Path, page: Path, link: str) -> Path | None:
    parts = urlsplit(link)
    if parts.scheme or parts.netloc or link.startswith(("mailto:", "tel:", "javascript:", "#")):
        return None
    raw = unquote(parts.path or "")
    if not raw:
        return None
    # 控制面运行时路由，不是静态文件。
    if raw.startswith(("/api", "/help", "/auth", "/ws/")):
        return None
    target = (page.parent / raw).resolve() if not raw.startswith("/") else (root / raw.lstrip("/")).resolve()
    if target.is_dir():
        target = target / "index.html"
    return target


def check(root: Path) -> dict:
    root = root.resolve()
    problems: list[str] = []
    pages = sorted(p for p in root.rglob("*.html") if p.is_file())
    for page in pages:
        parser = LinkParser()
        try:
            parser.feed(page.read_text(encoding="utf-8", errors="replace"))
        except OSError as exc:
            problems.append(f"{page.relative_to(root)}: 读取失败 {exc}")
            continue
        for link in parser.links:
            target = _target_for(root, page, link)
            if target is None:
                continue
            try:
                target.relative_to(root)
            except ValueError:
                # 允许指向 root 外的绝对路径（例如 /api/docs），这些是运行时路由。
                continue
            if not target.exists():
                problems.append(f"{page.relative_to(root)} → {link}")
    return {"ok": not problems, "pages": len(pages), "problems": problems}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", help="构建后的站点目录，例如 _site")
    args = parser.parse_args()
    root = Path(args.root).expanduser().resolve()
    if not root.is_dir():
        print(f"目录不存在：{root}", file=sys.stderr)
        return 2
    result = check(root)
    print(f"site-check: {'OK' if result['ok'] else 'FAILED'} · {result['pages']} pages")
    for item in result["problems"]:
        print(f"  ! {item}")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
