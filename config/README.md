# config/

| 路径 | 提交 | 说明 |
|------|------|------|
| `examples/*.example.json` | ✅ | 脱敏模板（cluster / nodes / job / models） |
| `local/*` | ❌ | 本机真实配置 |
| `ansible.cfg` | ✅ | Ansible 通用设置 |

```bash
mkdir -p config/local/jobs
cp config/examples/cluster.example.json config/local/cluster.json
cp config/examples/nodes.example.json   config/local/nodes.json
# 可选
cp config/examples/decider.example.json config/local/jobs/decider.json
cp config/examples/models.example.json  config/local/models.json
```

查找顺序：`config/local/` → `config/` → 仓库根。
