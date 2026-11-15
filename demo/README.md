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

- 默认自动播放（约 6.2s 一页）；滚轮 / 触摸 / 键盘会暂停；顶栏可再开
- ↓ / 空格 / 圆点翻页（小屏隐藏圆点，用手势滑动）
- 开场粒子与指针光斑；可见页才跑 GPU 轮换与粒子；Agent 终端逐行打字
- 细指针设备上能力卡片轻微透视（尊重 `prefers-reduced-motion`）

## 性能注意

- CSS / logo `preload`；字体异步加载（`media=print` → `all`），先系统中文字体兜底
- 下方配图 `loading=lazy`；`app.js` `defer`
- 非当前页粒子停画；切后台停定时器；`content-visibility` 减轻离屏绘制
- GitHub Pages CDN 自带协商缓存；改静态资源后重新部署即可失效
