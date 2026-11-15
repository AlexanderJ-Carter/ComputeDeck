<!-- BEAUTIFIED -->
<h1 align="center">ComputeDeck</h1>

<p align="center">
  <strong>多节点 GPU 控制面</strong>
  <br />
  <em>Web · CLI · MCP · 配置驱动</em>
</p>

<p align="center">
  <a href="#快速开始"><img src="https://img.shields.io/badge/快速开始-0D9488?style=for-the-badge" alt="快速开始" /></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-AGPL--3.0-red?style=for-the-badge" alt="AGPL-3.0" /></a>
  <a href="CHANGELOG.md"><img src="https://img.shields.io/badge/Changelog-Keep%20a%20Changelog-blue?style=for-the-badge" alt="Changelog" /></a>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.11+-3776AB?style=flat-square&logo=python&logoColor=white" alt="Python" />
  <img src="https://img.shields.io/badge/Ansible-自动化-EE0000?style=flat-square&logo=ansible&logoColor=white" alt="Ansible" />
  <img src="https://img.shields.io/badge/MCP-15%20tools-111827?style=flat-square" alt="MCP" />
  <img src="https://img.shields.io/badge/docs-MkDocs-526CFE?style=flat-square&logo=materialformkdocs&logoColor=white" alt="Docs" />
</p>

<p align="center">
  <code>gpu-control</code>
  ·
  <a href="docs/index.md">文档</a>
  ·
  <a href="CONTRIBUTING.md">贡献</a>
  ·
  <a href="SECURITY.md">安全</a>
</p>

---

## 背景

实验室和业务团队手里常有一批 SSH 就能登上的 GPU 机器。卡谁在用、进程在哪、能不能腾空，往往靠群聊和 `nvidia-smi`；微调、推理、Ray 各自一套启停脚本，换机器就要改路径和端口；想让 Cursor 这类 Agent 帮忙运维，又缺少统一的 HTTP / MCP 契约。就算接了 LiteLLM 或 nginx，也常和「现在到底哪几张卡在服」对不上。

**ComputeDeck**（命令行：`gpu-control`）把发现、租约、部署 Job、健康检查、统一入口和日志收成一套控制面。一台 Control 主机跑 Web UI、CLI 和 MCP，用 Ansible/SSH 编排 worker。

## 性能（带对标）

本机 2026-11-15 · macOS arm64 · Python 3.11 · loopback，未接 GPU worker。完整表见 [docs/性能实测.md](docs/性能实测.md)。

| 指标 | 测得 | 对标 | 怎么读 |
|------|------|------|--------|
| UI 就绪 | **~77 ms** | 空 `python -c pass` ≈ 17 ms；一眨眼 ≈ 100–400 ms | 约 4–5 个空 Python 进程，通常快过一次眨眼 |
| `check.sh` | **0.50 s** | 常见 CI 门禁数十秒起 | 改完本机秒级回归 |
| `/api/health` | p50 **0.61 ms** | 同机最小 HTTP ≈ 0.27 ms | 约 2.3× 裸 handler，仍远低于 1 ms |
| 看板轮询 `overview?light=1` | p50 **0.68 ms** | 全量 overview 0.96 ms / 17 KB | 体量约全量 **43%**，延迟低约 **30%** |
| 20 并发 light | 整批 **~41 ms** | — | 本机约 **~500 次/秒** 轮询量级 |
| OpenAPI / MCP / 模板 | **73** 操作 · **15** `gc_*` · **11** 模板 | 纯脚本机房通常 0 契约、0 MCP | Agent 与脚本共用同一能力面 |

## 能力

| 能力 | 说明 |
|------|------|
| 单屏看板 | 健康评分、GPU 池、Edge / TLS / Gateway、快捷运维 |
| Job 插件 | JSON 描述启停；训练 / 推理 / Ray / Docker 从模板安装 |
| 路径异构 | `defaults.paths`、节点覆盖、`${paths.*}` |
| 统一入口 | 内置 Edge LB（重试、冷却、least-conn），可挂 nginx / LiteLLM |
| Agent | OpenAPI、`/api/docs`、MCP `gc_*` |
| 可靠性 | 状态原子写与 flock、watchdog、diagnose |

场景写在 `config/local/`，换集群主要改配置。浏览器、CLI、MCP 共用能力目录；xterm 内置，内网也能开终端。

## 架构

<p align="center">
  <img src="docs/assets/architecture-overview.svg" alt="ComputeDeck 架构：使用者、Control 主机与 GPU Workers" width="900" />
</p>

控制面在 Control（Web / CLI / MCP / Edge + `state/`）；Worker 跑 Job。编排走 Ansible/SSH，推理走 Edge 健康转发。

## 快速开始

```bash
uv venv .venv && uv pip install -r requirements.txt
./examples/quickstart.sh
cp -n config/examples/cluster.example.json config/local/cluster.json
cp -n config/examples/nodes.example.json   config/local/nodes.json
# 按集群改 host、路径、GPU 数
gpu-control node render
gpu-control doctor
gpu-control auth set-password --generate --enable
gpu-control auth issue-token
gpu-control ui --background
gpu-control watchdog --background
```

```bash
ssh -L 8787:127.0.0.1:8787 <control>
# 浏览器 http://127.0.0.1:8787/
```

## 常用命令

```bash
gpu-control overview
gpu-control free --node gpu-1 --wait 30 --cordon
gpu-control jobs install train-job --name sft
gpu-control deploy sft --nodes gpu-1,gpu-2 --gpus 16
gpu-control gateway ensure
curl -s http://127.0.0.1:8787/api
```

文档：[手册](docs/手册.md) · [分布式](docs/分布式与推理.md) · [高可用](docs/高可用与负载均衡.md) · [AI/MCP](docs/ai-接入.md) · [性能实测](docs/性能实测.md)

## 环境与校验

| 组件 | 要求 |
|------|------|
| 主控 | Linux（推荐）/ macOS；Python 3.11+；`uv`；Ansible；SSH |
| 工作节点 | Linux + NVIDIA GPU；SSH 免密；`nvidia-smi` |
| 可选 | Docker + NVIDIA Toolkit；vLLM / Ray / LLaMA-Factory / DeepSpeed 等 |

```bash
./scripts/check.sh
gpu-control doctor
```

## 参与与许可

[CONTRIBUTING](CONTRIBUTING.md) · [SECURITY](SECURITY.md) · [SUPPORT](SUPPORT.md)

**AGPL-3.0**，见 [LICENSE](LICENSE) / [NOTICE](NOTICE)。版本 [`VERSION`](VERSION) · [CHANGELOG](CHANGELOG.md)
