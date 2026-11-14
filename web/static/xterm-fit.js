/* Terminal resize helper — replaces @xterm/addon-fit.
 *
 * xterm.js is vendored (see /static/vendor/xterm/VENDOR.md) without addon-fit.
 * We measure the cell box from xterm's render service when available, and fall
 * back to a hidden probe span using the terminal's own font settings.
 */
(function () {
  "use strict";

  var FALLBACK_CELL = { width: 8, height: 17 };

  function measureCell(term, host) {
    // Preferred: xterm's own computed cell dimensions.
    try {
      var dims = term._core && term._core._renderService && term._core._renderService.dimensions;
      var cell = dims && dims.css && dims.css.cell;
      if (cell && cell.width > 0 && cell.height > 0) {
        return { width: cell.width, height: cell.height };
      }
    } catch (_) {
      /* fall through to measurement */
    }

    // Fallback: measure a probe span with the same font as xterm.
    var opts = term.options || {};
    var probe = document.createElement("span");
    probe.textContent = "W".repeat(64);
    probe.setAttribute("aria-hidden", "true");
    probe.style.cssText = [
      "position:absolute",
      "visibility:hidden",
      "top:-9999px",
      "left:-9999px",
      "white-space:pre",
      "padding:0",
      "margin:0",
      "border:0",
      "font-family:" + (opts.fontFamily || "monospace"),
      "font-size:" + (Number(opts.fontSize) || 13) + "px",
      "line-height:" + (opts.lineHeight === undefined ? 1 : opts.lineHeight),
    ].join(";");
    host.appendChild(probe);
    var box = probe.getBoundingClientRect();
    host.removeChild(probe);
    if (box.width > 0 && box.height > 0) {
      return { width: box.width / 64, height: box.height };
    }
    return null;
  }

  function fitTerminal(term, host, onResize) {
    if (!term || !host) return false;
    if (!term.element) return false;

    var cs = getComputedStyle(host);
    var padX = (parseFloat(cs.paddingLeft) || 0) + (parseFloat(cs.paddingRight) || 0);
    var padY = (parseFloat(cs.paddingTop) || 0) + (parseFloat(cs.paddingBottom) || 0);

    // Prefer the xterm element box (inside host padding); fall back to host.
    var el = term.element;
    var width = (el.clientWidth || host.clientWidth - padX);
    var height = (el.clientHeight || host.clientHeight - padY);
    // Account for a vertical scrollbar inside the viewport, if any.
    var scrollbar = el.offsetWidth - width;
    if (scrollbar > 0 && scrollbar < 40) width -= scrollbar;

    if (width <= 0 || height <= 0) return false;

    var cell = measureCell(term, host) || FALLBACK_CELL;
    var cols = Math.max(2, Math.min(500, Math.floor(width / cell.width)));
    var rows = Math.max(2, Math.min(300, Math.floor(height / cell.height)));

    if (cols !== term.cols || rows !== term.rows) {
      try {
        term.resize(cols, rows);
      } catch (_) {
        return false;
      }
      if (typeof onResize === "function") onResize(cols, rows);
    }
    return true;
  }

  /* Observe the terminal host and keep xterm fitted to it. Returns a cleanup fn. */
  function autoFit(term, host, onResize) {
    if (!term || !host || typeof ResizeObserver === "undefined") return function () {};
    var timer = null;
    var raf = 0;

    function schedule() {
      if (timer) clearTimeout(timer);
      timer = setTimeout(function () {
        if (raf) cancelAnimationFrame(raf);
        raf = requestAnimationFrame(function () {
          fitTerminal(term, host, onResize);
        });
      }, 90);
    }

    var ro = new ResizeObserver(schedule);
    ro.observe(host);
    schedule();

    return function () {
      if (timer) clearTimeout(timer);
      if (raf) cancelAnimationFrame(raf);
      ro.disconnect();
    };
  }

  window.GCFit = {
    fitTerminal: fitTerminal,
    autoFit: autoFit,
  };
})();
