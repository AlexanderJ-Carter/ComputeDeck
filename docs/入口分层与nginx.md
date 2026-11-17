# 入口分层与 nginx

控制面里的“入口”容易越叠越多：节点端口、Edge、LiteLLM、nginx。原则只有一句：

> **同一批节点端口，只能有一层负责负载均衡。**

ComputeDeck 的推荐分工：

```
客户端
  │
  ├─(可选 TLS/域名/网页)── nginx ──┐
  │                                │
  └───────────────► Edge :18080 ◄──┘   ← 唯一节点 LB / 重试 / 冷却
                         │
                         ▼
              节点 GPU 推理端口 30000–30003
                         ▲
                         │
              LiteLLM :4000（可选 OpenAI 层）
              model 名 / 虚拟密钥 / 预算 / Admin
              与 Edge 共存时必须指向 Edge
```

## 1. 各层职责

| 层 | 职责 | 不该做什么 |
|---|---|---|
| 节点 GPU 端口 | 真正跑模型的服务 | 不直接作为产品入口 |
| **Edge :18080** | 节点 LB、健康感知、重试、冷却、可选 TLS | 不做模型名 / 虚拟密钥 |
| **LiteLLM :4000** | OpenAI 兼容、模型名、虚拟密钥、预算、Admin | 与 Edge 共存时不要直连节点再做第二套 LB |
| **nginx** | Web/TLS/域名/WAF、控制面 UI 反代 | 不要同时和 Edge 对同一批节点做 LB |

## 2. 推荐组合

| 组合 | 节点 LB 归属 | 适用 |
|---|---|---|
| **Edge only** | Edge | 默认、性能优先；所有 HTTP / OpenAI 请求都走 :18080 |
| **Edge + LiteLLM→Edge** | Edge | 需要 OpenAI 模型名、虚拟密钥、预算、Admin；LiteLLM 只做治理层 |
| LiteLLM direct | LiteLLM | 不使用 Edge；LiteLLM 的 model_list 直连节点 |
| nginx→Edge | Edge | 需要 443/TLS、域名、WAF；nginx 只做单上游前端 |
| nginx (kind=nginx) | nginx | 用 nginx 替代 Edge 做节点 LB；不要再开 Edge |
| nginx→LiteLLM→节点 | LiteLLM | 不使用 Edge，nginx 只做 TLS 前端 |

**性能建议**：对延迟敏感的流量直接走 **Edge :18080**。LiteLLM 是 Python 治理层，只有在需要模型名/虚拟密钥/预算时才放在链路上；不要把 LiteLLM 再串一层 LB。

## 3. 配置

```json
{
  "gateway": {
    "litellm_upstream": "edge",
    "nginx_upstream": "nodes",
    "edge_url": "http://127.0.0.1:18080"
  }
}
```

| 键 | 取值 | 说明 |
|---|---|---|
| `litellm_upstream` | `nodes` / `edge` | `edge` 时 LiteLLM 只生成一个指向 Edge 的 deployment；`nodes` 为旧的直连模式 |
| `nginx_upstream` | `nodes` / `edge` | `edge` 时 nginx 只做 TLS/Web 前端；`nodes` 时 nginx 作为 Edge 的同层替代 |
| `litellm_edge_url` | URL | 可选；默认 `http://127.0.0.1:<ingress.port>` |
| `litellm_database_url` / `litellm_database_url_file` | `postgres://…` | 可选；LiteLLM Admin UI 与虚拟密钥依赖 Postgres |

改完后：

```bash
gpu-control gateway render
gpu-control gateway stop --kind litellm
gpu-control gateway start --kind litellm
```

检查：

```bash
grep -c '^  - model_name:' state/gateway/litellm-model-list.yaml
# litellm_upstream=edge 时应为 1
grep 'api_base:' state/gateway/litellm-model-list.yaml
# 应为 http://127.0.0.1:18080/v1
gpu-control gateway status
```

### 3.1 LiteLLM Admin UI 与数据库

LiteLLM 的 `/ui` 管理员登录和虚拟密钥都依赖 Postgres：

- **未配数据库**：只能把 Master Key 当 `Authorization: Bearer` 调 `/v1`。用错误的 Key 请求时，LiteLLM 会回 `Authentication Error, Not connected to DB!`，这不是 Edge 的问题。
- **要开 Admin UI**：配 `gateway.litellm_database_url`（或 `litellm_database_url_file`），安装 LiteLLM 的数据库依赖并确保 `prisma` 客户端可用，然后重启 LiteLLM。管理台登录用户名 `admin`（`UI_USERNAME` 可覆盖），密码 = Master Key。
- 控制面「入口与网关」卡片会在未接数据库时把「打开 Admin」置灰，并提示改用 Master Key；`gpu-control gateway status` 的 `litellm.database` 字段给出同样的判断。

#### 3.1.1 用内置 Postgres（无需 root）

控制面自带一台用户态 Postgres 的管理命令（依赖 `pgserver`，二进制随 wheel 提供）：

```bash
/usr/bin/python3 -m pip install --user pgserver==0.1.4   # 一次性
gpu-control gateway litellm-db init      # 初始化 data 目录 + litellm 库 + DATABASE_URL
gpu-control gateway litellm-db status    # initialized / running / schema_tables
gpu-control gateway litellm-db start|stop
gpu-control gateway litellm-db url       # 打印完整 DATABASE_URL（含密码）
```

- 数据目录：`~/.local/share/computedeck/litellm-pg/data`
- 连接串：`config/local/secrets/litellm_database_url`（600）
- 端口：默认 `127.0.0.1:5433`（`GPU_CONTROL_LITELLM_PG_PORT` 可改）
- 配置：`gateway.litellm_database_mode = "embedded"` 时，`gateway start --kind litellm` 会自动拉起 Postgres
- schema：`init` 会优先使用 `state/gateway/litellm-schema.sql`、`config/local/litellm-schema.sql` 或仓库内 `config/examples/litellm-schema-1.86.7.sql`（LiteLLM 1.86.7）

LiteLLM 的 Python 侧仍需要 `prisma==0.11.0` 与预生成的客户端/查询引擎（LiteLLM 固定依赖该版本，离线环境需在可联网机器上生成后拷入 `~/.local`），并在启动进程里**不要**把 `prisma` CLI 放进 PATH：缺少 CLI 时 LiteLLM 会跳过自动迁移，直接使用预生成的 schema。

> **容器部署**：内置 Postgres 面向宿主机安装。容器里 `Path.home()` 是 `/root`，默认数据目录不在 compose 的卷里；如需在容器内使用，必须
> 1) 在镜像/容器里安装 `pgserver`（`/usr/bin/python3 -m pip install --user pgserver==0.1.4`），
> 2) 设 `GPU_CONTROL_LITELLM_PG_DATA=/app/state/litellm-pg/data`（`./state` 已挂载），
> 3) 同步 `config/local/secrets/litellm_database_url` 到该实例。
>
> 更推荐容器部署使用**外部 Postgres**，避免把数据库进程塞进控制面容器。

#### 3.1.2 外部数据库

已有 Postgres 时直接配连接串即可：

```json
{"gateway": {"litellm_database_url_file": "config/local/secrets/litellm_database_url"}}
```

连接串形如 `postgresql://litellm:<password>@db-host:5432/litellm`。首次使用需要把 LiteLLM 的 schema 导入该库（`litellm-schema-1.86.7.sql` 或 LiteLLM 自己跑 `prisma migrate deploy`）。

验证：

```bash
gpu-control gateway status | jq '.litellm.database'
# admin_ui=true 后，入口页「打开 Admin」可用；用户名 admin，密码 = Master Key
```

## 4. nginx 的四种用法

### 4.1 只做 Web/TLS 前端，API 交给 Edge（推荐）

```nginx
upstream gpu_control_edge {
    server 127.0.0.1:18080;
    keepalive 32;
}

server {
    listen 443 ssl;
    server_name api.example.com;
    ssl_certificate     /path/fullchain.pem;
    ssl_certificate_key /path/privkey.pem;

    location /v1/ {
        proxy_http_version 1.1;
        proxy_set_header Connection "";
        proxy_set_header Host $host;
        proxy_set_header Authorization $http_authorization;
        proxy_buffering off;                 # 流式 token 不被缓冲
        proxy_read_timeout 600s;
        proxy_pass http://gpu_control_edge;
    }

    location / {
        proxy_pass http://127.0.0.1:18080;   # 或控制面 :8787
    }
}
```

### 4.2 用 nginx 替代 Edge 做节点 LB

```bash
gpu-control gateway render --listen 18080
# 生成 state/gateway/nginx-inference-pool.conf
```

把它 include 到 nginx.conf，`nginx -t && nginx -s reload`。此时不要再启动 Edge。

### 4.3 nginx 反代控制面 UI

```nginx
location / {
    proxy_pass http://127.0.0.1:8787;
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
}
```

控制面鉴权必须开启；否则不要暴露到公网。

### 4.4 只做静态网页

nginx 托管站点，`/v1/` 另行动到 Edge；这和 API 网关不冲突，但不要用同一个 nginx 的 `least_conn` 再选节点。

## 5. 重复 LB 的识别

`gpu-control gateway status` / 入口页会显示：

- `Edge ... 唯一节点 LB`
- `LiteLLM 上游：Edge ...（推荐）`
- 若 LiteLLM 仍直连节点且 Edge 在跑：**「Edge 与 LiteLLM 都在对节点做负载均衡（重复）」**

修复二选一：

1. `gateway.litellm_upstream = "edge"`，再 render + restart LiteLLM；
2. 停用 Edge，由 LiteLLM 独占节点 LB。

## 6. 性能与 TLS

- Edge 是控制面内置的轻量 HTTP 代理，适合放在热路径；LiteLLM 只在需要治理能力时使用。
- TLS 只在一层终止：nginx 443 或 Edge TLS 二选一，不要在 nginx 和 Edge 之间重复握手。
- 流式响应：nginx 需 `proxy_buffering off`；Edge 原生流式转发。
