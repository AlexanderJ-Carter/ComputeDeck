(() => {
  const slides = [...document.querySelectorAll(".slide")];
  const dotsRoot = document.getElementById("dots");
  const progressBar = document.getElementById("progress-bar");
  const playBtn = document.getElementById("btn-play");
  if (!slides.length || !dotsRoot) return;

  const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const AUTO_MS = 6200;
  let current = 0;
  let autoplay = false;
  let autoTimer = 0;
  let userStopped = false;
  let termStarted = false;
  let counted = false;
  let gpuTimer = 0;
  const particleCtl = new Map();

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
    slides.forEach((s, i) => {
      const on = i === index;
      s.classList.toggle("is-in", on);
      s.toggleAttribute("data-active", on);
    });
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
    if (id === "s4") {
      startGpuShuffle();
      animateRing();
    } else stopGpuShuffle();
    if (id === "s9") runTerminal();
    syncParticles(id);
  };

  const goIndex = (index) => {
    const i = Math.max(0, Math.min(slides.length - 1, index));
    slides[i].scrollIntoView({ behavior: reduce ? "auto" : "smooth", block: "start" });
  };

  const go = (id) => {
    const el = document.getElementById(id);
    if (el) el.scrollIntoView({ behavior: reduce ? "auto" : "smooth", block: "start" });
  };

  dots.forEach((btn) =>
    btn.addEventListener("click", () => {
      stopAuto();
      go(btn.dataset.go);
    })
  );

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
      { threshold: [0.45, 0.6, 0.75] }
    );
    slides.forEach((s) => io.observe(s));
  }
  setActive(0);

  window.addEventListener("keydown", (ev) => {
    const tag = (ev.target && ev.target.tagName) || "";
    if (/INPUT|TEXTAREA|SELECT/.test(tag)) return;
    if (ev.key === "ArrowDown" || ev.key === "PageDown" || ev.key === " ") {
      ev.preventDefault();
      stopAuto();
      goIndex(current + 1);
    }
    if (ev.key === "ArrowUp" || ev.key === "PageUp") {
      ev.preventDefault();
      stopAuto();
      goIndex(current - 1);
    }
    if (ev.key === "Home") {
      ev.preventDefault();
      stopAuto();
      goIndex(0);
    }
    if (ev.key === "End") {
      ev.preventDefault();
      stopAuto();
      goIndex(slides.length - 1);
    }
  });

  const stopAuto = () => {
    autoplay = false;
    userStopped = true;
    if (playBtn) {
      playBtn.classList.remove("is-on");
      playBtn.textContent = "自动播放";
      playBtn.setAttribute("aria-pressed", "false");
    }
    window.clearInterval(autoTimer);
  };

  const startAuto = () => {
    if (reduce) return;
    autoplay = true;
    userStopped = false;
    if (playBtn) {
      playBtn.classList.add("is-on");
      playBtn.textContent = "暂停播放";
      playBtn.setAttribute("aria-pressed", "true");
    }
    window.clearInterval(autoTimer);
    autoTimer = window.setInterval(() => {
      const next = current + 1 >= slides.length ? 0 : current + 1;
      goIndex(next);
    }, AUTO_MS);
  };

  playBtn?.addEventListener("click", () => {
    if (autoplay) stopAuto();
    else startAuto();
  });

  const pauseOnInteract = () => {
    if (autoplay) stopAuto();
  };
  window.addEventListener("wheel", pauseOnInteract, { passive: true });
  window.addEventListener("touchstart", pauseOnInteract, { passive: true });

  if (!reduce) {
    window.setTimeout(() => {
      if (!userStopped && !document.hidden) startAuto();
    }, 900);
  }

  function animateRing() {
    const ring = document.querySelector("#s4 .ring i");
    const label = document.querySelector("#s4 .ring b");
    if (!ring || !label) return;
    const target = 78;
    if (reduce) {
      ring.style.setProperty("--p", String(target));
      label.textContent = String(target);
      return;
    }
    const start = performance.now();
    const dur = 900;
    const tick = (now) => {
      const t = Math.min(1, (now - start) / dur);
      const eased = 1 - Math.pow(1 - t, 3);
      const val = Math.round(target * eased);
      ring.style.setProperty("--p", String(val));
      label.textContent = String(val);
      if (t < 1) requestAnimationFrame(tick);
    };
    ring.style.setProperty("--p", "0");
    requestAnimationFrame(tick);
  }

  function startGpuShuffle() {
    const grid = document.getElementById("gpu-grid");
    if (!grid || gpuTimer) return;
    const n = 32;
    const paint = () => {
      const busy = new Set();
      while (busy.size < 8) busy.add(Math.floor(Math.random() * n));
      const frag = document.createDocumentFragment();
      for (let i = 0; i < n; i++) {
        const el = document.createElement("i");
        if (busy.has(i)) el.className = "busy";
        else if (Math.random() > 0.3) el.className = "free";
        frag.appendChild(el);
      }
      grid.replaceChildren(frag);
    };
    paint();
    if (!reduce) gpuTimer = window.setInterval(paint, 2800);
  }

  function stopGpuShuffle() {
    window.clearInterval(gpuTimer);
    gpuTimer = 0;
  }

  const hero = document.getElementById("s1");
  const spot = document.getElementById("hero-spot");
  if (hero && spot && !reduce) {
    let rafSpot = 0;
    hero.addEventListener("pointermove", (ev) => {
      if (rafSpot) return;
      rafSpot = requestAnimationFrame(() => {
        rafSpot = 0;
        const r = hero.getBoundingClientRect();
        spot.style.setProperty("--mx", `${((ev.clientX - r.left) / r.width) * 100}%`);
        spot.style.setProperty("--my", `${((ev.clientY - r.top) / r.height) * 100}%`);
      });
    });
  }

  if (!reduce && window.matchMedia("(pointer: fine)").matches) {
    document.querySelectorAll(".cap-rail li, .stack-steps article, .reli-grid article").forEach((el) => {
      el.addEventListener("pointermove", (ev) => {
        const r = el.getBoundingClientRect();
        const x = (ev.clientX - r.left) / r.width - 0.5;
        const y = (ev.clientY - r.top) / r.height - 0.5;
        el.classList.add("tilt");
        el.style.setProperty("--ry", `${x * 8}deg`);
        el.style.setProperty("--rx", `${-y * 8}deg`);
      });
      el.addEventListener("pointerleave", () => {
        el.classList.remove("tilt");
        el.style.removeProperty("--rx");
        el.style.removeProperty("--ry");
      });
    });
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
      pre.innerHTML = lines.map((l) => `<span class="${l.cls}">${l.text}</span>`).join("\n");
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
    window.setTimeout(() => {
      const cur = document.createElement("span");
      cur.className = "cursor";
      cur.setAttribute("aria-hidden", "true");
      pre.appendChild(cur);
    }, lines.length * 380 + 40);
  }

  function mountParticles(canvas) {
    if (!canvas || reduce) return null;
    const ctx = canvas.getContext("2d", { alpha: true });
    if (!ctx) return null;
    let cssW = 0;
    let cssH = 0;
    let dpr = 1;
    let raf = 0;
    let running = false;
    const count = window.matchMedia("(max-width: 700px)").matches ? 28 : 48;
    const dots = Array.from({ length: count }, () => ({
      x: Math.random(),
      y: Math.random(),
      r: 0.6 + Math.random() * 1.8,
      vx: (Math.random() - 0.5) * 0.00035,
      vy: (Math.random() - 0.5) * 0.00035,
    }));

    const resize = () => {
      const rect = canvas.parentElement.getBoundingClientRect();
      dpr = Math.min(devicePixelRatio || 1, 2);
      cssW = rect.width;
      cssH = rect.height;
      canvas.width = Math.floor(cssW * dpr);
      canvas.height = Math.floor(cssH * dpr);
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    };

    const draw = () => {
      if (!running) return;
      ctx.clearRect(0, 0, cssW, cssH);
      for (const d of dots) {
        d.x += d.vx;
        d.y += d.vy;
        if (d.x < 0 || d.x > 1) d.vx *= -1;
        if (d.y < 0 || d.y > 1) d.vy *= -1;
        ctx.beginPath();
        ctx.fillStyle = "rgba(13, 148, 136, 0.35)";
        ctx.arc(d.x * cssW, d.y * cssH, d.r, 0, Math.PI * 2);
        ctx.fill();
      }
      ctx.strokeStyle = "rgba(15, 118, 110, 0.12)";
      ctx.lineWidth = 1;
      for (let a = 0; a < dots.length; a++) {
        for (let b = a + 1; b < dots.length; b++) {
          const dx = (dots[a].x - dots[b].x) * cssW;
          const dy = (dots[a].y - dots[b].y) * cssH;
          const dist = Math.hypot(dx, dy);
          if (dist < 120) {
            ctx.globalAlpha = 1 - dist / 120;
            ctx.beginPath();
            ctx.moveTo(dots[a].x * cssW, dots[a].y * cssH);
            ctx.lineTo(dots[b].x * cssW, dots[b].y * cssH);
            ctx.stroke();
            ctx.globalAlpha = 1;
          }
        }
      }
      raf = requestAnimationFrame(draw);
    };

    const start = () => {
      if (running) return;
      running = true;
      resize();
      draw();
    };
    const stop = () => {
      running = false;
      cancelAnimationFrame(raf);
      ctx.clearRect(0, 0, canvas.width, canvas.height);
    };

    window.addEventListener("resize", () => {
      if (running) resize();
    });

    return { start, stop };
  }

  function syncParticles(activeId) {
    for (const [id, ctl] of particleCtl) {
      if (id === activeId) ctl.start();
      else ctl.stop();
    }
  }

  const heroCanvas = document.getElementById("hero-canvas");
  const finaleCanvas = document.getElementById("finale-canvas");
  const heroCtl = mountParticles(heroCanvas);
  const finaleCtl = mountParticles(finaleCanvas);
  if (heroCtl) particleCtl.set("s1", heroCtl);
  if (finaleCtl) particleCtl.set("s11", finaleCtl);
  syncParticles("s1");

  document.addEventListener("visibilitychange", () => {
    if (document.hidden) {
      for (const ctl of particleCtl.values()) ctl.stop();
      stopGpuShuffle();
      window.clearInterval(autoTimer);
    } else {
      syncParticles(slides[current]?.id);
      if (slides[current]?.id === "s4") startGpuShuffle();
      if (autoplay && !reduce) {
        window.clearInterval(autoTimer);
        autoTimer = window.setInterval(() => {
          const next = current + 1 >= slides.length ? 0 : current + 1;
          goIndex(next);
        }, AUTO_MS);
      }
    }
  });
})();
