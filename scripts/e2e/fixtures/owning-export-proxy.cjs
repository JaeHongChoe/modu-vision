'use strict';
const assert=require('node:assert/strict'),crypto=require('node:crypto'),fs=require('node:fs'),http=require('node:http'),net=require('node:net'),path=require('node:path');
const PROXY_PREFIXES = ['/api/', '/ws/'];

const CONTENT_TYPES = {
  '.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8', '.mjs': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8', '.json': 'application/json', '.map': 'application/json', '.svg': 'image/svg+xml',
  '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.ico': 'image/x-icon', '.woff': 'font/woff',
  '.woff2': 'font/woff2', '.txt': 'text/plain; charset=utf-8',
};

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
  const control = createExportGateControl();
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
    // Synchronous model/fixture subprocesses share this Node loop with the
    // API client. An idle pooled socket can expire before its close event is
    // observed after execFileSync returns. Use one connection per request on
    // both HTTP legs; never retry a mutation. WebSocket upgrades are separate.
    res.setHeader('connection', 'close');
    const url = req.url || '/';
    if (isProxied(url)) {
      if (!sameOrigin(req.headers)) {
        counters.foreignOrigin += 1;
        reject(url, 403);
        res.writeHead(403, { 'content-type': 'text/plain; charset=utf-8' }).end('Foreign origin');
        return;
      }
      counters.proxied += 1;
      control.watchRequest(req, res);
      const headers = { ...forwardedHeaders(req.headers, backend), connection: 'close' };
      delete headers['keep-alive'];
      const upstream = http.request({
        host: '127.0.0.1', port: backend.port, method: req.method, path: url, headers, agent: false,
      }, upstreamResponse => {
        const responseHeaders = { ...upstreamResponse.headers, connection: 'close' };
        delete responseHeaders['keep-alive'];
        if (control.tryHold(req, res, upstreamResponse, responseHeaders)) return;
        res.writeHead(upstreamResponse.statusCode || 502, responseHeaders);
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
    control,
    origin: ownOrigin,
    url: `${ownOrigin}/index.html?port=${port}`,
    stats: () => ({ ...counters, rejectedPaths: [...rejectedPaths] }),
    close: () => new Promise(resolve => {
      for (const socket of sockets) socket.destroy();
      server.close(() => resolve());
    }),
  };
}

/** Real HTTP response gate; constructor contains no server or process launch. */
function createExportGateControl(){
  let armed=null;const requests=new WeakMap(),records=[],errors=[];
  const sha=raw=>crypto.createHash('sha256').update(raw).digest('hex');
  const snapshot=record=>({key:record.key,project_id:record.project,format:record.format,
    request:record.request,upstream:record.upstream,client_close:record.clientClose,
    response_headers_written:record.headersWritten,released:record.released,
    discarded_after_client_close:record.discarded,error:record.error});
  const fail=(record,error)=>{record.error=String(error);errors.push({key:record.key,error:record.error});record.reject(error);};
  return{
    arm({key,project,context,format}){
      assert.equal(armed,null,'No overlapping response gate');assert.match(key,/^[a-z0-9-]{1,64}$/);
      assert.equal(typeof project,'string');assert.ok(project);assert.deepEqual(JSON.parse(context).project_id,project);
      assert.ok(['labelme','coco','yolo'].includes(format));assert.ok(!records.some(r=>r.key===key));
      let resolve,reject;const captured=new Promise((yes,no)=>{resolve=yes;reject=no;});captured.catch(()=>{});
      const record={key,project,context,format,request:null,upstream:null,clientClose:null,headersWritten:false,released:false,discarded:false,error:null,resolve,reject,res:null,releaseBody:null};
      records.push(record);armed=record;
      return{captured,snapshot:()=>snapshot(record),release(){
        assert.ok(record.upstream,'Original upstream body must complete before release');assert.equal(record.released,false,'Never replay response release');record.released=true;
        if(record.res.destroyed||record.clientClose){record.discarded=true;record.releaseBody=null;return snapshot(record);}
        record.headersWritten=true;const {status,headers,body}=record.releaseBody;record.res.writeHead(status,headers);record.res.end(body);record.releaseBody=null;return snapshot(record);
      }};
    },
    watchRequest(req,res){
      if(!armed||req.method!=='POST'||req.url!=='/api/dataset/formats/export')return;
      const row=armed;
      if(req.headers['x-vision-project']!==row.project||req.headers['x-vision-context']!==row.context)return;
      armed=null;requests.set(req,row);row.res=res;const parts=[];let size=0;
      const socket=req.socket,before=socket.bytesWritten;
      row.request={method:req.method,path:req.url,headers:{project:req.headers['x-vision-project'],context:req.headers['x-vision-context'],origin:req.headers.origin,site:req.headers['sec-fetch-site']},complete:false,body_base64:null,body_sha256:null};
      req.on('data',body=>{size+=body.length;if(size>16384){fail(row,new Error('Original export request exceeds finite JSON bound'));return;}parts.push(body);});
      req.once('end',()=>{try{const raw=Buffer.concat(parts);assert.equal(raw.toString('utf8'),JSON.stringify({format:row.format}));row.request.complete=req.complete;row.request.body_base64=raw.toString('base64');row.request.body_sha256=sha(raw);}catch(error){fail(row,error);}});
      res.once('close',()=>{row.clientClose={headers_sent:res.headersSent,writable_finished:res.writableFinished,bytes_before:before,bytes_at_close:socket.bytesWritten,closed_before_release:!row.released};});
    },
    tryHold(req,res,response,headers){
      const row=requests.get(req);if(!row||row.error)return false;
      const chunks=[];let size=0;
      response.on('data',chunk=>{size+=chunk.length;if(size>1048576){fail(row,new Error('Original export response exceeds finite body bound'));return;}chunks.push(chunk);});
      response.once('error',error=>fail(row,error));
      response.once('end',()=>{try{
        assert.equal(response.statusCode,200,'Only a genuine successful upstream export is retained');assert.equal(response.complete,true);assert.equal(row.request.complete,true);assert.ok(row.request.body_sha256);assert.equal(row.error,null);assert.equal(res.headersSent,false);
        const body=Buffer.concat(chunks);const value=JSON.parse(body.toString('utf8'));assert.equal(value.format,row.format);assert.equal(typeof value.download_url,'string');
        const encoded=response.headers['x-vision-context'];if(encoded!==undefined)assert.deepEqual(JSON.parse(encoded),JSON.parse(row.context));
        row.upstream={status:response.statusCode,complete:response.complete,headers:{content_type:response.headers['content-type'],content_length:response.headers['content-length'],context:encoded},body_base64:body.toString('base64'),body_sha256:sha(body),bytes:body.length};
        row.releaseBody={status:response.statusCode,headers:{...headers},body};row.resolve(snapshot(row));
      }catch(error){fail(row,error);}});
      return true;
    },
    snapshots:()=>records.map(snapshot),
    assertSettled(){assert.equal(armed,null,'Armed response request never arrived');assert.deepEqual(errors,[]);for(const row of records){assert.equal(row.error,null);assert.equal(row.released,true);assert.equal(row.releaseBody,null);}},
  };
}

module.exports={startRendererServer,createExportGateControl};
