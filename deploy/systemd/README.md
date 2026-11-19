# systemd 单元

模板假设控制面在 `/opt/computedeck`，运行用户 `computedeck`。按实际路径和 Job 名修改后：

```bash
sudo useradd --system --home /opt/computedeck --shell /usr/sbin/nologin computedeck
sudo chown -R computedeck:computedeck /opt/computedeck
sudo cp deploy/systemd/*.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now computedeck-ui computedeck-watchdog
sudo systemctl status computedeck-ui
```

- UI 默认只绑 `127.0.0.1`；需要局域网访问时改 `GPU_CONTROL_UI_HOST`，并先开鉴权。
- watchdog 的 `--job` 改成 `cluster.default_job` 或你的业务 Job。
- 单元假设仓库自带 `.venv`；容器部署不需要这两个单元。
- 需要反向代理 / TLS 时，在 nginx / Edge 层终止，见 [入口分层与 nginx](../../docs/入口分层与nginx.md)。
