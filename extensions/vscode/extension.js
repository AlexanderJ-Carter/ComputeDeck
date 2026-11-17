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
    const token = await this.context.secrets.get(TOKEN_KEY);
    if (token) headers.Authorization = `Bearer ${token}`;
    const cookie = await this.context.secrets.get(COOKIE_KEY);
    if (cookie) headers.Cookie = cookie;
    return headers;
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
      throw new Error(`无法连接 ${this.base()}：${err && err.message ? err.message : err}`);
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
      throw new ApiError('需要登录 ComputeDeck（命令面板：ComputeDeck: 密码登录 / 设置 API Token）', 401, data);
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
      } catch (_) {}
      throw new Error(`登录失败：${detail || res.status}`);
    }
    const setCookies =
      typeof res.headers.getSetCookie === 'function'
        ? res.headers.getSetCookie()
        : [res.headers.get('set-cookie')].filter(Boolean);
    const cookie = setCookies.map((x) => String(x).split(';')[0]).join('; ');
    if (!cookie) throw new Error('登录成功但没有拿到 Session Cookie');
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
}

function config() {
  return vscode.workspace.getConfiguration(SECTION);
}

function stateLabel(node) {
  if (!node) return '未知';
  if (node.liveness_state === 'offline' || node.state === 'offline') return '离线';
  if (node.liveness_state === 'degraded' || node.state === 'degraded') return '探活异常';
  if (node.snapshot_stale) return '快照过期';
  if (node.cordoned) return '已隔离';
  if (node.used) return '占用中';
  return '空闲';
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
      const [overview, live, jobs, gateway] = await Promise.all([
        this.client.overview(true),
        this.client.liveness().catch(() => null),
        this.client.jobs().catch(() => []),
        this.client.gatewayStatus().catch(() => null),
      ]);
      this.data = { overview, live, jobs, gateway };
      this.lastError = null;
    } catch (err) {
      this.lastError = err;
      if (showErrors) {
        vscode.window.showErrorMessage(`ComputeDeck: ${err.message || err}`);
      }
    }
    this.emitter.fire();
    updateStatusBar(this.data, this.lastError);
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
      const row = makeItem('$(error) 控制面未连接', vscode.TreeItemCollapsibleState.None, {
        description: this.client.base(),
        tooltip: String(this.lastError.message || this.lastError),
        command: { command: 'computedeck.login', title: '登录' },
      });
      return [
        makeItem('连接', vscode.TreeItemCollapsibleState.Expanded, {
          iconPath: new vscode.ThemeIcon('plug'),
          children: [row],
        }),
      ];
    }
    if (!this.data) {
      return [makeItem('$(sync~spin) 加载中…', vscode.TreeItemCollapsibleState.None)];
    }

    const { overview, live, jobs, gateway } = this.data;
    const summary = overview.summary || {};
    const nodes = overview.nodes || [];
    const leases = overview.leases || [];
    const queue = overview.queue || [];
    const layering = (gateway && gateway.layering) || {};
    const sections = [];

    const summaryRows = [
      ['健康评分', overview.quality ? `${overview.quality.score ?? '-'} · ${overview.quality.grade || ''}` : '-'],
      ['节点', `${summary.nodes_online ?? '-'} 在线 / ${summary.nodes_offline ?? 0} 离线`],
      ['GPU', `${summary.gpus_free ?? 0} 空闲 / ${summary.gpus_used ?? 0} 占用 / ${summary.gpus_total ?? 0} 计入`],
      ['排队', String(summary.queue_pending ?? 0)],
      ['租约', String(summary.leases ?? leases.length)],
      ['入口', layering.summary || (gateway && gateway.preferred) || '-'],
    ].map(([label, value]) =>
      makeItem(`${label}`, vscode.TreeItemCollapsibleState.None, {
        description: String(value),
        iconPath: new vscode.ThemeIcon('info'),
      })
    );
    sections.push(
      makeItem('集群总览', vscode.TreeItemCollapsibleState.Expanded, {
        description: `${summary.nodes ?? nodes.length} 节点 · ${summary.gpus_free ?? 0} 空闲 GPU`,
        iconPath: new vscode.ThemeIcon('dashboard'),
        children: summaryRows,
      })
    );

    const nodeRows = nodes.map((node) => {
      const state = stateLabel(node);
      const online = !['离线', '探活异常'].includes(state);
      const tooltip = new vscode.MarkdownString();
      tooltip.appendMarkdown(`**${node.name}**  \n`);
      tooltip.appendMarkdown(`主机：${node.host || '-'}  \n`);
      tooltip.appendMarkdown(`状态：${state}  \n`);
      tooltip.appendMarkdown(`GPU：${node.counted ? `${node.free} 空闲 / ${node.used} 占用` : '不计入（离线或快照过期）'} / 共 ${node.total ?? '-'}  \n`);
      if (node.last_seen) tooltip.appendMarkdown(`最后成功探活：${node.last_seen}  \n`);
      if (node.liveness_error) tooltip.appendMarkdown(`错误：${node.liveness_error}`);
      return makeItem(node.name, vscode.TreeItemCollapsibleState.None, {
        description: `${state} · ${node.counted ? `${node.free}/${node.total}` : '—'}`,
        iconPath: new vscode.ThemeIcon(online ? 'server' : 'error', online ? undefined : new vscode.ThemeColor('errorForeground')),
        contextValue: `node-${node.state || 'unknown'}`,
        tooltip,
        command: { command: 'computedeck.nodeMenu', title: '节点操作', arguments: [node] },
      });
    });
    sections.push(
      makeItem('节点', vscode.TreeItemCollapsibleState.Expanded, {
        description: `${nodes.length} 台 · ${summary.nodes_offline ?? 0} 离线`,
        iconPath: new vscode.ThemeIcon('server'),
        children: nodeRows.length ? nodeRows : [makeItem('暂无节点', vscode.TreeItemCollapsibleState.None)],
      })
    );

    const jobRows = (jobs || []).map((job) =>
      makeItem(job.name || 'job', vscode.TreeItemCollapsibleState.None, {
        description: `${job.gpus_per_node ?? '?'} GPU${job.default ? ' · 默认' : ''}`,
        iconPath: new vscode.ThemeIcon('rocket'),
        contextValue: 'job',
        tooltip: job.description || '',
        command: { command: 'computedeck.jobMenu', title: 'Job 操作', arguments: [job] },
      })
    );
    sections.push(
      makeItem('Job', vscode.TreeItemCollapsibleState.Collapsed, {
        description: `${jobRows.length} 个`,
        iconPath: new vscode.ThemeIcon('rocket'),
        children: jobRows.length ? jobRows : [makeItem('暂无 Job', vscode.TreeItemCollapsibleState.None)],
      })
    );

    const queueRows = [
      makeItem('队列 pending', vscode.TreeItemCollapsibleState.None, {
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
      makeItem('队列与租约', vscode.TreeItemCollapsibleState.Collapsed, {
        description: `${summary.queue_pending ?? 0} pending · ${leases.length} 租约`,
        iconPath: new vscode.ThemeIcon('list-ordered'),
        children: queueRows,
      })
    );

    const gatewayRows = [];
    const edge = (gateway && gateway.edge) || {};
    const ll = (gateway && gateway.litellm) || {};
    const nginx = (gateway && gateway.nginx) || {};
    gatewayRows.push(
      makeItem(`Edge :${gateway?.ingress?.port || 18080}`, vscode.TreeItemCollapsibleState.None, {
        description: edge.running ? '运行中 · 唯一节点 LB' : '未运行',
        iconPath: new vscode.ThemeIcon(edge.running ? 'globe' : 'circle-slash'),
        tooltip: gateway?.layering?.edge_url || '',
      }),
      makeItem(`LiteLLM :${ll.port || 4000}`, vscode.TreeItemCollapsibleState.None, {
        description:
          ll.upstream === 'edge'
            ? `OpenAI 层 · 经 Edge`
            : ll.running
              ? 'OpenAI 层 · 直连节点（重复 LB）'
              : '未运行',
        iconPath: new vscode.ThemeIcon(ll.running ? 'symbol-method' : 'circle-slash'),
        tooltip: gateway?.layering?.summary || '',
      }),
      makeItem('nginx', vscode.TreeItemCollapsibleState.None, {
        description: nginx.installed ? '已安装 · 仅 Web/TLS 前端' : '未安装 · 可选 Web/TLS 前端',
        iconPath: new vscode.ThemeIcon('browser'),
      })
    );
    if (gateway?.layering?.duplicate_node_lb) {
      gatewayRows.unshift(
        makeItem('$(warning) 检测到重复节点 LB', vscode.TreeItemCollapsibleState.None, {
          description: '建议 LiteLLM → Edge',
          iconPath: new vscode.ThemeIcon('warning', new vscode.ThemeColor('warningForeground')),
          tooltip: gateway.layering.recommendation || '',
        })
      );
    }
    sections.push(
      makeItem('入口', vscode.TreeItemCollapsibleState.Collapsed, {
        description: layering.summary || '',
        iconPath: new vscode.ThemeIcon('globe'),
        children: gatewayRows,
      })
    );

    const toolRows = [
      ['运行自检', 'computedeck.runDoctor', 'search'],
      ['节点探活', 'computedeck.liveness', 'pulse'],
      ['测试 SSH', 'computedeck.testSSH', 'plug'],
      ['运行只读诊断', 'computedeck.execPreset', 'beaker'],
      ['部署 Job', 'computedeck.deployJob', 'rocket'],
      ['腾空节点', 'computedeck.freeNode', 'debug-stop'],
      ['注册 MCP Server', 'computedeck.installMcp', 'extensions'],
      ['打开 Web 控制面', 'computedeck.openDashboard', 'globe'],
    ].map(([label, command, icon]) =>
      makeItem(label, vscode.TreeItemCollapsibleState.None, {
        iconPath: new vscode.ThemeIcon(icon),
        command: { command, title: label },
      })
    );
    sections.push(
      makeItem('操作', vscode.TreeItemCollapsibleState.Collapsed, {
        description: '诊断 / 部署 / 入口 / MCP',
        iconPath: new vscode.ThemeIcon('tools'),
        children: toolRows,
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
    statusBar.text = '$(error) ComputeDeck 未连接';
    statusBar.tooltip = String((error && error.message) || '点击登录或刷新');
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
  statusBar.text = `${offline > 0 ? '$(warning)' : '$(pulse)'} CD ${online}/${total} 节点 · ${free}/${gpuTotal} 空闲`;
  statusBar.tooltip = new vscode.MarkdownString(
    `**ComputeDeck**\n\n在线 ${online} / ${total}，离线 ${offline}，GPU 空闲 ${free}/${gpuTotal}\n\n点击打开 Web 控制面`
  );
  statusBar.backgroundColor = undefined;
  statusBar.command = 'computedeck.openDashboard';
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
  // 视图内联按钮传入的是 TreeItem，而不是原始节点对象。
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
    vscode.window.showWarningMessage('ComputeDeck: 没有节点');
    return null;
  }
  const picked = await vscode.window.showQuickPick(
    nodes.map((n) => ({
      label: n.name,
      description: `${stateLabel(n)} · ${n.counted ? `${n.free}/${n.total}` : '—'}`,
      node: n,
    })),
    { placeHolder: '选择节点' }
  );
  return picked ? picked.node : null;
}

async function pickJob() {
  const jobs = provider?.data?.jobs || (await client.jobs());
  if (!jobs.length) {
    vscode.window.showWarningMessage('ComputeDeck: 没有已安装 Job');
    return null;
  }
  const picked = await vscode.window.showQuickPick(
    jobs.map((j) => ({ label: j.name, description: `${j.gpus_per_node ?? '?'} GPU`, job: j })),
    { placeHolder: '选择 Job' }
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
    output.appendLine(`读取节点 SSH 配置失败：${err.message || err}`);
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
    { label: '打开终端', action: 'terminal', icon: 'terminal' },
    { label: '运行 nvidia-smi', action: 'nvidia-smi', icon: 'pulse' },
    { label: '查看 GPU 进程', action: 'gpu-procs', icon: 'list-unordered' },
    { label: node.cordoned ? '取消隔离' : '隔离节点', action: 'cordon', icon: 'lock' },
    { label: '腾空节点', action: 'free', icon: 'debug-stop' },
    { label: '打开 Web 节点详情', action: 'dashboard', icon: 'globe' },
  ].map((x) => ({
    label: `${offline && ['terminal', 'nvidia-smi', 'gpu-procs', 'free'].includes(x.action) ? '$(warning) ' : ''}${x.label}`,
    action: x.action,
    description: offline && ['terminal', 'nvidia-smi', 'gpu-procs', 'free'].includes(x.action) ? '节点离线' : '',
  }));
  const picked = await vscode.window.showQuickPick(choices, { placeHolder: `${node.name} · ${stateLabel(node)}` });
  if (!picked) return;
  switch (picked.action) {
    case 'terminal':
      if (offline) {
        vscode.window.showWarningMessage('节点离线，SSH 终端可能无法连接');
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
      return vscode.env.openExternal(vscode.Uri.parse(`${client.base()}/#/cluster`));
  }
}

async function jobMenu(jobArg) {
  const job = jobFromArg(jobArg) || (await pickJob());
  if (!job) return;
  const picked = await vscode.window.showQuickPick(
    [
      { label: '启动', action: 'start' },
      { label: '停止', action: 'stop' },
      { label: '重启', action: 'restart' },
      { label: '状态', action: 'status' },
      { label: '等待就绪', action: 'wait' },
    ],
    { placeHolder: `${job.name} 操作` }
  );
  if (!picked) return;
  const result = await vscode.window.withProgress(
    { location: vscode.ProgressLocation.Notification, title: `ComputeDeck: ${picked.label} ${job.name}` },
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
    { id: 'gpu-util', label: 'GPU 利用率' },
    { id: 'gpu-list', label: '列出 GPU' },
    { id: 'gpu-procs', label: 'GPU 进程' },
    { id: 'uptime', label: '负载/内存' },
    { id: 'disk', label: '磁盘' },
  ];
  let preset = presetArg;
  if (!preset) {
    const picked = await vscode.window.showQuickPick(
      presets.map((p) => ({ label: p.label || p.id, id: p.id })),
      { placeHolder: `在 ${node.name} 运行只读诊断` }
    );
    if (!picked) return;
    preset = picked.id;
  }
  const result = await vscode.window.withProgress(
    { location: vscode.ProgressLocation.Notification, title: `ComputeDeck: ${preset} @ ${node.name}` },
    () => client.execPreset(node.name, preset)
  );
  showResult(`${preset} @ ${node.name}`, result);
  if (!result.ok) vscode.window.showWarningMessage(`诊断失败：${result.error || '见输出'}`);
}

async function toggleCordon(nodeArg) {
  const node = nodeFromArg(nodeArg) || (await pickNode());
  if (!node) return;
  const action = node.cordoned ? 'uncordon' : 'cordon';
  const ok = await vscode.window.showWarningMessage(
    `${node.cordoned ? '取消隔离' : '隔离'}节点 ${node.name}？`,
    { modal: true },
    '确认'
  );
  if (ok !== '确认') return;
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
  const ok = await vscode.window.showWarningMessage(
    `腾空 ${node.name}？会停止 Job、结束 GPU 进程并释放租约。`,
    { modal: true },
    '腾空'
  );
  if (ok !== '腾空') return;
  const result = await vscode.window.withProgress(
    { location: vscode.ProgressLocation.Notification, title: `ComputeDeck: 腾空 ${node.name}` },
    () => client.freeNode(node.name, false)
  );
  showResult(`腾空 ${node.name}`, result);
  await provider.refresh(true);
}

async function deployJob() {
  const job = await pickJob();
  if (!job) return;
  const mode = await vscode.window.showQuickPick(
    [
      { label: '按 GPU 数自动选节点', mode: 'gpus' },
      { label: '指定节点', mode: 'node' },
    ],
    { placeHolder: '部署方式' }
  );
  if (!mode) return;
  const body = { job: job.name };
  if (mode.mode === 'gpus') {
    const raw = await vscode.window.showInputBox({
      prompt: '部署 GPU 数',
      value: String(job.gpus_per_node || 4),
      validateInput: (v) => (/^\d+$/.test(v) && Number(v) > 0 ? null : '请输入正整数'),
    });
    if (raw === undefined) return;
    body.gpus = Number(raw);
  } else {
    const node = await pickNode();
    if (!node) return;
    body.nodes = node.name;
  }
  const ok = await vscode.window.showWarningMessage(
    `部署 ${job.name}？`,
    { modal: true },
    '部署'
  );
  if (ok !== '部署') return;
  const result = await vscode.window.withProgress(
    { location: vscode.ProgressLocation.Notification, title: `ComputeDeck: 部署 ${job.name}` },
    () => client.deploy(job.name, body)
  );
  showResult(`部署 ${job.name}`, result);
  await provider.refresh(true);
}

function resolveMcpPath() {
  let raw = String(config().get('mcpPath', '${workspaceFolder}/bin/gpu-control-mcp') || '');
  const folder = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath || '';
  raw = raw.replace(/\$\{workspaceFolder\}/g, folder);
  if (!path.isAbsolute(raw) && folder) raw = path.join(folder, raw);
  return raw;
}

function registerMcp(context) {
  if (!vscode.lm || typeof vscode.lm.registerMcpServerDefinitionProvider !== 'function') {
    output.appendLine('当前 VS Code 不支持 MCP Server provider（需要 1.101+）。');
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
        const token = await context.secrets.get(TOKEN_KEY);
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
            const choice = await vscode.window.showWarningMessage(message, '密码登录', '设置 Token');
            if (choice === '密码登录') return vscode.commands.executeCommand('computedeck.login');
            if (choice === '设置 Token') return vscode.commands.executeCommand('computedeck.setToken');
            return;
          }
          vscode.window.showErrorMessage(`ComputeDeck: ${message}`);
        }
      })
    );

  cmd('computedeck.refresh', async () => {
    await provider.refresh(true);
  });
  cmd('computedeck.openDashboard', async () => {
    await vscode.env.openExternal(vscode.Uri.parse(client.base()));
  });
  cmd('computedeck.login', async () => {
    const password = await vscode.window.showInputBox({
      prompt: 'ComputeDeck 控制面密码',
      password: true,
      ignoreFocusOut: true,
    });
    if (!password) return;
    await client.login(password);
    vscode.window.showInformationMessage('ComputeDeck 登录成功');
    if (mcpEmitter) mcpEmitter.fire();
    await provider.refresh(true);
  });
  cmd('computedeck.setToken', async () => {
    const token = await vscode.window.showInputBox({
      prompt: 'API Token（gpu-control auth issue-token 生成）',
      password: true,
      ignoreFocusOut: true,
    });
    if (!token) return;
    await context.secrets.store(TOKEN_KEY, token.trim());
    await context.secrets.delete(COOKIE_KEY);
    vscode.window.showInformationMessage('ComputeDeck API Token 已保存');
    if (mcpEmitter) mcpEmitter.fire();
    await provider.refresh(true);
  });
  cmd('computedeck.logout', async () => {
    await context.secrets.delete(TOKEN_KEY);
    await context.secrets.delete(COOKIE_KEY);
    vscode.window.showInformationMessage('ComputeDeck 凭据已清除');
    if (mcpEmitter) mcpEmitter.fire();
    await provider.refresh(true);
  });
  cmd('computedeck.runDoctor', async () => {
    const result = await vscode.window.withProgress(
      { location: vscode.ProgressLocation.Notification, title: 'ComputeDeck: 运行自检' },
      () => client.doctor()
    );
    showResult('doctor', result);
    vscode.window.showInformationMessage(result.ok ? '自检通过' : '自检发现问题，见输出');
  });
  cmd('computedeck.liveness', async () => {
    const result = await vscode.window.withProgress(
      { location: vscode.ProgressLocation.Notification, title: 'ComputeDeck: 节点探活' },
      () => client.livenessCheck()
    );
    const s = result.summary || {};
    vscode.window.showInformationMessage(
      `探活完成：在线 ${s.online ?? 0}，离线 ${s.offline ?? 0}，未知 ${s.unknown ?? 0}`
    );
    showResult('liveness', result);
    await provider.refresh(true);
  });
  cmd('computedeck.testSSH', async () => {
    const node = await pickNode();
    const result = await vscode.window.withProgress(
      { location: vscode.ProgressLocation.Notification, title: 'ComputeDeck: 测试 SSH' },
      () => client.testSSH(node?.name)
    );
    showResult('test-ssh', result);
    vscode.window.showInformationMessage(
      `SSH：${result.ok_count ?? 0}/${result.total ?? 0} 通过`
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
      `启动 ${target.name}`,
      await client.jobAction(target.name, 'start')
    );
    await provider.refresh(true);
  });
  cmd('computedeck.jobStop', async (job) => {
    const target = jobFromArg(job) || (await pickJob());
    if (!target) return;
    showResult(
      `停止 ${target.name}`,
      await client.jobAction(target.name, 'stop')
    );
    await provider.refresh(true);
  });
  cmd('computedeck.jobRestart', async (job) => {
    const target = jobFromArg(job) || (await pickJob());
    if (!target) return;
    showResult(
      `重启 ${target.name}`,
      await client.jobAction(target.name, 'restart')
    );
    await provider.refresh(true);
  });
  cmd('computedeck.installMcp', async () => {
    const mcpPath = resolveMcpPath();
    if (!mcpPath || !fs.existsSync(mcpPath)) {
      vscode.window.showWarningMessage(
        `未找到 ${mcpPath || '(未配置)'}；请在设置 computedeck.mcpPath 指向 bin/gpu-control-mcp`
      );
      return;
    }
    const token = await context.secrets.get(TOKEN_KEY);
    if (!token) {
      vscode.window.showWarningMessage(
        'MCP 走 HTTP 需要 API Token；请先执行「ComputeDeck: 设置 API Token」'
      );
    }
    if (mcpEmitter) mcpEmitter.fire();
    vscode.window.showInformationMessage('ComputeDeck MCP Server 已注册（VS Code / Copilot 可使用）');
  });
}

function startPolling() {
  if (pollTimer) clearInterval(pollTimer);
  const seconds = Math.max(5, Number(config().get('pollIntervalSec', 30)) || 30);
  pollTimer = setInterval(() => {
    provider.refresh(false).catch(() => {});
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
        provider.refresh(false).catch(() => {});
      }
    })
  );

  startPolling();
  provider.refresh(false).catch(() => {});
  output.appendLine(`ComputeDeck extension activated · API ${client.base()}`);
}

function deactivate() {
  if (pollTimer) clearInterval(pollTimer);
}

module.exports = { activate, deactivate };
