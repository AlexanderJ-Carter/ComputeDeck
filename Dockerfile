# ComputeDeck 控制面镜像（UI / CLI / Ansible / MCP 依赖）。Worker 仍是真实 GPU 主机，不入此镜像。
FROM python:3.11-slim-bookworm

ARG VERSION=0.0.0
ARG VCS_REF=unknown
ARG BUILD_DATE=unknown

LABEL org.opencontainers.image.title="ComputeDeck" \
      org.opencontainers.image.description="Multi-node GPU control plane (Web / CLI / MCP). CLI name: gpu-control." \
      org.opencontainers.image.source="https://github.com/AlexanderJ-Carter/ComputeDeck" \
      org.opencontainers.image.url="https://github.com/AlexanderJ-Carter/ComputeDeck" \
      org.opencontainers.image.documentation="https://github.com/AlexanderJ-Carter/ComputeDeck/tree/main/docs" \
      org.opencontainers.image.licenses="AGPL-3.0-only" \
      org.opencontainers.image.vendor="ComputeDeck" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.revision="${VCS_REF}" \
      org.opencontainers.image.created="${BUILD_DATE}"

RUN apt-get update \
  && apt-get install -y --no-install-recommends \
    ca-certificates \
    git \
    openssh-client \
    sshpass \
  && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt requirements-mcp.txt ./
RUN python -m venv /app/.venv \
  && /app/.venv/bin/pip install --no-cache-dir -U pip \
  && /app/.venv/bin/pip install --no-cache-dir -r requirements.txt \
  && /app/.venv/bin/pip install --no-cache-dir -r requirements-mcp.txt

COPY . .
RUN chmod +x /app/bin/gpu-control /app/bin/gpu-control-mcp \
  && mkdir -p /app/config/local /app/state /app/snapshots \
  && ln -sf /app/bin/gpu-control /usr/local/bin/gpu-control \
  && ln -sf /app/bin/gpu-control-mcp /usr/local/bin/gpu-control-mcp

ENV PATH="/app/.venv/bin:/app/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    GPU_CONTROL_UI_HOST=0.0.0.0 \
    GPU_CONTROL_UI_PORT=8787 \
    COMPUTEDECK_VERSION="${VERSION}"

EXPOSE 8787

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD ["/app/.venv/bin/python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8787/api/health', timeout=3)"]

CMD ["/app/.venv/bin/python", "web/server.py"]
