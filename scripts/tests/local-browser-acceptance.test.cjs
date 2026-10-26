const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const helper = require('../qa/local-browser-acceptance.cjs');
test('saved restart evidence rejects a modified evaluation artifact', () => {
  assert.equal(typeof helper.snapshot, 'function', 'saved artifact guard must exist');
  const root=fs.mkdtempSync(path.join(os.tmpdir(),'modu-saved-'));
  try {
    const file=path.join(root,'eval.json');fs.writeFileSync(file,'{"job_id":"original"}');
    const before=helper.snapshot([file]);helper.verifySnapshot(before);
    fs.writeFileSync(file,'{"job_id":"replacement"}');
    assert.throws(()=>helper.verifySnapshot(before), /Saved artifact changed/);
  } finally {fs.rmSync(root,{recursive:true,force:true});}
});
test('persisted dispatch intent refuses a duplicate model run after interruption', () => {
  assert.equal(typeof helper.reserveTraining, 'function', 'dispatch guard must exist');
  const root=fs.mkdtempSync(path.join(os.tmpdir(),'modu-dispatch-'));
  try {const file=path.join(root,'dispatch.json');helper.reserveTraining(file);assert.throws(()=>helper.reserveTraining(file), /already reserved/);} finally {fs.rmSync(root,{recursive:true,force:true});}
});
test('ready parsing requires exact public fields and requested owned root', () => {
  assert.equal(typeof helper.parseReady,'function','ready validator must exist');
  const data={url:'https://localhost:12345',pid:42,data_dir:'/owned',certificate_file:'/owned/tls/localhost-cert.pem',certificate_sha256:'a'.repeat(64)};
  assert.deepEqual(helper.parseReady('MODU_WEB_READY='+JSON.stringify(data),'/owned'),data);
  assert.throws(()=>helper.parseReady('MODU_WEB_READY='+JSON.stringify({...data,token:'secret'}),'/owned'), /public fields/);
  assert.throws(()=>helper.parseReady('MODU_WEB_READY='+JSON.stringify(data),'/other'), /root/);
});
