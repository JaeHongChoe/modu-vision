import fs from 'node:fs';
import path from 'node:path';
import https from 'node:https';
import http from 'node:http';
import {execFileSync} from 'node:child_process';
import type {Workspace} from './test';
const harness = require('./harness.cjs');

/** Owned loopback HTTPS proxy and authenticated API; no process capability is
 * injected into renderer requests. The proxy's forwarding header is trusted
 * only by the isolated backend on loopback. */
export async function browserTeamServer(workspace: Workspace, rendererDir: string) {
  const cert = path.join(workspace.root, 'tls-cert.pem'), key = path.join(workspace.root, 'tls-key.pem');
  execFileSync(harness.resolvePython(), ['-c', `
from cryptography import x509
from cryptography.x509.oid import NameOID
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from pathlib import Path
from datetime import datetime, timedelta, timezone
import ipaddress,sys
key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
name=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'loopback Studio fixture')])
cert=x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key()).serial_number(x509.random_serial_number()).not_valid_before(datetime.now(timezone.utc)-timedelta(minutes=1)).not_valid_after(datetime.now(timezone.utc)+timedelta(days=1)).add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address('127.0.0.1'))]),critical=False).sign(key,hashes.SHA256())
Path(sys.argv[1]).write_bytes(cert.public_bytes(serialization.Encoding.PEM))
Path(sys.argv[2]).write_bytes(key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
Path(sys.argv[2]).chmod(0o600)
`, cert, key], {timeout: 15_000});
  let backend: any = null;
  const server = https.createServer({cert: fs.readFileSync(cert), key: fs.readFileSync(key)}, (req, res) => {
    if (req.url?.startsWith('/api/') || req.url === '/health') {
      if (!backend) {res.writeHead(503); res.end(); return;}
      const upstream = http.request(backend.baseUrl + req.url, {method: req.method,
        headers: {...req.headers, 'x-forwarded-proto': 'https', 'x-forwarded-for': '127.0.0.1'}}, response => {
        res.writeHead(response.statusCode || 502, response.headers); response.pipe(res);
      });
      upstream.on('error', () => {res.writeHead(502); res.end();}); req.pipe(upstream); return;
    }
    const relative = decodeURIComponent(new URL(req.url || '/', 'https://loopback.invalid').pathname);
    const file = path.resolve(rendererDir, '.' + (relative === '/' ? '/index.html' : relative));
    if (!file.startsWith(path.resolve(rendererDir) + path.sep) || !fs.existsSync(file) || !fs.statSync(file).isFile()) {
      res.writeHead(404); res.end(); return;
    }
    res.setHeader('Content-Type', file.endsWith('.js') ? 'text/javascript' : file.endsWith('.css') ? 'text/css' : 'text/html');
    fs.createReadStream(file).pipe(res);
  });
  await new Promise<void>(resolve => server.listen(0, '127.0.0.1', resolve));
  const port = (server.address() as {port: number}).port, origin = `https://127.0.0.1:${port}`;
  try {
    const command = harness.backendCommand({workspace});
    backend = await harness.startOwnedBackend({...command,
      args: [...command.args, '--shared-auth-dir', path.join(workspace.userData, 'team-accounts')],
      env: {...command.env, MODU_BROWSER_ORIGINS: JSON.stringify([origin])}});
    const call = async (route: string, body?: unknown, token?: string, method?: string) => {
      const response = await fetch(backend.baseUrl + route, {
        method: method || (body === undefined ? 'GET' : 'POST'), headers: {'Content-Type': 'application/json',
          ...(token ? {Authorization: `Bearer ${token}`} : {'X-Vision-Token': backend.token})},
        ...(body === undefined ? {} : {body: JSON.stringify(body)})});
      if (!response.ok) throw new Error(`Fixture API ${route}: ${response.status} ${await response.text()}`);
      return response.json();
    };
    return {origin, port, backend, call, close: async () => {
      await new Promise<void>(resolve => server.close(() => resolve()));
      const stop = await backend.stop(), closed = await harness.waitForPortClosed(backend.port, 10000);
      if (!closed || harness.processAlive(backend.pid) || !stop.exited || stop.groupAlive || stop.escaped.length)
        throw new Error('Owned browser team fixture did not stop cleanly');
      return {stop, backend_port_closed: closed, proxy_port_closed: await harness.waitForPortClosed(port, 10000)};
    }};
  } catch (error) {
    await new Promise<void>(resolve => server.close(() => resolve()));
    if (backend) await backend.stop();
    throw error;
  }
}
