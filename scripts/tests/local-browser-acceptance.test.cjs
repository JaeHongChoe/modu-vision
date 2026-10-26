const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const helper = require('../qa/local-browser-acceptance.cjs');
test('saved restart evidence rejects a modified evaluation artifact', () => {
    assert.equal(typeof helper.snapshot, 'function', 'saved artifact guard must exist');
    const root = fs.mkdtempSync(path.join(os.tmpdir(), 'modu-saved-'));
    try {
        const file = path.join(root, 'eval.json');
        fs.writeFileSync(file, '{"job_id":"original"}');
        const before = helper.snapshot([file]);
        helper.verifySnapshot(before);
        fs.writeFileSync(file, '{"job_id":"replacement"}');
        assert.throws(() => helper.verifySnapshot(before), /Saved artifact changed/);
    }
    finally {
        fs.rmSync(root, { recursive: true, force: true });
    }
});
test('persisted dispatch intent refuses a duplicate model run after interruption', () => {
    assert.equal(typeof helper.reserveTraining, 'function', 'dispatch guard must exist');
    const root = fs.mkdtempSync(path.join(os.tmpdir(), 'modu-dispatch-'));
    try {
        const file = path.join(root, 'dispatch.json');
        helper.reserveTraining(file);
        assert.throws(() => helper.reserveTraining(file), /already reserved/);
    }
    finally {
        fs.rmSync(root, { recursive: true, force: true });
    }
});
test('ready parsing requires exact public fields and requested owned root', () => {
    assert.equal(typeof helper.parseReady, 'function', 'ready validator must exist');
    const data = { url: 'https://localhost:12345', pid: 42, data_dir: '/owned', certificate_file: '/owned/tls/localhost-cert.pem', certificate_sha256: 'a'.repeat(64) };
    assert.deepEqual(helper.parseReady('MODU_WEB_READY=' + JSON.stringify(data), '/owned'), data);
    assert.throws(() => helper.parseReady('MODU_WEB_READY=' + JSON.stringify({ ...data, token: 'secret' }), '/owned'), /public fields/);
    assert.throws(() => helper.parseReady('MODU_WEB_READY=' + JSON.stringify(data), '/other'), /root/);
});
test('pure byte fixture reuse produces a ZIP with sixteen unique synthetic images', () => {
    const root = fs.mkdtempSync(path.join(os.tmpdir(), 'modu-fixture-'));
    try {
        const result = helper.fixtures(root);
        assert.equal(result.pixels.length, 16);
        assert.equal(new Set(result.pixels.map(row => row.sha256)).size, 16);
        assert.equal(fs.readFileSync(result.archive).readUInt32LE(0), 0x04034b50);
    }
    finally {
        fs.rmSync(root, { recursive: true, force: true });
    }
});
test('unsent reservation recovery refuses any actual training request or saved job', () => {
    assert.equal(typeof helper.releaseUnsentTraining, 'function', 'unsent recovery guard must exist');
    const root = fs.mkdtempSync(path.join(os.tmpdir(), 'modu-unsent-'));
    try {
        const file = path.join(root, 'dispatch.json');
        helper.reserveTraining(file);
        assert.throws(() => helper.releaseUnsentTraining(file, [{ method: 'POST', path: '/api/training/start' }], []), /dispatch/);
        assert.throws(() => helper.releaseUnsentTraining(file, [], [{ kind: 'training', job_id: 'saved' }]), /job/);
        helper.releaseUnsentTraining(file, [], []);
        assert.equal(fs.existsSync(file), false);
        assert.equal(fs.existsSync(file + '.unsent.json'), true);
        helper.reserveTraining(file);
    }
    finally {
        fs.rmSync(root, { recursive: true, force: true });
    }
});
test('response observation has immediate rejection handling while preserving later rejection', async () => {
    assert.equal(typeof helper.observeResponse, 'function', 'observer guard must exist');
    const promise = Promise.reject(new Error('page closed'));
    const observed = helper.observeResponse(promise);
    await new Promise(resolve => setImmediate(resolve));
    await assert.rejects(observed, /page closed/);
});
test('launch provenance appends current source and build hashes without rewriting the first source',()=>{
    assert.equal(typeof helper.recordLaunch,'function','append-only launch provenance required');
    const root=fs.mkdtempSync(path.join(os.tmpdir(),'modu-provenance-'));
    try{const file=path.join(root,'index.html');fs.writeFileSync(file,'first build');const state={source_head:'initial'};helper.recordLaunch(state,{pid:1},[file],'first');fs.writeFileSync(file,'second build');helper.recordLaunch(state,{pid:2},[file],'second');assert.equal(state.source_head,'initial');assert.deepEqual(state.launches.map(row=>row.source_head),['first','second']);assert.notEqual(state.launches[0].renderer[file],state.launches[1].renderer[file]);assert.equal(state.launches[0].ready.pid,1);}finally{fs.rmSync(root,{recursive:true,force:true});}
});
test('owned artifact reservation preserves failed-start and screenshot files across resumes',()=>{
    assert.equal(typeof helper.reserveArtifact,'function','unique artifact reservation required');
    const root=fs.mkdtempSync(path.join(os.tmpdir(),'modu-artifacts-'));
    try{for(const name of ['launcher-resume.log','launcher-2.log'])fs.writeFileSync(path.join(root,name),'old failure');
        const first=helper.reserveArtifact(root,'launcher-resume','.log');fs.writeFileSync(first,'new failed startup');
        const second=helper.reserveArtifact(root,'launcher-resume','.log');assert.notEqual(first,second);assert.equal(fs.readFileSync(first,'utf8'),'new failed startup');
        const image=helper.reserveArtifact(root,'failure-1','.png');fs.writeFileSync(image,'saved screenshot');assert.notEqual(helper.reserveArtifact(root,'failure-1','.png'),image);assert.equal(fs.readFileSync(image,'utf8'),'saved screenshot');
        for(const name of ['launcher-resume.log','launcher-2.log'])assert.equal(fs.readFileSync(path.join(root,name),'utf8'),'old failure');
    }finally{fs.rmSync(root,{recursive:true,force:true});}
});
function cleanupFixture() {
    const root = fs.mkdtempSync(path.join(os.tmpdir(), 'modu-cleanup-'));
    const receipt = path.join(root, 'state.json');
    const state = { status: 'passed', cleanup: [], failures: [], job_id: 'retained-job' };
    fs.writeFileSync(receipt, JSON.stringify(state));
    const writes = [];
    return { root, receipt, state, writes, save() { writes.push(JSON.parse(JSON.stringify(state))); fs.writeFileSync(receipt, JSON.stringify(state)); }, dispose() { fs.rmSync(root, { recursive: true, force: true }); } };
}
test('browser close rejection still stops the owned server and persists cleanup failure', async () => {
    const f = cleanupFixture();
    let stopped = false;
    try {
        await assert.rejects(helper.finalizeRun({ state: f.state, save: f.save, bodySucceeded: true, browser: { async close() { throw new Error('browser close rejected'); } }, server: { async stop() { stopped = true; return { port_closed: true }; } } }), /browser close rejected/);
        assert.equal(stopped, true, 'server cleanup must still be attempted');
        const saved = JSON.parse(fs.readFileSync(f.receipt));
        assert.equal(saved.status, 'failed');
        assert.equal(saved.cleanup_errors[0].phase, 'browser.close');
        assert.equal(saved.cleanup.length, 1);
        assert.ok(saved.failures.some(row => row.message.includes('browser close rejected')));
    }
    finally {
        f.dispose();
    }
});
test('server stop rejection persists a failed receipt rather than the earlier success', async () => {
    const f = cleanupFixture();
    let closed = false;
    try {
        await assert.rejects(helper.finalizeRun({ state: f.state, save: f.save, bodySucceeded: true, browser: { async close() { closed = true; } }, server: { async stop() { throw new Error('server stop rejected'); } } }), /server stop rejected/);
        assert.equal(closed, true);
        const saved = JSON.parse(fs.readFileSync(f.receipt));
        assert.equal(saved.status, 'failed');
        assert.equal(saved.cleanup_errors[0].phase, 'server.stop');
        assert.ok(saved.failures.some(row => row.message.includes('server stop rejected')));
    }
    finally {
        f.dispose();
    }
});
test('cleanup attempts all actions and preserves the original body failure and history', async () => {
    const f = cleanupFixture(), original = new Error('original acceptance failure');
    const calls = [];
    f.state.status = 'failed';
    f.state.failures.push({ message: original.message });
    try {
        await assert.rejects(helper.finalizeRun({ state: f.state, save: f.save, bodySucceeded: false, bodyError: original, browser: { async close() { calls.push('browser'); throw new Error('close fault'); } }, server: { async stop() { calls.push('server'); throw new Error('stop fault'); } } }), error => error === original);
        assert.deepEqual(calls, ['browser', 'server']);
        const saved = JSON.parse(fs.readFileSync(f.receipt));
        assert.equal(saved.status, 'failed');
        assert.equal(saved.failures[0].message, original.message);
        assert.equal(saved.cleanup_errors.length, 2);
        assert.equal(saved.failures.length, 3);
    }
    finally {
        f.dispose();
    }
});
test('success is persisted only after owned browser close and verified closed port', async () => {
    const f = cleanupFixture(), calls = [];
    f.state.status = 'cleanup-pending';
    try {
        await helper.finalizeRun({ state: f.state, save: f.save, bodySucceeded: true, browser: { async close() { assert.notEqual(f.state.status, 'passed'); calls.push('browser'); } }, server: { async stop() { assert.notEqual(f.state.status, 'passed'); calls.push('server'); return { port_closed: true }; } } });
        assert.deepEqual(calls, ['browser', 'server']);
        const saved = JSON.parse(fs.readFileSync(f.receipt));
        assert.equal(saved.status, 'passed');
        assert.ok(saved.closed_at);
        assert.ok(saved.finished_at);
        assert.equal(saved.cleanup_errors.length, 0);
    }
    finally {
        f.dispose();
    }
});
test('a returned open-port receipt cannot become successful acceptance', async () => {
    const f = cleanupFixture();
    try {
        await assert.rejects(helper.finalizeRun({ state: f.state, save: f.save, bodySucceeded: true, server: { async stop() { return { port_closed: false }; } } }), /port.*clos/i);
        assert.equal(JSON.parse(fs.readFileSync(f.receipt)).status, 'failed');
    }
    finally {
        f.dispose();
    }
});
test('initial and reconnect telemetry witnesses require hardware frames rather than heartbeat alone', () => {
    const context = { project_id: 'project', workspace_id: 'workspace', actor_id: 'actor' }, origin = 'https://localhost:12345';
    const initial = { origin: 'wss://localhost:12345', path: '/ws/telemetry', context, closed: false, frames: [{ event: 'pong' }] };
    const reconnect = { ...initial, frames: [{ event: 'pong' }, { event: 'hardware_stats' }] };
    assert.equal(helper.validTelemetrySocket(initial, origin, context), false, 'heartbeat-only initial socket is insufficient');
    assert.equal(helper.validTelemetrySocket(reconnect, origin, context), true);
    assert.equal(helper.validTelemetrySocket({ ...reconnect, closed: true }, origin, context), false);
    assert.equal(helper.validTelemetrySocket(reconnect, origin, { ...context, actor_id: 'other' }), false);
});

const secureHeaders = {
    'x-content-type-options': 'nosniff',
    'x-frame-options': 'DENY',
    'referrer-policy': 'no-referrer',
    'content-security-policy': "frame-ancestors 'none'",
};
test('successful HTTP status cannot hide any missing browser security header', () => {
    for (const name of Object.keys(secureHeaders)) {
        const headers = { ...secureHeaders };
        delete headers[name];
        assert.throws(() => helper.verifyWireResponse({ status: 200, headers }, 200), /security header/, name);
    }
});
test('a refusal status cannot hide incorrect browser security header values', () => {
    for (const [name, value] of Object.entries({
        'x-content-type-options': 'sniff',
        'x-frame-options': 'SAMEORIGIN',
        'referrer-policy': 'unsafe-url',
        'content-security-policy': "frame-ancestors *",
    })) {
        assert.throws(() => helper.verifyWireResponse({ status: 403, headers: { ...secureHeaders, [name]: value } }, 403), /security header/, name);
    }
});
test('wire evidence contains only selected security headers and still verifies status', () => {
    const response = { status: 401, headers: { ...secureHeaders, 'set-cookie': 'private-cookie', authorization: 'private-token', 'x-extra': 'unrelated' } };
    assert.deepEqual(helper.verifyWireResponse(response, [401, 403]), { status: 401, headers: secureHeaders });
    assert.throws(() => helper.verifyWireResponse({ ...response, status: 200 }, [401, 403]), /status/);
    assert.equal(response.headers['set-cookie'], 'private-cookie', 'the original response is not rewritten');
});
test('a route-specific sandbox policy is preserved only when explicitly expected', () => {
    const sandbox = "sandbox; default-src 'none'; img-src data:; style-src 'unsafe-inline'";
    const headers = Object.freeze({ ...secureHeaders, 'content-security-policy': sandbox });
    const checked = helper.verifyWireResponse({ status: 200, headers }, 200, { contentSecurityPolicy: sandbox });
    assert.deepEqual(checked, { status: 200, headers });
    assert.throws(() => helper.verifyWireResponse({ status: 200, headers }, 200), /security header/);
});
test('acceptance run base keeps the historical default', () => {
    assert.equal(helper.resolveRunBase('/owned/repo', {}), '/owned/repo/.superpowers/sdd/2026-10-26-local-browser-launch/private');
});
test('an explicit acceptance run base places new evidence in the requested plan', () => {
    assert.equal(helper.resolveRunBase('/owned/repo', { MV_BROWSER_ACCEPTANCE_BASE: '/owned/integration/private' }), '/owned/integration/private');
});
test('supporting cookie readback sends the exact trusted product Origin', () => {
    const headers = helper.apiReadHeaders('https://localhost:53154', { project_id: 'selected-project' });
    assert.equal(headers.Origin, 'https://localhost:53154');
});
test('supporting cookie readback retains its trusted Origin before project context exists', () => {
    assert.deepEqual(helper.apiReadHeaders('https://localhost:53154', null), { Origin: 'https://localhost:53154' });
});
test('supporting readback preserves the selected project and complete context without mutation', () => {
    const context = Object.freeze({ project_id: 'selected-project', workspace_id: 'workspace', actor_id: 'owner', extra: 'retained' });
    const headers = helper.apiReadHeaders('https://localhost:53154', context);
    assert.equal(headers['X-Vision-Project'], 'selected-project');
    assert.equal(headers['X-Vision-Context'], '{"project_id":"selected-project","workspace_id":"workspace","actor_id":"owner","extra":"retained"}');
    assert.deepEqual(context, { project_id: 'selected-project', workspace_id: 'workspace', actor_id: 'owner', extra: 'retained' });
    assert.equal(headers.Authorization, undefined);
});
