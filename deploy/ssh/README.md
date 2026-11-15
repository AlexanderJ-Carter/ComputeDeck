# 控制面容器用的 SSH 密钥

把能登录 GPU worker 的私钥放到本目录（例如 `id_ed25519`、`config`），**不要提交私钥**。

```bash
cp ~/.ssh/id_ed25519 deploy/ssh/
chmod 600 deploy/ssh/id_ed25519
# 可选：写 config，Host 与 nodes.json 的 ansible_host 对应
docker compose up -d --build
```

也可用环境变量改挂载路径：

```bash
export COMPUTEDECK_SSH_DIR=$HOME/.ssh
docker compose up -d
```
