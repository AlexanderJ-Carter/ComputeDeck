# Docstring 与注释规范

目标：中文、短、可扫读；给 Agent / 维护者看，不写空话。

## 原则

1. **模块顶栏必有**：一句话说明职责 + 关键入口（CLI / HTTP / 文件）。
2. **公开函数**：用简短中文 docstring；参数/返回仅在非显而易见时写。
3. **不写**：复述代码的注释、过时的英文草稿、冗余 `try/except` 回落说明。
4. **不要向后兼容注释**：删掉的 API 写在 `CHANGELOG`，不堆在代码里。

## 模块顶栏模板

```python
"""节点路径合并与 ``${paths.*}`` 占位符展开。

供 Job / models 解析使用；配置见 ``nodes.json`` defaults.paths。
"""
```

## 函数模板

```python
def resolve_job(job: dict, node: str, data: dict | None = None) -> dict:
    """按节点展开 Job 中的路径、env、docker 字段。"""
```

多参数时可用 Args（仍用中文）：

```python
def append_runlog(kind: str, *, ok: bool | None = None, summary: str = "") -> dict:
    """追加一条控制面运行日志。

    Args:
        kind: 类别，如 ``cli`` / ``oneclick`` / ``diagnose``。
        ok: 是否成功；未知传 ``None``。
        summary: 一行摘要。
    """
```

## MCP 工具

MCP `@mcp.tool()` 的 docstring **就是** Agent 看到的工具说明，必须中文、含关键约束（如鉴权、危险动作）。

## 文档站

叙述性内容放 `docs/`，用 MkDocs 构建：

```bash
uv pip install -r requirements-docs.txt
gpu-control docs build
```

接口契约以 `lib/api_catalog.py` → `/api/openapi.json` 为准，避免在多处复制路径表。
