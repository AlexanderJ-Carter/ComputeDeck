/* Pre-paint theme bootstrap. Kept external so CSP can stay script-src 'self'. */
(function () {
  var mode = localStorage.getItem("gpu-control-theme") || "auto";
  var resolved =
    mode === "auto"
      ? (window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light")
      : mode;
  document.documentElement.setAttribute("data-theme", resolved);
  document.documentElement.setAttribute("data-theme-mode", mode);
})();
