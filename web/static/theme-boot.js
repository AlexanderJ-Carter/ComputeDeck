/* Pre-paint theme + path-prefix bootstrap (CodeLab / VS Code /proxy/<port>/).
 * Kept external so CSP can stay script-src 'self'. */
(function () {
  var mode = localStorage.getItem("gpu-control-theme") || "auto";
  var resolved =
    mode === "auto"
      ? window.matchMedia("(prefers-color-scheme: dark)").matches
        ? "dark"
        : "light"
      : mode;
  document.documentElement.setAttribute("data-theme", resolved);
  document.documentElement.setAttribute("data-theme-mode", mode);

  // VS Code / CodeLab port proxy: page is at /proxy/8787/ but absolute
  // /static and /api would hit the IDE host root and return unstyled HTML.
  var path = location.pathname || "/";
  var match = path.match(/^(.*?\/proxy\/\d+)(?=\/|$)/);
  var base = match ? match[1] : "";
  window.__CD_BASE__ = base;

  function withBase(url) {
    if (!url || !base) return url;
    if (url.charAt(0) !== "/") return url;
    if (url.indexOf("//") === 0) return url;
    if (url.indexOf(base + "/") === 0 || url === base) return url;
    return base + url;
  }

  window.__CD_URL__ = withBase;

  function rewriteRootUrls(root) {
    if (!base || !root || !root.querySelectorAll) return;
    root.querySelectorAll("a[href^='/'], link[href^='/'], script[src^='/'], img[src^='/'], form[action^='/']").forEach(function (el) {
      var attr = el.hasAttribute("href") ? "href" : el.hasAttribute("src") ? "src" : "action";
      var val = el.getAttribute(attr);
      if (!val || val.indexOf("//") === 0) return;
      el.setAttribute(attr, withBase(val));
    });
  }

  window.__CD_REWRITE_ROOT_URLS__ = rewriteRootUrls;

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", function () {
      rewriteRootUrls(document);
    });
  } else {
    rewriteRootUrls(document);
  }
})();
