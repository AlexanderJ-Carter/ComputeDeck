# 入口分层与 nginx

控制面里的“入口”只保留两级：

> **节点 LB 只做一层：启用 LiteLLM 时由 LiteLLM 直连各节点端口；Edge / nginx 只做 18080 的 TLS/Web 前置反代。**

```
客户端
  │
  ├─(可选 TLS/域名/网页)── nginx / Edge :18080 ──┐
  │                                             │
  └──────────────────────────────► LiteLLM :4000 ◄┘   ← 唯一节点 LB
                                     │  模型名 / 虚拟密钥 / 预算 / Admin
                                     ▼
                          节点 GPU 推理端口 30000–30003
```

不使用 LiteLLM 的轻量部署，才让内置 Edge 直接做节点 LB。

## 1. 各层职责

| 层 | 职责 | 不该做什么 |
|---|---|---|
| 节点 GPU 端口 | 真正跑模型的服务 | 不直接作为产品入口 |
| **LiteLLM :4000** | 唯一节点 LB、重试/冷却、模型名、虚拟密钥、预算、Admin | 不要再让 Edge/nginx 对同一批节点做第二套 LB |
| **Edge :18080** | `ingress.proxy_to` 时只反代到 LiteLLM；无 LiteLLM 时直接做节点 LB | 不要在 LiteLLM 直连节点时再对节点做 LB |
| **nginx** | TLS/域名/WAF/控制面 UI 反代；`nginx_upstream=litellm` 时只反代 LiteLLM | 不要和 LiteLLM 对同一批节点做 LB |

## 2. 推荐组合

| 组合 | 节点 LB 归属 | 适用 |
|---|---|---|
| **LiteLLM direct（推荐）** | LiteLLM | 客户端直接打 `:4000`；少一跳，虚拟密钥/预算/重试都在 LiteLLM |
| LiteLLM + nginx :18080 | LiteLLM | 需要 443/TLS/域名；nginx 单 upstream → `127.0.0.1:4000` |
| LiteLLM + Edge :18080 | LiteLLM | 没有 nginx 时用内置 Edge 做前置反代（`ingress.proxy_to`） |
| Edge only | Edge | 不用 LiteLLM、只要原始 HTTP LB |
| nginx only | nginx | 用 nginx 替代 Edge 做节点 LB |

**性能建议**：对延迟敏感的流量直接走 **LiteLLM :4000**；18080 只承担 TLS/域名/网页，不要再串两层反代。

## 3. 配置

```json
{
  "gateway": {
    "litellm_upstream": "nodes",
    "nginx_upstream": "litellm",
    "ingress": {
      "proxy_to": "http://127.0.0.1:4000"
    }
  }
}
```

| 键 | 取值 | 说明 |
|---|---|---|
| `litellm_upstream` | `nodes`（推荐）/ `edge` | `nodes` 时 LiteLLM 的 model_list 直连每个节点端口；`edge` 用于不使用 LiteLLM、由 Edge 做节点 LB 的轻量部署 |
| `nginx_upstream` | `litellm`（推荐）/ `edge` / `nodes` | `litellm` 时 nginx 只做 TLS/Web 前端；`nodes` 时 nginx 自己做节点 LB |
| `ingress.proxy_to` | URL | 让内置 Edge 只反代到一个上游（通常 `http://127.0.0.1:4000`），不再选节点 |
| `litellm_edge_url` | URL | `litellm_upstream=edge` 的轻量部署使用；默认 `http://127.0.0.1:<ingress.port>` |
| `litellm_database_url` / `litellm_database_url_file` | `postgres://…` | LiteLLM Admin UI 与虚拟密钥依赖 Postgres |

改完后：

```bash
gpu-control gateway render
gpu-control gateway stop --kind litellm
gpu-control gateway start --kind litellm
gpu-control gateway stop --kind edge && gpu-control gateway start --kind edge   # 使用 proxy_to 时重启 Edge
```

检查：

```bash
grep -c '^  - model_name:' state/gateway/litellm-model-list.yaml
# litellm_upstream=nodes 时应等于节点端口数（例如 16）
grep 'api_base:' state/gateway/litellm-model-list.yaml
# 应为各节点 http://10.x.x.x:3000x/v1，而不是 18080
gpu-control gateway status
# layering.summary 应为「LiteLLM 是唯一节点 LB……」
```

### 3.1 LiteLLM Admin UI 与数据库

LiteLLM 的 `/ui` 管理员登录和虚拟密钥都依赖 Postgres：

- **未配数据库**：用 Master Key 作为 `Authorization: Bearer` 调 `/v1`；错误 Key 会得到 LiteLLM 的数据库未连接提示。
- **要开 Admin UI**：配 `gateway.litellm_database_url`（或 `litellm_database_url_file`），安装 LiteLLM 的数据库依赖并确保 `prisma` 客户端可用，然后重启 LiteLLM。管理台登录用户名 `admin`（`UI_USERNAME` 可覆盖），密码 = Master Key。
- 控制面「入口与网关」卡片会在未接数据库时把「打开 Admin」置灰，并提示改用 Master Key；`gpu-control gateway status` 的 `litellm.database` 字段给出同样的判断。

#### 3.1.1 用内置 Postgres（无需 root）

控制面自带一台用户态 Postgres 的管理命令（依赖 `pgserver`，二进制随 wheel 提供）：

```bash
/usr/bin/python3 -m pip install --user pgserver==0.1.4   # 一次性
gpu-control gateway litellm-db init      # 初始化 data 目录 + litellm 库 + DATABASE_URL
gpu-control gateway litellm-db status    # initialized / running / schema_tables
gpu-control gateway litellm-db start|stop
gpu-control gateway litellm-db url       # 默认脱敏；需要完整连接串加 --reveal
```

- 数据目录：`~/.local/share/computedeck/litellm-pg/data`
- 连接串：`config/local/secrets/litellm_database_url`（600）
- 端口：默认 `127.0.0.1:5433`（`GPU_CONTROL_LITELLM_PG_PORT` 可改）
- 配置：`gateway.litellm_database_mode = "embedded"` 时，`gateway start --kind litellm` 会自动拉起 Postgres
- schema：`init` 使用仓库内 `config/examples/litellm-schema-1.86.7.sql`（LiteLLM 1.86.7）；自定义版本放到 `config/local/litellm-schema.sql` 覆盖

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

### 4.1 Web/TLS 前端 → LiteLLM（推荐）

```nginx
upstream gpu_control_litellm {
    server 127.0.0.1:4000;
    keepalive 32;
}

server {
    listen 18080 ssl;
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
        proxy_pass http://gpu_control_litellm;
    }

    location / {
        proxy_pass http://127.0.0.1:8787;   # 控制面 UI
    }
}
```

`gateway.nginx_upstream="litellm"` 时 `gateway render` 会生成等价产物。

### 4.2 用 nginx 替代 Edge 做节点 LB（不用 LiteLLM 时）

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

- `LiteLLM 是唯一节点 LB（直连节点端口）`
- `LiteLLM 是唯一节点 LB；Edge 仅反代到 LiteLLM`（设置了 `ingress.proxy_to`）
- 若 LiteLLM 直连节点且 Edge 仍在直接选节点：**「Edge 与 LiteLLM 都在对节点做负载均衡（重复）」**

修复：

1. 让 Edge 只做前置代理：`gateway.ingress.proxy_to = "http://127.0.0.1:4000"`，重启 Edge；
2. 或在轻量部署里让 `litellm_upstream = "edge"`，由 Edge 独占节点 LB。

## 6. 性能与 TLS

- 热路径是 **LiteLLM :4000 → 节点端口**；`18080` 只承担 TLS/域名/网页，不要再叠第二层反代。
- TLS 只在一层终止：nginx 443/18080 或 Edge TLS 二选一，不要在 nginx 和 Edge 之间重复握手。
- 流式响应：nginx 需 `proxy_buffering off`；Edge 原生流式转发。
