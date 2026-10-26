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
