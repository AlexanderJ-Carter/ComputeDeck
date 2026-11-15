(() => {
  const slides = [...document.querySelectorAll(".slide")];
  const dotsRoot = document.getElementById("dots");
  const progressBar = document.getElementById("progress-bar");
  const playBtn = document.getElementById("btn-play");
  if (!slides.length || !dotsRoot) return;

  const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  let current = 0;
  let autoplay = false;
  let autoTimer = 0;
  let termStarted = false;
  let counted = false;

  // Build dots from slides
  dotsRoot.innerHTML = slides
    .map(
      (s, i) =>
        `<button type="button" data-go="${s.id}" aria-label="${s.dataset.title || s.id}"${
          i === 0 ? ' class="is-on" aria-current="true"' : ""
        }></button>`
    )
    .join("");
  const dots = [...dotsRoot.querySelectorAll("button")];

  const setActive = (index) => {
    current = index;
    slides.forEach((s, i) => s.classList.toggle("is-in", i === index));
    dots.forEach((d, i) => {
      const on = i === index;
      d.classList.toggle("is-on", on);
      if (on) d.setAttribute("aria-current", "true");
      else d.removeAttribute("aria-current");
    });
    if (progressBar) {
      progressBar.style.width = `${((index + 1) / slides.length) * 100}%`;
    }
    const id = slides[index]?.id;
    if (id === "s1") runCounters();
    if (id === "s9") runTerminal();
  };

  const goIndex = (index) => {
    const i = Math.max(0, Math.min(slides.length - 1, index));
    slides[i].scrollIntoView({ behavior: reduce ? "auto" : "smooth", block: "start" });
  };

  const go = (id) => {
    const el = document.getElementById(id);
    if (el) el.scrollIntoView({ behavior: reduce ? "auto" : "smooth", block: "start" });
  };

  dots.forEach((btn) => btn.addEventListener("click", () => go(btn.dataset.go)));

  if ("IntersectionObserver" in window) {
    const io = new IntersectionObserver(
      (entries) => {
        const visible = entries
          .filter((e) => e.isIntersecting)
          .sort((a, b) => b.intersectionRatio - a.intersectionRatio)[0];
        if (!visible) return;
        const idx = slides.indexOf(visible.target);
        if (idx >= 0) setActive(idx);
      },
      { threshold: [0.55, 0.7] }
    );
    slides.forEach((s) => io.observe(s));
  }
  setActive(0);

  window.addEventListener("keydown", (ev) => {
    const tag = (ev.target && ev.target.tagName) || "";
    if (/INPUT|TEXTAREA|SELECT/.test(tag)) return;
    if (ev.key === "ArrowDown" || ev.key === "PageDown" || ev.key === " ") {
      ev.preventDefault();
      goIndex(current + 1);
    }
    if (ev.key === "ArrowUp" || ev.key === "PageUp") {
      ev.preventDefault();
      goIndex(current - 1);
    }
    if (ev.key === "Home") {
      ev.preventDefault();
      goIndex(0);
    }
    if (ev.key === "End") {
      ev.preventDefault();
      goIndex(slides.length - 1);
    }
  });

  const stopAuto = () => {
    autoplay = false;
    if (playBtn) {
      playBtn.classList.remove("is-on");
      playBtn.textContent = "自动播放";
    }
    window.clearInterval(autoTimer);
  };

  const startAuto = () => {
    autoplay = true;
    if (playBtn) {
      playBtn.classList.add("is-on");
      playBtn.textContent = "停止播放";
    }
    window.clearInterval(autoTimer);
    autoTimer = window.setInterval(() => {
      const next = current + 1 >= slides.length ? 0 : current + 1;
      goIndex(next);
    }, 5200);
  };

  playBtn?.addEventListener("click", () => {
    if (autoplay) stopAuto();
    else startAuto();
  });

  // Pause autoplay on user scroll/touch
  let scrollQuiet = 0;
  window.addEventListener(
    "wheel",
    () => {
      if (!autoplay) return;
      window.clearTimeout(scrollQuiet);
      scrollQuiet = window.setTimeout(() => {}, 0);
      stopAuto();
    },
    { passive: true }
  );

  // GPU mosaic
  const grid = document.getElementById("gpu-grid");
  if (grid) {
    const n = 32;
    const busy = new Set([2, 3, 8, 9, 10, 16, 17, 24]);
    grid.innerHTML = Array.from({ length: n }, (_, i) => {
      const cls = busy.has(i) ? "busy" : Math.random() > 0.35 ? "free" : "";
      return `<i class="${cls}"></i>`;
    }).join("");
  }

  function runCounters() {
    if (counted || reduce) {
      document.querySelectorAll("[data-count]").forEach((el) => {
        el.textContent = el.dataset.count;
      });
      counted = true;
      return;
    }
    counted = true;
    document.querySelectorAll("[data-count]").forEach((el) => {
      const target = Number(el.dataset.count || 0);
      const start = performance.now();
      const dur = 900;
      const tick = (now) => {
        const t = Math.min(1, (now - start) / dur);
        const eased = 1 - Math.pow(1 - t, 3);
        el.textContent = String(Math.round(target * eased));
        if (t < 1) requestAnimationFrame(tick);
      };
      requestAnimationFrame(tick);
    });
  }

  function runTerminal() {
    if (termStarted) return;
    const pre = document.getElementById("term-lines");
    if (!pre) return;
    termStarted = true;
    const lines = [
      { text: "$ gc_overview(refresh=true)", cls: "muted" },
      { text: "nodes=8 free_gpus=20 leases=3 edge=up", cls: "ok" },
      { text: "$ gc_job_action(job='sft', action='status')", cls: "muted" },
      { text: "READY · gpu-1,gpu-2 · gpus_per_node=8", cls: "ok" },
      { text: "$ gc_free_node(node='gpu-3', wait=30)", cls: "muted" },
      { text: "stopped · procs cleared · lease released", cls: "ok" },
      { text: "$ gc_deploy(job='vllm')  # unit_gpus from cluster", cls: "muted" },
      { text: "selected=[gpu-5,gpu-6] · wait=ready · lease ok", cls: "ok" },
    ];
    if (reduce) {
      pre.innerHTML = lines
        .map((l) => `<span class="${l.cls}">${l.text}</span>`)
        .join("\n");
      return;
    }
    let i = 0;
    pre.textContent = "";
    const step = () => {
      if (i >= lines.length) return;
      const line = lines[i++];
      const span = document.createElement("span");
      span.className = line.cls;
      span.textContent = line.text;
      pre.appendChild(span);
      pre.appendChild(document.createTextNode("\n"));
      window.setTimeout(step, 380);
    };
    step();
  }

  // Soft particle field on hero / finale
  function mountParticles(canvas) {
    if (!canvas || reduce) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    let w = 0;
    let h = 0;
    let raf = 0;
    const dots = Array.from({ length: 48 }, () => ({
      x: Math.random(),
      y: Math.random(),
      r: 0.6 + Math.random() * 1.8,
      vx: (Math.random() - 0.5) * 0.00035,
      vy: (Math.random() - 0.5) * 0.00035,
    }));

    const resize = () => {
      const rect = canvas.parentElement.getBoundingClientRect();
      w = canvas.width = Math.floor(rect.width * devicePixelRatio);
      h = canvas.height = Math.floor(rect.height * devicePixelRatio);
      ctx.setTransform(devicePixelRatio, 0, 0, devicePixelRatio, 0, 0);
    };

    const draw = () => {
      const cw = w / devicePixelRatio;
      const ch = h / devicePixelRatio;
      ctx.clearRect(0, 0, cw, ch);
      for (const d of dots) {
        d.x += d.vx;
        d.y += d.vy;
        if (d.x < 0 || d.x > 1) d.vx *= -1;
        if (d.y < 0 || d.y > 1) d.vy *= -1;
        ctx.beginPath();
        ctx.fillStyle = "rgba(13, 148, 136, 0.35)";
        ctx.arc(d.x * cw, d.y * ch, d.r, 0, Math.PI * 2);
        ctx.fill();
      }
      // faint links
      ctx.strokeStyle = "rgba(15, 118, 110, 0.12)";
      ctx.lineWidth = 1;
      for (let a = 0; a < dots.length; a++) {
        for (let b = a + 1; b < dots.length; b++) {
          const dx = (dots[a].x - dots[b].x) * cw;
          const dy = (dots[a].y - dots[b].y) * ch;
          const dist = Math.hypot(dx, dy);
          if (dist < 120) {
            ctx.globalAlpha = 1 - dist / 120;
            ctx.beginPath();
            ctx.moveTo(dots[a].x * cw, dots[a].y * ch);
            ctx.lineTo(dots[b].x * cw, dots[b].y * ch);
            ctx.stroke();
            ctx.globalAlpha = 1;
          }
        }
      }
      raf = requestAnimationFrame(draw);
    };

    resize();
    draw();
    window.addEventListener("resize", resize);
    return () => {
      cancelAnimationFrame(raf);
      window.removeEventListener("resize", resize);
    };
  }

  mountParticles(document.getElementById("hero-canvas"));
  mountParticles(document.getElementById("finale-canvas"));
})();
