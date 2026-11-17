"""UI / MCP 共用的远程只读诊断预设。

这些命令不修改节点状态，可被助手 / MCP 自动执行；任意命令仍走 gc_exec 并需确认。
"""

from __future__ import annotations

EXEC_PRESETS = {
    "nvidia-smi": {
        "label": "nvidia-smi",
        "command": "nvidia-smi",
    },
    "gpu-util": {
        "label": "GPU 利用率",
        "command": (
            "nvidia-smi --query-gpu=index,name,utilization.gpu,utilization.memory,"
            "memory.used,memory.total,temperature.gpu "
            "--format=csv"
        ),
    },
    "gpu-list": {
        "label": "列出 GPU",
        "command": "nvidia-smi -L",
    },
    "gpu-procs": {
        "label": "GPU 进程",
        "command": (
            "nvidia-smi --query-compute-apps=gpu_uuid,pid,process_name,used_gpu_memory "
            "--format=csv"
        ),
    },
    "uptime": {
        "label": "负载/内存",
        "command": "uptime; free -h; nproc; cat /proc/loadavg",
    },
    "disk": {
        "label": "磁盘",
        "command": "df -h / /tmp 2>/dev/null; df -h | head -20",
    },
    "model-procs": {
        "label": "推理进程",
        "command": (
            "pgrep -af 'python|torch|vllm|sglang|uvicorn|gunicorn' | head -50 || true"
        ),
    },
    "train-procs": {
        "label": "训练进程",
        "command": (
            "pgrep -af 'torchrun|deepspeed|llamafactory|accelerate|train_manager' | head -50 || true"
        ),
    },
    "nccl-env": {
        "label": "NCCL/分布式环境",
        "command": (
            "env | grep -E '^(NCCL_|MASTER_|NNODES|NODE_RANK|NPROC_|CUDA_VISIBLE)' | sort || "
            "echo 'no dist env in this shell'"
        ),
    },
    "train-log": {
        "label": "最近训练日志尾",
        "command": (
            "ls -t /tmp/gpu-control-jobs/*/job.log 2>/dev/null | head -1 | "
            "xargs -r tail -n 40 || echo 'no job log under /tmp/gpu-control-jobs'"
        ),
    },
    "ports": {
        "label": "监听端口",
        "command": "ss -lntp 2>/dev/null | head -50 || netstat -lntp 2>/dev/null | head -50",
    },
    "api-ports": {
        "label": "常见 API 端口",
        "command": (
            "ss -lntp 2>/dev/null | grep -E ':3000|:8000|:8080|:4000' || "
            "echo 'no common API ports listening'"
        ),
    },
    "local-health": {
        "label": "本机 /health",
        "command": (
            "for p in 8000 8080 4000 3000; do "
            "code=$(curl -s -o /dev/null -w '%{http_code}' --connect-timeout 1 --max-time 2 "
            "http://127.0.0.1:$p/health || echo down); "
            "echo \"127.0.0.1:$p/health -> $code\"; done"
        ),
    },
    "docker-ps": {
        "label": "Docker 容器",
        "command": "docker ps --format 'table {{.Names}}\\t{{.Status}}\\t{{.Ports}}' 2>/dev/null || echo 'docker unavailable'",
    },
    "hostname": {
        "label": "主机信息",
        "command": "hostname; date; whoami; ip -4 addr show scope global 2>/dev/null | awk '/inet /{print $2}' | head -5",
    },
    "torch-cuda": {
        "label": "Torch CUDA",
        "command": (
            "python3 -c \"import importlib.util as u; "
            "s=u.find_spec('torch'); "
            "print('torch missing') if not s else "
            "(__import__('torch'), "
            "print('version', __import__('torch').__version__), "
            "print('cuda', __import__('torch').cuda.is_available(), "
            "'count', __import__('torch').cuda.device_count()))\""
        ),
    },
}
