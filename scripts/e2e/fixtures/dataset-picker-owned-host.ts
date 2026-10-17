import fs from 'node:fs';
import path from 'node:path';
import http from 'node:http';
import net from 'node:net';
import type {Page} from '@playwright/test';
import {expect, type Evidence, type Workspace} from './test';
import {installDesktopHostShim} from './desktop-host-shim';
import type {SharedConnection} from '../../../src/types/electron';
const harness = require('./harness.cjs');

// A desktop-host stand-in with genuine local and account-authenticated servers.
// Only host dialog/session answers are controlled. All application API and
// WebSocket traffic reaches the real backend; credentials stay in Node.
export async function datasetPickerOwnedHost(workspace: Workspace, rendererDir: string, evidence: Evidence) {
  const staticRoot = fs.realpathSync(rendererDir);
  const localWorkspace = harness.createWorkspace(path.join(workspace.root, 'picker-local')) as Workspace;
  const sharedWorkspace = harness.createWorkspace(path.join(workspace.root, 'picker-shared')) as Workspace;
  const owned: Array<{name: string; pid: number; port: number; started_at: string; logs: unknown; closure: unknown}> = [];
  const publishOwned = () => evidence.note('dataset_picker_owned_backends', owned.map(row => ({...row})));
  const recordStart = (name: string, backend: any) => {
    evidence.redact(backend.token);
    owned.push({name, pid: backend.pid, port: backend.port, started_at: backend.startedAt, logs: backend.logs,
      closure: {status: 'unknown', reason: 'normal owned close has not completed'}});
    publishOwned();
  };
  const local = await harness.startOwnedBackend(harness.backendCommand({workspace: localWorkspace}));
  recordStart('local', local);
  let shared: any;
  const sockets = new Set<net.Socket>();
  let origin = '', session: {token: string; connection: SharedConnection} | null = null;
  const prefix = '/picker-shared';
  const safeOrigin = (headers: http.IncomingHttpHeaders) =>
    (headers.origin === undefined || headers.origin === origin)
    && (headers['sec-fetch-site'] === undefined || ['same-origin', 'none'].includes(String(headers['sec-fetch-site'])));
  const target = (url: string) => {
    const team = url.startsWith(prefix + '/');
    const route = team ? url.slice(prefix.length) : url;
    if (!(route.startsWith('/api/') || route.startsWith('/ws/') || route === '/health' || route.startsWith('/health?'))) return null;
    return {backend: team ? shared : local, route, team};
  };
  const headers = (incoming: http.IncomingHttpHeaders, own: ReturnType<typeof target>) => {
    const value: http.OutgoingHttpHeaders = {...incoming, host: `127.0.0.1:${own!.backend.port}`, connection: 'close'};
    delete value.origin; delete value.referer; delete value.authorization; delete value['x-vision-token'];
    if (own!.team) {if (session) value.authorization = `Bearer ${session.token}`;}
    else value['x-vision-token'] = local.token;
    return value;
  };
  const server = http.createServer((request, response) => {
    const url = request.url || '/';
    const own = target(url);
    if (own) {
      if (!safeOrigin(request.headers)) {response.writeHead(403).end(); return;}
      const upstream = http.request({host: '127.0.0.1', port: own.backend.port, path: own.route,
        method: request.method, headers: headers(request.headers, own), agent: false}, result => {
        response.writeHead(result.statusCode || 502, {...result.headers, connection: 'close'}); result.pipe(response);
        result.on('error', () => response.destroy());
      });
      upstream.on('error', () => {if (!response.headersSent) response.writeHead(502); response.end();});
      response.on('close', () => {if (!response.writableFinished) upstream.destroy();});
      request.pipe(upstream); return;
    }
    if (!['GET', 'HEAD'].includes(request.method || '')) {response.writeHead(405).end(); return;}
    if (url === '/favicon.ico') {response.writeHead(204).end(); return;}
    let file: string;
    try {
      const pathname = decodeURIComponent(new URL(url, 'http://owned.invalid').pathname);
      file = fs.realpathSync(path.resolve(staticRoot, '.' + (pathname === '/' ? '/index.html' : pathname)));
      if (!file.startsWith(staticRoot + path.sep) || !fs.statSync(file).isFile()) throw Error('Missing asset');
    } catch {response.writeHead(404).end(); return;}
    const mime: Record<string, string> = {'.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.svg': 'image/svg+xml', '.png': 'image/png'};
    response.writeHead(200, {'content-type': mime[path.extname(file)] || 'application/octet-stream', 'cache-control': 'no-store'});
    if (request.method === 'HEAD') response.end(); else fs.createReadStream(file).on('error', () => response.destroy()).pipe(response);
  });
  server.on('connection', socket => {sockets.add(socket); socket.on('close', () => sockets.delete(socket));});
  server.on('upgrade', (request, socket, head) => {
    const own = target(request.url || '/');
    if (!own || !own.route.startsWith('/ws/') || !safeOrigin(request.headers)) {socket.end('HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\n\r\n'); return;}
    const upstream = net.connect(own.backend.port, '127.0.0.1', () => {
      let wire = `${request.method} ${own.route} HTTP/1.1\r\n`;
      const outgoing = {...headers(request.headers, own), connection: request.headers.connection || 'Upgrade'};
      for (const [name, value] of Object.entries(outgoing)) for (const item of Array.isArray(value) ? value : [value]) if (item !== undefined) wire += `${name}: ${item}\r\n`;
      upstream.write(wire + '\r\n'); if (head.length) upstream.write(head); upstream.pipe(socket); socket.pipe(upstream);
    });
    sockets.add(upstream); upstream.on('close', () => sockets.delete(upstream));
    upstream.on('error', () => socket.destroy()); socket.on('error', () => upstream.destroy()); socket.on('close', () => upstream.destroy());
  });
  const call = async (backend: any, route: string, body?: unknown, method = body === undefined ? 'GET' : 'POST', token?: string) => {
    const response = await fetch(backend.baseUrl + route, {method, headers: {'Content-Type': 'application/json',
      ...(token ? {Authorization: `Bearer ${token}`} : {'X-Vision-Token': backend.token})},
      ...(body === undefined ? {} : {body: JSON.stringify(body)}), signal: AbortSignal.timeout(15_000)});
    if (!response.ok) throw Error(`Owned picker API ${method} ${route}: ${response.status} ${await response.text()}`);
    return response.json();
  };
  const stops: unknown[] = [];
  const stop = async (backend: any) => {
    const owner = owned.find(row => row.pid === backend.pid)!;
    evidence.addFile(backend.logs.stdout); evidence.addFile(backend.logs.stderr);
    let result: any;
    try {result = await backend.stop();}
    catch (error) {owner.closure = {status: 'unknown', reason: 'owned stop threw before returning', error: String(error)}; publishOwned(); throw error;}
    owner.closure = {status: 'partial', stop: result, port_closed: null, pid_alive: null}; publishOwned();
    const portClosed = await harness.waitForPortClosed(backend.port, 10_000), pidAlive = harness.processAlive(backend.pid);
    const row = {pid: backend.pid, port: backend.port, port_closed: portClosed, pid_alive: pidAlive, ...result};
    stops.push(row); owner.closure = {status: 'observed', ...row};
    publishOwned(); evidence.note('dataset_picker_backend_stop', stops.slice());
    // Persist observations before enforcing the normal, unforced close gate.
    expect(result.exited).toBe(true); expect(result.groupAlive).toBe(false); expect(result.escaped).toEqual([]);
    expect(result.forced).toBe(false); expect(result.unownedReferences).toEqual([]);
    expect(result.scanError).toBeFalsy(); expect(portClosed).toBe(true); expect(pidAlive).toBe(false);
    return row;
  };
  try {
    const command = harness.backendCommand({workspace: sharedWorkspace});
    shared = await harness.startOwnedBackend({...command, args: [...command.args, '--shared-auth-dir', path.join(sharedWorkspace.userData, 'accounts')]});
    recordStart('shared', shared);
    await new Promise<void>((resolve, reject) => {server.once('error', reject); server.listen(0, '127.0.0.1', resolve);});
    const port = (server.address() as net.AddressInfo).port; origin = `http://127.0.0.1:${port}`;
    const password = 'dataset-picker-owned-password-123'; evidence.redact(password);
    await call(shared, '/api/accounts/bootstrap', {username: 'pickeradmin', password});
    const admin = await call(shared, '/api/accounts/login', {username: 'pickeradmin', password}); evidence.redact(admin.token);
    return {
      origin, port, url: `${origin}/index.html?port=${port}`, password,
      callLocal: (route: string, body?: unknown, method?: string) => call(local, route, body, method),
      callShared: (route: string, body?: unknown, method?: string) => call(shared, route, body, method, admin.token),
      install: async (page: Page) => {
        await installDesktopHostShim(page, port);
        await page.exposeBinding('__datasetPickerLogin', async (_source, input: {server_url: string; username: string; password: string}) => {
          expect(input.server_url).toBe(origin + prefix);
          const value = await call(shared, '/api/accounts/login', {username: input.username, password: input.password}); evidence.redact(value.token);
          session = {token: value.token, connection: {server_url: origin + prefix, expires_at: value.expires_at, user: value.user}};
          return session.connection;
        });
        await page.exposeBinding('__datasetPickerSelect', async (_source, id: string) => {
          if (!session) throw Error('Owned picker shared session is absent');
          const result = await call(shared, '/api/accounts/me', undefined, 'GET', session.token);
          expect(result.selected_project_id).toBe(id); session.connection = {...session.connection, project_id: id}; return session.connection;
        });
        await page.exposeBinding('__datasetPickerDisconnect', async () => {
          if (session) await call(shared, '/api/accounts/logout', undefined, 'POST', session.token); session = null;
        });
      },
      attachSessionShim: (page: Page) => page.evaluate(() => {
        const host = window.api!; const bindings = window as any;
        host.loginSharedServer = input => bindings.__datasetPickerLogin(input);
        host.selectSharedProject = id => bindings.__datasetPickerSelect(id);
        host.getSharedConnection = async () => null;
        host.disconnectSharedServer = () => bindings.__datasetPickerDisconnect();
      }),
      close: async () => {
        for (const socket of sockets) socket.destroy();
        await new Promise<void>(resolve => server.close(() => resolve()));
        const failures: unknown[] = [];
        for (const backend of [shared, local]) try {await stop(backend);} catch (error) {failures.push(error);}
        evidence.note('dataset_picker_cleanup_errors', failures.map(String));
        if (failures.length) throw new AggregateError(failures, 'Owned picker backend close failed');
      },
    };
  } catch (error) {
    for (const socket of sockets) socket.destroy(); server.close();
    const failures: unknown[] = [];
    for (const backend of [shared, local].filter(Boolean)) try {await stop(backend);} catch (failure) {failures.push(failure);}
    evidence.note('dataset_picker_setup_cleanup_errors', failures.map(String));
    if (error instanceof Error && failures.length) try {
      Object.defineProperty(error, 'dataset_picker_cleanup_failures', {value: failures.map(String), configurable: true});
    } catch {/* Keep the original setup failure. */}
    throw error;
  }
}
