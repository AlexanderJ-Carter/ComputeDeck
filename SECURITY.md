# 安全说明

## 支持的版本

| 版本 | 支持 |
|------|------|
| 0.21.x | 当前维护 |
| < 0.21 | 请升级 |

## 报告漏洞

请通过私信或邮件联系维护者（见提交作者），**不要**在公开 Issue 里贴可利用细节。

## 部署基线

- 生产必须开启鉴权：`gpu-control auth set-password --generate --enable`，并用 Bearer Token 访问 API / MCP。
- UI 默认绑定 `127.0.0.1`（或 `cluster.control.ui_host`）；公网暴露前先看 [安全加固](docs/安全加固.md)。
- 密钥与场景配置只放 `config/local/secrets/` 与 `config/local/`，勿提交。
- 推理入口（Edge / nginx / LiteLLM）鉴权与控制面鉴权分离，需各自配置。

更完整的清单见 [安全加固](docs/安全加固.md) 与 [生产部署](docs/生产部署.md)。
