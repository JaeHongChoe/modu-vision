const test=require('node:test'),assert=require('node:assert/strict'),path=require('node:path'),fs=require('node:fs');
const file=path.join(__dirname,'protocol.ts');const impl=fs.existsSync(file)?require('./testSupport.cjs').load(file):{};
const frame={id:'rpc_1',actor_id:'actor-a',session_id:'session-a',tab_id:'tab-a',method:'dialog.folder',args:{purpose:'dataset-import'}};
test('bounded framed requests accept chunk boundaries but reject unknown operations and invalid identities',()=>{
 assert.equal(typeof impl.FrameDecoder,'function','missing bounded framed protocol');
 const decoder=new impl.FrameDecoder(),line=JSON.stringify(frame)+'\n';
 assert.deepEqual(decoder.push(Buffer.from(line.slice(0,20))),[]);assert.deepEqual(decoder.push(Buffer.from(line.slice(20))),[frame]);
 assert.throws(()=>impl.parseRequest({...frame,method:'shell.exec'}),e=>e.code==='unknown_method');
 for(const bad of ['', '../actor', 'a\nb', 'x'.repeat(201)])assert.throws(()=>impl.parseRequest({...frame,actor_id:bad}),e=>e.code==='invalid_request');
 assert.throws(()=>impl.parseRequest({...frame,args:[]}),e=>e.code==='invalid_request');
});
test('oversized partial input fails before retaining or executing it, including multibyte bytes',()=>{
 assert.equal(typeof impl.FrameDecoder,'function','missing bounded framed protocol');
 const decoder=new impl.FrameDecoder(40);assert.throws(()=>decoder.push(Buffer.from('가'.repeat(14))),e=>e.code==='frame_too_large');
 assert.throws(()=>decoder.push(Buffer.from(JSON.stringify(frame)+'\n')),e=>e.code==='frame_too_large');
});
