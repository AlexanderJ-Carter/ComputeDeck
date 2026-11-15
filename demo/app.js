(() => {
  const slides = [...document.querySelectorAll(".slide")];
  const dots = [...document.querySelectorAll(".dots button")];
  if (!slides.length) return;

  const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  const setActive = (index) => {
    slides.forEach((s, i) => s.classList.toggle("is-in", i === index));
    dots.forEach((d, i) => {
      const on = i === index;
      d.classList.toggle("is-on", on);
      if (on) d.setAttribute("aria-current", "true");
      else d.removeAttribute("aria-current");
    });
  };

  const go = (id) => {
    const el = document.getElementById(id);
    if (!el) return;
    el.scrollIntoView({ behavior: reduce ? "auto" : "smooth", block: "start" });
  };

  dots.forEach((btn) => {
    btn.addEventListener("click", () => go(btn.dataset.go));
  });

  if ("IntersectionObserver" in window) {
    const io = new IntersectionObserver(
      (entries) => {
        const visible = entries
          .filter((e) => e.isIntersecting)
          .sort((a, b) => b.intersectionRatio - a.intersectionRatio)[0];
        if (!visible) return;
        const idx = Number(visible.target.dataset.index || 0);
        setActive(idx);
      },
      { threshold: [0.55, 0.7] }
    );
    slides.forEach((s) => io.observe(s));
  } else {
    setActive(0);
  }

  // Mark first slide in immediately for first paint.
  setActive(0);

  window.addEventListener("keydown", (ev) => {
    const tag = (ev.target && ev.target.tagName) || "";
    if (/INPUT|TEXTAREA|SELECT/.test(tag)) return;
    const current = dots.findIndex((d) => d.classList.contains("is-on"));
    if (ev.key === "ArrowDown" || ev.key === "PageDown" || ev.key === " ") {
      ev.preventDefault();
      const next = Math.min(slides.length - 1, current + 1);
      go(slides[next].id);
    }
    if (ev.key === "ArrowUp" || ev.key === "PageUp") {
      ev.preventDefault();
      const prev = Math.max(0, current - 1);
      go(slides[prev].id);
    }
    if (ev.key === "Home") {
      ev.preventDefault();
      go(slides[0].id);
    }
    if (ev.key === "End") {
      ev.preventDefault();
      go(slides[slides.length - 1].id);
    }
  });
})();
