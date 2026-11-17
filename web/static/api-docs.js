/* API docs page logic (external for CSP). */
    const TOKEN_KEY = "gpu-control-api-token";
    const state = { paths: {}, auth: null, info: null, rows: [] };

    function escapeHtml(s) {
      return String(s)
        .replaceAll("&", "&amp;").replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;").replaceAll('"', "&quot;");
    }

    function methodClass(m) { return (m || "get").toLowerCase(); }

    function authHeaders() {
      const token = (document.getElementById("token").value || "").trim();
      const h = { Accept: "application/json" };
      if (token) h.Authorization = "Bearer " + token;
      return h;
    }

    function renderAuth(auth) {
      const el = document.getElementById("auth-box");
      if (!auth) { el.textContent = "无鉴权元数据"; return; }
      const schemes = (auth.schemes || []).join(" / ") || "（未启用）";
      el.innerHTML = [
        `<div><b>mode</b> = <code>${escapeHtml(auth.mode)}</code> · enabled=${auth.enabled} · ${schemes}</div>`,
        `<div style="margin-top:6px">${escapeHtml(auth.note || "")}</div>`,
        `<div style="margin-top:6px">签发：<code>${escapeHtml(auth.issue_token || "")}</code></div>`,
      ].join("");
    }

    function flatten() {
      const rows = [];
      for (const [path, methods] of Object.entries(state.paths)) {
        for (const [method, op] of Object.entries(methods)) {
          rows.push({
            method: method.toUpperCase(),
            path,
            summary: op.summary || "",
            tags: op.tags || [],
            security: op.security,
            body: op.requestBody || null,
            params: op.parameters || [],
          });
        }
      }
      rows.sort((a, b) => a.path.localeCompare(b.path) || a.method.localeCompare(b.method));
      return rows;
    }

    function fillTry(row) {
      document.getElementById("try-method").value = row.method;
      document.getElementById("try-path").value = row.path;
      if (row.body && row.body.content && row.body.content["application/json"]) {
        const schema = row.body.content["application/json"].schema || {};
        document.getElementById("try-body").value = JSON.stringify(exampleFromSchema(schema), null, 2);
      }
      document.querySelectorAll("#rows tr").forEach((tr) => tr.classList.remove("active"));
    }

    function exampleFromSchema(schema) {
      if (!schema || typeof schema !== "object") return {};
      if (schema.example != null) return schema.example;
      if (schema.type === "object" || schema.properties) {
        const out = {};
        for (const [k, v] of Object.entries(schema.properties || {})) {
          if (v.enum) out[k] = v.enum[0];
          else if (v.type === "boolean") out[k] = false;
          else if (v.type === "integer" || v.type === "number") out[k] = 0;
          else if (v.type === "array") out[k] = [];
          else if (v.type === "object") out[k] = {};
          else out[k] = "";
        }
        return out;
      }
      return {};
    }

    function render() {
      const q = (document.getElementById("q").value || "").trim().toLowerCase();
      const tag = document.getElementById("tag").value;
      const tbody = document.getElementById("rows");
      const rows = flatten().filter((r) => {
        if (tag && !(r.tags || []).includes(tag)) return false;
        if (!q) return true;
        return `${r.method} ${r.path} ${r.summary} ${(r.tags || []).join(" ")}`.toLowerCase().includes(q);
      });
      state.rows = rows;
      tbody.innerHTML = rows.map((r, idx) => {
        const publicOp = Array.isArray(r.security) && r.security.length === 0;
        const tags = (r.tags || []).map((t) => `<span class="tag">${escapeHtml(t)}</span>`).join("");
        const pub = publicOp ? `<span class="tag public">公开</span>` : "";
        let detail = "";
        if ((r.params && r.params.length) || r.body) {
          detail = `<details><summary>参数 / 请求体</summary><pre>${escapeHtml(JSON.stringify({ parameters: r.params, requestBody: r.body }, null, 2))}</pre></details>`;
        }
        return `<tr data-idx="${idx}" tabindex="0" aria-label="${escapeHtml(`${r.method} ${r.path} ${r.summary}`)}">
          <td><span class="method ${methodClass(r.method)}">${r.method}</span></td>
          <td><div class="path">${escapeHtml(r.path)}</div>${tags}${pub}${detail}</td>
          <td>${escapeHtml(r.summary)}</td>
        </tr>`;
      }).join("") || `<tr><td colspan="3" class="meta">无匹配接口</td></tr>`;

      tbody.querySelectorAll("tr[data-idx]").forEach((tr) => {
        const apply = () => {
          const row = state.rows[Number(tr.dataset.idx)];
          if (!row) return;
          fillTry(row);
          tr.classList.add("active");
        };
        tr.addEventListener("click", (ev) => {
          if (ev.target.closest("details")) return;
          apply();
        });
        tr.addEventListener("keydown", (ev) => {
          if (ev.target.closest("details")) return;
          if (ev.key === "Enter" || ev.key === " ") {
            ev.preventDefault();
            apply();
          }
        });
      });
    }

    async function sendTry() {
      const method = document.getElementById("try-method").value;
      let path = (document.getElementById("try-path").value || "").trim();
      if (!path.startsWith("/")) path = "/" + path;
      const out = document.getElementById("try-out");
      out.textContent = "请求中…";
      const opts = { method, headers: authHeaders(), credentials: "same-origin" };
      if (method !== "GET" && method !== "HEAD") {
        opts.headers["Content-Type"] = "application/json";
        const raw = document.getElementById("try-body").value.trim();
        opts.body = raw || "{}";
      }
      try {
        const res = await fetch(path, opts);
        const text = await res.text();
        let body = text;
        try { body = JSON.stringify(JSON.parse(text), null, 2); } catch (_) {}
        out.innerHTML = `<span class="${res.ok ? "ok" : "err"}">HTTP ${res.status}</span>\n${escapeHtml(body)}`;
      } catch (err) {
        out.innerHTML = `<span class="err">${escapeHtml(err.message || err)}</span>`;
      }
    }

    async function boot() {
      document.getElementById("token").value = localStorage.getItem(TOKEN_KEY) || "";
      document.getElementById("btn-save-token").addEventListener("click", () => {
        localStorage.setItem(TOKEN_KEY, document.getElementById("token").value.trim());
        const status = document.getElementById("token-status");
        if (status) {
          status.hidden = false;
          status.textContent = "已保存 Token（仅本机浏览器）";
          clearTimeout(status._t);
          status._t = setTimeout(() => { status.hidden = true; }, 2600);
        }
      });
      document.getElementById("btn-send").addEventListener("click", sendTry);
      document.getElementById("q").addEventListener("input", render);
      document.getElementById("q").addEventListener("keydown", (ev) => {
        if (ev.key !== "Enter") return;
        const first = document.querySelector("#rows tr[data-idx]");
        if (first) {
          ev.preventDefault();
          first.focus();
          first.click();
        }
      });
      document.getElementById("tag").addEventListener("change", render);
      try {
        const res = await fetch("/api/openapi.json", { credentials: "same-origin", headers: authHeaders() });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const doc = await res.json();
        state.paths = doc.paths || {};
        state.auth = doc["x-gpu-control-auth"] || null;
        state.info = doc.info || {};
        document.getElementById("hdr-meta").textContent =
          `${state.info.title || "gpu-control"} · v${state.info.version || "?"} · OpenAPI ${doc.openapi || "3"}`;
        renderAuth(state.auth);
        const tags = new Set();
        for (const methods of Object.values(state.paths)) {
          for (const op of Object.values(methods)) (op.tags || []).forEach((t) => tags.add(t));
        }
        const sel = document.getElementById("tag");
        [...tags].sort().forEach((t) => {
          const opt = document.createElement("option");
          opt.value = t; opt.textContent = t; sel.appendChild(opt);
        });
        render();
      } catch (err) {
        document.getElementById("hdr-meta").innerHTML = `<span class="err">${escapeHtml(err.message || err)}</span>`;
        document.getElementById("auth-box").textContent = "无法加载 openapi.json；可先填写 Bearer 再刷新";
      }
    }
    boot();

