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
  auth: null,
  ingress: null,
  lastRefreshAt: 0,
  pollFailures: 0,
  pollSkip: 0,
  jobTemplates: [],
};

const VIEW_TITLE = {
  actions: "看板",
  cluster: "节点矩阵",
  nodes: "节点",
  workloads: "任务",
  models: "模型",
  toolkits: "工具栈",
  endpoints: "API 端点",
  ops: "命令",
  terminal: "终端",
  guide: "指南",
  config: "配置",
};

const VIEW_CLI = {
  actions: "gpu-control overview && gpu-control toolkits probe",
  cluster: "gpu-control get nodes",
  nodes: "gpu-control node render",
  workloads: "gpu-control get jobs",
  models: "gpu-control models deps && gpu-control models verify",
  toolkits: "gpu-control toolkits list && gpu-control toolkits probe",
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

const stateSetup = {
  data: null,
  jobBase: null,
  jobBaseName: null,
  jobLoadSeq: 0,
  nodesDraft: null,
  nodesFilter: "",
  nodesDensity: "auto", // auto | cards | compact
};

const $ = (id) => document.getElementById(id);

function on(id, event, fn, opts) {
  const el = typeof id === "string" ? $(id) : id;
  if (!el) {
    console.warn(`[ui] missing element for ${event}:`, id);
    return null;
  }
  el.addEventListener(event, fn, opts);
  return el;
}

function resolveTheme(mode) {
  if (mode === "light" || mode === "dark") return mode;
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

function applyTheme(mode) {
  const next = mode === "dark" || mode === "light" ? mode : "auto";
  document.documentElement.setAttribute("data-theme", resolveTheme(next));
  document.documentElement.setAttribute("data-theme-mode", next);
  localStorage.setItem("gpu-control-theme", next);
  document.querySelectorAll("[data-theme-set]").forEach((btn) => {
    const active = btn.dataset.themeSet === next;
    btn.classList.toggle("active", active);
    btn.setAttribute("aria-pressed", active ? "true" : "false");
  });
}

function initTheme() {
  const saved = localStorage.getItem("gpu-control-theme") || "auto";
  applyTheme(saved);
  const mq = window.matchMedia("(prefers-color-scheme: dark)");
  const onChange = () => {
    if ((localStorage.getItem("gpu-control-theme") || "auto") === "auto") applyTheme("auto");
  };
  if (mq.addEventListener) mq.addEventListener("change", onChange);
  else if (mq.addListener) mq.addListener(onChange);
  document.querySelectorAll("[data-theme-set]").forEach((btn) => {
    btn.addEventListener("click", () => applyTheme(btn.dataset.themeSet));
  });
}

/* Busy is counted: nested / concurrent actions stay consistent and navigation
   remains usable while a long remote command runs. */
const busyState = { depth: 0 };
const setBusy = (busy) => {
  busyState.depth = Math.max(0, busyState.depth + (busy ? 1 : -1));
  const active = busyState.depth > 0;
  state.busy = active;
  document.body.classList.toggle("is-busy", active);
  document.documentElement.setAttribute("aria-busy", active ? "true" : "false");
  const flag = $("busy-flag");
  if (flag) flag.textContent = active ? "执行中…" : "";
  const indicator = $("busy-indicator");
  if (indicator) indicator.hidden = !active;
  document.querySelectorAll("[data-action], [data-job-action], [data-oneclick]").forEach((btn) => {
    btn.disabled = active;
  });
};

function toast(text, { tone = "info", timeout = 2600 } = {}) {
  const el = $("toast");
  if (!el) return;
  el.hidden = false;
  el.textContent = text;
  el.dataset.tone = tone;
  clearTimeout(toast._t);
  toast._t = setTimeout(() => {
    el.hidden = true;
  }, timeout);
}
function toastOk(text) {
  toast(text, { tone: "ok" });
}
function toastBad(text) {
  toast(text, { tone: "bad", timeout: 4200 });
}

function log(text) {
  const el = $("log");
  if (!el) return;
  const stamp = new Date().toLocaleTimeString();
  el.textContent = `[${stamp}] ${text}\n` + el.textContent;
  const lines = el.textContent.split("\n");
  if (lines.length > 240) el.textContent = lines.slice(0, 240).join("\n");
}

function actionError(label, err) {
  const msg = String((err && err.message) || err || "未知错误");
  log(`${label}: ${msg}`);
  toastBad(`${label}失败：${msg}`);
}

/* Clipboard API needs a secure context; fall back for plain-HTTP LAN access. */
function copyText(text) {
  if (navigator.clipboard && window.isSecureContext) {
    return navigator.clipboard.writeText(text);
  }
  return new Promise((resolve, reject) => {
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.setAttribute("readonly", "");
    ta.style.cssText = "position:fixed;top:-9999px;left:-9999px;opacity:0";
    document.body.appendChild(ta);
    ta.select();
    try {
      const ok = document.execCommand && document.execCommand("copy");
      if (ok) resolve();
      else reject(new Error("浏览器不允许复制"));
    } catch (err) {
      reject(err);
    } finally {
      ta.remove();
    }
  });
}

/* In-app confirmation dialog (native <dialog>, falls back to window.confirm). */
function confirmDialog({
  title = "确认操作",
  message = "",
  confirmText = "确认",
  cancelText = "取消",
  tone = "primary",
} = {}) {
  if (typeof HTMLDialogElement === "undefined" || !document.body) {
    return Promise.resolve(window.confirm(message || title));
  }
  return new Promise((resolve) => {
    const dlg = document.createElement("dialog");
    dlg.className = "dialog";
    dlg.innerHTML = `<form method="dialog" class="dialog-card">
      <h2>${escapeHtml(title)}</h2>
      <p>${escapeHtml(message)}</p>
      <div class="dialog-actions">
        <button value="cancel" class="ghost" formnovalidate>${escapeHtml(cancelText)}</button>
        <button value="ok" class="${tone === "danger" ? "danger" : "primary"}">${escapeHtml(confirmText)}</button>
      </div>
    </form>`;
    document.body.appendChild(dlg);
    dlg.addEventListener(
      "close",
      () => {
        resolve(dlg.returnValue === "ok");
        dlg.remove();
      },
      { once: true }
    );
    dlg.showModal();
    const ok = dlg.querySelector('button[value="ok"]');
    if (ok) ok.focus();
  });
}

const HTML_ESCAPES = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
function escapeHtml(s) {
  return String(s == null ? "" : s).replace(/[&<>"']/g, (ch) => HTML_ESCAPES[ch]);
}

/* Only allow http(s), protocol-relative, root-relative or anchor links. */
function safeHref(url) {
  const value = String(url || "").trim();
  if (!value) return "#";
  if (/^(https?:)?\/\//i.test(value)) return value;
  if (value.startsWith("/") || value.startsWith("#")) return value;
  return "#";
}

function revealConsole() {
  const wrap = $("batch-console-wrap") || document.querySelector(".console-wrap");
  if (!wrap) return;
  if (wrap.classList.contains("collapsed")) {
    wrap.classList.remove("collapsed");
    const toggle = $("btn-console-toggle");
    if (toggle) toggle.textContent = "收起";
  }
  wrap.scrollIntoView({ behavior: "smooth", block: "nearest" });
  hideConsoleUnread();
}

function hideConsoleUnread() {
  const badge = $("console-unread");
  if (badge) badge.hidden = true;
}

function consoleVisible() {
  const wrap = $("batch-console-wrap");
  if (!wrap) return false;
  const rect = wrap.getBoundingClientRect();
  return rect.top < window.innerHeight && rect.bottom > 0;
}

function setConsoleMeta(text) {
  const meta = $("console-meta");
  if (meta) meta.textContent = text;
}

function markConsoleUnread() {
  const badge = $("console-unread");
  if (badge && !consoleVisible()) badge.hidden = false;
}

function writeConsoleHtml(html, { empty = false, meta = "", reveal = false } = {}) {
  const el = $("console");
  if (!el) return;
  el.classList.toggle("empty", !!empty);
  el.innerHTML = html;
  if (meta) setConsoleMeta(meta);
  else setConsoleMeta(empty ? "空" : `已更新 · ${new Date().toLocaleTimeString()}`);
  if (reveal) revealConsole();
  else markConsoleUnread();
}

function writeConsoleText(text, { empty = false, meta = "", reveal = false } = {}) {
  const el = $("console");
  if (!el) return;
  el.classList.toggle("empty", !!empty);
  el.textContent = text || "(empty)";
  if (meta) setConsoleMeta(meta);
  else setConsoleMeta(empty ? "空" : `已更新 · ${new Date().toLocaleTimeString()}`);
  if (reveal) revealConsole();
  else markConsoleUnread();
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
        <header class="${ok ? "ok" : "fail"}"><span>${escapeHtml(step.name || "?")}</span><span>${ok ? "成功" : "失败"
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
    lines.push("执行结果会显示在这里。");
    writeConsoleText(lines.join("\n"), { meta: doctor.ok ? "Doctor OK" : "Doctor 有问题" });
    log(doctor.ok ? "自检通过" : "自检有问题，详见执行输出");
  } catch (err) {
    writeConsoleText(
      `自检失败：${err.message || err}\n\n点「一键连通」或上方运维卡片查看输出。`,
      { meta: "Doctor 失败" }
    );
  }
}

async function api(path, options = {}) {
  const { timeoutMs = 600000, ...fetchOpts } = options;
  const headers = { "Content-Type": "application/json", ...(fetchOpts.headers || {}) };
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  let res;
  try {
    res = await fetch(path, {
      credentials: "same-origin",
      ...fetchOpts,
      headers,
      signal: controller.signal,
    });
  } catch (err) {
    if (err && err.name === "AbortError") throw new Error(`请求超时（${Math.round(timeoutMs / 1000)}s）`);
    throw err;
  } finally {
    clearTimeout(timer);
  }
  if (res.status === 304) return { __notModified: true, etag: res.headers.get("ETag") };
  let data = {};
  try {
    data = await res.json();
  } catch (_) {
    data = {};
  }
  if (res.status === 401 && path !== "/api/auth/login" && path !== "/api/auth/status") {
    state.auth = data.auth || state.auth;
    showAuthGate(true);
    throw new Error(data.error || "需要登录");
  }
  if (!res.ok) {
    let detail = data.results
      ? JSON.stringify(data.results, null, 2)
      : data.result
        ? `${data.result.stdout || ""}\n${data.result.stderr || ""}`.trim()
        : "";
    if (!detail) {
      const parts = [data.error, data.hint, data.detail].filter(Boolean);
      detail = parts.length ? parts.join(" — ") : JSON.stringify(data);
    }
    throw new Error(detail || `HTTP ${res.status}`);
  }
  if (res.headers.get("ETag")) data.__etag = res.headers.get("ETag");
  return data;
}

function showAuthGate(show) {
  const gate = document.getElementById("auth-gate");
  if (!gate) return;
  gate.hidden = !show;
  document.body.classList.toggle("auth-locked", !!show);
  const logout = document.getElementById("btn-logout");
  if (logout) logout.hidden = !!show || !(state.auth && state.auth.enabled);
  if (show) {
    const input = document.getElementById("auth-password");
    if (input) setTimeout(() => input.focus(), 50);
  }
}

function renderAuthSessionChip(auth) {
  const chip = document.getElementById("auth-session-chip");
  if (!chip) return;
  if (!auth || !auth.enabled || !auth.authenticated) {
    chip.hidden = true;
    chip.textContent = "";
    return;
  }
  const mode = auth.mode || "session";
  chip.hidden = false;
  chip.textContent = mode === "oidc" ? "已登录 · OIDC" : "已登录 · 密码";
}

async function refreshAuthStatus() {
  const data = await api("/api/auth/status");
  state.auth = data;
  const needLogin = !!(data.enabled && !data.authenticated);
  showAuthGate(needLogin);
  const logout = document.getElementById("btn-logout");
  if (logout) logout.hidden = !(data.enabled && data.authenticated);
  renderAuthSessionChip(data);
  const hint = document.getElementById("auth-hint");
  const oidcBox = document.getElementById("auth-oidc-box");
  const pwBox = document.getElementById("auth-password-box");
  const oidcMeta = document.getElementById("auth-oidc-meta");
  const oidcReady = !!(data.oidc_ready || (data.oidc && data.oidc.ready));
  const showOidc = data.mode === "oidc" && oidcReady;
  const showPassword = !!data.password_login || data.mode === "password";
  if (oidcBox) oidcBox.hidden = !showOidc;
  if (pwBox) pwBox.hidden = !showPassword;
  if (oidcMeta && data.oidc) {
    oidcMeta.textContent = data.oidc.issuer
      ? `IdP · ${data.oidc.issuer}`
      : "OpenID Connect";
  }
  if (hint && data.enabled) {
    if (data.mode === "oidc") {
      hint.textContent = oidcReady
        ? `模式 OIDC · 会话 ${data.session_ttl_hours || 168}h`
        : "模式 OIDC，但尚未配齐 issuer/client_id（见 cluster.control.auth.oidc）";
    } else {
      hint.textContent = data.password_configured
        ? `模式 ${data.mode} · 会话 ${data.session_ttl_hours || 168}h`
        : "已启用鉴权，但尚未设置密码（gpu-control auth set-password）";
    }
  }
  const pwInput = document.getElementById("auth-password");
  if (pwInput) pwInput.required = showPassword && !showOidc;
  return data;
}

async function enterConsoleAfterAuth() {
  const route = currentRoute();
  setView(route.view, { updateHash: false });
  if (route.view === "config" && route.tab) setConfigTab(route.tab);
  setBusy(true);
  try {
    await refresh(false);
    await loadSetup();
    await loadQuality();
    await seedConsole();
    hideConsoleUnread();
  } catch (err) {
    log(String(err.message || err));
  } finally {
    setBusy(false);
  }
}

async function submitLogin(ev) {
  if (ev) ev.preventDefault();
  const errEl = document.getElementById("auth-error");
  const password = document.getElementById("auth-password").value;
  errEl.hidden = true;
  if (!password) {
    errEl.textContent = "请输入密码，或使用 OIDC 登录";
    errEl.hidden = false;
    return;
  }
  try {
    const data = await api("/api/auth/login", {
      method: "POST",
      body: JSON.stringify({ password }),
    });
    state.auth = data.auth || data;
    document.getElementById("auth-password").value = "";
    showAuthGate(false);
    toast("已登录");
    await enterConsoleAfterAuth();
  } catch (err) {
    errEl.textContent = String(err.message || err);
    errEl.hidden = false;
  }
}

async function logout() {
  try {
    await api("/api/auth/logout", { method: "POST", body: "{}" });
  } catch (_) { }
  await refreshAuthStatus();
  toast("已退出");
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

function nodeOption(n) {
  const name = escapeHtml(n.name);
  return `<option value="${name}">${name}</option>`;
}

function fillTargetSelects(nodes) {
  const optsAll = `<option value="all">全部节点</option>` + nodes.map(nodeOption).join("");
  const optsNode = nodes.map(nodeOption).join("");
  for (const id of ["exec-target", "py-target", "job-target"]) {
    fillSelectPreserve($(id), optsAll, state.selectedNode);
  }
  fillSelectPreserve($("deploy-node"), optsNode || `<option value="">暂无节点</option>`, state.selectedNode);
  fillSelectPreserve($("term-node"), optsNode || `<option value="">暂无节点</option>`, state.selectedNode);
  const modelNode = $("model-node");
  if (modelNode) {
    const prev = modelNode.value;
    modelNode.innerHTML =
      `<option value="">全部节点</option>` + nodes.map(nodeOption).join("");
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
  const used = Number(summary.gpus_used || 0);
  const free = Number(summary.gpus_free || 0);
  const total = Number(summary.gpus_total || used + free) || 0;
  const set = (id, text) => {
    const el = $(id);
    if (el) el.textContent = text;
  };
  set("dash-occ-gpu", total ? `${used}/${total}（${Math.round((used / total) * 100)}%）` : "-");
  set("dash-occ-nodes", `${summary.busy_nodes ?? "-"} / ${summary.free_nodes ?? "-"}`);
  set("dash-queue-2", String(summary.queue_pending ?? 0));
  set("dash-cordon-2", String(summary.cordoned ?? 0));
  set(
    "dash-api",
    summary.api_total != null ? `${summary.api_ok ?? 0}/${summary.api_total}` : "-"
  );
}

function renderPresets(presets) {
  const root = document.getElementById("exec-presets");
  root.innerHTML = (presets || [])
    .map((p) => {
      const id = typeof p === "string" ? p : p.id;
      const label = typeof p === "string" ? p : p.label || p.id;
      return `<button type="button" data-preset="${escapeHtml(id)}">${escapeHtml(label)}</button>`;
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
  if (kind === "litellm" && snippets.litellm_yaml) return snippets.litellm_yaml;
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
  const layers = topo.layers || {};
  const ai = layers.ai_gateway || {};
  const nginx = local.nginx || {};
  const proxy = local.openai_compat_proxy || local.litellm_gateway || {};
  const extra = local.extra_http || {};
  const bits = [
    nodeBits.length ? `按节点 ${nodeBits.join(" · ")}` : `默认端口 ${(pool.port_range || []).join("-") || "-"}`,
  ];
  if (ai.model_name) bits.push(`model=${ai.model_name}`);
  if (ai.routing_strategy) bits.push(`route=${ai.routing_strategy}`);
  if (proxy.port) bits.push(`AI Gateway :${proxy.port}`);
  if (extra.port) bits.push(`extra :${extra.port}`);
  if (nginx.installed) bits.push("nginx 已装");
  else bits.push(nginx.status_label || "nginx 不必装（Edge）");
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
    mosaic.innerHTML =
      '<div class="empty-state"><strong>暂无 API 端点</strong><p>先启动 Job 或刷新 GPU 快照，按当前 api.base_port 生成端点。</p></div>';
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
          const lat = h
            ? h.latency_ms != null
              ? `${h.latency_ms}ms`
              : h.ok
                ? "ok"
                : "fail"
            : "n/a";
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
    root.innerHTML =
      '<div class="empty-state"><strong>暂无节点</strong><p>先在「节点」页添加节点，再刷新主机指标。</p></div>';
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
    const host = $("term");
    if (host) {
      host.innerHTML =
        '<div class="empty-state"><strong>终端组件未加载</strong><p>缺少本地 vendor/xterm 资源，请检查 /static/vendor/xterm/。</p></div>';
    }
    toastBad("终端组件未加载");
    return null;
  }
  const host = $("term");
  if (!host) return null;
  const term = new Terminal({
    cursorBlink: true,
    fontFamily:
      'ui-monospace, "SFMono-Regular", Menlo, Consolas, "Liberation Mono", "DejaVu Sans Mono", monospace',
    fontSize: 13,
    theme: {
      background: "#0b1210",
      foreground: "#d5e8df",
      cursor: "#2ec4b6",
      selectionBackground: "rgba(46, 196, 182, 0.28)",
    },
  });
  term.open(host);
  termState.term = term;
  termState.fit = {
    fit: () => {
      if (window.GCFit) window.GCFit.fitTerminal(term, host, sendTermResize);
    },
  };
  if (window.GCFit && window.GCFit.autoFit) {
    termState.cleanupFit = window.GCFit.autoFit(term, host, sendTermResize);
  }
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
    await refreshIngressStatus();
  } catch (err) {
    toast(String(err.message || err));
  } finally {
    setBusy(false);
  }
}

function renderIngressMeta(status) {
  const el = document.getElementById("ingress-meta");
  if (!el || !status) return;
  const edge = status.edge || {};
  const ll = status.litellm || {};
  const pool = (edge.status && edge.status.pool) || {};
  const settings = (edge.status && edge.status.settings) || {};
  el.textContent = [
    `对外入口 ${status.public_base_url || "-"}`,
    `主入口 ${status.preferred || "edge"}`,
    `edge ${edge.running ? "运行中" : "应启动"}${edge.pid ? " pid=" + edge.pid : ""}`,
    settings.strategy ? `LB ${settings.strategy}` : null,
    pool.upstream_count != null ? `上游 ${pool.available ?? "?"}/${pool.upstream_count}` : null,
    `litellm ${ll.running ? "运行中" : ll.ready ? "可选·就绪" : "可选·未配"}`,
    `nginx ${status.nginx && status.nginx.binary
      ? "已装"
      : status.nginx && status.nginx.needed
        ? "未装"
        : "不必装"
    }`,
  ]
    .filter(Boolean)
    .join(" · ");
}

async function refreshIngressStatus() {
  const data = await api("/api/gateway/status");
  renderIngressMeta(data);
  renderDashGateway(data);
  return data;
}

async function startIngress() {
  setBusy(true);
  try {
    const data = await api("/api/gateway/start", {
      method: "POST",
      body: JSON.stringify({ kind: "edge" }),
    });
    toast(data.ok ? "统一入口已启动" : "启动失败");
    log(JSON.stringify(data.results || data, null, 2));
    await refreshIngressStatus();
  } catch (err) {
    toast(String(err.message || err));
  } finally {
    setBusy(false);
  }
}

async function stopIngress() {
  setBusy(true);
  try {
    const data = await api("/api/gateway/stop", {
      method: "POST",
      body: JSON.stringify({ kind: "all" }),
    });
    toast("入口已停止");
    log(JSON.stringify(data.results || data, null, 2));
    await refreshIngressStatus();
  } catch (err) {
    toast(String(err.message || err));
  } finally {
    setBusy(false);
  }
}

function renderNodes(nodes) {
  const root = $("nodes");
  if (!root) return;
  if (!nodes?.length) {
    root.innerHTML =
      '<div class="empty-state"><strong>暂无节点快照</strong><p>先在「集群 · 节点」添加节点并刷新 GPU。</p></div>';
    renderNodeAdmin(nodes || []);
    renderDrawer();
    return;
  }
  root.innerHTML = nodes
    .map((node) => {
      const selected = state.selectedNode === node.name;
      const rawState = node.state || (node.cordoned ? "cordoned" : node.used ? "busy" : "free");
      const st = ["free", "busy", "cordoned"].includes(rawState) ? rawState : "free";
      const name = escapeHtml(node.name);
      const leaseJob = node.lease && node.lease.job ? escapeHtml(node.lease.job) : "";
      const badge = node.cordoned
        ? `<span class="chip warn">已隔离</span>`
        : leaseJob
          ? `<span class="chip warn">${leaseJob}</span>`
          : node.used
            ? `<span class="chip warn">占用中</span>`
            : `<span class="chip ok">空闲</span>`;
      const bars = (node.gpus || [])
        .map((g) => {
          const cls = g.status === "free" ? "free" : "occupied";
          const util = g.utilization_percent != null ? `${g.utilization_percent}%` : "-";
          const mem =
            g.memory_used_mib != null ? `${Math.round(g.memory_used_mib / 1024)}G` : "-";
          const title = `GPU ${g.index ?? "?"} · util ${util} · mem ${mem}`;
          return `<i class="bar ${cls}" title="${escapeHtml(title)}"></i>`;
        })
        .join("");
      return `<button type="button" class="node-tile ${selected ? "selected" : ""} state-${st}"
          data-select="${name}" aria-pressed="${selected ? "true" : "false"}">
        <span class="name">${name}</span>
        <span class="host">${escapeHtml(node.host || "-")}</span>
        <span class="stats"><span class="meta">空闲 ${node.free}/${node.total}</span>${badge}</span>
        <span class="meta node-usage">CPU ${node.cpu_percent ?? "-"}% · 负载 ${node.load1 ?? "-"} · 内存 ${node.mem_used_mb != null ? Math.round(node.mem_used_mb / 1024) + "G" : "-"
        }</span>
        <span class="bars">${bars || '<span class="meta">暂无快照</span>'}</span>
      </button>`;
    })
    .join("");
  root.querySelectorAll("[data-select]").forEach((el) => {
    el.addEventListener("click", () => selectNode(el.dataset.select));
  });
  renderNodeAdmin(nodes);
  renderDrawer();
}

function renderNodeAdmin(nodes) {
  const root = $("node-admin");
  if (!root) return;
  if (!nodes?.length) {
    root.innerHTML =
      '<div class="empty-state compact"><strong>尚未登记节点</strong><p>在上方表单填写主机后添加，再执行 node render。</p></div>';
    return;
  }
  root.innerHTML = nodes
    .map(
      (n) => `<div class="item">
        <div><strong>${escapeHtml(n.name)}</strong><div class="meta">${escapeHtml(n.host || "-")} · ${escapeHtml((n.labels || []).join(",") || "-")}</div></div>
        <button data-remove="${escapeHtml(n.name)}" class="danger tiny">移除</button>
      </div>`
    )
    .join("");
  bindRemoveButtons(document.getElementById("node-admin"));
}

function closeNodeDrawer() {
  const dlg = document.getElementById("node-drawer");
  if (dlg?.open) dlg.close();
}

function openNodeDrawer() {
  const dlg = document.getElementById("node-drawer");
  if (!dlg || typeof dlg.showModal !== "function") return;
  if (!dlg.open) dlg.showModal();
}

function renderDrawer({ open = false } = {}) {
  const body = document.getElementById("drawer-body");
  const dlg = document.getElementById("node-drawer");
  if (!body) return;
  const name = state.selectedNode;
  if (!name || !state.overview) {
    body.innerHTML =
      '<p class="muted">点选节点卡片查看详情。<br/>任务启停请到「任务」页选目标节点。</p>';
    if (dlg?.open) dlg.close();
    return;
  }
  const node = (state.overview.nodes || []).find((n) => n.name === name);
  if (!node) {
    body.innerHTML = '<p class="muted">节点不在当前快照中，请刷新总览。</p>';
    if (dlg?.open) dlg.close();
    return;
  }
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
  const safeName = escapeHtml(node.name);
  const actions = node.cordoned
    ? `<button data-uncordon="${safeName}">取消隔离</button>
       <button data-free="${safeName}" class="danger">腾空</button>`
    : `<button data-cordon="${safeName}">隔离</button>
       <button data-free="${safeName}" class="danger">腾空</button>
       <button data-free-cordon="${safeName}" class="danger">腾空并隔离</button>`;
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
      <button data-procs-refresh="${safeName}" data-keep-enabled="1">刷新进程</button>
      <button data-procs-kill-all="${safeName}" class="danger">结束全部 GPU 进程</button>
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
      closeNodeDrawer();
      document.getElementById("term-node").value = btn.dataset.openTerm;
      setView("terminal");
      connectTerminal();
    });
  });
  if (open || dlg?.open) openNodeDrawer();
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
  renderDrawer({ open: !stay });
  log(`已选中 ${name}`);
  if (!stay) setView("cluster");
}

function bindRemoveButtons(root) {
  root.querySelectorAll("button[data-remove]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const ok = await confirmDialog({
        title: "移除节点",
        message: `确认从 nodes.json 移除 ${btn.dataset.remove}？不会停止该节点上的进程。`,
        confirmText: "移除",
        tone: "danger",
      });
      if (!ok) return;
      setBusy(true);
      try {
        const data = await api("/api/node/remove", {
          method: "POST",
          body: JSON.stringify({ name: btn.dataset.remove }),
        });
        if (state.selectedNode === btn.dataset.remove) state.selectedNode = null;
        log(data.result.stdout || "已移除");
        stateSetup.data = null;
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
    btn.addEventListener("click", async () => {
      const ok = await confirmDialog({
        title: "结束全部 GPU 进程",
        message: `确认结束 ${btn.dataset.procsKillAll} 上全部 GPU compute 进程？会影响正在运行的服务。`,
        confirmText: "全部结束",
        tone: "danger",
      });
      if (!ok) return;
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
    btn.addEventListener("click", async () => {
      const pid = Number(btn.dataset.killPid);
      const node = btn.dataset.killNode;
      const ok = await confirmDialog({
        title: "结束进程",
        message: `确认结束 ${node} 上的 PID ${pid}？`,
        confirmText: "结束进程",
        tone: "danger",
      });
      if (!ok) return;
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
    btn.addEventListener("click", async () => {
      const ok = await confirmDialog({
        title: "腾空节点",
        message: `确认腾空 ${btn.dataset.free}？会停止已注册 Job、结束 GPU 进程并校验空闲。`,
        confirmText: "腾空",
        tone: "danger",
      });
      if (!ok) return;
      wrap(`free ${btn.dataset.free}`, () => runFree(btn.dataset.free));
    });
  });
  root.querySelectorAll("button[data-free-cordon]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const ok = await confirmDialog({
        title: "腾空并隔离",
        message: `确认腾空并隔离 ${btn.dataset.freeCordon}？调度器将跳过该节点。`,
        confirmText: "腾空并隔离",
        tone: "danger",
      });
      if (!ok) return;
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

function jobOptionLabel(j) {
  const src = j.source === "local" ? "local" : j.source === "builtin" ? "内置" : "";
  const gpus = j.gpus_per_node != null ? `${j.gpus_per_node}GPU` : "";
  const kind = j.kind === "train" ? "训练" : "";
  const dist = j.distributed ? "多机" : "";
  const bits = [j.name, kind, dist, gpus, src].filter(Boolean);
  return bits.join(" · ");
}

function renderJobs(jobs) {
  const list = jobs || [];
  const opts =
    list.map((j) => `<option value="${escapeHtml(j.name)}">${escapeHtml(jobOptionLabel(j))}</option>`).join("") ||
    `<option value="">（无任务 — 请安装模板）</option>`;
  for (const id of ["job-select", "setup-job-pick"]) {
    const sel = document.getElementById(id);
    if (!sel) continue;
    const prev = sel.value;
    const withEmpty =
      id === "setup-job-pick"
        ? `<option value="">— 选择已有 Job —</option>${opts}`
        : opts;
    sel.innerHTML = withEmpty;
    if (prev && [...sel.options].some((o) => o.value === prev)) sel.value = prev;
  }
  const root = document.getElementById("jobs");
  if (root) {
    root.innerHTML =
      list
        .map((j) => {
          const runtime = j.runtime || "process";
          const args = (j.extra_args || []).join(" ");
          const src = j.source || "";
          const kind = j.kind === "train" ? "train" : "";
          const dist = j.distributed ? "distributed" : "";
          return `<div class="item"><div><strong>${escapeHtml(j.name)}</strong>
          <div class="meta">${escapeHtml(j.description || "")}</div>
          <div class="meta">${escapeHtml(runtime)}${kind ? " · " + kind : ""}${dist ? " · " + dist : ""
            }${src ? " · " + escapeHtml(src) : ""}${j.model ? " · model=" + escapeHtml(j.model) : ""
            }
          ${args ? " · " + escapeHtml(args) : ""}</div></div>
          <span class="chip">${j.gpus_per_node || "?"} GPU</span></div>`;
        })
        .join("") ||
      '<div class="empty-state"><strong>暂无已安装 Job</strong><p>展开下方「从模板安装」安装 http-serve / train / vllm 等模板。</p></div>';
  }
  syncJobArgsHint(list);
  syncJobSelectHint(list);
  syncDeployGpusDefault(list);
}

function syncJobSelectHint(jobs) {
  const list = jobs || state.overview?.jobs || [];
  const describe = (name) => {
    const job = list.find((j) => j.name === name);
    if (!job) return "还没有任务。到「任务」页安装模板或创建 Job。";
    return `${job.description || job.name} · ${job.gpus_per_node ?? "?"} GPU/节点${job.distributed ? " · 多机" : ""
      }`;
  };
  const sel = $("job-select");
  const hint = $("job-select-hint");
  if (hint) hint.textContent = describe(sel?.value);
}

function syncDeployGpusDefault(jobs) {
  const sel = document.getElementById("job-select");
  const input = document.getElementById("deploy-gpus");
  const job = (jobs || state.overview?.jobs || []).find((j) => j.name === sel?.value);
  if (!job) return;
  const per = Number(job.gpus_per_node || 4);
  if (input && !input.dataset.touched) input.value = String(per);
}

async function refreshJobTemplates() {
  const data = await api("/api/jobs/templates");
  const templates = data.templates || [];
  state.jobTemplates = templates;
  const sel = document.getElementById("job-template");
  if (sel) {
    const byCat = new Map();
    for (const t of templates) {
      const cat = t.category || "其他";
      if (!byCat.has(cat)) byCat.set(cat, []);
      byCat.get(cat).push(t);
    }
    const parts = [];
    for (const [cat, rows] of byCat) {
      parts.push(`<optgroup label="${escapeHtml(cat)}">`);
      for (const t of rows) {
        const mark = t.installed ? "已有" : "可装";
        const star = t.recommended ? "★ " : "";
        parts.push(
          `<option value="${escapeHtml(t.id)}">${star}${escapeHtml(t.id)} · ${escapeHtml(
            t.runtime || ""
          )} (${mark})</option>`
        );
      }
      parts.push("</optgroup>");
    }
    sel.innerHTML = parts.join("") || `<option value="">（无模板）</option>`;
  }
  renderJobTemplatesList(templates);
  syncJobTemplateHint();
  return templates;
}

function renderJobTemplatesList(templates) {
  const el = $("job-templates-list");
  if (!el) return;
  if (!templates?.length) {
    el.innerHTML = `<div class="empty">未发现模板</div>`;
    return;
  }
  el.innerHTML = templates
    .map((t) => {
      const badges = [
        t.category || "通用",
        t.runtime || "process",
        t.installed ? "已安装" : "可安装",
        t.recommended ? "推荐" : "",
      ]
        .filter(Boolean)
        .map((b) => `<span class="chip">${escapeHtml(b)}</span>`)
        .join("");
      return `<button type="button" class="item item-btn" data-pick-template="${escapeHtml(t.id)}">
        <div>
          <strong>${escapeHtml(t.id)}</strong>
          <div class="meta">${escapeHtml(t.description || "")}</div>
          <div class="chip-row">${badges}</div>
        </div>
      </button>`;
    })
    .join("");
  el.querySelectorAll("[data-pick-template]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const sel = $("job-template");
      if (sel) {
        sel.value = btn.dataset.pickTemplate;
        syncJobTemplateHint();
      }
    });
  });
  const fold = $("job-templates-fold");
  if (fold && templates.some((t) => t.recommended && !t.installed)) {
    fold.open = true;
  }
}

function syncJobTemplateHint() {
  const meta = $("job-templates-meta");
  if (!meta) return;
  const id = $("job-template")?.value;
  const tpl = (state.jobTemplates || []).find((t) => t.id === id);
  const total = (state.jobTemplates || []).length;
  const pending = (state.jobTemplates || []).filter((t) => t.recommended && !t.installed).length;
  if (!tpl) {
    meta.textContent = total
      ? `${total} 个模板 · 推荐待装 ${pending}`
      : "未发现模板";
    return;
  }
  meta.textContent = `${tpl.category || ""} · ${tpl.description || tpl.id} · ${tpl.runtime || "process"
    }${tpl.installed ? " · 已安装" : " · 可安装"}${tpl.recommended ? " · 推荐" : ""} · 待装推荐 ${pending}`;
}

async function installSelectedJobTemplate() {
  const template = document.getElementById("job-template")?.value;
  const name = document.getElementById("job-template-name")?.value?.trim() || "";
  if (!template) return toast("请选择模板");
  setBusy(true);
  try {
    const data = await api("/api/jobs/install-template", {
      method: "POST",
      body: JSON.stringify({ template, name: name || undefined }),
    });
    toast(`已安装 Job：${data.name}`);
    if (data.overview) applyOverview(data.overview);
    else await refresh(false);
    await refreshJobTemplates();
    stateSetup.data = null;
  } catch (err) {
    actionError("安装 Job 模板", err);
  } finally {
    setBusy(false);
  }
}

async function installRecommendedJobTemplates() {
  setBusy(true);
  try {
    const data = await api("/api/jobs/install-template", {
      method: "POST",
      body: JSON.stringify({ recommended: true }),
    });
    const n = (data.installed || []).length;
    const skip = (data.skipped || []).length;
    toast(n ? `推荐包：新装 ${n}，跳过 ${skip}` : `推荐包已齐（跳过 ${skip}）`);
    if (data.overview) applyOverview(data.overview);
    else await refresh(false);
    await refreshJobTemplates();
    stateSetup.data = null;
  } catch (err) {
    actionError("安装推荐 Job 包", err);
  } finally {
    setBusy(false);
  }
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
  syncJobSelectHint(jobs);
  syncDeployGpusDefault(jobs);
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
        .join("") ||
      '<div class="empty-state compact"><strong>无缓存目录</strong><p>可在 models.json 中登记 cache 路径。</p></div>';
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
  const root = $("queue");
  if (!root) return;
  if (!queue || !queue.length) {
    root.innerHTML =
      '<div class="empty-state"><strong>队列为空</strong><p>在任务页选择 Job 后入队，或直接部署。</p></div>';
    return;
  }
  const recent = [...queue].reverse().slice(0, 10);
  root.innerHTML = `<table class="table"><thead><tr><th>ID</th><th>状态</th><th>任务</th><th></th></tr></thead><tbody>
    ${recent
      .map((wl) => {
        const canCancel = wl.status === "pending" || wl.status === "admitted";
        const id = escapeHtml(wl.id);
        return `<tr>
          <td>${id}<div class="meta">${escapeHtml(wl.gpus)} GPU</div></td>
          <td>${escapeHtml(wl.status)}</td>
          <td>${escapeHtml(wl.job)}</td>
          <td>${canCancel ? `<button data-cancel="${id}" class="danger tiny">取消</button>` : ""}</td>
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
        actionError("取消队列任务", err);
      } finally {
        setBusy(false);
      }
    });
  });
}

function renderLeases(leases) {
  const root = $("leases");
  if (!root) return;
  if (!leases || !leases.length) {
    root.innerHTML =
      '<div class="empty-state"><strong>暂无占用登记</strong><p>部署任务或手动 acquire 后可在这里释放。</p></div>';
    return;
  }
  root.innerHTML = leases
    .map((l) => {
      const id = escapeHtml(l.id);
      return `<div class="item">
        <div><strong>${id}</strong><div class="meta">${escapeHtml(l.job)} · ${escapeHtml((l.nodes || []).join(", "))}</div></div>
        <button data-lease="${id}" class="danger tiny">Release</button>
      </div>`;
    })
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
        actionError("释放 Lease", err);
      } finally {
        setBusy(false);
      }
    });
  });
}

function renderProbe(probe) {
  const root = $("probe");
  if (!root) return;
  if (!probe || !probe.matrix) {
    root.innerHTML =
      '<div class="empty-state"><strong>尚未探测互通</strong><p>点「探测互通」检查节点间 SSH 可达性。</p></div>';
    return;
  }
  const nodes = probe.nodes || [];
  const matrix = probe.matrix || {};
  const head = nodes.map((n) => `<th>${escapeHtml(n)}</th>`).join("");
  const body = nodes
    .map((src) => {
      const cells = nodes
        .map((dst) => {
          if (src === dst) return "<td>·</td>";
          const cell = matrix[src] && matrix[src][dst];
          if (!cell) return "<td>?</td>";
          return `<td class="probe-cell ${cell.ok ? "ok" : "bad"}">${cell.ok ? "Y" : "N"}</td>`;
        })
        .join("");
      return `<tr><th>${escapeHtml(src)}</th>${cells}</tr>`;
    })
    .join("");
  root.innerHTML = `<div class="meta">ok=${probe.ok_count ?? "-"} fail=${probe.fail_count ?? "-"}</div>
    <table class="table"><thead><tr><th></th>${head}</tr></thead><tbody>${body}</tbody></table>`;
}

function applyOverview(data) {
  // Light polls omit lb / full probe; keep previous heavy fields.
  if (data.light && state.overview) {
    data = {
      ...state.overview,
      ...data,
      lb: data.lb || state.overview.lb,
      probe: data.probe?.matrix ? data.probe : state.overview.probe || data.probe,
      notes: data.notes || state.overview.notes,
      ingress: data.ingress || state.overview.ingress || state.ingress,
    };
  }
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
  renderJobs(data.jobs || []);
  if (data.quality) renderQuality(data.quality);
  renderView(state.view);
  const generatedAt = $("generated-at");
  if (generatedAt) generatedAt.textContent = data.generated_at || "-";
  const rayNote = $("notes-line");
  if (rayNote) {
    rayNote.textContent = (data.notes && (data.notes.innovation || data.notes.strategy)) || "";
  }
}

/* Render only the active view; hidden pages are filled when switched to. */
function renderView(name) {
  const data = state.overview;
  if (!data) return;
  if (name === "actions") {
    renderDashboard(data);
  } else if (name === "cluster" || name === "nodes") {
    renderNodes(data.nodes || []);
  } else if (name === "workloads") {
    renderQueue(data.queue || []);
    renderLeases(data.leases || []);
    renderProbe(data.probe || {});
  } else if (name === "endpoints") {
    renderPool(data);
  } else if (name === "terminal") {
    renderHostMetrics(data);
  }
}

function renderToolkits(payload) {
  const bundles = payload.bundles || [];
  const toolkits = payload.toolkits || [];
  const stateTk = payload.state || {};
  const bundleRoot = document.getElementById("toolkit-bundles");
  if (bundleRoot) {
    bundleRoot.innerHTML =
      bundles
        .map(
          (b) => `<button type="button" class="chip" data-toolkit-bundle="${escapeHtml(b.id)}" title="${escapeHtml(b.description || "")}">
            ${escapeHtml(b.name || b.id)}
          </button>`
        )
        .join("") ||
      '<div class="empty-state compact"><strong>无工具栈捆绑</strong><p>检查 toolkits 配置中的 bundles。</p></div>';
    bundleRoot.querySelectorAll("[data-toolkit-bundle]").forEach((btn) => {
      btn.addEventListener("click", async () => {
        const ok = await confirmDialog({
          title: "安装工具栈",
          message: `在全部 GPU 节点安装 ${btn.dataset.toolkitBundle}？pip 安装可能持续数分钟。`,
          confirmText: "开始安装",
        });
        if (!ok) return;
        runToolkit("install", { bundle: btn.dataset.toolkitBundle });
      });
    });
  }
  const list = document.getElementById("toolkit-list");
  if (list) {
    list.innerHTML =
      toolkits
        .map((t) => {
          const pkgs = (t.install_pip || []).join(", ") || "（仅探测）";
          return `<div class="item">
            <div>
              <strong>${escapeHtml(t.name || t.id)}</strong>
              <span class="chip">${escapeHtml(t.group || "")}</span>
              <div class="meta">${escapeHtml(t.description || "")}</div>
              <div class="meta">pip: ${escapeHtml(pkgs)}</div>
            </div>
            <button class="ghost tiny" data-toolkit-id="${escapeHtml(t.id)}">探测</button>
          </div>`;
        })
        .join("") ||
      '<div class="empty-state compact"><strong>无工具栈定义</strong><p>检查 config/local/toolkits.json。</p></div>';
    list.querySelectorAll("[data-toolkit-id]").forEach((btn) => {
      btn.addEventListener("click", () => runToolkit("probe", { ids: [btn.dataset.toolkitId] }));
    });
  }
  const meta = document.getElementById("toolkit-state-meta");
  if (meta) meta.textContent = stateTk.generated_at ? `探测于 ${stateTk.generated_at}` : "尚未探测";
  const nodesRoot = document.getElementById("toolkit-nodes");
  if (nodesRoot) {
    const nodes = stateTk.nodes || {};
    nodesRoot.innerHTML =
      Object.keys(nodes)
        .sort()
        .map((name) => {
          const entry = nodes[name] || {};
          const chips = (entry.toolkits || [])
            .map((x) => {
              const cls = x.ok ? "ok" : "bad";
              return `<span class="chip ${cls}">${escapeHtml(x.id)}${x.version ? " " + escapeHtml(String(x.version)) : ""}</span>`;
            })
            .join(" ");
          return `<div class="item"><div><strong>${escapeHtml(name)}</strong>
            <div class="meta">python ${escapeHtml(entry.python || "-")} · rc=${entry.rc ?? "-"}</div>
            <div class="chip-row">${chips || '<span class="meta">无结果</span>'}</div>
          </div></div>`;
        })
        .join("") || '<div class="meta">暂无探测结果</div>';
  }
}

async function refreshToolkits() {
  const data = await api("/api/toolkits");
  renderToolkits(data);
  return data;
}

async function runToolkit(action, extra = {}) {
  const label = action === "probe" ? "探测工具栈" : action === "install" ? "安装工具栈" : action;
  setBusy(true);
  try {
    const body = { ...extra };
    const data = await api(`/api/toolkits/${action}`, {
      method: "POST",
      body: JSON.stringify(body),
    });
    writeConsoleText(data.result?.stdout || data.result?.stderr || action, {
      meta: `toolkits ${action}`,
    });
    if (data.toolkits) renderToolkits(data.toolkits);
    if (data.overview) applyOverview(data.overview);
    if (data.result?.rc === 0) toastOk(`${label}完成`);
    else toastBad(`${label}有失败，详见执行输出`);
  } catch (err) {
    writeConsoleText(String(err.message || err), { meta: `toolkits ${action}` });
    actionError(label, err);
  } finally {
    setBusy(false);
  }
}

function renderAccessStatus(data) {
  const root = $("dash-board");
  if (!root) return;
  const summary = data.summary || {};
  const ingress = data.ingress || state.ingress || {};
  const edge = ingress.edge || {};
  const tls = ingress.tls || {};
  const domain = ingress.domain || (ingress.domains || [])[0] || "";
  const tk = data.toolkits || {};
  const tkSum = tk.summary || {};
  const wd = data.watchdog || {};
  const apiOk = summary.api_ok;
  const apiTotal = summary.api_total;
  const toneOf = (severity) =>
    severity === "error" ? "bad" : severity === "warn" ? "warn" : severity === "ok" ? "ok" : "neutral";
  const viewOf = (c) => (c.id === "tls" ? "config" : "endpoints");
  const companionLabel = {
    edge: "统一入口",
    nginx: "nginx",
    litellm: "AI Gateway",
    tls: "HTTPS / TLS",
  };
  const enrich = (c) => {
    let hint = c.detail || "";
    if (c.id === "edge") {
      const settings = (edge.status && edge.status.settings) || {};
      const pool = (edge.status && edge.status.pool) || {};
      if (settings.strategy) hint += ` · LB ${settings.strategy}`;
      if (pool.upstream_count != null) {
        hint += ` · 上游 ${pool.available ?? "?"}/${pool.upstream_count}`;
      }
    }
    return hint;
  };

  const rows = [
    {
      label: "API 健康",
      value: apiTotal != null ? `${apiOk ?? 0}/${apiTotal}` : "未探测",
      hint:
        apiTotal != null
          ? apiOk === apiTotal
            ? "全部端点正常"
            : `${Math.max(0, apiTotal - (apiOk || 0))} 路异常`
          : "尚未探测",
      tone: apiTotal != null ? (apiOk === apiTotal ? "ok" : "warn") : "neutral",
      view: "endpoints",
    },
  ];

  const companions = Array.isArray(ingress.companions) ? ingress.companions : [];
  if (companions.length) {
    for (const id of ["edge", "nginx", "litellm", "tls"]) {
      const c = companions.find((row) => row.id === id);
      if (!c) continue;
      rows.push({
        label: companionLabel[id] || c.name || id,
        value: c.label || c.state || "-",
        hint: enrich(c),
        tone: toneOf(c.severity),
        view: viewOf(c),
      });
    }
  } else {
    // Older payloads: keep a useful fallback rather than an empty panel.
    rows.push(
      {
        label: "统一入口",
        value: edge.running ? "运行中" : ingress.ingress && ingress.ingress.enabled === false ? "未启用" : "未运行",
        hint: edge.listen || ingress.public_base_url || "未配置监听",
        tone: edge.running ? "ok" : "neutral",
        view: "endpoints",
      },
      {
        label: "HTTPS / TLS",
        value: tls.label || (tls.enabled ? "已启用" : "未启用"),
        hint: tls.ready ? tls.not_after || "证书就绪" : tls.enabled ? (tls.reasons || []).join("；") || "缺少证书" : "HTTP 明文",
        tone: tls.enabled && !tls.ready ? "warn" : tls.ready ? "ok" : "neutral",
        view: "config",
      }
    );
  }

  rows.push({
    label: "域名",
    value: domain || "未配置",
    hint: ingress.public_base_url || "使用 IP:端口访问",
    tone: domain ? "ok" : "neutral",
    view: "config",
  });
  rows.push({
    label: "自动重启",
    value: wd.running ? "运行中" : wd.enabled ? "未运行" : "未启用",
    hint: `${(wd.jobs || []).join(", ") || "跟随默认 Job"} · ${wd.interval_sec || 60}s · 连败 ${wd.failure_threshold || 2} 次重启 / 上限 ${wd.max_restarts || 3} 次`,
    tone: wd.running ? "ok" : wd.enabled ? "warn" : "neutral",
    view: "workloads",
  });
  rows.push({
    label: "工具栈",
    value: tkSum.total ? `${tkSum.ok ?? 0}/${tkSum.total}` : "未探测",
    hint: tk.generated_at ? `探测于 ${tk.generated_at}` : "训练 / 推理常用依赖",
    tone: tkSum.total ? (tkSum.ok === tkSum.total ? "ok" : "warn") : "neutral",
    view: "toolkits",
  });

  root.innerHTML = rows
    .map((row) => {
      const tone = row.tone || "neutral";
      return `<button type="button" class="status-row ${tone}" data-view-jump="${row.view || "endpoints"}"
          title="打开 ${escapeHtml(row.label)} 详情">
        <span class="status-dot ${tone}" aria-hidden="true"></span>
        <span class="status-main">
          <span class="status-label">${escapeHtml(row.label)}</span>
          <span class="status-hint">${escapeHtml(row.hint || "")}</span>
        </span>
        <span class="chip chip--${tone}">${escapeHtml(String(row.value))}</span>
      </button>`;
    })
    .join("");
  const at = $("dash-board-at");
  if (at) at.textContent = data.generated_at || "实时总览";
  const urlEl = $("dash-ingress-url");
  if (urlEl) {
    const url = ingress.public_base_url || edge.url || "";
    urlEl.textContent = url ? `对外 ${url}` : "对外 URL 未配置";
  }
}

function renderGpuPool(data) {
  const summary = data.summary || {};
  const free = Number(summary.gpus_free || 0);
  const used = Number(summary.gpus_used || 0);
  const total = Number(summary.gpus_total || free + used) || 0;
  const usedPct = total ? Math.round((used / total) * 100) : 0;

  const usedEl = $("dash-gpu-used");
  if (usedEl) usedEl.textContent = total ? String(used) : "-";
  const totalEl = $("dash-gpu-total");
  if (totalEl) totalEl.textContent = total ? String(total) : "-";
  const freeEl = $("dash-gpu-free");
  if (freeEl) {
    freeEl.textContent = total ? `空闲 ${free}` : "空闲 -";
    freeEl.className = `chip chip--${free > 0 ? "ok" : total ? "warn" : "neutral"}`;
  }
  const bar = $("dash-bar-used");
  if (bar) bar.style.width = `${usedPct}%`;
  const meter = document.querySelector(".dash-bar[role='progressbar']");
  if (meter) meter.setAttribute("aria-valuenow", String(usedPct));
  const meta = $("dash-gpu-meta");
  if (meta) {
    meta.textContent = total
      ? `${used}/${total} 占用 · ${usedPct}%${data.cached ? " · 缓存" : ""}`
      : "暂无 GPU 数据";
  }

  const strip = $("dash-node-strip");
  if (!strip) return;
  const nodes = data.nodes || [];
  strip.innerHTML =
    nodes
      .map((node) => {
        const name = escapeHtml(node.name);
        const totalGpus = Number(node.total || (node.gpus || []).length) || 0;
        const usedGpus = Number(node.used || 0);
        const cls = node.cordoned ? "cordoned" : usedGpus ? "busy" : "free";
        const leaseJob = node.lease && node.lease.job ? escapeHtml(node.lease.job) : "";
        const gpus =
          (node.gpus || []).length > 0
            ? node.gpus
            : Array.from({ length: totalGpus }, (_, i) => ({
              index: i,
              status: i < usedGpus ? "occupied" : "free",
            }));
        const segs = gpus
          .map((g) => {
            const status = g.status === "free" ? "free" : "occupied";
            const util = g.utilization_percent != null ? `${g.utilization_percent}%` : "-";
            const mem =
              g.memory_used_mib != null ? `${Math.round(g.memory_used_mib / 1024)}G` : "-";
            return `<i class="gpu-seg ${status}" title="GPU ${g.index ?? "?"} · util ${util} · mem ${mem}"></i>`;
          })
          .join("");
        return `<button type="button" class="gpu-node-row ${cls}" data-select="${name}"
            title="${escapeHtml(node.host || "")}${leaseJob ? " · " + leaseJob : ""}">
          <span class="gpu-node-name">${name}${leaseJob ? `<span class="meta"> · ${leaseJob}</span>` : ""}</span>
          <span class="gpu-segments" aria-hidden="true">${segs}</span>
          <span class="gpu-node-count">${usedGpus}/${totalGpus || "-"}</span>
        </button>`;
      })
      .join("") || '<p class="meta">暂无节点快照</p>';
  strip.querySelectorAll("[data-select]").forEach((el) => {
    el.addEventListener("click", () => selectNode(el.dataset.select));
  });
}

function renderDashboard(data) {
  const summary = data.summary || {};
  const free = Number(summary.gpus_free || 0);
  const used = Number(summary.gpus_used || 0);
  const total = Number(summary.gpus_total || free + used) || 0;
  const usedPct = total ? Math.round((used / total) * 100) : 0;
  renderGpuPool(data);
  renderAccessStatus(data);

  const setText = (id, text) => {
    const el = $(id);
    if (el) el.textContent = text;
  };

  const cluster = data.cluster || {};
  const ctrl = data.control || {};
  const ctrlHost = ctrl.host || ctrl.ansible_host || "";
  setText("dash-cluster-name", cluster.name || "-");
  setText("dash-cluster-strategy", cluster.strategy || "-");
  setText(
    "dash-cluster-control",
    ctrlHost ? `${ctrl.node || ctrl.name || "control"}@${ctrlHost}` : ctrl.node || "-"
  );
  setText("dash-cluster-job", cluster.default_job || "-");
  setText(
    "dash-cluster-nodes",
    `${summary.nodes ?? (data.nodes || []).length} · 忙 ${summary.busy_nodes ?? "-"} · 闲 ${summary.free_nodes ?? "-"}`
  );
  setText("dash-cluster-cordon", String(summary.cordoned ?? 0));
  setText("dash-cluster-at", data.generated_at || "-");

  setText("dash-jobs", String(summary.jobs ?? (data.jobs || []).length));
  setText("dash-leases", String(summary.leases ?? (data.leases || []).length));
  setText("dash-queue", String(summary.queue_pending ?? 0));
  setText("dash-occ-gpu", total ? `${used}/${total}（${usedPct}%）` : "-");
  setText(
    "dash-occ-nodes",
    `${summary.busy_nodes ?? "-"} / ${summary.free_nodes ?? "-"}`
  );
  const apiOk = summary.api_ok;
  const apiTotal = summary.api_total;
  setText("dash-api", apiTotal != null ? `${apiOk ?? 0}/${apiTotal}` : "-");

  setText("dash-queue-2", String(summary.queue_pending ?? 0));
  setText("dash-cordon-2", String(summary.cordoned ?? 0));

  const leasesRoot = $("dash-leases-list");
  if (leasesRoot) {
    const leases = data.leases || [];
    leasesRoot.innerHTML =
      leases
        .slice(0, 8)
        .map((lease) => {
          const nodes = (lease.nodes || []).join(", ");
          return `<div class="item">
            <div><strong>${escapeHtml(lease.job || lease.id || "lease")}</strong>
              <div class="meta">${escapeHtml(nodes)} · ${escapeHtml(lease.gpus ?? "?")}GPU${lease.note ? " · " + escapeHtml(lease.note) : ""}</div>
            </div>
          </div>`;
        })
        .join("") || '<div class="empty-state compact"><strong>无占用登记</strong><p>节点全部空闲或未登记 Lease。</p></div>';
  }

  renderDashGateway(data.ingress || state.ingress);
  if (data.ingress) state.ingress = data.ingress;
  if (data.notices) renderNotices(data.notices);
  if (data.metrics_tail) renderTrends(data.metrics_tail);
  const syncAt = $("notices-sync-at");
  if (syncAt && data.generated_at) syncAt.textContent = `规则告警 · ${data.generated_at}`;

  const tk = data.toolkits || {};
  const chips = $("dash-toolkit-chips");
  if (chips) {
    const rows = tk.by_id || [];
    chips.innerHTML =
      rows
        .map((r) => {
          const ok = (r.ok || 0) > 0 && (r.fail || 0) === 0;
          const mixed = (r.ok || 0) > 0 && (r.fail || 0) > 0;
          const cls = ok ? "ok" : mixed ? "" : "bad";
          const ver = (r.versions || [])[0] || "";
          return `<span class="chip ${cls}">${escapeHtml(r.id)}${ver ? " " + escapeHtml(ver) : ""} · ${r.ok || 0}ok</span>`;
        })
        .join("") || '<span class="meta">尚未探测工具栈</span>';
  }
  const tkMeta = $("dash-toolkit-meta");
  if (tkMeta) {
    tkMeta.textContent = tk.generated_at
      ? `探测于 ${tk.generated_at}`
      : "尚未探测工具栈";
  }
  syncJobSelectHint(data.jobs || []);
}

function renderDashGateway(status) {
  const root = document.getElementById("dash-gateway");
  if (!root) return;
  if (!status) {
    root.innerHTML = '<p class="muted">入口状态未加载</p>';
    return;
  }
  state.ingress = status;
  renderIngressMeta(status);
  const edge = status.edge || {};
  const ll = status.litellm || {};
  const nginx = status.nginx || {};
  const pool = (edge.status && edge.status.pool) || {};
  const settings = (edge.status && edge.status.settings) || {};
  const publicUrl = status.public_base_url || edge.url || "";
  const healthz = edge.healthz || (publicUrl ? `${publicUrl}/_gpu_control/health` : "");
  const companions = Array.isArray(status.companions) ? status.companions : null;
  const rows = companions
    ? companions.map((c) => {
      const links = [];
      if (c.id === "edge") {
        if (publicUrl) links.push({ href: publicUrl, label: publicUrl });
        if (healthz) links.push({ href: healthz, label: "health" });
      } else if (c.id === "litellm" && ll.url && ll.running) {
        links.push({ href: ll.url, label: ll.url });
      }
      let detail = c.detail || "";
      if (c.id === "edge") {
        const extra = [
          settings.strategy ? `LB ${settings.strategy}` : null,
          pool.upstream_count != null ? `上游 ${pool.available ?? "?"}/${pool.upstream_count}` : null,
        ]
          .filter(Boolean)
          .join(" · ");
        if (extra) detail = detail ? `${detail} · ${extra}` : extra;
      }
      if (c.id === "litellm" && detail.includes("/")) {
        detail = detail.replace(/\/opt\/[^\s·]+/g, (p) => {
          const parts = p.split("/").filter(Boolean);
          return parts.length > 2 ? `…/${parts.slice(-2).join("/")}` : p;
        });
      }
      return {
        name: c.name,
        tone: c.state === "running" || c.state === "installed" || c.state === "ready" || c.state === "not_needed"
          ? c.state === "running" || c.state === "installed"
            ? "on"
            : "idle"
          : c.severity === "error" || c.severity === "warn"
            ? "bad"
            : "off",
        chip: c.label,
        detail,
        links,
      };
    })
    : [
      {
        name: "Edge LB",
        tone: edge.running ? "on" : "bad",
        chip: edge.running ? "运行中" : "应启动",
        detail: edge.listen || "",
        links: [
          publicUrl ? { href: publicUrl, label: publicUrl } : null,
          healthz ? { href: healthz, label: "health" } : null,
        ].filter(Boolean),
      },
      {
        name: "nginx",
        tone: nginx.binary ? "on" : "idle",
        chip: nginx.binary ? "已装" : nginx.needed ? "未装" : "不必装",
        detail: nginx.hint || "",
        links: [],
      },
      {
        name: "LiteLLM",
        tone: ll.running ? "on" : ll.ready ? "idle" : "off",
        chip: ll.running ? "运行中" : ll.ready ? "可选·就绪" : "可选·未配",
        detail: ll.dir || "",
        links: [],
      },
    ];
  root.innerHTML = rows
    .map((row) => {
      const links = (row.links || [])
        .map((l) => `<a href="${escapeHtml(safeHref(l.href))}" target="_blank" rel="noopener">${escapeHtml(l.label)}</a>`)
        .join("");
      return `<div class="dash-gw-row ${row.tone || "off"}">
        <div class="name">${escapeHtml(row.name)}</div>
        <div>
          <div class="meta">${escapeHtml(row.detail || "")}</div>
          <div class="links">${links || '<span class="meta">—</span>'}</div>
        </div>
        <span class="chip">${escapeHtml(row.chip || "")}</span>
      </div>`;
    })
    .join("");
}

function renderQuality(q) {
  if (!q) return;
  state.quality = q;
  const set = (id, text) => {
    const el = $(id);
    if (el) el.textContent = text;
  };
  const score = Number(q.score);
  const hasScore = Number.isFinite(score);
  const ring = $("quality-ring");
  if (ring) {
    ring.style.setProperty("--score", String(hasScore ? Math.max(0, Math.min(100, score)) : 0));
    ring.dataset.tone = hasScore ? (score >= 85 ? "ok" : score >= 60 ? "warn" : "bad") : "idle";
    ring.setAttribute("aria-label", `集群健康评分 ${hasScore ? score : "-"} ${q.grade || ""}`.trim());
  }
  set("quality-score", hasScore ? String(score) : "-");
  set("quality-grade", q.grade || "未检测");
  const ssh = q.ssh || {};
  const api = q.api || {};
  const mesh = q.mesh || {};
  const gpus = q.gpus || {};
  set("q-ssh", ssh.total != null ? `${ssh.ok_count ?? 0}/${ssh.total}` : "-");
  set("q-api", api.total != null ? `${api.ok ?? 0}/${api.total}` : "-");
  set("q-mesh", mesh.total ? `${mesh.ok}/${mesh.total}` : "-");
  set("q-gpu", gpus.total != null ? `${gpus.free}/${gpus.total}` : "-");
  set(
    "quality-updated",
    q.updated_at ? `更新于 ${q.updated_at}` : "完成「全量同步」或「一键连通」后更新评分"
  );

  const results = (ssh.results || []).slice().sort((a, b) => (a.node || "").localeCompare(b.node || ""));
  const maxLat = Math.max(1, ...results.map((r) => r.latency_ms || 0));
  const bars = $("ssh-latency-bars");
  if (!bars) return;
  if (!results.length) {
    bars.innerHTML = '<p class="muted">尚未探测 SSH</p>';
    set("ssh-latency-meta", "尚未探测");
  } else {
    const lat = ssh.latency_ms || {};
    set(
      "ssh-latency-meta",
      `平均 ${lat.avg ?? "-"}ms · 最快 ${lat.min ?? "-"}ms · 最慢 ${lat.max ?? "-"}ms`
    );
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

function renderRunlog(payload) {
  const root = document.getElementById("runlog-list");
  const meta = document.getElementById("runlog-meta");
  if (!root) return;
  const items = payload.items || [];
  root.innerHTML =
    items
      .slice(0, 12)
      .map((item) => {
        const cls = item.ok === false ? "fail" : item.ok ? "ok" : "";
        return `<div class="item ${cls}">
          <div><strong>${escapeHtml(item.kind || "")}</strong>
            <span class="meta"> ${escapeHtml(item.ts || "")}</span>
            <div class="meta">${escapeHtml(item.summary || "")}</div>
          </div>
          <span class="chip">${item.ok === false ? "失败" : item.ok ? "成功" : "—"}</span>
        </div>`;
      })
      .join("") ||
    '<div class="empty-state compact"><strong>暂无运行日志</strong><p>CLI / 一键动作 / 检测修复会自动记录。</p></div>';
  if (meta) {
    meta.textContent = payload.total
      ? `${items.length}/${payload.total} · ${payload.path || "state/runlog.jsonl"}`
      : "记录 CLI / 一键动作 / 检测修复";
  }
}

async function refreshRunlog() {
  const data = await api("/api/runlog?limit=30");
  renderRunlog(data);
  return data;
}

async function runOneclick(action, extra = {}) {
  setBusy(true);
  try {
    const body = { action, ...extra };
    if (action === "deploy") {
      body.job = document.getElementById("job-select")?.value || "";
      if (!body.job) {
        showSteps([{ name: "deploy", ok: false, ms: 0, error: "请先到任务页选择 Job" }], action);
        return log("请先到任务页选择 Job");
      }
      body.gpus = Number(
        document.getElementById("deploy-gpus")?.value ||
        state.overview?.jobs?.find((j) => j.name === body.job)?.gpus_per_node ||
        4
      );
      const ok = await confirmDialog({
        title: "一键部署",
        message: `确认部署 ${body.job}（${body.gpus} GPU）？由服务端按空闲 GPU 自动选机。`,
        confirmText: "部署",
      });
      if (!ok) return;
    }
    log(`开始：${action}`);
    const data = await api("/api/actions/oneclick", {
      method: "POST",
      body: JSON.stringify(body),
    });
    renderActionSteps(data.steps || [], data.elapsed_ms);
    if (data.quality) renderQuality(data.quality);
    if (data.overview) applyOverview(data.overview);
    refreshRunlog().catch(() => { });
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

async function refresh(doRemoteRefresh, { light = false } = {}) {
  if (state.refreshInFlight && !doRemoteRefresh) return state.overview;
  state.refreshInFlight = true;
  try {
    const headers = {};
    if (!doRemoteRefresh && state.etag) headers["If-None-Match"] = state.etag;
    let path = "/api/overview";
    if (doRemoteRefresh) path += "?refresh=1";
    else if (light) path += "?light=1";
    const timeoutMs = doRemoteRefresh ? 200000 : light ? 20000 : 60000;
    const data = await api(path, { headers, timeoutMs });
    if (data.__notModified) {
      if (data.etag) state.etag = data.etag;
      state.lastRefreshAt = Date.now();
      return state.overview;
    }
    applyOverview(data);
    state.lastRefreshAt = Date.now();
    state.pollFailures = 0;
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
    toastOk("命令执行完成");
  } catch (err) {
    actionError("远程命令", err);
  } finally {
    setBusy(false);
  }
}

const EXEC_HISTORY_KEY = "gc_exec_history_v1";
let execSuggestItems = [];
let execSuggestIndex = -1;
let execSuggestTimer = 0;

function loadExecHistory() {
  try {
    const raw = localStorage.getItem(EXEC_HISTORY_KEY);
    const list = raw ? JSON.parse(raw) : [];
    return Array.isArray(list) ? list.filter((x) => typeof x === "string") : [];
  } catch {
    return [];
  }
}

function rememberExecCommand(command) {
  const cmd = (command || "").trim();
  if (!cmd) return;
  const next = [cmd, ...loadExecHistory().filter((x) => x !== cmd)].slice(0, 20);
  try {
    localStorage.setItem(EXEC_HISTORY_KEY, JSON.stringify(next));
  } catch {
    /* ignore quota */
  }
}

function hideExecSuggest() {
  const list = $("exec-suggest-list");
  if (!list) return;
  list.hidden = true;
  list.innerHTML = "";
  execSuggestItems = [];
  execSuggestIndex = -1;
}

function renderExecSuggest(items) {
  const list = $("exec-suggest-list");
  if (!list) return;
  execSuggestItems = items || [];
  execSuggestIndex = execSuggestItems.length ? 0 : -1;
  if (!execSuggestItems.length) {
    hideExecSuggest();
    return;
  }
  list.innerHTML = execSuggestItems
    .map(
      (row, i) =>
        `<li role="option" data-idx="${i}" class="${i === 0 ? "is-active" : ""}">` +
        `<strong>${escapeHtml(row.label || row.command)}</strong>` +
        `<code>${escapeHtml(row.command)}</code></li>`
    )
    .join("");
  list.hidden = false;
  list.querySelectorAll("li").forEach((li) => {
    li.addEventListener("mousedown", (ev) => {
      ev.preventDefault();
      applyExecSuggest(Number(li.dataset.idx));
    });
  });
}

function applyExecSuggest(idx) {
  const row = execSuggestItems[idx];
  const input = $("exec-custom");
  if (!row || !input) return;
  input.value = row.command;
  hideExecSuggest();
  input.focus();
}

async function refreshExecSuggest(query) {
  const q = (query || "").trim();
  const history = loadExecHistory()
    .filter((cmd) => !q || cmd.toLowerCase().includes(q.toLowerCase()))
    .slice(0, 6)
    .map((command) => ({ command, label: "最近使用", source: "history" }));
  let remote = [];
  try {
    const qs = new URLSearchParams({ q, limit: "16" });
    const data = await api(`/api/exec/suggest?${qs}`);
    remote = data.items || [];
  } catch {
    remote = [];
  }
  const seen = new Set();
  const merged = [];
  for (const row of [...history, ...remote]) {
    if (!row?.command || seen.has(row.command)) continue;
    seen.add(row.command);
    merged.push(row);
    if (merged.length >= 12) break;
  }
  renderExecSuggest(merged);
}

function bindExecSuggest() {
  const input = $("exec-custom");
  if (!input) return;
  input.addEventListener("input", () => {
    window.clearTimeout(execSuggestTimer);
    execSuggestTimer = window.setTimeout(() => refreshExecSuggest(input.value), 120);
  });
  input.addEventListener("focus", () => refreshExecSuggest(input.value));
  input.addEventListener("blur", () => {
    window.setTimeout(hideExecSuggest, 120);
  });
  input.addEventListener("keydown", (ev) => {
    const list = $("exec-suggest-list");
    const open = list && !list.hidden && execSuggestItems.length;
    if (ev.key === "ArrowDown" && open) {
      ev.preventDefault();
      execSuggestIndex = (execSuggestIndex + 1) % execSuggestItems.length;
      list.querySelectorAll("li").forEach((li, i) => li.classList.toggle("is-active", i === execSuggestIndex));
      return;
    }
    if (ev.key === "ArrowUp" && open) {
      ev.preventDefault();
      execSuggestIndex = (execSuggestIndex - 1 + execSuggestItems.length) % execSuggestItems.length;
      list.querySelectorAll("li").forEach((li, i) => li.classList.toggle("is-active", i === execSuggestIndex));
      return;
    }
    if (ev.key === "Tab" && open && execSuggestIndex >= 0) {
      ev.preventDefault();
      applyExecSuggest(execSuggestIndex);
      return;
    }
    if (ev.key === "Escape") {
      hideExecSuggest();
      return;
    }
    if (ev.key === "Enter") {
      ev.preventDefault();
      if (open && execSuggestIndex >= 0 && ev.shiftKey) {
        applyExecSuggest(execSuggestIndex);
        return;
      }
      $("btn-exec-custom")?.click();
    }
  });
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
  if (!body.script && !body.code) {
    toastBad("请填写脚本路径或 -c 代码");
    return;
  }
  setBusy(true);
  try {
    const data = await api("/api/run-python", { method: "POST", body: JSON.stringify(body) });
    showResults(data.results, data.command);
    toastOk("Python 已执行");
  } catch (err) {
    actionError("运行 Python", err);
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
  const extraArgs = parseExtraArgs($("job-extra-args")?.value);
  const body = { action, ...target };
  if (extraArgs) body.extra_args = extraArgs;
  const scopeLabel = scope && scope !== "all" ? scope : "全部节点";
  if (action === "stop" || action === "restart" || (action === "start" && scopeLabel === "全部节点")) {
    const ok = await confirmDialog({
      title: action === "stop" ? "停止任务" : action === "restart" ? "重启任务" : "启动任务",
      message: `${action === "stop" ? "停止" : action === "restart" ? "重启" : "启动"} ${job}（${scopeLabel}）？`,
      confirmText: action === "stop" ? "停止" : action === "restart" ? "重启" : "启动",
      tone: action === "stop" ? "danger" : "primary",
    });
    if (!ok) return;
  }
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
    toastOk(`Job ${action} 完成`);
    await refresh(false);
  } catch (err) {
    writeConsoleText(String(err.message || err), { meta: `Job ${action} 失败` });
    actionError(`Job ${action}`, err);
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

const VIEW_ORDER = [
  "actions",
  "cluster",
  "workloads",
  "models",
  "toolkits",
  "nodes",
  "endpoints",
  "ops",
  "terminal",
  "guide",
  "config",
];

function setConfigTab(name, { updateHash = false } = {}) {
  document.querySelectorAll("#config-tabs .tab").forEach((t) => {
    t.classList.toggle("active", t.dataset.tab === name);
  });
  document.querySelectorAll("[data-tab-panel]").forEach((p) => {
    p.classList.toggle("active", p.dataset.tabPanel === name);
  });
  if (updateHash && state.view === "config") {
    history.replaceState(null, "", `#/config/${name}`);
  }
}

function currentRoute() {
  const raw = (location.hash || "").replace(/^#\/?/, "");
  const [view, tab] = raw.split("/");
  return { view: VIEW_TITLE[view] ? view : "actions", tab: tab || "" };
}

function setView(name, { updateHash = true } = {}) {
  if (!VIEW_TITLE[name]) name = "actions";
  state.view = name;
  document.querySelectorAll(".nav-item").forEach((n) => {
    const active = n.dataset.view === name;
    n.classList.toggle("active", active);
    n.setAttribute("aria-current", active ? "page" : "false");
  });
  document.querySelectorAll(".view").forEach((v) => {
    v.classList.toggle("active", v.dataset.viewPanel === name);
  });
  const title = $("view-title");
  if (title) title.textContent = VIEW_TITLE[name] || name;
  const hint = $("cli-hint-code");
  if (hint) hint.textContent = VIEW_CLI[name] || "gpu-control --help";
  if (updateHash) {
    const hash = `#/${name}`;
    if (location.hash !== hash) history.pushState(null, "", hash);
  }
  renderView(name);
  if ((name === "guide" || name === "config") && !stateSetup.data) {
    loadSetup().catch((err) => actionError("加载配置", err));
  }
  if (name === "models") {
    refreshModels().catch((err) => log(String(err.message || err)));
  }
  if (name === "toolkits") {
    refreshToolkits().catch((err) => log(String(err.message || err)));
  }
  if (name === "actions") {
    if (!state.quality) loadQuality().catch(() => { });
    refreshRunlog().catch(() => { });
    refreshIngressStatus().catch(() => { });
  }
  if (name === "terminal") {
    ensureTerminal();
    if (termState.fit) {
      setTimeout(() => {
        termState.fit.fit();
        sendTermResize();
      }, 60);
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
      const note = meta.note ? ` # ${String(meta.note).replace(/\s+/g, " ")}` : "";
      return [name, meta.ansible_host || "", gpus, labels].join(" ").trim() + note;
    })
    .join("\n");
}

/* Merge edited rows back into the existing document.
   Unknown per-node fields (note/paths/env/role/...) and top-level keys
   (control, defaults) must survive a save. */
function editorToNodes(text, baseDoc) {
  const base = baseDoc || { defaults: {}, nodes: {} };
  const baseNodes = base.nodes || {};
  const doc = { ...base };
  doc.defaults = { ...(base.defaults || {}) };
  doc.nodes = {};
  for (const raw of text.split("\n")) {
    let line = raw.trim();
    if (!line || line.startsWith("#")) continue;
    let note = "";
    const hashAt = line.indexOf("#");
    if (hashAt >= 0) {
      note = line.slice(hashAt + 1).trim();
      line = line.slice(0, hashAt).trim();
    }
    if (!line) continue;
    const parts = line.split(/\s+/);
    const name = parts[0];
    const host = parts[1];
    if (!name || !host) continue;
    const gpus = parts[2] && /^\d+$/.test(parts[2]) ? Number(parts[2]) : undefined;
    const labels = parts.slice(gpus == null ? 2 : 3).join(" ");
    const prev = baseNodes[name] || {};
    const node = { ...prev, ansible_host: host };
    if (!node.role) node.role = doc.defaults.role || "serving";
    if (gpus != null) node.gpus_per_node = gpus;
    if (labels) node.labels = labels.split(",").map((x) => x.trim()).filter(Boolean);
    if (note) node.note = note;
    doc.nodes[name] = node;
  }
  return doc;
}

function fillJobForm(job, fallbackName = "") {
  const name = (job && job.name) || fallbackName || "";
  $("job-name").value = name;
  $("setup-job-path").textContent =
    (job && job.path) || (name ? `config/local/jobs/${name}.json` : "config/local/jobs/<name>.json");
  $("job-desc").value = (job && job.description) || "";
  $("job-gpus").value = job && job.gpus_per_node != null ? job.gpus_per_node : 4;
  $("job-python").value = (job && job.python) || "";
  $("job-script").value = (job && job.script) || "";
  $("job-extra").value = ((job && job.extra_args) || []).join(" ");
  const kind = $("job-kind");
  if (kind) kind.value = (job && job.kind) || "service";
  const runtime = $("job-runtime");
  if (runtime) runtime.value = (job && job.runtime) || "process";
  const image = $("job-docker-image");
  if (image) image.value = (job && job.docker && job.docker.image) || "";
  const envBox = $("job-env");
  if (envBox) {
    const env = (job && job.env) || {};
    envBox.value = Object.entries(env)
      .map(([k, v]) => `${k}=${v}`)
      .join("\n");
  }
  syncJobRuntimeFields();
}

function syncJobRuntimeFields() {
  const runtime = ($("job-runtime")?.value || "process").trim();
  const proc = $("job-process-fields");
  const dock = $("job-docker-fields");
  if (proc) proc.hidden = runtime !== "process";
  if (dock) dock.hidden = runtime !== "docker";
}

function parseEnvText(text) {
  const out = {};
  for (const raw of String(text || "").split("\n")) {
    const line = raw.trim();
    if (!line || line.startsWith("#")) continue;
    const eq = line.indexOf("=");
    if (eq <= 0) continue;
    out[line.slice(0, eq).trim()] = line.slice(eq + 1).trim();
  }
  return out;
}

function ensureNodesDraft(doc) {
  const base = doc || (stateSetup.data && stateSetup.data.nodes) || { defaults: {}, nodes: {} };
  stateSetup.nodesDraft = {
    ...base,
    defaults: { ...(base.defaults || {}) },
    nodes: { ...(base.nodes || {}) },
  };
  return stateSetup.nodesDraft;
}

function nodesDraftCount() {
  return Object.keys((stateSetup.nodesDraft && stateSetup.nodesDraft.nodes) || {}).length;
}

function resolvedNodesDensity() {
  const mode = stateSetup.nodesDensity || "auto";
  if (mode === "cards" || mode === "compact") return mode;
  return nodesDraftCount() >= 6 ? "compact" : "cards";
}

function nodeMatchesFilter(name, meta, q) {
  if (!q) return true;
  const hay = [
    name,
    meta.ansible_host || "",
    (meta.labels || []).join(","),
    meta.note || "",
  ]
    .join(" ")
    .toLowerCase();
  return hay.includes(q);
}

function readCardFields(card) {
  const name = (card.querySelector('[data-nc="name"]')?.value || "").trim();
  const host = (card.querySelector('[data-nc="host"]')?.value || "").trim();
  const gpusRaw = (card.querySelector('[data-nc="gpus"]')?.value || "").trim();
  const labels = (card.querySelector('[data-nc="labels"]')?.value || "")
    .split(",")
    .map((x) => x.trim())
    .filter(Boolean);
  const note = (card.querySelector('[data-nc="note"]')?.value || "").trim();
  return { name, host, gpusRaw, labels, note };
}

function commitCardToDraft(card) {
  const draft = ensureNodesDraft(stateSetup.nodesDraft);
  const oldKey = card.dataset.nodeCard;
  const { name, host, gpusRaw, labels, note } = readCardFields(card);
  if (!oldKey) return;
  if (!name) return;
  const prev = draft.nodes[oldKey] || draft.nodes[name] || {};
  const node = { ...prev };
  if (host) node.ansible_host = host;
  if (!node.role) node.role = draft.defaults.role || "serving";
  if (gpusRaw && /^\d+$/.test(gpusRaw)) node.gpus_per_node = Number(gpusRaw);
  else if (!gpusRaw && "gpus_per_node" in node) delete node.gpus_per_node;
  if (labels.length) node.labels = labels;
  else if ("labels" in node) delete node.labels;
  if (note) node.note = note;
  else if ("note" in node) delete node.note;
  if (name !== oldKey) {
    if (draft.nodes[name] && name !== oldKey) {
      toastBad(`节点名 ${name} 已存在`);
      card.querySelector('[data-nc="name"]').value = oldKey;
      return;
    }
    delete draft.nodes[oldKey];
    card.dataset.nodeCard = name;
  }
  draft.nodes[name] = node;
  syncNodesEditorFromDraft();
  updateNodesChrome();
}

function syncNodesEditorFromDraft() {
  const editor = $("nodes-editor");
  if (!editor || !stateSetup.nodesDraft) return;
  editor.value = nodesDocToEditor(stateSetup.nodesDraft);
}

function updateNodesChrome() {
  const total = nodesDraftCount();
  const countEl = $("nodes-count");
  if (countEl) countEl.textContent = `${total} 台`;
  const q = (stateSetup.nodesFilter || "").trim().toLowerCase();
  const meta = $("nodes-filter-meta");
  if (meta) {
    if (!q) {
      meta.hidden = true;
      meta.textContent = "";
    } else {
      const shown = Object.entries((stateSetup.nodesDraft && stateSetup.nodesDraft.nodes) || {}).filter(([n, m]) =>
        nodeMatchesFilter(n, m || {}, q)
      ).length;
      meta.hidden = false;
      meta.textContent = `筛选中：显示 ${shown} / 共 ${total}（未显示的节点保存时仍会保留）`;
    }
  }
  const root = $("nodes-cards");
  if (root) root.dataset.density = resolvedNodesDensity();
}

function renderNodesCards(doc) {
  if (doc) ensureNodesDraft(doc);
  else if (!stateSetup.nodesDraft) ensureNodesDraft();
  const draft = stateSetup.nodesDraft;
  const root = $("nodes-cards");
  if (!root || !draft) return;
  const density = resolvedNodesDensity();
  root.dataset.density = density;
  const q = (stateSetup.nodesFilter || "").trim().toLowerCase();
  const entries = Object.entries(draft.nodes || {}).filter(([name, meta]) =>
    nodeMatchesFilter(name, meta || {}, q)
  );
  if (!Object.keys(draft.nodes || {}).length) {
    root.innerHTML =
      '<div class="nodes-empty"><strong>还没有 GPU 节点</strong><p>点「加节点」，或展开下方文本行批量粘贴。</p></div>';
    updateNodesChrome();
    return;
  }
  if (!entries.length) {
    root.innerHTML = '<div class="nodes-empty"><strong>无匹配节点</strong><p>清空筛选条件看看全部列表。</p></div>';
    updateNodesChrome();
    return;
  }
  const compact = density === "compact";
  root.innerHTML = entries
    .map(([name, meta]) => {
      const labels = (meta.labels || []).join(",");
      if (compact) {
        return `<div class="node-card is-compact" data-node-card="${escapeHtml(name)}">
          <div class="field-row">
            <label class="field"><span>名称</span><input data-nc="name" value="${escapeHtml(name)}" /></label>
            <label class="field"><span>地址</span><input data-nc="host" value="${escapeHtml(meta.ansible_host || "")}" /></label>
            <label class="field"><span>GPU</span><input data-nc="gpus" type="number" value="${escapeHtml(meta.gpus_per_node ?? "")}" /></label>
            <label class="field"><span>标签</span><input data-nc="labels" value="${escapeHtml(labels)}" /></label>
            <div class="node-card-actions">
              <button type="button" class="ghost tiny" data-toggle-node-extra data-keep-enabled="1">备注</button>
              <button type="button" class="ghost tiny" data-remove-node-card="${escapeHtml(name)}" data-keep-enabled="1">删</button>
            </div>
          </div>
          <label class="field node-extra" hidden><span>备注</span><input data-nc="note" value="${escapeHtml(meta.note || "")}" /></label>
        </div>`;
      }
      return `<div class="node-card" data-node-card="${escapeHtml(name)}">
        <div class="field-row">
          <label class="field"><span>名称</span><input data-nc="name" value="${escapeHtml(name)}" /></label>
          <label class="field"><span>地址</span><input data-nc="host" value="${escapeHtml(meta.ansible_host || "")}" /></label>
          <label class="field"><span>GPU</span><input data-nc="gpus" type="number" value="${escapeHtml(meta.gpus_per_node ?? "")}" /></label>
        </div>
        <label class="field"><span>标签（逗号）</span><input data-nc="labels" value="${escapeHtml(labels)}" /></label>
        <label class="field"><span>备注</span><input data-nc="note" value="${escapeHtml(meta.note || "")}" /></label>
        <div class="node-card-actions">
          <button type="button" class="ghost tiny" data-remove-node-card="${escapeHtml(name)}" data-keep-enabled="1">删除</button>
        </div>
      </div>`;
    })
    .join("");
  root.querySelectorAll("[data-node-card]").forEach((card) => {
    card.querySelectorAll("input").forEach((input) => {
      input.addEventListener("change", () => commitCardToDraft(card));
      input.addEventListener("blur", () => commitCardToDraft(card));
    });
  });
  root.querySelectorAll("[data-remove-node-card]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const key = btn.dataset.removeNodeCard;
      const draftNow = ensureNodesDraft(stateSetup.nodesDraft);
      delete draftNow.nodes[key];
      renderNodesCards();
      syncNodesEditorFromDraft();
    });
  });
  root.querySelectorAll("[data-toggle-node-extra]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const card = btn.closest("[data-node-card]");
      const extra = card?.querySelector(".node-extra");
      if (!extra) return;
      extra.hidden = !extra.hidden;
    });
  });
  updateNodesChrome();
  syncNodesEditorFromDraft();
}

function cardsToNodesDoc(baseDoc) {
  // 兼容旧调用：以草稿为准（含被筛选隐藏的节点）
  const draft = ensureNodesDraft(stateSetup.nodesDraft || baseDoc);
  document.querySelectorAll("#nodes-cards [data-node-card]").forEach((card) => commitCardToDraft(card));
  return {
    ...draft,
    defaults: { ...(draft.defaults || {}) },
    nodes: { ...(draft.nodes || {}) },
  };
}

function syncNodesCardsFromEditor() {
  const base = (stateSetup.data && stateSetup.data.nodes) || { defaults: {}, nodes: {} };
  const doc = editorToNodes($("nodes-editor")?.value || "", base);
  ensureNodesDraft(doc);
  renderNodesCards();
}

function addNodeCard() {
  const draft = ensureNodesDraft(stateSetup.nodesDraft);
  let i = 1;
  let name = `gpu-${i}`;
  while (draft.nodes[name]) {
    i += 1;
    name = `gpu-${i}`;
  }
  draft.nodes[name] = {
    ansible_host: "",
    gpus_per_node: Number((draft.defaults && draft.defaults.gpus_per_node) || 4),
    labels: draft.defaults && draft.defaults.role ? [String(draft.defaults.role)] : ["serving"],
    role: (draft.defaults && draft.defaults.role) || "serving",
  };
  stateSetup.nodesFilter = "";
  if ($("nodes-filter")) $("nodes-filter").value = "";
  renderNodesCards();
  const card = [...document.querySelectorAll("#nodes-cards [data-node-card]")].find(
    (el) => el.dataset.nodeCard === name
  );
  card?.querySelector('[data-nc="host"]')?.focus();
}

/* Load the full Job JSON (not the summary) so saving can merge instead of
   replacing kind/model/env/docker. */
async function loadJobIntoForm(name) {
  const pick = $("setup-job-pick");
  if (!name) {
    stateSetup.jobBase = null;
    stateSetup.jobBaseName = null;
    fillJobForm(null);
    return;
  }
  const seq = ++stateSetup.jobLoadSeq;
  try {
    const resp = await api(`/api/config/job/${encodeURIComponent(name)}`);
    if (seq !== stateSetup.jobLoadSeq) return;
    stateSetup.jobBase = resp.data || {};
    stateSetup.jobBaseName = name;
    fillJobForm({ ...(resp.data || {}), name, path: resp.path });
    if (pick) pick.value = name;
  } catch (_) {
    if (seq !== stateSetup.jobLoadSeq) return;
    const summary = ((stateSetup.data && stateSetup.data.jobs) || []).find((j) => j.name === name);
    stateSetup.jobBase = null;
    stateSetup.jobBaseName = null;
    fillJobForm(summary, name);
  }
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
  const ingress = ((cluster.gateway || {}).ingress || {});
  const tls = ingress.tls || {};
  const domainEl = document.getElementById("cluster-domain");
  if (domainEl) domainEl.value = ingress.domain || (ingress.domains || [])[0] || "";
  const pubEl = document.getElementById("cluster-public-url");
  if (pubEl) pubEl.value = ingress.public_base_url || "";
  const tlsEn = document.getElementById("cluster-tls-enabled");
  if (tlsEn) tlsEn.checked = !!tls.enabled;
  const tlsCert = document.getElementById("cluster-tls-cert");
  if (tlsCert) tlsCert.value = tls.cert_file || "";
  const tlsKey = document.getElementById("cluster-tls-key");
  if (tlsKey) tlsKey.value = tls.key_file || "";
  const tlsCa = document.getElementById("cluster-tls-ca");
  if (tlsCa) tlsCa.value = tls.ca_file || "";
  document.getElementById("nodes-editor").value = nodesDocToEditor(data.nodes || {});
  ensureNodesDraft(data.nodes || { defaults: {}, nodes: {} });
  if ($("nodes-filter")) $("nodes-filter").value = stateSetup.nodesFilter || "";
  if ($("nodes-density")) $("nodes-density").value = stateSetup.nodesDensity || "auto";
  renderNodesCards();

  const wd = cluster.watchdog || {};
  const wdEn = $("wd-enabled");
  if (wdEn) wdEn.checked = wd.enabled !== false;
  if ($("wd-jobs")) $("wd-jobs").value = (wd.jobs || []).join(",");
  if ($("wd-interval")) $("wd-interval").value = wd.interval_sec ?? 60;
  if ($("wd-max-restarts")) $("wd-max-restarts").value = wd.max_restarts ?? 3;
  if ($("wd-failure-threshold")) $("wd-failure-threshold").value = wd.failure_threshold ?? 2;
  if ($("wd-nodes")) $("wd-nodes").value = wd.nodes || "all";

  const auth = (cluster.control && cluster.control.auth) || {};
  const oidc = auth.oidc || {};
  if ($("auth-mode")) $("auth-mode").value = auth.mode || "off";
  if ($("auth-ttl")) $("auth-ttl").value = auth.session_ttl_hours ?? 168;
  if ($("auth-password-hash")) $("auth-password-hash").value = auth.password_hash_file || "";
  if ($("auth-session-secret")) $("auth-session-secret").value = auth.session_secret_file || "";
  if ($("auth-api-token")) $("auth-api-token").value = auth.api_token_file || "";
  if ($("auth-oidc-issuer")) $("auth-oidc-issuer").value = oidc.issuer || "";
  if ($("auth-oidc-client-id")) $("auth-oidc-client-id").value = oidc.client_id || "";
  if ($("auth-oidc-secret")) $("auth-oidc-secret").value = oidc.client_secret_file || "";
  if ($("auth-oidc-password-fallback")) {
    $("auth-oidc-password-fallback").checked = !!oidc.allow_password_fallback;
  }

  const pick = document.getElementById("setup-job-pick");
  if (pick) {
    const prevPick = pick.value;
    const opts = (data.jobs || [])
      .map((j) => `<option value="${escapeHtml(j.name)}">${escapeHtml(jobOptionLabel(j))}</option>`)
      .join("");
    pick.innerHTML = `<option value="">— 选择已有 Job —</option>${opts}`;
    const names = [...pick.options].map((o) => o.value);
    if (prevPick && names.includes(prevPick)) pick.value = prevPick;
    else if (defaultJob) pick.value = defaultJob;
  }
  const job =
    (data.jobs || []).find((j) => j.name === (pick?.value || defaultJob)) ||
    (data.jobs || [])[0];
  if (job) fillJobForm(job);
  else fillJobForm(null, defaultJob);

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
  const preferred =
    (data.cluster && data.cluster.default_job) ||
    ((data.jobs || [])[0] && (data.jobs || [])[0].name) ||
    "";
  if (preferred) {
    const pick = $("setup-job-pick");
    if (pick) pick.value = preferred;
    await loadJobIntoForm(preferred);
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

function applyWatchdogFields(cluster) {
  const jobs = ($("wd-jobs")?.value || "")
    .split(",")
    .map((x) => x.trim())
    .filter(Boolean);
  cluster.watchdog = {
    ...(cluster.watchdog || {}),
    enabled: !!$("wd-enabled")?.checked,
    jobs,
    interval_sec: Number($("wd-interval")?.value || 60),
    max_restarts: Number($("wd-max-restarts")?.value || 3),
    failure_threshold: Number($("wd-failure-threshold")?.value || 2),
    nodes: ($("wd-nodes")?.value || "all").trim() || "all",
  };
  return cluster;
}

function applyAuthFields(cluster) {
  const control = { ...(cluster.control || {}) };
  const prev = control.auth || {};
  const prevOidc = prev.oidc || {};
  control.auth = {
    ...prev,
    mode: ($("auth-mode")?.value || "off").trim() || "off",
    session_ttl_hours: Number($("auth-ttl")?.value || 168),
    password_hash_file: ($("auth-password-hash")?.value || "").trim(),
    session_secret_file: ($("auth-session-secret")?.value || "").trim(),
    api_token_file: ($("auth-api-token")?.value || "").trim(),
    oidc: {
      ...prevOidc,
      issuer: ($("auth-oidc-issuer")?.value || "").trim(),
      client_id: ($("auth-oidc-client-id")?.value || "").trim(),
      client_secret_file: ($("auth-oidc-secret")?.value || "").trim(),
      allow_password_fallback: !!$("auth-oidc-password-fallback")?.checked,
    },
  };
  cluster.control = control;
  return cluster;
}

async function postClusterConfig(data, okMsg) {
  setBusy(true);
  try {
    const resp = await api("/api/config/cluster", {
      method: "POST",
      body: JSON.stringify({ data }),
    });
    fillSetupForm(resp.setup);
    toastOk(okMsg || "集群配置已保存");
  } catch (err) {
    actionError("保存集群配置", err);
  } finally {
    setBusy(false);
  }
}

async function saveCluster() {
  const base = (stateSetup.data && stateSetup.data.cluster) || {};
  const baseGw = base.gateway || {};
  const baseIngress = baseGw.ingress || {};
  const baseTls = baseIngress.tls || {};
  const domain = (document.getElementById("cluster-domain")?.value || "").trim();
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
    gateway: {
      ...baseGw,
      ingress: {
        ...baseIngress,
        domain,
        domains: domain ? [domain] : baseIngress.domains || [],
        public_base_url: (document.getElementById("cluster-public-url")?.value || "").trim(),
        tls: {
          ...baseTls,
          enabled: !!document.getElementById("cluster-tls-enabled")?.checked,
          cert_file: (document.getElementById("cluster-tls-cert")?.value || "").trim(),
          key_file: (document.getElementById("cluster-tls-key")?.value || "").trim(),
          ca_file: (document.getElementById("cluster-tls-ca")?.value || "").trim(),
        },
      },
    },
  };
  applyWatchdogFields(data);
  applyAuthFields(data);
  await postClusterConfig(data, "集群配置已保存");
}

async function saveWatchdog() {
  const data = { ...((stateSetup.data && stateSetup.data.cluster) || {}) };
  applyWatchdogFields(data);
  await postClusterConfig(data, "Watchdog 已保存");
}

async function saveAuthConfig() {
  const data = { ...((stateSetup.data && stateSetup.data.cluster) || {}) };
  applyAuthFields(data);
  await postClusterConfig(data, "鉴权配置已保存");
}

async function saveNodesEditor() {
  const base = (stateSetup.data && stateSetup.data.nodes) || { defaults: {}, nodes: {} };
  // 先提交可见卡片到草稿；草稿含被筛选隐藏的节点
  let data = cardsToNodesDoc(base);
  const incomplete = Object.entries(data.nodes || {}).filter(([, meta]) => !(meta && meta.ansible_host));
  if (incomplete.length) {
    toastBad(`还有 ${incomplete.length} 台未填地址：${incomplete.map(([n]) => n).slice(0, 5).join(", ")}`);
    return;
  }
  if (!Object.keys(data.nodes).length) {
    data = editorToNodes($("nodes-editor").value, base);
  } else {
    $("nodes-editor").value = nodesDocToEditor(data);
  }
  if (!Object.keys(data.nodes).length) {
    toastBad("至少保留一个节点");
    return;
  }
  const removed = Object.keys(base.nodes || {}).filter((name) => !data.nodes[name]);
  if (removed.length) {
    const ok = await confirmDialog({
      title: "删除节点",
      message: `以下节点将从 nodes.json 删除：\n${removed.join(", ")}\n\n确认保存？`,
      confirmText: "删除并保存",
      tone: "danger",
    });
    if (!ok) return;
  }
  // keep ssh defaults from form if present
  const key = $("ssh-key").value.trim();
  const user = $("ssh-user").value.trim();
  const port = $("ssh-port").value.trim();
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
    toastOk(`已保存 ${Object.keys(data.nodes).length} 个节点`);
    await refresh(false);
  } catch (err) {
    actionError("保存节点", err);
  } finally {
    setBusy(false);
  }
}

async function saveJob() {
  const name = ($("job-name").value || "").trim();
  if (!name) {
    toastBad("请填写 Job 名");
    return;
  }
  if (!/^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/.test(name)) {
    toastBad("Job 名只允许字母数字开头，可含 . _ -（≤64 字符）");
    return;
  }
  const extra = $("job-extra")
    .value.trim()
    .split(/\s+/)
    .filter(Boolean);

  // Merge over the full existing JSON so kind/model/env/docker/distributed
  // and future fields are preserved when the wizard only edits a few inputs.
  let base = {};
  if (stateSetup.jobBase && stateSetup.jobBase.name === name) {
    base = stateSetup.jobBase;
  } else {
    try {
      const resp = await api(`/api/config/job/${encodeURIComponent(name)}`);
      base = resp.data || {};
    } catch (_) {
      base = {};
    }
  }
  const runtime = ($("job-runtime")?.value || base.runtime || "process").trim();
  const data = {
    ...base,
    name,
    description: $("job-desc").value.trim(),
    kind: ($("job-kind")?.value || base.kind || "service").trim(),
    gpus_per_node: Number($("job-gpus").value || 4),
    runtime,
    python: $("job-python").value.trim(),
    script: $("job-script").value.trim(),
    extra_args: extra,
    env: parseEnvText($("job-env")?.value || ""),
  };
  if (runtime === "docker") {
    data.docker = { ...(base.docker || {}), image: ($("job-docker-image")?.value || "").trim() };
  }
  if (!Array.isArray(data.actions) || !data.actions.length) {
    data.actions = ["start", "stop", "restart", "status", "wait"];
  }
  setBusy(true);
  try {
    const resp = await api(`/api/config/job/${encodeURIComponent(name)}`, {
      method: "POST",
      body: JSON.stringify({ data }),
    });
    fillSetupForm(resp.setup);
    const pick = $("setup-job-pick");
    if (pick) pick.value = name;
    fillJobForm({ ...data, path: resp.path });
    stateSetup.jobBase = data;
    stateSetup.jobBaseName = name;
    toastOk("Job 已保存");
    await refresh(false);
  } catch (err) {
    actionError("保存 Job", err);
  } finally {
    setBusy(false);
  }
}

function setupAutoRefresh() {
  clearInterval(state.autoTimer);
  const toggle = $("auto-refresh");
  if (!toggle || !toggle.checked) return;
  state.autoTimer = setInterval(async () => {
    if (state.busy || document.hidden) return;
    // Exponential-ish backoff: after a failure, poll every other tick.
    if (state.pollFailures > 0) {
      state.pollSkip = (state.pollSkip + 1) % 2;
      if (state.pollSkip) return;
    }
    try {
      // Light poll: skip LB rebuild / full probe matrix to keep UI snappy.
      await refresh(false, { light: true });
    } catch (_) {
      state.pollFailures = Math.min(5, state.pollFailures + 1);
    }
  }, 12000);
}

document.addEventListener("visibilitychange", () => {
  if (document.hidden) return;
  if (Date.now() - state.lastRefreshAt > 12000) {
    refresh(false, { light: true }).catch(() => { });
  }
});

async function boot() {
  initTheme();
  document.querySelectorAll(".nav-item").forEach((btn) => {
    btn.addEventListener("click", () => setView(btn.dataset.view));
  });
  const nodeDrawerClose = document.getElementById("node-drawer-close");
  const nodeDrawer = document.getElementById("node-drawer");
  if (nodeDrawerClose) nodeDrawerClose.addEventListener("click", () => closeNodeDrawer());
  if (nodeDrawer) {
    nodeDrawer.addEventListener("click", (ev) => {
      if (ev.target === nodeDrawer) closeNodeDrawer();
    });
    nodeDrawer.addEventListener("cancel", (ev) => {
      ev.preventDefault();
      closeNodeDrawer();
    });
  }
  document.querySelectorAll("#config-tabs .tab").forEach((btn) => {
    btn.addEventListener("click", () => setConfigTab(btn.dataset.tab, { updateHash: true }));
  });
  window.addEventListener("hashchange", () => {
    const route = currentRoute();
    setView(route.view, { updateHash: false });
    if (route.view === "config" && route.tab) setConfigTab(route.tab);
  });
  // Block double-submits on action buttons without locking navigation.
  document.addEventListener(
    "click",
    (ev) => {
      const btn =
        ev.target && ev.target.closest
          ? ev.target.closest("[data-action], [data-job-action], [data-oneclick]")
          : null;
      if (btn && state.busy) {
        ev.preventDefault();
        ev.stopPropagation();
        toast("正在执行，请稍候", { tone: "warn" });
      }
    },
    true
  );
  const authForm = document.getElementById("auth-form");
  if (authForm) authForm.addEventListener("submit", submitLogin);
  const logoutBtn = document.getElementById("btn-logout");
  if (logoutBtn) logoutBtn.addEventListener("click", logout);
  try {
    await refreshAuthStatus();
  } catch (err) {
    log(String(err.message || err));
  }
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
  document.addEventListener("click", (ev) => {
    const jump = ev.target && ev.target.closest ? ev.target.closest("[data-view-jump]") : null;
    if (jump) setView(jump.dataset.viewJump);
  });
  document.getElementById("btn-full-sync").addEventListener("click", () => runOneclick("full-sync"));
  document.getElementById("btn-runlog-refresh")?.addEventListener("click", () => {
    refreshRunlog().then(() => toast("日志已刷新")).catch((err) => log(String(err.message || err)));
  });
  on("btn-load-quality", "click", async () => {
    try {
      await loadQuality();
      toastOk("评分已刷新");
    } catch (err) {
      actionError("刷新评分", err);
    }
  });
  on("btn-quick-refresh", "click", () => {
    const btn = $("btn-refresh");
    if (btn) btn.click();
  });
  on("btn-quick-health", "click", runApiHealth);
  on("btn-console-scroll", "click", revealConsole);
  on("console-unread", "click", revealConsole);
  on("btn-console-toggle", "click", () => {
    const wrap = $("batch-console-wrap");
    if (!wrap) return;
    const collapsed = wrap.classList.toggle("collapsed");
    $("btn-console-toggle").textContent = collapsed ? "展开" : "收起";
  });
  document.querySelectorAll("[data-oneclick]").forEach((btn) => {
    btn.addEventListener("click", () => runOneclick(btn.dataset.oneclick));
  });
  document.getElementById("btn-toolkit-probe")?.addEventListener("click", () => runToolkit("probe"));
  on("btn-toolkit-train", "click", async () => {
    const ok = await confirmDialog({
      title: "安装微调栈",
      message: "在全部 GPU 节点安装 LLaMA-Factory / DeepSpeed / Accelerate / PEFT？pip 安装可能持续数分钟。",
      confirmText: "开始安装",
    });
    if (!ok) return;
    runToolkit("install", { bundle: "train-stack" });
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
  document.getElementById("btn-ingress-start").addEventListener("click", startIngress);
  document.getElementById("btn-ingress-stop").addEventListener("click", stopIngress);
  document.getElementById("btn-ingress-status").addEventListener("click", async () => {
    try {
      const data = await refreshIngressStatus();
      log(JSON.stringify(data, null, 2));
      toast("入口状态已刷新");
    } catch (err) {
      toast(String(err.message || err));
    }
  });
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
  document.getElementById("btn-save-watchdog")?.addEventListener("click", saveWatchdog);
  document.getElementById("btn-save-auth")?.addEventListener("click", saveAuthConfig);
  document.getElementById("btn-add-node-card")?.addEventListener("click", () => addNodeCard());
  document.getElementById("job-runtime")?.addEventListener("change", syncJobRuntimeFields);
  document.getElementById("nodes-editor")?.addEventListener("change", syncNodesCardsFromEditor);
  document.getElementById("nodes-filter")?.addEventListener("input", (ev) => {
    stateSetup.nodesFilter = ev.target.value || "";
    renderNodesCards();
  });
  document.getElementById("nodes-density")?.addEventListener("change", (ev) => {
    stateSetup.nodesDensity = ev.target.value || "auto";
    document.querySelectorAll("#nodes-cards [data-node-card]").forEach((card) => commitCardToDraft(card));
    renderNodesCards();
  });
  on("btn-notices", "click", () => setNoticesPopOpen(!!$("notices-pop")?.hidden));
  on("btn-notices-close", "click", () => setNoticesPopOpen(false));
  on("btn-notices-read-all", "click", () => markAllNoticesRead());
  on("btn-notices-sync", "click", () => syncNotices({ toast: true }));
  on("btn-ai-analyze", "click", runAiAnalyze);
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
  on("btn-copy-urls", "click", async () => {
    const catalog = (state.overview && state.overview.endpoints) || {};
    const urls = catalog.urls || (catalog.endpoints || []).map((ep) => ep.url).filter(Boolean);
    if (!urls.length) return toast("暂无 URL");
    try {
      await copyText(urls.join("\n"));
      toastOk(`已复制 ${urls.length} 条 URL`);
    } catch (err) {
      toastBad(`复制失败：${err.message || err}`);
    }
  });

  document.getElementById("btn-exec-custom").addEventListener("click", () => {
    const command = document.getElementById("exec-custom").value.trim();
    if (!command) return log("命令为空");
    hideExecSuggest();
    rememberExecCommand(command);
    runExec({ command });
  });
  bindExecSuggest();
  document.getElementById("btn-run-py").addEventListener("click", runPython);
  document.querySelectorAll("[data-job-action]").forEach((btn) => {
    btn.addEventListener("click", () => runJobAction(btn.dataset.jobAction));
  });
  on("btn-job-logs", "click", async () => {
    const job = $("job-select")?.value;
    if (!job) return toastBad("请先选择 Job");
    const scope = $("job-target")?.value || "all";
    const qs = new URLSearchParams({ lines: "120" });
    if (scope && scope !== "all") qs.set("node", scope);
    setBusy(true);
    try {
      const data = await api(`/api/jobs/${encodeURIComponent(job)}/logs?${qs}`);
      writeConsoleText(data.result?.stdout || data.result?.stderr || "（无日志）", {
        meta: `Job ${job} 日志`,
      });
      toastOk("日志已拉取");
    } catch (err) {
      actionError("拉取 Job 日志", err);
    } finally {
      setBusy(false);
    }
  });
  document.getElementById("job-select")?.addEventListener("change", () => {
    syncJobArgsHint(state.overview?.jobs);
  });
  document.getElementById("deploy-gpus")?.addEventListener("input", (ev) => {
    ev.target.dataset.touched = "1";
  });
  document.getElementById("btn-install-job-template")?.addEventListener("click", installSelectedJobTemplate);
  document
    .getElementById("btn-install-recommended-jobs")
    ?.addEventListener("click", installRecommendedJobTemplates);
  on("job-template", "change", () => syncJobTemplateHint());
  on("setup-job-pick", "change", (ev) => {
    loadJobIntoForm(ev.target.value).catch((err) => actionError("加载 Job", err));
  });
  refreshJobTemplates().catch(() => { });
  document.getElementById("job-extra-args")?.addEventListener("input", (ev) => {
    ev.target.dataset.touched = "1";
  });

  document.getElementById("btn-models-refresh")?.addEventListener("click", () => refreshModels());
  on("model-select", "change", () => {
    const inline = $("model-action-result");
    if (inline) {
      inline.hidden = true;
      inline.textContent = "";
    }
    syncModelDetail();
  });
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
      const rows = data.results || [];
      const output = rows.length
        ? rows
          .map((r) => `[${resultOk(r) ? "OK" : "FAIL"}] ${r.node || r.host || "?"}\n${resultBody(r)}`)
          .join("\n\n")
        : [data.result?.stdout, data.result?.stderr].filter(Boolean).join("\n").trim() || label;
      if (rows.length) showResults(rows, label);
      else writeConsoleText(output, { meta: label });
      const inline = $("model-action-result");
      if (inline) {
        inline.textContent = output;
        inline.hidden = false;
      }
      if (data.models) renderModels(data.models);
      const ok = rows.length ? rows.every(resultOk) : data.result ? data.result.rc === 0 : true;
      if (ok) toastOk(`${label}完成`);
      else toastBad(`${label}有失败`);
    } catch (err) {
      const inline = $("model-action-result");
      if (inline) {
        inline.textContent = String(err.message || err);
        inline.hidden = false;
      }
      writeConsoleText(String(err.message || err), { meta: label });
      actionError(label, err);
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
  on("btn-models-install", "click", async () => {
    const ok = await confirmDialog({
      title: "安装拉取工具",
      message: "在目标节点安装 huggingface_hub + modelscope？",
      confirmText: "安装",
    });
    if (!ok) return;
    await runModelsAction("/api/models/install-tools", "安装拉取工具");
  });
  document.getElementById("btn-models-download")?.addEventListener("click", async () => {
    const name = document.getElementById("pull-repo")?.value.trim();
    if (!name) return toast("请填写仓库 ID，例如 org/model");
    const tool = document.getElementById("pull-tool")?.value || "auto";
    const registerId = document.getElementById("pull-id")?.value.trim();
    const register = !!document.getElementById("pull-register")?.checked;
    const node = $("model-node")?.value;
    const ok = await confirmDialog({
      title: "下载模型",
      message: `确认下载 ${name}${node ? ` 到 ${node}` : " 到全部节点"}？大模型可能耗时较长。`,
      confirmText: "开始下载",
    });
    if (!ok) return;
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
  on("btn-models-pull", "click", async () => {
    const model = $("model-select")?.value;
    if (!model) return toast("请选择已登记模型");
    const ok = await confirmDialog({
      title: "重新拉取模型",
      message: `确认重新拉取 ${model}？`,
      confirmText: "重新拉取",
    });
    if (!ok) return;
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
  on("btn-clear-secrets", "click", async () => {
    const ok = await confirmDialog({
      title: "清除 Token",
      message: "清除已保存的 Hugging Face / ModelScope Token？",
      confirmText: "清除",
      tone: "danger",
    });
    if (!ok) return;
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
    const ok = await confirmDialog({
      title: "部署 Job",
      message: `确认部署 ${job}${body.nodes ? ` 到 ${body.nodes}` : `（${body.gpus} GPU）`}？`,
      confirmText: "部署",
    });
    if (!ok) return;
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

  on("btn-queue-submit", "click", async () => {
    const job = $("job-select").value;
    if (!job) return toastBad("请先在任务页选择 Job");
    const meta = (state.overview?.jobs || []).find((j) => j.name === job);
    const gpus = Number(
      (meta && meta.gpus_per_node) || $("deploy-gpus")?.value || 4
    );
    setBusy(true);
    try {
      const data = await api("/api/queue/submit", {
        method: "POST",
        body: JSON.stringify({ job, gpus, note: "from-ui" }),
      });
      log(data.result.stdout || "submitted");
      toastOk(`已入队 ${job} · ${gpus} GPU`);
      await refresh(false);
    } catch (err) {
      actionError("任务入队", err);
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
      $("add-labels").value = "";
      stateSetup.data = null;
      toastOk(`已添加节点 ${name}`);
      await refresh(false);
      setView("nodes", { updateHash: false });
    } catch (err) {
      actionError("添加节点", err);
    } finally {
      setBusy(false);
    }
  });

  document.getElementById("btn-clear-console").addEventListener("click", () => {
    writeConsoleText("已清空。点「一键连通」或上方运维卡片可再次产生输出。", {
      empty: true,
      meta: "已清空",
    });
  });
  document.getElementById("btn-clear-log").addEventListener("click", () => {
    document.getElementById("log").textContent = "已清空";
  });
  document.getElementById("btn-console-connect").addEventListener("click", () => runOneclick("connect"));

  window.addEventListener("keydown", (ev) => {
    if (ev.metaKey || ev.ctrlKey || ev.altKey || ev.repeat) return;
    const target = ev.target;
    if (target && (["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName) || target.isContentEditable)) {
      return;
    }
    const map = {
      1: "actions",
      2: "cluster",
      3: "workloads",
      4: "models",
      5: "toolkits",
      6: "nodes",
      7: "endpoints",
      8: "ops",
      9: "terminal",
      g: "guide",
      G: "guide",
      c: "config",
      C: "config",
    };
    if (map[ev.key]) {
      ev.preventDefault();
      setView(map[ev.key]);
    }
    if (ev.key === "r" || ev.key === "R") {
      ev.preventDefault();
      $("btn-refresh")?.click();
    }
    if (ev.key === "h" || ev.key === "H") {
      ev.preventDefault();
      runApiHealth();
    }
  });

  bindAssistant();
  bindNotices();
  restoreAiAnalyze();

  if (state.auth && state.auth.enabled && !state.auth.authenticated) {
    writeConsoleText("控制台已锁定，请先登录。", { meta: "等待登录", empty: true });
    return;
  }

  const route = currentRoute();
  setView(route.view, { updateHash: false });
  if (route.view === "config" && route.tab) setConfigTab(route.tab);

  setBusy(true);
  try {
    await refresh(false);
    await loadQuality();
    await seedConsole();
    hideConsoleUnread();
    await loadAssistantStatus();
  } catch (err) {
    log(String(err.message || err));
    writeConsoleText(`初始化失败：${err.message || err}\n\n可以点「一键连通」重试。`, {
      meta: "初始化失败",
    });
  } finally {
    setBusy(false);
  }
}

const noticesState = {
  open: false,
  items: [],
  unread: 0,
};

function sparklinePath(values, width, height, pad = 4, domain = null) {
  const nums = values.map((v) => (v == null || Number.isNaN(Number(v)) ? null : Number(v)));
  const solid = nums.filter((v) => v != null);
  if (solid.length < 2) return { line: "", area: "" };
  let min = Math.min(...solid);
  let max = Math.max(...solid);
  if (domain && Number.isFinite(domain.min) && Number.isFinite(domain.max) && domain.max > domain.min) {
    min = domain.min;
    max = domain.max;
  } else if (max === min) {
    // 全常数时 (v-min)/span=0 会贴底，看起来像没画。
    if (min === 0) {
      max = 1;
    } else if (min > 0) {
      max = min * 1.25;
      min = 0;
    } else {
      const padY = Math.abs(min) || 1;
      min -= padY;
      max += padY;
    }
  }
  const span = max - min || 1;
  const n = nums.length;
  const pts = [];
  nums.forEach((v, i) => {
    if (v == null) return;
    const x = pad + (i / Math.max(1, n - 1)) * (width - pad * 2);
    const clamped = Math.min(max, Math.max(min, v));
    const y = height - pad - ((clamped - min) / span) * (height - pad * 2);
    pts.push([x, y]);
  });
  if (pts.length < 2) return { line: "", area: "" };
  const line = pts.map((p, i) => `${i ? "L" : "M"}${p[0].toFixed(1)},${p[1].toFixed(1)}`).join(" ");
  const area = `${line} L${pts[pts.length - 1][0].toFixed(1)},${height - pad} L${pts[0][0].toFixed(1)},${height - pad} Z`;
  return { line, area };
}

function paintSpark(svgId, values, { altValues = null, domain = null, altDomain = null } = {}) {
  const svg = $(svgId);
  if (!svg) return;
  const w = 320;
  const h = 72;
  const pad = 4;
  const main = sparklinePath(values, w, h, pad, domain);
  let html = "";
  // 参考线：顶 / 中 / 底，避免满刻度常数曲线「看不见」
  html += `<path class="guide" d="M${pad},${pad} H${w - pad}"></path>`;
  html += `<path class="guide" d="M${pad},${(h / 2).toFixed(1)} H${w - pad}"></path>`;
  html += `<path class="guide" d="M${pad},${h - pad} H${w - pad}"></path>`;
  if (main.area) html += `<path class="area" d="${main.area}"></path>`;
  if (main.line) html += `<path class="line" d="${main.line}"></path>`;
  if (altValues) {
    const alt = sparklinePath(altValues, w, h, pad, altDomain);
    if (alt.line) html += `<path class="line alt" d="${alt.line}"></path>`;
  }
  if (!main.line && !(altValues && sparklinePath(altValues, w, h, pad, altDomain).line)) {
    html += `<text x="12" y="40" fill="currentColor" font-size="12">采样不足，刷新总览稍后可见</text>`;
  }
  svg.innerHTML = html;
}

function fmtPct(v) {
  if (v == null || Number.isNaN(Number(v))) return "-";
  return `${Number(v).toFixed(Number(v) % 1 ? 1 : 0)}%`;
}

function renderTrends(payload) {
  const points = (payload && payload.points) || [];
  const note = $("dash-trends-note");
  if (note) note.textContent = payload?.note || "控制面采样（非业务 QPS）";
  const last = points[points.length - 1] || {};
  const set = (id, text) => {
    const el = $(id);
    if (el) el.textContent = text;
  };
  set("trend-api", fmtPct(last.api_ok_pct));
  set("trend-gpu", fmtPct(last.gpu_util_pct));
  set("trend-cpu", fmtPct(last.cpu_avg_pct));
  set("trend-edge", last.edge_upstreams != null ? String(last.edge_upstreams) : "-");
  set("trend-checks", last.api_total != null ? String(last.api_total) : "-");
  set("trend-score", last.quality_score != null ? String(Math.round(last.quality_score)) : "-");
  const pctDomain = { min: 0, max: 100 };
  paintSpark("chart-api", points.map((p) => p.api_ok_pct), { domain: pctDomain });
  paintSpark("chart-gpu", points.map((p) => p.gpu_util_pct), { domain: pctDomain });
  paintSpark("chart-cpu", points.map((p) => p.cpu_avg_pct), { domain: pctDomain });
  paintSpark("chart-mix", points.map((p) => p.quality_score), {
    domain: pctDomain,
    altValues: points.map((p) => p.api_total),
  });
}

function noticeItemHtml(row) {
  const unread = row.read ? "" : " unread";
  const sev = escapeHtml(row.severity || "info");
  const fix = row.fix
    ? `<code class="meta">${escapeHtml(String(row.fix))}</code>`
    : "";
  return `<article class="notice-item${unread}" data-severity="${sev}" data-notice-id="${escapeHtml(row.id)}">
    <header>
      <strong>${escapeHtml(row.title || row.id)}</strong>
      <span class="meta">${escapeHtml(row.source || "")} · ${escapeHtml(sev)}</span>
    </header>
    <p>${escapeHtml(row.detail || "")}</p>
    ${fix}
    <div class="notice-actions">
      ${row.read ? "" : `<button type="button" class="ghost tiny" data-notice-read="${escapeHtml(row.id)}" data-keep-enabled="1">已读</button>`}
      <button type="button" class="ghost tiny" data-notice-dismiss="${escapeHtml(row.id)}" data-keep-enabled="1">清除</button>
    </div>
  </article>`;
}

function bindNoticeActions(root) {
  if (!root) return;
  root.querySelectorAll("[data-notice-read]").forEach((btn) => {
    btn.addEventListener("click", () => markNotices([btn.dataset.noticeRead], { read: true }));
  });
  root.querySelectorAll("[data-notice-dismiss]").forEach((btn) => {
    btn.addEventListener("click", () => markNotices([btn.dataset.noticeDismiss], { dismissed: true }));
  });
}

function renderNotices(payload) {
  const data = payload || {};
  noticesState.items = data.items || [];
  noticesState.unread = Number(data.unread || 0);
  const badge = $("notices-badge");
  if (badge) {
    if (noticesState.unread > 0) {
      badge.hidden = false;
      badge.textContent = String(Math.min(99, noticesState.unread));
    } else {
      badge.hidden = true;
    }
  }
  const html =
    noticesState.items.map(noticeItemHtml).join("") ||
    '<p class="meta">暂无公告。点「同步检测」拉取 diagnose / 健康规则。</p>';
  const list = $("notices-list");
  const popList = $("notices-pop-list");
  if (list) {
    list.innerHTML = html;
    bindNoticeActions(list);
  }
  if (popList) {
    popList.innerHTML = html;
    bindNoticeActions(popList);
  }
}

function setNoticesPopOpen(open) {
  noticesState.open = !!open;
  const pop = $("notices-pop");
  const btn = $("btn-notices");
  if (pop) pop.hidden = !open;
  if (btn) btn.setAttribute("aria-expanded", open ? "true" : "false");
}

async function syncNotices({ toast: showToast = false } = {}) {
  try {
    const data = await api("/api/notices?sync=1", { timeoutMs: 90000 });
    renderNotices(data);
    if (showToast) toastOk(`已同步 ${data.count || 0} 条公告`);
  } catch (err) {
    if (showToast) toastBad(`同步公告失败：${err.message || err}`);
    else log(`notices sync: ${err.message || err}`);
  }
}

async function markNotices(ids, flags) {
  try {
    const data = await api("/api/notices/mark", {
      method: "POST",
      body: JSON.stringify({ ids, ...flags }),
    });
    renderNotices(data);
  } catch (err) {
    toastBad(`更新公告失败：${err.message || err}`);
  }
}

async function markAllNoticesRead() {
  await markNotices(undefined, { read: true });
}

function restoreAiAnalyze() {
  try {
    const raw = localStorage.getItem("gpu-control-ai-analyze");
    if (!raw) return;
    const saved = JSON.parse(raw);
    const box = $("ai-analyze-box");
    const at = $("ai-analyze-at");
    if (box && saved.reply) box.textContent = saved.reply;
    if (at && saved.at) at.textContent = saved.at;
  } catch (_) {
    /* ignore */
  }
}

async function runAiAnalyze() {
  const box = $("ai-analyze-box");
  const at = $("ai-analyze-at");
  const btn = $("btn-ai-analyze");
  if (btn) btn.disabled = true;
  if (box) box.textContent = "正在深度同步（diagnose）并请求 AI 解读…";
  if (at) at.textContent = "进行中…";
  try {
    const data = await api("/api/assistant/analyze", {
      method: "POST",
      body: "{}",
      timeoutMs: 120000,
    });
    const reply = data.reply || data.error || "（无解读）";
    const stamp = new Date().toLocaleString();
    if (box) box.textContent = reply;
    if (at) at.textContent = stamp;
    localStorage.setItem(
      "gpu-control-ai-analyze",
      JSON.stringify({ reply, at: stamp, ok: !!data.ok })
    );
    if (data.open_settings) {
      setAssistantOpen(true);
      setAssistantSettingsOpen(true);
    }
    await syncNotices();
    if (data.ok) toastOk("AI 解读完成");
  } catch (err) {
    if (box) box.textContent = `解读失败：${err.message || err}`;
    if (at) at.textContent = "失败";
    toastBad(`AI 解读失败：${err.message || err}`);
  } finally {
    if (btn) btn.disabled = false;
  }
}

function bindNotices() {
  document.addEventListener("click", (ev) => {
    const pop = $("notices-pop");
    const btn = $("btn-notices");
    if (!noticesState.open || !pop) return;
    const t = ev.target;
    if (pop.contains(t) || btn?.contains(t)) return;
    setNoticesPopOpen(false);
  });
}

const assistantState = {
  open: false,
  busy: false,
  settingsOpen: false,
  messages: [],
  status: null,
  models: [],
};

function setAssistantOpen(open) {
  assistantState.open = !!open;
  const panel = $("assistant-panel");
  const fab = $("assistant-fab");
  if (panel) panel.hidden = !open;
  if (fab) fab.setAttribute("aria-expanded", open ? "true" : "false");
  if (open) {
    if (assistantState.settingsOpen) $("assistant-base-url")?.focus();
    else $("assistant-input")?.focus();
  }
}

function setAssistantSettingsOpen(open) {
  assistantState.settingsOpen = !!open;
  const settings = $("assistant-settings");
  const chat = $("assistant-chat-view");
  const btn = $("assistant-settings-btn");
  if (settings) settings.hidden = !open;
  if (chat) chat.hidden = !!open;
  if (btn) btn.textContent = open ? "对话" : "设置";
  if (open) {
    fillAssistantSettingsForm(assistantState.status);
    $("assistant-base-url")?.focus();
  } else {
    $("assistant-input")?.focus();
  }
}

function fillAssistantSettingsForm(status) {
  const base = status?.base_url || "";
  const model = status?.model || "";
  const preset = $("assistant-preset");
  const baseInput = $("assistant-base-url");
  const modelInput = $("assistant-model");
  const keyInput = $("assistant-api-key");
  const clearKey = $("assistant-clear-key");
  const hint = $("assistant-settings-hint");
  if (baseInput) baseInput.value = base;
  if (modelInput) modelInput.value = model;
  if (keyInput) keyInput.value = "";
  if (clearKey) clearKey.checked = false;
  if (preset) {
    const openaiUrl = "https://api.openai.com/v1";
    preset.value = !base || base === openaiUrl ? "openai" : "custom";
  }
  if (hint) {
    const note = status?.network_note || "请求由控制面主机访问 Base URL（不是浏览器本机）。";
    hint.textContent = status?.api_key_configured
      ? `${note} 已保存 API Key（留空则保留）。`
      : note;
  }
  renderAssistantModelOptions(assistantState.models, model);
}

function renderAssistantModelOptions(models, selected) {
  const select = $("assistant-model-select");
  const list = $("assistant-model-list");
  const ids = Array.isArray(models) ? models : [];
  assistantState.models = ids;
  if (list) {
    list.innerHTML = ids.map((id) => `<option value="${escapeHtml(id)}"></option>`).join("");
  }
  if (!select) return;
  const current = selected || $("assistant-model")?.value || "";
  const opts = ['<option value="">— 手填或选择 —</option>'];
  for (const id of ids) {
    const sel = id === current ? " selected" : "";
    opts.push(`<option value="${escapeHtml(id)}"${sel}>${escapeHtml(id)}</option>`);
  }
  if (current && !ids.includes(current)) {
    opts.push(`<option value="${escapeHtml(current)}" selected>${escapeHtml(current)}（当前）</option>`);
  }
  select.innerHTML = opts.join("");
}

function applyAssistantStatus(data) {
  assistantState.status = data;
  const mode = $("assistant-mode");
  if (mode) {
    if (data.ready) {
      mode.textContent = `模型 · ${data.model || "configured"}`;
    } else {
      mode.textContent = "未配置 · 请设置上游";
    }
  }
  renderAssistantQuick(data.quick_actions || []);
  if (assistantState.settingsOpen) fillAssistantSettingsForm(data);
}

function appendAssistantMsg(role, text, extra = {}) {
  const thread = $("assistant-thread");
  if (!thread) return;
  const el = document.createElement("div");
  el.className = `assistant-msg ${role === "user" ? "user" : "bot"}`;
  el.textContent = text || "";
  if (extra.warning) {
    const w = document.createElement("span");
    w.className = "warn";
    w.textContent = extra.warning;
    el.appendChild(w);
  }
  thread.appendChild(el);
  thread.scrollTop = thread.scrollHeight;
}

function renderAssistantQuick(actions) {
  const root = $("assistant-quick");
  if (!root) return;
  root.innerHTML = (actions || [])
    .map(
      (a) =>
        `<button type="button" data-assistant-quick="${escapeHtml(a.id)}" title="${escapeHtml(a.hint || "")}">${escapeHtml(a.label)}</button>`
    )
    .join("");
  root.querySelectorAll("[data-assistant-quick]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const action = (assistantState.status?.quick_actions || []).find((x) => x.id === btn.dataset.assistantQuick);
      if (!action?.prompt) return;
      if (action.prompt === "__open_settings__") {
        setAssistantSettingsOpen(true);
        return;
      }
      sendAssistant(action.prompt);
    });
  });
}

function renderAssistantPending(pending) {
  const root = $("assistant-pending");
  if (!root) return;
  const items = pending || [];
  if (!items.length) {
    root.hidden = true;
    root.innerHTML = "";
    return;
  }
  root.hidden = false;
  root.innerHTML = items
    .map((row, i) => {
      const tool = row.tool || "";
      const args = JSON.stringify(row.args || {});
      return `<button type="button" data-pending-idx="${i}"><strong>${escapeHtml(tool)}</strong> · ${escapeHtml(row.risk || "")}<br><code>${escapeHtml(args)}</code></button>`;
    })
    .join("");
  root.querySelectorAll("[data-pending-idx]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const row = items[Number(btn.dataset.pendingIdx)];
      if (row) runAssistantPending(row);
    });
  });
}

async function runAssistantPending(row) {
  const tool = row.tool;
  const args = row.args || {};
  try {
    if (tool === "gc_deploy") {
      await api("/api/deploy", {
        method: "POST",
        body: JSON.stringify({
          job: args.job,
          nodes: args.nodes,
          force: !!args.force,
        }),
      });
      toastOk(`已部署 ${args.job || ""}`);
    } else if (tool === "gc_free_node") {
      await api("/api/free", {
        method: "POST",
        body: JSON.stringify({ node: args.node, cordon: !!args.cordon }),
      });
      toastOk(`已腾空 ${args.node || ""}`);
    } else if (tool === "gc_job_action") {
      const name = args.name;
      const action = args.action;
      await api(`/api/jobs/${encodeURIComponent(name)}/action`, {
        method: "POST",
        body: JSON.stringify({ action, node: args.node }),
      });
      toastOk(`Job ${name} ${action}`);
    } else {
      toastBad(`暂不支持一键确认：${tool}`);
      return;
    }
    appendAssistantMsg("bot", `已执行 ${tool}。可在「执行输出」查看细节。`);
    renderAssistantPending([]);
    await refresh(false);
  } catch (err) {
    actionError("助手建议操作", err);
  }
}

async function loadAssistantStatus() {
  const mode = $("assistant-mode");
  try {
    const data = await api("/api/assistant/status");
    applyAssistantStatus(data);
  } catch (err) {
    if (mode) mode.textContent = "助手暂不可用";
    log(`assistant status: ${err.message || err}`);
  }
}

function assistantDraftBody() {
  const baseUrl = ($("assistant-base-url")?.value || "").trim();
  const apiKey = ($("assistant-api-key")?.value || "").trim();
  const body = {};
  if (baseUrl) body.base_url = baseUrl;
  if (apiKey) body.api_key = apiKey;
  return body;
}

async function probeAssistantUpstream() {
  const hint = $("assistant-settings-hint");
  const btn = $("assistant-probe");
  if (btn) btn.disabled = true;
  try {
    const data = await api("/api/assistant/probe", {
      method: "POST",
      body: JSON.stringify(assistantDraftBody()),
      timeoutMs: 45000,
    });
    if (data.ok) {
      renderAssistantModelOptions(data.models_sample || [], $("assistant-model")?.value);
      if (hint) hint.textContent = `连通正常 · ${data.host || ""} · 约 ${data.count || 0} 个模型`;
      toastOk("上游连通正常");
    } else {
      if (hint) hint.textContent = `${data.error || "探测失败"}${data.hint ? " — " + data.hint : ""}`;
      toastBad(data.error || "探测失败");
    }
  } catch (err) {
    if (hint) hint.textContent = `探测失败：${err.message || err}`;
    toastBad(`连通探测失败：${err.message || err}`);
  } finally {
    if (btn) btn.disabled = false;
  }
}

async function fetchAssistantModels() {
  const hint = $("assistant-settings-hint");
  const btn = $("assistant-fetch-models");
  if (!$("assistant-base-url")?.value?.trim()) {
    toastBad("请先填写 Base URL（内网请用可达的兼容网关）");
    return;
  }
  if (btn) btn.disabled = true;
  try {
    const data = await api("/api/assistant/models", {
      method: "POST",
      body: JSON.stringify(assistantDraftBody()),
      timeoutMs: 45000,
    });
    renderAssistantModelOptions(data.models || [], data.selected || $("assistant-model")?.value);
    if (hint) hint.textContent = `已拉取 ${data.count || 0} 个模型（${data.base_url || ""}）`;
    toastOk(`已拉取 ${data.count || 0} 个模型`);
  } catch (err) {
    if (hint) hint.textContent = `拉取失败：${err.message || err}`;
    toastBad(`拉取模型失败：${err.message || err}`);
  } finally {
    if (btn) btn.disabled = false;
  }
}

async function saveAssistantConfig() {
  const baseUrl = ($("assistant-base-url")?.value || "").trim();
  const model = ($("assistant-model")?.value || "").trim();
  const apiKey = ($("assistant-api-key")?.value || "").trim();
  const clearKey = !!$("assistant-clear-key")?.checked;
  const hint = $("assistant-settings-hint");
  const btn = $("assistant-save-config");
  if (!baseUrl || !model) {
    toastBad("请填写 Base URL 与模型");
    return;
  }
  if (btn) btn.disabled = true;
  try {
    const body = {
      base_url: baseUrl,
      model,
      enabled: true,
      clear_api_key: clearKey,
    };
    if (apiKey) body.api_key = apiKey;
    const data = await api("/api/assistant/config", {
      method: "POST",
      body: JSON.stringify(body),
    });
    applyAssistantStatus(data);
    if ($("assistant-api-key")) $("assistant-api-key").value = "";
    if ($("assistant-clear-key")) $("assistant-clear-key").checked = false;
    if (hint) hint.textContent = data.ready ? "已保存，可返回对话。" : "已保存，但仍未就绪。";
    toastOk(data.ready ? "助手上游已就绪" : "已保存配置");
    if (data.ready) setAssistantSettingsOpen(false);
  } catch (err) {
    if (hint) hint.textContent = `保存失败：${err.message || err}`;
    toastBad(`保存失败：${err.message || err}`);
  } finally {
    if (btn) btn.disabled = false;
  }
}

async function sendAssistant(text) {
  const content = (text || "").trim();
  if (!content || assistantState.busy) return;
  if (content === "__open_settings__") {
    setAssistantSettingsOpen(true);
    return;
  }
  const input = $("assistant-input");
  if (input) input.value = "";
  assistantState.messages.push({ role: "user", content });
  appendAssistantMsg("user", content);
  assistantState.busy = true;
  const sendBtn = $("assistant-send");
  if (sendBtn) sendBtn.disabled = true;
  try {
    const data = await api("/api/assistant/chat", {
      method: "POST",
      body: JSON.stringify({ messages: assistantState.messages }),
      timeoutMs: 120000,
    });
    const reply = data.reply || data.error || "（无回复）";
    assistantState.messages.push({ role: "assistant", content: reply });
    appendAssistantMsg("bot", reply, { warning: data.warning });
    if (data.quick_actions) renderAssistantQuick(data.quick_actions);
    renderAssistantPending(data.pending_actions || []);
    if (data.open_settings) setAssistantSettingsOpen(true);
  } catch (err) {
    appendAssistantMsg("bot", `请求失败：${err.message || err}`);
  } finally {
    assistantState.busy = false;
    if (sendBtn) sendBtn.disabled = false;
  }
}

function bindAssistant() {
  const fab = $("assistant-fab");
  const closeBtn = $("assistant-close");
  const form = $("assistant-form");
  const settingsBtn = $("assistant-settings-btn");
  const settingsBack = $("assistant-settings-back");
  const fetchBtn = $("assistant-fetch-models");
  const probeBtn = $("assistant-probe");
  const saveBtn = $("assistant-save-config");
  const preset = $("assistant-preset");
  const modelSelect = $("assistant-model-select");
  if (fab) {
    fab.addEventListener("click", () => {
      setAssistantOpen(!assistantState.open);
      if (assistantState.open && !assistantState.status) loadAssistantStatus();
    });
  }
  if (closeBtn) closeBtn.addEventListener("click", () => setAssistantOpen(false));
  if (settingsBtn) {
    settingsBtn.addEventListener("click", () => {
      setAssistantSettingsOpen(!assistantState.settingsOpen);
    });
  }
  if (settingsBack) settingsBack.addEventListener("click", () => setAssistantSettingsOpen(false));
  if (fetchBtn) fetchBtn.addEventListener("click", () => fetchAssistantModels());
  if (probeBtn) probeBtn.addEventListener("click", () => probeAssistantUpstream());
  if (saveBtn) saveBtn.addEventListener("click", () => saveAssistantConfig());
  if (preset) {
    preset.addEventListener("change", () => {
      const baseInput = $("assistant-base-url");
      const hint = $("assistant-settings-hint");
      if (!baseInput) return;
      if (preset.value === "openai") {
        baseInput.value = "https://api.openai.com/v1";
        if (hint) {
          hint.textContent =
            "OpenAI 官方需控制面主机能解析外网 DNS。当前内网机器常失败：请改用「自定义」填可达网关，或先点连通探测。";
        }
      } else if (!baseInput.value || baseInput.value === "https://api.openai.com/v1") {
        baseInput.value = "";
        baseInput.focus();
        if (hint) {
          hint.textContent = "请填写控制面可达的兼容网关，例如 http://10.x.x.x:8000/v1";
        }
      }
    });
  }
  if (modelSelect) {
    modelSelect.addEventListener("change", () => {
      const modelInput = $("assistant-model");
      if (modelInput && modelSelect.value) modelInput.value = modelSelect.value;
    });
  }
  if (form) {
    form.addEventListener("submit", (ev) => {
      ev.preventDefault();
      sendAssistant($("assistant-input")?.value || "");
    });
  }
}

boot();
