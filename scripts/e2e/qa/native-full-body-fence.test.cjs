/* Root-only static VM protocol controls: no Electron/Playwright or product module import. */
'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const SOURCE = path.resolve(__dirname, '../fixtures/native-full-body-fence.cjs');
function load() {
  const box = {module: {exports: {}}}; vm.runInNewContext(fs.readFileSync(SOURCE, 'utf8'), box, {filename: SOURCE});
  return box.module.exports;
}
function fixture(fault) {
  const calls = [], records = []; let detachCount = 0;
  const session = {
    async send(method, parameters) { calls.push([method, parameters === undefined ? null : JSON.parse(JSON.stringify(parameters))]); if (fault) await fault(method, parameters); },
    async detach() { detachCount++; calls.push(['detach', null]); if (fault) await fault('detach'); },
  };
  const page = {context: () => ({async newCDPSession(actual) { assert.equal(actual, page); calls.push(['create', null]); if (fault) await fault('create'); return session; }})};
  return {calls, records, page, session, detachCount: () => detachCount, record: value => records.push(JSON.parse(JSON.stringify(value)))};
}
const expected = [['create', null], ['Network.enable', null], ['Network.setCacheDisabled', {cacheDisabled: true}],
  ['use', null], ['Network.setCacheDisabled', {cacheDisabled: false}], ['Network.disable', null], ['detach', null]];
for (const token of [undefined, '', '0', 'true', '01']) test('default token '+String(token)+' performs no CDP operation', async () => {
  let used = 0; const guard = load(); const value = await guard.withNativeFullBodyReads({context(){throw Error('CDP must be untouched');}}, async () => {used++; return 73;}, () => {throw Error('default must not add a receipt');}, token);
  assert.equal(value, 73); assert.equal(used, 1);
});
test('exact opt-in establishes cache-disabled before body and restores/detaches before return', async () => {
  const guard=load(), f=fixture(); const value=await guard.withNativeFullBodyReads(f.page,async()=>{f.calls.push(['use',null]);return 17;},f.record,'1');
  assert.equal(value,17);assert.deepEqual(f.calls,expected);assert.equal(f.detachCount(),1);assert.equal(f.records.length,1);
  const r=f.records[0];assert.equal(r.cache_disabled_before_use,true);assert.equal(r.cache_restored,true);assert.equal(r.network_disabled,true);assert.equal(r.all_owned_commands_settled,true);assert.equal(r.all_owned_CDP_sessions_closed,true);assert.equal(r.first_exception,null);
});
test('the body waits for the original cache-disable promise', async () => {
  const guard=load();let release,used=false;const gate=new Promise(resolve=>{release=resolve;});const f=fixture(async(method,p)=>{if(method==='Network.setCacheDisabled'&&p.cacheDisabled)await gate;});
  const run=guard.withNativeFullBodyReads(f.page,async()=>{used=true;},f.record,'1');await Promise.resolve();await Promise.resolve();await Promise.resolve();assert.equal(used,false);release();await run;assert.equal(used,true);assert.equal(f.records[0].all_owned_CDP_sessions_closed,true);
});
for (const failing of ['Network.enable','cache-on']) test('setup '+failing+' fails before body but attempts independent restoration and detach', async () => {
  const guard=load(), primary=Object.freeze(new Error('original setup failure'));let used=false;const f=fixture(async(method,p)=>{if(method===failing||(failing==='cache-on'&&method==='Network.setCacheDisabled'&&p.cacheDisabled))throw primary;});
  await assert.rejects(guard.withNativeFullBodyReads(f.page,async()=>{used=true;},f.record,'1'),cause=>cause===primary);assert.equal(used,false);assert.equal(f.detachCount(),1);assert.deepEqual(f.calls.slice(-3),expected.slice(-3));assert.equal(f.records[0].all_owned_CDP_sessions_closed,true);assert.equal(f.records[0].use_invoked,false);
});
test('a creation rejection cannot claim a closed created session', async () => {
  const guard=load(), primary=new Error('create failure'),f=fixture(async(method)=>{if(method==='create')throw primary;});await assert.rejects(guard.withNativeFullBodyReads(f.page,async()=>{throw Error('must not run');},f.record,'1'),cause=>cause===primary);assert.deepEqual(f.calls,[['create',null]]);assert.equal(f.detachCount(),0);assert.equal(f.records[0].all_owned_CDP_sessions_closed,false);
});
test('body exception object remains primary through restoration and detach failures', async () => {
  const guard=load(), primary=Object.freeze(new Error('original body')),restore=new Error('restore'),disable=new Error('disable'),detach=new Error('detach'),f=fixture(async(method,p)=>{if(method==='Network.setCacheDisabled'&&!p.cacheDisabled)throw restore;if(method==='Network.disable')throw disable;if(method==='detach')throw detach;});
  await assert.rejects(guard.withNativeFullBodyReads(f.page,async()=>{f.calls.push(['use',null]);throw primary;},f.record,'1'),cause=>cause===primary);assert.deepEqual(f.calls,expected);assert.equal(f.detachCount(),1);const r=f.records[0];assert.equal(r.secondary_exceptions.length,3);assert.equal(r.detach_uncertain,true);assert.equal(r.all_owned_CDP_sessions_closed,false);
});
for (const failing of ['cache-off','Network.disable','detach']) test('cleanup '+failing+' refuses success and never retries detach',async()=>{
  const guard=load(), primary=new Error('cleanup '+failing),f=fixture(async(method,p)=>{if(method===failing||(failing==='cache-off'&&method==='Network.setCacheDisabled'&&!p.cacheDisabled))throw primary;});await assert.rejects(guard.withNativeFullBodyReads(f.page,async()=>{f.calls.push(['use',null]);},f.record,'1'),cause=>cause===primary);assert.deepEqual(f.calls,expected);assert.equal(f.detachCount(),1);assert.notEqual(f.records[0].first_exception,null);assert.equal(f.records[0].all_owned_commands_settled,true);
});
test('receipt callback failure is negative after all owned protocol closure',async()=>{
 const guard=load(),primary=new Error('receipt'),f=fixture();await assert.rejects(guard.withNativeFullBodyReads(f.page,async()=>{f.calls.push(['use',null]);},()=>{throw primary;},'1'),cause=>cause===primary);assert.deepEqual(f.calls,expected);assert.equal(f.detachCount(),1);
});
test('original fixture primary survives unchanged teardown failure and receipt failure',()=>{
 const guard=load(),primary=Object.freeze(new Error('use')),teardown=new Error('teardown'),receipt=new Error('note');let summary;const state=guard.createFixtureFailure(value=>{summary=value;throw receipt;});state.capture(primary);state.capture(teardown);assert.throws(()=>state.finish({original_app_teardown_attempted:true}),cause=>cause===primary);assert.equal(summary.first_exception,'Error: use');assert.deepEqual(Array.from(summary.secondary_exceptions),['Error: teardown']);
});
test('a rendering fault is bounded and preserves its original primary object',()=>{
 const guard=load(),render=new Error('render'),primary={toString(){throw render;}};let summary;const state=guard.createFixtureFailure(value=>{summary=value;});state.capture(primary);assert.throws(()=>state.finish({}),cause=>cause===primary);assert.equal(summary.rendering_fault_count,1);assert.equal(summary.first_exception,'<exception rendering failed>');
});
test('fixture integration is exact opt-in and restores inside the body adapter before original app.close',()=>{
 const source=fs.readFileSync(path.resolve(__dirname,'../fixtures/test.ts'),'utf8');assert.equal(source.split("const nativeFullBodyFenceMode = process.env.MV_E2E_NATIVE_FULL_BODY_READS;").length-1,1);assert.ok(source.indexOf("require('./native-full-body-fence.cjs')")<source.indexOf('await electron.launch'));assert.match(source,/nativeFullBodyFenceMode === '1'/);assert.ok(source.indexOf('withNativeFullBodyReads(window')<source.indexOf('app.close().catch'));assert.match(source,/else \{\n        await use\(session\);\n      \}/);assert.match(source,/if \(!fullBodyFailure\) throw cause;/);assert.match(source,/if \(fullBodyFailure\) fullBodyFailure.finish/);
});
