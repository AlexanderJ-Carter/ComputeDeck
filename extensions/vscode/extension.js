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
          output.appendLine(t('已从本机控制面读取 API Token：{0}', localTokenPath() || '(local)'));
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
      throw new ApiError(t('需要登录 ComputeDeck（命令面板：密码登录 / 设置 API Token）'), 401, data);
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

  notices() {
    return this.request('/api/notices', { timeoutMs: 15000 });
  }

  authStatus() {
    return this.request('/api/auth/status', { timeoutMs: 10000 });
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
        makeItem(t('控制面未连接'), vscode.TreeItemCollapsibleState.None, {
          description: this.client.base(),
          tooltip: errText,
          iconPath: new vscode.ThemeIcon('error', new vscode.ThemeColor('errorForeground')),
          command: { command: 'computedeck.login', title: t('登录') },
        }),
      ];
      if (localToken && fs.existsSync(localToken)) {
        children.push(
          makeItem(t('使用本机 Token'), vscode.TreeItemCollapsibleState.None, {
            description: t('读取 config/local/secrets/ui_api_token'),
            iconPath: new vscode.ThemeIcon('key'),
            command: { command: 'computedeck.useLocalCredentials', title: t('使用本机 Token') },
          })
        );
      }
      children.push(
        makeItem(t('打开上手引导'), vscode.TreeItemCollapsibleState.None, {
          description: t('5 步了解插件'),
          iconPath: new vscode.ThemeIcon('book'),
          command: { command: 'computedeck.openWalkthrough', title: t('打开上手引导') },
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
      [t('租约'), String(summary.leases ?? leases.length), 'lock'],
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
        command: { command: 'computedeck.jobMenu', title: t('Job 操作'), arguments: [job] },
      })
    );
    sections.push(
      makeItem(t('Job'), vscode.TreeItemCollapsibleState.Collapsed, {
        description: t('{0} 个', jobRows.length),
        iconPath: new vscode.ThemeIcon('rocket'),
        children: jobRows.length ? jobRows : [makeItem(t('暂无 Job'), vscode.TreeItemCollapsibleState.None)],
      })
    );

    const queueRows = [
      makeItem(t('队列 pending'), vscode.TreeItemCollapsibleState.None, {
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
      makeItem(t('队列与租约'), vscode.TreeItemCollapsibleState.Collapsed, {
        description: t('{0} pending · {1} 租约', summary.queue_pending ?? 0, leases.length),
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
            : assistant.ready
              ? t('助手就绪')
              : t('{0} 节点 · {1} 空闲 GPU', summary.nodes ?? nodes.length, summary.gpus_free ?? 0),
        iconPath: new vscode.ThemeIcon('window'),
        command: { command: 'computedeck.openWorkbench', title: t('打开工作台') },
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
    statusBar.tooltip = String((error && error.message) || t('点击登录或刷新'));
    statusBar.backgroundColor = new vscode.ThemeColor('statusBarItem.errorBackground');
    statusBar.command = 'computedeck.login';
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
  statusBar.text = `${warn ? '$(warning)' : '$(pulse)'} ${t('CD {0}/{1} 节点 · {2}/{3} 空闲', online, total, free, gpuTotal)}${unread > 0 ? t(' · {0} 公告', unread) : ''
    }`;
  statusBar.tooltip = new vscode.MarkdownString(
    [
      t('**ComputeDeck**'),
      '',
      t('在线 {0} / {1}，离线 {2}，GPU 空闲 {3}/{4}', online, total, offline, free, gpuTotal),
      unread > 0 ? t('未读监管公告：{0}', unread) : t('监管公告：无未读'),
      assistantReady
        ? t('Web 小助手：就绪（{0}）', data.assistant?.model || data.assistant?.mode || 'llm')
        : t('Web 小助手：未就绪'),
      '',
      t('点击打开工作台；看板 / 助手 / 公告都在编辑器窗口内打开'),
    ].join('\n')
  );
  statusBar.backgroundColor = warn
    ? new vscode.ThemeColor('statusBarItem.warningBackground')
    : undefined;
  statusBar.command = 'computedeck.openWorkbench';
}

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
  { id: 'notices', label: '监管公告', hash: '/#/notices' },
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
    .pill {
      display: inline-flex; align-items: center; gap: 6px;
      border: 1px solid var(--line); border-radius: 999px; padding: 3px 10px;
      background: var(--soft); font-size: 12px;
    }
    .dot { width: 7px; height: 7px; border-radius: 50%; background: #94a3b8; }
    .dot.on { background: #16a34a; } .dot.off { background: #dc2626; }
    .actions { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 14px; }
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
      <div class="pill"><span class="dot ${state.apiOk ? 'on' : 'off'}"></span>${state.apiOk ? '已经连上，可以直接用' : '还没连上，先看下面第 1 步'}</div>
      <h1 style="margin-top:12px">怎么用 ComputeDeck</h1>
      <p class="sub">不用记术语。日常就三件事：看机器、开大屏、需要时再登录。</p>
      <div class="actions" id="topActions"></div>
    </header>

    <section class="card">
      <h2>日常怎么用</h2>
      <ol>
        <li><strong>看集群</strong>：左侧 ComputeDeck，或点「打开工作台」。</li>
        <li><strong>看完整大屏</strong>：点「打开完整看板」（会在编辑器里打开网页）。</li>
        <li><strong>连不上大屏</strong>：点「用浏览器打开」；左侧还能刷集群就说明总控是好的。</li>
      </ol>
    </section>

    <section class="card">
      <h2>三种机器，各干什么</h2>
      <ul>
        <li><strong>总控机</strong>（你现在多半就在这台）：管调度、网页、插件。在这台电脑上用插件，一般<strong>不用输密码</strong>。</li>
        <li><strong>GPU 干活机</strong>：真正跑任务的机器。你不用单独登录它们，总控会自动连过去。</li>
        <li><strong>你自己的笔记本</strong>（如果没直接坐在总控上）：先用远程桌面 / CodeLab / SSH 进到总控，再按上面两步用。</li>
      </ul>
    </section>

    <section class="card">
      <h2>什么时候要输密码？</h2>
      <ul>
        <li><strong>插件 / 侧栏 / 工作台</strong>：在总控上打开仓库时，多数情况自动登录。${state.passwordlessPlugin ? '你现在就是这种。' : '若提示未连接，点「使用本机密钥」或「密码登录」。'}</li>
        <li><strong>完整大屏网页</strong>：第一次可能要登录一次；之后一段时间内不用再输。</li>
        <li><strong>GPU 干活机</strong>：不用你输网页密码。</li>
      </ul>
      <p class="muted">当前状态：${state.apiOk ? '插件已连上' : '插件未连上'} · ${state.passwordlessPlugin ? '免密可用' : '可能需要登录一次'}</p>
    </section>

    ${
      state.tryEmbed && safeEmbed
        ? `<section class="embed-wrap"><iframe src="${safeEmbed}" title="ComputeDeck"></iframe></section>`
        : ''
    }
  </div>
  <script nonce="${nonce}">
    const vscode = acquireVsCodeApi();
    const actions = document.getElementById('topActions');
    function btn(label, command, primary) {
      const b = document.createElement('button');
      b.textContent = label;
      if (primary) b.className = 'primary';
      b.addEventListener('click', () => vscode.postMessage({ command }));
      actions.append(b);
    }
    btn('打开完整看板', 'simpleBrowser', true);
    btn('打开工作台', 'openWorkbench');
    btn('用浏览器打开', 'openExternal');
    btn('五分钟上手', 'openWalkthrough');
    btn('刷新', 'refresh');
  </script>
</body>
</html>`;
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
  const panelTitle = opts.title || pageTitleForHash(opts.hash);
  if (controlUiPanel) {
    controlUiPanel.title = panelTitle;
    controlUiPanel.reveal(vscode.ViewColumn.Beside, false);
    controlUiPanel.webview.html = buildAccessHubHtml(controlUiPanel.webview, state);
    controlUiPanel._cdAccess = state;
    return;
  }
  controlUiPanel = vscode.window.createWebviewPanel(
    'computedeck.controlUi',
    panelTitle,
    vscode.ViewColumn.Beside,
    { enableScripts: true, retainContextWhenHidden: true }
  );
  controlUiPanel._cdAccess = state;
  controlUiPanel.webview.html = buildAccessHubHtml(controlUiPanel.webview, state);
  controlUiPanel.onDidDispose(() => {
    controlUiPanel = undefined;
  });
  controlUiPanel.webview.onDidReceiveMessage(async (msg) => {
    const cur = controlUiPanel?._cdAccess || state;
    if (!msg || !msg.command) return;
    if (msg.command === 'simpleBrowser') {
      try {
        await vscode.commands.executeCommand('simpleBrowser.show', cur.browserUrl || cur.localUrl);
      } catch (_) {
        await vscode.env.openExternal(vscode.Uri.parse(cur.browserUrl || cur.localUrl));
      }
      return;
    }
    if (msg.command === 'openExternal') {
      await vscode.env.openExternal(vscode.Uri.parse(cur.browserUrl || cur.localUrl));
      return;
    }
    if (msg.command === 'copyUrl') {
      await vscode.env.clipboard.writeText(cur.browserUrl || cur.localUrl || '');
      vscode.window.showInformationMessage(t('已复制控制面 URL'));
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

async function openControlUi(hash = '', title) {
  const localUrl = controlUiUrl(hash);
  const browserUrl = await resolveUiUrl(hash);
  const mode = String(config().get('uiOpenMode', 'auto') || 'auto');
  const panelTitle = title || pageTitleForHash(hash);

  if (mode === 'external') {
    await vscode.env.openExternal(vscode.Uri.parse(browserUrl));
    return 'external';
  }

  if (mode === 'simpleBrowser') {
    try {
      await vscode.commands.executeCommand('simpleBrowser.show', browserUrl);
      return 'simpleBrowser';
    } catch (_) {
      showAccessHub({ hash, title: panelTitle, localUrl, browserUrl, tryEmbed: false });
      return 'access';
    }
  }

  if (mode === 'embed') {
    showAccessHub({ hash, title: panelTitle, localUrl, browserUrl, tryEmbed: true });
    return 'embed';
  }

  // auto / access：CodeLab Web 默认进接入页，避免坏掉的 iframe。
  if (mode === 'access' || mode === 'auto' || isWebIde()) {
    if (mode === 'auto' && !isWebIde()) {
      try {
        await vscode.commands.executeCommand('simpleBrowser.show', browserUrl);
        return 'simpleBrowser';
      } catch (_) { }
    }
    showAccessHub({ hash, title: panelTitle, localUrl, browserUrl, tryEmbed: false });
    return 'access';
  }

  showAccessHub({ hash, title: panelTitle, localUrl, browserUrl, tryEmbed: false });
  return 'access';
}

async function openControlUiExternal(hash = '') {
  await vscode.env.openExternal(vscode.Uri.parse(await resolveUiUrl(hash)));
}

async function pickControlPage() {
  const picked = await vscode.window.showQuickPick(
    CONTROL_PAGES.map((p) => ({ label: p.label, description: p.hash, page: p })),
    { placeHolder: t('在编辑器窗口打开控制面页面') }
  );
  if (!picked) return;
  await openControlUi(picked.page.hash, t('ComputeDeck · {0}', picked.page.label));
}

const GUIDE_STEPS = [
  {
    id: 'connect',
    title: '1. 连接控制面',
    file: '01-connect.md',
    actions: [
      { label: '使用本机 Token', command: 'computedeck.useLocalCredentials' },
      { label: '密码登录', command: 'computedeck.login' },
      { label: '打开设置', command: 'workbench.action.openSettings', args: ['computedeck'] },
      { label: '刷新集群', command: 'computedeck.refresh' },
    ],
  },
  {
    id: 'workbench',
    title: '2. 工作台与窗口',
    file: '02-cluster.md',
    actions: [
      { label: '打开工作台', command: 'computedeck.openWorkbench' },
      { label: '打开看板', command: 'computedeck.openDashboard' },
      { label: '选择页面…', command: 'computedeck.openPage' },
    ],
  },
  {
    id: 'nodes',
    title: '3. 节点终端与诊断',
    file: '03-nodes.md',
    actions: [
      { label: '节点探活', command: 'computedeck.liveness' },
      { label: '节点操作', command: 'computedeck.nodeMenu' },
      { label: '测试 SSH', command: 'computedeck.testSSH' },
    ],
  },
  {
    id: 'jobs',
    title: '4. 部署与启停 Job',
    file: '04-jobs.md',
    actions: [
      { label: '部署 Job', command: 'computedeck.deployJob' },
      { label: '运行自检', command: 'computedeck.runDoctor' },
    ],
  },
  {
    id: 'mcp',
    title: '5. MCP · 小助手 · 公告',
    file: '05-mcp.md',
    actions: [
      { label: '注册 MCP', command: 'computedeck.installMcp' },
      { label: '打开小助手', command: 'computedeck.openAssistant' },
      { label: '打开监管公告', command: 'computedeck.openNotices' },
    ],
  },
];

function readGuideMarkdown(fileName) {
  if (!extensionContext) return '';
  const uri = vscode.Uri.joinPath(extensionContext.extensionUri, 'media', 'walkthrough', fileName);
  try {
    return fs.readFileSync(uri.fsPath, 'utf8');
  } catch (_) {
    return '';
  }
}

function markdownToGuideHtml(md) {
  const escaped = String(md || '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
  const lines = escaped.split(/\r?\n/);
  const html = [];
  let inList = false;
  const flushList = () => {
    if (inList) {
      html.push('</ul>');
      inList = false;
    }
  };
  for (const line of lines) {
    const heading = line.match(/^(#{1,3})\s+(.*)$/);
    if (heading) {
      flushList();
      const level = heading[1].length;
      html.push(`<h${level}>${heading[2]}</h${level}>`);
      continue;
    }
    const li = line.match(/^[-*]\s+(.*)$/);
    if (li) {
      if (!inList) {
        html.push('<ul>');
        inList = true;
      }
      html.push(`<li>${li[1].replace(/`([^`]+)`/g, '<code>$1</code>')}</li>`);
      continue;
    }
    if (!line.trim()) {
      flushList();
      continue;
    }
    flushList();
    html.push(
      `<p>${line
        .replace(/`([^`]+)`/g, '<code>$1</code>')
        .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
        .replace(
          /\[([^\]]+)\]\(command:([^)]+)\)/g,
          '<button type="button" data-cmd="$2">$1</button>'
        )}</p>`
    );
  }
  flushList();
  return html.join('\n');
}

function buildGuideHtml(webview, stepIndex) {
  const nonce = `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
  const idx = Math.max(0, Math.min(GUIDE_STEPS.length - 1, Number(stepIndex) || 0));
  const step = GUIDE_STEPS[idx];
  const body = markdownToGuideHtml(readGuideMarkdown(step.file));
  const nav = GUIDE_STEPS.map(
    (s, i) =>
      `<button type="button" class="step${i === idx ? ' active' : ''}" data-step="${i}">${s.title}</button>`
  ).join('');
  const actions = (step.actions || [])
    .map(
      (a) =>
        `<button type="button" class="primary" data-cmd="${a.command}" data-args='${JSON.stringify(a.args || [])}'>${a.label}</button>`
    )
    .join('');
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
  <title>ComputeDeck 上手指南</title>
  <style>
    :root { color-scheme: light dark; }
    body {
      margin: 0; display: grid; grid-template-columns: 220px minmax(0, 1fr);
      min-height: 100vh; font: 13px/1.5 var(--vscode-font-family);
      color: var(--vscode-foreground); background: var(--vscode-editor-background);
    }
    aside {
      border-right: 1px solid var(--vscode-widget-border, rgba(127,127,127,.35));
      padding: 14px 10px; background: color-mix(in srgb, var(--vscode-editor-background) 92%, var(--vscode-foreground) 4%);
    }
    aside h1 { margin: 0 0 10px; font-size: 14px; padding: 0 6px; }
    aside .step {
      display: block; width: 100%; text-align: left; margin: 0 0 6px;
      border: 0; background: transparent; color: inherit; border-radius: 6px;
      padding: 8px 10px; cursor: pointer; font: inherit;
    }
    aside .step:hover { background: var(--vscode-list-hoverBackground); }
    aside .step.active {
      background: var(--vscode-list-activeSelectionBackground);
      color: var(--vscode-list-activeSelectionForeground);
    }
    main { padding: 18px 22px 28px; overflow: auto; }
    main h1, main h2, main h3 { margin: 0.4em 0 0.35em; }
    main p, main li { margin: 0.35em 0; }
    main ul { padding-left: 1.2em; }
    main code {
      font-family: var(--vscode-editor-font-family, monospace);
      font-size: 12px; padding: 0 4px; border-radius: 4px;
      background: color-mix(in srgb, var(--vscode-editor-background) 80%, var(--vscode-foreground) 12%);
    }
    .actions { display: flex; flex-wrap: wrap; gap: 8px; margin: 16px 0 8px; }
    button.primary, main button {
      appearance: none; border: 1px solid var(--vscode-button-border, transparent);
      background: var(--vscode-button-background); color: var(--vscode-button-foreground);
      border-radius: 6px; padding: 6px 12px; font: inherit; cursor: pointer;
    }
    main button {
      background: var(--vscode-button-secondaryBackground);
      color: var(--vscode-button-secondaryForeground);
      margin: 0 4px 4px 0;
    }
    .pager { display: flex; gap: 8px; margin-top: 18px; }
    .hint { opacity: .7; font-size: 12px; margin-top: 10px; }
  </style>
</head>
<body>
  <aside>
    <h1>上手指南</h1>
    ${nav}
  </aside>
  <main>
    <div id="body">${body}</div>
    <div class="actions">${actions}</div>
    <div class="pager">
      <button type="button" class="primary" id="prev" ${idx === 0 ? 'disabled' : ''}>上一步</button>
      <button type="button" class="primary" id="next">${idx >= GUIDE_STEPS.length - 1 ? '完成' : '下一步'}</button>
    </div>
    <p class="hint">支持原生 Walkthrough 的环境也可从欢迎页打开同一套步骤；CodeLab 下用本窗口更稳。</p>
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
    t('ComputeDeck 上手指南'),
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
      vscode.window.showInformationMessage(t('上手指南已完成。可随时从侧栏或命令面板再打开。'));
      guidePanel?.dispose();
      return;
    }
    if (msg.command === 'run' && msg.id) {
      await vscode.commands.executeCommand(msg.id, ...(Array.isArray(msg.args) ? msg.args : []));
    }
  });
}

async function openGettingStarted() {
  const walkthroughId = extensionContext
    ? `${extensionContext.extension.id}#computedeck.getStarted`
    : 'computedeck.computedeck-vscode#computedeck.getStarted';
  try {
    await vscode.commands.executeCommand('workbench.action.openWalkthrough', walkthroughId, false);
    if (extensionContext) {
      await extensionContext.globalState.update('computedeck.guideOpened', true);
    }
    return 'walkthrough';
  } catch (_) {
    // CodeLab / 部分发行版没有 Walkthrough UI。
  }
  showGuide(0);
  if (extensionContext) {
    await extensionContext.globalState.update('computedeck.guideOpened', true);
  }
  return 'guide';
}

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
      model: assistant.model || assistant.mode || '',
      base_url: assistant.base_url || '',
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
      <div class="section-h"><h2>控制面页面</h2><span class="more">在编辑器窗口打开</span></div>
      <div class="pages" id="pages"></div>
    </section>

    <section class="grid-2">
      <div class="panel">
        <h3>连接与 Agent</h3>
        <div class="status-rows" id="aiBlock"></div>
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
        <p class="foot">危险操作会二次确认；详情在输出通道「ComputeDeck」。</p>
      </div>
    </section>

    <section class="notices">
      <div class="notices-h">
        <h3>监管公告</h3>
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
        : (state.error || '检查 apiBase / Token');
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
        detail.textContent = '完整大屏点「打开完整看板」；打不开再用「用浏览器打开」。';
        accessBox.append(title, detail);
      }
      el('kpiMore').textContent = connected
        ? ('空闲 GPU ' + (s.gpus_free ?? 0) + ' · 租约 ' + (s.leases ?? 0))
        : '';
      el('kpis').replaceChildren(
        kpiCard('在线节点', (s.nodes_online ?? 0) + '/' + (s.nodes ?? 0), '集群可达节点', offline > 0 ? 'alert' : ''),
        kpiCard('空闲 GPU', (s.gpus_free ?? 0) + '/' + (s.gpus_total ?? 0), '可调度显卡', Number(s.gpus_free || 0) === 0 ? 'alert' : ''),
        kpiCard('离线', String(offline), '需探活或检修', offline > 0 ? 'bad' : ''),
        kpiCard('未读公告', String(unread), '监管与健康提示', unread > 0 ? 'alert' : '')
      );
      const pages = el('pages');
      pages.replaceChildren();
      for (const p of PAGES) {
        pages.append(btn(p.label, { command: 'openHash', hash: p.hash, title: p.label }));
      }
      const a = state.assistant || {};
      const m = state.mcp || {};
      el('aiBlock').replaceChildren(
        statusRow('小助手', a.ready ? ('就绪 · ' + (a.model || 'llm')) : (a.enabled ? '已启用，未就绪' : '未启用'), a.base_url || a.network_note || '', a.ready ? true : false),
        statusRow('MCP', m.ready ? '可执行文件就绪' : '未找到 gpu-control-mcp', m.path || '', m.ready ? true : false),
        statusRow('Token', m.tokenReady ? '就绪' : '需要配置', m.tokenReady ? '本机或 SecretStorage' : '可使用本机 Token / 设置 Token', m.tokenReady ? true : false)
      );
      el('authActions').replaceChildren(
        btn(m.tokenReady ? '使用本机 Token' : '设置 Token', m.tokenReady ? 'useLocalCredentials' : 'setToken'),
        btn('注册 MCP', 'installMcp'),
        btn('密码登录', 'login', 'ghost'),
        btn('设置', 'openSettings', 'ghost')
      );
      el('opsDiag').replaceChildren(
        btn('运行自检', 'runDoctor'),
        btn('节点探活', 'liveness'),
        btn('测试 SSH', 'testSSH'),
        btn('只读诊断', 'execPreset')
      );
      el('opsMutate').replaceChildren(
        btn('部署 Job', 'deployJob', 'primary'),
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
      openNotices: 'computedeck.openNotices',
      openWalkthrough: 'computedeck.openWalkthrough',
      openGuide: 'computedeck.openGuide',
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
    vscode.window.showWarningMessage(t('ComputeDeck: 没有已安装 Job'));
    return null;
  }
  const picked = await vscode.window.showQuickPick(
    jobs.map((j) => ({ label: j.name, description: t('{0} GPU', j.gpus_per_node ?? '?'), job: j })),
    { placeHolder: t('选择 Job') }
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
    { label: t('打开 Web 节点详情'), action: 'dashboard', icon: 'globe' },
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
    t('腾空 {0}？会停止 Job、结束 GPU 进程并释放租约。', node.name),
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
    output.appendLine(t('当前 VS Code 不支持 MCP Server provider（需要 1.101+）。'));
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
            const setToken = t('设置 Token');
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
    await openControlUi('/#/actions', t('ComputeDeck · 看板'));
  });
  cmd('computedeck.openPage', async () => {
    await pickControlPage();
  });
  cmd('computedeck.openAssistant', async () => {
    await openControlUi('/#/assistant', t('ComputeDeck · 小助手'));
  });
  cmd('computedeck.openNotices', async () => {
    await openControlUi('/#/notices', t('ComputeDeck · 监管公告'));
  });
  cmd('computedeck.openExternalBrowser', async () => {
    await openControlUiExternal('/#/actions');
  });
  cmd('computedeck.openWalkthrough', async () => {
    await openGettingStarted();
  });
  cmd('computedeck.openGuide', async () => {
    showGuide(0);
    await context.globalState.update('computedeck.guideOpened', true);
  });
  cmd('computedeck.login', async () => {
    const password = await vscode.window.showInputBox({
      prompt: t('ComputeDeck 控制面密码'),
      password: true,
      ignoreFocusOut: true,
    });
    if (!password) return;
    await client.login(password);
    vscode.window.showInformationMessage(t('ComputeDeck 登录成功'));
    if (mcpEmitter) mcpEmitter.fire();
    await provider.refresh(true);
  });
  cmd('computedeck.setToken', async () => {
    const token = await vscode.window.showInputBox({
      prompt: t('API Token（gpu-control auth issue-token 生成）'),
      password: true,
      ignoreFocusOut: true,
    });
    if (!token) return;
    await context.secrets.store(TOKEN_KEY, token.trim());
    await context.secrets.delete(COOKIE_KEY);
    vscode.window.showInformationMessage(t('ComputeDeck API Token 已保存'));
    if (mcpEmitter) mcpEmitter.fire();
    await provider.refresh(true);
  });
  cmd('computedeck.useLocalCredentials', async () => {
    const tokenPath = localTokenPath();
    const token = readLocalApiToken();
    if (!token) {
      vscode.window.showWarningMessage(
        t(
          '未找到本机 Token（{0}）。在控制面仓库执行 gpu-control auth issue-token。',
          tokenPath || 'config/local/secrets/ui_api_token'
        )
      );
      return;
    }
    await context.secrets.store(TOKEN_KEY, token);
    await context.secrets.delete(COOKIE_KEY);
    if (mcpEmitter) mcpEmitter.fire();
    vscode.window.showInformationMessage(t('已加载本机 API Token'));
    await provider.refresh(true);
  });
  cmd('computedeck.logout', async () => {
    await context.secrets.delete(TOKEN_KEY);
    await context.secrets.delete(COOKIE_KEY);
    vscode.window.showInformationMessage(t('ComputeDeck 凭据已清除'));
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
        t('未找到 {0}；请在设置 computedeck.mcpPath 指向 bin/gpu-control-mcp', mcpPath || t('(未配置)'))
      );
      return;
    }
    const token = await client.resolveToken();
    if (!token) {
      vscode.window.showWarningMessage(
        t('MCP 走 HTTP 需要 API Token；请先执行「ComputeDeck: 设置 API Token」')
      );
    }
    if (mcpEmitter) mcpEmitter.fire();
    vscode.window.showInformationMessage(t('ComputeDeck MCP Server 已注册（VS Code / Copilot 可使用）'));
  });
}

function startPolling() {
  if (pollTimer) clearInterval(pollTimer);
  const seconds = Math.max(5, Number(config().get('pollIntervalSec', 30)) || 30);
  pollTimer = setInterval(() => {
    provider.refresh(false).catch(() => { });
  }, seconds * 1000);
}

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
  output.appendLine(t('ComputeDeck 已激活 · API {0}', client.base()));
  if (controlRoot) {
    output.appendLine(t('检测到本机控制面目录：{0}', controlRoot));
  }
  if (config().get('autoLocalAuth', true) && localTokenPath() && fs.existsSync(localTokenPath())) {
    output.appendLine(t('将自动使用本机 API Token（无需粘贴）'));
  }
  provider.refresh(false).catch(() => { });

  if (!context.globalState.get('computedeck.guidePrompted')) {
    context.globalState.update('computedeck.guidePrompted', true);
    const open = t('怎么用');
    vscode.window
      .showInformationMessage(t('欢迎使用 ComputeDeck。点「怎么用」看三步就会。'), open)
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
