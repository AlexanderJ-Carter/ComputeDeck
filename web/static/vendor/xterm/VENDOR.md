# 内置 xterm.js 说明

控制台需在无公网 CDN 的内网主机可用，因此将 xterm.js 打进仓库，而不是从 jsDelivr 加载。

| 文件 | 上游 | sha256 |
|------|------|--------|
| `xterm-6.1.0.min.js` | `@xterm/xterm@6.1.0-beta.291` → `lib/xterm.js`（UMD） | `8e051771c1098d7252f21d84408ddf1daea6b55635897fde9ec60d7e25cc4d8e` |
| `xterm-6.1.0.css` | `@xterm/xterm@6.1.0-beta.291` → `css/xterm.css` | `4d9a1d50808997f097ccc6040a5da6f6cb06b14e5ee2402df5196a218bba838f` |

来源：npm 包 `@xterm/xterm`（复制 `lib/xterm.js` 与 `css/xterm.css`）。

上游许可证：MIT — 见本目录 [LICENSE](LICENSE)（与仓库根目录 AGPL-3.0 相互独立）。

## 升级步骤

1. 复制新的 `lib/xterm.js`、`css/xterm.css`，按版本重命名。
2. 更新上表，以及 `web/static/index.html` 中的 `<link>` / `<script>`。
3. 运行 `node --check web/static/xterm-fit.js`，并打开 `/` 与终端页验证。
4. `xterm-fit.js` 不依赖 `addon-fit`，无需再内置额外 addon。
