'use strict';

const vscode = require('vscode');
const fs = require('fs');
const path = require('path');

const SECTION = 'computedeck';
const TOKEN_KEY = 'computedeck.token';
const COOKIE_KEY = 'computedeck.cookie';
const MCP_PROVIDER_ID = 'computedeck.mcp';

let client;
let provider;
let statusBar;
let output;
let pollTimer;
let mcpEmitter;
let workbenchPanel;
let controlUiPanel;
let guidePanel;
let extensionContext;

class ApiError extends Error {
  constructor(message, status, data) {
    super(message);
    this.status = status;
    this.data = data;
  }
}

// ── API 客户端 ─────────────────────────────────────────────
class ComputeDeckClient {
  constructor(context) {
    this.context = context;
  }

  base() {
    const raw = vscode.workspace.getConfiguration(SECTION).get('apiBase', 'http://127.0.0.1:8787');
    return String(raw || '').trim().replace(/\/+$/, '') || 'http://127.0.0.1:8787';
  }

  async authHeaders() {
    const headers = { 'Content-Type': 'application/json', Accept: 'application/json' };
    const token = await this.resolveToken();
    if (token) headers.Authorization = `Bearer ${token}`;
    const cookie = await this.context.secrets.get(COOKIE_KEY);
    if (cookie) headers.Cookie = cookie;
    return headers;
  }

  async resolveToken() {
    // On the control host, prefer the live secrets file so a stale SecretStorage
    // value cannot block "open workspace and it just works".
    const fromLocal = readLocalApiToken();
    if (fromLocal) {
      const stored = await this.context.secrets.get(TOKEN_KEY);
      if (stored !== fromLocal) {
        await this.context.secrets.store(TOKEN_KEY, fromLocal);
        if (output) {
          output.appendLine(t('已自动读取本机登录密钥：{0}', localTokenPath() || '(local)'));
        }
      }
      return fromLocal;
    }
    const stored = await this.context.secrets.get(TOKEN_KEY);
    if (stored) return stored;
    const fromEnv = String(process.env.GPU_CONTROL_API_TOKEN || '').trim();
    if (!fromEnv) return '';
    await this.context.secrets.store(TOKEN_KEY, fromEnv);
    return fromEnv;
  }

  async request(pathname, { method = 'GET', body, timeoutMs = 30000 } = {}) {
    const url = `${this.base()}${pathname}`;
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    let res;
    try {
      res = await fetch(url, {
        method,
        headers: await this.authHeaders(),
        body: body === undefined ? undefined : JSON.stringify(body),
        signal: controller.signal,
      });
    } catch (err) {
      throw new Error(t('无法连接 {0}：{1}', this.base(), err && err.message ? err.message : err));
    } finally {
      clearTimeout(timer);
    }
    const text = await res.text();
    let data = {};
    try {
      data = text ? JSON.parse(text) : {};
    } catch (_) {
      data = { raw: text.slice(0, 2000) };
    }
    if (res.status === 401) {
      throw new ApiError(t('需要登录（命令面板：密码登录 / 使用本机密钥）'), 401, data);
    }
    if (!res.ok) {
      const message = data.error || data.detail || `HTTP ${res.status}`;
      throw new ApiError(String(message), res.status, data);
    }
    return data;
  }

  async login(password) {
    const res = await fetch(`${this.base()}/api/auth/login`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ password }),
    });
    const text = await res.text();
    if (!res.ok) {
      let detail = text;
      try {
        detail = JSON.parse(text).error || detail;
      } catch (_) { }
      throw new Error(t('登录失败：{0}', detail || res.status));
    }
    const setCookies =
      typeof res.headers.getSetCookie === 'function'
        ? res.headers.getSetCookie()
        : [res.headers.get('set-cookie')].filter(Boolean);
    const cookie = setCookies.map((x) => String(x).split(';')[0]).join('; ');
    if (!cookie) throw new Error(t('登录成功但没有拿到 Session Cookie'));
    await this.context.secrets.store(COOKIE_KEY, cookie);
    return cookie;
  }

  overview(light = true) {
    return this.request(`/api/overview${light ? '?light=1' : ''}`, { timeoutMs: 30000 });
  }

  liveness() {
    return this.request('/api/liveness', { timeoutMs: 15000 });
  }

  livenessCheck() {
    return this.request('/api/liveness/check', { method: 'POST', body: {}, timeoutMs: 90000 });
  }

  async jobs() {
    const data = await this.request('/api/jobs', { timeoutMs: 15000 });
    return Array.isArray(data.jobs) ? data.jobs : Array.isArray(data) ? data : [];
  }

  gatewayStatus() {
    return this.request('/api/gateway/status', { timeoutMs: 15000 });
  }

  doctor() {
    return this.request('/api/doctor', { timeoutMs: 60000 });
  }

  testSSH(node) {
    return this.request('/api/setup/test-ssh', {
      method: 'POST',
      body: node ? { node } : {},
      timeoutMs: 90000,
    });
  }

  execPreset(node, preset) {
    return this.request('/api/exec', {
      method: 'POST',
      body: { node, preset },
      timeoutMs: 180000,
    });
  }

  deploy(job, { gpus, nodes } = {}) {
    const body = { job };
    if (nodes) body.nodes = nodes;
    if (gpus != null) body.gpus = gpus;
    return this.request('/api/deploy', { method: 'POST', body, timeoutMs: 1900000 });
  }

  freeNode(node, cordon) {
    return this.request('/api/free', {
      method: 'POST',
      body: { node, cordon: !!cordon },
      timeoutMs: 1200000,
    });
  }

  jobAction(name, action) {
    return this.request(`/api/jobs/${encodeURIComponent(name)}/action`, {
      method: 'POST',
      body: { action },
      timeoutMs: 1200000,
    });
  }

  configNodes() {
    return this.request('/api/config/nodes', { timeoutMs: 15000 });
  }

  assistantStatus() {
    return this.request('/api/assistant/status', { timeoutMs: 15000 });
  }

  assistantConfig(body) {
    return this.request('/api/assistant/config', { method: 'POST', body, timeoutMs: 30000 });
  }

  assistantProbe(body = {}) {
    return this.request('/api/assistant/probe', { method: 'POST', body, timeoutMs: 45000 });
  }

  assistantModels(body = {}) {
    return this.request('/api/assistant/models', { method: 'POST', body, timeoutMs: 60000 });
  }

  notices() {
    return this.request('/api/notices', { timeoutMs: 15000 });
  }

  authStatus() {
    return this.request('/api/auth/status', { timeoutMs: 10000 });
  }

  assistantChat(body) {
    return this.request('/api/assistant/chat', {
      method: 'POST',
      body,
      timeoutMs: 180000,
    });
  }

  assistantExecute(body) {
    return this.request('/api/assistant/execute', {
      method: 'POST',
      body,
      timeoutMs: 180000,
    });
  }

  noticesList() {
    return this.request('/api/notices', { timeoutMs: 15000 });
  }

  noticesMark(ids, read = true) {
    return this.request('/api/notices/mark', {
      method: 'POST',
      body: { ids, read: !!read },
      timeoutMs: 15000,
    });
  }

  queueList() {
    return this.request('/api/queue', { timeoutMs: 15000 });
  }

  queueAction(action, body = {}) {
    return this.request(`/api/queue/${action}`, {
      method: 'POST',
      body,
      timeoutMs: 30000,
    });
  }

  queueCancel(id) {
    return this.request(`/api/queue/${encodeURIComponent(id)}/cancel`, {
      method: 'POST',
      body: {},
      timeoutMs: 30000,
    });
  }

  gatewayAction(action, kind) {
    const body = kind ? { kind } : {};
    return this.request(`/api/gateway/${action}`, {
      method: 'POST',
      body,
      timeoutMs: 180000,
    });
  }

  exchangeTicket(target) {
    return this.request('/api/auth/exchange', {
      method: 'POST',
      body: { target: target || '/#/actions' },
      timeoutMs: 6000,
    });
  }
}

function config() {
  return vscode.workspace.getConfiguration(SECTION);
}

function t(message, ...args) {
  return vscode.l10n.t(message, ...args);
}

function looksLikeControlRoot(dir) {
  if (!dir) return false;
  return (
    fs.existsSync(path.join(dir, 'bin', 'gpu-control')) ||
    fs.existsSync(path.join(dir, 'bin', 'gpu-control-mcp'))
  );
}

function findControlRoot() {
  const configured = String(config().get('controlRoot', '') || '').trim();
  if (configured) {
    const abs = path.isAbsolute(configured)
      ? configured
      : path.join(vscode.workspace.workspaceFolders?.[0]?.uri.fsPath || '', configured);
    if (looksLikeControlRoot(abs)) return abs;
  }
  for (const folder of vscode.workspace.workspaceFolders || []) {
    if (looksLikeControlRoot(folder.uri.fsPath)) return folder.uri.fsPath;
  }
  const home = String(process.env.HOME || process.env.USERPROFILE || '').trim();
  if (home) {
    const guess = path.join(home, 'gpu-control');
    if (looksLikeControlRoot(guess)) return guess;
  }
  return '';
}

function localTokenPath() {
  const root = findControlRoot();
  return root ? path.join(root, 'config', 'local', 'secrets', 'ui_api_token') : '';
}

function readLocalApiToken() {
  if (!config().get('autoLocalAuth', true)) return '';
  const tokenPath = localTokenPath();
  if (!tokenPath || !fs.existsSync(tokenPath)) return '';
  const raw = fs.readFileSync(tokenPath, 'utf8').trim();
  if (!raw) return '';
  const line = raw.split(/\r?\n/).find((x) => x.trim() && !x.trim().startsWith('#')) || '';
  const matched = line.match(/^(?:token=)?(.+)$/i);
  return String(matched ? matched[1] : line).trim();
}

function stateLabel(node) {
  if (!node) return t('未知');
  if (node.liveness_state === 'offline' || node.state === 'offline') return t('离线');
  if (node.liveness_state === 'degraded' || node.state === 'degraded') return t('探活异常');
  if (node.snapshot_stale) return t('快照过期');
  if (node.cordoned) return t('已隔离');
  if (node.used) return t('占用中');
  return t('空闲');
}

function isNodeOnline(node) {
  if (!node) return false;
  if (node.liveness_state === 'offline' || node.state === 'offline') return false;
  if (node.liveness_state === 'degraded' || node.state === 'degraded') return false;
  return true;
}

function nodeIcon(node) {
  if (!isNodeOnline(node)) {
    return new vscode.ThemeIcon('error', new vscode.ThemeColor('errorForeground'));
  }
  if (node.cordoned) {
    return new vscode.ThemeIcon('lock', new vscode.ThemeColor('charts.orange'));
  }
  if (node.snapshot_stale) {
    return new vscode.ThemeIcon('warning', new vscode.ThemeColor('editorWarning.foreground'));
  }
  if (node.used) {
    return new vscode.ThemeIcon('vm-active', new vscode.ThemeColor('charts.blue'));
  }
  return new vscode.ThemeIcon('server', new vscode.ThemeColor('charts.green'));
}

function severityIcon(severity) {
  const level = String(severity || '').toLowerCase();
  if (level === 'error' || level === 'critical') {
    return new vscode.ThemeIcon('error', new vscode.ThemeColor('errorForeground'));
  }
  if (level === 'warn' || level === 'warning') {
    return new vscode.ThemeIcon('warning', new vscode.ThemeColor('editorWarning.foreground'));
  }
  return new vscode.ThemeIcon('info');
}

function setDisconnectedContext(disconnected) {
  vscode.commands.executeCommand('setContext', 'computedeck.disconnected', !!disconnected);
}

function makeItem(label, collapsibleState, extra = {}) {
  const item = new vscode.TreeItem(label, collapsibleState);
  Object.assign(item, extra);
  return item;
}

// ── 侧栏树 ─────────────────────────────────────────────────
class ClusterProvider {
  constructor(deckClient) {
    this.client = deckClient;
    this.emitter = new vscode.EventEmitter();
    this.onDidChangeTreeData = this.emitter.event;
    this.data = null;
    this.lastError = null;
  }

  async refresh(showErrors = false) {
    try {
      const [overview, live, jobs, gateway, assistant, notices, auth] = await Promise.all([
        this.client.overview(true),
        this.client.liveness().catch(() => null),
        this.client.jobs().catch(() => []),
        this.client.gatewayStatus().catch(() => null),
        this.client.assistantStatus().catch(() => null),
        this.client.notices().catch(() => null),
        this.client.authStatus().catch(() => null),
      ]);
      this.data = { overview, live, jobs, gateway, assistant, notices, auth };
      this.lastError = null;
      setDisconnectedContext(false);
    } catch (err) {
      this.lastError = err;
      setDisconnectedContext(true);
      if (showErrors) {
        vscode.window.showErrorMessage(t('ComputeDeck: {0}', err.message || err));
      }
    }
    this.emitter.fire();
    updateStatusBar(this.data, this.lastError);
    refreshWorkbench();
    refreshAccessHub();
    return this.data;
  }

  getTreeItem(element) {
    return element;
  }

  getChildren(element) {
    if (element && element.children) return element.children;
    if (element) return [];
    return this.sections();
  }

  sections() {
    if (this.lastError) {
      const errText = String(this.lastError.message || this.lastError);
      const localToken = localTokenPath();
      const children = [
        makeItem(t('还没连上'), vscode.TreeItemCollapsibleState.None, {
          description: this.client.base(),
          tooltip: errText,
          iconPath: new vscode.ThemeIcon('error', new vscode.ThemeColor('errorForeground')),
          command: { command: 'computedeck.openAccess', title: t('怎么用') },
        }),
      ];
      if (localToken && fs.existsSync(localToken)) {
        children.push(
          makeItem(t('使用本机密钥'), vscode.TreeItemCollapsibleState.None, {
            description: t('主控上一般点这个就能连'),
            iconPath: new vscode.ThemeIcon('key'),
            command: { command: 'computedeck.useLocalCredentials', title: t('使用本机密钥') },
          })
        );
      }
      children.push(
        makeItem(t('怎么用'), vscode.TreeItemCollapsibleState.None, {
          description: t('连接、登录和看板说明'),
          iconPath: new vscode.ThemeIcon('question'),
          command: { command: 'computedeck.openAccess', title: t('怎么用') },
        }),
        makeItem(t('五分钟上手'), vscode.TreeItemCollapsibleState.None, {
          description: t('分步引导'),
          iconPath: new vscode.ThemeIcon('book'),
          command: { command: 'computedeck.openWalkthrough', title: t('五分钟上手') },
        })
      );
      return [
        makeItem(t('连接'), vscode.TreeItemCollapsibleState.Expanded, {
          iconPath: new vscode.ThemeIcon('plug'),
          children,
        }),
      ];
    }
    if (!this.data) {
      return [
        makeItem(t('加载中…'), vscode.TreeItemCollapsibleState.None, {
          iconPath: new vscode.ThemeIcon('sync~spin'),
        }),
      ];
    }

    const { overview, live, jobs, gateway } = this.data;
    const summary = overview.summary || {};
    const nodes = overview.nodes || [];
    const leases = overview.leases || [];
    const queue = overview.queue || [];
    const layering = (gateway && gateway.layering) || {};
    const sections = [];

    const qualityScore = overview.quality ? Number(overview.quality.score) : NaN;
    const summaryRows = [
      [
        t('健康评分'),
        overview.quality ? `${overview.quality.score ?? '-'} · ${overview.quality.grade || ''}` : '-',
        Number.isFinite(qualityScore) && qualityScore < 70 ? 'warning' : 'heart',
      ],
      [t('节点'), t('{0} 在线 / {1} 离线', summary.nodes_online ?? '-', summary.nodes_offline ?? 0), summary.nodes_offline ? 'warning' : 'server'],
      [t('GPU'), t('{0} 空闲 / {1} 占用 / {2} 计入', summary.gpus_free ?? 0, summary.gpus_used ?? 0, summary.gpus_total ?? 0), 'circuit-board'],
      [t('排队'), String(summary.queue_pending ?? 0), 'list-ordered'],
      [t('占用中'), String(summary.leases ?? leases.length), 'lock'],
      [t('入口'), layering.summary || (gateway && gateway.preferred) || '-', 'globe'],
    ].map(([label, value, icon]) =>
      makeItem(`${label}`, vscode.TreeItemCollapsibleState.None, {
        description: String(value),
        iconPath: new vscode.ThemeIcon(icon),
      })
    );
    sections.push(
      makeItem(t('集群总览'), vscode.TreeItemCollapsibleState.Collapsed, {
        description: t('{0} 节点 · {1} 空闲 GPU', summary.nodes ?? nodes.length, summary.gpus_free ?? 0),
        iconPath: new vscode.ThemeIcon('dashboard'),
        children: summaryRows,
      })
    );

    const nodeRows = nodes.map((node) => {
      const state = stateLabel(node);
      const tooltip = new vscode.MarkdownString();
      tooltip.appendMarkdown(`**${node.name}**  \n`);
      tooltip.appendMarkdown(`${t('主机：{0}', node.host || '-')}  \n`);
      tooltip.appendMarkdown(`${t('状态：{0}', state)}  \n`);
      if (node.counted) {
        tooltip.appendMarkdown(`${t('GPU：{0} 空闲 / {1} 占用', node.free, node.used)}${t(' / 共 {0}', node.total ?? '-')}  \n`);
      } else {
        tooltip.appendMarkdown(`${t('GPU：不计入（离线或快照过期） / 共 {0}', node.total ?? '-')}  \n`);
      }
      if (node.last_seen) tooltip.appendMarkdown(`${t('最后成功探活：{0}', node.last_seen)}  \n`);
      if (node.liveness_error) tooltip.appendMarkdown(t('错误：{0}', node.liveness_error));
      return makeItem(node.name, vscode.TreeItemCollapsibleState.None, {
        description: `${state} · ${node.counted ? `${node.free}/${node.total}` : '—'}`,
        iconPath: nodeIcon(node),
        contextValue: `node-${node.state || 'unknown'}`,
        tooltip,
        command: { command: 'computedeck.nodeMenu', title: t('节点操作'), arguments: [node] },
      });
    });
    sections.push(
      makeItem(t('节点'), vscode.TreeItemCollapsibleState.Collapsed, {
        description: t('{0} 台 · {1} 离线', nodes.length, summary.nodes_offline ?? 0),
        iconPath: new vscode.ThemeIcon('server'),
        children: nodeRows.length ? nodeRows : [makeItem(t('暂无节点'), vscode.TreeItemCollapsibleState.None)],
      })
    );

    const jobRows = (jobs || []).map((job) =>
      makeItem(job.name || 'job', vscode.TreeItemCollapsibleState.None, {
        description: job.default
          ? t('{0} GPU · 默认', job.gpus_per_node ?? '?')
          : t('{0} GPU', job.gpus_per_node ?? '?'),
        iconPath: new vscode.ThemeIcon('rocket'),
        contextValue: 'job',
        tooltip: job.description || '',
        command: { command: 'computedeck.jobMenu', title: t('任务操作'), arguments: [job] },
      })
    );
    sections.push(
      makeItem(t('任务'), vscode.TreeItemCollapsibleState.Collapsed, {
        description: t('{0} 个', jobRows.length),
        iconPath: new vscode.ThemeIcon('rocket'),
        children: jobRows.length ? jobRows : [makeItem(t('暂无任务'), vscode.TreeItemCollapsibleState.None)],
      })
    );

    const queueRows = [
      makeItem(t('等待调度'), vscode.TreeItemCollapsibleState.None, {
        description: String(queue.filter((x) => (x.status || '') === 'pending').length),
        iconPath: new vscode.ThemeIcon('list-ordered'),
      }),
      ...(leases || []).map((lease) =>
        makeItem(lease.job || lease.id || 'lease', vscode.TreeItemCollapsibleState.None, {
          description: (lease.nodes || []).join(', '),
          iconPath: new vscode.ThemeIcon('lock'),
          tooltip: `gpus=${lease.gpus ?? '-'} owner=${lease.owner || '-'} id=${lease.id || '-'}`,
        })
      ),
    ];
    sections.push(
      makeItem(t('排队'), vscode.TreeItemCollapsibleState.Collapsed, {
        description: t('{0} 等待 · {1} 占用中', summary.queue_pending ?? 0, leases.length),
        iconPath: new vscode.ThemeIcon('list-ordered'),
        children: queueRows,
      })
    );

    const gatewayRows = [];
    const edge = (gateway && gateway.edge) || {};
    const ll = (gateway && gateway.litellm) || {};
    const nginx = (gateway && gateway.nginx) || {};
    const edgeFront = Boolean(gateway?.layering?.edge_front);
    gatewayRows.push(
      makeItem(`Edge :${gateway?.ingress?.port || 18080}`, vscode.TreeItemCollapsibleState.None, {
        description: edge.running
          ? edgeFront
            ? t('前置反代 → LiteLLM')
            : t('节点 LB')
          : t('未运行'),
        iconPath: new vscode.ThemeIcon(edge.running ? 'globe' : 'circle-slash'),
        tooltip: gateway?.layering?.edge_url || '',
      }),
      makeItem(`LiteLLM :${ll.port || 4000}`, vscode.TreeItemCollapsibleState.None, {
        description:
          ll.upstream === 'edge'
            ? t('经 Edge 治理')
            : ll.running
              ? t('唯一节点 LB')
              : t('未运行'),
        iconPath: new vscode.ThemeIcon(ll.running ? 'symbol-method' : 'circle-slash'),
        tooltip: gateway?.layering?.summary || '',
      }),
      makeItem('nginx', vscode.TreeItemCollapsibleState.None, {
        description: nginx.installed ? t('已安装 · Web/TLS 前置') : t('未安装'),
        iconPath: new vscode.ThemeIcon('browser'),
      })
    );
    if (gateway?.layering?.duplicate_node_lb) {
      gatewayRows.unshift(
        makeItem(t('检测到重复节点 LB'), vscode.TreeItemCollapsibleState.None, {
          description: t('让 Edge 做前置反代'),
          iconPath: new vscode.ThemeIcon('warning', new vscode.ThemeColor('warningForeground')),
          tooltip: gateway.layering.recommendation || '',
        })
      );
    }
    sections.push(
      makeItem(t('入口'), vscode.TreeItemCollapsibleState.Collapsed, {
        description: layering.summary || '',
        iconPath: new vscode.ThemeIcon('globe'),
        children: gatewayRows,
      })
    );

    const notices = this.data.notices || {};
    const unread = Number(notices.unread || 0);
    const assistant = this.data.assistant || {};

    // Sidebar stays lean: rich AI / notices / actions live in the workbench panel.
    sections.unshift(
      makeItem(t('打开工作台'), vscode.TreeItemCollapsibleState.None, {
        description:
          unread > 0
            ? t('{0} 未读公告', unread)
            : t('{0} 节点 · {1} 空闲 GPU', summary.nodes ?? nodes.length, summary.gpus_free ?? 0),
        iconPath: new vscode.ThemeIcon('window'),
        command: { command: 'computedeck.openWorkbench', title: t('打开工作台') },
      }),
      makeItem(t('打开小助手'), vscode.TreeItemCollapsibleState.None, {
        description: assistant.ready
          ? t('可用 · {0}', assistant.model || 'llm')
          : assistant.enabled
            ? t('已启用，待配置 API')
            : t('未启用 · 点此配置'),
        iconPath: new vscode.ThemeIcon(assistant.ready ? 'sparkle' : 'tools'),
        command: {
          command: assistant.ready ? 'computedeck.openAssistant' : 'computedeck.configureAssistant',
          title: assistant.ready ? t('打开小助手') : t('配置小助手 API'),
        },
      })
    );
    return sections;
  }
}

function updateStatusBar(data, error) {
  if (!config().get('statusBar', true)) {
    statusBar.hide();
    return;
  }
  statusBar.show();
  if (error || !data) {
    statusBar.text = t('$(error) ComputeDeck 未连接');
    statusBar.tooltip = String((error && error.message) || t('点击打开「怎么用」'));
    statusBar.backgroundColor = new vscode.ThemeColor('statusBarItem.errorBackground');
    statusBar.command = 'computedeck.openAccess';
    return;
  }
  const summary = data.overview?.summary || {};
  const online = summary.nodes_online ?? 0;
  const total = summary.nodes ?? 0;
  const offline = summary.nodes_offline ?? 0;
  const free = summary.gpus_free ?? 0;
  const gpuTotal = summary.gpus_total ?? 0;
  const unread = Number(data.notices?.unread || 0);
  const assistantReady = Boolean(data.assistant?.ready);
  const warn = offline > 0 || unread > 0;
  statusBar.text = `${warn ? '$(warning)' : '$(pulse)'} ${t('{0}/{1} 节点 · {2}/{3} 空闲', online, total, free, gpuTotal)}${unread > 0 ? t(' · {0} 公告', unread) : ''
    }`;
  statusBar.tooltip = new vscode.MarkdownString(
    [
      t('**ComputeDeck**'),
      '',
      t('在线 {0}/{1}，离线 {2}，空闲 GPU {3}/{4}', online, total, offline, free, gpuTotal),
      unread > 0 ? t('未读公告：{0}', unread) : t('公告：无未读'),
      assistantReady
        ? t('小助手：可用 · {0}', data.assistant?.model || 'llm')
        : t('小助手：未就绪（可配置 API）'),
      '',
      t('点击打开工作台'),
    ].join('\n')
  );
  statusBar.backgroundColor = warn
    ? new vscode.ThemeColor('statusBarItem.warningBackground')
    : undefined;
  statusBar.command = 'computedeck.openWorkbench';
}

// ── 看板链接与一次性登录票据 ─────────────────────────────────
const CONTROL_PAGES = [
  { id: 'actions', label: '看板', hash: '/#/actions' },
  { id: 'cluster', label: '节点', hash: '/#/cluster' },
  { id: 'workloads', label: '任务', hash: '/#/workloads' },
  { id: 'endpoints', label: '入口', hash: '/#/endpoints' },
  { id: 'models', label: '模型', hash: '/#/models' },
  { id: 'toolkits', label: '工具栈', hash: '/#/toolkits' },
  { id: 'ops', label: '批量命令', hash: '/#/ops' },
  { id: 'terminal', label: '交互终端', hash: '/#/terminal' },
  { id: 'config', label: '配置', hash: '/#/config' },
  { id: 'assistant', label: '小助手', hash: '/#/assistant' },
  { id: 'notices', label: '公告', hash: '/#/notices' },
];

function controlUiUrl(hash = '') {
  const base = client.base();
  if (!hash) return `${base}/#/actions`;
  if (hash.startsWith('http://') || hash.startsWith('https://')) return hash;
  if (hash.startsWith('/#')) return `${base}${hash}`;
  if (hash.startsWith('#')) return `${base}/${hash}`;
  if (hash.startsWith('/')) return `${base}${hash}`;
  return `${base}/#/${String(hash).replace(/^\/+/, '')}`;
}

async function resolveUiUrl(hash = '') {
  const local = controlUiUrl(hash);
  try {
    const external = await vscode.env.asExternalUri(vscode.Uri.parse(local));
    return external.toString(true);
  } catch (_) {
    return local;
  }
}

function buildConsumeUrl(baseUrl, code, next) {
  try {
    const url = new URL(baseUrl);
    url.hash = '';
    url.search = '';
    url.pathname = `${url.pathname.replace(/\/+$/, '')}/auth/consume`;
    const params = new URLSearchParams({ code: String(code || '') });
    params.set('next', next || '/#/actions');
    url.search = params.toString();
    return url.toString();
  } catch (_) {
    return baseUrl;
  }
}

/**
 * 浏览器打开看板前，先用插件手里的 Bearer Token 换一次性票据。
 * 换不到（未登录 / 鉴权关闭 / 旧控制面）就退回原地址，至少还能显示登录页。
 */
async function dashboardUrlWithTicket(browserUrl, hash) {
  const targetUrl = browserUrl || controlUiUrl(hash);
  try {
    const data = await client.exchangeTicket(hash || '/#/actions');
    if (!data || data.auth_disabled || !data.code) return targetUrl;
    return buildConsumeUrl(targetUrl, data.code, hash || '/#/actions');
  } catch (err) {
    if (output) output.appendLine(t('打开看板：未换取登录票据（{0}）', (err && err.message) || err));
    return targetUrl;
  }
}

function pageTitleForHash(hash = '') {
  const id = String(hash || '/#/actions')
    .replace(/^\/#\/?/, '')
    .replace(/^#\/?/, '')
    .split('/')[0] || 'actions';
  const page = CONTROL_PAGES.find((p) => p.id === id);
  return page ? t('ComputeDeck · {0}', page.label) : t('ComputeDeck 接入');
}

function isWebIde() {
  return vscode.env.uiKind === vscode.UIKind.Web;
}

function accessProfile() {
  const data = provider?.data || null;
  const auth = data?.auth || {};
  const assistant = data?.assistant || {};
  const controlRoot = findControlRoot();
  const localToken = Boolean(readLocalApiToken());
  const envToken = Boolean(String(process.env.GPU_CONTROL_API_TOKEN || '').trim());
  const apiOk = Boolean(data) && !provider?.lastError;
  const mode = String(auth.mode || (apiOk ? 'unknown' : '—'));
  return {
    apiOk,
    apiBase: client.base(),
    controlRoot: controlRoot || '',
    uiKind: isWebIde() ? 'web' : 'desktop',
    authMode: mode,
    authEnabled: Boolean(auth.enabled),
    passwordConfigured: Boolean(auth.password_configured),
    apiTokenConfigured: Boolean(auth.api_token_configured),
    sessionAuthenticated: Boolean(auth.authenticated),
    oidcReady: Boolean(auth.oidc_ready || auth.oidc?.ready),
    localToken,
    envToken,
    passwordlessPlugin: localToken || envToken,
    passwordlessWorkers: true,
    assistantReady: Boolean(assistant.ready),
    assistantEnabled: Boolean(assistant.enabled),
    assistantModel: assistant.model || '',
    assistantBaseUrl: assistant.base_url || '',
  };
}

function buildAccessHubHtml(webview, state) {
  const nonce = `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
  const csp = [
    `default-src 'none'`,
    `style-src ${webview.cspSource} 'unsafe-inline'`,
    `script-src 'nonce-${nonce}'`,
    state.tryEmbed && state.browserUrl
      ? `frame-src ${(() => {
          try {
            return new URL(state.browserUrl).origin;
          } catch (_) {
            return 'http: https:';
          }
        })()} http: https:`
      : '',
  ]
    .filter(Boolean)
    .join('; ');
  const safeEmbed = state.tryEmbed && state.browserUrl
    ? String(state.browserUrl).replace(/&/g, '&amp;').replace(/"/g, '&quot;')
    : '';
  return `<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8" />
  <meta http-equiv="Content-Security-Policy" content="${csp}" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>怎么用 ComputeDeck</title>
  <style>
    :root {
      color-scheme: light dark;
      --line: var(--vscode-widget-border, rgba(127,127,127,.28));
      --soft: color-mix(in srgb, var(--vscode-editor-background) 92%, var(--vscode-foreground) 5%);
      --accent: #0f766e;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0; padding: 22px 24px 36px;
      font: 14px/1.55 var(--vscode-font-family);
      color: var(--vscode-foreground);
      background:
        radial-gradient(900px 360px at 100% -20%, color-mix(in srgb, var(--accent) 12%, transparent), transparent 55%),
        var(--vscode-editor-background);
    }
    .shell { max-width: 720px; margin: 0 auto; display: grid; gap: 14px; }
    h1 { margin: 0; font-size: 22px; font-weight: 700; letter-spacing: -.02em; }
    .sub { margin: 8px 0 0; opacity: .72; font-size: 13px; }
    .pills { display: flex; flex-wrap: wrap; gap: 6px; }
    .pill {
      display: inline-flex; align-items: center; gap: 6px;
      border: 1px solid var(--line); border-radius: 999px; padding: 3px 10px;
      background: var(--soft); font-size: 12px;
    }
    .dot { width: 7px; height: 7px; border-radius: 50%; background: #94a3b8; }
    .dot.on { background: #16a34a; } .dot.off { background: #dc2626; }
    .actions { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 10px; }
    .action-label { margin: 16px 0 -2px; font-size: 12px; opacity: .6; }
    button {
      appearance: none; font: inherit; cursor: pointer; border-radius: 8px;
      border: 1px solid var(--line); padding: 8px 14px;
      background: var(--vscode-button-secondaryBackground);
      color: var(--vscode-button-secondaryForeground);
    }
    button.primary {
      border-color: transparent; font-weight: 600;
      background: var(--vscode-button-background, var(--accent));
      color: var(--vscode-button-foreground, #f0fdfa);
    }
    .card {
      border: 1px solid var(--line); border-radius: 12px; background: var(--soft);
      padding: 16px 16px 14px;
    }
    .card h2 { margin: 0 0 8px; font-size: 15px; font-weight: 700; }
    .card ol, .card ul { margin: 0; padding-left: 1.2em; display: grid; gap: 6px; }
    .card li { margin: 0; }
    .muted { opacity: .65; font-size: 12px; margin-top: 10px; }
    .embed-wrap {
      border: 1px solid var(--line); border-radius: 12px; overflow: hidden; background: #fff;
    }
    .embed-wrap iframe { border: 0; width: 100%; height: 70vh; display: block; }
  </style>
</head>
<body>
  <div class="shell">
    <header>
      <div class="pills">
        <div class="pill"><span class="dot ${state.apiOk ? 'on' : 'off'}"></span>${state.apiOk ? '已经连上，可以直接用' : '还没连上，先看下面第 1 步'}</div>
        <div class="pill">${state.passwordlessPlugin ? '本机插件 · 免密' : '本机插件 · 需要登录一次'}</div>
      </div>
      <h1 style="margin-top:12px">怎么用 ComputeDeck</h1>
      <p class="sub">日常主要用到三处：左侧看机器，完整看板看图表和助手，偶尔登录一次。</p>
      <div class="actions" id="topActions"></div>
      <p class="action-label">连接</p>
      <div class="actions" id="authActions"></div>
    </header>

    <section class="card">
      <h2>常用操作</h2>
      <ol>
        <li>点左侧的 ComputeDeck，或打开工作台，查看节点和任务。</li>
        <li>需要图表和助手时，点「打开完整看板」，会在编辑器里打开网页。</li>
        <li>看板打不开时，点「用浏览器打开」。左侧还能刷新集群，说明主控本身是好的。</li>
      </ol>
    </section>

    <section class="card">
      <h2>主控、工作节点和个人电脑</h2>
      <p>你多半在主控机上。这台机器负责调度、网页和插件，用插件时通常不需要输密码。</p>
      <p>GPU 工作节点只负责跑任务，由主控通过 SSH 连接，不需要单独登录它们的网页。</p>
      <p>如果人不在主控机旁边，先用远程桌面、CodeLab 或 SSH 连过去，再按上面的步骤操作。</p>
    </section>

    <section class="card">
      <h2>什么时候需要登录</h2>
      <ul>
        <li><strong>插件侧栏和工作台</strong>：在主控机上打开仓库时通常会自动读取本机 Token。${state.passwordlessPlugin ? '你现在就是这种情况。' : '如果提示未连接，点「使用本机密钥」或「密码登录」。'}</li>
        <li><strong>完整看板</strong>：从插件点开时，会自动换一张一次性登录票据，不用再输密码；直接在浏览器输入网址仍然需要密码或 OIDC。</li>
        <li><strong>其他设备</strong>：笔记本、内网或公网直接访问网页，需要密码 / 企业登录；票据只在装了插件的主控本机上可用。</li>
        <li><strong>GPU 工作节点</strong>：不需要输入网页密码，只走 SSH。</li>
      </ul>
      <p class="muted">当前状态：${state.apiOk ? '插件已连上' : '插件未连上'} · ${state.passwordlessPlugin ? '免密可用' : '可能需要登录一次'}</p>
    </section>

    <section class="card">
      <h2>小助手和 AI 工具</h2>
      <p>网页小助手在看板里聊天，能查看集群状态并给出建议。使用前要先配置模型地址和密钥。</p>
      <p>注册「给 AI 用的工具」后，编辑器里的 Copilot 等可以直接调用集群命令。</p>
      <p class="muted">当前：${state.assistantReady ? `小助手可用（${state.assistantModel || '已配置'}）` : '小助手还没配置 API，可以点下面的按钮设置。'}</p>
      <div class="actions" id="aiActions" style="margin-top:12px"></div>
    </section>

    ${
      state.tryEmbed && safeEmbed
        ? `<section class="embed-wrap"><iframe src="${safeEmbed}" title="ComputeDeck"></iframe></section>`
        : ''
    }
  </div>
  <script nonce="${nonce}">
    const vscode = acquireVsCodeApi();
    function btn(label, command, primary, parent) {
      const b = document.createElement('button');
      b.textContent = label;
      if (primary) b.className = 'primary';
      b.addEventListener('click', () => vscode.postMessage({ command }));
      (parent || document.getElementById('topActions')).append(b);
    }
    btn('打开完整看板', 'simpleBrowser', true);
    btn('打开工作台', 'openWorkbench');
    btn('五分钟上手', 'openWalkthrough');
    const auth = document.getElementById('authActions');
    btn('刷新', 'refresh', false, auth);
    btn('使用本机密钥', 'useLocalCredentials', false, auth);
    btn('密码登录', 'login', false, auth);
    btn('用浏览器打开', 'openExternal', false, auth);
    btn('复制看板地址', 'copyUrl', false, auth);
    const ai = document.getElementById('aiActions');
    ${state.assistantReady
      ? "btn('打开小助手', 'openAssistant', true, ai); btn('改配置', 'configureAssistant', false, ai);"
      : "btn('配置小助手 API', 'configureAssistant', true, ai); btn('打开小助手', 'openAssistant', false, ai);"}
    btn('注册给 AI 用的工具', 'installMcp', false, ai);
  </script>
</body>
</html>`;
}

function accessHubSignature(state) {
  return JSON.stringify({
    apiOk: !!state.apiOk,
    passwordless: !!state.passwordlessPlugin,
    assistantReady: !!state.assistantReady,
    assistantModel: state.assistantModel || '',
    localUrl: state.localUrl || '',
    browserUrl: state.browserUrl || '',
    tryEmbed: !!state.tryEmbed,
  });
}

function showAccessHub(opts = {}) {
  const profile = accessProfile();
  const state = {
    ...profile,
    title: opts.title || t('怎么用 ComputeDeck'),
    hash: opts.hash || '/#/actions',
    localUrl: opts.localUrl || controlUiUrl(opts.hash || '/#/actions'),
    browserUrl: opts.browserUrl || '',
    tryEmbed: Boolean(opts.tryEmbed),
  };
  // 接入页标题固定为「怎么用」，避免「看板」标签里实际是说明页。
  const panelTitle = t('怎么用 ComputeDeck');
  if (controlUiPanel) {
    controlUiPanel.title = panelTitle;
    controlUiPanel.reveal(vscode.ViewColumn.Beside, false);
    controlUiPanel.webview.html = buildAccessHubHtml(controlUiPanel.webview, state);
    controlUiPanel._cdAccess = state;
    controlUiPanel._cdSig = accessHubSignature(state);
    return;
  }
  controlUiPanel = vscode.window.createWebviewPanel(
    'computedeck.controlUi',
    panelTitle,
    vscode.ViewColumn.Beside,
    { enableScripts: true, retainContextWhenHidden: true }
  );
  controlUiPanel._cdAccess = state;
  controlUiPanel._cdSig = accessHubSignature(state);
  controlUiPanel.webview.html = buildAccessHubHtml(controlUiPanel.webview, state);
  controlUiPanel.onDidDispose(() => {
    controlUiPanel = undefined;
  });
  controlUiPanel.webview.onDidReceiveMessage(async (msg) => {
    const cur = controlUiPanel?._cdAccess || state;
    if (!msg || !msg.command) return;
    if (msg.command === 'simpleBrowser') {
      const targetUrl = await dashboardUrlWithTicket(cur.browserUrl || cur.localUrl, cur.hash);
      try {
        await vscode.commands.executeCommand('simpleBrowser.show', targetUrl);
      } catch (_) {
        await vscode.env.openExternal(vscode.Uri.parse(targetUrl));
      }
      return;
    }
    if (msg.command === 'openExternal') {
      const targetUrl = await dashboardUrlWithTicket(cur.browserUrl || cur.localUrl, cur.hash);
      await vscode.env.openExternal(vscode.Uri.parse(targetUrl));
      return;
    }
    if (msg.command === 'copyUrl') {
      await vscode.env.clipboard.writeText(cur.browserUrl || cur.localUrl || '');
      vscode.window.showInformationMessage(t('已复制看板地址'));
      return;
    }
    if (msg.command === 'embed') {
      showAccessHub({ ...cur, tryEmbed: true, title: cur.title });
      return;
    }
    if (msg.command === 'openWorkbench') {
      showWorkbench();
      return;
    }
    if (msg.command === 'openWalkthrough') {
      await openGettingStarted();
      return;
    }
    if (msg.command === 'useLocalCredentials') {
      await vscode.commands.executeCommand('computedeck.useLocalCredentials');
      return;
    }
    if (msg.command === 'login') {
      await vscode.commands.executeCommand('computedeck.login');
      return;
    }
    if (msg.command === 'openAssistant') {
      await vscode.commands.executeCommand('computedeck.openAssistant');
      return;
    }
    if (msg.command === 'configureAssistant') {
      await vscode.commands.executeCommand('computedeck.configureAssistant');
      return;
    }
    if (msg.command === 'installMcp') {
      await vscode.commands.executeCommand('computedeck.installMcp');
      return;
    }
    if (msg.command === 'refresh') {
      await provider.refresh(true);
      const browserUrl = await resolveUiUrl(cur.hash || '/#/actions');
      showAccessHub({
        ...cur,
        ...accessProfile(),
        browserUrl,
        localUrl: controlUiUrl(cur.hash || '/#/actions'),
      });
    }
  });
}

async function trySimpleBrowser(url) {
  try {
    await vscode.commands.executeCommand('simpleBrowser.show', url);
    return true;
  } catch (_) {
    return false;
  }
}

async function openControlUi(hash = '', title) {
  const localUrl = controlUiUrl(hash);
  const browserUrl = await resolveUiUrl(hash);
  const mode = String(config().get('uiOpenMode', 'auto') || 'auto');
  const panelTitle = title || pageTitleForHash(hash);

  if (mode === 'external') {
    const targetUrl = await dashboardUrlWithTicket(browserUrl, hash);
    await vscode.env.openExternal(vscode.Uri.parse(targetUrl));
    return 'external';
  }

  if (mode === 'simpleBrowser') {
    const targetUrl = await dashboardUrlWithTicket(browserUrl, hash);
    if (await trySimpleBrowser(targetUrl)) return 'simpleBrowser';
    showAccessHub({
      hash,
      title: t('怎么用 ComputeDeck'),
      localUrl,
      browserUrl,
      tryEmbed: false,
    });
    return 'access';
  }

  if (mode === 'embed') {
    const targetUrl = await dashboardUrlWithTicket(browserUrl, hash);
    showAccessHub({ hash, title: panelTitle, localUrl, browserUrl: targetUrl, tryEmbed: true });
    return 'embed';
  }

  if (mode === 'access') {
    showAccessHub({
      hash,
      title: t('怎么用 ComputeDeck'),
      localUrl,
      browserUrl,
      tryEmbed: false,
    });
    return 'access';
  }

  // auto：先开真正的网页看板；失败再给「怎么用」（标题不再冒充看板）。
  const targetUrl = await dashboardUrlWithTicket(browserUrl, hash);
  if (await trySimpleBrowser(targetUrl)) return 'simpleBrowser';
  showAccessHub({
    hash,
    title: t('怎么用 ComputeDeck'),
    localUrl,
    browserUrl,
    tryEmbed: false,
  });
  return 'access';
}

async function openControlUiExternal(hash = '') {
  const browserUrl = await resolveUiUrl(hash);
  await vscode.env.openExternal(vscode.Uri.parse(await dashboardUrlWithTicket(browserUrl, hash)));
}

async function pickControlPage() {
  const picked = await vscode.window.showQuickPick(
    CONTROL_PAGES.map((p) => ({ label: p.label, description: p.hash, page: p })),
    { placeHolder: t('打开看板里的哪一页？') }
  );
  if (!picked) return;
  await openControlUi(picked.page.hash, t('ComputeDeck · {0}', picked.page.label));
}

// ── 五分钟上手内容 ─────────────────────────────────────────
const GUIDE_STEPS = [
  {
    id: 'connect',
    title: '先连上',
    file: '01-connect.md',
    actions: [
      { label: '使用本机密钥', command: 'computedeck.useLocalCredentials' },
      { label: '密码登录', command: 'computedeck.login' },
      { label: '怎么用', command: 'computedeck.openAccess' },
      { label: '刷新', command: 'computedeck.refresh' },
    ],
  },
  {
    id: 'workbench',
    title: '看集群和看板',
    file: '02-cluster.md',
    actions: [
      { label: '打开工作台', command: 'computedeck.openWorkbench' },
      { label: '打开完整看板', command: 'computedeck.openDashboard' },
      { label: '怎么用', command: 'computedeck.openAccess' },
    ],
  },
  {
    id: 'nodes',
    title: '节点操作',
    file: '03-nodes.md',
    actions: [
      { label: '检查节点是否在线', command: 'computedeck.liveness' },
      { label: '节点操作', command: 'computedeck.nodeMenu' },
      { label: '测试能否 SSH', command: 'computedeck.testSSH' },
    ],
  },
  {
    id: 'jobs',
    title: '部署任务',
    file: '04-jobs.md',
    actions: [
      { label: '部署任务', command: 'computedeck.deployJob' },
      { label: '运行自检', command: 'computedeck.runDoctor' },
    ],
  },
  {
    id: 'mcp',
    title: '助手与 AI',
    file: '05-mcp.md',
    actions: [
      { label: '打开小助手', command: 'computedeck.openAssistant' },
      { label: '配置小助手 API', command: 'computedeck.configureAssistant' },
      { label: '注册给 AI 用的工具', command: 'computedeck.installMcp' },
    ],
  },
];

async function configureAssistant() {
  let status = {};
  try {
    status = await client.assistantStatus();
  } catch (err) {
    vscode.window.showErrorMessage(t('读不到小助手状态：{0}', err.message || err));
    return;
  }

  const start = await vscode.window.showQuickPick(
    [
      {
        label: status.ready ? t('打开小助手聊天') : t('开始配置 API'),
        description: status.ready
          ? t('当前模型 {0}', status.model || '')
          : t('填写地址 / 密钥 / 模型'),
        action: status.ready ? 'open' : 'setup',
      },
      {
        label: t('配置或修改 API'),
        description: status.base_url || t('还没填地址'),
        action: 'setup',
      },
      {
        label: t('只打开看板里的设置页'),
        description: t('图表旁边的完整配置界面'),
        action: 'web',
      },
    ],
    { placeHolder: t('小助手：聊天还是先配 API？') }
  );
  if (!start) return;
  if (start.action === 'open') {
    await openControlUi('/#/assistant', t('ComputeDeck · 小助手'));
    return;
  }
  if (start.action === 'web') {
    await openControlUi('/#/assistant', t('ComputeDeck · 小助手'));
    return;
  }

  const presets = Array.isArray(status.presets) ? status.presets : [];
  const presetPick = await vscode.window.showQuickPick(
    [
      ...presets.map((p) => ({
        label: p.label || p.id,
        description: p.base_url || '',
        base_url: p.base_url || '',
      })),
      {
        label: t('保留当前地址'),
        description: status.base_url || t('（空）'),
        base_url: status.base_url || '',
      },
    ],
    { placeHolder: t('选模型接口类型（内网一般选自定义）') }
  );
  if (!presetPick) return;

  const baseUrl = await vscode.window.showInputBox({
    prompt: t('模型接口地址（主控机能访问的，通常带 /v1）'),
    value: presetPick.base_url || status.base_url || '',
    ignoreFocusOut: true,
    placeHolder: 'https://example.com/v1',
  });
  if (baseUrl === undefined) return;
  if (!String(baseUrl).trim()) {
    vscode.window.showWarningMessage(t('地址不能为空'));
    return;
  }

  let apiKey;
  const keyPrompt = status.api_key_configured
    ? t('API 密钥（留空表示沿用已保存的）')
    : t('API 密钥（会保存在主控本机，不会进 git）');
  apiKey = await vscode.window.showInputBox({
    prompt: keyPrompt,
    password: true,
    ignoreFocusOut: true,
  });
  if (apiKey === undefined) return;

  const draft = {
    base_url: String(baseUrl).trim(),
  };
  if (String(apiKey || '').trim()) draft.api_key = String(apiKey).trim();

  const probeChoice = await vscode.window.showQuickPick(
    [
      { label: t('先探测能不能连上'), action: 'probe' },
      { label: t('跳过探测，直接选模型'), action: 'skip' },
    ],
    { placeHolder: t('要不要先测一下接口？') }
  );
  if (!probeChoice) return;
  if (probeChoice.action === 'probe') {
    try {
      const probe = await client.assistantProbe(draft);
      if (probe && probe.ok === false) {
        const detail = probe.hint || probe.error || probe.message || t('探测失败');
        const cont = await vscode.window.showWarningMessage(
          t('探测未通过：{0}', detail),
          t('仍然继续'),
          t('取消')
        );
        if (cont !== t('仍然继续')) return;
      } else {
        vscode.window.showInformationMessage(t('探测通过，可以选模型了'));
      }
    } catch (err) {
      const cont = await vscode.window.showWarningMessage(
        t('探测出错：{0}', err.message || err),
        t('仍然继续'),
        t('取消')
      );
      if (cont !== t('仍然继续')) return;
    }
  }

  let model = status.model || '';
  try {
    const listed = await client.assistantModels(draft);
    const models = Array.isArray(listed?.models)
      ? listed.models
      : Array.isArray(listed?.data)
        ? listed.data
        : [];
    const ids = models
      .map((m) => (typeof m === 'string' ? m : m.id || m.name || ''))
      .filter(Boolean);
    if (ids.length) {
      const picked = await vscode.window.showQuickPick(
        [
          ...ids.slice(0, 80).map((id) => ({ label: id, description: id === model ? t('当前') : '' })),
          { label: t('手动输入模型名…'), description: '', manual: true },
        ],
        { placeHolder: t('选择模型') }
      );
      if (!picked) return;
      if (picked.manual) {
        model =
          (await vscode.window.showInputBox({
            prompt: t('模型名'),
            value: model,
            ignoreFocusOut: true,
          })) || '';
      } else {
        model = picked.label;
      }
    } else {
      model =
        (await vscode.window.showInputBox({
          prompt: t('没拉到模型列表，请手动填写模型名'),
          value: model,
          ignoreFocusOut: true,
        })) || '';
    }
  } catch (err) {
    model =
      (await vscode.window.showInputBox({
        prompt: t('拉取模型失败（{0}），请手动填写模型名', err.message || err),
        value: model,
        ignoreFocusOut: true,
      })) || '';
  }
  if (!String(model).trim()) {
    vscode.window.showWarningMessage(t('模型名不能为空'));
    return;
  }

  const body = {
    base_url: String(baseUrl).trim(),
    model: String(model).trim(),
    enabled: true,
  };
  if (String(apiKey || '').trim()) body.api_key = String(apiKey).trim();

  try {
    const saved = await client.assistantConfig(body);
    output.appendLine(t('小助手 API 已保存：模型 {0} · {1}', saved.model || body.model, saved.base_url || body.base_url));
    vscode.window.showInformationMessage(
      saved.ready ? t('小助手已就绪') : t('已保存，但尚未就绪，请检查地址或密钥')
    );
    await provider.refresh(true);
    if (saved.ready) {
      const open = t('打开小助手');
      const choice = await vscode.window.showInformationMessage(t('要现在打开聊天吗？'), open);
      if (choice === open) await openControlUi('/#/assistant', t('ComputeDeck · 小助手'));
    }
  } catch (err) {
    vscode.window.showErrorMessage(t('保存失败：{0}', err.message || err));
  }
}

function readGuideMarkdown(fileName) {
  if (!extensionContext) return '';
  const uri = vscode.Uri.joinPath(extensionContext.extensionUri, 'media', 'guide', fileName);
  try {
    return fs.readFileSync(uri.fsPath, 'utf8');
  } catch (_) {
    return '';
  }
}

function formatGuideInline(text) {
  return String(text || '')
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
    .replace(/\[([^\]]+)\]\(command:([^)]+)\)/g, '<strong>$1</strong>');
}

function markdownToGuideHtml(md) {
  const escaped = String(md || '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
  const lines = escaped.split(/\r?\n/);
  const html = [];
  let listTag = '';
  const flushList = () => {
    if (listTag) {
      html.push(`</${listTag}>`);
      listTag = '';
    }
  };
  const openList = (tag) => {
    if (listTag !== tag) {
      flushList();
      html.push(`<${tag}>`);
      listTag = tag;
    }
  };
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    const heading = line.match(/^(#{1,3})\s+(.*)$/);
    if (heading) {
      flushList();
      html.push(`<h${heading[1].length}>${formatGuideInline(heading[2])}</h${heading[1].length}>`);
      i += 1;
      continue;
    }
    if (/^\|.+\|/.test(line) && i + 1 < lines.length && /^\|\s*[-:| ]+\|/.test(lines[i + 1])) {
      flushList();
      const rows = [];
      while (i < lines.length && /^\|.+\|/.test(lines[i])) {
        const cells = lines[i]
          .replace(/^\|/, '')
          .replace(/\|$/, '')
          .split('|')
          .map((c) => c.trim());
        if (!/^[-:| ]+$/.test(cells.join(''))) rows.push(cells);
        i += 1;
      }
      if (rows.length) {
        const head = rows[0];
        const body = rows.slice(1);
        html.push('<table><thead><tr>');
        for (const cell of head) html.push(`<th>${formatGuideInline(cell)}</th>`);
        html.push('</tr></thead><tbody>');
        for (const row of body) {
          html.push('<tr>');
          for (const cell of row) html.push(`<td>${formatGuideInline(cell)}</td>`);
          html.push('</tr>');
        }
        html.push('</tbody></table>');
      }
      continue;
    }
    const ul = line.match(/^[-*]\s+(.*)$/);
    if (ul) {
      openList('ul');
      html.push(`<li>${formatGuideInline(ul[1])}</li>`);
      i += 1;
      continue;
    }
    const ol = line.match(/^\d+\.\s+(.*)$/);
    if (ol) {
      openList('ol');
      html.push(`<li>${formatGuideInline(ol[1])}</li>`);
      i += 1;
      continue;
    }
    if (!line.trim()) {
      flushList();
      i += 1;
      continue;
    }
    flushList();
    // Skip trailing command-link-only lines; step action buttons already cover them.
    if (/^\[[^\]]+\]\(command:[^)]+\)(?:\s*[｜|]\s*\[[^\]]+\]\(command:[^)]+\))*$/.test(line.trim())) {
      i += 1;
      continue;
    }
    html.push(`<p>${formatGuideInline(line)}</p>`);
    i += 1;
  }
  flushList();
  return html.join('\n');
}

function buildGuideHtml(webview, stepIndex) {
  const nonce = `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
  const idx = Math.max(0, Math.min(GUIDE_STEPS.length - 1, Number(stepIndex) || 0));
  const step = GUIDE_STEPS[idx];
  const body = markdownToGuideHtml(readGuideMarkdown(step.file)).replace(/^<h2>.*?<\/h2>\s*/, '');
  const nav = GUIDE_STEPS.map(
    (s, i) =>
      `<button type="button" class="step${i === idx ? ' active' : ''}${i < idx ? ' done' : ''}" data-step="${i}">
        <span class="num">${String(i + 1).padStart(2, '0')}</span>
        <span class="label">${s.title}</span>
      </button>`
  ).join('');
  const actions = (step.actions || [])
    .map(
      (a, i) =>
        `<button type="button" class="${i === 0 ? 'primary' : ''}" data-cmd="${a.command}" data-args='${JSON.stringify(a.args || [])}'>${a.label}</button>`
    )
    .join('');
  const pct = Math.round(((idx + 1) / GUIDE_STEPS.length) * 100);
  const csp = [
    `default-src 'none'`,
    `style-src ${webview.cspSource} 'unsafe-inline'`,
    `script-src 'nonce-${nonce}'`,
  ].join('; ');
  return `<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8" />
  <meta http-equiv="Content-Security-Policy" content="${csp}" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>五分钟上手</title>
  <style>
    :root {
      color-scheme: light dark;
      --accent: var(--vscode-button-background, #0f766e);
      --accent-ink: var(--vscode-button-foreground, #f0fdfa);
      --line: var(--vscode-widget-border, rgba(127,127,127,.3));
      --soft: color-mix(in srgb, var(--vscode-editor-background) 91%, var(--vscode-foreground) 6%);
      --muted: color-mix(in srgb, var(--vscode-foreground) 62%, transparent);
    }
    * { box-sizing: border-box; }
    body {
      margin: 0; min-height: 100vh; display: grid; grid-template-columns: 268px minmax(0, 1fr);
      font: 13.5px/1.6 var(--vscode-font-family);
      color: var(--vscode-foreground);
      background:
        radial-gradient(900px 420px at 100% -10%, color-mix(in srgb, var(--accent) 9%, transparent), transparent 60%),
        var(--vscode-editor-background);
    }
    aside {
      position: sticky; top: 0; height: 100vh; padding: 22px 16px 18px;
      border-right: 1px solid var(--line);
      background: color-mix(in srgb, var(--vscode-editor-background) 94%, var(--vscode-foreground) 3%);
      display: flex; flex-direction: column; gap: 18px;
    }
    .brand { display: flex; align-items: center; gap: 10px; padding: 0 6px; }
    .brand svg { width: 30px; height: 30px; flex: 0 0 auto; }
    .brand strong { display: block; font-size: 14px; letter-spacing: -.01em; }
    .brand small { display: block; font-size: 11px; color: var(--muted); }
    .nav { display: grid; gap: 5px; }
    .step {
      display: flex; align-items: center; gap: 10px; width: 100%; text-align: left;
      border: 1px solid transparent; background: transparent; color: inherit;
      border-radius: 9px; padding: 9px 10px; cursor: pointer; font: inherit;
    }
    .step:hover { background: var(--vscode-list-hoverBackground); }
    .step .num {
      width: 24px; height: 24px; border-radius: 50%; flex: 0 0 auto;
      display: grid; place-items: center; font-size: 10px; font-weight: 700;
      border: 1px solid var(--line); color: var(--muted);
      font-family: var(--vscode-editor-font-family, monospace);
    }
    .step .label { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .step.done .num { border-color: color-mix(in srgb, var(--accent) 55%, var(--line)); color: var(--accent); }
    .step.active {
      background: color-mix(in srgb, var(--accent) 12%, var(--soft));
      border-color: color-mix(in srgb, var(--accent) 30%, var(--line));
      font-weight: 600;
    }
    .step.active .num { background: var(--accent); border-color: var(--accent); color: var(--accent-ink); }
    .aside-foot { margin-top: auto; padding: 12px 8px 0; border-top: 1px solid var(--line); }
    .aside-foot .meta { font-size: 11px; color: var(--muted); display: flex; justify-content: space-between; margin-bottom: 7px; }
    .bar { height: 4px; border-radius: 999px; background: color-mix(in srgb, var(--vscode-foreground) 8%, transparent); overflow: hidden; }
    .bar i { display: block; height: 100%; background: linear-gradient(90deg, #2dd4bf, var(--accent)); border-radius: inherit; transition: width .25s ease; }
    main { padding: 30px clamp(20px, 5vw, 64px) 26px; overflow: auto; display: flex; flex-direction: column; }
    .wrap { width: 100%; max-width: 800px; margin: auto; display: grid; gap: 16px; }
    .kicker {
      font-size: 11px; font-weight: 700; letter-spacing: .12em; text-transform: uppercase;
      color: color-mix(in srgb, var(--accent) 78%, var(--vscode-foreground));
    }
    h1 { margin: 0; font-size: clamp(25px, 3.2vw, 34px); line-height: 1.15; letter-spacing: -.03em; }
    .card {
      border: 1px solid var(--line); border-radius: 16px; background: var(--soft);
      padding: 22px 24px 20px;
    }
    #body > h2, #body:empty { display: none; }
    #body p { margin: 0 0 12px; color: color-mix(in srgb, var(--vscode-foreground) 86%, transparent); max-width: 64ch; }
    #body p:last-child { margin-bottom: 0; }
    #body ul, #body ol { margin: 0 0 12px; padding-left: 1.3em; display: grid; gap: 6px; }
    #body li { max-width: 62ch; }
    #body table { width: 100%; border-collapse: separate; border-spacing: 0; margin: 4px 0 14px; font-size: 12.5px; border: 1px solid var(--line); border-radius: 10px; overflow: hidden; }
    #body th, #body td { padding: 9px 11px; text-align: left; vertical-align: top; border-bottom: 1px solid var(--line); }
    #body tr:last-child td { border-bottom: 0; }
    #body th { background: color-mix(in srgb, var(--vscode-foreground) 5%, transparent); font-weight: 600; }
    #body code {
      font-family: var(--vscode-editor-font-family, monospace); font-size: 12px;
      padding: 1px 5px; border-radius: 5px;
      background: color-mix(in srgb, var(--vscode-foreground) 9%, transparent);
    }
    #body a { color: var(--vscode-textLink-foreground); text-decoration: none; }
    #body a:hover { text-decoration: underline; }
    #body strong { font-weight: 650; }
    .actions { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 2px; }
    .actions button {
      appearance: none; font: inherit; cursor: pointer; border-radius: 999px;
      border: 1px solid var(--line); padding: 8px 15px;
      background: color-mix(in srgb, var(--vscode-foreground) 6%, transparent);
      color: var(--vscode-foreground);
    }
    .actions button:hover { border-color: color-mix(in srgb, var(--accent) 45%, var(--line)); }
    .actions button.primary {
      border-color: transparent; font-weight: 650;
      background: var(--accent); color: var(--accent-ink);
    }
    .pager { display: flex; align-items: center; gap: 10px; padding-top: 14px; border-top: 1px solid var(--line); }
    .pager .spacer { flex: 1; }
    .pager button {
      appearance: none; font: inherit; cursor: pointer; border-radius: 8px; padding: 8px 15px;
      border: 1px solid var(--line); background: transparent; color: var(--vscode-foreground);
    }
    .pager button.primary { border-color: transparent; background: var(--accent); color: var(--accent-ink); font-weight: 650; }
    .pager button:disabled { opacity: .4; cursor: default; }
    .hint { margin: 0; font-size: 11.5px; color: var(--muted); }
    @media (max-width: 760px) {
      body { grid-template-columns: 1fr; }
      aside { position: static; height: auto; border-right: 0; border-bottom: 1px solid var(--line); }
      .nav { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .aside-foot { margin-top: 0; }
      main { padding: 20px 16px 24px; }
    }
  </style>
</head>
<body>
  <aside>
    <div class="brand">
      <svg viewBox="0 0 128 128" aria-hidden="true">
        <defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#2dd4bf"/><stop offset="1" stop-color="#0f766e"/></linearGradient></defs>
        <rect width="128" height="128" rx="30" fill="url(#g)"/>
        <rect x="22" y="30" width="64" height="16" rx="5" fill="#fff" opacity=".96"/>
        <rect x="28" y="55" width="58" height="16" rx="5" fill="#ccfbf1" opacity=".95"/>
        <rect x="34" y="80" width="52" height="16" rx="5" fill="#99f6e4" opacity=".9"/>
        <path d="M96 38V88" stroke="#fff" stroke-opacity=".4" stroke-width="2" stroke-dasharray="3 4"/>
        <circle cx="96" cy="38" r="7.5" fill="#fb923c"/>
        <circle cx="96" cy="63" r="5.5" fill="#fff" opacity=".88"/>
        <circle cx="96" cy="88" r="5.5" fill="#fff" opacity=".72"/>
      </svg>
      <div><strong>ComputeDeck</strong><small>上手指南</small></div>
    </div>
    <nav class="nav" aria-label="步骤">${nav}</nav>
    <div class="aside-foot">
      <div class="meta"><span>进度</span><span>${idx + 1} / ${GUIDE_STEPS.length}</span></div>
      <div class="bar"><i style="width:${pct}%"></i></div>
    </div>
  </aside>
  <main>
    <div class="wrap">
      <div class="kicker">第 ${idx + 1} 步 / 共 ${GUIDE_STEPS.length} 步</div>
      <h1>${step.title}</h1>
      <section class="card"><div id="body">${body}</div></section>
      <div class="actions">${actions}</div>
      <div class="pager">
        <button type="button" id="prev" ${idx === 0 ? 'disabled' : ''}>上一步</button>
        <span class="spacer"></span>
        <p class="hint">下面的按钮会直接执行对应操作</p>
        <button type="button" class="primary" id="next">${idx >= GUIDE_STEPS.length - 1 ? '完成' : '下一步'}</button>
      </div>
    </div>
  </main>
  <script nonce="${nonce}">
    const vscode = acquireVsCodeApi();
    const step = ${idx};
    document.querySelectorAll('[data-step]').forEach((el) => {
      el.addEventListener('click', () => vscode.postMessage({ command: 'goto', step: Number(el.dataset.step) }));
    });
    document.querySelectorAll('[data-cmd]').forEach((el) => {
      el.addEventListener('click', () => {
        let args = [];
        try { args = JSON.parse(el.dataset.args || '[]'); } catch (_) {}
        vscode.postMessage({ command: 'run', id: el.dataset.cmd, args });
      });
    });
    document.getElementById('prev').addEventListener('click', () => vscode.postMessage({ command: 'goto', step: step - 1 }));
    document.getElementById('next').addEventListener('click', () => {
      if (step >= ${GUIDE_STEPS.length - 1}) vscode.postMessage({ command: 'done' });
      else vscode.postMessage({ command: 'goto', step: step + 1 });
    });
  </script>
</body>
</html>`;
}

function showGuide(stepIndex = 0) {
  const idx = Math.max(0, Math.min(GUIDE_STEPS.length - 1, Number(stepIndex) || 0));
  if (guidePanel) {
    guidePanel.reveal(vscode.ViewColumn.Active, false);
    guidePanel.webview.html = buildGuideHtml(guidePanel.webview, idx);
    return;
  }
  guidePanel = vscode.window.createWebviewPanel(
    'computedeck.guide',
    t('五分钟上手'),
    vscode.ViewColumn.Active,
    { enableScripts: true, retainContextWhenHidden: true }
  );
  guidePanel.webview.html = buildGuideHtml(guidePanel.webview, idx);
  guidePanel.onDidDispose(() => {
    guidePanel = undefined;
  });
  guidePanel.webview.onDidReceiveMessage(async (msg) => {
    if (!msg || !msg.command) return;
    if (msg.command === 'goto') {
      showGuide(msg.step);
      return;
    }
    if (msg.command === 'done') {
      if (extensionContext) {
        await extensionContext.globalState.update('computedeck.guideCompleted', true);
      }
      vscode.window.showInformationMessage(t('五分钟上手已完成。以后可在命令面板搜 ComputeDeck 再打开。'));
      guidePanel?.dispose();
      return;
    }
    if (msg.command === 'run' && msg.id) {
      await vscode.commands.executeCommand(msg.id, ...(Array.isArray(msg.args) ? msg.args : []));
    }
  });
}

async function openGettingStarted() {
  // 原生 Walkthrough 的排版由编辑器控制，信息一多就变成一列蓝色按钮；
  // 统一用自定义面板，侧栏步骤和内容区都能按产品自己的层次排。
  showGuide(0);
  if (extensionContext) {
    await extensionContext.globalState.update('computedeck.guideOpened', true);
  }
  return 'guide';
}

// ── 工作台面板 ─────────────────────────────────────────────
function workbenchSnapshot() {
  const data = provider?.data || null;
  const error = provider?.lastError || null;
  const summary = data?.overview?.summary || {};
  const assistant = data?.assistant || {};
  const notices = data?.notices || {};
  const mcpPath = resolveMcpPath();
  const mcpReady = Boolean(mcpPath && fs.existsSync(mcpPath));
  const tokenReady = Boolean(readLocalApiToken() || process.env.GPU_CONTROL_API_TOKEN);
  const access = accessProfile();
  return {
    connected: Boolean(data) && !error,
    error: error ? String(error.message || error) : '',
    base: client.base(),
    access,
    summary: {
      nodes: summary.nodes ?? 0,
      nodes_online: summary.nodes_online ?? 0,
      nodes_offline: summary.nodes_offline ?? 0,
      gpus_free: summary.gpus_free ?? 0,
      gpus_total: summary.gpus_total ?? 0,
      queue_pending: summary.queue_pending ?? 0,
      leases: (data?.leases || []).length,
    },
    assistant: {
      enabled: Boolean(assistant.enabled),
      ready: Boolean(assistant.ready),
      model: assistant.model || '',
      mode: assistant.mode || '',
      base_url: assistant.base_url || '',
      api_key_configured: Boolean(assistant.api_key_configured),
      setup_hint: assistant.setup_hint || '',
      network_note: assistant.network_note || '',
    },
    notices: {
      unread: Number(notices.unread || 0),
      items: (notices.items || [])
        .filter((n) => !n.dismissed)
        .slice(0, 8)
        .map((n) => ({
          id: n.id || '',
          title: n.title || n.id || t('公告'),
          severity: n.severity || '',
          source: n.source || '',
          detail: n.detail || '',
        })),
    },
    mcp: {
      ready: mcpReady,
      path: mcpPath || '',
      tokenReady,
    },
  };
}

function buildWorkbenchHtml(webview) {
  const nonce = `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
  const csp = [
    `default-src 'none'`,
    `style-src ${webview.cspSource} 'unsafe-inline'`,
    `script-src 'nonce-${nonce}'`,
  ].join('; ');
  const pagesJson = JSON.stringify(CONTROL_PAGES);
  return `<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8" />
  <meta http-equiv="Content-Security-Policy" content="${csp}" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>ComputeDeck 工作台</title>
  <style>
    :root {
      color-scheme: light dark;
      --cd-gap: 16px;
      --cd-radius: 10px;
      --cd-line: var(--vscode-widget-border, rgba(127,127,127,.28));
      --cd-soft: color-mix(in srgb, var(--vscode-editor-background) 92%, var(--vscode-foreground) 5%);
      --cd-accent: #0f766e;
      --cd-accent-fg: #f0fdfa;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      min-height: 100vh;
      padding: 22px 24px 32px;
      font: 13px/1.5 var(--vscode-font-family);
      color: var(--vscode-foreground);
      background:
        radial-gradient(1200px 420px at 0% -10%, color-mix(in srgb, var(--cd-accent) 14%, transparent), transparent 60%),
        var(--vscode-editor-background);
    }
    .shell { max-width: 1080px; margin: 0 auto; display: grid; gap: 18px; }
    .hero {
      display: flex; flex-wrap: wrap; align-items: flex-start; justify-content: space-between;
      gap: 14px; padding-bottom: 4px;
    }
    .hero-copy h1 {
      margin: 0; font-size: 20px; font-weight: 700; letter-spacing: -.02em;
    }
    .hero-copy .meta {
      margin: 6px 0 0; display: flex; flex-wrap: wrap; gap: 8px; align-items: center;
      color: color-mix(in srgb, var(--vscode-foreground) 68%, transparent);
      font-size: 12px;
    }
    .pill {
      display: inline-flex; align-items: center; gap: 6px;
      border: 1px solid var(--cd-line); border-radius: 999px;
      padding: 2px 9px; background: var(--cd-soft); font-size: 12px;
    }
    .dot { width: 7px; height: 7px; border-radius: 50%; background: #94a3b8; }
    .dot.on { background: #16a34a; }
    .dot.off { background: #dc2626; }
    .dot.warn { background: #d97706; }
    .hero-actions { display: flex; flex-wrap: wrap; gap: 8px; }
    button {
      appearance: none; font: inherit; cursor: pointer;
      border-radius: 8px; border: 1px solid var(--cd-line);
      background: var(--vscode-button-secondaryBackground);
      color: var(--vscode-button-secondaryForeground);
      padding: 7px 12px;
    }
    button:hover { filter: brightness(1.04); }
    button.primary {
      border-color: transparent;
      background: var(--vscode-button-background, var(--cd-accent));
      color: var(--vscode-button-foreground, var(--cd-accent-fg));
      font-weight: 600;
    }
    button.ghost {
      background: transparent;
    }
    button.danger {
      border-color: color-mix(in srgb, #dc2626 35%, var(--cd-line));
      color: #b91c1c;
      background: color-mix(in srgb, #dc2626 8%, transparent);
    }
    .section { display: grid; gap: 10px; }
    .section-h {
      display: flex; align-items: baseline; justify-content: space-between; gap: 10px;
    }
    .section-h h2 {
      margin: 0; font-size: 12px; font-weight: 700; letter-spacing: .04em;
      text-transform: uppercase; opacity: .72;
    }
    .section-h .more { font-size: 12px; opacity: .7; }
    .kpis {
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 10px;
    }
    @media (max-width: 720px) {
      .kpis { grid-template-columns: repeat(2, minmax(0, 1fr)); }
    }
    .kpi {
      border: 1px solid var(--cd-line); border-radius: var(--cd-radius);
      padding: 14px 14px 12px; background: var(--cd-soft);
      min-height: 84px;
    }
    .kpi .label { font-size: 12px; opacity: .68; }
    .kpi .value {
      margin-top: 6px; font-size: 26px; font-weight: 700; letter-spacing: -.03em;
      font-variant-numeric: tabular-nums;
    }
    .kpi .hint { margin-top: 4px; font-size: 11px; opacity: .58; }
    .kpi.alert .value { color: var(--vscode-editorWarning-foreground, #d97706); }
    .kpi.bad .value { color: var(--vscode-errorForeground, #dc2626); }
    .pages {
      display: flex; flex-wrap: wrap; gap: 6px;
      padding: 8px; border: 1px solid var(--cd-line); border-radius: 12px;
      background: color-mix(in srgb, var(--cd-soft) 80%, transparent);
    }
    .pages button {
      border: 0; background: transparent; padding: 6px 11px; border-radius: 8px;
      color: inherit; opacity: .86;
    }
    .pages button:hover {
      background: color-mix(in srgb, var(--vscode-list-hoverBackground) 90%, transparent);
      opacity: 1;
    }
    .grid-2 {
      display: grid;
      grid-template-columns: minmax(0, 1.1fr) minmax(0, .9fr);
      gap: var(--cd-gap);
    }
    @media (max-width: 860px) {
      .grid-2 { grid-template-columns: 1fr; }
    }
    .panel {
      border: 1px solid var(--cd-line); border-radius: 12px;
      background: var(--cd-soft); padding: 14px 16px 16px;
      display: grid; gap: 12px; align-content: start;
    }
    .panel h3 { margin: 0; font-size: 14px; font-weight: 700; }
    .status-rows { display: grid; gap: 8px; }
    .status-row {
      display: grid; grid-template-columns: 72px minmax(0, 1fr); gap: 10px;
      align-items: start; padding: 8px 0;
      border-top: 1px solid color-mix(in srgb, var(--cd-line) 80%, transparent);
    }
    .status-row:first-child { border-top: 0; padding-top: 0; }
    .status-row .k { font-size: 12px; opacity: .62; padding-top: 2px; }
    .status-row .v { min-width: 0; }
    .status-row .title { font-weight: 600; }
    .status-row .desc {
      margin-top: 2px; font-size: 12px; opacity: .66;
      overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
    }
    .btn-row { display: flex; flex-wrap: wrap; gap: 8px; }
    .ops-group { display: grid; gap: 8px; }
    .ops-group .label {
      font-size: 11px; letter-spacing: .03em; text-transform: uppercase; opacity: .55;
    }
    .notices {
      border: 1px solid var(--cd-line); border-radius: 12px; background: var(--cd-soft);
      overflow: hidden;
    }
    .notices-h {
      display: flex; align-items: center; justify-content: space-between; gap: 10px;
      padding: 12px 14px; border-bottom: 1px solid var(--cd-line);
    }
    .notices-h h3 { margin: 0; font-size: 14px; font-weight: 700; }
    .notice-list { list-style: none; margin: 0; padding: 0; max-height: 280px; overflow: auto; }
    .notice-list li {
      display: grid; grid-template-columns: 64px minmax(0, 1fr); gap: 10px;
      padding: 10px 14px; cursor: pointer;
      border-top: 1px solid color-mix(in srgb, var(--cd-line) 75%, transparent);
    }
    .notice-list li:first-child { border-top: 0; }
    .notice-list li:hover { background: color-mix(in srgb, var(--vscode-list-hoverBackground) 85%, transparent); }
    .sev {
      height: fit-content; justify-self: start;
      border-radius: 999px; padding: 2px 8px; font-size: 11px; font-weight: 650;
      border: 1px solid var(--cd-line);
    }
    .sev.error, .sev.critical {
      color: #b91c1c; border-color: color-mix(in srgb, #dc2626 30%, var(--cd-line));
      background: color-mix(in srgb, #dc2626 10%, transparent);
    }
    .sev.warn, .sev.warning {
      color: #b45309; border-color: color-mix(in srgb, #d97706 30%, var(--cd-line));
      background: color-mix(in srgb, #d97706 10%, transparent);
    }
    .sev.info { opacity: .8; }
    .notice-title { font-weight: 600; }
    .notice-detail {
      margin-top: 2px; font-size: 12px; opacity: .62;
      display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden;
    }
    .empty { padding: 18px 14px; opacity: .6; }
    .foot { font-size: 12px; opacity: .55; }
  </style>
</head>
<body>
  <div class="shell">
    <header class="hero">
      <div class="hero-copy">
        <h1>ComputeDeck 工作台</h1>
        <div class="meta">
          <span class="pill"><span class="dot" id="connDot"></span><span id="connLabel">加载中</span></span>
          <span id="baseLabel"></span>
        </div>
      </div>
      <div class="hero-actions" id="heroActions"></div>
    </header>

    <section class="notices" style="border-style:solid">
      <div class="notices-h">
        <h3>先看这里</h3>
        <div class="btn-row" id="accessActions"></div>
      </div>
      <div class="empty" id="accessBox" style="padding:12px 14px">加载中…</div>
    </section>

    <section class="section">
      <div class="section-h"><h2>集群状态</h2><span class="more" id="kpiMore"></span></div>
      <div class="kpis" id="kpis"></div>
    </section>

    <section class="section">
      <div class="section-h"><h2>看板页面</h2><span class="more">在编辑器里打开</span></div>
      <div class="pages" id="pages"></div>
    </section>

    <section class="section">
      <div class="section-h"><h2>小助手与 AI</h2><span class="more" id="aiMore"></span></div>
      <div class="grid-2">
        <div class="panel">
          <h3>网页小助手</h3>
          <div class="status-rows" id="assistantBlock"></div>
          <div class="btn-row" id="assistantActions"></div>
          <p class="foot" id="assistantHint"></p>
        </div>
        <div class="panel">
          <h3>给编辑器 AI 用的工具</h3>
          <div class="status-rows" id="mcpBlock"></div>
          <div class="btn-row" id="mcpActions"></div>
          <p class="foot">注册后，Copilot / CatPaw 等可直接调集群命令。完整对话仍在网页小助手里。</p>
        </div>
      </div>
    </section>

    <section class="grid-2">
      <div class="panel">
        <h3>登录</h3>
        <div class="status-rows" id="authBlock"></div>
        <div class="btn-row" id="authActions"></div>
      </div>
      <div class="panel">
        <h3>常用操作</h3>
        <div class="ops-group">
          <div class="label">诊断</div>
          <div class="btn-row" id="opsDiag"></div>
        </div>
        <div class="ops-group">
          <div class="label">部署 / 维护</div>
          <div class="btn-row" id="opsMutate"></div>
        </div>
        <p class="foot">危险操作会再确认一次；文字结果在输出面板「ComputeDeck」。</p>
      </div>
    </section>

    <section class="notices">
      <div class="notices-h">
        <h3>公告</h3>
        <div class="btn-row" id="noticeActions"></div>
      </div>
      <ul class="notice-list" id="notices"></ul>
    </section>
  </div>
  <script nonce="${nonce}">
    const vscode = acquireVsCodeApi();
    const PAGES = ${pagesJson};
    const el = (id) => document.getElementById(id);
    function btn(label, payload, kind) {
      const b = document.createElement('button');
      b.textContent = label;
      if (kind) b.className = kind;
      b.addEventListener('click', () => vscode.postMessage(
        typeof payload === 'string' ? { command: payload } : payload
      ));
      return b;
    }
    function kpiCard(label, value, hint, tone) {
      const d = document.createElement('div');
      d.className = 'kpi' + (tone ? ' ' + tone : '');
      d.innerHTML = '<div class="label"></div><div class="value"></div><div class="hint"></div>';
      d.children[0].textContent = label;
      d.children[1].textContent = value;
      d.children[2].textContent = hint || '';
      return d;
    }
    function statusRow(key, title, desc, ok) {
      const row = document.createElement('div');
      row.className = 'status-row';
      const k = document.createElement('div');
      k.className = 'k';
      k.textContent = key;
      const v = document.createElement('div');
      v.className = 'v';
      const t = document.createElement('div');
      t.className = 'title';
      t.textContent = title;
      if (ok === true) t.style.color = 'var(--vscode-testing-iconPassed, #16a34a)';
      if (ok === false) t.style.color = 'var(--vscode-editorWarning-foreground, #d97706)';
      v.append(t);
      if (desc) {
        const d = document.createElement('div');
        d.className = 'desc';
        d.textContent = desc;
        d.title = desc;
        v.append(d);
      }
      row.append(k, v);
      return row;
    }
    function render(state) {
      const s = state.summary || {};
      const unread = Number((state.notices && state.notices.unread) || 0);
      const offline = Number(s.nodes_offline || 0);
      const connected = Boolean(state.connected);
      const dot = el('connDot');
      dot.className = 'dot ' + (connected ? (offline > 0 || unread > 0 ? 'warn' : 'on') : 'off');
      el('connLabel').textContent = connected ? '已连接' : '未连接';
      el('baseLabel').textContent = connected
        ? (state.base || '')
        : (state.error || '请点「怎么用」或「使用本机密钥」');
      el('heroActions').replaceChildren(
        btn('刷新', 'refresh', 'ghost'),
        btn('怎么用', 'openAccess', 'primary'),
        btn('打开完整看板', 'openDashboard', 'ghost'),
        btn('五分钟上手', 'openWalkthrough', 'ghost')
      );
      const access = state.access || {};
      const accessBox = el('accessBox');
      if (accessBox) {
        accessBox.replaceChildren();
        const title = document.createElement('div');
        title.className = 'notice-title';
        title.textContent = access.passwordlessPlugin
          ? '已自动登录，可直接用侧栏和工作台'
          : '若刷不出集群，点「怎么用」里的登录按钮';
        const detail = document.createElement('div');
        detail.className = 'notice-detail';
        detail.textContent = '需要完整界面时点「打开完整看板」；打不开再用「用浏览器打开」。';
        accessBox.append(title, detail);
      }
      el('kpiMore').textContent = connected
        ? ('空闲 GPU ' + (s.gpus_free ?? 0) + ' · 占用中 ' + (s.leases ?? 0))
        : '';
      el('kpis').replaceChildren(
        kpiCard('在线节点', (s.nodes_online ?? 0) + '/' + (s.nodes ?? 0), '能连上的机器', offline > 0 ? 'alert' : ''),
        kpiCard('空闲 GPU', (s.gpus_free ?? 0) + '/' + (s.gpus_total ?? 0), '还能用的显卡', Number(s.gpus_free || 0) === 0 ? 'alert' : ''),
        kpiCard('离线', String(offline), '需要检查的机器', offline > 0 ? 'bad' : ''),
        kpiCard('未读公告', String(unread), '健康与提示', unread > 0 ? 'alert' : '')
      );
      const pages = el('pages');
      pages.replaceChildren();
      for (const p of PAGES) {
        pages.append(btn(p.label, { command: 'openHash', hash: p.hash, title: p.label }));
      }
      const a = state.assistant || {};
      const m = state.mcp || {};
      el('aiMore').textContent = a.ready
        ? ('模型 ' + (a.model || '已配置'))
        : (a.setup_hint || '需要配置 API');
      el('assistantBlock').replaceChildren(
        statusRow('状态', a.ready ? '可用' : (a.enabled ? '已启用，未就绪' : '未启用'), a.model || '', a.ready ? true : false),
        statusRow('接口地址', a.base_url || '未填写', a.network_note || '', a.base_url ? true : false),
        statusRow('密钥', a.api_key_configured ? '已配置' : '未配置', a.api_key_configured ? '保存在主控本机' : '配置时粘贴一次即可', a.api_key_configured ? true : false)
      );
      el('assistantActions').replaceChildren(
        btn(a.ready ? '打开小助手' : '配置小助手 API', a.ready ? 'openAssistant' : 'configureAssistant', 'primary'),
        btn(a.ready ? '改配置' : '打开看板配置页', a.ready ? 'configureAssistant' : 'openAssistant', 'ghost')
      );
      el('assistantHint').textContent = a.ready
        ? '聊天、看集群建议、危险操作会二次确认。'
        : (a.setup_hint || '填主控能访问的模型地址、密钥和模型名。内网请用兼容网关。');
      el('mcpBlock').replaceChildren(
        statusRow('工具程序', m.ready ? '已找到' : '未找到', m.path || '一般在 bin/gpu-control-mcp', m.ready ? true : false),
        statusRow('登录密钥', m.tokenReady ? '已就绪' : '需要登录', m.tokenReady ? '本机密钥可用' : '先登录再注册', m.tokenReady ? true : false)
      );
      el('mcpActions').replaceChildren(
        btn('注册给 AI 用的工具', 'installMcp', m.ready ? 'primary' : 'ghost'),
        btn('打开小助手', 'openAssistant', 'ghost')
      );
      el('authBlock').replaceChildren(
        statusRow('主控登录', m.tokenReady ? '已就绪' : '需要登录', state.base || '', m.tokenReady ? true : false)
      );
      el('authActions').replaceChildren(
        btn(m.tokenReady ? '使用本机密钥' : '设置登录密钥', m.tokenReady ? 'useLocalCredentials' : 'setToken'),
        btn('密码登录', 'login', 'ghost'),
        btn('插件设置', 'openSettings', 'ghost')
      );
      el('opsDiag').replaceChildren(
        btn('运行自检', 'runDoctor'),
        btn('检查节点是否在线', 'liveness'),
        btn('测试能否 SSH', 'testSSH'),
        btn('只读诊断', 'execPreset')
      );
      el('opsMutate').replaceChildren(
        btn('部署任务', 'deployJob', 'primary'),
        btn('腾空节点', 'freeNode', 'danger')
      );
      el('accessActions').replaceChildren(
        btn('怎么用', 'openAccess', 'primary'),
        btn('打开完整看板', 'openDashboard', 'ghost')
      );
      el('noticeActions').replaceChildren(
        btn(unread > 0 ? ('查看全部 · ' + unread) : '打开公告页', 'openNotices', 'ghost')
      );
      const list = el('notices');
      const items = (state.notices && state.notices.items) || [];
      list.replaceChildren();
      if (!items.length) {
        const empty = document.createElement('div');
        empty.className = 'empty';
        empty.textContent = '暂无公告';
        list.append(empty);
        return;
      }
      for (const n of items.slice(0, 8)) {
        const li = document.createElement('li');
        const sev = document.createElement('span');
        const sevName = String(n.severity || 'info').toLowerCase();
        sev.className = 'sev ' + sevName;
        sev.textContent = sevName;
        const body = document.createElement('div');
        const title = document.createElement('div');
        title.className = 'notice-title';
        title.textContent = n.title || '公告';
        const detail = document.createElement('div');
        detail.className = 'notice-detail';
        detail.textContent = n.detail || n.source || '';
        body.append(title, detail);
        li.append(sev, body);
        li.addEventListener('click', () => vscode.postMessage({ command: 'openNotices' }));
        list.append(li);
      }
    }
    window.addEventListener('message', (ev) => {
      if (ev.data && ev.data.type === 'state') render(ev.data.state || {});
    });
    vscode.postMessage({ command: 'ready' });
  </script>
</body>
</html>`;
}

function refreshWorkbench() {
  if (!workbenchPanel) return;
  workbenchPanel.webview.postMessage({ type: 'state', state: workbenchSnapshot() });
}

function refreshAccessHub() {
  if (!controlUiPanel || !controlUiPanel._cdAccess) return;
  const cur = controlUiPanel._cdAccess;
  const next = {
    ...cur,
    ...accessProfile(),
    title: t('怎么用 ComputeDeck'),
  };
  controlUiPanel._cdAccess = next;
  // 只重建发生变化的页面，避免每次轮询把正在阅读的说明页刷新掉。
  const sig = accessHubSignature(next);
  if (sig === controlUiPanel._cdSig) return;
  controlUiPanel._cdSig = sig;
  controlUiPanel.title = next.title;
  controlUiPanel.webview.html = buildAccessHubHtml(controlUiPanel.webview, next);
}

function showWorkbench() {
  if (workbenchPanel) {
    workbenchPanel.reveal(vscode.ViewColumn.Active, false);
    workbenchPanel.webview.html = buildWorkbenchHtml(workbenchPanel.webview);
    refreshWorkbench();
    return;
  }
  workbenchPanel = vscode.window.createWebviewPanel(
    'computedeck.workbench',
    t('ComputeDeck 工作台'),
    vscode.ViewColumn.Beside,
    { enableScripts: true, retainContextWhenHidden: true }
  );
  workbenchPanel.webview.html = buildWorkbenchHtml(workbenchPanel.webview);
  workbenchPanel.onDidDispose(() => {
    workbenchPanel = undefined;
  });
  workbenchPanel.webview.onDidReceiveMessage(async (msg) => {
    const command = msg && msg.command;
    if (!command) return;
    if (command === 'ready' || command === 'refresh') {
      if (command === 'refresh') await provider.refresh(true);
      else refreshWorkbench();
      return;
    }
    if (command === 'openHash') {
      const page = CONTROL_PAGES.find((p) => p.hash === msg.hash) || { label: msg.title || '看板', hash: msg.hash };
      await openControlUi(page.hash || '/#/actions', t('ComputeDeck · {0}', page.label || msg.title || '看板'));
      return;
    }
    if (command === 'openPage') {
      await pickControlPage();
      return;
    }
    if (command === 'openSettings') {
      await vscode.commands.executeCommand('workbench.action.openSettings', 'computedeck');
      return;
    }
    if (command === 'openAccess') {
      await vscode.commands.executeCommand('computedeck.openAccess');
      return;
    }
    const map = {
      openDashboard: 'computedeck.openDashboard',
      openAssistant: 'computedeck.openAssistant',
      configureAssistant: 'computedeck.configureAssistant',
      openNotices: 'computedeck.openNotices',
      openWalkthrough: 'computedeck.openWalkthrough',
      openAccess: 'computedeck.openAccess',
      runDoctor: 'computedeck.runDoctor',
      liveness: 'computedeck.liveness',
      testSSH: 'computedeck.testSSH',
      execPreset: 'computedeck.execPreset',
      deployJob: 'computedeck.deployJob',
      freeNode: 'computedeck.freeNode',
      installMcp: 'computedeck.installMcp',
      login: 'computedeck.login',
      useLocalCredentials: 'computedeck.useLocalCredentials',
      setToken: 'computedeck.setToken',
    };
    const target = map[command];
    if (target) await vscode.commands.executeCommand(target);
  });
  refreshWorkbench();
}

function showResult(title, payload) {
  output.show(true);
  output.appendLine(`\n=== ${title} ===`);
  try {
    output.appendLine(JSON.stringify(payload, null, 2));
  } catch (_) {
    output.appendLine(String(payload));
  }
}

function nodeFromArg(arg) {
  if (!arg) return null;
  const nodes = provider?.data?.overview?.nodes || [];
  if (typeof arg === 'string') return nodes.find((n) => n.name === arg) || null;
  // Inline view buttons pass a TreeItem, not the raw node object.
  if (arg.contextValue && String(arg.contextValue).startsWith('node')) {
    const label = typeof arg.label === 'string' ? arg.label : arg.label?.label;
    return nodes.find((n) => n.name === label) || null;
  }
  return arg.name ? arg : null;
}

function jobFromArg(arg) {
  if (!arg) return null;
  const jobs = provider?.data?.jobs || [];
  if (typeof arg === 'string') {
    return jobs.find((j) => j.name === arg) || { name: arg };
  }
  if (arg.contextValue === 'job') {
    const label = typeof arg.label === 'string' ? arg.label : arg.label?.label;
    return jobs.find((j) => j.name === label) || (label ? { name: label } : null);
  }
  return arg.name ? arg : null;
}

async function pickNode() {
  const data = provider?.data || (await provider.refresh(false));
  const nodes = data?.overview?.nodes || [];
  if (!nodes.length) {
    vscode.window.showWarningMessage(t('ComputeDeck: 没有节点'));
    return null;
  }
  const picked = await vscode.window.showQuickPick(
    nodes.map((n) => ({
      label: n.name,
      description: `${stateLabel(n)} · ${n.counted ? `${n.free}/${n.total}` : '—'}`,
      node: n,
    })),
    { placeHolder: t('选择节点') }
  );
  return picked ? picked.node : null;
}

async function pickJob() {
  const jobs = provider?.data?.jobs || (await client.jobs());
  if (!jobs.length) {
    vscode.window.showWarningMessage(t('还没有已安装的任务'));
    return null;
  }
  const picked = await vscode.window.showQuickPick(
    jobs.map((j) => ({ label: j.name, description: t('{0} GPU', j.gpus_per_node ?? '?'), job: j })),
    { placeHolder: t('选择任务') }
  );
  return picked ? picked.job : null;
}

async function openNodeTerminal(nodeArg) {
  const node = nodeFromArg(nodeArg) || (await pickNode());
  if (!node) return;
  let host = node.host;
  let port = 22;
  let user = config().get('sshUser', '') || '';
  let key = '';
  try {
    const cfg = await client.configNodes();
    const doc = cfg.data || {};
    const meta = (doc.nodes || {})[node.name] || {};
    const defaults = doc.defaults || {};
    host = meta.ansible_host || host;
    port = Number(meta.ansible_port || defaults.ansible_port || 22);
    user = user || meta.ansible_user || defaults.ansible_user || 'root';
    key = String(meta.ansible_ssh_private_key_file || defaults.ansible_ssh_private_key_file || '');
  } catch (err) {
    output.appendLine(t('读取节点 SSH 配置失败：{0}', err.message || err));
  }
  const args = [];
  if (key && fs.existsSync(key)) args.push('-i', key);
  args.push('-p', String(port), `${user}@${host}`);
  const terminal = vscode.window.createTerminal({
    name: `CD: ${node.name}`,
    shellPath: 'ssh',
    shellArgs: args,
  });
  terminal.show();
}

async function nodeMenu(nodeArg) {
  const node = nodeFromArg(nodeArg) || (await pickNode());
  if (!node) return;
  const offline = node.liveness_state === 'offline' || node.state === 'offline';
  const choices = [
    { label: t('打开终端'), action: 'terminal', icon: 'terminal' },
    { label: t('运行 nvidia-smi'), action: 'nvidia-smi', icon: 'pulse' },
    { label: t('查看 GPU 进程'), action: 'gpu-procs', icon: 'list-unordered' },
    { label: node.cordoned ? t('取消隔离') : t('隔离节点'), action: 'cordon', icon: 'lock' },
    { label: t('腾空节点'), action: 'free', icon: 'debug-stop' },
    { label: t('在看板里查看这台节点'), action: 'dashboard', icon: 'globe' },
  ].map((x) => ({
    label: `${offline && ['terminal', 'nvidia-smi', 'gpu-procs', 'free'].includes(x.action) ? '$(warning) ' : ''}${x.label}`,
    action: x.action,
    description: offline && ['terminal', 'nvidia-smi', 'gpu-procs', 'free'].includes(x.action) ? t('节点离线') : '',
  }));
  const picked = await vscode.window.showQuickPick(choices, { placeHolder: `${node.name} · ${stateLabel(node)}` });
  if (!picked) return;
  switch (picked.action) {
    case 'terminal':
      if (offline) {
        vscode.window.showWarningMessage(t('节点离线，SSH 终端可能无法连接'));
      }
      return openNodeTerminal(node);
    case 'nvidia-smi':
      return runPreset(node, 'nvidia-smi');
    case 'gpu-procs':
      return runPreset(node, 'gpu-procs');
    case 'cordon':
      return toggleCordon(node);
    case 'free':
      return freeNode(node);
    case 'dashboard':
      return openControlUi('/#/cluster');
  }
}

async function jobMenu(jobArg) {
  const job = jobFromArg(jobArg) || (await pickJob());
  if (!job) return;
  const picked = await vscode.window.showQuickPick(
    [
      { label: t('启动'), action: 'start' },
      { label: t('停止'), action: 'stop' },
      { label: t('重启'), action: 'restart' },
      { label: t('状态'), action: 'status' },
      { label: t('等待就绪'), action: 'wait' },
    ],
    { placeHolder: t('{0} 操作', job.name) }
  );
  if (!picked) return;
  const result = await vscode.window.withProgress(
    { location: vscode.ProgressLocation.Notification, title: t('ComputeDeck: {0} {1}', picked.label, job.name) },
    () => client.jobAction(job.name, picked.action)
  );
  showResult(`${job.name} ${picked.label}`, result);
  await provider.refresh(true);
}

async function runPreset(nodeArg, presetArg) {
  const node = nodeFromArg(nodeArg) || (await pickNode());
  if (!node) return;
  const presets = provider?.data?.overview?.exec_presets || [
    { id: 'nvidia-smi', label: 'nvidia-smi' },
    { id: 'gpu-util', label: t('GPU 利用率') },
    { id: 'gpu-list', label: t('列出 GPU') },
    { id: 'gpu-procs', label: t('GPU 进程') },
    { id: 'uptime', label: t('负载/内存') },
    { id: 'disk', label: t('磁盘') },
  ];
  let preset = presetArg;
  if (!preset) {
    const picked = await vscode.window.showQuickPick(
      presets.map((p) => ({ label: p.label || p.id, id: p.id })),
      { placeHolder: t('在 {0} 运行只读诊断', node.name) }
    );
    if (!picked) return;
    preset = picked.id;
  }
  const result = await vscode.window.withProgress(
    { location: vscode.ProgressLocation.Notification, title: t('ComputeDeck: {0} @ {1}', preset, node.name) },
    () => client.execPreset(node.name, preset)
  );
  showResult(`${preset} @ ${node.name}`, result);
  if (!result.ok) vscode.window.showWarningMessage(t('诊断失败：{0}', result.error || t('见输出')));
}

async function toggleCordon(nodeArg) {
  const node = nodeFromArg(nodeArg) || (await pickNode());
  if (!node) return;
  const action = node.cordoned ? 'uncordon' : 'cordon';
  const confirm = t('确认');
  const ok = await vscode.window.showWarningMessage(
    node.cordoned ? t('取消隔离节点 {0}？', node.name) : t('隔离节点 {0}？', node.name),
    { modal: true },
    confirm
  );
  if (ok !== confirm) return;
  const result = await client.request(`/api/node/${action}`, {
    method: 'POST',
    body: { name: node.name },
  });
  showResult(`${action} ${node.name}`, result);
  await provider.refresh(true);
}

async function freeNode(nodeArg) {
  const node = nodeFromArg(nodeArg) || (await pickNode());
  if (!node) return;
  const freeLabel = t('腾空');
  const ok = await vscode.window.showWarningMessage(
    t('腾空 {0}？会停止任务、结束 GPU 进程并释放占用。', node.name),
    { modal: true },
    freeLabel
  );
  if (ok !== freeLabel) return;
  const result = await vscode.window.withProgress(
    { location: vscode.ProgressLocation.Notification, title: t('ComputeDeck: 腾空 {0}', node.name) },
    () => client.freeNode(node.name, false)
  );
  showResult(t('腾空 {0}', node.name), result);
  await provider.refresh(true);
}

async function deployJob() {
  const job = await pickJob();
  if (!job) return;
  const mode = await vscode.window.showQuickPick(
    [
      { label: t('按 GPU 数自动选节点'), mode: 'gpus' },
      { label: t('指定节点'), mode: 'node' },
    ],
    { placeHolder: t('部署方式') }
  );
  if (!mode) return;
  const body = { job: job.name };
  if (mode.mode === 'gpus') {
    const raw = await vscode.window.showInputBox({
      prompt: t('部署 GPU 数'),
      value: String(job.gpus_per_node || 4),
      validateInput: (v) => (/^\d+$/.test(v) && Number(v) > 0 ? null : t('请输入正整数')),
    });
    if (raw === undefined) return;
    body.gpus = Number(raw);
  } else {
    const node = await pickNode();
    if (!node) return;
    body.nodes = node.name;
  }
  const deployLabel = t('部署');
  const ok = await vscode.window.showWarningMessage(
    t('部署 {0}？', job.name),
    { modal: true },
    deployLabel
  );
  if (ok !== deployLabel) return;
  const result = await vscode.window.withProgress(
    { location: vscode.ProgressLocation.Notification, title: t('ComputeDeck: 部署 {0}', job.name) },
    () => client.deploy(job.name, body)
  );
  showResult(t('部署 {0}', job.name), result);
  await provider.refresh(true);
}

function resolveMcpPath() {
  let raw = String(config().get('mcpPath', '${workspaceFolder}/bin/gpu-control-mcp') || '');
  const folder = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath || '';
  const controlRoot = findControlRoot();
  raw = raw.replace(/\$\{workspaceFolder\}/g, folder || controlRoot);
  if (!path.isAbsolute(raw) && (folder || controlRoot)) {
    raw = path.join(folder || controlRoot, raw);
  }
  if ((!raw || !fs.existsSync(raw)) && controlRoot) {
    const fallback = path.join(controlRoot, 'bin', 'gpu-control-mcp');
    if (fs.existsSync(fallback)) return fallback;
  }
  return raw;
}

function registerMcp(context) {
  if (!vscode.lm || typeof vscode.lm.registerMcpServerDefinitionProvider !== 'function') {
    output.appendLine(t('当前编辑器版本太旧，无法注册给 AI 用的工具（需要 1.101+）。'));
    return;
  }
  mcpEmitter = new vscode.EventEmitter();
  context.subscriptions.push(
    vscode.lm.registerMcpServerDefinitionProvider(MCP_PROVIDER_ID, {
      onDidChangeMcpServerDefinitions: mcpEmitter.event,
      provideMcpServerDefinitions: async () => {
        const mcpPath = resolveMcpPath();
        if (!mcpPath || !fs.existsSync(mcpPath)) return [];
        if (typeof vscode.McpStdioServerDefinition !== 'function') return [];
        const env = { GPU_CONTROL_API_BASE: client.base() };
        const token = await client.resolveToken();
        if (token) env.GPU_CONTROL_API_TOKEN = token;
        return [new vscode.McpStdioServerDefinition('ComputeDeck', mcpPath, [], env)];
      },
    })
  );
}


// ── 插件内操作（不打开网页） ───────────────────────────────

let assistantPanel;
let assistantReady = false;
let assistantInitialPrompt = '';
let assistantConversation = '';

function buildAssistantPanelHtml(webview) {
  const nonce = `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
  const csp = [
    `default-src 'none'`,
    `style-src ${webview.cspSource} 'unsafe-inline'`,
    `script-src 'nonce-${nonce}'`,
  ].join('; ');
  return `<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8" />
  <meta http-equiv="Content-Security-Policy" content="${csp}" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>ComputeDeck 小助手</title>
  <style>
    :root {
      color-scheme: light dark;
      --accent: var(--vscode-button-background, #0f766e);
      --accent-ink: var(--vscode-button-foreground, #f0fdfa);
      --line: var(--vscode-widget-border, rgba(127,127,127,.28));
      --soft: color-mix(in srgb, var(--vscode-editor-background) 92%, var(--vscode-foreground) 5%);
      --muted: color-mix(in srgb, var(--vscode-foreground) 62%, transparent);
    }
    * { box-sizing: border-box; }
    body {
      margin: 0; height: 100vh; display: grid; grid-template-rows: auto minmax(0,1fr) auto;
      font: 13px/1.55 var(--vscode-font-family); color: var(--vscode-foreground);
      background: var(--vscode-editor-background);
    }
    header {
      display: flex; align-items: center; gap: 10px; padding: 12px 16px;
      border-bottom: 1px solid var(--line);
      background: color-mix(in srgb, var(--vscode-editor-background) 94%, var(--vscode-foreground) 3%);
    }
    .mark { width: 26px; height: 26px; border-radius: 8px; background: linear-gradient(135deg,#2dd4bf,#0f766e); display: grid; place-items: center; color: #fff; font-weight: 800; font-size: 12px; }
    .title { font-weight: 650; }
    .model { font-size: 11px; color: var(--muted); }
    header .spacer { flex: 1; }
    header button, .composer button {
      appearance: none; font: inherit; cursor: pointer; border-radius: 8px;
      border: 1px solid var(--line); background: transparent; color: var(--vscode-foreground); padding: 6px 10px;
    }
    header button.primary, .composer button.primary { border-color: transparent; background: var(--accent); color: var(--accent-ink); font-weight: 600; }
    main { overflow: auto; padding: 18px 16px 24px; }
    .thread { max-width: 760px; margin: 0 auto; display: grid; gap: 12px; }
    .empty { color: var(--muted); text-align: center; padding: 40px 16px; }
    .empty strong { display: block; color: var(--vscode-foreground); margin-bottom: 6px; }
    .msg { display: grid; gap: 4px; max-width: 92%; }
    .msg.user { justify-self: end; }
    .msg.bot { justify-self: start; }
    .bubble {
      padding: 10px 13px; border-radius: 14px; white-space: pre-wrap; word-break: break-word;
      border: 1px solid var(--line); background: var(--soft);
    }
    .msg.user .bubble { background: var(--accent); color: var(--accent-ink); border-color: transparent; }
    .role { font-size: 10.5px; color: var(--muted); padding: 0 3px; }
    .action {
      margin-top: 6px; border: 1px solid color-mix(in srgb, var(--vscode-editorWarning-foreground, #d97706) 40%, var(--line));
      border-left: 3px solid var(--vscode-editorWarning-foreground, #d97706);
      border-radius: 10px; padding: 10px 12px; display: grid; gap: 6px; background: var(--soft);
    }
    .action[data-risk="destructive"] { border-color: color-mix(in srgb, #dc2626 40%, var(--line)); border-left-color: #dc2626; }
    .action .summary { font-weight: 600; }
    .action .meta { font-size: 11px; color: var(--muted); }
    .action .row { display: flex; gap: 8px; flex-wrap: wrap; }
    .action button { appearance: none; font: inherit; cursor: pointer; border-radius: 7px; border: 1px solid var(--line); background: transparent; color: var(--vscode-foreground); padding: 5px 10px; }
    .action button.primary { border-color: transparent; background: var(--accent); color: var(--accent-ink); }
    .action[data-state="done"] { opacity: .65; }
    .action[data-state="failed"] { border-left-color: #dc2626; }
    footer { border-top: 1px solid var(--line); padding: 12px 16px 14px; background: color-mix(in srgb, var(--vscode-editor-background) 96%, var(--vscode-foreground) 2%); }
    .composer { max-width: 760px; margin: 0 auto; display: grid; grid-template-columns: minmax(0,1fr) auto; gap: 8px; align-items: end; }
    textarea {
      resize: none; min-height: 44px; max-height: 160px; width: 100%; padding: 10px 12px;
      border-radius: 10px; border: 1px solid var(--line); background: var(--vscode-input-background, transparent);
      color: var(--vscode-input-foreground, var(--vscode-foreground)); font: inherit;
    }
    .hint { max-width: 760px; margin: 7px auto 0; color: var(--muted); font-size: 11px; }
    .thinking { color: var(--muted); font-size: 12px; padding: 4px 2px; }
  </style>
</head>
<body>
  <header>
    <span class="mark">CD</span>
    <div><div class="title">ComputeDeck 助手</div><div class="model" id="model">加载中…</div></div>
    <span class="spacer"></span>
    <button type="button" id="new">新对话</button>
    <button type="button" id="settings">设置</button>
  </header>
  <main><div class="thread" id="thread">
    <div class="empty" id="empty"><strong>问集群相关的问题</strong>例如「哪些节点空闲？」「为什么健康分低？」<br>助手会调用只读工具；写操作会先给出确认卡。</div>
  </div></main>
  <footer>
    <div class="composer">
      <textarea id="input" rows="1" placeholder="输入问题，Enter 发送，Shift+Enter 换行"></textarea>
      <button type="button" class="primary" id="send">发送</button>
    </div>
    <p class="hint">写操作需要确认；破坏性操作需要确认两次。</p>
  </footer>
  <script nonce="${nonce}">
    const vscode = acquireVsCodeApi();
    const thread = document.getElementById('thread');
    const input = document.getElementById('input');
    const send = document.getElementById('send');
    const empty = document.getElementById('empty');
    let busy = false;
    let lastAssistant = null;

    function addMessage(role, text) {
      if (empty) empty.remove();
      const wrap = document.createElement('div');
      wrap.className = 'msg ' + role;
      const who = document.createElement('div');
      who.className = 'role';
      who.textContent = role === 'user' ? '你' : 'ComputeDeck';
      const bubble = document.createElement('div');
      bubble.className = 'bubble';
      bubble.textContent = text;
      wrap.append(who, bubble);
      thread.append(wrap);
      thread.scrollIntoView({ block: 'end' });
      if (role === 'bot') lastAssistant = wrap;
      return wrap;
    }

    function addAction(parent, action) {
      const card = document.createElement('div');
      card.className = 'action';
      card.dataset.id = action.id || '';
      card.dataset.risk = action.risk || 'write';
      card.dataset.state = 'pending';
      const summary = document.createElement('div');
      summary.className = 'summary';
      summary.textContent = action.summary || action.label || '执行该操作';
      const meta = document.createElement('div');
      meta.className = 'meta';
      meta.textContent = action.risk === 'destructive' ? '破坏性操作 · 需要二次确认' : '写操作 · 需要确认';
      const row = document.createElement('div');
      row.className = 'row';
      const allow = document.createElement('button');
      allow.className = 'primary';
      allow.textContent = '允许';
      const reject = document.createElement('button');
      reject.textContent = '拒绝';
      let armed = false;
      allow.addEventListener('click', () => {
        if (action.risk === 'destructive' && !armed) {
          armed = true;
          allow.textContent = '再次确认';
          meta.textContent = '再点一次确认执行；Esc 不是取消，请点拒绝。';
          return;
        }
        card.dataset.state = 'running';
        allow.disabled = true;
        reject.disabled = true;
        vscode.postMessage({ type: 'execute', actionId: action.id, confirm: action.risk === 'destructive' ? 'EXECUTE' : true });
      });
      reject.addEventListener('click', () => {
        card.dataset.state = 'rejected';
        allow.disabled = true;
        reject.disabled = true;
        vscode.postMessage({ type: 'reject', actionId: action.id });
      });
      row.append(allow, reject);
      card.append(summary, meta, row);
      parent.append(card);
    }

    function sendText() {
      const text = input.value.trim();
      if (!text || busy) return;
      input.value = '';
      input.style.height = 'auto';
      addMessage('user', text);
      busy = true;
      send.disabled = true;
      vscode.postMessage({ type: 'ask', text });
    }
    send.addEventListener('click', sendText);
    input.addEventListener('keydown', (ev) => {
      if (ev.key === 'Enter' && !ev.shiftKey) { ev.preventDefault(); sendText(); }
    });
    input.addEventListener('input', () => {
      input.style.height = 'auto';
      input.style.height = Math.min(160, input.scrollHeight) + 'px';
    });
    document.getElementById('new').addEventListener('click', () => vscode.postMessage({ type: 'new' }));
    document.getElementById('settings').addEventListener('click', () => vscode.postMessage({ type: 'settings' }));

    window.addEventListener('message', (ev) => {
      const msg = ev.data || {};
      if (msg.type === 'init') {
        if (msg.model) document.getElementById('model').textContent = msg.model;
        if (msg.prompt) { input.value = msg.prompt; sendText(); }
        return;
      }
      if (msg.type === 'thinking') {
        if (!lastAssistant || !lastAssistant.querySelector('.thinking')) {
          const wrap = addMessage('bot', '');
          const t = document.createElement('div');
          t.className = 'thinking';
          t.textContent = '正在查看集群…';
          wrap.querySelector('.bubble').append(t);
        }
        return;
      }
      if (msg.type === 'reply') {
        busy = false;
        send.disabled = false;
        const thinking = lastAssistant && lastAssistant.querySelector('.thinking');
        if (thinking) thinking.remove();
        const reply = msg.reply || msg.error || '（无回复）';
        if (lastAssistant && !lastAssistant.querySelector('.bubble').textContent) {
          lastAssistant.querySelector('.bubble').textContent = reply;
        } else {
          addMessage('bot', reply);
        }
        for (const action of msg.actions || []) addAction(lastAssistant || thread, action);
        thread.scrollIntoView({ block: 'end' });
        return;
      }
      if (msg.type === 'executed') {
        const card = thread.querySelector('.action[data-id="' + msg.actionId + '"]');
        if (card) {
          card.dataset.state = msg.ok ? 'done' : 'failed';
          const meta = card.querySelector('.meta');
          if (meta) meta.textContent = msg.ok ? '已执行' : ('失败：' + (msg.error || '未知错误'));
        }
        if (msg.reply) addMessage('bot', msg.reply);
        return;
      }
      if (msg.type === 'error') {
        busy = false;
        send.disabled = false;
        addMessage('bot', msg.error || '请求失败');
      }
    });
    vscode.postMessage({ type: 'ready' });
  </script>
</body>
</html>`;
}

function showAssistantPanel(prompt = '') {
  assistantInitialPrompt = prompt || '';
  if (assistantPanel) {
    assistantPanel.reveal(vscode.ViewColumn.Beside, false);
    if (assistantReady) {
      assistantPanel.webview.postMessage({ type: 'init', prompt: assistantInitialPrompt });
      assistantInitialPrompt = '';
    }
    return;
  }
  assistantPanel = vscode.window.createWebviewPanel(
    'computedeck.assistant',
    t('ComputeDeck 小助手'),
    vscode.ViewColumn.Beside,
    { enableScripts: true, retainContextWhenHidden: true }
  );
  assistantPanel.webview.html = buildAssistantPanelHtml(assistantPanel.webview);
  assistantPanel.onDidDispose(() => {
    assistantPanel = undefined;
    assistantReady = false;
  });
  assistantPanel.webview.onDidReceiveMessage(async (msg) => {
    if (!msg || !msg.type) return;
    if (msg.type === 'ready') {
      assistantReady = true;
      let model = '';
      try {
        const status = await client.assistantStatus();
        model = status.ready ? t('{0} · 已就绪', status.model || 'LLM') : t('未配置 API');
      } catch (_) {
        model = t('状态未知');
      }
      assistantPanel?.webview.postMessage({ type: 'init', model, prompt: assistantInitialPrompt });
      assistantInitialPrompt = '';
      return;
    }
    if (msg.type === 'settings') {
      await vscode.commands.executeCommand('computedeck.configureAssistant');
      return;
    }
    if (msg.type === 'new') {
      assistantConversation = '';
      if (assistantPanel) assistantPanel.webview.html = buildAssistantPanelHtml(assistantPanel.webview);
      assistantReady = false;
      return;
    }
    if (msg.type === 'ask') {
      const text = String(msg.text || '').trim();
      if (!text) return;
      assistantPanel?.webview.postMessage({ type: 'thinking' });
      try {
        const body = { message: text };
        if (assistantConversation) body.conversation_id = assistantConversation;
        const data = await client.assistantChat(body);
        if (data.conversation_id) assistantConversation = data.conversation_id;
        assistantPanel?.webview.postMessage({
          type: 'reply',
          reply: data.reply || data.error || '',
          actions: data.pending_actions || [],
        });
      } catch (err) {
        assistantPanel?.webview.postMessage({ type: 'error', error: err.message || String(err) });
      }
      return;
    }
    if (msg.type === 'execute' || msg.type === 'reject') {
      try {
        const data = await client.assistantExecute({
          conversation_id: assistantConversation,
          action_id: msg.actionId,
          ...(msg.type === 'reject' ? { stage: 'reject' } : { confirm: msg.confirm }),
        });
        assistantPanel?.webview.postMessage({
          type: 'executed',
          actionId: msg.actionId,
          ok: msg.type === 'reject' ? true : !!data.ok,
          reply: data.reply || '',
          error: data.error || '',
        });
        if (msg.type !== 'reject') provider.refresh(false).catch(() => {});
      } catch (err) {
        assistantPanel?.webview.postMessage({
          type: 'executed',
          actionId: msg.actionId,
          ok: false,
          error: err.message || String(err),
        });
      }
    }
  });
}

async function askAssistant(promptText = '') {
  try {
    const status = await client.assistantStatus();
    if (!status || !status.ready) {
      const choice = await vscode.window.showWarningMessage(
        t('小助手还没配置好，需要先填模型地址和密钥。'),
        t('配置小助手 API')
      );
      if (choice) await vscode.commands.executeCommand('computedeck.configureAssistant');
      return;
    }
  } catch (err) {
    vscode.window.showErrorMessage(t('读取小助手状态失败：{0}', err.message || err));
    return;
  }
  showAssistantPanel(promptText || '');
}

async function openNoticesNative() {
  let data = null;
  try {
    data = await client.noticesList();
  } catch (err) {
    vscode.window.showErrorMessage(t('读取公告失败：{0}', err.message || err));
    return;
  }
  const items = (data.items || []).filter((n) => !n.dismissed);
  if (!items.length) {
    vscode.window.showInformationMessage(t('没有未处理公告'));
    return;
  }
  const picked = await vscode.window.showQuickPick(
    items.map((n) => ({
      label: `${n.severity === 'error' ? '$(error)' : n.severity === 'warn' ? '$(warning)' : '$(info)'} ${n.title || n.id || t('公告')}`,
      description: n.detail || '',
      detail: [n.source, n.detail].filter(Boolean).join(' · '),
      notice: n,
    })),
    { placeHolder: t('公告（选中标记已读）') }
  );
  if (!picked) return;
  try {
    await client.noticesMark([picked.notice.id], true);
    provider.refresh(false).catch(() => {});
  } catch (_) {
    // 标记已读失败不影响阅读
  }
  output.show(true);
  output.appendLine(`\n[${picked.notice.title || picked.notice.id}]`);
  if (picked.notice.detail) output.appendLine(String(picked.notice.detail));
}

async function openQueueNative() {
  let data = null;
  try {
    data = await client.queueList();
  } catch (err) {
    vscode.window.showErrorMessage(t('读取队列失败：{0}', err.message || err));
    return;
  }
  const workloads = Array.isArray(data.workloads) ? data.workloads : [];
  const pending = workloads.filter((w) => w.status === 'pending');
  const items = [
    { label: '$(add) 提交 Job 到队列', action: 'submit' },
    { label: `$(play) 调度队列${pending.length ? `（${pending.length} 个等待）` : ''}`, action: 'schedule' },
    ...workloads.map((w) => ({
      label: `$(list-ordered) ${w.id} · ${w.job} · ${w.gpus || '?'} GPU`,
      description: `${w.status || ''}${w.selected_nodes?.length ? ` · ${w.selected_nodes.join(',')}` : ''}`,
      action: 'cancel',
      workload: w,
    })),
  ];
  const picked = await vscode.window.showQuickPick(items, { placeHolder: t('排队与占卡') });
  if (!picked) return;
  try {
    if (picked.action === 'submit') {
      const jobs = await client.jobs();
      const jobPick = await vscode.window.showQuickPick(
        jobs.map((j) => ({ label: j.name, description: j.distributed ? t('多机') : t('单机'), job: j })),
        { placeHolder: t('选择 Job') }
      );
      if (!jobPick) return;
      const gpusText = await vscode.window.showInputBox({
        prompt: t('需要多少 GPU'),
        value: String(jobPick.job.gpus_per_node || 1),
        validateInput: (v) => (/^\d+$/.test(String(v).trim()) ? '' : t('请输入整数')),
      });
      if (!gpusText) return;
      const nodes = await vscode.window.showInputBox({
        prompt: t('指定节点（逗号分隔，可留空自动选）'),
        placeHolder: 'gpu-1,gpu-2',
      });
      const note = await vscode.window.showInputBox({ prompt: t('备注（可留空）') });
      const result = await client.queueAction('submit', {
        job: jobPick.job.name,
        gpus: Number(gpusText),
        ...(nodes ? { nodes } : {}),
        ...(note ? { note } : {}),
      });
      output.show(true);
      output.appendLine(JSON.stringify(result, null, 2));
      vscode.window.showInformationMessage(t('已加入队列'));
    } else if (picked.action === 'schedule') {
      const result = await client.queueAction('schedule', {});
      output.show(true);
      output.appendLine(JSON.stringify(result, null, 2));
      vscode.window.showInformationMessage(t('已触发调度'));
    } else if (picked.action === 'cancel' && picked.workload) {
      await client.queueCancel(picked.workload.id);
      vscode.window.showInformationMessage(t('已取消 {0}', picked.workload.id));
    }
    await provider.refresh(false);
  } catch (err) {
    vscode.window.showErrorMessage(t('队列操作失败：{0}', err.message || err));
  }
}

async function openGatewayNative() {
  let status = null;
  try {
    status = await client.gatewayStatus();
  } catch (err) {
    vscode.window.showErrorMessage(t('读取入口状态失败：{0}', err.message || err));
    return;
  }
  const layering = status.layering || {};
  const edge = status.edge || {};
  const litellm = status.litellm || {};
  output.show(true);
  output.appendLine(`\n[入口] ${layering.summary || status.preferred || 'edge'}`);
  output.appendLine(`${t('Edge')}: ${edge.running ? t('运行中') : t('未运行')} · ${edge.listen || '-'}`);
  output.appendLine(`${t('LiteLLM')}: ${litellm.running ? t('运行中') : t('未运行')} · ${litellm.url || '-'}`);
  if (status.nginx) {
    output.appendLine(`nginx: ${status.nginx.in_sync ? t('配置已同步') : t('配置待应用')} · ${status.nginx.apply_path || t('未配置 apply_path')}`);
  }
  const picked = await vscode.window.showQuickPick(
    [
      { label: '$(refresh) 重新渲染入口产物', action: 'render' },
      { label: '$(play) 启动 Edge', action: 'start', kind: 'edge' },
      { label: '$(play) 启动 LiteLLM', action: 'start', kind: 'litellm' },
      { label: '$(stop) 停止全部入口', action: 'stop', kind: 'all' },
    ],
    { placeHolder: t('入口操作') }
  );
  if (!picked) return;
  try {
    await client.gatewayAction(picked.action, picked.kind);
    vscode.window.showInformationMessage(t('入口操作已执行'));
    await provider.refresh(false);
  } catch (err) {
    vscode.window.showErrorMessage(t('入口操作失败：{0}', err.message || err));
  }
}

async function openConfigFile() {
  const root = findControlRoot() || (vscode.workspace.workspaceFolders || [])[0]?.uri.fsPath;
  if (!root) {
    vscode.window.showWarningMessage(t('找不到主控仓库；请在主控仓库里打开工作区。'));
    return;
  }
  const local = path.join(root, 'config', 'local');
  const candidates = [
    { label: 'cluster.json', path: path.join(local, 'cluster.json') },
    { label: 'nodes.json', path: path.join(local, 'nodes.json') },
    { label: 'models.json', path: path.join(local, 'models.json') },
    { label: 'toolkits.json', path: path.join(local, 'toolkits.json') },
  ];
  try {
    for (const name of fs.readdirSync(path.join(local, 'jobs'))) {
      if (name.endsWith('.json')) {
        candidates.push({ label: `jobs/${name}`, path: path.join(local, 'jobs', name) });
      }
    }
  } catch (_) {
    // jobs 目录可选
  }
  const picked = await vscode.window.showQuickPick(
    candidates.map((item) => ({
      label: item.label,
      description: fs.existsSync(item.path) ? item.path : t('尚未创建'),
      path: item.path,
    })),
    { placeHolder: t('打开控制面配置文件') }
  );
  if (!picked) return;
  if (!fs.existsSync(picked.path)) {
    vscode.window.showWarningMessage(t('文件不存在：{0}', picked.path));
    return;
  }
  const doc = await vscode.workspace.openTextDocument(vscode.Uri.file(picked.path));
  await vscode.window.showTextDocument(doc, { preview: false });
}

// ── 命令与动作用户界面 ─────────────────────────────────────
function registerCommands(context) {
  const cmd = (name, fn) =>
    context.subscriptions.push(
      vscode.commands.registerCommand(name, async (...args) => {
        try {
          await fn(...args);
        } catch (err) {
          const message = err && err.message ? err.message : String(err);
          output.appendLine(`ERROR ${name}: ${message}`);
          if (err && err.status === 401) {
            const login = t('密码登录');
            const setToken = t('设置登录密钥');
            const choice = await vscode.window.showWarningMessage(message, login, setToken);
            if (choice === login) return vscode.commands.executeCommand('computedeck.login');
            if (choice === setToken) return vscode.commands.executeCommand('computedeck.setToken');
            return;
          }
          vscode.window.showErrorMessage(t('ComputeDeck: {0}', message));
        }
      })
    );

  cmd('computedeck.refresh', async () => {
    await provider.refresh(true);
  });
  cmd('computedeck.openWorkbench', async () => {
    showWorkbench();
  });
  cmd('computedeck.openAccess', async () => {
    const hash = '/#/actions';
    showAccessHub({
      hash,
      title: t('怎么用 ComputeDeck'),
      localUrl: controlUiUrl(hash),
      browserUrl: await resolveUiUrl(hash),
      tryEmbed: false,
    });
  });
  cmd('computedeck.openDashboard', async () => {
    const opened = await openControlUi('/#/actions', t('ComputeDeck · 看板'));
    if (opened === 'access') {
      vscode.window.showInformationMessage(
        t('网页看板没能直接打开。可以点「打开完整看板」，或用浏览器打开。')
      );
    }
  });
  cmd('computedeck.openPage', async () => {
    await pickControlPage();
  });
  cmd('computedeck.openAssistant', async () => {
    await askAssistant();
  });
  cmd('computedeck.configureAssistant', async () => {
    await configureAssistant();
  });
  cmd('computedeck.openNotices', async () => {
    await openNoticesNative();
  });
  cmd('computedeck.queue', async () => {
    await openQueueNative();
  });
  cmd('computedeck.gateway', async () => {
    await openGatewayNative();
  });
  cmd('computedeck.openConfig', async () => {
    await openConfigFile();
  });
  cmd('computedeck.openExternalBrowser', async () => {
    await openControlUiExternal('/#/actions');
  });
  cmd('computedeck.openWalkthrough', async () => {
    await openGettingStarted();
  });
  cmd('computedeck.login', async () => {
    const password = await vscode.window.showInputBox({
      prompt: t('主控登录密码'),
      password: true,
      ignoreFocusOut: true,
    });
    if (!password) return;
    await client.login(password);
    vscode.window.showInformationMessage(t('登录成功'));
    if (mcpEmitter) mcpEmitter.fire();
    await provider.refresh(true);
  });
  cmd('computedeck.setToken', async () => {
    const token = await vscode.window.showInputBox({
      prompt: t('粘贴登录密钥（主控上可自动读取，一般不用手填）'),
      password: true,
      ignoreFocusOut: true,
    });
    if (!token) return;
    await context.secrets.store(TOKEN_KEY, token.trim());
    await context.secrets.delete(COOKIE_KEY);
    vscode.window.showInformationMessage(t('登录密钥已保存'));
    if (mcpEmitter) mcpEmitter.fire();
    await provider.refresh(true);
  });
  cmd('computedeck.useLocalCredentials', async () => {
    const tokenPath = localTokenPath();
    const token = readLocalApiToken();
    if (!token) {
      vscode.window.showWarningMessage(
        t('没找到本机密钥（{0}）。请先打开「怎么用」，或在主控生成密钥。', tokenPath || '本机密钥文件')
      );
      return;
    }
    await context.secrets.store(TOKEN_KEY, token);
    await context.secrets.delete(COOKIE_KEY);
    if (mcpEmitter) mcpEmitter.fire();
    vscode.window.showInformationMessage(t('已使用本机密钥登录'));
    await provider.refresh(true);
  });
  cmd('computedeck.logout', async () => {
    await context.secrets.delete(TOKEN_KEY);
    await context.secrets.delete(COOKIE_KEY);
    vscode.window.showInformationMessage(t('已退出登录'));
    if (mcpEmitter) mcpEmitter.fire();
    await provider.refresh(true);
  });
  cmd('computedeck.runDoctor', async () => {
    const result = await vscode.window.withProgress(
      { location: vscode.ProgressLocation.Notification, title: t('ComputeDeck: 运行自检') },
      () => client.doctor()
    );
    showResult('doctor', result);
    vscode.window.showInformationMessage(result.ok ? t('自检通过') : t('自检发现问题，见输出'));
  });
  cmd('computedeck.liveness', async () => {
    const result = await vscode.window.withProgress(
      { location: vscode.ProgressLocation.Notification, title: t('ComputeDeck: 节点探活') },
      () => client.livenessCheck()
    );
    const s = result.summary || {};
    vscode.window.showInformationMessage(
      t('探活完成：在线 {0}，离线 {1}，未知 {2}', s.online ?? 0, s.offline ?? 0, s.unknown ?? 0)
    );
    showResult('liveness', result);
    await provider.refresh(true);
  });
  cmd('computedeck.testSSH', async () => {
    const node = await pickNode();
    const result = await vscode.window.withProgress(
      { location: vscode.ProgressLocation.Notification, title: t('ComputeDeck: 测试 SSH') },
      () => client.testSSH(node?.name)
    );
    showResult('test-ssh', result);
    vscode.window.showInformationMessage(
      t('SSH：{0}/{1} 通过', result.ok_count ?? 0, result.total ?? 0)
    );
  });
  cmd('computedeck.execPreset', async () => runPreset());
  cmd('computedeck.deployJob', async () => deployJob());
  cmd('computedeck.freeNode', async () => freeNode());
  cmd('computedeck.nodeMenu', async (node) => nodeMenu(node));
  cmd('computedeck.jobMenu', async (job) => jobMenu(job));
  cmd('computedeck.nodeTerminal', async (node) => openNodeTerminal(node));
  cmd('computedeck.nodePreset', async (node) => runPreset(node));
  cmd('computedeck.nodeFree', async (node) => freeNode(node));
  cmd('computedeck.nodeCordon', async (node) => toggleCordon(node));
  cmd('computedeck.jobStart', async (job) => {
    const target = jobFromArg(job) || (await pickJob());
    if (!target) return;
    showResult(
      t('启动 {0}', target.name),
      await client.jobAction(target.name, 'start')
    );
    await provider.refresh(true);
  });
  cmd('computedeck.jobStop', async (job) => {
    const target = jobFromArg(job) || (await pickJob());
    if (!target) return;
    showResult(
      t('停止 {0}', target.name),
      await client.jobAction(target.name, 'stop')
    );
    await provider.refresh(true);
  });
  cmd('computedeck.jobRestart', async (job) => {
    const target = jobFromArg(job) || (await pickJob());
    if (!target) return;
    showResult(
      t('重启 {0}', target.name),
      await client.jobAction(target.name, 'restart')
    );
    await provider.refresh(true);
  });
  cmd('computedeck.installMcp', async () => {
    const mcpPath = resolveMcpPath();
    if (!mcpPath || !fs.existsSync(mcpPath)) {
      vscode.window.showWarningMessage(
        t('没找到 AI 工具程序（{0}）。一般在主控仓库的 bin 目录里。', mcpPath || t('未配置'))
      );
      return;
    }
    const token = await client.resolveToken();
    if (!token) {
      vscode.window.showWarningMessage(t('给 AI 用的工具需要先登录。请先「使用本机密钥」或「密码登录」。'));
    }
    if (mcpEmitter) mcpEmitter.fire();
    vscode.window.showInformationMessage(t('已注册给 AI 用的工具（Copilot 等可以使用）'));
  });
}

function startPolling() {
  if (pollTimer) clearInterval(pollTimer);
  const seconds = Math.max(5, Number(config().get('pollIntervalSec', 30)) || 30);
  pollTimer = setInterval(() => {
    provider.refresh(false).catch(() => { });
  }, seconds * 1000);
}

// ── 生命周期 ───────────────────────────────────────────────
function activate(context) {
  extensionContext = context;
  output = vscode.window.createOutputChannel('ComputeDeck');
  client = new ComputeDeckClient(context);
  provider = new ClusterProvider(client);
  statusBar = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 100);
  context.subscriptions.push(output, statusBar);

  context.subscriptions.push(
    vscode.window.registerTreeDataProvider('computedeck.cluster', provider)
  );
  registerCommands(context);
  registerMcp(context);

  context.subscriptions.push(
    vscode.workspace.onDidChangeConfiguration((ev) => {
      if (ev.affectsConfiguration(SECTION)) {
        if (mcpEmitter) mcpEmitter.fire();
        startPolling();
        provider.refresh(false).catch(() => { });
      }
    })
  );

  startPolling();
  const controlRoot = findControlRoot();
  output.appendLine(t('ComputeDeck 已启动 · 地址 {0}', client.base()));
  if (controlRoot) {
    output.appendLine(t('已识别主控目录：{0}', controlRoot));
  }
  if (config().get('autoLocalAuth', true) && localTokenPath() && fs.existsSync(localTokenPath())) {
    output.appendLine(t('将自动使用本机密钥登录'));
  }
  provider.refresh(false).catch(() => { });

  if (!context.globalState.get('computedeck.guidePrompted')) {
    context.globalState.update('computedeck.guidePrompted', true);
    const open = t('怎么用');
    vscode.window
      .showInformationMessage(t('欢迎使用 ComputeDeck。第一次用可以先看「怎么用」。'), open)
      .then((choice) => {
        if (choice === open) return vscode.commands.executeCommand('computedeck.openAccess');
        return undefined;
      });
  }
}

function deactivate() {
  if (pollTimer) clearInterval(pollTimer);
}

module.exports = { activate, deactivate };
