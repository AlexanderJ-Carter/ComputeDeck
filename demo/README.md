# ComputeDeck 宣传展板

全屏纵向 scroll-snap 演示页（中文），适合投屏与 GitHub Pages。

## 本地预览

```bash
cd demo
python3 -m http.server 4173
# http://127.0.0.1:4173/
```

## GitHub Pages

已用工作流 `.github/workflows/pages.yml` 发布本目录。

- 默认：https://alexanderj-carter.github.io/ComputeDeck/
- 自定义域名：见 [docs/自定义域名.md](../docs/自定义域名.md)；买域后把域名写入 `CNAME`（参考 `CNAME.example`）

产品名 **ComputeDeck**；命令行仍为 `gpu-control`。

## 交互

- ↓ / 空格 / 圆点翻页；顶栏「自动播放」
- 开场粒子与指针光斑；GPU 格轮换；Agent 终端逐行打字
- 能力卡片轻微透视跟随指针（尊重 `prefers-reduced-motion`）
