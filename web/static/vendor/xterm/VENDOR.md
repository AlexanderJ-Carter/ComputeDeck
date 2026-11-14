# Vendored xterm.js

The control console must work on intranet hosts without access to public CDNs,
so xterm.js is vendored into the repository instead of loaded from jsDelivr.

| file | upstream | sha256 |
|------|----------|--------|
| `xterm-6.1.0.min.js` | `@xterm/xterm@6.1.0-beta.291` → `lib/xterm.js` (UMD build) | `8e051771c1098d7252f21d84408ddf1daea6b55635897fde9ec60d7e25cc4d8e` |
| `xterm-6.1.0.css` | `@xterm/xterm@6.1.0-beta.291` → `css/xterm.css` | `4d9a1d50808997f097ccc6040a5da6f6cb06b14e5ee2402df5196a218bba838f` |

Source: upstream `@xterm/xterm` npm package (copy `lib/xterm.js` + `css/xterm.css`).

License: MIT — see [LICENSE](LICENSE).

## Upgrading

1. Copy the new `lib/xterm.js` and `css/xterm.css` in, renaming with the version.
2. Update the table above and the `<link>` / `<script>` tags in `web/static/index.html`.
3. Run `node --check web/static/xterm-fit.js` and load `/` + the terminal page.
4. `xterm-fit.js` does not depend on `addon-fit`, so no extra addon needs vendoring.
