#!/usr/bin/env python3
"""Model module: catalog, deps, path check, verify, pull, install-tools."""

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
        verify = item.get("verify") or {}
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
            "verify": {
                "expect_files": list(verify.get("expect_files") or []),
                "min_bytes": int(verify.get("min_bytes") or 0),
            },
        })
    return rows


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


def print_exec_rows(rows: list[dict]) -> int:
    if not rows:
        return 1
    for item in rows:
        print(f"===== {item.get('node')} (rc={item.get('rc')}) =====")
        print((item.get("stdout") or "").rstrip())
        if item.get("stderr"):
            print((item.get("stderr") or "").rstrip())
    return 0 if all(int(r.get("rc", 1)) == 0 for r in rows) else 1


def exec_limit(args: argparse.Namespace) -> list[str]:
    if args.node:
        return ["--node", args.node]
    return ["--nodes", args.nodes or "all"]


def build_check_command(caches: list[dict], models: list[dict]) -> str:
    parts = [
        "echo HOST=$(hostname)",
        "echo HF=$(command -v huggingface-cli >/dev/null && echo yes || echo no)",
        "echo MS=$(command -v modelscope >/dev/null && echo yes || echo no)",
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


def build_verify_command(models: list[dict]) -> str:
    parts = ["echo HOST=$(hostname)", "fail=0"]
    for m in models:
        p = shlex.quote(m["path"])
        mid = shlex.quote(m["id"])
        parts.append(f"echo VERIFY:{mid}")
        parts.append(
            f"if [ ! -e {p} ]; then echo FAIL missing_path; fail=1; "
            f"else echo path_ok; "
            f"bytes=$(du -sb {p} 2>/dev/null | awk '{{print $1}}'); "
            f"echo bytes=$bytes; fi"
        )
        for name in m.get("verify", {}).get("expect_files") or []:
            fn = shlex.quote(name)
            parts.append(
                f"if [ -e {p} ] && [ ! -e {p}/{fn} ]; then echo FAIL missing_file {fn}; fail=1; "
                f"elif [ -e {p}/{fn} ]; then echo file_ok {fn}; fi"
            )
        min_b = int((m.get("verify") or {}).get("min_bytes") or 0)
        if min_b > 0:
            parts.append(
                f"if [ -e {p} ]; then bytes=$(du -sb {p} 2>/dev/null | awk '{{print $1}}'); "
                f"if [ \"${{bytes:-0}}\" -lt {min_b} ]; then echo FAIL min_bytes want>={min_b}; fail=1; "
                f"else echo size_ok; fi; fi"
            )
    parts.append("exit $fail")
    return "; ".join(parts)


def build_deps_command(python_bin: str) -> str:
    py = shlex.quote(python_bin or "python3")
    return (
        "echo HOST=$(hostname); "
        f"echo PYTHON={py}; "
        "echo HF_CLI=$(command -v huggingface-cli || command -v hf || echo missing); "
        "echo MODELSCOPE=$(command -v modelscope || echo missing); "
        "echo DOCKER=$(command -v docker || echo missing); "
        f"{py} - <<'PY'\n"
        "import importlib.util as u\n"
        "print('pkg_huggingface_hub', bool(u.find_spec('huggingface_hub')))\n"
        "print('pkg_modelscope', bool(u.find_spec('modelscope')))\n"
        "print('can_snapshot_download', bool(u.find_spec('huggingface_hub')))\n"
        "PY"
    )


def resolve_python_for_node(node: str | None) -> str:
    data = load_nodes_data()
    paths = node_paths(node, data) if node else default_paths(data)
    return paths.get("python") or "python3"


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
        print("未声明 models[]。编辑 config/local/models.json（见 docs/手册.md）")
        return 1
    for m in models:
        args = " ".join(m["launch"]["extra_args"])
        vf = ",".join(m["verify"]["expect_files"]) or "-"
        print(
            f"{m['id']}\tpath={m['path']}\trepo={m['repo'] or '-'}"
            f"\tjob={m['launch']['job'] or '-'}\targs={args or '-'}\tverify_files={vf}"
        )
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    data = load_models()
    node = args.node
    caches = cache_entries(data, node=node)
    models = model_entries(data, node=node)
    if not caches and not models:
        example = LOCAL_DIR.parent / "examples" / "models.example.json"
        print(f"未配置 models.json。\n  cp {example} {LOCAL_DIR / 'models.json'}")
        return 1
    remote = build_check_command(caches, models)
    result = run_cli(["exec", "--quiet", *exec_limit(args), "--", remote], timeout=180)
    rows = collect_exec()
    if rows:
        return print_exec_rows(rows)
    print(result["stdout"] or result["stderr"] or "无结果")
    return int(result["rc"] or 1)


def cmd_verify(args: argparse.Namespace) -> int:
    data = load_models()
    models = model_entries(data, node=args.node)
    if args.model:
        models = [m for m in models if m["id"] == args.model]
    if not models:
        print("没有可校验的 models[]（或 --model 不匹配）")
        return 1
    remote = build_verify_command(models)
    result = run_cli(["exec", "--quiet", *exec_limit(args), "--", remote], timeout=180)
    rows = collect_exec()
    if rows:
        return print_exec_rows(rows)
    print(result["stdout"] or result["stderr"] or "无结果")
    return int(result["rc"] or 1)


def cmd_deps(args: argparse.Namespace) -> int:
    py = resolve_python_for_node(args.node)
    remote = build_deps_command(py)
    result = run_cli(["exec", "--quiet", *exec_limit(args), "--", remote], timeout=120)
    rows = collect_exec()
    if rows:
        return print_exec_rows(rows)
    print(result["stdout"] or result["stderr"] or "无结果")
    return int(result["rc"] or 1)


def cmd_install_tools(args: argparse.Namespace) -> int:
    py = resolve_python_for_node(args.node)
    py_q = shlex.quote(py)
    pkgs = "huggingface_hub[cli] modelscope"
    remote = (
        f"echo HOST=$(hostname); "
        f"{py_q} -m pip install -U {pkgs} && "
        f"command -v huggingface-cli; command -v modelscope; "
        f"echo DONE"
    )
    result = run_cli(
        ["exec", "--quiet", *exec_limit(args), "--", remote],
        timeout=args.timeout,
    )
    rows = collect_exec()
    if rows:
        return print_exec_rows(rows)
    print(result["stdout"] or result["stderr"])
    return int(result["rc"] or 1)


def cmd_pull(args: argparse.Namespace) -> int:
    data = load_models()
    pull = data.get("pull") or {}
    tool = (args.tool or pull.get("tool") or "auto").lower()
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
        print("需要 --name <repo_id> 或 --model <id>（且声明 repo）")
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
    py = shlex.quote(resolve_python_for_node(args.node))
    hf_py = (
        f"{py} -c \"from huggingface_hub import snapshot_download; "
        f"print(snapshot_download(repo_id={repo!r}, local_dir={target!r}))\""
    )
    if tool == "huggingface-cli":
        remote = (
            f"mkdir -p {dest_q} && "
            f"if command -v huggingface-cli >/dev/null 2>&1; then "
            f"HF_HUB_CACHE={dest_q} huggingface-cli download {repo_q}; "
            f"elif command -v hf >/dev/null 2>&1; then "
            f"hf download {repo_q} --local-dir {dest_q}; "
            f"else {hf_py}; fi"
        )
    elif tool == "modelscope":
        remote = (
            f"mkdir -p {dest_q} && "
            f"command -v modelscope >/dev/null || "
            f"{{ echo '缺少 modelscope；先: gpu-control models install-tools'; exit 127; }}; "
            f"modelscope download --model {repo_q} --local_dir {dest_q}"
        )
    else:
        remote = (
            f"mkdir -p {dest_q} && "
            f"if command -v huggingface-cli >/dev/null 2>&1; then "
            f"HF_HUB_CACHE={dest_q} huggingface-cli download {repo_q}; "
            f"elif command -v hf >/dev/null 2>&1; then "
            f"hf download {repo_q} --local-dir {dest_q}; "
            f"elif {py} -c 'import huggingface_hub' >/dev/null 2>&1; then "
            f"{hf_py}; "
            f"elif command -v modelscope >/dev/null 2>&1; then "
            f"modelscope download --model {repo_q} --local_dir {dest_q}; "
            f"else echo '需要 huggingface_hub 或 modelscope；"
            f"运行: gpu-control models install-tools'; exit 127; fi"
        )
    result = run_cli(["exec", "--quiet", *exec_limit(args), "--", remote], timeout=args.timeout)
    rows = collect_exec()
    if rows:
        return print_exec_rows(rows)
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

    p_check = sub.add_parser("check", help="检查缓存/模型路径与磁盘")
    p_check.add_argument("--node")
    p_check.add_argument("--nodes", default="all")
    p_check.set_defaults(func=cmd_check)

    p_verify = sub.add_parser("verify", help="按 verify 规则校验模型目录")
    p_verify.add_argument("--model", help="只校验某个 models[].id")
    p_verify.add_argument("--node")
    p_verify.add_argument("--nodes", default="all")
    p_verify.set_defaults(func=cmd_verify)

    p_deps = sub.add_parser("deps", help="检查节点 HF / ModelScope / docker")
    p_deps.add_argument("--node")
    p_deps.add_argument("--nodes", default="all")
    p_deps.set_defaults(func=cmd_deps)

    p_install = sub.add_parser("install-tools", help="在节点 pip 安装 huggingface_hub + modelscope")
    p_install.add_argument("--node")
    p_install.add_argument("--nodes", default="all")
    p_install.add_argument("--timeout", type=int, default=900)
    p_install.set_defaults(func=cmd_install_tools)

    p_pull = sub.add_parser("pull", help="在节点上拉取模型权重")
    p_pull.add_argument("--name", help="仓库 id")
    p_pull.add_argument("--model", help="models[].id")
    p_pull.add_argument("--cache", help="caches[].id")
    p_pull.add_argument("--tool", choices=["auto", "huggingface-cli", "modelscope"])
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
