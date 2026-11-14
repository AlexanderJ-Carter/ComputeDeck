#!/usr/bin/env python3
"""Optional model-module helpers: cache check / pull on workers."""

from __future__ import annotations

import argparse
import json
import shlex
import subprocess
from pathlib import Path

import _bootstrap  # noqa: F401
from paths import (
    LOCAL_DIR,
    PROJECT_DIR,
    STATE_DIR,
    VENV_PYTHON,
    CLI,
    expand_path,
    resolve_config,
)


def models_file() -> Path:
    return resolve_config("models.json")


def load_models() -> dict:
    path = models_file()
    if not path.is_file():
        return {"caches": [], "pull": {}}
    return json.loads(path.read_text(encoding="utf-8"))


def cache_entries(data: dict) -> list[dict]:
    rows = []
    for item in data.get("caches") or []:
        path = expand_path(str(item.get("path") or ""))
        if not path:
            continue
        rows.append({
            "id": item.get("id") or Path(path).name,
            "path": path,
            "note": item.get("note") or "",
        })
    return rows


def build_check_command(caches: list[dict]) -> str:
    parts = ["echo HOST=$(hostname)"]
    for c in caches:
        p = shlex.quote(c["path"])
        cid = shlex.quote(c["id"])
        parts.append(
            f"echo CACHE:{cid}; "
            f"if [ -e {p} ]; then "
            f"du -sh {p} 2>/dev/null | awk '{{print \"size\", $1}}'; "
            f"df -h {p} 2>/dev/null | awk 'NR==2{{print \"disk\", $4 \" free of \" $2}}'; "
            f"else echo missing; fi"
        )
    parts.append("df -h / /tmp 2>/dev/null | head -5")
    return "; ".join(parts)


def run_cli(args: list[str], timeout: int = 300) -> dict:
    completed = subprocess.run(
        [str(VENV_PYTHON), str(CLI), *args],
        cwd=PROJECT_DIR,
        text=True,
        capture_output=True,
        check=False,
        timeout=timeout,
    )
    return {
        "rc": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "cmd": args,
    }


def collect_exec() -> list[dict]:
    results = []
    exec_dir = STATE_DIR / "exec"
    if not exec_dir.is_dir():
        return results
    for path in sorted(exec_dir.glob("*.json")):
        results.append(json.loads(path.read_text(encoding="utf-8")))
    return results


def cmd_check(_: argparse.Namespace) -> int:
    data = load_models()
    caches = cache_entries(data)
    if not caches:
        example = LOCAL_DIR.parent / "examples" / "models.example.json"
        print(
            "未配置 models.json。复制示例后编辑路径：\n"
            f"  cp {example} {LOCAL_DIR / 'models.json'}"
        )
        return 1
    remote = build_check_command(caches)
    result = run_cli(["exec", "--quiet", "--nodes", "all", "--", remote], timeout=180)
    rows = collect_exec()
    if rows:
        for item in rows:
            print(f"===== {item.get('node')} (rc={item.get('rc')}) =====")
            print((item.get("stdout") or "").rstrip())
            if item.get("stderr"):
                print((item.get("stderr") or "").rstrip())
        ok = all(int(r.get("rc", 1)) == 0 for r in rows)
        return 0 if ok else 1
    print(result["stdout"] or result["stderr"] or "无结果")
    return int(result["rc"] or 1)


def cmd_pull(args: argparse.Namespace) -> int:
    data = load_models()
    pull = data.get("pull") or {}
    caches = {c["id"]: c for c in cache_entries(data)}
    cache_id = args.cache or pull.get("default_cache")
    if not cache_id or cache_id not in caches:
        print("需要 --cache 或 models.json pull.default_cache，且对应 caches[].id 存在")
        return 1
    if not args.name:
        print("需要 --name <repo_id>，例如 org/model")
        return 1
    dest = shlex.quote(caches[cache_id]["path"])
    repo = shlex.quote(args.name)
    remote = (
        f"mkdir -p {dest} && "
        f"if command -v huggingface-cli >/dev/null 2>&1; then "
        f"HF_HUB_CACHE={dest} huggingface-cli download {repo}; "
        f"elif command -v modelscope >/dev/null 2>&1; then "
        f"modelscope download --model {repo} --local_dir {dest}/{repo}; "
        f"else echo '需要 huggingface-cli 或 modelscope'; exit 127; fi"
    )
    limit = ["--node", args.node] if args.node else ["--nodes", args.nodes or "all"]
    result = run_cli(["exec", "--quiet", *limit, "--", remote], timeout=args.timeout)
    rows = collect_exec()
    if rows:
        for item in rows:
            print(f"===== {item.get('node')} (rc={item.get('rc')}) =====")
            print((item.get("stdout") or item.get("stderr") or "").rstrip())
        return 0 if all(int(r.get("rc", 1)) == 0 for r in rows) else 1
    print(result["stdout"] or result["stderr"])
    return int(result["rc"] or 1)


def cmd_show(_: argparse.Namespace) -> int:
    path = models_file()
    data = load_models()
    print(json.dumps({
        "path": str(path),
        "exists": path.is_file(),
        "caches": cache_entries(data),
        "pull": data.get("pull") or {},
    }, ensure_ascii=False, indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser("show", help="显示 models 配置").set_defaults(func=cmd_show)
    sub.add_parser("check", help="各节点检查缓存路径与磁盘").set_defaults(func=cmd_check)
    p_pull = sub.add_parser("pull", help="在节点上拉取模型权重")
    p_pull.add_argument("--name", required=True, help="仓库 id，如 org/model")
    p_pull.add_argument("--cache", help="caches[].id")
    p_pull.add_argument("--node")
    p_pull.add_argument("--nodes", default="all")
    p_pull.add_argument("--timeout", type=int, default=3600)
    p_pull.set_defaults(func=cmd_pull)
    args = parser.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
