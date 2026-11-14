#!/usr/bin/env python3
"""Model module: catalog, cache check, pull, path resolve."""

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
from node_paths import default_paths, load_nodes_data, node_paths, substitute


def models_file() -> Path:
    return resolve_config("models.json")


def load_models() -> dict:
    path = models_file()
    if not path.is_file():
        return {"caches": [], "models": [], "pull": {}}
    return json.loads(path.read_text(encoding="utf-8"))


def cache_entries(data: dict, *, node: str | None = None) -> list[dict]:
    nodes_data = load_nodes_data()
    paths = node_paths(node, nodes_data) if node else default_paths(nodes_data)
    rows = []
    for item in data.get("caches") or []:
        raw = str(item.get("path") or "")
        if not raw:
            continue
        try:
            path = substitute(raw, paths) if "${paths." in raw else expand_path(raw)
        except KeyError:
            path = expand_path(raw)
        rows.append({
            "id": item.get("id") or Path(path).name,
            "path": path,
            "note": item.get("note") or "",
        })
    return rows


def model_entries(data: dict, *, node: str | None = None) -> list[dict]:
    nodes_data = load_nodes_data()
    paths = node_paths(node, nodes_data) if node else default_paths(nodes_data)
    caches = {c["id"]: c for c in cache_entries(data, node=node)}
    rows = []
    for item in data.get("models") or []:
        mid = item.get("id")
        if not mid:
            continue
        raw_path = str(item.get("path") or "")
        cache_id = item.get("cache")
        if raw_path:
            try:
                path = substitute(raw_path, paths) if "${paths." in raw_path else expand_path(raw_path)
            except KeyError as exc:
                path = f"<error:{exc}>"
        elif cache_id and cache_id in caches:
            path = caches[cache_id]["path"]
        else:
            path = ""
        launch = item.get("launch") or {}
        rows.append({
            "id": mid,
            "repo": item.get("repo") or "",
            "path": path,
            "cache": cache_id or "",
            "note": item.get("note") or "",
            "launch": {
                "job": launch.get("job") or "",
                "extra_args": list(launch.get("extra_args") or []),
                "runtime": launch.get("runtime") or "process",
            },
        })
    return rows


def build_check_command(caches: list[dict], models: list[dict]) -> str:
    parts = [
        "echo HOST=$(hostname)",
        "echo DOCKER=$(command -v docker >/dev/null && echo yes || echo no)",
    ]
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
    for m in models:
        p = shlex.quote(m["path"])
        mid = shlex.quote(m["id"])
        parts.append(
            f"echo MODEL:{mid}; "
            f"if [ -e {p} ]; then echo path_ok {p}; "
            f"du -sh {p} 2>/dev/null | awk '{{print \"size\", $1}}'; "
            f"else echo missing {p}; fi"
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


def cmd_show(_: argparse.Namespace) -> int:
    path = models_file()
    data = load_models()
    print(json.dumps({
        "path": str(path),
        "exists": path.is_file(),
        "defaults_paths": default_paths(),
        "caches": cache_entries(data),
        "models": model_entries(data),
        "pull": data.get("pull") or {},
    }, ensure_ascii=False, indent=2))
    return 0


def cmd_list(_: argparse.Namespace) -> int:
    data = load_models()
    models = model_entries(data)
    if not models:
        print("未声明 models[]。编辑 config/local/models.json")
        return 1
    for m in models:
        args = " ".join(m["launch"]["extra_args"])
        print(
            f"{m['id']}\tpath={m['path']}\trepo={m['repo'] or '-'}"
            f"\tjob={m['launch']['job'] or '-'}\targs={args or '-'}"
        )
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    data = load_models()
    node = args.node
    caches = cache_entries(data, node=node)
    models = model_entries(data, node=node)
    if not caches and not models:
        example = LOCAL_DIR.parent / "examples" / "models.example.json"
        print(
            "未配置 models.json。复制示例后编辑路径：\n"
            f"  cp {example} {LOCAL_DIR / 'models.json'}"
        )
        return 1
    remote = build_check_command(caches, models)
    limit = ["--node", node] if node else ["--nodes", args.nodes or "all"]
    result = run_cli(["exec", "--quiet", *limit, "--", remote], timeout=180)
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
    caches = {c["id"]: c for c in cache_entries(data, node=args.node)}
    models = {m["id"]: m for m in model_entries(data, node=args.node)}

    repo = args.name
    dest = None
    cache_id = args.cache or pull.get("default_cache")

    if args.model and args.model in models:
        m = models[args.model]
        repo = repo or m.get("repo")
        dest = m.get("path")
        cache_id = args.cache or m.get("cache") or cache_id

    if not repo:
        print("需要 --name <repo_id> 或 --model <models[].id>（且声明 repo）")
        return 1

    if dest:
        target = dest
    else:
        if not cache_id or cache_id not in caches:
            print("需要 --cache / pull.default_cache，或模型声明 path")
            return 1
        target = caches[cache_id]["path"]

    dest_q = shlex.quote(target)
    repo_q = shlex.quote(repo)
    remote = (
        f"mkdir -p {dest_q} && "
        f"if command -v huggingface-cli >/dev/null 2>&1; then "
        f"HF_HUB_CACHE={dest_q} huggingface-cli download {repo_q}; "
        f"elif command -v modelscope >/dev/null 2>&1; then "
        f"modelscope download --model {repo_q} --local_dir {dest_q}/{repo_q}; "
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


def cmd_paths(args: argparse.Namespace) -> int:
    data = load_nodes_data()
    nodes = sorted((data.get("nodes") or {}).keys())
    if args.node:
        nodes = [args.node]
    payload = {
        "defaults": default_paths(data),
        "nodes": {name: node_paths(name, data) for name in nodes},
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser("show", help="显示 models 配置").set_defaults(func=cmd_show)
    sub.add_parser("list", help="列出已声明模型").set_defaults(func=cmd_list)
    p_check = sub.add_parser("check", help="各节点检查缓存 / 模型路径 / docker")
    p_check.add_argument("--node")
    p_check.add_argument("--nodes", default="all")
    p_check.set_defaults(func=cmd_check)
    p_pull = sub.add_parser("pull", help="在节点上拉取模型权重")
    p_pull.add_argument("--name", help="仓库 id，如 org/model")
    p_pull.add_argument("--model", help="models[].id")
    p_pull.add_argument("--cache", help="caches[].id")
    p_pull.add_argument("--node")
    p_pull.add_argument("--nodes", default="all")
    p_pull.add_argument("--timeout", type=int, default=3600)
    p_pull.set_defaults(func=cmd_pull)
    p_paths = sub.add_parser("paths", help="显示 defaults/节点路径合并结果")
    p_paths.add_argument("--node")
    p_paths.set_defaults(func=cmd_paths)
    args = parser.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
