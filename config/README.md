# config/

ComputeDeck 的配置根目录。

| 路径 | 是否提交 | 说明 |
|------|----------|------|
| `examples/` | 是 | 集群 / 节点 / Job / models / toolkits 等中性模板 |
| `local/` | 否（gitignore） | 本机真实集群；含 `jobs/`、`secrets/` |

```bash
cp -n config/examples/cluster.example.json config/local/cluster.json
cp -n config/examples/nodes.example.json config/local/nodes.json
gpu-control jobs install --recommended   # → config/local/jobs/
```

字段说明见 [配置参考](../docs/配置参考.md)；上手见 [手册](../docs/手册.md)。
