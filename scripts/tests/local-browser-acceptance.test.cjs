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
