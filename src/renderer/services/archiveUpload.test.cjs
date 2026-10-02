const test=require('node:test'),assert=require('node:assert/strict'),crypto=require('node:crypto'),fs=require('node:fs'),path=require('node:path'),Module=require('node:module'),ts=require('typescript');
function load(file){const name=path.join(__dirname,file),m=new Module(name,module);m.filename=name;m.paths=Module._nodeModulePaths(path.dirname(name));m.require=ref=>ref.startsWith('.')?load(path.relative(__dirname,path.join(path.dirname(name),ref))+'.ts'):require(ref);m._compile(ts.transpileModule(fs.readFileSync(name,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,name);return m.exports;}
const {uploadArchive,UPLOAD_CHUNK}=load('archiveUpload.ts');
// A server that keeps committed bytes like ArtifactStore.append: chunks at the committed offset extend it.
function server(size,{dropAt=null,refuse=null,statusDownOnce=false}={}){const state={offset:0,chunks:[],calls:0,statusCalls:0,completed:false,stored:[],cancelled:0};
 const upload=()=>({id:'u1',kind:'source',sha256:state.sha256,size_bytes:size,offset:state.offset,state:'staging',artifact_id:null,expires_at:null});
 return {state,client:{
  beginUpload:async data=>{state.sha256=data.sha256;assert.equal(data.size_bytes,size);return {upload:upload()};},
  uploadStatus:async()=>{state.statusCalls+=1;if(statusDownOnce&&state.statusCalls===1)throw new Error('network down');return {upload:upload()};},
  cancelUpload:async()=>{state.cancelled+=1;return {state:'cancelled'};},
  uploadChunk:async(id,offset,chunk)=>{state.calls+=1;assert.ok(chunk.size<=UPLOAD_CHUNK);
   if(refuse!==null&&state.calls===refuse)throw Object.assign(new Error('quota'),{status:413});
   const bytes=Buffer.from(await chunk.arrayBuffer());
   if(offset===state.offset){state.stored.push(bytes);state.offset+=bytes.length;}
   if(dropAt!==null&&state.calls===dropAt)throw new Error('connection reset after the server committed the chunk');
   state.chunks.push([offset,bytes.length]);return {upload:upload()};},
  completeUpload:async()=>{state.completed=true;return {artifact_ref:{id:'a'.repeat(32),revision:1,sha256:state.sha256}};},
 }};}
const zip=bytes=>new File([bytes],'set.zip',{type:'application/zip'});
test('an archive is hashed locally, sent in 4 MiB chunks and completed with the same digest',async()=>{
 const bytes=crypto.randomBytes(UPLOAD_CHUNK*2+1234),{state,client}=server(bytes.length),phases=[];
 const result=await uploadArchive(zip(bytes),client,{onProgress:p=>phases.push(p.phase),wait:async()=>{}});
 assert.equal(result.sha256,crypto.createHash('sha256').update(bytes).digest('hex'));
 assert.deepEqual(state.chunks.map(([offset])=>offset),[0,UPLOAD_CHUNK,UPLOAD_CHUNK*2]);
 assert.ok(Buffer.concat(state.stored).equals(bytes)&&state.completed&&result.artifact.sha256===result.sha256);
 assert.deepEqual([...new Set(phases)],['hashing','uploading','verifying']);});
test('a dropped chunk resumes from the offset the server committed',async()=>{
 const bytes=crypto.randomBytes(UPLOAD_CHUNK*3),{state,client}=server(bytes.length,{dropAt:2});
 await uploadArchive(zip(bytes),client,{wait:async()=>{}});
 assert.equal(state.statusCalls,1);assert.ok(Buffer.concat(state.stored).equals(bytes),'no byte sent twice or skipped');});
test('a refused chunk stops without retrying, an abort stops, and only ZIP files are accepted',async()=>{
 const bytes=crypto.randomBytes(UPLOAD_CHUNK+5),refused=server(bytes.length,{refuse:1});
 await assert.rejects(uploadArchive(zip(bytes),refused.client,{wait:async()=>{}}),/quota/);
 assert.equal(refused.state.statusCalls,0);
 const controller=new AbortController();controller.abort();
 await assert.rejects(uploadArchive(zip(bytes),server(bytes.length).client,{signal:controller.signal}),/중지/);
 await assert.rejects(uploadArchive(new File([bytes],'set.tar'),server(bytes.length).client),/ZIP 파일만/);});
test('a stopped, refused or failed upload releases its server reservation, and an unreachable status call is retried',async()=>{
 const bytes=crypto.randomBytes(UPLOAD_CHUNK*2+9);
 const refused=server(bytes.length,{refuse:2});
 await assert.rejects(uploadArchive(zip(bytes),refused.client,{wait:async()=>{}}),/quota/);
 assert.equal(refused.state.cancelled,1,'the quota held by the refused upload is released');
 const controller=new AbortController(),aborted=server(bytes.length);
 const stopAfterFirst={...aborted.client,uploadChunk:async(...args)=>{const result=await aborted.client.uploadChunk(...args);controller.abort();return result;}};
 await assert.rejects(uploadArchive(zip(bytes),stopAfterFirst,{signal:controller.signal,wait:async()=>{}}),/중지/);
 assert.equal(aborted.state.cancelled,1,'a stopped upload is cancelled on the server');
 const flaky=server(bytes.length,{dropAt:1,statusDownOnce:true});
 await uploadArchive(zip(bytes),flaky.client,{wait:async()=>{}});
 // the status call fails, so the committed chunk is sent again; the server answers it with its offset (as ArtifactStore does)
 assert.ok(flaky.state.completed&&flaky.state.cancelled===0&&flaky.state.statusCalls===1&&flaky.state.calls===4,'one unreachable status call does not end the upload');
 assert.ok(Buffer.concat(flaky.state.stored).equals(bytes));});
