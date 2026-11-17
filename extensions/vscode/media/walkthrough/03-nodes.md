## 节点：终端、诊断、隔离、腾空

把鼠标移到节点行，右侧出现三个行内按钮：

| 按钮 | 作用 |
|---|---|
| 终端 | 用节点的 `ansible_host / port / user / key` 打开集成 SSH 终端 |
| 诊断 | 运行只读预设（`nvidia-smi`、GPU 利用率、进程、磁盘…） |
| 腾空 | 停 Job、结束 GPU 进程、释放租约（会弹确认） |

点节点名打开操作菜单，还能**隔离 / 取消隔离**节点。离线节点会被标注，避免误连。

推荐流程：

1. [运行节点探活](command:computedeck.liveness) 确认在线状态
2. 对可疑节点跑一次只读诊断
3. 需要维护时先隔离，再腾空

[节点操作](command:computedeck.nodeMenu) ｜ [测试 SSH](command:computedeck.testSSH)
