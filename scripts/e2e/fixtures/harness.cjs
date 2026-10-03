'use strict';
// End-to-end harness core shared by the Node bootstrap checks and the
// Playwright fixtures. It owns every process it starts, keeps generated
// material in place, and never sends the backend token to page scripts.
const crypto = require('node:crypto');
const fs = require('node:fs');
const http = require('node:http');
const net = require('node:net');
const path = require('node:path');
const zlib = require('node:zlib');
const { execFileSync, spawn, spawnSync } = require('node:child_process');

const REPO_ROOT = path.resolve(__dirname, '..', '..', '..');
const PORT_LINE = /^VISION_AI_STUDIO_PORT=([1-9]\d{0,4})$/;
const PROXY_PREFIXES = ['/api/', '/ws/'];
// Same imports the desktop supervisor checks before it would try to install
// packages; the harness refuses to start instead of mutating an interpreter.
const BACKEND_IMPORTS = ['fastapi', 'uvicorn', 'pydantic', 'torch', 'torchvision', 'cv2', 'PIL', 'numpy', 'sklearn', 'psutil', 'httpx'];
const CONTENT_TYPES = {
  '.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8', '.mjs': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8', '.json': 'application/json', '.map': 'application/json', '.svg': 'image/svg+xml',
  '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.ico': 'image/x-icon', '.woff': 'font/woff',
  '.woff2': 'font/woff2', '.txt': 'text/plain; charset=utf-8',
};
const IS_WINDOWS = process.platform === 'win32';
const liveGroups = new Set();

// A crashed worker must not leave owned backends running. Only process groups
// this module created are signalled; on Windows tree termination uses taskkill.
process.once('exit', () => {
  for (const pid of liveGroups) {
    try {
      if (IS_WINDOWS) execFileSync('taskkill', ['/pid', String(pid), '/T', '/F'], { stdio: 'ignore' });
      else process.kill(-pid, 'SIGKILL');
    } catch { /* already gone */ }
  }
});

const delay = ms => new Promise(resolve => setTimeout(resolve, ms));

// Races a promise against a deadline and always clears the timer, so pending
// deadlines never keep a finished test process alive.
function within(promise, ms, onTimeout = () => null) {
  let timer;
  const deadline = new Promise((resolve, reject) => {
    timer = setTimeout(() => { try { resolve(onTimeout()); } catch (error) { reject(error); } }, ms);
  });
  return Promise.race([promise, deadline]).finally(() => clearTimeout(timer));
}

function createPortParser() {
  let pending = '';
  let port = null;
  return chunk => {
    if (port !== null) return port;
    const lines = (pending + String(chunk)).split('\n');
    pending = lines.pop().slice(-4096);
    for (const line of lines) {
      const match = PORT_LINE.exec(line.replace(/\r$/, ''));
      if (match && Number(match[1]) <= 65535) {
        port = Number(match[1]);
        break;
      }
    }
    return port;
  };
}

// App and harness variables can redirect a backend to shared stores; tests
// set the ones they need explicitly.
function sanitizeEnv(baseEnv = process.env) {
  const env = {};
  for (const [key, value] of Object.entries(baseEnv)) {
    if (/^(VISION_|MODU_VISION_|MV_E2E_)/i.test(key) || /^ELECTRON_RUN_AS_NODE$/i.test(key)) continue;
    env[key] = value;
  }
  return env;
}

function resolvePython(env = process.env, platform = process.platform) {
  return env.MV_E2E_PYTHON || env.VISION_AI_PYTHON || (platform === 'win32' ? 'python' : 'python3');
}

function assertSpawnable(command, platform = process.platform) {
  if (platform === 'win32' && /\.(bat|cmd)$/i.test(command)) {
    throw new Error(`Point MV_E2E_PYTHON at python.exe, not a ${path.extname(command)} shim: ${command}`);
  }
}

function sha256File(file) {
  return crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex');
}

function describeFile(file) {
  try {
    const stat = fs.statSync(file);
    return { path: file, bytes: stat.size, sha256: sha256File(file) };
  } catch {
    return { path: file, missing: true };
  }
}

const CRC_TABLE = Array.from({ length: 256 }, (_, n) => {
  let c = n;
  for (let k = 0; k < 8; k += 1) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
  return c >>> 0;
});

function crc32(buffer) {
  let crc = 0xffffffff;
  for (const byte of buffer) crc = CRC_TABLE[(crc ^ byte) & 0xff] ^ (crc >>> 8);
  return (crc ^ 0xffffffff) >>> 0;
}

function pngChunk(type, data) {
  const length = Buffer.alloc(4);
  length.writeUInt32BE(data.length);
  const body = Buffer.concat([Buffer.from(type, 'ascii'), data]);
  const crc = Buffer.alloc(4);
  crc.writeUInt32BE(crc32(body));
  return Buffer.concat([length, body, crc]);
}

// Small deterministic RGB images; a dark square marks the defect sample.
function syntheticPng(size, defect) {
  const rows = [];
  for (let y = 0; y < size; y += 1) {
    const row = Buffer.alloc(1 + size * 3);
    for (let x = 0; x < size; x += 1) {
      const dark = defect && x >= size / 4 && x < size / 2 && y >= size / 4 && y < size / 2;
      row.fill(dark ? 30 : 180, 1 + x * 3, 4 + x * 3);
    }
    rows.push(row);
  }
  const header = Buffer.alloc(13);
  header.writeUInt32BE(size, 0);
  header.writeUInt32BE(size, 4);
  header[8] = 8; header[9] = 2; header[10] = 0; header[11] = 0; header[12] = 0;
  return Buffer.concat([
    Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]),
    pngChunk('IHDR', header), pngChunk('IDAT', zlib.deflateSync(Buffer.concat(rows))), pngChunk('IEND', Buffer.alloc(0)),
  ]);
}

function createWorkspace(root) {
  if (fs.existsSync(root) && fs.readdirSync(root).length) {
    throw new Error(`Workspace root is not empty: ${root}`);
  }
  const workspace = {
    root,
    userData: path.join(root, 'userData'),
    projects: path.join(root, 'projects'),
    home: path.join(root, 'home'),
    dataset: path.join(root, 'dataset'),
    logs: path.join(root, 'logs'),
  };
  for (const dir of Object.values(workspace)) fs.mkdirSync(dir, { recursive: true });
  // Specs start past the first-run guide (S2-01), which would otherwise open over an empty project; the first-start
  // spec writes dismissed:false before it loads the app.
  fs.writeFileSync(path.join(workspace.userData, 'onboarding.json'), JSON.stringify({ version: 1, dismissed: true }));
  workspace.images = [['ok', false], ['ng', true]].map(([label, defect]) => {
    const dir = path.join(workspace.dataset, label);
    fs.mkdirSync(dir, { recursive: true });
    const file = path.join(dir, `sample-${label}.png`);
    fs.writeFileSync(file, syntheticPng(32, defect));
    return { label, ...describeFile(file) };
  });
  return workspace;
}

function backendCommand({ python = resolvePython(), repoRoot = REPO_ROOT, workspace, logLevel = 'warning', isolateHome = true }) {
  const env = {
    PYTHONPATH: repoRoot,
    PYTHONDONTWRITEBYTECODE: '1',
    VISION_AI_STUDIO_USER_DATA_DIR: workspace.userData,
  };
  if (isolateHome) Object.assign(env, { HOME: workspace.home, USERPROFILE: workspace.home });
  return {
    command: python,
    args: [path.join(repoRoot, 'backend', 'main.py'), '--host', '127.0.0.1', '--port', '0',
      '--project-dir', workspace.projects, '--log-level', logLevel],
    cwd: workspace.root,
    env,
    logDir: workspace.logs,
    ownershipMarker: workspace.root,
  };
}

function signalTree(pid, signal) {
  if (IS_WINDOWS) {
    try {
      execFileSync('taskkill', ['/pid', String(pid), '/T', ...(signal === 'SIGKILL' ? ['/F'] : [])], { stdio: 'ignore' });
    } catch { /* tree already gone */ }
    return;
  }
  try { process.kill(-pid, signal); } catch { /* group already gone */ }
}

function groupAlive(pgid) {
  if (IS_WINDOWS) return false;
  try { process.kill(-pgid, 0); return true; } catch (error) { return error.code === 'EPERM'; }
}

function processAlive(pid) {
  try { process.kill(pid, 0); return true; } catch (error) { return error.code === 'EPERM'; }
}

// Executables and module markers of processes the harness may have caused.
const WORKER_EXECUTABLE = /^(python(\d+(\.\d+)*)?(\.exe)?|node(\.exe)?|electron(\.exe)?|electron helper.*|vision_ai_backend(\.exe)?)$/i;
const WORKER_MARKERS = ['backend/main.py', 'backend\\main.py', 'backend.engine.', 'backend.training_cli', '--basic-training-worker',
  '--flow-package-runner', '--flow-package-worker', '--inspection-service', '--managed-service-project'];

function scanProcesses() {
  try {
    if (IS_WINDOWS) {
      // UTF-8 JSON keeps non-ASCII workspace paths intact on localized systems.
      const output = execFileSync('powershell.exe', ['-NoProfile', '-NonInteractive', '-Command',
        '[Console]::OutputEncoding=New-Object System.Text.UTF8Encoding $false; Get-CimInstance Win32_Process | '
        + 'Select-Object ProcessId,ParentProcessId,CommandLine | ConvertTo-Json -Compress'],
      { encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'], windowsHide: true, maxBuffer: 64 * 1024 * 1024 });
      const parsed = JSON.parse(output.replace(/^\uFEFF/, '') || '[]');
      return { rows: (Array.isArray(parsed) ? parsed : [parsed]).map(row => ({
        pid: Number(row.ProcessId), ppid: Number(row.ParentProcessId), pgid: null, uid: null, command: row.CommandLine || '',
      })), error: null };
    }
    // Under the C locale ps escapes non-ASCII bytes, which would hide Korean paths.
    const locale = process.platform === 'darwin' ? 'en_US.UTF-8' : 'C.UTF-8';
    const output = execFileSync('ps', ['-axww', '-o', 'pid=', '-o', 'ppid=', '-o', 'pgid=', '-o', 'uid=', '-o', 'command='], {
      encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'], maxBuffer: 64 * 1024 * 1024,
      env: { ...process.env, LC_ALL: locale, LANG: locale },
    });
    const rows = output.split('\n').map(line => /^\s*(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(.*)$/.exec(line)).filter(Boolean)
      .map(match => ({ pid: Number(match[1]), ppid: Number(match[2]), pgid: Number(match[3]), uid: Number(match[4]), command: match[5] }));
    return { rows, error: null };
  } catch (error) {
    return { rows: [], error: error.message };
  }
}

function markerForms(marker) {
  const forms = new Set([marker]);
  try { forms.add(fs.realpathSync.native(marker)); } catch { /* marker may not exist yet */ }
  const withSeparators = [...forms].flatMap(form => [form, form.replace(/\\/g, '/')]).map(form => form.replace(/[\\/]+$/, ''));
  return [...new Set(withSeparators)].map(form => (IS_WINDOWS ? form.toLowerCase() : form));
}

function executableName(command) {
  const first = command.startsWith('"') ? command.slice(1, command.indexOf('"', 1)) : command.split(/\s+/)[0];
  return path.basename(first.replace(/\\/g, '/'));
}

// A process is reaped only when it belongs to this user, references a path
// inside the workspace and is a Python/Node/Electron/backend worker. Anything
// else that merely mentions the path (log viewers, shells) is reported only.
function findProcessesReferencing(marker) {
  if (!marker) return { owned: [], others: [], rows: [], error: null };
  const scan = scanProcesses();
  const forms = markerForms(marker);
  const protectedPids = ancestorsOf(scan.rows, process.pid);
  const uid = typeof process.getuid === 'function' ? process.getuid() : null;
  const owned = [];
  const others = [];
  for (const row of scan.rows) {
    // The harness never reaps itself or its ancestors (e.g. a wrapper script);
    // terminateProcesses also never signals the harness's own process group.
    if (!row.pid || protectedPids.has(row.pid)) continue;
    const haystack = IS_WINDOWS ? row.command.toLowerCase() : row.command;
    const normalized = haystack.replace(/\\/g, '/');
    if (!forms.some(form => haystack.includes(`${form}${path.sep}`) || normalized.includes(`${form.replace(/\\/g, '/')}/`)
      || haystack.endsWith(form) || normalized.endsWith(form.replace(/\\/g, '/')))) continue;
    const worker = WORKER_EXECUTABLE.test(executableName(row.command)) || WORKER_MARKERS.some(item => row.command.includes(item));
    if ((uid === null || row.uid === uid) && worker) owned.push(row);
    else others.push({ pid: row.pid, command: row.command.slice(0, 400) });
  }
  return { owned, others, rows: scan.rows, error: scan.error };
}

function ancestorsOf(rows, pid) {
  const parent = new Map(rows.map(row => [row.pid, row.ppid]));
  const chain = new Set([pid, process.ppid]);
  for (let current = parent.get(pid); current && !chain.has(current); current = parent.get(current)) chain.add(current);
  return chain;
}

function ownGroup(rows) {
  return rows.find(row => row.pid === process.pid)?.pgid ?? null;
}

function descendantsOf(rows, roots) {
  const children = new Map();
  for (const row of rows) {
    if (!children.has(row.ppid)) children.set(row.ppid, []);
    children.get(row.ppid).push(row);
  }
  const found = new Map();
  const queue = [...roots];
  while (queue.length) {
    const next = queue.shift();
    for (const child of children.get(next) || []) {
      if (!found.has(child.pid) && child.pid !== process.pid) { found.set(child.pid, child); queue.push(child.pid); }
    }
  }
  return [...found.values()];
}

async function terminateProcesses(owned, rows = []) {
  const protectedPids = ancestorsOf(rows, process.pid);
  const group = ownGroup(rows);
  const targets = new Map(owned.filter(row => !protectedPids.has(row.pid)).map(row => [row.pid, row]));
  for (const child of descendantsOf(rows, [...targets.keys()])) {
    if (!protectedPids.has(child.pid)) targets.set(child.pid, child);
  }
  const signal = (row, name) => {
    if (IS_WINDOWS) { signalTree(row.pid, 'SIGKILL'); return; }
    // A worker that leads its own session or group takes its whole group.
    const leadsGroup = row.pgid === row.pid && row.pgid !== group;
    try { process.kill(leadsGroup ? -row.pid : row.pid, name); } catch { /* gone */ }
  };
  for (const row of targets.values()) signal(row, 'SIGTERM');
  const deadline = Date.now() + 3000;
  while (Date.now() < deadline && [...targets.keys()].some(processAlive)) await delay(50);
  for (const row of [...targets.values()].filter(item => processAlive(item.pid))) signal(row, 'SIGKILL');
  for (let i = 0; i < 20 && [...targets.keys()].some(processAlive); i += 1) await delay(50);
  return [...targets.values()].map(row => ({ pid: row.pid, command: row.command.slice(0, 400), alive: processAlive(row.pid) }));
}

function httpGet(url, { headers = {}, timeoutMs = 5000 } = {}) {
  return new Promise((resolve, reject) => {
    const req = http.get(url, { headers, timeout: timeoutMs }, res => {
      const chunks = [];
      res.on('data', chunk => chunks.push(chunk));
      res.on('end', () => resolve({ status: res.statusCode, headers: res.headers, body: Buffer.concat(chunks).toString('utf8') }));
    });
    req.on('timeout', () => req.destroy(new Error(`GET ${url} timed out`)));
    req.on('error', reject);
  });
}

async function startOwnedBackend({
  command, args, cwd, env = {}, logDir, timeoutMs = 120000, healthPath = '/health', baseEnv = sanitizeEnv(process.env),
  ownershipMarker = null,
} = {}) {
  if (!command || !Array.isArray(args) || !logDir) throw new TypeError('command, args and logDir are required');
  assertSpawnable(command);
  const token = crypto.randomBytes(32).toString('hex');
  fs.mkdirSync(logDir, { recursive: true });
  const logs = { stdout: path.join(logDir, 'backend.stdout.log'), stderr: path.join(logDir, 'backend.stderr.log') };
  const stdoutLog = fs.createWriteStream(logs.stdout, { flags: 'a' });
  const stderrLog = fs.createWriteStream(logs.stderr, { flags: 'a' });
  const childEnv = { ...baseEnv, ...env, VISION_AI_STUDIO_API_TOKEN: token, PYTHONUNBUFFERED: '1' };
  const startedAt = new Date().toISOString();
  const child = spawn(command, args, {
    cwd, env: childEnv, stdio: ['ignore', 'pipe', 'pipe'], detached: !IS_WINDOWS, windowsHide: true,
  });
  if (child.pid) liveGroups.add(child.pid);
  const stderrTail = [];
  const parser = createPortParser();
  let exitInfo = null;
  let spawnError = null;
  const exited = new Promise(resolve => {
    child.once('exit', (code, signal) => { exitInfo = { code, signal, at: new Date().toISOString() }; resolve(exitInfo); });
    child.once('error', error => { spawnError = error; exitInfo = { code: null, signal: null, error: error.message, at: new Date().toISOString() }; resolve(exitInfo); });
  });
  const closed = new Promise(resolve => child.once('close', resolve));
  child.stderr.on('data', chunk => {
    stderrLog.write(chunk);
    stderrTail.push(...String(chunk).split(/\r?\n/).filter(Boolean));
    stderrTail.splice(0, Math.max(0, stderrTail.length - 40));
  });
  const portPromise = new Promise((resolve, reject) => {
    child.stdout.on('data', chunk => {
      stdoutLog.write(chunk);
      const port = parser(chunk);
      if (port) resolve(port);
    });
    exited.then(async info => {
      await within(closed, 1000);
      const cause = spawnError ? `could not start: ${spawnError.message}` : `exit code ${info.code}, signal ${info.signal}`;
      reject(new Error(`Backend exited before it became ready (${cause}). stderr: ${stderrTail.join(' | ')}`));
    });
  });

  let stopping = null;
  const stop = ({ graceMs = 10000 } = {}) => {
    if (stopping) return stopping;
    stopping = (async () => {
      const began = Date.now();
      const exitedBeforeStop = exitInfo !== null;
      let forced = false;
      let forcedReason = null;
      if (exitInfo === null && child.pid) {
        if (IS_WINDOWS) {
          // Windows has no graceful signal for a windowless console process.
          forced = true;
          forcedReason = 'windows_console_process_has_no_graceful_signal';
          signalTree(child.pid, 'SIGKILL');
          await within(exited, 5000);
        } else {
          signalTree(child.pid, 'SIGTERM');
          if (!(await within(exited, graceMs))) {
            forced = true;
            forcedReason = 'grace_period_elapsed';
            signalTree(child.pid, 'SIGKILL');
            await within(exited, 5000);
          }
        }
      }
      // Descendants stay in the owned process group after the leader exits.
      if (child.pid && groupAlive(child.pid)) {
        signalTree(child.pid, 'SIGTERM');
        for (let i = 0; i < 40 && groupAlive(child.pid); i += 1) await delay(50);
        if (groupAlive(child.pid)) { forced = true; forcedReason ||= 'group_survived_leader'; signalTree(child.pid, 'SIGKILL'); }
      }
      const scan = findProcessesReferencing(ownershipMarker);
      const escaped = await terminateProcesses(scan.owned, scan.rows);
      if (child.pid && exitInfo !== null && !groupAlive(child.pid)) liveGroups.delete(child.pid);
      await within(closed, 1000);
      stdoutLog.end();
      stderrLog.end();
      return {
        exited: exitInfo !== null, exitCode: exitInfo?.code ?? null, signal: exitInfo?.signal ?? null,
        exitedBeforeStop, forced, forcedReason, groupAlive: child.pid ? groupAlive(child.pid) : false,
        escaped, unownedReferences: scan.others, scanError: ownershipMarker ? scan.error : null,
        durationMs: Date.now() - began, stoppedAt: new Date().toISOString(),
      };
    })();
    return stopping;
  };

  const withDeadline = (promise, what) => within(promise, timeoutMs, () => {
    throw new Error(`Timed out after ${timeoutMs} ms waiting for ${what}. stderr: ${stderrTail.join(' | ')}`);
  });
  try {
    const port = await withDeadline(portPromise, 'the backend port announcement');
    const baseUrl = `http://127.0.0.1:${port}`;
    const health = await withDeadline((async () => {
      for (;;) {
        if (exitInfo !== null) throw new Error(`Backend exited during health checks. stderr: ${stderrTail.join(' | ')}`);
        try {
          const response = await httpGet(`${baseUrl}${healthPath}`, { timeoutMs: 3000 });
          if (response.status === 200) {
            const payload = JSON.parse(response.body);
            if (payload.status === 'ok') return payload;
          }
        } catch { /* not ready yet */ }
        await delay(250);
      }
    })(), 'backend health');
    const backend = {
      pid: child.pid, port, baseUrl, health, logs, startedAt, stop,
      // Ports are assigned by the OS and announced by the backend, so runs never
      // collide with other applications on a shared machine.
      portPolicy: 'os_assigned_announced',
      running: () => exitInfo === null,
      stderrTail: () => [...stderrTail],
    };
    // Kept off enumeration so that recording the backend never records the token.
    Object.defineProperty(backend, 'token', { value: token, enumerable: false });
    return backend;
  } catch (error) {
    await stop({ graceMs: 3000 });
    throw error;
  }
}

function insideRoot(root, candidate) {
  const relative = path.relative(root, candidate);
  return relative === '' || (relative !== '..' && !relative.startsWith(`..${path.sep}`) && !path.isAbsolute(relative));
}

function resolveStaticFile(root, requestUrl) {
  let pathname;
  try {
    pathname = decodeURIComponent(new URL(requestUrl, 'http://harness.invalid').pathname);
  } catch {
    return { status: 400 };
  }
  if (pathname.includes('\0')) return { status: 400 };
  const candidate = path.resolve(root, `.${pathname}`);
  if (!insideRoot(root, candidate)) return { status: 403 };
  let file = candidate;
  try {
    if (fs.statSync(file).isDirectory()) file = path.join(file, 'index.html');
    const real = fs.realpathSync(file);
    if (!insideRoot(root, real)) return { status: 403 };
    if (!fs.statSync(real).isFile()) return { status: 404 };
    return { status: 200, file: real };
  } catch {
    return { status: 404 };
  }
}

function forwardedHeaders(incoming, backend) {
  const headers = { ...incoming, host: `127.0.0.1:${backend.port}`, 'x-vision-token': backend.token };
  // Same-origin requests only reach this point; the backend sees same-host calls.
  delete headers.origin;
  delete headers.referer;
  return headers;
}

async function startRendererServer({ staticDir, backend }) {
  const root = fs.realpathSync(staticDir);
  const counters = { served: 0, proxied: 0, rejected: 0, foreignOrigin: 0 };
  const rejectedPaths = [];
  const sockets = new Set();
  let ownOrigin = null;
  const reject = (url, status) => {
    counters.rejected += 1;
    if (rejectedPaths.length < 20) rejectedPaths.push({ status, path: url.split('?')[0] });
  };
  const isProxied = url => url === '/health' || url.startsWith('/health?') || PROXY_PREFIXES.some(prefix => url.startsWith(prefix));
  // The token is attached for same-origin pages only, which blocks other sites
  // from using this test server as an authenticated relay.
  // Requests without Origin (image loads, navigations) still carry Fetch
  // Metadata in browsers; cross-site ones are refused as well.
  const sameOrigin = headers => (headers.origin === undefined || headers.origin === ownOrigin)
    && (headers['sec-fetch-site'] === undefined || ['same-origin', 'none'].includes(headers['sec-fetch-site']));

  const server = http.createServer((req, res) => {
    const url = req.url || '/';
    if (isProxied(url)) {
      if (!sameOrigin(req.headers)) {
        counters.foreignOrigin += 1;
        reject(url, 403);
        res.writeHead(403, { 'content-type': 'text/plain; charset=utf-8' }).end('Foreign origin');
        return;
      }
      counters.proxied += 1;
      const upstream = http.request({
        host: '127.0.0.1', port: backend.port, method: req.method, path: url, headers: forwardedHeaders(req.headers, backend),
      }, upstreamResponse => {
        res.writeHead(upstreamResponse.statusCode || 502, upstreamResponse.headers);
        upstreamResponse.pipe(res);
        upstreamResponse.on('error', () => res.destroy());
      });
      upstream.on('error', () => {
        if (res.headersSent) { res.destroy(); return; }
        res.writeHead(502, { 'content-type': 'application/json' }).end(JSON.stringify({ detail: 'Owned backend is unavailable' }));
      });
      res.on('close', () => { if (!res.writableFinished) upstream.destroy(); });
      req.pipe(upstream);
      return;
    }
    if (req.method !== 'GET' && req.method !== 'HEAD') {
      reject(url, 405);
      res.writeHead(405).end();
      return;
    }
    const resolved = resolveStaticFile(root, url);
    if (resolved.status !== 200) {
      reject(url, resolved.status);
      res.writeHead(resolved.status, { 'content-type': 'text/plain; charset=utf-8' }).end('Not available');
      return;
    }
    counters.served += 1;
    res.writeHead(200, {
      'content-type': CONTENT_TYPES[path.extname(resolved.file).toLowerCase()] || 'application/octet-stream',
      'cache-control': 'no-store',
    });
    if (req.method === 'HEAD') { res.end(); return; }
    const stream = fs.createReadStream(resolved.file);
    stream.on('error', () => res.destroy());
    stream.pipe(res);
  });
  server.on('connection', socket => { sockets.add(socket); socket.on('close', () => sockets.delete(socket)); });

  server.on('upgrade', (req, socket, head) => {
    if (!(req.url || '').startsWith('/ws/') || !sameOrigin(req.headers)) {
      if (!sameOrigin(req.headers)) counters.foreignOrigin += 1;
      reject(req.url || '/', 403);
      socket.end('HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\n\r\n');
      return;
    }
    counters.proxied += 1;
    const upstream = net.connect(backend.port, '127.0.0.1', () => {
      let request = `${req.method} ${req.url} HTTP/1.1\r\n`;
      for (const [name, value] of Object.entries(forwardedHeaders(req.headers, backend))) {
        for (const item of Array.isArray(value) ? value : [value]) request += `${name}: ${item}\r\n`;
      }
      upstream.write(`${request}\r\n`);
      if (head && head.length) upstream.write(head);
      upstream.pipe(socket);
      socket.pipe(upstream);
    });
    sockets.add(upstream);
    upstream.on('close', () => sockets.delete(upstream));
    upstream.on('error', () => socket.destroy());
    socket.on('error', () => upstream.destroy());
    socket.on('close', () => upstream.destroy());
  });

  await new Promise((resolve, rejectListen) => {
    server.once('error', rejectListen);
    server.listen(0, '127.0.0.1', resolve);
  });
  const { port } = server.address();
  ownOrigin = `http://127.0.0.1:${port}`;
  return {
    port,
    origin: ownOrigin,
    url: `${ownOrigin}/index.html?port=${port}`,
    stats: () => ({ ...counters, rejectedPaths: [...rejectedPaths] }),
    close: () => new Promise(resolve => {
      for (const socket of sockets) socket.destroy();
      server.close(() => resolve());
    }),
  };
}

function runLogged(command, args, { cwd, env, logFile, timeoutMs }) {
  fs.mkdirSync(path.dirname(logFile), { recursive: true });
  return new Promise((resolve, reject) => {
    const log = fs.createWriteStream(logFile, { flags: 'a' });
    const child = spawn(command, args, { cwd, env, stdio: ['ignore', 'pipe', 'pipe'], windowsHide: true });
    child.stdout.pipe(log, { end: false });
    child.stderr.pipe(log, { end: false });
    const timer = setTimeout(() => child.kill(), timeoutMs);
    child.once('error', error => { clearTimeout(timer); log.end(); reject(error); });
    child.once('close', code => { clearTimeout(timer); log.end(); resolve(code); });
  });
}

async function buildRenderer({ repoRoot = REPO_ROOT, outDir, logFile, timeoutMs = 300000 }) {
  if (fs.existsSync(outDir) && fs.readdirSync(outDir).length) {
    throw new Error(`Renderer output directory is not empty: ${outDir}`);
  }
  const vite = path.join(repoRoot, 'node_modules', 'vite', 'bin', 'vite.js');
  const code = await runLogged(process.execPath, [vite, 'build', '--outDir', outDir, '--emptyOutDir', '--logLevel', 'warn'], {
    cwd: repoRoot, env: { ...process.env, NODE_ENV: 'production' }, logFile, timeoutMs,
  });
  const index = path.join(outDir, 'index.html');
  if (code !== 0 || !fs.existsSync(index)) throw new Error(`Renderer build failed with exit code ${code}; see ${logFile}`);
  return { outDir, index: describeFile(index), log: logFile };
}

// Builds the main and preload processes into a separate application folder
// so that end-to-end runs never rewrite the checkout's dist outputs.
async function buildElectronApp({ repoRoot = REPO_ROOT, appDir, logFile, timeoutMs = 300000 }) {
  const rendererIndex = path.join(appDir, 'dist', 'index.html');
  if (!fs.existsSync(rendererIndex)) throw new Error(`Build the renderer into ${path.join(appDir, 'dist')} first`);
  const outDir = path.join(appDir, 'dist-electron');
  if (fs.existsSync(outDir) && fs.readdirSync(outDir).length) throw new Error(`Electron output directory is not empty: ${outDir}`);
  const tsc = path.join(repoRoot, 'node_modules', 'typescript', 'bin', 'tsc');
  const code = await runLogged(process.execPath, [tsc, '-p', path.join(repoRoot, 'build', 'tsconfig.node.json'),
    '--outDir', outDir, '--tsBuildInfoFile', path.join(appDir, 'tsconfig.node.tsbuildinfo')], {
    cwd: repoRoot, env: process.env, logFile, timeoutMs,
  });
  if (code !== 0) throw new Error(`Electron main build failed with exit code ${code}; see ${logFile}`);
  const repoPackage = JSON.parse(fs.readFileSync(path.join(repoRoot, 'package.json'), 'utf8'));
  fs.writeFileSync(path.join(appDir, 'package.json'), `${JSON.stringify({
    name: repoPackage.name, version: repoPackage.version, private: true, main: 'dist-electron/main/index.js',
  }, null, 2)}\n`);
  // The supervisor resolves backend/main.py and scripts relative to the app path.
  for (const name of ['backend', 'scripts']) {
    const link = path.join(appDir, name);
    if (!fs.existsSync(link)) fs.symlinkSync(path.join(repoRoot, name), link, IS_WINDOWS ? 'junction' : 'dir');
  }
  return { appDir, outputs: assertElectronBuild(appDir), log: logFile };
}

function assertElectronBuild(appDir) {
  const outputs = [
    path.join(appDir, 'dist-electron', 'main', 'index.js'),
    path.join(appDir, 'dist-electron', 'preload', 'index.js'),
    path.join(appDir, 'dist', 'index.html'),
    path.join(appDir, 'package.json'),
  ].map(describeFile);
  const missing = outputs.filter(item => item.missing).map(item => item.path);
  if (missing.length) throw new Error(`Electron build outputs are missing: ${missing.join(', ')}`);
  return outputs;
}

function electronLaunchOptions({ appDir, workspace, python = resolvePython(), devServerUrl, baseEnv = process.env, isolateHome = false, electronPath }) {
  const env = sanitizeEnv(baseEnv);
  Object.assign(env, {
    VISION_AI_STUDIO_USER_DATA_DIR: workspace.userData,
    VISION_AI_PYTHON: python,
    VISION_AI_STUDIO_DEV_SOURCE_BACKEND: '1',
    VITE_DEV_SERVER_URL: devServerUrl,
    PYTHONDONTWRITEBYTECODE: '1',
  });
  if (isolateHome) Object.assign(env, { HOME: workspace.home, USERPROFILE: workspace.home });
  return { executablePath: electronPath, args: [appDir], cwd: appDir, env };
}

function lockedVersion(repoRoot, name) {
  try {
    const lock = JSON.parse(fs.readFileSync(path.join(repoRoot, 'package-lock.json'), 'utf8'));
    return lock.packages?.[`node_modules/${name}`]?.version ?? null;
  } catch {
    return null;
  }
}

function installedVersion(repoRoot, name) {
  try {
    return JSON.parse(fs.readFileSync(path.join(repoRoot, 'node_modules', name, 'package.json'), 'utf8')).version ?? null;
  } catch {
    return null;
  }
}

// Collects facts without side effects; evaluatePreflight turns them into problems.
function collectPreflightFacts({ repoRoot = REPO_ROOT, env = process.env, python = resolvePython(env), checkPython = true } = {}) {
  const config = (() => { try { return fs.readFileSync(path.join(repoRoot, 'playwright.config.ts'), 'utf8'); } catch { return null; } })();
  let bundledChromium = null;
  try {
    const { chromium } = require(path.join(repoRoot, 'node_modules', 'playwright-core'));
    const executable = chromium.executablePath();
    bundledChromium = { path: executable, present: fs.existsSync(executable) };
  } catch (error) {
    bundledChromium = { path: null, present: false, error: error.message };
  }
  let pythonImports = null;
  if (checkPython) {
    const result = spawnSync(python, ['-c', `import ${BACKEND_IMPORTS.join(', ')}`], {
      encoding: 'utf8', timeout: 90000, windowsHide: true, env: sanitizeEnv(env),
    });
    pythonImports = { python, ok: result.status === 0, error: result.error?.message || result.stderr?.trim().split('\n').pop() || null };
  }
  return {
    playwright: { locked: lockedVersion(repoRoot, '@playwright/test'), installed: installedVersion(repoRoot, '@playwright/test') },
    electron: { locked: lockedVersion(repoRoot, 'electron'), installed: installedVersion(repoRoot, 'electron') },
    config: config === null ? null : {
      browserProject: /name:\s*'browser'/.test(config), electronProject: /name:\s*'electron'/.test(config),
    },
    browserChannel: env.MV_E2E_BROWSER_CHANNEL || null,
    bundledChromium,
    pythonImports,
  };
}

function evaluatePreflight(facts, { needBrowser = true, needElectron = true, needPython = true } = {}) {
  const problems = [];
  if (!facts.playwright.installed) problems.push('@playwright/test is not installed; run "npm ci"');
  else if (facts.playwright.installed !== facts.playwright.locked) {
    problems.push(`@playwright/test ${facts.playwright.installed} differs from the lockfile ${facts.playwright.locked}; run "npm ci"`);
  }
  if (!facts.config) problems.push('playwright.config.ts is missing');
  else if (!facts.config.browserProject || !facts.config.electronProject) problems.push('playwright.config.ts must define browser and electron projects');
  if (needBrowser && !facts.browserChannel && !facts.bundledChromium?.present) {
    problems.push('No browser for the browser project: run "npx playwright install chromium" or set MV_E2E_BROWSER_CHANNEL');
  }
  if (needElectron && (!facts.electron.installed || facts.electron.installed !== facts.electron.locked)) {
    problems.push(`Electron ${facts.electron.installed} is missing or differs from the lockfile ${facts.electron.locked}; run "npm ci"`);
  }
  if (needPython && facts.pythonImports && !facts.pythonImports.ok) {
    problems.push(`Python at ${facts.pythonImports.python} cannot import the backend stack (${facts.pythonImports.error}); set MV_E2E_PYTHON`);
  }
  return problems;
}

function writeManifest(file, data, { redact = [] } = {}) {
  const manifest = {
    schema: 'modu-vision.e2e-evidence/v1',
    written_at: new Date().toISOString(),
    ...data,
    files: (data.files || []).map(describeFile),
  };
  let text = `${JSON.stringify(manifest, null, 2)}\n`;
  for (const secret of redact.filter(Boolean)) text = text.split(secret).join('[redacted]');
  fs.mkdirSync(path.dirname(file), { recursive: true });
  const temporary = `${file}.${process.pid}.tmp`;
  fs.writeFileSync(temporary, text);
  fs.renameSync(temporary, file);
  return JSON.parse(text);
}

function sourceIdentity(repoRoot = REPO_ROOT) {
  try {
    // Optional locks are disabled so concurrent git users are never blocked.
    const run = args => execFileSync('git', ['--no-optional-locks', ...args], {
      cwd: repoRoot, encoding: 'utf8', stdio: ['ignore', 'pipe', 'ignore'],
    }).trim();
    return { commit: run(['rev-parse', 'HEAD']), dirty: run(['status', '--porcelain']).length > 0 };
  } catch (error) {
    return { commit: null, dirty: null, error: error.message };
  }
}

function closedLoopbackUrl() {
  return new Promise((resolve, reject) => {
    const server = net.createServer();
    server.once('error', reject);
    server.listen(0, '127.0.0.1', () => {
      const { port } = server.address();
      server.close(() => resolve(`http://127.0.0.1:${port}/`));
    });
  });
}

function portOpen(port) {
  return new Promise(resolve => {
    const socket = net.connect(port, '127.0.0.1');
    socket.once('connect', () => { socket.destroy(); resolve(true); });
    socket.once('error', () => resolve(false));
  });
}

async function waitForPortClosed(port, timeoutMs = 15000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (!(await portOpen(port))) return true;
    await delay(200);
  }
  return false;
}

module.exports = {
  BACKEND_IMPORTS,
  REPO_ROOT,
  assertElectronBuild,
  assertSpawnable,
  backendCommand,
  buildElectronApp,
  buildRenderer,
  closedLoopbackUrl,
  collectPreflightFacts,
  createPortParser,
  createWorkspace,
  describeFile,
  electronLaunchOptions,
  evaluatePreflight,
  descendantsOf,
  findProcessesReferencing,
  scanProcesses,
  httpGet,
  processAlive,
  resolvePython,
  sanitizeEnv,
  sha256File,
  sourceIdentity,
  startOwnedBackend,
  startRendererServer,
  terminateProcesses,
  waitForPortClosed,
  writeManifest,
};
