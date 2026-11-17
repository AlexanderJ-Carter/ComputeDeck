"""宽松 JSON 解析：模型 / CLI 输出偶发破损时用 json-repair 修复。

只用于「上游或本机 CLI 产出的数据」；HTTP 请求体、配置文件、鉴权材料仍用严格 JSON。
"""

from __future__ import annotations

import json
from typing import Any

try:  # 可选依赖：缺失时退化为严格解析
    from json_repair import repair_json
except ImportError:  # pragma: no cover - 环境未装 json-repair
    repair_json = None  # type: ignore[assignment]

_MISSING = object()


def loads_relaxed(text: Any, *, default: Any = _MISSING) -> Any:
    """先严格解析，失败后尝试 json-repair；仍失败时返回 default 或抛错。"""
    if isinstance(text, (dict, list)):
        return text
    raw = text.decode("utf-8", errors="replace") if isinstance(text, bytes) else str(text or "")
    raw = raw.strip()
    if not raw:
        if default is not _MISSING:
            return default
        raise json.JSONDecodeError("empty JSON", raw, 0)
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        pass
    if repair_json is not None:
        try:
            repaired = repair_json(raw, return_objects=True)
            if isinstance(repaired, (dict, list)):
                return repaired
            if isinstance(repaired, str):
                return json.loads(repaired)
            return repaired
        except Exception:  # noqa: BLE001 - repair 只是兜底
            pass
    if default is not _MISSING:
        return default
    raise json.JSONDecodeError("invalid JSON", raw, 0)


def loads_object(text: Any, *, default: dict | None = None) -> dict:
    """解析并保证返回 dict；顶层不是对象时返回 default / {}。"""
    obj = loads_relaxed(text, default=default if default is not None else {})
    return obj if isinstance(obj, dict) else (default if default is not None else {})
