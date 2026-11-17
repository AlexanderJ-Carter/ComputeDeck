# 安全说明

## 支持的版本

| 版本 | 支持 |
|------|------|
| 1.0.x | 当前维护 |
| < 1.0 | 请升级 |

## 报告漏洞

请邮件联系维护者：`2253940186@qq.com`（见 [AUTHORS](AUTHORS)）。

**不要**在公开 Issue 里贴可利用细节、密钥或内网拓扑。

我们会确认收悉，并在修复后按需发布安全通告（可匿名致谢）。

## 部署基线

- 生产必须开启鉴权：`gpu-control auth set-password --generate --enable`，并用 Bearer Token 访问 API / MCP。
- UI 默认绑定 `127.0.0.1`（或 `cluster.control.ui_host`）；公网暴露前先看 [安全加固](docs/安全加固.md)。
- 密钥与场景配置只放 `config/local/secrets/` 与 `config/local/`，勿提交。
- 推理入口（Edge / nginx / LiteLLM）鉴权与控制面鉴权分离，需各自配置。
- 本项目以 **AGPL-3.0** 发布：网络提供修改版时须向使用者提供对应源码（见 [NOTICE](NOTICE)）。

更完整的清单见 [安全加固](docs/安全加固.md) 与 [生产部署](docs/生产部署.md)。
