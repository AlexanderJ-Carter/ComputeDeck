const state = {
  overview: null,
  selectedNode: null,
  etag: null,
  busy: false,
  autoTimer: null,
  view: "actions",
  quality: null,
  refreshInFlight: false,
  lb: null,
  models: null,
};

const VIEW_TITLE = {
  actions: "一键运维",
  cluster: "总览",
  nodes: "节点",
  workloads: "任务",
  models: "模型",
  endpoints: "API 端点",
  ops: "命令",
  terminal: "终端",
  guide: "指南",
  config: "配置",
};

const VIEW_CLI = {
  actions: "gpu-control doctor && gpu-control health",
  cluster: "gpu-control get nodes",
  nodes: "gpu-control node render",
  workloads: "gpu-control get jobs",
  models: "gpu-control models deps && gpu-control models verify",
  endpoints: "gpu-control health",
  ops: "gpu-control exec --nodes all -- ...",
  terminal: "ssh <node>  # Web 内置 PTY",
  guide: "gpu-control doctor",
  config: "gpu-control doctor",
};

const termState = {
  term: null,
  fit: null,
  ws: null,
  node: null,
};

const stateSetup = { data: null };

function applyTheme(theme) {
  const next = theme === "dark" ? "dark" : "light";
  document.documentElement.setAttribute("data-theme", next);
  localStorage.setItem("gpu-control-theme", next);
  document.querySelectorAll("[data-theme-set]").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.themeSet === next);
  });
}

function initTheme() {
  const saved = localStorage.getItem("gpu-control-theme");
  const theme =
    saved || (window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
  applyTheme(theme);
  document.querySelectorAll("[data-theme-set]").forEach((btn) => {
    btn.addEventListener("click", () => applyTheme(btn.dataset.themeSet));
  });
}

const setBusy = (busy) => {
  state.busy = busy;
  document.body.classList.toggle("is-busy", busy);
  document.getElementById("busy-flag").textContent = busy ? "执行中…" : "";
  document.querySelectorAll("button").forEach((btn) => {
    if (btn.dataset.keepEnabled === "1") return;
    btn.disabled = busy;
  });
};

function toast(text) {
  const el = document.getElementById("toast");
  el.hidden = false;
  el.textContent = text;
  clearTimeout(toast._t);
  toast._t = setTimeout(() => {
    el.hidden = true;
  }, 2400);
}

function log(text) {
  const el = document.getElementById("log");
  const stamp = new Date().toLocaleTimeString();
  el.textContent = `[${stamp}] ${text}\n` + el.textContent;
}

function escapeHtml(s) {
  return String(s)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;");
}

function revealConsole() {
  const wrap = document.getElementById("batch-console-wrap") || document.querySelector(".console-wrap");
  if (wrap) wrap.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

function setConsoleMeta(text) {
  const meta = document.getElementById("console-meta");
  if (meta) meta.textContent = text;
}

function writeConsoleHtml(html, { empty = false, meta = "" } = {}) {
  const el = document.getElementById("console");
  if (!el) return;
  el.classList.toggle("empty", !!empty);
  el.innerHTML = html;
  if (meta) setConsoleMeta(meta);
  else setConsoleMeta(empty ? "空" : `已更新 · ${new Date().toLocaleTimeString()}`);
  revealConsole();
}

function writeConsoleText(text, { empty = false, meta = "" } = {}) {
  const el = document.getElementById("console");
  if (!el) return;
  el.classList.toggle("empty", !!empty);
  el.textContent = text || "(empty)";
  if (meta) setConsoleMeta(meta);
  else setConsoleMeta(empty ? "空" : `已更新 · ${new Date().toLocaleTimeString()}`);
  revealConsole();
}

function resultOk(item) {
  if (typeof item?.ok === "boolean") return item.ok;
  if (item?.rc !== undefined && item?.rc !== null) return Number(item.rc) === 0;
  return false;
}

function resultBody(item) {
  const parts = [item?.stdout, item?.stderr, item?.detail].filter(Boolean);
  if (parts.length) return parts.join("\n").trim();
  if (item?.latency_ms != null) return `latency ${item.latency_ms}ms`;
  return "(empty)";
}

function resultMeta(item) {
  const bits = [];
  if (item?.rc !== undefined && item?.rc !== null) bits.push(`rc=${item.rc}`);
  else bits.push(resultOk(item) ? "ok" : "fail");
  if (item?.latency_ms != null) bits.push(`${item.latency_ms}ms`);
  if (item?.host) bits.push(item.host);
  return bits.join(" · ");
}

function showResults(results, command) {
  if (!results || !results.length) {
    writeConsoleHtml(
      `<div class="node-block"><header class="fail">无结构化结果</header><pre>${escapeHtml(
        command || ""
      )}</pre></div>`,
      { meta: "无结果" }
    );
    return;
  }
  writeConsoleHtml(
    results
      .map((item) => {
        const ok = resultOk(item);
        return `<div class="node-block">
        <header class="${ok ? "ok" : "fail"}"><span>${escapeHtml(item.node || "?")}</span><span>${escapeHtml(resultMeta(item))}</span></header>
        <pre>${escapeHtml(resultBody(item))}</pre>
      </div>`;
      })
      .join(""),
    { meta: `${results.length} 个节点` }
  );
}

function formatStepBody(step) {
  if (step?.error) return String(step.error);
  const p = step?.payload;
  if (p == null) return "(无返回)";
  if (Array.isArray(p.results) && p.results.length) {
    return p.results
      .map((r) => {
        const mark = resultOk(r) ? "OK" : "FAIL";
        const label = r.node || r.host || "?";
        return `[${mark}] ${label}\n${resultBody(r)}`;
      })
      .join("\n\n");
  }
  if (p.result && typeof p.result === "object") {
    const nested = [p.result.stdout, p.result.stderr].filter(Boolean).join("\n").trim();
    if (nested) return nested;
  }
  if (p.stdout || p.stderr) {
    return [p.stdout, p.stderr].filter(Boolean).join("\n").trim() || `rc=${p.rc}`;
  }
  if (Array.isArray(p.issues) || Array.isArray(p.notes)) {
    const lines = [];
    lines.push(p.ok ? "doctor: OK" : "doctor: FAIL");
    for (const issue of p.issues || []) lines.push(`! ${issue}`);
    for (const note of p.notes || []) lines.push(`· ${note}`);
    return lines.join("\n");
  }
  try {
    return JSON.stringify(p, null, 2);
  } catch {
    return String(p);
  }
}

function showSteps(steps, title) {
  if (!steps || !steps.length) {
    writeConsoleHtml(
      `<div class="node-block"><header class="fail">${escapeHtml(
        title || "动作"
      )}</header><pre>无步骤输出</pre></div>`,
      { meta: "无步骤" }
    );
    return;
  }
  const rich = steps.find(
    (s) => s.payload && Array.isArray(s.payload.results) && s.payload.results.length
  );
  if (rich && steps.length === 1) {
    showResults(rich.payload.results, rich.name);
    return;
  }
  writeConsoleHtml(
    steps
      .map((step) => {
        const ok = !!step.ok;
        return `<div class="node-block">
        <header class="${ok ? "ok" : "fail"}"><span>${escapeHtml(step.name || "?")}</span><span>${
          ok ? "成功" : "失败"
        } · ${escapeHtml(String(step.ms ?? "-"))}ms</span></header>
        <pre>${escapeHtml(formatStepBody(step))}</pre>
      </div>`;
      })
      .join(""),
    { meta: `${steps.length} 步 · ${title || "动作"}` }
  );
}

async function seedConsole() {
  try {
    const doctor = await api("/api/doctor");
    const lines = [];
    lines.push(doctor.ok ? "Doctor OK" : "Doctor FAIL");
    for (const issue of doctor.issues || []) lines.push(`! ${issue}`);
    for (const note of (doctor.notes || []).slice(0, 12)) lines.push(`· ${note}`);
    lines.push("");
    lines.push("点上方「一键连通 / 一键就绪 / 刷新 GPU」后，完整 stdout 会写在这里。");
    writeConsoleText(lines.join("\n"), { meta: doctor.ok ? "Doctor OK" : "Doctor 有问题" });
    log(doctor.ok ? "Doctor 通过，控制台已就绪" : "Doctor 有问题，详见批量输出");
  } catch (err) {
    writeConsoleText(
      `控制台已就绪，但 Doctor 失败：${err.message || err}\n\n点「跑一键连通」或上方运维卡片查看输出。`,
      { meta: "Doctor 失败" }
    );
  }
}

async function api(path, options = {}) {
  const headers = { "Content-Type": "application/json", ...(options.headers || {}) };
  const res = await fetch(path, { ...options, headers });
  if (res.status === 304) return { __notModified: true, etag: res.headers.get("ETag") };
  const data = await res.json();
  if (!res.ok) {
    const detail = data.results
      ? JSON.stringify(data.results, null, 2)
      : data.result
        ? `${data.result.stdout || ""}\n${data.result.stderr || ""}`.trim()
        : data.error || JSON.stringify(data);
    throw new Error(detail || `HTTP ${res.status}`);
  }
  if (res.headers.get("ETag")) data.__etag = res.headers.get("ETag");
  return data;
}

function selectedFrom(selectId) {
  const val = document.getElementById(selectId).value;
  if (val && val !== "all") return { node: val };
  return { nodes: "all" };
}

function fillSelectPreserve(el, html, prefer) {
  if (!el) return;
  const prev = el.value;
  el.innerHTML = html;
  const options = [...el.options].map((o) => o.value);
  if (options.includes(prev)) el.value = prev;
  else if (prefer && options.includes(prefer)) el.value = prefer;
}

function fillTargetSelects(nodes) {
  const optsAll =
    `<option value="all">全部节点</option>` +
    nodes.map((n) => `<option value="${n.name}">${n.name}</option>`).join("");
  const optsNode = nodes.map((n) => `<option value="${n.name}">${n.name}</option>`).join("");
  for (const id of ["exec-target", "py-target", "job-target"]) {
    fillSelectPreserve(document.getElementById(id), optsAll, state.selectedNode);
  }
  fillSelectPreserve(
    document.getElementById("deploy-node"),
    optsNode || `<option value="">暂无节点</option>`,
    state.selectedNode
  );
  fillSelectPreserve(
    document.getElementById("term-node"),
    optsNode || `<option value="">暂无节点</option>`,
    state.selectedNode
  );
  const modelNode = document.getElementById("model-node");
  if (modelNode) {
    const prev = modelNode.value;
    modelNode.innerHTML =
      `<option value="">全部节点</option>` +
      nodes.map((n) => `<option value="${n.name}">${n.name}</option>`).join("");
    if ([...modelNode.options].some((o) => o.value === prev)) modelNode.value = prev;
    else if (state.selectedNode && [...modelNode.options].some((o) => o.value === state.selectedNode)) {
      modelNode.value = state.selectedNode;
    }
  }
  syncDeployMode();
}

function syncDeployMode() {
  const mode = document.getElementById("deploy-mode");
  if (!mode) return;
  const byNode = mode.value === "node";
  const gpusWrap = document.getElementById("deploy-gpus-wrap");
  const nodeWrap = document.getElementById("deploy-node-wrap");
  if (gpusWrap) gpusWrap.hidden = byNode;
  if (nodeWrap) nodeWrap.hidden = !byNode;
}

function renderSummary(summary) {
  document.getElementById("stat-nodes").textContent = summary.nodes;
  document.getElementById("stat-free").textContent = summary.gpus_free;
  document.getElementById("stat-used").textContent = summary.gpus_used;
  document.getElementById("stat-pending").textContent = summary.queue_pending ?? 0;
  document.getElementById("stat-cordoned").textContent = summary.cordoned ?? 0;
  const ok = summary.api_ok;
  const total = summary.api_total;
  document.getElementById("stat-api").textContent =
    ok == null || total == null ? "-" : `${ok}/${total}`;
}

function renderPresets(presets) {
  const root = document.getElementById("exec-presets");
  root.innerHTML = (presets || [])
    .map((p) => {
      const id = typeof p === "string" ? p : p.id;
      const label = typeof p === "string" ? p : p.label || p.id;
      return `<button type="button" data-preset="${id}">${label}</button>`;
    })
    .join("");
  root.querySelectorAll("button[data-preset]").forEach((btn) => {
    btn.addEventListener("click", () => runExec({ preset: btn.dataset.preset }));
  });
}

function activeSnippet(lb) {
  const kind = document.getElementById("snippet-kind")?.value || "python";
  const snippets = (lb && lb.snippets) || {};
  if (snippets[kind]) return snippets[kind];
  if (kind === "gateway_notes" && snippets.litellm_notes) return snippets.litellm_notes;
  if (kind === "python") return (lb && lb.snippet) || "";
  return snippets.python || (lb && lb.snippet) || "";
}

function renderGatewayTopo(lb, endpointsCatalog) {
  const el = document.getElementById("gateway-topo");
  if (!el) return;
  const topo = (lb && lb.topology) || {};
  const pool = topo.inference_pool || {};
  const local = topo.local_services || {};
  const byNode = pool.by_node || (endpointsCatalog && endpointsCatalog.by_node) || {};
  const nodeBits = Object.entries(byNode).map(([name, info]) => {
    const range = info.range || (info.ports || []).join(",") || `${info.base_port}+${info.ports_per_node}`;
    return `${name} ${range}`;
  });
  const nginx = local.nginx || {};
  const proxy = local.openai_compat_proxy || local.litellm_gateway || {};
  const extra = local.extra_http || local.legacy_vllm || {};
  const bits = [
    nodeBits.length ? `按节点 ${nodeBits.join(" · ")}` : `默认端口 ${(pool.port_range || []).join("-") || "-"}`,
  ];
  if (proxy.port) bits.push(`proxy :${proxy.port}`);
  if (extra.port) bits.push(`extra :${extra.port}`);
  bits.push(`nginx ${nginx.installed ? "已装" : "未装"}`);
  el.textContent = bits.join(" · ");
}

function renderPool(overview) {
  const catalog = overview.endpoints || {};
  const endpoints = catalog.endpoints || [];
  const health = overview.endpoint_health || {};
  const byKey = {};
  for (const r of health.results || []) byKey[`${r.node}:${r.gpu}`] = r;
  const mosaic = document.getElementById("api-mosaic");
  if (!endpoints.length) {
    mosaic.innerHTML = '<div class="meta">暂无端点</div>';
  } else {
    const groups = new Map();
    for (const ep of endpoints) {
      const key = ep.node || "?";
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push(ep);
    }
    mosaic.innerHTML = [...groups.entries()]
      .map(([node, eps]) => {
        const okN = eps.filter((ep) => byKey[`${ep.node}:${ep.gpu}`]?.ok).length;
        const ports = eps.map((ep) => {
          const h = byKey[`${ep.node}:${ep.gpu}`];
          const cls = !h ? "" : h.ok ? "ok" : "fail";
          const lat = h ? `${h.latency_ms}ms` : "n/a";
          return `<div class="api-cell ${cls}">
              <div class="u">:${escapeHtml(String(ep.port))}</div>
              <div class="t">g${escapeHtml(String(ep.gpu))} · ${escapeHtml(lat)}</div>
            </div>`;
        });
        return `<div class="api-node-row">
            <div class="api-node-label">
              <strong>${escapeHtml(node)}</strong>
              <span class="meta">${eps.length} 路 · ${okN}/${eps.length}</span>
            </div>
            <div class="api-node-ports">${ports.join("")}</div>
          </div>`;
      })
      .join("");
  }
  const byNode = catalog.by_node || {};
  const hetero = Object.values(byNode).map((x) => x.ports_per_node);
  const heteroNote =
    hetero.length && new Set(hetero).size > 1 ? " · 异构端口" : "";
  document.getElementById("pool-meta").textContent = health.generated_at
    ? `${health.ok_count}/${health.total} 正常 · ${health.mode || "-"} · ${health.generated_at}${heteroNote}`
    : `端点 ${endpoints.length}${heteroNote}`;
  const lb = overview.lb;
  state.lb = lb || null;
  renderGatewayTopo(lb, catalog);
  if (lb) {
    document.getElementById("client-snippet").textContent = activeSnippet(lb);
    document.getElementById("lb-meta").textContent =
      `策略 ${lb.strategy} · 健康 ${lb.healthy}/${lb.total}` +
      (lb.health_at ? ` · ${lb.health_at}` : "");
  } else {
    const urls = endpoints.map((e) => e.url);
    document.getElementById("client-snippet").textContent = urls.length
      ? `# 客户端轮询端点\nENDPOINTS = ${JSON.stringify(urls, null, 2)}`
      : "";
  }
}

function renderHostMetrics(overview) {
  const root = document.getElementById("host-metrics");
  if (!root) return;
  const nodes = overview.nodes || [];
  const at = (overview.summary && overview.summary.host_metrics_at) || (overview.host_metrics || {}).generated_at;
  document.getElementById("metrics-at").textContent = at ? `采集于 ${at}` : "尚未采集，点「刷新主机指标」";
  if (!nodes.length) {
    root.innerHTML = '<p class="muted">暂无节点</p>';
    return;
  }
  root.innerHTML = nodes
    .map((n) => {
      const memPct =
        n.mem_total_mb > 0 ? Math.min(100, Math.round((100 * (n.mem_used_mb || 0)) / n.mem_total_mb)) : 0;
      const cpu = n.cpu_percent != null ? Math.min(100, Math.round(n.cpu_percent)) : 0;
      return `<div class="metric-card">
        <div class="name">${escapeHtml(n.name)}</div>
        <div class="row"><span>CPU</span><b>${n.cpu_percent ?? "-"}%</b></div>
        <div class="metric-bar"><span style="width:${cpu}%"></span></div>
        <div class="row"><span>负载</span><b>${n.load1 ?? "-"}</b></div>
        <div class="row"><span>内存</span><b>${n.mem_used_mb ?? "-"} / ${n.mem_total_mb ?? "-"} MB</b></div>
        <div class="metric-bar"><span style="width:${memPct}%"></span></div>
        <div class="row"><span>操作</span><button class="tiny primary" data-open-term="${escapeHtml(
          n.name
        )}" data-keep-enabled="1">终端</button></div>
      </div>`;
    })
    .join("");
  root.querySelectorAll("[data-open-term]").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.getElementById("term-node").value = btn.dataset.openTerm;
      connectTerminal();
    });
  });
}

function ensureTerminal() {
  if (termState.term) return termState.term;
  if (typeof Terminal === "undefined") {
    toast("xterm 未加载，请检查网络/CDN");
    return null;
  }
  const term = new Terminal({
    cursorBlink: true,
    fontFamily: '"IBM Plex Mono", Menlo, Consolas, monospace',
    fontSize: 13,
    theme: {
      background: "#0b1210",
      foreground: "#d5e8df",
      cursor: "#2ec4b6",
      selectionBackground: "rgba(46, 196, 182, 0.28)",
    },
  });
  let fit = null;
  if (typeof FitAddon !== "undefined") {
    fit = new FitAddon.FitAddon();
    term.loadAddon(fit);
  }
  term.open(document.getElementById("term"));
  termState.term = term;
  termState.fit = fit;
  term.onData((data) => {
    if (termState.ws && termState.ws.readyState === WebSocket.OPEN) {
      termState.ws.send(JSON.stringify({ type: "input", data }));
    }
  });
  window.addEventListener("resize", () => {
    if (termState.fit) termState.fit.fit();
    sendTermResize();
  });
  return term;
}

function sendTermResize() {
  if (!termState.term || !termState.ws || termState.ws.readyState !== WebSocket.OPEN) return;
  termState.ws.send(
    JSON.stringify({
      type: "resize",
      cols: termState.term.cols,
      rows: termState.term.rows,
    })
  );
}

function disconnectTerminal() {
  if (termState.ws) {
    try {
      termState.ws.close();
    } catch (_) { }
  }
  termState.ws = null;
  termState.node = null;
  document.getElementById("term-status").textContent = "已断开";
}

function connectTerminal() {
  const node = document.getElementById("term-node").value;
  if (!node) return toast("请选择节点");
  const term = ensureTerminal();
  if (!term) return;
  disconnectTerminal();
  if (termState.fit) termState.fit.fit();
  term.reset();
  term.writeln(`\r\n\x1b[36m连接 ${node} …\x1b[0m`);
  const cols = term.cols || 120;
  const rows = term.rows || 32;
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  const url = `${proto}//${location.host}/ws/terminal?node=${encodeURIComponent(node)}&cols=${cols}&rows=${rows}`;
  const ws = new WebSocket(url);
  ws.binaryType = "arraybuffer";
  termState.ws = ws;
  termState.node = node;
  document.getElementById("term-status").textContent = `正在连接 ${node}…`;
  ws.onopen = () => {
    document.getElementById("term-status").textContent = `已连接 ${node}（交互 PTY）`;
    sendTermResize();
    term.focus();
  };
  ws.onmessage = (ev) => {
    if (typeof ev.data === "string") {
      try {
        const msg = JSON.parse(ev.data);
        if (msg.type === "status") {
          document.getElementById("term-status").textContent =
            `已连接 ${msg.node} · ${msg.user}@${msg.host}`;
          return;
        }
      } catch (_) { }
      term.write(ev.data);
      return;
    }
    term.write(new Uint8Array(ev.data));
  };
  ws.onclose = () => {
    document.getElementById("term-status").textContent = `连接已关闭（${node}）`;
    term.writeln("\r\n\x1b[31m[会话结束]\x1b[0m");
  };
  ws.onerror = () => {
    document.getElementById("term-status").textContent = `连接失败（${node}）`;
    toast("终端连接失败");
  };
}

async function refreshMetrics() {
  setBusy(true);
  try {
    const data = await api("/api/metrics/refresh", { method: "POST", body: "{}" });
    if (data.overview) applyOverview(data.overview);
    toast(`主机指标 ${data.metrics?.ok_count ?? "-"}/${data.metrics?.total ?? "-"}`);
  } catch (err) {
    log(String(err.message || err));
  } finally {
    setBusy(false);
  }
}

async function refreshLb() {
  const strategy = document.getElementById("lb-strategy").value || "least_latency";
  try {
    const data = await api(`/api/endpoints/lb?strategy=${encodeURIComponent(strategy)}`);
    state.lb = data;
    renderGatewayTopo(data);
    document.getElementById("client-snippet").textContent = activeSnippet(data);
    document.getElementById("lb-meta").textContent =
      `策略 ${data.strategy} · 健康 ${data.healthy}/${data.total}` +
      (data.health_at ? ` · ${data.health_at}` : "");
    toast("负载计划已更新");
  } catch (err) {
    log(String(err.message || err));
  }
}

async function renderGatewayFiles() {
  setBusy(true);
  try {
    const data = await api("/api/gateway/render", { method: "POST", body: "{}" });
    state.lb = { ...(state.lb || {}), snippets: data.snippets, topology: data.topology };
    renderGatewayTopo(state.lb);
    document.getElementById("client-snippet").textContent = activeSnippet(state.lb);
    const written = data.written || {};
    toast(`已写出 ${Object.keys(written).length} 个网关文件 → state/gateway/`);
    log(Object.entries(written).map(([k, v]) => `${k}: ${v}`).join("\n") || "gateway rendered");
  } catch (err) {
    toast(String(err.message || err));
  } finally {
    setBusy(false);
  }
}

function renderNodes(nodes) {
  const root = document.getElementById("nodes");
  root.innerHTML = nodes
    .map((node) => {
      const selected = state.selectedNode === node.name ? "selected" : "";
      const st = node.state || (node.cordoned ? "cordoned" : node.used ? "busy" : "free");
      const badge = node.cordoned
        ? `<span class="chip warn">已隔离</span>`
        : node.lease
          ? `<span class="chip warn">${node.lease.job}</span>`
          : node.used
            ? `<span class="chip warn">占用中</span>`
            : `<span class="chip ok">空闲</span>`;
      const bars = (node.gpus || [])
        .map((g) => `<div class="bar ${g.status}"></div>`)
        .join("");
      return `<div class="node-tile ${selected} state-${st}" data-select="${node.name}">
        <div class="name">${node.name}</div>
        <div class="host">${node.host || "-"}</div>
        <div class="stats"><span class="meta">空闲 ${node.free}/${node.total}</span>${badge}</div>
        <div class="meta" style="margin-top:6px">CPU ${node.cpu_percent ?? "-"}% · 负载 ${node.load1 ?? "-"} · 内存 ${
          node.mem_used_mb != null ? Math.round(node.mem_used_mb / 1024) + "G" : "-"
        }</div>
        <div class="bars">${bars || '<div class="meta">暂无快照</div>'}</div>
      </div>`;
    })
    .join("");
  root.querySelectorAll("[data-select]").forEach((el) => {
    el.addEventListener("click", () => selectNode(el.dataset.select));
  });
  renderNodeAdmin(nodes);
  renderDrawer();
}

function renderNodeAdmin(nodes) {
  document.getElementById("node-admin").innerHTML = nodes
    .map(
      (n) => `<div class="item">
        <div><strong>${n.name}</strong><div class="meta">${n.host || "-"} · ${(n.labels || []).join(",") || "-"}</div></div>
        <button data-remove="${n.name}" class="danger tiny">移除</button>
      </div>`
    )
    .join("");
  bindRemoveButtons(document.getElementById("node-admin"));
}

function renderDrawer() {
  const body = document.getElementById("drawer-body");
  const name = state.selectedNode;
  if (!name || !state.overview) {
    body.innerHTML =
      '<p class="muted">点选左侧瓦片查看详情。<br/>任务启停请到「任务」页选目标节点。</p>';
    return;
  }
  const node = (state.overview.nodes || []).find((n) => n.name === name);
  if (!node) return;
  const cordon = node.cordon || {};
  const procs = [];
  for (const g of node.gpus || []) {
    for (const p of g.processes || []) {
      procs.push({
        gpu: g.index,
        pid: p.pid,
        name: p.name,
        memory_mib: p.memory_mib,
      });
    }
  }
  const procRows = procs.length
    ? `<table class="table"><thead><tr><th>GPU</th><th>PID</th><th>MEM</th><th>NAME</th><th></th></tr></thead><tbody>
        ${procs
          .map(
            (p) => `<tr>
              <td>${escapeHtml(String(p.gpu))}</td>
              <td>${escapeHtml(String(p.pid))}</td>
              <td>${escapeHtml(String(p.memory_mib))}MiB</td>
              <td class="meta">${escapeHtml(p.name || "-")}</td>
              <td><button class="danger tiny" data-kill-pid="${p.pid}" data-kill-node="${escapeHtml(
                node.name
              )}">结束</button></td>
            </tr>`
          )
          .join("")}
      </tbody></table>`
    : '<p class="muted tiny">当前快照无 GPU compute 进程</p>';
  const actions = node.cordoned
    ? `<button data-uncordon="${node.name}">取消隔离</button>
       <button data-free="${node.name}" class="danger">腾空</button>`
    : `<button data-cordon="${node.name}">隔离</button>
       <button data-free="${node.name}" class="danger">腾空</button>
       <button data-free-cordon="${node.name}" class="danger">腾空并隔离</button>`;
  body.innerHTML = `
    <h3>${escapeHtml(node.name)}</h3>
    <div class="kv"><b>地址</b> ${escapeHtml(node.host || "-")}</div>
    <div class="kv"><b>角色</b> ${escapeHtml(node.role || "-")}</div>
    <div class="kv"><b>标签</b> ${escapeHtml((node.labels || []).join(", ") || "-")}</div>
    <div class="kv"><b>状态</b> ${node.cordoned ? "已隔离" : node.used ? "占用中" : "可调度"}</div>
    <div class="kv"><b>GPU</b> 空闲 ${node.free} / 占用 ${node.used} / 共 ${node.total}</div>
    <div class="kv"><b>CPU</b> ${node.cpu_percent ?? "-"}% · 负载 ${node.load1 ?? "-"} / ${node.cores ?? "-"} 核</div>
    <div class="kv"><b>内存</b> ${node.mem_used_mb ?? "-"} / ${node.mem_total_mb ?? "-"} MB</div>
    <div class="kv"><b>占用</b> ${escapeHtml(node.lease ? node.lease.job + " / " + node.lease.id : "-")}</div>
    <div class="kv"><b>隔离原因</b> ${escapeHtml(node.cordoned ? (cordon.reason || "cordoned") : "-")}</div>
    <div class="kv"><b>备注</b> ${escapeHtml(node.note || "-")}</div>
    <h3 class="sub">GPU 进程</h3>
    ${procRows}
    <div class="btn-row" style="margin:8px 0">
      <button data-procs-refresh="${node.name}" data-keep-enabled="1">刷新进程</button>
      <button data-procs-kill-all="${node.name}" class="danger">结束全部 GPU 进程</button>
    </div>
    <p class="muted tiny">腾空 = 停注册 Job + 结束 GPU 进程 + 释 lease + 校验空闲。</p>
    <div class="actions">${actions}
      <button data-open-term="${node.name}" class="primary">打开终端</button>
    </div>
    <p class="meta" style="margin-top:12px">CLI: gpu-control procs list --node ${escapeHtml(
      node.name
    )} --refresh<br/>gpu-control procs kill --node ${escapeHtml(node.name)} --all<br/>gpu-control free --node ${escapeHtml(
      node.name
    )}</p>`;
  bindNodeActions(body);
  body.querySelectorAll("[data-open-term]").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.getElementById("term-node").value = btn.dataset.openTerm;
      setView("terminal");
      connectTerminal();
    });
  });
}

function selectNode(name, { stay = false } = {}) {
  state.selectedNode = name;
  for (const id of ["exec-target", "py-target", "job-target", "deploy-node", "term-node"]) {
    const el = document.getElementById(id);
    if (el && [...el.options].some((o) => o.value === name)) el.value = name;
  }
  const mode = document.getElementById("deploy-mode");
  if (mode) {
    mode.value = "node";
    syncDeployMode();
  }
  if (state.overview) renderNodes(state.overview.nodes);
  log(`已选中 ${name}`);
  if (!stay) setView("cluster");
}

function bindRemoveButtons(root) {
  root.querySelectorAll("button[data-remove]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      if (!confirm(`确认移除节点 ${btn.dataset.remove}？`)) return;
      setBusy(true);
      try {
        const data = await api("/api/node/remove", {
          method: "POST",
          body: JSON.stringify({ name: btn.dataset.remove }),
        });
        if (state.selectedNode === btn.dataset.remove) state.selectedNode = null;
        log(data.result.stdout || "已移除");
        await refresh(false);
      } catch (err) {
        log(String(err.message || err));
      } finally {
        setBusy(false);
      }
    });
  });
}

function bindNodeActions(root) {
  const wrap = async (label, fn) => {
    setBusy(true);
    try {
      log(label);
      await fn();
      await refresh(false);
    } catch (err) {
      log(String(err.message || err));
    } finally {
      setBusy(false);
    }
  };
  const showCliText = (data, fallback) => {
    const text = [data.result?.stdout, data.result?.stderr].filter(Boolean).join("\n") || fallback;
    writeConsoleText(text, { meta: "节点动作" });
    log(text.split("\n").slice(-4).join(" | "));
    return text;
  };
  const runFree = async (node, opts = {}) => {
    const data = await api("/api/free", {
      method: "POST",
      body: JSON.stringify({
        node,
        wait: 20,
        ...opts,
      }),
    });
    const text = showCliText(data, "free done");
    if (data.overview) applyOverview(data.overview);
    if (data.result?.rc !== 0) throw new Error(text || "腾空未完全空闲");
    toast(opts.cordon ? "已腾空并隔离" : "已腾空");
  };
  root.querySelectorAll("button[data-procs-refresh]").forEach((btn) => {
    btn.addEventListener("click", () => {
      wrap(`procs ${btn.dataset.procsRefresh}`, async () => {
        const data = await api("/api/procs", {
          method: "POST",
          body: JSON.stringify({ node: btn.dataset.procsRefresh, refresh: true }),
        });
        showCliText(data, "procs listed");
        if (data.overview) applyOverview(data.overview);
        toast("进程已刷新");
      });
    });
  });
  root.querySelectorAll("button[data-procs-kill-all]").forEach((btn) => {
    btn.addEventListener("click", () => {
      if (!confirm(`确认结束 ${btn.dataset.procsKillAll} 上全部 GPU 进程？`)) return;
      wrap(`procs kill --all ${btn.dataset.procsKillAll}`, async () => {
        const data = await api("/api/procs/kill", {
          method: "POST",
          body: JSON.stringify({ node: btn.dataset.procsKillAll, all: true }),
        });
        showCliText(data, "killed");
        if (data.overview) applyOverview(data.overview);
        toast("已结束 GPU 进程");
      });
    });
  });
  root.querySelectorAll("button[data-kill-pid]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const pid = Number(btn.dataset.killPid);
      const node = btn.dataset.killNode;
      if (!confirm(`确认结束 ${node} 上 PID ${pid}？`)) return;
      wrap(`kill ${pid}@${node}`, async () => {
        const data = await api("/api/procs/kill", {
          method: "POST",
          body: JSON.stringify({ node, pids: [pid] }),
        });
        showCliText(data, "killed");
        if (data.overview) applyOverview(data.overview);
        toast(`已结束 PID ${pid}`);
      });
    });
  });
  root.querySelectorAll("button[data-free]").forEach((btn) => {
    btn.addEventListener("click", () => {
      if (!confirm(`确认腾空 ${btn.dataset.free}？（停 Job + 结束 GPU 进程 + 校验）`)) return;
      wrap(`free ${btn.dataset.free}`, () => runFree(btn.dataset.free));
    });
  });
  root.querySelectorAll("button[data-free-cordon]").forEach((btn) => {
    btn.addEventListener("click", () => {
      if (!confirm(`确认腾空并隔离 ${btn.dataset.freeCordon}？调度器将跳过该节点`)) return;
      wrap(`free+cordon ${btn.dataset.freeCordon}`, () =>
        runFree(btn.dataset.freeCordon, {
          cordon: true,
          reason: "freed for external use",
        })
      );
    });
  });
  root.querySelectorAll("button[data-cordon]").forEach((btn) => {
    btn.addEventListener("click", () => {
      wrap(`cordon ${btn.dataset.cordon}`, async () => {
        const data = await api("/api/node/cordon", {
          method: "POST",
          body: JSON.stringify({ name: btn.dataset.cordon, reason: "manual from ui" }),
        });
        log(data.result.stdout || "已隔离");
        if (data.overview) applyOverview(data.overview);
        toast("已隔离（不自动停任务）");
      });
    });
  });
  root.querySelectorAll("button[data-uncordon]").forEach((btn) => {
    btn.addEventListener("click", () => {
      wrap(`uncordon ${btn.dataset.uncordon}`, async () => {
        const data = await api("/api/node/uncordon", {
          method: "POST",
          body: JSON.stringify({ name: btn.dataset.uncordon }),
        });
        log(data.result.stdout || "已取消隔离");
        if (data.overview) applyOverview(data.overview);
        toast("已取消隔离");
      });
    });
  });
}

function renderJobs(jobs) {
  const opts = (jobs || []).map((j) => `<option value="${j.name}">${j.name}</option>`).join("");
  for (const id of ["job-select", "quick-job"]) {
    const sel = document.getElementById(id);
    if (!sel) continue;
    const prev = sel.value;
    sel.innerHTML = opts;
    if ([...sel.options].some((o) => o.value === prev)) sel.value = prev;
  }
  document.getElementById("jobs").innerHTML =
    (jobs || [])
      .map((j) => {
        const runtime = j.runtime || "process";
        const args = (j.extra_args || []).join(" ");
        return `<div class="item"><div><strong>${escapeHtml(j.name)}</strong>
          <div class="meta">${escapeHtml(j.description || "")}</div>
          <div class="meta">${escapeHtml(runtime)}${j.model ? " · model=" + escapeHtml(j.model) : ""}
          ${args ? " · " + escapeHtml(args) : ""}</div></div>
          <span class="chip">${j.gpus_per_node || "?"} GPU</span></div>`;
      })
      .join("") || '<div class="meta">暂无任务</div>';
  syncJobArgsHint(jobs);
}

function syncJobArgsHint(jobs) {
  const sel = document.getElementById("job-select");
  const hint = document.getElementById("job-args-hint");
  const input = document.getElementById("job-extra-args");
  if (!sel || !hint) return;
  const job = (jobs || state.overview?.jobs || []).find((j) => j.name === sel.value);
  const args = (job?.extra_args || []).join(" ");
  hint.textContent = args ? `默认：${args}` : "";
  if (input && !input.dataset.touched) input.placeholder = args || "--flag value";
}

function renderSecretsMeta(secrets) {
  const el = document.getElementById("secrets-meta");
  if (!el) return;
  const hf = secrets?.hf_configured ? "HF✓" : "HF–";
  const ms = secrets?.modelscope_configured ? "MS✓" : "MS–";
  el.textContent = `${hf} · ${ms}`;
}

function renderModels(payload) {
  state.models = payload;
  const models = payload?.models || [];
  const sel = document.getElementById("model-select");
  if (!sel) return;
  const prev = sel.value;
  sel.innerHTML =
    models.map((m) => `<option value="${escapeHtml(m.id)}">${escapeHtml(m.id)}</option>`).join("") ||
    `<option value="">尚未登记模型</option>`;
  if ([...sel.options].some((o) => o.value === prev)) sel.value = prev;
  const caches = document.getElementById("model-caches");
  if (caches) {
    caches.innerHTML =
      (payload?.caches || [])
        .map(
          (c) =>
            `<div class="item"><div><strong>${escapeHtml(c.id)}</strong><div class="meta">${escapeHtml(
              c.path
            )}</div></div></div>`
        )
        .join("") || '<div class="meta">无 caches</div>';
  }
  const defaults = document.getElementById("model-defaults-paths");
  if (defaults) {
    defaults.textContent = JSON.stringify(payload?.defaults_paths || {}, null, 2);
  }
  renderSecretsMeta(payload?.secrets);
  syncModelDetail();
}

function syncModelDetail() {
  const payload = state.models;
  const id = document.getElementById("model-select")?.value;
  const m = (payload?.models || []).find((x) => x.id === id);
  document.getElementById("model-path").textContent = m?.path || "-";
  document.getElementById("model-repo").textContent = m?.repo || "-";
  document.getElementById("model-job").textContent = m?.launch?.job || "-";
  document.getElementById("model-args").textContent = (m?.launch?.extra_args || []).join(" ") || "-";
  const verifyEl = document.getElementById("model-verify");
  if (verifyEl) {
    const files = (m?.verify?.expect_files || []).join(", ") || "-";
    const minB = m?.verify?.min_bytes || 0;
    verifyEl.textContent = minB ? `${files} · min_bytes=${minB}` : files;
  }
}

async function refreshModels() {
  const node = document.getElementById("model-node")?.value || "";
  const q = node ? `?node=${encodeURIComponent(node)}` : "";
  try {
    const data = await api(`/api/models${q}`);
    renderModels(data);
  } catch (err) {
    toast(String(err.message || err));
  }
}

function renderQueue(queue) {
  const root = document.getElementById("queue");
  if (!queue || !queue.length) {
    root.innerHTML = '<div class="meta">队列为空</div>';
    return;
  }
  const recent = [...queue].reverse().slice(0, 10);
  root.innerHTML = `<table class="table"><thead><tr><th>ID</th><th>状态</th><th>任务</th><th></th></tr></thead><tbody>
    ${recent
      .map((wl) => {
        const canCancel = wl.status === "pending" || wl.status === "admitted";
        return `<tr>
          <td>${wl.id}<div class="meta">${wl.gpus} GPU</div></td>
          <td>${wl.status}</td>
          <td>${wl.job}</td>
          <td>${canCancel ? `<button data-cancel="${wl.id}" class="danger tiny">取消</button>` : ""}</td>
        </tr>`;
      })
      .join("")}
  </tbody></table>`;
  root.querySelectorAll("button[data-cancel]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      setBusy(true);
      try {
        const data = await api(`/api/queue/${encodeURIComponent(btn.dataset.cancel)}/cancel`, {
          method: "POST",
          body: "{}",
        });
        log(data.result.stdout || "cancelled");
        await refresh(false);
      } catch (err) {
        log(String(err.message || err));
      } finally {
        setBusy(false);
      }
    });
  });
}

function renderLeases(leases) {
  const root = document.getElementById("leases");
  if (!leases.length) {
    root.innerHTML = '<div class="meta">no leases</div>';
    return;
  }
  root.innerHTML = leases
    .map(
      (l) => `<div class="item">
        <div><strong>${l.id}</strong><div class="meta">${l.job} · ${(l.nodes || []).join(", ")}</div></div>
        <button data-lease="${l.id}" class="danger tiny">Release</button>
      </div>`
    )
    .join("");
  root.querySelectorAll("button[data-lease]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      setBusy(true);
      try {
        const data = await api(`/api/leases/${encodeURIComponent(btn.dataset.lease)}/release`, {
          method: "POST",
          body: "{}",
        });
        log(data.result.stdout || "released");
        await refresh(false);
      } catch (err) {
        log(String(err.message || err));
      } finally {
        setBusy(false);
      }
    });
  });
}

function renderProbe(probe) {
  const root = document.getElementById("probe");
  if (!probe || !probe.matrix) {
    root.innerHTML = '<div class="meta">not probed</div>';
    return;
  }
  const nodes = probe.nodes || [];
  const matrix = probe.matrix || {};
  const head = nodes.map((n) => `<th>${n}</th>`).join("");
  const body = nodes
    .map((src) => {
      const cells = nodes
        .map((dst) => {
          if (src === dst) return "<td>·</td>";
          const cell = matrix[src] && matrix[src][dst];
          if (!cell) return "<td>?</td>";
          return `<td style="color:${cell.ok ? "var(--ok)" : "var(--bad)"};font-weight:700">${cell.ok ? "Y" : "N"
            }</td>`;
        })
        .join("");
      return `<tr><th>${src}</th>${cells}</tr>`;
    })
    .join("");
  root.innerHTML = `<div class="meta">ok=${probe.ok_count ?? "-"} fail=${probe.fail_count ?? "-"}</div>
    <table class="table"><thead><tr><th></th>${head}</tr></thead><tbody>${body}</tbody></table>`;
}

function applyOverview(data) {
  state.overview = data;
  if (data.__etag) state.etag = data.__etag;
  else if (data.etag) state.etag = data.etag;
  if (state.selectedNode && !(data.nodes || []).some((n) => n.name === state.selectedNode)) {
    state.selectedNode = null;
  }
  if (data.version) document.getElementById("ui-version").textContent = `v${data.version}`;
  const strategy = (data.cluster && data.cluster.strategy) || "multi-node";
  const strategyLabel =
    strategy === "inference-pool"
      ? "多节点 API 水平扩展"
      : strategy === "multi-node"
        ? "多节点 GPU 控制面"
        : strategy;
  const ctrl = data.control || {};
  const ctrlHost = ctrl.host || ctrl.ansible_host || "";
  const ctrlPort = ctrl.ui_port || 8787;
  document.getElementById("strategy-line").textContent = ctrlHost
    ? `${strategyLabel} · 主控 ${ctrl.node || "control"}@${ctrlHost} · UI :${ctrlPort}`
    : `${strategyLabel} · 主控统一调度`;
  renderSummary(data.summary || {});
  fillTargetSelects(data.nodes || []);
  renderPresets(data.exec_presets || []);
  renderNodes(data.nodes || []);
  renderPool(data);
  renderJobs(data.jobs || []);
  renderQueue(data.queue || []);
  renderLeases(data.leases || []);
  renderProbe(data.probe || {});
  renderHostMetrics(data);
  document.getElementById("generated-at").textContent = data.generated_at || "-";
  document.getElementById("ray-note").textContent =
    (data.notes && (data.notes.innovation || data.notes.strategy)) || "";
}

function renderQuality(q) {
  if (!q) return;
  state.quality = q;
  document.getElementById("quality-score").textContent = q.score ?? "-";
  document.getElementById("quality-grade").textContent = q.grade || "未检测";
  const ssh = q.ssh || {};
  const api = q.api || {};
  const mesh = q.mesh || {};
  const gpus = q.gpus || {};
  document.getElementById("q-ssh").textContent =
    ssh.total != null ? `${ssh.ok_count ?? 0}/${ssh.total}` : "-";
  document.getElementById("q-api").textContent =
    api.total != null ? `${api.ok ?? 0}/${api.total}` : "-";
  document.getElementById("q-mesh").textContent =
    mesh.total ? `${mesh.ok}/${mesh.total}` : "-";
  document.getElementById("q-gpu").textContent =
    gpus.total != null ? `${gpus.free}/${gpus.total}` : "-";
  document.getElementById("quality-updated").textContent = q.updated_at
    ? `更新于 ${q.updated_at}`
    : "完成「全量同步」或「一键连通」后更新评分";

  const results = (ssh.results || []).slice().sort((a, b) => (a.node || "").localeCompare(b.node || ""));
  const maxLat = Math.max(1, ...results.map((r) => r.latency_ms || 0));
  const bars = document.getElementById("ssh-latency-bars");
  if (!results.length) {
    bars.innerHTML = '<p class="muted">尚未探测 SSH</p>';
    document.getElementById("ssh-latency-meta").textContent = "尚未探测";
  } else {
    const lat = ssh.latency_ms || {};
    document.getElementById("ssh-latency-meta").textContent =
      `平均 ${lat.avg ?? "-"}ms · 最快 ${lat.min ?? "-"}ms · 最慢 ${lat.max ?? "-"}ms`;
    bars.innerHTML = results
      .map((r) => {
        const pct = r.ok ? Math.max(6, Math.round(((r.latency_ms || 0) / maxLat) * 100)) : 100;
        return `<div class="latency-row ${r.ok ? "" : "fail"}">
          <span>${escapeHtml(r.node)}</span>
          <div class="latency-track"><div class="latency-fill" style="width:${pct}%"></div></div>
          <span>${r.ok ? `${r.latency_ms}ms` : "失败"}</span>
        </div>`;
      })
      .join("");
  }
}

function renderActionSteps(steps, elapsed) {
  const root = document.getElementById("action-steps");
  if (!steps || !steps.length) {
    root.innerHTML = '<p class="muted">暂无步骤</p>';
    return;
  }
  root.innerHTML =
    steps
      .map(
        (s) => `<div class="step-item ${s.ok ? "ok" : "fail"}">
          <span>${escapeHtml(s.name)}${s.error ? " · " + escapeHtml(s.error) : ""}</span>
          <span class="meta">${s.ok ? "成功" : "失败"} · ${s.ms}ms</span>
        </div>`
      )
      .join("") +
    `<div class="meta" style="margin-top:6px">总耗时 ${elapsed ?? "-"}ms</div>`;
}

async function loadQuality() {
  const q = await api("/api/quality");
  renderQuality(q);
  return q;
}

async function runOneclick(action, extra = {}) {
  setBusy(true);
  try {
    const body = { action, ...extra };
    if (action === "deploy") {
      body.job = document.getElementById("quick-job").value || "";
      if (!body.job) {
        showSteps([{ name: "deploy", ok: false, ms: 0, error: "请先选择任务（或注册 Job）" }], action);
        return log("请先选择任务（或注册 Job）");
      }
      body.gpus = Number(document.getElementById("quick-gpus").value || 4);
      if (!confirm(`确认一键部署 ${body.job}（${body.gpus} GPU）？`)) return;
    }
    log(`开始：${action}`);
    const data = await api("/api/actions/oneclick", {
      method: "POST",
      body: JSON.stringify(body),
    });
    renderActionSteps(data.steps || [], data.elapsed_ms);
    if (data.quality) renderQuality(data.quality);
    if (data.overview) applyOverview(data.overview);
    if (data.error && !(data.steps || []).length) {
      showSteps([{ name: action, ok: false, ms: data.elapsed_ms, error: data.error }], action);
    } else {
      showSteps(data.steps || [], action);
    }
    toast(data.ok ? `完成 · ${data.elapsed_ms}ms` : `部分失败 · ${data.elapsed_ms}ms`);
    log(`${action} ${data.ok ? "成功" : "失败"} · ${data.elapsed_ms}ms`);
  } catch (err) {
    showSteps([{ name: action, ok: false, ms: 0, error: String(err.message || err) }], action);
    log(String(err.message || err));
  } finally {
    setBusy(false);
  }
}

async function refresh(doRemoteRefresh) {
  if (state.refreshInFlight && !doRemoteRefresh) return state.overview;
  state.refreshInFlight = true;
  try {
    const headers = {};
    if (!doRemoteRefresh && state.etag) headers["If-None-Match"] = state.etag;
    const path = doRemoteRefresh ? "/api/overview?refresh=1" : "/api/overview";
    const data = await api(path, { headers });
    if (data.__notModified) {
      if (data.etag) state.etag = data.etag;
      return state.overview;
    }
    applyOverview(data);
    return data;
  } finally {
    state.refreshInFlight = false;
  }
}

async function runExec(payload) {
  const target = selectedFrom("exec-target");
  setBusy(true);
  try {
    log(`exec @ ${target.node || target.nodes}`);
    const data = await api("/api/exec", {
      method: "POST",
      body: JSON.stringify({ ...target, ...payload }),
    });
    showResults(data.results, data.command);
    setView("ops");
  } catch (err) {
    log(String(err.message || err));
  } finally {
    setBusy(false);
  }
}

async function runPython() {
  const target = selectedFrom("py-target");
  const body = {
    ...target,
    python: document.getElementById("py-bin").value.trim(),
    script: document.getElementById("py-script").value.trim(),
    code: document.getElementById("py-code").value.trim(),
    args: document.getElementById("py-args").value.trim(),
    cuda: document.getElementById("py-cuda").value.trim(),
    cwd: document.getElementById("py-cwd").value.trim(),
    background: document.getElementById("py-bg").checked,
  };
  if (!body.script && !body.code) return log("请填写脚本或代码");
  setBusy(true);
  try {
    const data = await api("/api/run-python", { method: "POST", body: JSON.stringify(body) });
    showResults(data.results, data.command);
  } catch (err) {
    log(String(err.message || err));
  } finally {
    setBusy(false);
  }
}

function parseExtraArgs(text) {
  const raw = (text || "").trim();
  if (!raw) return null;
  return raw.split(/\s+/).filter(Boolean);
}

async function runJobAction(action) {
  const job = document.getElementById("job-select").value;
  if (!job) return;
  const scope = document.getElementById("job-target").value;
  let target;
  if (!scope || scope === "all") target = { nodes: "all" };
  else {
    state.selectedNode = scope;
    target = { node: scope };
  }
  const extraArgs = parseExtraArgs(document.getElementById("job-extra-args")?.value);
  const body = { action, ...target };
  if (extraArgs) body.extra_args = extraArgs;
  setBusy(true);
  try {
    const data = await api(`/api/jobs/${encodeURIComponent(job)}/action`, {
      method: "POST",
      body: JSON.stringify(body),
    });
    writeConsoleText(
      data.result.stdout || data.result.stderr || `(rc=${data.result.rc})`,
      { meta: `Job ${action}` }
    );
    await refresh(false);
  } catch (err) {
    writeConsoleText(String(err.message || err), { meta: `Job ${action} 失败` });
    log(String(err.message || err));
  } finally {
    setBusy(false);
  }
}

async function runApiHealth() {
  setBusy(true);
  try {
    log("正在探测 API 健康…");
    const t0 = performance.now();
    const data = await api("/api/endpoints/health/run", { method: "POST", body: "{}" });
    const ms = Math.round(performance.now() - t0);
    if (data.overview) applyOverview(data.overview);
    const h = data.health || {};
    toast(`API ${h.ok_count}/${h.total} · ${ms}ms`);
    loadQuality().catch(() => { });
    setView("endpoints");
  } catch (err) {
    log(String(err.message || err));
  } finally {
    setBusy(false);
  }
}

async function runDoctor() {
  setBusy(true);
  try {
    const data = await api("/api/doctor");
    const text = data.ok
      ? `Doctor OK\n${(data.notes || []).map((x) => "· " + x).join("\n")}`
      : `Doctor ISSUES\n${(data.issues || []).map((x) => "! " + x).join("\n")}`;
    writeConsoleText(text, { meta: data.ok ? "Doctor OK" : "Doctor 有问题" });
    const box = document.getElementById("doctor-box");
    if (box) box.textContent = text;
    toast(data.ok ? "自检通过" : "自检发现问题");
    setView("config");
    setConfigTab("checklist");
  } catch (err) {
    writeConsoleText(String(err.message || err), { meta: "Doctor 失败" });
    log(String(err.message || err));
  } finally {
    setBusy(false);
  }
}

function setConfigTab(name) {
  document.querySelectorAll("#config-tabs .tab").forEach((t) => {
    t.classList.toggle("active", t.dataset.tab === name);
  });
  document.querySelectorAll("[data-tab-panel]").forEach((p) => {
    p.classList.toggle("active", p.dataset.tabPanel === name);
  });
}

function setView(name) {
  state.view = name;
  document.querySelectorAll(".nav-item").forEach((n) => n.classList.toggle("active", n.dataset.view === name));
  document.querySelectorAll(".view").forEach((v) => v.classList.toggle("active", v.dataset.viewPanel === name));
  document.getElementById("view-title").textContent = VIEW_TITLE[name] || name;
  const hint = document.getElementById("cli-hint-code");
  if (hint) hint.textContent = VIEW_CLI[name] || "gpu-control --help";
  if (name === "guide" || name === "config") {
    loadSetup().catch((err) => log(String(err.message || err)));
  }
  if (name === "models") {
    refreshModels().catch((err) => log(String(err.message || err)));
  }
  if (name === "actions") {
    loadQuality().catch(() => { });
  }
  if (name === "terminal") {
    ensureTerminal();
    if (termState.fit) {
      setTimeout(() => {
        termState.fit.fit();
        sendTermResize();
      }, 30);
    }
  }
}

function nodesDocToEditor(doc) {
  const nodes = (doc && doc.nodes) || {};
  return Object.keys(nodes)
    .sort()
    .map((name) => {
      const meta = nodes[name] || {};
      const gpus = meta.gpus_per_node ?? "";
      const labels = (meta.labels || []).join(",");
      return [name, meta.ansible_host || "", gpus, labels].join(" ").trim();
    })
    .join("\n");
}

function editorToNodes(text, baseDoc) {
  const doc = {
    defaults: { ...(baseDoc.defaults || {}) },
    nodes: {},
  };
  for (const raw of text.split("\n")) {
    const line = raw.trim();
    if (!line || line.startsWith("#")) continue;
    const parts = line.split(/\s+/);
    const name = parts[0];
    const host = parts[1];
    if (!name || !host) continue;
    const gpus = parts[2] && /^\d+$/.test(parts[2]) ? Number(parts[2]) : undefined;
    const labels = parts.slice(gpus == null ? 2 : 3).join(" ");
    const node = { ansible_host: host, role: "serving" };
    if (gpus != null) node.gpus_per_node = gpus;
    if (labels) node.labels = labels.split(",").map((x) => x.trim()).filter(Boolean);
    doc.nodes[name] = node;
  }
  return doc;
}

function fillSetupForm(data) {
  stateSetup.data = data;
  const defaults = (data.nodes && data.nodes.defaults) || {};
  const cluster = data.cluster || {};
  const api = cluster.api || {};
  const scheduling = cluster.scheduling || {};

  document.getElementById("setup-nodes-path").textContent = data.nodes_path || "";
  document.getElementById("setup-cluster-path").textContent = data.cluster_path || "";
  document.getElementById("ssh-key").value = defaults.ansible_ssh_private_key_file || "";
  document.getElementById("ssh-user").value = defaults.ansible_user || "";
  document.getElementById("ssh-port").value = defaults.ansible_port ?? "";
  document.getElementById("cluster-name").value = cluster.name || "";
  const defaultJob =
    cluster.default_job ||
    ((data.jobs || [])[0] && (data.jobs || [])[0].name) ||
    "";
  document.getElementById("cluster-job").value = defaultJob;
  document.getElementById("cluster-base-port").value = api.base_port ?? 8000;
  document.getElementById("cluster-ports").value = api.ports_per_node ?? 1;
  document.getElementById("cluster-api-key").value = api.api_key_file || "";
  document.getElementById("cluster-prefer").value = (scheduling.prefer_labels || []).join(",");
  document.getElementById("cluster-avoid").value = (scheduling.avoid_labels || []).join(",");
  document.getElementById("nodes-editor").value = nodesDocToEditor(data.nodes || {});

  const job =
    (data.jobs || []).find((j) => j.name === defaultJob) || (data.jobs || [])[0];
  if (job) {
    document.getElementById("job-name").value = job.name || defaultJob;
    document.getElementById("setup-job-path").textContent =
      job.path || `config/local/jobs/${job.name || defaultJob}.json`;
    document.getElementById("job-desc").value = job.description || "";
    document.getElementById("job-gpus").value = job.gpus_per_node ?? 4;
    document.getElementById("job-python").value = job.python || "";
    document.getElementById("job-script").value = job.script || "";
    document.getElementById("job-extra").value = (job.extra_args || []).join(" ");
  } else {
    document.getElementById("job-name").value = defaultJob || "";
    document.getElementById("setup-job-path").textContent = "config/local/jobs/<name>.json";
  }

  const chips = document.getElementById("ssh-key-chips");
  chips.innerHTML = (data.ssh_keys || [])
    .map(
      (k) =>
        `<button type="button" class="key-chip" data-key="${escapeHtml(k.path)}" data-keep-enabled="1">${escapeHtml(
          k.path
        )}</button>`
    )
    .join("") || '<span class="meta">未在 ~/.ssh 发现可用私钥</span>';
  chips.querySelectorAll("[data-key]").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.getElementById("ssh-key").value = btn.dataset.key;
      toast("已填入密钥路径");
    });
  });

  document.getElementById("setup-checklist").innerHTML = (data.checklist || [])
    .map((item) => {
      const ok = item.ok;
      return `<div class="check-item ${ok ? "ok" : "bad"}">
        <div class="mark">${ok ? "✓" : "!"}</div>
        <div>
          <div class="label">${escapeHtml(item.label)}</div>
          <div class="detail">${escapeHtml(item.detail || (ok ? "ok" : "missing"))}</div>
        </div>
      </div>`;
    })
    .join("");

  document.getElementById("guide-steps").innerHTML = (data.guide || [])
    .map(
      (step, i) => `<div class="guide-card">
        <h3>${i + 1}. ${escapeHtml(step.title)}</h3>
        <p>${escapeHtml(step.body)}</p>
        <pre>${escapeHtml((step.cli || []).join("\n"))}</pre>
      </div>`
    )
    .join("");

  document.getElementById("config-help").textContent = [
    `project: ${data.project}`,
    `local:   ${data.config_local}`,
    `cluster: ${data.cluster_path}`,
    `nodes:   ${data.nodes_path}`,
    "",
    "查找顺序: config/local → config → 仓库根",
    "密钥与内网 IP 只写 config/local（gitignore）",
    "",
    "CLI:",
    "  gpu-control doctor",
    "  gpu-control node render",
    "  gpu-control get nodes",
    "  gpu-control health",
    "  gpu-control ui --background",
  ].join("\n");
}

async function loadSetup() {
  const data = await api("/api/setup");
  fillSetupForm(data);
  if (data.jobs && data.jobs.length) {
    const name = data.jobs[0].name;
    try {
      const jobResp = await api(`/api/config/job/${encodeURIComponent(name)}`);
      const job = jobResp.data || {};
      document.getElementById("setup-job-path").textContent = jobResp.path || "";
      document.getElementById("job-desc").value = job.description || "";
      document.getElementById("job-gpus").value = job.gpus_per_node ?? 4;
      document.getElementById("job-python").value = job.python || "";
      document.getElementById("job-script").value = job.script || "";
      document.getElementById("job-extra").value = (job.extra_args || []).join(" ");
    } catch (_) { }
  }
  return data;
}

async function saveSsh() {
  const key = document.getElementById("ssh-key").value.trim();
  const user = document.getElementById("ssh-user").value.trim();
  const port = document.getElementById("ssh-port").value.trim();
  if (!key) return toast("请填写 SSH 私钥路径");
  setBusy(true);
  try {
    const data = await api("/api/setup/apply-ssh", {
      method: "POST",
      body: JSON.stringify({ key, user, port: port ? Number(port) : undefined }),
    });
    fillSetupForm(data.setup);
    toast(data.key_exists ? "SSH 已保存" : "已保存，但密钥文件不存在");
    log(data.render?.stdout || "ssh saved");
  } catch (err) {
    log(String(err.message || err));
  } finally {
    setBusy(false);
  }
}

async function testSsh() {
  setBusy(true);
  try {
    const data = await api("/api/setup/test-ssh", { method: "POST", body: "{}" });
    const root = document.getElementById("ssh-test-result");
    root.innerHTML = (data.results || [])
      .map(
        (r) => `<div class="ssh-row ${r.ok ? "ok" : "fail"}">
          <span><b>${escapeHtml(r.node)}</b> ${escapeHtml(r.host || "")}</span>
          <span>${escapeHtml(r.detail || "")}</span>
        </div>`
      )
      .join("");
    toast(data.ok ? "SSH 全部通" : "部分节点不通");
    setConfigTab("wizard");
  } catch (err) {
    log(String(err.message || err));
  } finally {
    setBusy(false);
  }
}

async function saveCluster() {
  const base = (stateSetup.data && stateSetup.data.cluster) || {};
  const data = {
    ...base,
    name: document.getElementById("cluster-name").value.trim() || base.name,
    default_job: document.getElementById("cluster-job").value.trim() || "",
    api: {
      ...(base.api || {}),
      base_port: Number(document.getElementById("cluster-base-port").value || 8000),
      ports_per_node: Number(document.getElementById("cluster-ports").value || 1),
      api_key_file: document.getElementById("cluster-api-key").value.trim(),
    },
    scheduling: {
      ...(base.scheduling || {}),
      prefer_labels: document
        .getElementById("cluster-prefer")
        .value.split(",")
        .map((x) => x.trim())
        .filter(Boolean),
      avoid_labels: document
        .getElementById("cluster-avoid")
        .value.split(",")
        .map((x) => x.trim())
        .filter(Boolean),
    },
  };
  setBusy(true);
  try {
    const resp = await api("/api/config/cluster", {
      method: "POST",
      body: JSON.stringify({ data }),
    });
    fillSetupForm(resp.setup);
    toast("cluster 已保存");
  } catch (err) {
    log(String(err.message || err));
  } finally {
    setBusy(false);
  }
}

async function saveNodesEditor() {
  const base = (stateSetup.data && stateSetup.data.nodes) || { defaults: {}, nodes: {} };
  const data = editorToNodes(document.getElementById("nodes-editor").value, base);
  // keep ssh defaults from form if present
  const key = document.getElementById("ssh-key").value.trim();
  const user = document.getElementById("ssh-user").value.trim();
  const port = document.getElementById("ssh-port").value.trim();
  if (key) data.defaults.ansible_ssh_private_key_file = key;
  if (user) data.defaults.ansible_user = user;
  if (port) data.defaults.ansible_port = Number(port);
  setBusy(true);
  try {
    const resp = await api("/api/config/nodes", {
      method: "POST",
      body: JSON.stringify({ data }),
    });
    fillSetupForm(resp.setup);
    toast(`已保存 ${Object.keys(data.nodes).length} 个节点`);
    await refresh(false);
  } catch (err) {
    log(String(err.message || err));
  } finally {
    setBusy(false);
  }
}

async function saveJob() {
  const name = (document.getElementById("job-name").value || "").trim();
  if (!name) return log("请填写 Job 名");
  const extra = document
    .getElementById("job-extra")
    .value.trim()
    .split(/\s+/)
    .filter(Boolean);
  const data = {
    name,
    description: document.getElementById("job-desc").value.trim(),
    gpus_per_node: Number(document.getElementById("job-gpus").value || 4),
    runtime: "process",
    python: document.getElementById("job-python").value.trim(),
    script: document.getElementById("job-script").value.trim(),
    extra_args: extra,
    actions: ["start", "stop", "restart", "status", "wait"],
  };
  setBusy(true);
  try {
    const resp = await api(`/api/config/job/${encodeURIComponent(name)}`, {
      method: "POST",
      body: JSON.stringify({ data }),
    });
    fillSetupForm(resp.setup);
    toast("job 已保存");
    await refresh(false);
  } catch (err) {
    log(String(err.message || err));
  } finally {
    setBusy(false);
  }
}

function setupAutoRefresh() {
  clearInterval(state.autoTimer);
  if (!document.getElementById("auto-refresh").checked) return;
  state.autoTimer = setInterval(async () => {
    if (state.busy) return;
    try {
      await refresh(false);
    } catch (_) { }
  }, 8000);
}

async function boot() {
  initTheme();
  document.querySelectorAll(".nav-item").forEach((btn) => {
    btn.addEventListener("click", () => setView(btn.dataset.view));
  });
  document.querySelectorAll("#config-tabs .tab").forEach((btn) => {
    btn.addEventListener("click", () => setConfigTab(btn.dataset.tab));
  });
  setupAutoRefresh();
  document.getElementById("auto-refresh").addEventListener("change", setupAutoRefresh);

  document.getElementById("btn-refresh").addEventListener("click", async () => {
    setBusy(true);
    try {
      await refresh(true);
      toast("GPU 快照已刷新");
    } catch (err) {
      log(String(err.message || err));
    } finally {
      setBusy(false);
    }
  });
  document.getElementById("btn-reload").addEventListener("click", async () => {
    try {
      await refresh(false);
      await loadSetup();
    } catch (err) {
      log(String(err.message || err));
    }
  });
  document.getElementById("btn-reload-setup").addEventListener("click", async () => {
    try {
      await loadSetup();
      toast("配置状态已刷新");
    } catch (err) {
      log(String(err.message || err));
    }
  });
  document.getElementById("btn-doctor").addEventListener("click", runDoctor);
  document.getElementById("btn-doctor-2").addEventListener("click", runDoctor);
  document.getElementById("btn-goto-setup").addEventListener("click", () => {
    setView("config");
    setConfigTab("wizard");
  });
  document.getElementById("btn-goto-config").addEventListener("click", () => {
    setView("config");
    setConfigTab("wizard");
  });
  document.getElementById("btn-full-sync").addEventListener("click", () => runOneclick("full-sync"));
  document.getElementById("btn-load-quality").addEventListener("click", async () => {
    try {
      await loadQuality();
      toast("评分已刷新");
    } catch (err) {
      log(String(err.message || err));
    }
  });
  document.querySelectorAll("[data-oneclick]").forEach((btn) => {
    btn.addEventListener("click", () => runOneclick(btn.dataset.oneclick));
  });
  document.getElementById("btn-term-connect").addEventListener("click", connectTerminal);
  document.getElementById("btn-term-disconnect").addEventListener("click", disconnectTerminal);
  document.getElementById("btn-metrics-refresh").addEventListener("click", refreshMetrics);
  document.getElementById("btn-lb-refresh").addEventListener("click", refreshLb);
  document.getElementById("lb-strategy").addEventListener("change", refreshLb);
  document.getElementById("snippet-kind").addEventListener("change", () => {
    document.getElementById("client-snippet").textContent = activeSnippet(state.lb);
  });
  document.getElementById("btn-gateway-render").addEventListener("click", renderGatewayFiles);
  document.getElementById("btn-save-ssh").addEventListener("click", saveSsh);
  document.getElementById("btn-test-ssh").addEventListener("click", testSsh);
  document.getElementById("btn-render").addEventListener("click", async () => {
    setBusy(true);
    try {
      const data = await api("/api/setup/render", { method: "POST", body: "{}" });
      fillSetupForm(data.setup);
      toast("inventory 已生成");
      log(data.result?.stdout || "生成完成");
    } catch (err) {
      log(String(err.message || err));
    } finally {
      setBusy(false);
    }
  });
  document.getElementById("btn-save-cluster").addEventListener("click", saveCluster);
  document.getElementById("btn-save-nodes-json").addEventListener("click", saveNodesEditor);
  document.getElementById("btn-save-job").addEventListener("click", saveJob);
  document.getElementById("btn-api-health").addEventListener("click", runApiHealth);
  document.getElementById("btn-api-health-2").addEventListener("click", runApiHealth);
  document.getElementById("btn-probe").addEventListener("click", async () => {
    setBusy(true);
    try {
      const data = await api("/api/probe/run", { method: "POST", body: "{}" });
      log(data.result.stdout || "互通探测完成");
      await refresh(false);
      setView("workloads");
    } catch (err) {
      log(String(err.message || err));
    } finally {
      setBusy(false);
    }
  });
  document.getElementById("btn-schedule").addEventListener("click", async () => {
    setBusy(true);
    try {
      const data = await api("/api/queue/schedule", { method: "POST", body: "{}" });
      log(data.result.stdout || "调度完成");
      await refresh(false);
    } catch (err) {
      log(String(err.message || err));
    } finally {
      setBusy(false);
    }
  });
  document.getElementById("btn-copy-urls").addEventListener("click", async () => {
    const urls = ((state.overview && state.overview.endpoints) || {}).urls || [];
    if (!urls.length) return toast("暂无 URL");
    try {
      await navigator.clipboard.writeText(urls.join("\n"));
      toast(`已复制 ${urls.length} 条 URL`);
    } catch (_) {
      toast("复制失败");
    }
  });

  document.getElementById("btn-exec-custom").addEventListener("click", () => {
    const command = document.getElementById("exec-custom").value.trim();
    if (!command) return log("命令为空");
    runExec({ command });
  });
  document.getElementById("exec-custom").addEventListener("keydown", (ev) => {
    if (ev.key === "Enter") {
      ev.preventDefault();
      document.getElementById("btn-exec-custom").click();
    }
  });
  document.getElementById("btn-run-py").addEventListener("click", runPython);
  document.querySelectorAll("[data-job-action]").forEach((btn) => {
    btn.addEventListener("click", () => runJobAction(btn.dataset.jobAction));
  });
  document.getElementById("job-select")?.addEventListener("change", () => {
    syncJobArgsHint(state.overview?.jobs);
  });
  document.getElementById("job-extra-args")?.addEventListener("input", (ev) => {
    ev.target.dataset.touched = "1";
  });

  document.getElementById("btn-models-refresh")?.addEventListener("click", () => refreshModels());
  document.getElementById("model-select")?.addEventListener("change", syncModelDetail);
  document.getElementById("model-node")?.addEventListener("change", () => refreshModels());
  const runModelsAction = async (path, label, extra = {}) => {
    const node = document.getElementById("model-node")?.value;
    const model = document.getElementById("model-select")?.value;
    setBusy(true);
    try {
      const body = { ...extra };
      if (node) body.node = node;
      else body.nodes = "all";
      if (path.endsWith("/verify") && model) body.model = model;
      const data = await api(path, { method: "POST", body: JSON.stringify(body) });
      if (data.results?.length) showResults(data.results, label);
      else writeConsoleText(data.result?.stdout || data.result?.stderr || label, { meta: label });
      if (data.models) renderModels(data.models);
      toast(data.result?.rc === 0 ? `${label}完成` : `${label}有失败`);
    } catch (err) {
      writeConsoleText(String(err.message || err), { meta: label });
      log(String(err.message || err));
    } finally {
      setBusy(false);
    }
  };
  document.getElementById("btn-models-deps")?.addEventListener("click", () =>
    runModelsAction("/api/models/deps", "依赖检查")
  );
  document.getElementById("btn-models-check")?.addEventListener("click", () =>
    runModelsAction("/api/models/check", "路径检查")
  );
  document.getElementById("btn-models-verify")?.addEventListener("click", () =>
    runModelsAction("/api/models/verify", "模型校验")
  );
  document.getElementById("btn-models-install")?.addEventListener("click", async () => {
    if (!confirm("在目标节点安装 huggingface_hub + modelscope？")) return;
    await runModelsAction("/api/models/install-tools", "安装拉取工具");
  });
  document.getElementById("btn-models-download")?.addEventListener("click", async () => {
    const name = document.getElementById("pull-repo")?.value.trim();
    if (!name) return toast("请填写仓库 ID，例如 org/model");
    const tool = document.getElementById("pull-tool")?.value || "auto";
    const registerId = document.getElementById("pull-id")?.value.trim();
    const register = !!document.getElementById("pull-register")?.checked;
    const node = document.getElementById("model-node")?.value;
    if (!confirm(`下载 ${name}？`)) return;
    setBusy(true);
    try {
      const body = { name, tool, register };
      if (registerId) body.register_id = registerId;
      if (node) body.node = node;
      else body.nodes = "all";
      const data = await api("/api/models/pull", { method: "POST", body: JSON.stringify(body) });
      if (data.results?.length) showResults(data.results, "下载模型");
      else writeConsoleText(data.result?.stdout || data.result?.stderr || "pull done", { meta: "下载" });
      if (data.models) renderModels(data.models);
      toast(data.result?.rc === 0 ? "下载完成" : "下载失败");
      setView("actions");
    } catch (err) {
      writeConsoleText(String(err.message || err), { meta: "下载失败" });
      log(String(err.message || err));
    } finally {
      setBusy(false);
    }
  });
  document.getElementById("btn-models-pull")?.addEventListener("click", async () => {
    const model = document.getElementById("model-select")?.value;
    if (!model) return toast("请选择已登记模型");
    if (!confirm(`重新拉取 ${model}？`)) return;
    await runModelsAction("/api/models/pull", "重拉", { model });
  });
  document.getElementById("btn-save-secrets")?.addEventListener("click", async () => {
    const hf = document.getElementById("secret-hf")?.value.trim();
    const ms = document.getElementById("secret-ms")?.value.trim();
    if (!hf && !ms) return toast("请至少填一个 Token");
    setBusy(true);
    try {
      let secrets = null;
      if (hf) {
        const data = await api("/api/models/secrets", {
          method: "POST",
          body: JSON.stringify({ kind: "hf", token: hf }),
        });
        secrets = data.secrets;
      }
      if (ms) {
        const data = await api("/api/models/secrets", {
          method: "POST",
          body: JSON.stringify({ kind: "modelscope", token: ms }),
        });
        secrets = data.secrets;
      }
      renderSecretsMeta(secrets);
      document.getElementById("secret-hf").value = "";
      document.getElementById("secret-ms").value = "";
      toast("Token 已保存");
    } catch (err) {
      toast(String(err.message || err));
    } finally {
      setBusy(false);
    }
  });
  document.getElementById("btn-clear-secrets")?.addEventListener("click", async () => {
    if (!confirm("清除已保存的 HF / ModelScope Token？")) return;
    setBusy(true);
    try {
      await api("/api/models/secrets", { method: "POST", body: JSON.stringify({ kind: "hf", clear: true }) });
      const data = await api("/api/models/secrets", {
        method: "POST",
        body: JSON.stringify({ kind: "modelscope", clear: true }),
      });
      renderSecretsMeta(data.secrets);
      toast("已清除");
    } catch (err) {
      toast(String(err.message || err));
    } finally {
      setBusy(false);
    }
  });
  document.getElementById("btn-models-apply-job")?.addEventListener("click", () => {
    const m = (state.models?.models || []).find(
      (x) => x.id === document.getElementById("model-select")?.value
    );
    if (!m) return toast("请选择模型");
    if (m.launch?.job) {
      const jobSel = document.getElementById("job-select");
      if (jobSel && [...jobSel.options].some((o) => o.value === m.launch.job)) {
        jobSel.value = m.launch.job;
      }
      const quick = document.getElementById("quick-job");
      if (quick && [...quick.options].some((o) => o.value === m.launch.job)) {
        quick.value = m.launch.job;
      }
    }
    const input = document.getElementById("job-extra-args");
    if (input) {
      input.value = (m.launch?.extra_args || []).join(" ");
      input.dataset.touched = "1";
    }
    setView("workloads");
    toast("已填入任务页，可直接部署");
  });

  document.getElementById("deploy-mode").addEventListener("change", syncDeployMode);
  document.getElementById("job-target").addEventListener("change", () => {
    const v = document.getElementById("job-target").value;
    if (v && v !== "all") {
      state.selectedNode = v;
      if (state.overview) renderNodes(state.overview.nodes);
    }
  });
  document.getElementById("btn-deploy").addEventListener("click", async () => {
    const job = document.getElementById("job-select").value;
    if (!job) return;
    const body = { job, note: "from-ui" };
    const mode = document.getElementById("deploy-mode").value;
    if (mode === "node") {
      const node = document.getElementById("deploy-node").value;
      if (!node) return log("请选择要部署的节点");
      body.nodes = node;
      state.selectedNode = node;
    } else body.gpus = Number(document.getElementById("deploy-gpus").value);
    if (!confirm(`确认部署 ${job}？`)) return;
    setBusy(true);
    try {
      const data = await api("/api/deploy", { method: "POST", body: JSON.stringify(body) });
      writeConsoleText(data.result.stdout || data.result.stderr || "deploy done", {
        meta: "deploy",
      });
      await refresh(true);
    } catch (err) {
      writeConsoleText(String(err.message || err), { meta: "deploy 失败" });
      log(String(err.message || err));
    } finally {
      setBusy(false);
    }
  });

  document.getElementById("btn-queue-submit").addEventListener("click", async () => {
    const job = document.getElementById("job-select").value;
    if (!job) return log("请先选择任务");
    setBusy(true);
    try {
      const data = await api("/api/queue/submit", {
        method: "POST",
        body: JSON.stringify({ job, gpus: 4, note: "from-ui" }),
      });
      log(data.result.stdout || "submitted");
      await refresh(false);
    } catch (err) {
      log(String(err.message || err));
    } finally {
      setBusy(false);
    }
  });

  document.getElementById("btn-add-node").addEventListener("click", async () => {
    const name = document.getElementById("add-name").value.trim();
    const host = document.getElementById("add-host").value.trim();
    const gpus = Number(document.getElementById("add-gpus").value || 4);
    const labels = document.getElementById("add-labels").value.trim();
    if (!name || !host) return log("请填写名称和地址");
    setBusy(true);
    try {
      const data = await api("/api/node/add", {
        method: "POST",
        body: JSON.stringify({ name, host, gpus, labels }),
      });
      log(data.result.stdout || "已添加");
      document.getElementById("add-name").value = "";
      document.getElementById("add-host").value = "";
      await refresh(false);
      setView("nodes");
    } catch (err) {
      log(String(err.message || err));
    } finally {
      setBusy(false);
    }
  });

  document.getElementById("btn-clear-console").addEventListener("click", () => {
    writeConsoleText("已清空。点「跑一键连通」或上方运维卡片可再次产生输出。", {
      empty: true,
      meta: "已清空",
    });
  });
  document.getElementById("btn-clear-log").addEventListener("click", () => {
    document.getElementById("log").textContent = "已清空";
  });
  document.getElementById("btn-console-connect").addEventListener("click", () => runOneclick("connect"));

  window.addEventListener("keydown", (ev) => {
    if (ev.target && ["INPUT", "TEXTAREA", "SELECT"].includes(ev.target.tagName)) return;
    const map = {
      1: "actions",
      2: "cluster",
      3: "nodes",
      4: "workloads",
      5: "endpoints",
      6: "ops",
      7: "terminal",
      8: "guide",
      9: "config",
    };
    if (map[ev.key]) setView(map[ev.key]);
    if (ev.key === "r" || ev.key === "R") {
      ev.preventDefault();
      document.getElementById("btn-refresh").click();
    }
    if (ev.key === "h" || ev.key === "H") {
      ev.preventDefault();
      runApiHealth();
    }
  });

  setBusy(true);
  try {
    await refresh(false);
    await loadQuality();
    setView("actions");
    await seedConsole();
  } catch (err) {
    log(String(err.message || err));
    writeConsoleText(`初始化失败：${err.message || err}\n\n仍可点「跑一键连通」尝试。`, {
      meta: "初始化失败",
    });
  } finally {
    setBusy(false);
  }
}

boot();
