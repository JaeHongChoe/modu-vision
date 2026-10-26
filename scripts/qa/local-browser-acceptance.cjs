#!/usr/bin/env node
'use strict';
// An isolated real-product acceptance runner. No fixture server, bridge or storage injection.
const fs = require('node:fs'), path = require('node:path'), crypto = require('node:crypto');
const net = require('node:net'), https = require('node:https'), { spawn, execFileSync } = require('node:child_process');
const assert = require('node:assert/strict');
const sha = value => crypto.createHash('sha256').update(value).digest('hex');
const fileSha = file => sha(fs.readFileSync(file));
const readJson = file => JSON.parse(fs.readFileSync(file, 'utf8'));
const writeJson = (file, value) => fs.writeFileSync(file, JSON.stringify(value, null, 2) + '\n', { mode: 0o600 });
function snapshot(files) { return Object.fromEntries(files.map(file => [file, fileSha(file)])); }
function verifySnapshot(saved) { for (const [file, hash] of Object.entries(saved))
    assert.equal(fileSha(file), hash, 'Saved artifact changed: ' + file); }
function reserveTraining(file) { try {
    fs.writeFileSync(file, JSON.stringify({ reserved_at: new Date().toISOString(), status: 'dispatch-intent' }), { flag: 'wx', mode: 0o600 });
}
catch (error) {
    if (error.code === 'EEXIST')
        throw new Error('Training already reserved; resume saved results instead of dispatching again');
    throw error;
} }
function recordLaunch(state,ready,files,head){
    state.launches ||= [];
    state.launches.push({at:new Date().toISOString(),source_head:head,ready,renderer:snapshot(files)});
}
function observeResponse(promise) { promise.catch(() => { }); return promise; }
function releaseUnsentTraining(file, requests, tasks) {
    assert.equal(readJson(file).status, 'dispatch-intent', 'Only an unsent dispatch intent can be recovered');
    assert.ok(!requests.some(row => row.method === 'POST' && row.path === '/api/training/start'), 'Actual dispatch request prevents recovery');
    assert.ok(!tasks.some(row => row.kind === 'training'), 'Saved training job prevents recovery');
    fs.copyFileSync(file, file + '.unsent.json', fs.constants.COPYFILE_EXCL);
    fs.unlinkSync(file);
}
function parseReady(line, root) {
    assert.ok(line.startsWith('MODU_WEB_READY='), 'complete ready line required');
    const value = JSON.parse(line.slice(15));
    assert.deepEqual(Object.keys(value).sort(), ['url', 'pid', 'data_dir', 'certificate_file', 'certificate_sha256'].sort(), 'ready public fields');
    const url = new URL(value.url);
    assert.equal(url.protocol, 'https:');
    assert.equal(url.hostname, 'localhost');
    assert.ok(Number(url.port) > 0);
    assert.equal(url.pathname, '/');
    assert.equal(url.search, '');
    assert.equal(url.hash, '');
    assert.equal(url.username, '');
    assert.equal(url.password, '');
    assert.ok(Number.isInteger(value.pid) && value.pid > 0);
    assert.equal(value.data_dir, root, 'owned data root');
    assert.equal(value.certificate_file, path.join(root, 'tls', 'localhost-cert.pem'));
    assert.match(value.certificate_sha256, /^[0-9a-f]{64}$/);
    return value;
}
module.exports = { snapshot, verifySnapshot, reserveTraining, parseReady, fixtures, releaseUnsentTraining, observeResponse, recordLaunch };
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
async function portClosed(port) { return new Promise(resolve => { const socket = net.connect({ host: '127.0.0.1', port }); socket.once('connect', () => { socket.destroy(); resolve(false); }); socket.once('error', () => resolve(true)); }); }
async function until(predicate, timeout = 30000) { const stop = Date.now() + timeout; while (Date.now() < stop) {
    if (await predicate())
        return;
    await pause(200);
} throw new Error('Bounded condition timed out'); }
async function launch(root, port, env, run, number) {
    const child = spawn(process.execPath, ['scripts/local-web.cjs', '--data-dir', root, '--port', String(port), '--no-open'], { cwd: path.resolve(__dirname, '../..'), env, stdio: ['ignore', 'pipe', 'pipe'] });
    const log = fs.createWriteStream(path.join(run, `launcher-${number}.log`), { mode: 0o600 });
    let pending = '', ready, error;
    child.stdout.on('data', bytes => { log.write(bytes); pending += bytes.toString(); let newline; while ((newline = pending.indexOf('\n')) >= 0) {
        const line = pending.slice(0, newline).replace(/\r$/, '');
        pending = pending.slice(newline + 1);
        if (line.startsWith('MODU_WEB_READY=')) {
            try {
                ready = parseReady(line, root);
            }
            catch (e) {
                error = e;
            }
        }
    } });
    child.stderr.on('data', bytes => log.write(bytes));
    child.once('error', e => { error = e; });
    child.once('exit', () => log.end());
    const stop = async () => { if (child.exitCode === null && child.signalCode === null)
        child.kill('SIGINT'); await until(() => child.exitCode !== null || child.signalCode !== null, 20000); if (ready)
        await until(() => portClosed(Number(new URL(ready.url).port)), 10000); return { wrapper_pid: child.pid, server_pid: ready?.pid, exit_code: child.exitCode, signal: child.signalCode, port_closed: ready ? await portClosed(Number(new URL(ready.url).port)) : null }; };
    try {
        await until(() => { if (error)
            throw error; if (child.exitCode !== null)
            throw new Error('Launcher exited ' + child.exitCode); return !!ready; }, 90000);
        const cert = new crypto.X509Certificate(fs.readFileSync(ready.certificate_file));
        assert.equal(sha(cert.raw), ready.certificate_sha256);
        return { child, ready, stop };
    }
    catch (e) {
        await stop();
        throw e;
    }
}
function bootstrap(python, root, credentialFile) {
    // pexpect owns a PTY; the random secret is sent only to getpass's hidden prompts.
    const script = `import json, pathlib, secrets, sys, pexpect\nroot, output=sys.argv[1:]\nsecret=secrets.token_urlsafe(32)\nchild=pexpect.spawn(sys.executable,['-m','backend.accounts_cli','--shared-auth-dir',root+'/auth','--username','browserqaowner'],encoding='utf-8',timeout=20,echo=False)\nchild.expect('New administrator password');child.sendline(secret)\nchild.expect('Repeat password');child.sendline(secret)\nchild.expect(pexpect.EOF);child.close()\nassert child.exitstatus==0, 'Owned account setup failed'\np=pathlib.Path(output);p.write_text(json.dumps({'username':'browserqaowner','password':secret}));p.chmod(0o600)\n`;
    execFileSync(python, ['-c', script, root, credentialFile], { stdio: ['ignore', 'ignore', 'pipe'] });
}
function fixtures(run) {
    // Only pure byte helpers are reused; appFlow and old fixture servers are never imported.
    const ts = require('typescript'), Module = require('node:module');
    const load = (file, source) => { const m = new Module(file, module); m.filename = file; m.paths = module.paths; m._compile(ts.transpile(source, { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, esModuleInterop: true }), file); return m.exports; };
    const zipFile = path.resolve(__dirname, '../e2e/fixtures/storedZip.ts');
    const { storedZip } = load(zipFile, fs.readFileSync(zipFile, 'utf8'));
    const appFile = path.resolve(__dirname, '../e2e/qa/appFlow.ts');
    const source = fs.readFileSync(appFile, 'utf8');
    const start = source.indexOf('function chunk('), end = source.indexOf('/** A small segmentation');
    const { png } = load(appFile, "import {crc32,deflateSync} from 'node:zlib';\n" + source.slice(start, end));
    const entries = [], pixels = [];
    for (const [classIndex, label] of ['OK', 'NG'].entries())
        for (let index = 0; index < 8; index++) {
            const relative = `${label}/${label.toLowerCase()}_${index}.png`;
            const bytes = png(64, 3, (x, y) => { const base = 120 + ((x * 3 + y * 5 + index * 11 + classIndex * 7) % 80); return label === 'NG' && x >= 27 + index % 3 && x <= 31 + index % 3 && y > 8 && y < 56 ? [20 + index, 35, 45] : [base, 125 + (x + index * 7) % 60, 130 + (y + index * 9) % 60]; });
            entries.push([relative, bytes]);
            pixels.push({ relative_path: relative, sha256: sha(bytes) });
        }
    assert.equal(new Set(pixels.map(p => p.sha256)).size, 16);
    const archive = path.join(run, 'classification.zip');
    fs.writeFileSync(archive, storedZip(entries));
    return { archive, pixels };
}
async function main() {
    const { chromium, expect: baseExpect } = require('@playwright/test');
    const expect = baseExpect.configure({ timeout: 20000 });
    const repo = path.resolve(__dirname, '../..'), base = path.join(repo, '.superpowers/sdd/2026-10-26-local-browser-launch/private');
    const resume = process.argv[2] === '--resume';
    const run = resume ? path.resolve(process.argv[3]) : path.join(base, 'run-' + new Date().toISOString().replace(/[:.]/g, '-'));
    fs.mkdirSync(run, { recursive: true, mode: 0o700 });
    fs.chmodSync(run, 0o700);
    const root = path.join(run, 'web-data'), credentialFile = path.join(run, 'credentials.json'), stateFile = path.join(run, 'state.json'), dispatch = path.join(run, 'training-dispatch.json');
    const python = process.env.VISION_AI_PYTHON || path.join(repo, '.superpowers/sdd/2026-10-26-web-roundtrip/venv/bin/python');
    const weights = process.env.MV_E2E_RESNET18_WEIGHTS || '/Users/kai/.cache/torch/hub/checkpoints/resnet18-f37072fd.pth';
    const weightsSha = 'f37072fd47e89c5e827621c5baffa7500819f7896bbacec160b1a16c560e07ec';
    assert.equal(fileSha(weights), weightsSha);
    const torchHome = path.join(run, 'torch'), cached = path.join(torchHome, 'hub/checkpoints/resnet18-f37072fd.pth');
    fs.mkdirSync(path.dirname(cached), { recursive: true });
    if (!fs.existsSync(cached))
        fs.copyFileSync(weights, cached);
    assert.equal(fileSha(cached), weightsSha);
    if (!resume)
        bootstrap(python, root, credentialFile);
    const credential = readJson(credentialFile);
    const env = { ...process.env, VISION_AI_PYTHON: python, TORCH_HOME: torchHome, OMP_NUM_THREADS: '2', MKL_NUM_THREADS: '2' };
    let state = resume ? readJson(stateFile) : { started_at: new Date().toISOString(), run, root, source_head: execFileSync('git', ['rev-parse', 'HEAD'], { cwd: repo, encoding: 'utf8' }).trim(), synthetic_only: true, weights_sha256: weightsSha, failures: [], requests: [], cleanup: [] };
    const save = () => writeJson(stateFile, state);
    save();
    console.log('PRIVATE_RUN=' + run);
    let server, browser, context, page;
    const sockets = [];
    let apiContext = null;
    const observe = target => {
        target.on('request', req => { const u = new URL(req.url()); if (u.pathname.startsWith('/api/')) {
            const h = req.headers();
            if (h['x-vision-context'])
                apiContext = JSON.parse(h['x-vision-context']);
            if (req.method() !== 'GET' || u.pathname.includes('evaluation/results')) {
                state.requests.push({ method: req.method(), path: u.pathname, at: new Date().toISOString() });
                save();
            }
        } });
        target.on('websocket', socket => { const url = new URL(socket.url()); const projectContext = JSON.parse(url.searchParams.get('project_context') || 'null'); const row = { origin: url.origin, path: url.pathname, context: projectContext, frames: [], closed: false }; sockets.push(row); socket.on('framereceived', frame => { let event; try {
            const j = JSON.parse(String(frame.payload));
            event = j.event || j.type;
        }
        catch {
            if (String(frame.payload) === 'pong')
                event = 'pong';
        } if (event === 'hardware_stats' || event === 'pong') {
            row.frames.push({ event, at: new Date().toISOString() });
            save();
        } }); socket.on('close', () => { row.closed = true; row.closed_at = new Date().toISOString(); }); socket.on('socketerror', () => { row.error = true; }); });
    };
    const wire = (target, route, method) => observeResponse(target.waitForResponse(r => new URL(r.url()).pathname === route && r.request().method() === method, { timeout: 30000 }));
    const checked = async (response) => { assert.ok(response.ok(), 'HTTP ' + response.status() + ': ' + (await response.text()).slice(0, 1000)); return response.json(); };
    const api = async (route) => checked(await context.request.get(server.ready.url + route, { headers: apiContext ? { 'X-Vision-Project': apiContext.project_id, 'X-Vision-Context': JSON.stringify(apiContext) } : {} }));
    const stage = (target, index) => target.getByRole('navigation', { name: 'Workflow Stages' }).getByRole('button').nth(index).click();
    const expand = async (target, text) => { const summary = target.locator('summary').filter({ hasText: text }); if (await summary.locator('..').getAttribute('open') === null)
        await summary.click(); };
    const screenshot = async (name) => page.screenshot({ path: path.join(run, name + '.png'), fullPage: true });
    async function login(target, create = false) {
        await target.goto(server.ready.url);
        const all = target.getByRole('button', { name: '모두 보기', exact: true });
        if (await all.isVisible())
            await expand(target.locator('header'), '더 보기');
        await target.getByRole('button', { name: '서버 관리', exact: true }).click();
        await expand(target, '공동 작업 서버');
        await expect(target.getByLabel('공동 작업 서버 주소')).toHaveValue(server.ready.url);
        await target.getByLabel('공유 계정 이름', { exact: true }).fill(credential.username);
        await target.getByLabel('공유 계정 비밀번호', { exact: true }).fill(credential.password);
        const response = wire(target, '/api/accounts/login', 'POST');
        await target.getByRole('button', { name: '연결·로그인', exact: true }).click();
        await checked(await response);
        if (create) {
            await expand(target, '프로젝트·계정 관리');
            await target.getByLabel('공유 프로젝트 이름').fill('Local browser synthetic acceptance');
            await target.getByLabel('공유 프로젝트 작업 종류').selectOption('classification');
            const response = wire(target, '/api/project/create', 'POST');
            await target.getByLabel('공유 프로젝트 이름').locator('..').getByRole('button', { name: '만들기', exact: true }).click();
            state.project = await checked(await response);
            save();
        }
        await expect(target.getByLabel('공동 작업 프로젝트', { exact: true })).toHaveValue(state.project.id);
        await expect(target.getByLabel('공동 작업 프로젝트', { exact: true })).toBeEnabled();
        await expect(target.getByTitle('프로젝트 관리', { exact: true })).toContainText(state.project.name);
        const intro = target.getByRole('dialog', { name: '처음 시작하기', exact: true });
        if (await intro.isVisible())
            await intro.getByRole('button', { name: '닫기', exact: true }).click();
        await target.getByRole('dialog', { name: '컴퓨팅 서버 관리', exact: true }).getByRole('button', { name: '닫기', exact: true }).click();
        if (await all.isVisible())
            await all.click();
        console.log(create ? 'ACTUAL_UI_LOGIN_PROJECT_CREATED' : 'ACTUAL_UI_FRESH_LOGIN');
    }
    async function verifyRestored(target) {
        const restored = await api('/api/project/current');
        assert.equal(restored.id, state.project.id);
        assert.equal(restored.source_dataset_dir, state.source);
        verifySnapshot(state.preserved);
        assert.deepEqual((await api(state.imagesRoute)).items.map(row => [row.file_path, row.label, row.split]).sort(), state.savedImages);
        assert.equal(fileSha(state.splitFile), state.splitSha);
        for (const saved of state.labels) {
            const metadata = await api('/api/dataset/metadata/image?' + new URLSearchParams({ image_path: saved.image_path }));
            assert.equal(metadata.revision, saved.revision);
        }
        await target.getByRole('button', { name: '작업 센터', exact: true }).click();
        const center = target.getByRole('dialog', { name: '작업 센터', exact: true });
        await center.getByLabel('저장 작업 다시 열기', { exact: true }).selectOption('training:local:' + state.job_id);
        const result = wire(target, '/api/evaluation/results', 'GET');
        await center.getByRole('button', { name: '완료 후보 평가로 이동', exact: true }).click();
        const value = await checked(await result);
        assert.equal(value.job_id, state.job_id);
        assert.equal(value.evaluation_id, state.evaluated.evaluation_id);
        await expand(target, '평가 이력 · 제품/Lot별 오류');
        await target.getByLabel('모델별 저장 평가').selectOption(state.evaluated.evaluation_id);
        assert.deepEqual((await api(state.historyRoute)).items.find(row => row.evaluation_id === state.evaluated.evaluation_id), state.originalHistory);
        const jobs = await api('/api/training-workspace/tasks');
        assert.deepEqual(jobs.tasks.filter(row => row.kind === 'training').map(row => row.job_id), [state.job_id]);
        verifySnapshot(state.preserved);
        state.original_job_reopened = true;
        save();
    }
    try {
        server = await launch(root, resume ? state.initial_ready ? Number(new URL(state.initial_ready.url).port) : 0 : 0, env, run, resume ? 'resume' : 1);
        state.initial_ready ||= server.ready;
        const rendererFiles=()=>[path.join(root,'renderer/index.html'),...fs.readdirSync(path.join(root,'renderer/assets')).map(name=>path.join(root,'renderer/assets',name))];
        const currentHead=()=>execFileSync('git',['rev-parse','HEAD'],{cwd:repo,encoding:'utf8'}).trim();
        recordLaunch(state,server.ready,rendererFiles(),currentHead());
        save();
        console.log('PRODUCT_READY=' + server.ready.url);
        // Read-only unauthenticated and hostile boundary probes use the product TLS listener.
        const probe = (route, headers = {}) => new Promise((resolve, reject) => { https.get(server.ready.url + route, { rejectUnauthorized: false, headers }, r => { r.resume(); r.on('end', () => resolve(r.statusCode)); }).on('error', reject); });
        state.boundaries = { unauthenticated_api: await probe('/api/project/current'), host: await probe('/health', { Host: 'evil.invalid' }), origin: await probe('/api/project/current', { Origin: 'https://evil.invalid' }), forwarded: await probe('/health', { 'X-Forwarded-Host': 'evil.invalid', 'X-Forwarded-Proto': 'http' }) };
        assert.ok([401, 403].includes(state.boundaries.unauthenticated_api));
        assert.equal(state.boundaries.host, 403);
        assert.equal(state.boundaries.origin, 403);
        assert.equal(state.boundaries.forwarded, 200);
        save();
        browser = await chromium.launch({ headless: true });
        context = await browser.newContext({ ignoreHTTPSErrors: true, viewport: { width: 1440, height: 1000 } });
        page = await context.newPage();
        page.setDefaultTimeout(20000);
        observe(page);
        // Allow only this owned HTTPS/WSS origin; routes are admitted without response/header rewriting.
        const fence = async (ctx) => { await ctx.route('**/*', route => { const u = new URL(route.request().url()); if (['data:', 'blob:'].includes(u.protocol) || u.origin === server.ready.url)
            return route.continue(); state.blocked_requests ||= []; state.blocked_requests.push(u.origin); save(); return route.abort('blockedbyclient'); }); };
        await fence(context);
        await login(page, !resume);
        await screenshot(resume ? 'resumed-login' : 'actual-default-ui-login-project');
        if (!state.source) {
            Object.assign(state, fixtures(run));
            save();
            await stage(page, 0);
            await page.getByRole('button', { name: '검증된 데이터 버전', exact: true }).click();
            const intake = page.getByRole('dialog', { name: '검증된 데이터 버전', exact: true });
            await intake.getByLabel('가져올 ZIP 파일').setInputFiles(state.archive);
            await intake.getByRole('button', { name: 'ZIP 올리고 검증', exact: true }).click();
            await expect(intake.getByRole('region', { name: '현재 가져오기' })).toContainText('검증 완료 · 채택 전', { timeout: 30000 });
            await intake.getByRole('button', { name: '이 버전 채택', exact: true }).click();
            const accepted = observeResponse(page.waitForResponse(r => /\/api\/dataset\/imports\/[^/]+\/accept$/.test(new URL(r.url()).pathname) && r.request().method() === 'POST'));
            await intake.getByRole('button', { name: '채택 확인', exact: true }).click();
            state.adoption = await checked(await accepted);
            const imported = await api('/api/dataset/imports/' + state.adoption.job_id);
            assert.equal((await api('/api/project/current')).source_dataset_dir, state.project.source_dataset_dir);
            await intake.getByRole('button', { name: '이 ZIP으로 작업 시작', exact: true }).click();
            await expect.poll(async () => (await api('/api/project/current')).source_dataset_dir).toBe(imported.source.root);
            await expect(intake).toBeHidden();
            state.current = await api('/api/project/current');
            state.source = state.current.source_dataset_dir;
            assert.ok(!path.relative(root, state.source).startsWith('..'));
            const thumbs = page.locator('button[aria-label$="라벨링에서 열기"] img');
            await expect(thumbs).toHaveCount(16);
            await expect.poll(() => thumbs.evaluateAll(images => images.every(image => image.complete && image.naturalWidth > 0))).toBe(true);
            state.imagesRoute = '/api/dataset/images?' + new URLSearchParams({ folder_path: state.source, task: 'classification', limit: '100' });
            const images = await api(state.imagesRoute);
            assert.equal(images.total, 16);
            for (const pixel of state.pixels)
                assert.equal(fileSha(path.join(state.source, pixel.relative_path)), pixel.sha256);
            state.labels = [];
            await stage(page, 1);
            for (const label of ['OK', 'NG']) {
                const selected = images.items.find(row => row.label === label);
                await page.locator('[data-labeling-filmstrip]').getByAltText(selected.file_name, { exact: true }).click();
                const picker = page.getByRole('region', { name: '이미지 클래스', exact: true });
                const changed = wire(page, '/api/dataset/metadata/class-override', 'PUT');
                const to = label === 'OK' ? 'NG' : 'OK';
                await picker.getByRole('button', { name: to, exact: true }).click();
                const saved = await checked(await changed);
                assert.equal(saved.class, to);
                const metadata = await api('/api/dataset/metadata/image?' + new URLSearchParams({ image_path: selected.file_path }));
                assert.equal(metadata.revision, saved.metadata.revision);
                state.labels.push({ image_path: selected.file_path, from: label, to, revision: metadata.revision });
                save();
            }
            await screenshot('saved-two-opposite-labels');
            await stage(page, 0);
            await page.getByRole('button', { name: '60 / 20 / 20', exact: true }).click();
            const splitResponse = wire(page, '/api/dataset/split', 'POST');
            await page.getByRole('button', { name: '이 비율로 나누기', exact: true }).click();
            state.split = (await checked(await splitResponse)).split;
            assert.deepEqual(state.split, { train: 10, val: 4, test: 2 });
            state.splitFile = path.join(state.current.dataset_dir, 'splits', sha(Buffer.from(state.source)) + '.json');
            state.splitSha = fileSha(state.splitFile);
            const assignments = readJson(state.splitFile).assignments;
            state.heldout = Object.entries(assignments).filter(([, split]) => split === 'test').map(([relative]) => path.join(state.source, relative)).sort();
            assert.equal(state.heldout.length, 2);
            state.savedImages = (await api(state.imagesRoute)).items.map(row => [row.file_path, row.label, row.split]).sort();
            save();
        }
        if (!state.job_id) {
            await stage(page, 2);
            const family = page.getByRole('region', { name: '모델 학습 허브' }).getByRole('button', { name: /이미지 분류/ });
            if (await family.getAttribute('aria-pressed') !== 'true')
                await family.click();
            await page.getByRole('combobox', { name: '학습 모델 구조', exact: true }).selectOption('resnet18');
            await page.getByLabel('수동 학습 횟수', { exact: true }).fill('1');
            await page.getByLabel('수동 학습 입력 크기', { exact: true }).fill('64');
            await expand(page, '다음 학습 배치·로컬 장치 설정');
            await page.getByLabel('다음 학습 배치 크기', { exact: true }).fill('2');
            await page.getByLabel('다음 학습 로컬 장치', { exact: true }).selectOption('cpu');
            await expand(page, '학습 실행 예산·대기열');
            await page.getByLabel('학습 시간 제한 (분)', { exact: true }).fill('1');
            const readiness = wire(page, '/api/training-workspace/readiness', 'POST');
            await page.getByRole('button', { name: '로컬 준비 검사', exact: true }).click();
            state.readiness = await checked(await readiness);
            assert.equal(state.readiness.ready, true);
            assert.equal(state.readiness.runtime.device, 'cpu');
            assert.equal(state.readiness.weights.sha256, weightsSha);
            save();
            await expect(page.getByRole('button', { name: '선택 설정으로 학습 시작', exact: true })).toBeEnabled();
            if (resume && fs.existsSync(dispatch))
                releaseUnsentTraining(dispatch, state.requests, (await api('/api/training-workspace/tasks')).tasks);
            state.model_source_head=currentHead();state.model_renderer=state.launches.at(-1).renderer;save();
            reserveTraining(dispatch);
            console.log('SINGLE_TRAINING_DISPATCH_RESERVED');
            const started = wire(page, '/api/training/start', 'POST');
            await page.getByRole('button', { name: '선택 설정으로 학습 시작', exact: true }).click();
            const response = await started;
            const training = await checked(response);
            state.job_id = training.job_id;
            state.posted = response.request().postDataJSON();
            writeJson(dispatch, { status: 'dispatched', job_id: state.job_id, at: new Date().toISOString() });
            save();
            console.log('TRAINING_JOB_ID=' + state.job_id);
            assert.equal(state.posted.device, 'cpu');
            assert.equal(state.posted.max_runtime_s, 60);
            await expect.poll(async () => { state.terminal = await api('/api/training/status?job_id=' + encodeURIComponent(state.job_id)); save(); if (['failed', 'cancelled', 'aborted', 'stopped'].includes(state.terminal.status))
                throw new Error('Training terminal failure: ' + state.terminal.status); return state.terminal.status; }, { timeout: 95000, intervals: [500, 1000, 2000] }).toBe('completed');
            console.log('SINGLE_TRAINING_COMPLETED');
            await screenshot('real-cpu-training-completed');
            state.jobDir = path.join(state.current.models_dir, state.job_id);
            state.checkpoint = path.join(state.jobDir, 'best_model.pt');
            state.metadataFile = path.join(state.jobDir, 'model_meta.json');
            state.receiptFile = path.join(state.jobDir, 'job_receipt.json');
            await expect.poll(() => fs.existsSync(state.receiptFile) && readJson(state.receiptFile).status).toBe('completed');
            const metadata=readJson(state.metadataFile);assert.equal(metadata.device,'cpu');assert.equal(metadata.backbone,'resnet18');assert.equal(metadata.task,'classification');assert.deepEqual(metadata.training_config,{...metadata.training_config,epochs:1,image_size:64,batch_size:2});assert.deepEqual([...metadata.classes].sort(),['NG','OK']);
            assert.ok(fs.statSync(state.checkpoint).size > 1000000);
            save();
        }
        if (state.job_id && !state.jobDir) {
            state.jobDir = path.join(state.current.models_dir, state.job_id);
            state.checkpoint = path.join(state.jobDir, 'best_model.pt');
            state.metadataFile = path.join(state.jobDir, 'model_meta.json');
            state.receiptFile = path.join(state.jobDir, 'job_receipt.json');
            save();
        }
        if (!state.evaluated) {
            state.healthBefore = await api('/health');
            const evaluation = wire(page, '/api/evaluation/results', 'GET');
            await stage(page, 3);
            state.evaluated = await checked(await evaluation);
            save();
        }
        assert.equal(state.evaluated.job_id, state.job_id);
        assert.equal(state.evaluated.metrics.evaluated_split, 'test');
        assert.equal(state.evaluated.metrics.selection_overlap, false);
        assert.deepEqual(state.evaluated.test_predictions.map(row => row.file_path).sort(), state.heldout);
        assert.equal(state.evaluated.confusion_matrix.matrix.flat().reduce((a, b) => a + b, 0), 2);
        for (const value of Object.values(state.evaluated.metrics))
            if (typeof value === 'number')
                assert.ok(Number.isFinite(value));
        state.healthAfter = await api('/health');
        assert.equal(state.healthBefore.device, state.healthAfter.device);
        state.evaluation_device_mode = 'automatic';
        state.evaluation_device_readback = state.healthAfter.device;
        state.evaluationFile = path.join(state.jobDir, 'eval_results.json');
        state.historyFile = path.join(state.current.project_dir, 'reports/evaluations', state.evaluated.evaluation_id + '.json');
        state.historyRoute = '/api/evaluation/history?' + new URLSearchParams({ source_dataset_path: state.source, task: 'classification' });
        state.originalHistory ||= (await api(state.historyRoute)).items.find(row => row.evaluation_id === state.evaluated.evaluation_id);
        state.preserved ||= snapshot([state.checkpoint, state.metadataFile, state.receiptFile, state.evaluationFile, state.historyFile]);
        save();
        await screenshot('real-heldout-evaluation');
        const validSocket = row => row.origin === server.ready.url.replace('https:', 'wss:') && row.path === '/ws/telemetry' && row.context?.project_id === state.project.id && row.context?.workspace_id === apiContext?.workspace_id && row.context?.actor_id === apiContext?.actor_id && row.frames.length && !row.closed;
        await expect.poll(() => sockets.some(validSocket), { timeout: 20000 }).toBe(true);
        state.wss_initial = sockets.filter(validSocket).map(row => ({ ...row }));
        save();
        const initial = sockets.find(validSocket);
        const previous = server.ready;
        state.cleanup.push(await server.stop());
        await expect.poll(() => initial.closed).toBe(true);
        save();
        server = await launch(root, Number(new URL(previous.url).port), env, run, 2);
        recordLaunch(state,server.ready,rendererFiles(),currentHead());
        state.restart = { previous, current: server.ready };
        assert.equal(server.ready.url, previous.url);
        assert.notEqual(server.ready.pid, previous.pid);
        await expect.poll(() => sockets.some(row => row !== initial && validSocket(row)), { timeout: 30000 }).toBe(true);
        state.wss_reconnected = sockets.filter(row => row !== initial && validSocket(row)).map(row => ({ ...row }));
        save();
        console.log('SAME_PAGE_SAME_ORIGIN_WSS_RECONNECTED');
        const previousContext = context;
        context = await browser.newContext({ ignoreHTTPSErrors: true, viewport: { width: 1440, height: 1000 } });
        await fence(context);
        page = await context.newPage();
        observe(page);
        await login(page);
        await verifyRestored(page);
        await screenshot('original-saved-evaluation-new-login');
        // Fresh cookie/CSRF authority: change and restore one label through actual UI.
        await stage(page, 1);
        const label = state.labels[0];
        const image = (await api(state.imagesRoute)).items.find(row => row.file_path === label.image_path);
        await page.locator('[data-labeling-filmstrip]').getByAltText(image.file_name, { exact: true }).click();
        const picker = page.getByRole('region', { name: '이미지 클래스', exact: true });
        for (const desired of [label.from, label.to]) {
            const saved = wire(page, '/api/dataset/metadata/class-override', 'PUT');
            await picker.getByRole('button', { name: desired, exact: true }).click();
            assert.equal((await checked(await saved)).class, desired);
        }
        state.fresh_csrf_ui_write = true;
        verifySnapshot(state.preserved);
        assert.equal(fileSha(state.splitFile), state.splitSha);
        assert.equal(state.requests.filter(row => row.path === '/api/training/start' && row.method === 'POST').length, 1);
        const cookies = await context.cookies(server.ready.url);
        const session = cookies.find(row => row.name === 'vision_session');
        assert.ok(session?.httpOnly && session?.secure && session?.sameSite === 'Strict');
        const storage = await page.evaluate(() => JSON.stringify({ local: { ...localStorage }, session: { ...sessionStorage } }));
        assert.ok(!storage.includes(credential.password));
        state.secure_cookie = { httpOnly: session.httpOnly, secure: session.secure, sameSite: session.sameSite };
        state.status = 'passed';
        state.finished_at = new Date().toISOString();
        save();
        await previousContext.close();
    }
    catch (error) {
        state.status = 'failed';
        state.failures.push({ at: new Date().toISOString(), message: String(error.stack || error), job_id: state.job_id || null });
        save();
        if (page)
            await screenshot('failure-' + state.failures.length).catch(() => { });
        throw error;
    }
    finally {
        if (browser)
            await browser.close();
        if (server)
            state.cleanup.push(await server.stop());
        state.closed_at = new Date().toISOString();
        save();
        console.log('FINAL_STATUS=' + state.status);
    }
}
if (require.main === module)
    main().catch(error => { console.error(error.stack); process.exitCode = 1; });
