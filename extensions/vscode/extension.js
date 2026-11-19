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
      const [overview, live, jobs, gateway, assistant, notices] = await Promise.all([
        this.client.overview(true),
        this.client.liveness().catch(() => null),
        this.client.jobs().catch(() => []),
        this.client.gatewayStatus().catch(() => null),
        this.client.assistantStatus().catch(() => null),
        this.client.notices().catch(() => null),
      ]);
      this.data = { overview, live, jobs, gateway, assistant, notices };
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
      makeItem(t('节点'), vscode.TreeItemCollapsibleState.Expanded, {
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
      t('点击打开工作台；完整看板可用「打开 Web 控制面」'),
    ].join('\n')
  );
  statusBar.backgroundColor = warn
    ? new vscode.ThemeColor('statusBarItem.warningBackground')
    : undefined;
  statusBar.command = 'computedeck.openWorkbench';
}

async function openControlUi(hash = '') {
  const url = `${client.base()}${hash || ''}`;
  try {
    await vscode.commands.executeCommand('simpleBrowser.show', url);
    return;
  } catch (_) {
    // Simple Browser 在部分发行版不可用，回落系统浏览器。
  }
  await vscode.env.openExternal(vscode.Uri.parse(url));
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
  return {
    connected: Boolean(data) && !error,
    error: error ? String(error.message || error) : '',
    base: client.base(),
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
  const nonce = String(Date.now());
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
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>ComputeDeck 工作台</title>
  <style>
    :root {
      color-scheme: light dark;
      --gap: 14px;
      --radius: 10px;
    }
    body {
      margin: 0;
      padding: 20px 22px 28px;
      font: 13px/1.45 var(--vscode-font-family);
      color: var(--vscode-foreground);
      background: var(--vscode-editor-background);
    }
    h1 {
      margin: 0 0 4px;
      font-size: 18px;
      font-weight: 650;
    }
    .sub {
      margin: 0 0 18px;
      opacity: 0.75;
    }
    .row {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin-bottom: 16px;
    }
    button, .linkish {
      appearance: none;
      border: 1px solid var(--vscode-button-border, transparent);
      background: var(--vscode-button-secondaryBackground);
      color: var(--vscode-button-secondaryForeground);
      border-radius: 6px;
      padding: 6px 12px;
      font: inherit;
      cursor: pointer;
    }
    button.primary {
      background: var(--vscode-button-background);
      color: var(--vscode-button-foreground);
    }
    button:hover { filter: brightness(1.05); }
    .grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
      gap: var(--gap);
      margin-bottom: 18px;
    }
    .card, .panel {
      border: 1px solid var(--vscode-widget-border, rgba(127,127,127,.35));
      border-radius: var(--radius);
      padding: 12px 14px;
      background: color-mix(in srgb, var(--vscode-editor-background) 88%, var(--vscode-foreground) 4%);
    }
    .card .label { opacity: 0.7; font-size: 12px; }
    .card .value { margin-top: 4px; font-size: 22px; font-weight: 650; }
    .panel h2 {
      margin: 0 0 10px;
      font-size: 14px;
      font-weight: 650;
    }
    .muted { opacity: 0.7; }
    .warn { color: var(--vscode-editorWarning-foreground); }
    .ok { color: var(--vscode-testing-iconPassed, #3fb950); }
    .list { margin: 0; padding: 0; list-style: none; }
    .list li {
      padding: 8px 0;
      border-top: 1px solid var(--vscode-widget-border, rgba(127,127,127,.25));
    }
    .list li:first-child { border-top: none; padding-top: 0; }
    .actions { display: flex; flex-wrap: wrap; gap: 8px; }
    .two {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
      gap: var(--gap);
    }
  </style>
</head>
<body>
  <h1>ComputeDeck 工作台</h1>
  <p class="sub" id="subtitle">加载中…</p>
  <div class="row" id="topActions"></div>
  <div class="grid" id="kpis"></div>
  <div class="two">
    <section class="panel">
      <h2>助手 · 公告 · MCP</h2>
      <div id="aiBlock" class="muted">—</div>
    </section>
    <section class="panel">
      <h2>常用操作</h2>
      <div class="actions" id="ops"></div>
    </section>
  </div>
  <section class="panel" style="margin-top:14px">
    <h2>监管公告</h2>
    <ul class="list" id="notices"></ul>
  </section>
  <script nonce="${nonce}">
    const vscode = acquireVsCodeApi();
    const el = (id) => document.getElementById(id);
    function btn(label, cmd, primary) {
      const b = document.createElement('button');
      b.textContent = label;
      if (primary) b.className = 'primary';
      b.addEventListener('click', () => vscode.postMessage({ command: cmd }));
      return b;
    }
    function render(state) {
      const s = state.summary || {};
      el('subtitle').textContent = state.connected
        ? (state.base || '') + ' · 侧栏保留节点/Job 导航'
        : (state.error || '未连接控制面');
      const top = el('topActions');
      top.replaceChildren(
        btn('刷新', 'refresh', true),
        btn('打开 Web 看板', 'openDashboard'),
        btn('Web 小助手', 'openAssistant'),
        btn('监管公告页', 'openNotices'),
        btn('上手引导', 'openWalkthrough')
      );
      const kpis = [
        ['在线节点', (s.nodes_online ?? 0) + '/' + (s.nodes ?? 0)],
        ['空闲 GPU', (s.gpus_free ?? 0) + '/' + (s.gpus_total ?? 0)],
        ['离线', String(s.nodes_offline ?? 0)],
        ['队列 pending', String(s.queue_pending ?? 0)],
        ['租约', String(s.leases ?? 0)],
        ['未读公告', String((state.notices && state.notices.unread) || 0)],
      ];
      el('kpis').replaceChildren(...kpis.map(([label, value]) => {
        const d = document.createElement('div');
        d.className = 'card';
        d.innerHTML = '<div class="label"></div><div class="value"></div>';
        d.children[0].textContent = label;
        d.children[1].textContent = value;
        return d;
      }));
      const a = state.assistant || {};
      const m = state.mcp || {};
      const ai = el('aiBlock');
      ai.replaceChildren();
      const p1 = document.createElement('p');
      p1.innerHTML = '';
      p1.append(
        document.createTextNode('小助手：'),
      );
      const span = document.createElement('span');
      span.className = a.ready ? 'ok' : 'muted';
      span.textContent = a.ready
        ? ('就绪' + (a.model ? ' · ' + a.model : ''))
        : (a.enabled ? '已启用，尚未就绪' : '未启用');
      p1.append(span);
      const p2 = document.createElement('p');
      p2.className = 'muted';
      p2.textContent = a.base_url || a.network_note || '';
      const p3 = document.createElement('p');
      p3.textContent = 'MCP：' + (m.ready ? '可执行文件就绪' : '未找到 gpu-control-mcp')
        + ' · Token ' + (m.tokenReady ? '就绪' : '需配置');
      const authRow = document.createElement('div');
      authRow.className = 'actions';
      authRow.style.marginTop = '8px';
      authRow.append(
        btn(m.tokenReady ? '使用本机 Token' : '设置 Token', m.tokenReady ? 'useLocalCredentials' : 'setToken'),
        btn('注册 MCP', 'installMcp'),
        btn('密码登录', 'login')
      );
      ai.append(p1, p2, p3, authRow);
      const ops = el('ops');
      ops.replaceChildren(
        btn('运行自检', 'runDoctor'),
        btn('节点探活', 'liveness'),
        btn('测试 SSH', 'testSSH'),
        btn('只读诊断', 'execPreset'),
        btn('部署 Job', 'deployJob'),
        btn('腾空节点', 'freeNode')
      );
      const list = el('notices');
      const items = (state.notices && state.notices.items) || [];
      list.replaceChildren();
      if (!items.length) {
        const li = document.createElement('li');
        li.className = 'muted';
        li.textContent = '暂无公告';
        list.append(li);
      } else {
        for (const n of items) {
          const li = document.createElement('li');
          const title = document.createElement('div');
          title.textContent = (n.severity ? '[' + n.severity + '] ' : '') + (n.title || '');
          if (n.severity === 'error' || n.severity === 'critical') title.className = 'warn';
          const detail = document.createElement('div');
          detail.className = 'muted';
          detail.textContent = n.detail || n.source || '';
          li.append(title, detail);
          list.append(li);
        }
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
    const map = {
      openDashboard: 'computedeck.openDashboard',
      openAssistant: 'computedeck.openAssistant',
      openNotices: 'computedeck.openNotices',
      openWalkthrough: 'computedeck.openWalkthrough',
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
  cmd('computedeck.openDashboard', async () => {
    await openControlUi();
  });
  cmd('computedeck.openAssistant', async () => {
    await openControlUi('/#/assistant');
  });
  cmd('computedeck.openNotices', async () => {
    await openControlUi('/#/notices');
  });
  cmd('computedeck.openWalkthrough', async () => {
    const walkthroughId = `${context.extension.id}#computedeck.getStarted`;
    try {
      await vscode.commands.executeCommand('workbench.action.openWalkthrough', walkthroughId, false);
      return;
    } catch (_) {
      // CodeLab / 部分 VS Code 发行版没有 Walkthrough 命令，回落到 markdown 预览。
    }
    const guide = vscode.Uri.joinPath(context.extensionUri, 'media', 'walkthrough', '01-connect.md');
    await vscode.commands.executeCommand('markdown.showPreview', guide);
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
}

function deactivate() {
  if (pollTimer) clearInterval(pollTimer);
}

module.exports = { activate, deactivate };
