const test=require('node:test'),assert=require('node:assert/strict'),crypto=require('node:crypto'),fs=require('node:fs'),path=require('node:path'),Module=require('node:module'),ts=require('typescript');
function load(file){const name=path.join(__dirname,file),m=new Module(name,module);m.filename=name;m.paths=Module._nodeModulePaths(__dirname);m.require=ref=>ref.startsWith('.')?load(ref+'.ts'):require(ref);m._compile(ts.transpileModule(fs.readFileSync(name,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,name);return m.exports;}
const {Sha256Stream}=load('sha256Stream.ts');
const node=bytes=>crypto.createHash('sha256').update(bytes).digest('hex');
test('known vectors, every padding boundary and any chunking match the platform digest',()=>{
 assert.equal(new Sha256Stream().hex(),'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855');
 assert.equal(new Sha256Stream().update(new TextEncoder().encode('abc')).hex(),'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad');
 for(const size of [1,55,56,57,63,64,65,119,120,127,128,129,1000,4096+3]){
  const bytes=crypto.randomBytes(size);assert.equal(new Sha256Stream().update(bytes).hex(),node(bytes),`size ${size}`);}
 const big=crypto.randomBytes(3*1024*1024+17);const stream=new Sha256Stream();
 let offset=0,step=1;while(offset<big.length){const next=Math.min(big.length,offset+step);stream.update(big.subarray(offset,next));offset=next;step=(step*7+13)%200003+1;}
 assert.equal(stream.hex(),node(big),'chunk sizes that never align with 64-byte blocks');});
test('a finished digest cannot be fed or read again',()=>{const stream=new Sha256Stream();stream.hex();
 assert.throws(()=>stream.update(new Uint8Array(1)),/finished/);assert.throws(()=>stream.hex(),/finished/);});
