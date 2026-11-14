"""一站式 AI 工具栈目录：探测 / 安装说明与节点侧命令。

控制面不内置二进制，通过节点 ``paths.python`` 做 probe / pip install。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from paths import CONFIG_DIR, LOCAL_DIR, STATE_DIR

STATE_FILE = STATE_DIR / "toolkits.json"

# 内置常用栈（可被 config/local/toolkits.json 覆盖同 id）
BUILTIN_TOOLKITS: list[dict[str, Any]] = [
    {
        "id": "torch",
        "name": "PyTorch",
        "group": "runtime",
        "description": "训练/推理基础运行时",
        "probe_import": "torch",
        "install_pip": [],
        "note": "通常由节点镜像预装；不在此强制覆盖版本",
    },
    {
        "id": "transformers",
        "name": "Transformers",
        "group": "runtime",
        "description": "Hugging Face 模型库",
        "probe_import": "transformers",
        "install_pip": ["transformers"],
    },
    {
        "id": "accelerate",
        "name": "Accelerate",
        "group": "train",
        "description": "HF 多卡启动器",
        "probe_import": "accelerate",
        "install_pip": ["accelerate"],
        "related_jobs": ["torchrun-train"],
    },
    {
        "id": "deepspeed",
        "name": "DeepSpeed",
        "group": "train",
        "description": "ZeRO 分布式训练引擎",
        "probe_import": "deepspeed",
        "install_pip": ["deepspeed"],
        "related_jobs": ["deepspeed-train", "train-job"],
    },
    {
        "id": "llamafactory",
        "name": "LLaMA-Factory",
        "group": "train",
        "description": "一站式微调（SFT/LoRA/…）",
        "probe_import": "llamafactory",
        "probe_cli": "llamafactory-cli version",
        "install_pip": ["llamafactory[torch,metrics]"],
        "related_jobs": ["train-job", "llamafactory-train"],
    },
    {
        "id": "peft",
        "name": "PEFT",
        "group": "train",
        "description": "LoRA / 参数高效微调",
        "probe_import": "peft",
        "install_pip": ["peft"],
    },
    {
        "id": "vllm",
        "name": "vLLM",
        "group": "serve",
        "description": "高吞吐推理服务",
        "probe_import": "vllm",
        "install_pip": ["vllm"],
        "related_jobs": ["vllm-docker"],
    },
    {
        "id": "openai-pkg",
        "name": "OpenAI SDK",
        "group": "client",
        "description": "调用 OpenAI 兼容 API",
        "probe_import": "openai",
        "install_pip": ["openai"],
    },
]

BUNDLES: list[dict[str, Any]] = [
    {
        "id": "train-stack",
        "name": "微调栈",
        "description": "LLaMA-Factory + DeepSpeed + Accelerate + PEFT",
        "toolkits": ["accelerate", "deepspeed", "peft", "llamafactory"],
    },
    {
        "id": "serve-stack",
        "name": "推理栈",
        "description": "vLLM + OpenAI SDK（节点 pip；Docker 任务可另用镜像）",
        "toolkits": ["vllm", "openai-pkg"],
    },
]


def _local_override_path() -> Path:
    return LOCAL_DIR / "toolkits.json"


def _example_path() -> Path:
    return CONFIG_DIR / "examples" / "toolkits.example.json"


def load_toolkit_catalog() -> list[dict[str, Any]]:
    """合并内置与 local 覆盖（同 id 以 local 为准）。"""
    by_id = {item["id"]: dict(item) for item in BUILTIN_TOOLKITS}
    path = _local_override_path()
    if path.is_file():
        raw = json.loads(path.read_text(encoding="utf-8"))
        for item in raw.get("toolkits") or []:
            if not isinstance(item, dict) or not item.get("id"):
                continue
            tid = str(item["id"])
            merged = dict(by_id.get(tid) or {})
            merged.update(item)
            by_id[tid] = merged
    return sorted(by_id.values(), key=lambda x: (x.get("group") or "", x.get("id") or ""))


def load_bundles() -> list[dict[str, Any]]:
    path = _local_override_path()
    if path.is_file():
        raw = json.loads(path.read_text(encoding="utf-8"))
        if raw.get("bundles"):
            return list(raw["bundles"])
    return list(BUNDLES)


def catalog_payload() -> dict[str, Any]:
    return {
        "toolkits": load_toolkit_catalog(),
        "bundles": load_bundles(),
        "state": load_toolkit_state(),
        "example": str(_example_path()),
        "local": str(_local_override_path()),
    }


def load_toolkit_state() -> dict[str, Any]:
    if not STATE_FILE.is_file():
        return {"nodes": {}, "generated_at": None}
    return json.loads(STATE_FILE.read_text(encoding="utf-8"))


def save_toolkit_state(data: dict[str, Any]) -> Path:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return STATE_FILE


def resolve_ids(ids: list[str] | None = None, bundle: str | None = None) -> list[dict[str, Any]]:
    catalog = {t["id"]: t for t in load_toolkit_catalog()}
    wanted: list[str] = []
    if bundle:
        for b in load_bundles():
            if b["id"] == bundle:
                wanted.extend(b.get("toolkits") or [])
                break
        else:
            raise ValueError(f"未知 bundle: {bundle}")
    if ids:
        wanted.extend(ids)
    if not wanted:
        wanted = list(catalog.keys())
    out = []
    seen = set()
    for tid in wanted:
        if tid in seen:
            continue
        seen.add(tid)
        if tid not in catalog:
            raise ValueError(f"未知 toolkit: {tid}")
        out.append(catalog[tid])
    return out


def build_probe_script(toolkits: list[dict[str, Any]], python_bin: str = "python3") -> str:
    """生成在节点上执行的探测脚本（输出一行 JSON）。"""
    specs = []
    for t in toolkits:
        specs.append(
            {
                "id": t["id"],
                "import": t.get("probe_import") or "",
                "cli": t.get("probe_cli") or "",
            }
        )
    payload = json.dumps(specs, ensure_ascii=False)
    code = (
        "import importlib.util,json,subprocess\n"
        f"specs=json.loads({payload!r})\n"
        "out=[]\n"
        "for s in specs:\n"
        "  item={'id':s['id'],'ok':False,'version':None,'detail':''}\n"
        "  mod=s.get('import') or ''\n"
        "  if mod:\n"
        "    if importlib.util.find_spec(mod) is None:\n"
        "      item['detail']='missing'\n"
        "    else:\n"
        "      m=__import__(mod)\n"
        "      item['ok']=True\n"
        "      item['version']=getattr(m,'__version__',None)\n"
        "      if mod=='torch':\n"
        "        item['detail']=f\"cuda={m.cuda.is_available()} n={m.cuda.device_count()}\"\n"
        "  cli=s.get('cli') or ''\n"
        "  if cli and not item['ok']:\n"
        "    r=subprocess.run(cli,shell=True,capture_output=True,text=True)\n"
        "    item['ok']=r.returncode==0\n"
        "    item['detail']=(r.stdout or r.stderr or '')[:200].strip()\n"
        "  out.append(item)\n"
        "print(json.dumps({'toolkits':out},ensure_ascii=False))\n"
    )
    return f"{python_bin} -c {code!r}"


def build_install_script(toolkits: list[dict[str, Any]], python_bin: str = "python3") -> str:
    """生成 pip 安装命令；跳过 install_pip 为空的项。"""
    pkgs: list[str] = []
    for t in toolkits:
        for pkg in t.get("install_pip") or []:
            if pkg and pkg not in pkgs:
                pkgs.append(pkg)
    if not pkgs:
        return "echo 'no pip packages to install'; exit 0"
    quoted = " ".join(repr(p) for p in pkgs)
    return f"{python_bin} -m pip install -U {quoted}"
