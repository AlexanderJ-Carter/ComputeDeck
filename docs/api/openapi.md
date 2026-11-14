# OpenAPI / Swagger

## 在线页

控制面 UI 提供自托管文档页（**不依赖外网 CDN**）：

| URL | 内容 |
|-----|------|
| `/api/docs` | 接口列表、鉴权说明、Bearer 试调 |
| `/docs` | 同上别名 |
| `/api/openapi.json` | OpenAPI 3.0.3 |

在 UI「指南 → API / MCP 文档」也可打开。

## 安全方案（OpenAPI components）

- `bearerAuth`：`Authorization: Bearer <token>`
- `cookieAuth`：Cookie `gc_session`

公开接口在 operation 上设置 `security: []`。

## 本地导入

可把 `/api/openapi.json` 导入 Postman / Insomnia / 其他 OpenAPI 客户端。

```bash
curl -s http://127.0.0.1:8787/api/openapi.json -o openapi.json
```

## 与文档站的关系

- **MkDocs `/help/`**：叙述性文档（手册、约定）
- **`/api/docs`**：运行时接口与试调（跟随当前进程版本）
