## 部署与启停 Job

Job 是插件：列表来自主控已安装的 Job，不写死业务名。

部署有两种方式：

- **按 GPU 数自动选节点** — 填需要的 GPU 数，控制面按调度策略挑节点
- **指定节点** — 自己选一台空闲节点

Job 行内的三个按钮分别对应 **启动 / 重启 / 停止**；点 Job 名还能查看状态、等待就绪。

部署、启停都会先在通知里显示进度，结果写入 **输出 → ComputeDeck** 面板。

[部署 Job](command:computedeck.deployJob) ｜ [运行自检](command:computedeck.runDoctor)
