"""静态站点路径解析工具。"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import unquote


def resolve_help_target(static_dir: Path | str, request_path: str) -> Path | None:
    """把 ``/help/...`` 请求路径解析为文档站文件。

    浏览器会把中文路径编码成 ``%E6%89%8B%E5%86%8C`` 这类形式，因此必须先
    解码再拼路径；解码后仍用 ``is_relative_to`` 限制在文档根目录内。

    :param static_dir: ``web/static`` 目录。
    :param request_path: 原始请求路径（可能含百分号编码）。
    :returns: 命中的文件；不存在或越界时返回 ``None``。
    """
    root = (Path(static_dir) / "help").resolve()
    rel = request_path[len("/help"):].lstrip("/") or "index.html"
    rel = unquote(rel)
    if rel.endswith("/"):
        rel += "index.html"
    target = (root / rel).resolve()
    if root.is_dir() and target.is_relative_to(root) and target.is_file():
        return target
    return None
