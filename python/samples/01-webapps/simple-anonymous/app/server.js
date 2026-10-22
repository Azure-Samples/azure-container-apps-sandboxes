// Node HTTP server used by samples/sandboxes/scenarios/01-webapps/simple-anonymous.
// Zero external dependencies — uses built-in http/os/fs so the sandbox
// doesn't need `npm install` before starting.
//
//   GET /                 -> HTML landing page ("Hello from a sandbox")
//   GET /healthz          -> { status: "ok" }
//   GET /api/hello        -> { message, hostname, uptime, pid }
//   GET /api/info         -> { node, platform, arch, cpus, memoryMB, startedAt }
//   GET /api/sysinfo      -> uname-style { hostname, kernel, osRelease, arch,
//                            distro, cpuModel, cpus, totalMemMB, nodeVersion, ip }
//   GET /api/stats        -> live { uptime, loadavg, cpus, memTotalMB, memFreeMB,
//                            memUsedPct, procCount, serverUptime, serverPid }
//   GET /api/processes    -> top 10 by RSS [{ pid, comm, rssKB, state }]

const http = require('http');
const os = require('os');
const fs = require('fs');

const PORT = parseInt(process.env.PORT || '8080', 10);
const STARTED_AT = new Date();
const IDLE_TIMEOUT_SEC = parseInt(process.env.IDLE_TIMEOUT_SEC || '1800', 10);
let LAST_REQ_AT = Date.now();

// ---------- helpers ----------

function json(res, status, body) {
  res.writeHead(status, {
    'Content-Type': 'application/json; charset=utf-8',
    'Cache-Control': 'no-store',
  });
  res.end(JSON.stringify(body, null, 2) + '\n');
}

function html(res, status, body) {
  res.writeHead(status, {
    'Content-Type': 'text/html; charset=utf-8',
    'Cache-Control': 'no-store',
  });
  res.end(body);
}

function safeRead(path) {
  try { return fs.readFileSync(path, 'utf8').trim(); } catch (_) { return ''; }
}

function esc(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

function readDistro() {
  const txt = safeRead('/etc/os-release');
  if (!txt) return '';
  const m = txt.match(/^PRETTY_NAME="?([^"\n]+)"?/m);
  return m ? m[1] : '';
}

function primaryIp() {
  const ifaces = os.networkInterfaces();
  for (const name of Object.keys(ifaces)) {
    for (const a of ifaces[name] || []) {
      if (a.family === 'IPv4' && !a.internal) return a.address;
    }
  }
  return '127.0.0.1';
}

function procCount() {
  try {
    return fs.readdirSync('/proc').filter((n) => /^\d+$/.test(n)).length;
  } catch (_) { return 0; }
}

function topProcesses(limit) {
  let pids = [];
  try {
    pids = fs.readdirSync('/proc').filter((n) => /^\d+$/.test(n));
  } catch (_) { return []; }

  const rows = [];
  for (const pid of pids) {
    const status = safeRead(`/proc/${pid}/status`);
    if (!status) continue;
    const comm = safeRead(`/proc/${pid}/comm`) || '?';
    const rssMatch = status.match(/^VmRSS:\s+(\d+)\s+kB/m);
    const stateMatch = status.match(/^State:\s+(\S+)/m);
    rows.push({
      pid: parseInt(pid, 10),
      comm,
      rssKB: rssMatch ? parseInt(rssMatch[1], 10) : 0,
      state: stateMatch ? stateMatch[1] : '?',
    });
  }
  rows.sort((a, b) => b.rssKB - a.rssKB);
  return rows.slice(0, limit);
}

// ---------- payload builders ----------

function hello() {
  return {
    message: 'Hello from sandbox',
    hostname: os.hostname(),
    uptime: Math.round(process.uptime()),
    pid: process.pid,
  };
}

function info() {
  return {
    node: process.version,
    platform: process.platform,
    arch: process.arch,
    cpus: os.cpus().length,
    memoryMB: Math.round(os.totalmem() / 1024 / 1024),
    startedAt: STARTED_AT.toISOString(),
  };
}

function sysinfo() {
  const cpus = os.cpus();
  return {
    hostname: os.hostname(),
    kernel: safeRead('/proc/sys/kernel/ostype') || os.type(),
    osRelease: safeRead('/proc/sys/kernel/osrelease') || os.release(),
    arch: os.arch(),
    distro: readDistro() || 'unknown',
    cpuModel: (cpus[0] && cpus[0].model) ? cpus[0].model.trim() : 'unknown',
    cpus: cpus.length,
    totalMemMB: Math.round(os.totalmem() / 1024 / 1024),
    nodeVersion: process.version,
    ip: primaryIp(),
  };
}

function stats() {
  const total = os.totalmem();
  const free = os.freemem();
  const secondsSinceLastReq = Math.max(0, Math.round((Date.now() - LAST_REQ_AT) / 1000));
  return {
    uptime: Math.round(os.uptime()),
    loadavg: os.loadavg().map((n) => Math.round(n * 100) / 100),
    cpus: os.cpus().length,
    memTotalMB: Math.round(total / 1024 / 1024),
    memFreeMB: Math.round(free / 1024 / 1024),
    memUsedPct: Math.round(((total - free) / total) * 1000) / 10,
    procCount: procCount(),
    serverUptime: Math.round(process.uptime()),
    serverPid: process.pid,
    idleTimeoutSec: IDLE_TIMEOUT_SEC,
    secondsSinceLastReq,
    secondsUntilSuspend: Math.max(0, IDLE_TIMEOUT_SEC - secondsSinceLastReq),
  };
}

// ---------- HTML landing page ----------

function page() {
  const h = hello();
  const s = sysinfo();
  return `<!doctype html>
<html lang="en" class="h-full">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Hello from a sandbox · Azure Container Apps</title>
<script src="https://cdn.tailwindcss.com"></script>
<script>
  tailwind.config = {
    theme: {
      extend: {
        colors: {
          ink: { 950: '#070a1a', 900: '#0b1020', 850: '#0f1530', 800: '#141a3a' },
          brand: { 400: '#7aa2ff', 500: '#5b8cff', 600: '#3b6bff' },
        },
        fontFamily: {
          mono: ['ui-monospace','SFMono-Regular','Menlo','Consolas','monospace'],
        },
      }
    }
  }
</script>
<style>
  body { background: radial-gradient(1200px 600px at 10% -10%, #1e2a6b 0%, transparent 60%),
                     radial-gradient(900px 500px at 110% 10%, #2a1e6b 0%, transparent 55%),
                     #070a1a; }
  .card { background: rgba(20, 26, 58, 0.65); backdrop-filter: blur(8px); }
  .pulse-dot { animation: pulse 1.6s ease-in-out infinite; }
  @keyframes pulse { 0%,100% { opacity: 1 } 50% { opacity: .3 } }
  pre { tab-size: 2; }
</style>
</head>
<body class="min-h-full text-slate-100 font-sans antialiased">

<!-- Header -->
<header class="border-b border-white/5">
  <div class="mx-auto max-w-6xl px-6 py-4 flex items-center justify-between">
    <div class="flex items-center gap-3">
      <div class="size-9 rounded-lg bg-gradient-to-br from-brand-400 to-fuchsia-500 grid place-items-center text-ink-950 font-bold">A</div>
      <div>
        <div class="text-sm text-slate-400">Azure Container Apps</div>
        <div class="font-semibold tracking-tight">Sandbox · live demo</div>
      </div>
    </div>
    <nav class="flex items-center gap-3 text-sm">
      <a href="https://sandboxes.azure.com" target="_blank" rel="noopener"
         class="px-3 py-1.5 rounded-md bg-white/5 hover:bg-white/10 transition">Portal ↗</a>
      <a href="https://github.com/annaji-msft/ai-apps/tree/main/samples/sandboxes/scenarios/01-webapps/simple-anonymous"
         target="_blank" rel="noopener"
         class="px-3 py-1.5 rounded-md bg-white/5 hover:bg-white/10 transition">GitHub ↗</a>
    </nav>
  </div>
</header>

<!-- Hero -->
<section class="mx-auto max-w-6xl px-6 pt-16 pb-10">
  <div class="flex flex-wrap items-center gap-2 text-xs">
    <span class="inline-flex items-center gap-1.5 rounded-full bg-emerald-500/10 text-emerald-400 px-3 py-1 ring-1 ring-emerald-500/30">
      <span class="size-1.5 rounded-full bg-emerald-400 pulse-dot"></span> live · responding
    </span>
    <span class="rounded-full bg-white/5 text-slate-300 px-3 py-1 ring-1 ring-white/10 font-mono">
      ${esc(h.hostname)} · up <span id="hero-uptime">${h.uptime}</span>s
    </span>
    <span class="rounded-full bg-amber-500/10 text-amber-300 px-3 py-1 ring-1 ring-amber-500/30 font-mono" title="Sandbox auto-suspends after this much idle time. Polling this page resets the timer.">
      💤 sleeps in <span id="hero-sleep">…</span>
    </span>
    <span class="rounded-full bg-brand-500/10 text-brand-400 px-3 py-1 ring-1 ring-brand-500/30 font-mono">
      pattern: simple-anonymous · port 8080 open to the internet
    </span>
  </div>
  <h1 class="mt-5 text-5xl sm:text-6xl font-bold tracking-tight">
    Hello from a sandbox <span class="inline-block">👋</span>
  </h1>
  <p class="mt-4 max-w-2xl text-lg text-slate-300">
    This page is being served from an <em>ephemeral, isolated VM</em> spun up by Azure
    Container Apps Sandboxes. Everything below — kernel, CPU, memory, processes —
    is read live from inside it.
  </p>
  <div class="mt-7 flex flex-wrap gap-3">
    <a href="https://sandboxes.azure.com" target="_blank" rel="noopener"
       class="inline-flex items-center gap-2 rounded-lg bg-gradient-to-r from-brand-500 to-fuchsia-500 px-5 py-2.5 font-semibold text-white shadow-lg shadow-brand-600/30 hover:opacity-95 transition">
      Open the portal → sandboxes.azure.com
    </a>
    <a href="#get-started"
       class="inline-flex items-center gap-2 rounded-lg bg-white/5 ring-1 ring-white/10 px-5 py-2.5 font-medium hover:bg-white/10 transition">
      Quick start
    </a>
  </div>
</section>

<!-- System info (uname) -->
<section class="mx-auto max-w-6xl px-6 pb-8">
  <h2 class="text-sm font-semibold uppercase tracking-wider text-slate-400 mb-3">System info <span class="font-normal normal-case text-slate-500">(uname -a + os-release)</span></h2>
  <div class="card rounded-2xl ring-1 ring-white/10 p-6">
    <dl class="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-x-8 gap-y-4 text-sm">
      <div><dt class="text-slate-400">Hostname</dt><dd class="font-mono mt-0.5">${esc(s.hostname)}</dd></div>
      <div><dt class="text-slate-400">Distro</dt><dd class="font-mono mt-0.5">${esc(s.distro)}</dd></div>
      <div><dt class="text-slate-400">Kernel</dt><dd class="font-mono mt-0.5">${esc(s.kernel)} ${esc(s.osRelease)}</dd></div>
      <div><dt class="text-slate-400">Arch</dt><dd class="font-mono mt-0.5">${esc(s.arch)}</dd></div>
      <div class="md:col-span-2"><dt class="text-slate-400">CPU</dt><dd class="font-mono mt-0.5 truncate" title="${esc(s.cpuModel)}">${esc(s.cpuModel)}</dd></div>
      <div><dt class="text-slate-400">vCPUs</dt><dd class="font-mono mt-0.5">${s.cpus}</dd></div>
      <div><dt class="text-slate-400">Memory</dt><dd class="font-mono mt-0.5">${s.totalMemMB} MB</dd></div>
      <div><dt class="text-slate-400">Node</dt><dd class="font-mono mt-0.5">${esc(s.nodeVersion)}</dd></div>
      <div><dt class="text-slate-400">Internal IP</dt><dd class="font-mono mt-0.5">${esc(s.ip)}</dd></div>
    </dl>
  </div>
</section>

<!-- Live stats -->
<section class="mx-auto max-w-6xl px-6 pb-8">
  <div class="flex items-center justify-between mb-3">
    <h2 class="text-sm font-semibold uppercase tracking-wider text-slate-400">Live stats</h2>
    <div class="text-xs text-slate-500">refreshes every 2s · <span id="stats-fresh" class="text-emerald-400">●</span></div>
  </div>
  <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
    <div class="card rounded-2xl ring-1 ring-white/10 p-5">
      <div class="text-xs text-slate-400 uppercase tracking-wider">CPU load</div>
      <div class="mt-2 flex items-baseline gap-2">
        <div class="text-3xl font-bold tabular-nums" id="cpu-1m">—</div>
        <div class="text-xs text-slate-400">1m</div>
      </div>
      <div class="mt-1 text-xs text-slate-400 tabular-nums">
        5m <span id="cpu-5m" class="text-slate-200">—</span> ·
        15m <span id="cpu-15m" class="text-slate-200">—</span> ·
        <span id="cpu-count" class="text-slate-200">—</span> vCPUs
      </div>
      <div class="mt-3 h-2 bg-white/5 rounded-full overflow-hidden">
        <div id="cpu-bar" class="h-full bg-gradient-to-r from-brand-500 to-fuchsia-500 transition-all" style="width:0%"></div>
      </div>
    </div>

    <div class="card rounded-2xl ring-1 ring-white/10 p-5">
      <div class="text-xs text-slate-400 uppercase tracking-wider">Memory</div>
      <div class="mt-2 flex items-baseline gap-2">
        <div class="text-3xl font-bold tabular-nums" id="mem-pct">—</div>
        <div class="text-xs text-slate-400">used</div>
      </div>
      <div class="mt-1 text-xs text-slate-400 tabular-nums">
        <span id="mem-used" class="text-slate-200">—</span> /
        <span id="mem-total" class="text-slate-200">—</span> MB
      </div>
      <div class="mt-3 h-2 bg-white/5 rounded-full overflow-hidden">
        <div id="mem-bar" class="h-full bg-gradient-to-r from-emerald-400 to-cyan-400 transition-all" style="width:0%"></div>
      </div>
    </div>

    <div class="card rounded-2xl ring-1 ring-white/10 p-5">
      <div class="text-xs text-slate-400 uppercase tracking-wider">Processes</div>
      <div class="mt-2 flex items-baseline gap-2">
        <div class="text-3xl font-bold tabular-nums" id="proc-count">—</div>
        <div class="text-xs text-slate-400" id="proc-delta">running</div>
      </div>
      <div class="mt-1 text-xs text-slate-400">
        from <code class="font-mono text-slate-200">/proc/[0-9]*</code>
      </div>
    </div>

    <div class="card rounded-2xl ring-1 ring-white/10 p-5">
      <div class="text-xs text-slate-400 uppercase tracking-wider">Uptime</div>
      <div class="mt-2 flex items-baseline gap-2">
        <div class="text-3xl font-bold tabular-nums" id="uptime-pretty">—</div>
      </div>
      <div class="mt-1 text-xs text-slate-400 tabular-nums">
        server <span id="server-up" class="text-slate-200">—</span>s ·
        pid <span id="server-pid" class="text-slate-200">—</span>
      </div>
    </div>
  </div>
</section>

<!-- Top processes -->
<section class="mx-auto max-w-6xl px-6 pb-8">
  <h2 class="text-sm font-semibold uppercase tracking-wider text-slate-400 mb-3">Top processes <span class="font-normal normal-case text-slate-500">(by RSS, refresh 5s)</span></h2>
  <div class="card rounded-2xl ring-1 ring-white/10 overflow-hidden">
    <table class="w-full text-sm font-mono">
      <thead class="text-xs uppercase text-slate-400 bg-white/5">
        <tr>
          <th class="text-left px-5 py-3 w-20">PID</th>
          <th class="text-left px-5 py-3">Command</th>
          <th class="text-right px-5 py-3 w-32">RSS</th>
          <th class="text-left px-5 py-3 w-20">State</th>
        </tr>
      </thead>
      <tbody id="proc-tbody" class="divide-y divide-white/5">
        <tr><td class="px-5 py-4 text-slate-500" colspan="4">loading…</td></tr>
      </tbody>
    </table>
  </div>
</section>

<!-- What is a sandbox -->
<section class="mx-auto max-w-6xl px-6 pb-8">
  <div class="card rounded-2xl ring-1 ring-white/10 p-6">
    <h2 class="text-lg font-semibold">What is a sandbox?</h2>
    <p class="text-slate-300 mt-2 max-w-3xl">
      A <strong>sandbox</strong> is an on-demand, isolated Linux VM with its own filesystem,
      network, and processes. Spin one up in seconds, run anything inside, expose ports
      publicly or behind Entra, snapshot it, then throw it away. Perfect for agent tool-use,
      code interpreters, per-PR preview environments, and untrusted user code.
    </p>
  </div>
</section>

<!-- Prerequisites -->
<section class="mx-auto max-w-6xl px-6 pb-8">
  <h2 class="text-sm font-semibold uppercase tracking-wider text-slate-400 mb-3">Prerequisites</h2>
  <div class="card rounded-2xl ring-1 ring-white/10 p-6">
    <ul class="space-y-2.5 text-sm text-slate-300">
      <li class="flex gap-3">
        <span class="text-brand-400 mt-0.5">▸</span>
        <span><a href="https://learn.microsoft.com/cli/azure/install-azure-cli" target="_blank" rel="noopener" class="text-brand-400 hover:underline">Azure CLI</a>
        installed and logged in &mdash; <code class="font-mono text-xs bg-white/5 ring-1 ring-white/10 rounded px-1.5 py-0.5">az login</code></span>
      </li>
      <li class="flex gap-3">
        <span class="text-brand-400 mt-0.5">▸</span>
        <span>An Azure subscription with a resource group</span>
      </li>
      <li class="flex gap-3">
        <span class="text-brand-400 mt-0.5">▸</span>
        <span>For the Python SDK: <strong>Python &ge; 3.10</strong></span>
      </li>
      <li class="flex gap-3">
        <span class="text-brand-400 mt-0.5">▸</span>
        <span>On hosted compute (Azure VMs, Container Apps, CI/CD), <code class="font-mono text-xs bg-white/5 ring-1 ring-white/10 rounded px-1.5 py-0.5">DefaultAzureCredential</code> automatically uses managed identity &mdash; no Azure CLI needed.</span>
      </li>
    </ul>
  </div>
</section>

<!-- Get started -->
<section id="get-started" class="mx-auto max-w-6xl px-6 pb-16">
  <h2 class="text-2xl font-bold tracking-tight">Quick start</h2>
  <p class="text-slate-400 mt-1">Zero to sandbox in five minutes. Install, log in, create a sandbox group, grant yourself data&#8209;plane access, then run a command. Pick your flavor.</p>

  <div class="mt-5 card rounded-2xl ring-1 ring-white/10 overflow-hidden">
    <div class="flex border-b border-white/10 text-sm">
      <button data-tab="cli" class="tab px-5 py-3 font-medium border-b-2 border-brand-400 text-white">aca CLI</button>
      <button data-tab="sdk" class="tab px-5 py-3 font-medium border-b-2 border-transparent text-slate-400 hover:text-white">Python SDK</button>
      <div class="ml-auto px-4 py-2 flex items-center">
        <button id="copy-btn" class="text-xs rounded-md bg-white/5 ring-1 ring-white/10 px-3 py-1.5 hover:bg-white/10 transition">Copy</button>
      </div>
    </div>

    <pre id="snippet-cli" class="tab-pane block p-6 text-sm overflow-x-auto text-slate-100"><span class="text-slate-500"># Install (Linux / macOS)</span>
curl -fsSL https://aka.ms/aca-cli-install | sh

<span class="text-slate-500"># Install (Windows PowerShell)</span>
<span class="text-slate-500"># irm https://aka.ms/aca-cli-install-ps | iex</span>

<span class="text-slate-500"># 0. Login to Azure</span>
az login

<span class="text-slate-500"># 1. Create a resource group (skip if you have one)</span>
az group create --name my-rg --location eastus2

<span class="text-slate-500"># 2. Create a sandbox group (--set-config saves my-rg + region as defaults)</span>
<span class="text-fuchsia-300">aca</span> sandboxgroup create --name my-sandbox-group -g my-rg --location eastus2 --set-config

<span class="text-slate-500"># 3. Grant yourself data-plane access</span>
<span class="text-fuchsia-300">aca</span> sandboxgroup role create \
  --role <span class="text-emerald-300">"Container Apps SandboxGroup Data Owner"</span> \
  --principal-id $(az ad signed-in-user show --query id -o tsv)

<span class="text-slate-500"># 4. Verify setup</span>
<span class="text-fuchsia-300">aca</span> doctor

<span class="text-slate-500"># 5. Create a sandbox and capture its id</span>
SANDBOX_ID=$(<span class="text-fuchsia-300">aca</span> sandbox create --disk ubuntu -o json | jq -r .id)

<span class="text-slate-500"># 6. Run a command</span>
<span class="text-fuchsia-300">aca</span> sandbox exec --id <span class="text-amber-300">"$SANDBOX_ID"</span> -c <span class="text-emerald-300">"echo hello world &amp;&amp; uname -a"</span>

<span class="text-slate-500"># 7. Clean up</span>
<span class="text-fuchsia-300">aca</span> sandbox delete --id <span class="text-amber-300">"$SANDBOX_ID"</span> --yes</pre>

    <pre id="snippet-sdk" class="tab-pane hidden p-6 text-sm overflow-x-auto text-slate-100"><span class="text-slate-500"># Install</span>
pip install azure-containerapps-sandbox

<span class="text-slate-500"># Then in Python (assumes sandbox group + role already exist — see CLI tab or README):</span>
<span class="text-brand-400">from</span> azure.identity <span class="text-brand-400">import</span> DefaultAzureCredential
<span class="text-brand-400">from</span> azure.containerapps.sandbox <span class="text-brand-400">import</span> SandboxGroupClient, endpoint_for_region

credential      = DefaultAzureCredential()
subscription_id = <span class="text-emerald-300">"&lt;your-subscription-id&gt;"</span>
resource_group  = <span class="text-emerald-300">"my-rg"</span>
sandbox_group   = <span class="text-emerald-300">"my-sandbox-group"</span>
region          = <span class="text-emerald-300">"eastus2"</span>

client  = SandboxGroupClient(endpoint_for_region(region), credential,
    subscription_id=subscription_id,
    resource_group=resource_group,
    sandbox_group=sandbox_group)

sandbox = client.begin_create_sandbox(disk=<span class="text-emerald-300">"ubuntu"</span>).result()
<span class="text-brand-400">print</span>(sandbox.exec(<span class="text-emerald-300">"echo hello world &amp;&amp; uname -a"</span>).stdout)
sandbox.delete()

<span class="text-slate-500"># First-time bootstrap (resource group, sandbox group, role assignment):</span>
<span class="text-slate-500"># https://github.com/microsoft/azure-container-apps/blob/main/docs/early/python-sdk/README.md</span></pre>
  </div>

  <div class="mt-4 grid sm:grid-cols-2 gap-3 text-sm">
    <a href="https://github.com/microsoft/azure-container-apps/blob/main/docs/early/aca-cli/README.md"
       target="_blank" rel="noopener"
       class="rounded-lg bg-white/5 ring-1 ring-white/10 p-4 hover:bg-white/10 transition flex items-center justify-between">
      <div>
        <div class="font-medium text-slate-100">aca CLI README ↗</div>
        <div class="text-slate-400 text-xs mt-0.5">Install, ports, files, snapshots, egress, managed identity</div>
      </div>
      <span class="text-brand-400">→</span>
    </a>
    <a href="https://github.com/microsoft/azure-container-apps/blob/main/docs/early/python-sdk/README.md"
       target="_blank" rel="noopener"
       class="rounded-lg bg-white/5 ring-1 ring-white/10 p-4 hover:bg-white/10 transition flex items-center justify-between">
      <div>
        <div class="font-medium text-slate-100">Python SDK README ↗</div>
        <div class="text-slate-400 text-xs mt-0.5">Clients, exec, files, ports, egress, snapshots, lifecycle</div>
      </div>
      <span class="text-brand-400">→</span>
    </a>
  </div>

  <div class="mt-4 text-sm text-slate-400">
    Prefer a UI? Open <a href="https://sandboxes.azure.com" target="_blank" rel="noopener" class="text-brand-400 hover:underline">sandboxes.azure.com</a> and click <em>New sandbox</em>.
  </div>
</section>

<!-- Endpoints -->
<section class="mx-auto max-w-6xl px-6 pb-16">
  <h2 class="text-sm font-semibold uppercase tracking-wider text-slate-400 mb-3">Endpoints on this sandbox</h2>
  <div class="card rounded-2xl ring-1 ring-white/10 divide-y divide-white/5 text-sm font-mono">
    <a href="/"             class="flex items-center gap-4 px-5 py-3 hover:bg-white/5"><span class="text-emerald-400 w-10">GET</span><span class="flex-1">/</span><span class="text-slate-500">this page</span></a>
    <a href="/healthz"      class="flex items-center gap-4 px-5 py-3 hover:bg-white/5"><span class="text-emerald-400 w-10">GET</span><span class="flex-1">/healthz</span><span class="text-slate-500">liveness probe</span></a>
    <a href="/api/hello"    class="flex items-center gap-4 px-5 py-3 hover:bg-white/5"><span class="text-emerald-400 w-10">GET</span><span class="flex-1">/api/hello</span><span class="text-slate-500">message + uptime</span></a>
    <a href="/api/info"     class="flex items-center gap-4 px-5 py-3 hover:bg-white/5"><span class="text-emerald-400 w-10">GET</span><span class="flex-1">/api/info</span><span class="text-slate-500">runtime + host info</span></a>
    <a href="/api/sysinfo"  class="flex items-center gap-4 px-5 py-3 hover:bg-white/5"><span class="text-emerald-400 w-10">GET</span><span class="flex-1">/api/sysinfo</span><span class="text-slate-500">uname-style system info</span></a>
    <a href="/api/stats"    class="flex items-center gap-4 px-5 py-3 hover:bg-white/5"><span class="text-emerald-400 w-10">GET</span><span class="flex-1">/api/stats</span><span class="text-slate-500">live cpu/mem/uptime</span></a>
    <a href="/api/processes"class="flex items-center gap-4 px-5 py-3 hover:bg-white/5"><span class="text-emerald-400 w-10">GET</span><span class="flex-1">/api/processes</span><span class="text-slate-500">top processes by RSS</span></a>
  </div>
</section>

<footer class="border-t border-white/5 mt-8">
  <div class="mx-auto max-w-6xl px-6 py-6 flex flex-col md:flex-row gap-3 items-center justify-between text-sm text-slate-400">
    <div>Served from <code class="text-slate-200">${esc(h.hostname)}</code> · started <code class="text-slate-200">${STARTED_AT.toISOString()}</code></div>
    <div class="flex gap-4">
      <a href="https://sandboxes.azure.com" target="_blank" rel="noopener" class="hover:text-white">sandboxes.azure.com ↗</a>
      <a href="https://github.com/annaji-msft/ai-apps/tree/main/samples/sandboxes/scenarios/01-webapps/simple-anonymous" target="_blank" rel="noopener" class="hover:text-white">Source on GitHub ↗</a>
    </div>
  </div>
</footer>

<script>
  // Hero uptime ticker
  (function() {
    var el = document.getElementById('hero-uptime');
    var n = parseInt(el.textContent, 10) || 0;
    setInterval(function() { el.textContent = (++n).toString(); }, 1000);
  })();

  // Pretty uptime (s -> e.g. "2d 4h 13m")
  function fmtUptime(sec) {
    sec = Math.max(0, sec | 0);
    var d = Math.floor(sec / 86400); sec %= 86400;
    var h = Math.floor(sec / 3600);  sec %= 3600;
    var m = Math.floor(sec / 60);    var s = sec % 60;
    if (d) return d + 'd ' + h + 'h ' + m + 'm';
    if (h) return h + 'h ' + m + 'm';
    if (m) return m + 'm ' + s + 's';
    return s + 's';
  }

  var lastProcCount = null;
  async function pollStats() {
    try {
      var r = await fetch('/api/stats', { cache: 'no-store' });
      if (!r.ok) throw new Error(r.status);
      var d = await r.json();
      var cpus = d.cpus || 1;
      var load1pct = Math.min(100, Math.round((d.loadavg[0] / cpus) * 100));
      document.getElementById('cpu-1m').textContent = d.loadavg[0].toFixed(2);
      document.getElementById('cpu-5m').textContent = d.loadavg[1].toFixed(2);
      document.getElementById('cpu-15m').textContent = d.loadavg[2].toFixed(2);
      document.getElementById('cpu-count').textContent = cpus;
      document.getElementById('cpu-bar').style.width = load1pct + '%';
      document.getElementById('mem-pct').textContent = d.memUsedPct.toFixed(1) + '%';
      document.getElementById('mem-used').textContent = (d.memTotalMB - d.memFreeMB).toLocaleString();
      document.getElementById('mem-total').textContent = d.memTotalMB.toLocaleString();
      document.getElementById('mem-bar').style.width = d.memUsedPct + '%';
      document.getElementById('proc-count').textContent = d.procCount;
      if (lastProcCount !== null) {
        var delta = d.procCount - lastProcCount;
        var el = document.getElementById('proc-delta');
        el.textContent = delta === 0 ? 'running' : (delta > 0 ? ('+' + delta + ' since last') : (delta + ' since last'));
      }
      lastProcCount = d.procCount;
      document.getElementById('uptime-pretty').textContent = fmtUptime(d.uptime);
      document.getElementById('server-up').textContent = d.serverUptime;
      document.getElementById('server-pid').textContent = d.serverPid;
      if (typeof d.secondsUntilSuspend === 'number') {
        var sleepEl = document.getElementById('hero-sleep');
        sleepEl.textContent = fmtUptime(d.secondsUntilSuspend) + ' (idle ' + fmtUptime(d.idleTimeoutSec) + ')';
      }
      var fresh = document.getElementById('stats-fresh');
      fresh.classList.remove('text-rose-400'); fresh.classList.add('text-emerald-400');
    } catch (e) {
      var fresh = document.getElementById('stats-fresh');
      fresh.classList.remove('text-emerald-400'); fresh.classList.add('text-rose-400');
    }
  }

  async function pollProcs() {
    try {
      var r = await fetch('/api/processes', { cache: 'no-store' });
      if (!r.ok) throw new Error(r.status);
      var rows = await r.json();
      var tbody = document.getElementById('proc-tbody');
      tbody.innerHTML = rows.map(function(p) {
        var rss = p.rssKB >= 1024 ? (p.rssKB / 1024).toFixed(1) + ' MB' : p.rssKB + ' KB';
        return '<tr class="hover:bg-white/5">' +
          '<td class="px-5 py-2 text-slate-400">' + p.pid + '</td>' +
          '<td class="px-5 py-2">' + (p.comm || '?').replace(/[<>&]/g, '') + '</td>' +
          '<td class="px-5 py-2 text-right tabular-nums">' + rss + '</td>' +
          '<td class="px-5 py-2"><span class="text-xs rounded bg-white/5 ring-1 ring-white/10 px-2 py-0.5">' + p.state + '</span></td>' +
          '</tr>';
      }).join('') || '<tr><td class="px-5 py-4 text-slate-500" colspan="4">(no processes)</td></tr>';
    } catch (_) {}
  }

  pollStats(); pollProcs();
  setInterval(pollStats, 2000);
  setInterval(pollProcs, 5000);

  // Tab switcher
  document.querySelectorAll('.tab').forEach(function(btn) {
    btn.addEventListener('click', function() {
      var which = btn.getAttribute('data-tab');
      document.querySelectorAll('.tab').forEach(function(b) {
        b.classList.remove('border-brand-400', 'text-white');
        b.classList.add('border-transparent', 'text-slate-400');
      });
      btn.classList.add('border-brand-400', 'text-white');
      btn.classList.remove('border-transparent', 'text-slate-400');
      document.getElementById('snippet-cli').classList.toggle('hidden', which !== 'cli');
      document.getElementById('snippet-sdk').classList.toggle('hidden', which !== 'sdk');
    });
  });

  // Copy button
  document.getElementById('copy-btn').addEventListener('click', function() {
    var visible = document.querySelector('.tab-pane:not(.hidden)');
    var text = visible ? visible.textContent : '';
    navigator.clipboard.writeText(text).then(function() {
      var b = document.getElementById('copy-btn');
      var orig = b.textContent;
      b.textContent = 'Copied!';
      setTimeout(function() { b.textContent = orig; }, 1200);
    });
  });
</script>
</body>
</html>`;
}

// ---------- routing ----------

const routes = {
  '/':              { handler: (res) => html(res, 200, page()) },
  '/healthz':       { handler: (res) => json(res, 200, { status: 'ok' }) },
  '/api/hello':     { handler: (res) => json(res, 200, hello()) },
  '/api/info':      { handler: (res) => json(res, 200, info()) },
  '/api/sysinfo':   { handler: (res) => json(res, 200, sysinfo()) },
  '/api/stats':     { handler: (res) => json(res, 200, stats()) },
  '/api/processes': { handler: (res) => json(res, 200, topProcesses(10)) },
};

http.createServer((req, res) => {
  const path = req.url.split('?')[0];
  LAST_REQ_AT = Date.now();
  const route = routes[path];
  if (route) {
    route.handler(res);
  } else {
    json(res, 404, { error: 'not found', path });
  }
}).listen(PORT, '0.0.0.0', () => {
  console.log(`Server listening on :${PORT}`);
});
