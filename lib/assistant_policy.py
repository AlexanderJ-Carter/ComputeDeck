"""助手 / MCP 共用的工具风险分级与教学摘要。

UI 小助手与任意 stdio Agent 共用同一套语义；不在此执行工具。
"""

from __future__ import annotations

from typing import Any

# risk: read | write | destructive
TOOL_RISKS: dict[str, str] = {
    "gc_api_catalog": "read",
    "gc_auth_status": "read",
    "gc_doctor": "read",
    "gc_overview": "read",
    "gc_get_jobs": "read",
    "gc_job_action": "write",
    "gc_get_config": "read",
    "gc_put_config": "write",
    "gc_models": "write",  # pull 等会写磁盘；list/show 亦走同一工具
    "gc_toolkits": "write",
    "gc_procs": "destructive",
    "gc_free_node": "destructive",
    "gc_deploy": "write",
    "gc_queue": "write",
    "gc_lease": "write",
    "gc_diagnose": "write",  # repair 可改状态
    "gc_gateway": "write",
    "gc_node": "write",
    "gc_exec": "destructive",
    "gc_cli": "destructive",
}

TEACHING_SUMMARY = (
    "ComputeDeck（CLI/MCP：gpu-control）是可迁移的多节点 GPU 控制面。"
    "先查目录（gc_api_catalog / GET /api），不要假设特定 Job 名，以已安装列表为准。"
    "调度用 gc_queue / gc_lease；排障用 gc_diagnose；入口用 gc_gateway；"
    "隔离与 inventory 用 gc_node；异主租约部署用 gc_deploy(force=true)。"
    "改配置后建议 gc_node(action='render') 与 gc_doctor()。"
    "kill / free / 写配置前先 gc_overview 或 gc_get_config 确认目标。"
    "HTTP 401 时检查 GPU_CONTROL_API_TOKEN / gc_auth_status。"
)

MCP_CLIENT_EXAMPLES = {
    "cursor": "config/examples/mcp.cursor.json",
    "claude_code": "config/examples/mcp.claude-code.json",
    "codex": "config/examples/mcp.codex.toml",
    "codex_json": "config/examples/mcp.codex.json",
    "continue": "config/examples/mcp.continue.json",
    "windsurf": "config/examples/mcp.windsurf.json",
    "generic": "config/examples/mcp.generic.json",
}


def risk_for(tool_name: str) -> str:
    """返回工具风险级别；未知工具视为 destructive。"""
    return TOOL_RISKS.get(tool_name, "destructive")


def tools_by_risk() -> dict[str, list[str]]:
    """按 risk 分组的工具名列表。"""
    out: dict[str, list[str]] = {"read": [], "write": [], "destructive": []}
    for name, risk in TOOL_RISKS.items():
        out.setdefault(risk, []).append(name)
    for key in out:
        out[key].sort()
    return out


def policy_payload() -> dict[str, Any]:
    """供 ``GET /api`` / 助手元数据暴露。"""
    return {
        "teaching": TEACHING_SUMMARY,
        "risks": TOOL_RISKS,
        "by_risk": tools_by_risk(),
        "mcp_examples": MCP_CLIENT_EXAMPLES,
        "ui_defaults": {
            "auto_execute": ["read"],
            "confirm": ["write"],
            "confirm_twice": ["destructive"],
        },
    }
