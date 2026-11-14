# HTTP API 概览

控制面默认监听 **`:8787`**。机器可读目录：

```bash
curl -s http://127.0.0.1:8787/api | jq .
curl -s http://127.0.0.1:8787/api/openapi.json | jq '.info'
```

交互文档（带鉴权试调）：[/api/docs](/api/docs) · 完整 OpenAPI 说明见 [OpenAPI / Swagger](openapi.md)。

## 鉴权

| 方式 | 用法 |
|------|------|
| Session Cookie | 浏览器 `POST /api/auth/login` |
| Bearer Token | `Authorization: Bearer <token>`（`gpu-control auth issue-token`） |

公开发现路径（无需登录）：`/api` · `/api/openapi.json` · `/api/docs` · `/api/health` · `/api/auth/*` · `/help/`。

## 分组

| 分组 | 用途 |
|------|------|
| meta | 健康、目录、OpenAPI、运行日志、检测 |
| auth | 登录 / 退出 / 身份 |
| read | 总览、Job、模型、Gateway 状态 |
| config | 读写 cluster / nodes / job |
| ops | 部署、腾空、exec、gateway 启停、工具栈 |
| models | 拉取 / 校验 / Token |
| terminal | WebSocket 终端 |

## 常用示例

```bash
TOKEN=$(cat config/local/secrets/ui_api_token)

# 总览（轻量）
curl -s -H "Authorization: Bearer $TOKEN" \
  'http://127.0.0.1:8787/api/overview?light=1'

# 写配置
curl -s -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"data":{...}}' http://127.0.0.1:8787/api/config/cluster

# 一键检测
curl -s -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8787/api/diagnose
```

完整路径列表以 `GET /api` 的 `http` 字段为准。Agent 侧约定见 [AI / MCP 接入](../ai-接入.md)。
