const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),Module=require('node:module'),ts=require('typescript');
function load(file){const m=new Module(file,module);m.filename=file;m.paths=Module._nodeModulePaths(path.dirname(file));const require=m.require.bind(m);m.require=name=>name.startsWith('.')?load(path.resolve(path.dirname(file),name+'.ts')):require(name);m._compile(ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS}}).outputText,file);return m.exports;}
const helpers=load(path.join(__dirname,'evaluationSourceOverlay.ts'));
test('projection requires captured bytes, declared resize and exact native geometry',()=>{
 const hash='a'.repeat(64),preview={image_sha256:hash,original_size:[160,96]},evidence={coordinate_space:'model_input',mapping:{kind:'full_image_resize',source_size:[160,96]}};
 assert.deepEqual(helpers.projectionSize(evidence,preview,hash),[160,96]);
 for(const expected of [undefined,'b'.repeat(64)])assert.throws(()=>helpers.projectionSize(evidence,preview,expected),/해시/);
 for(const changed of [{...evidence,mapping:undefined},{...evidence,coordinate_space:'roi'},{...evidence,mapping:{kind:'letterbox',source_size:[160,96]}},{...evidence,mapping:{kind:'full_image_resize',source_size:[96,160]}}])assert.throws(()=>helpers.projectionSize(changed,preview,hash),/좌표/);
});
test('non-square nearest projection keeps the exact mask, transparent background and unchanged input',()=>{
 const source=new Uint8ClampedArray([35,35,35,255,74,222,128,255,34,211,238,255,244,35,35,255]);const before=source.slice();
 const actual=helpers.projectedPixels(source,2,2,6,4),colors=[[35,35,35,0],[74,222,128,255],[34,211,238,255],[244,35,35,255]];
 const expected=Array.from({length:4},(_,y)=>Array.from({length:6},(_,x)=>colors[(y<2?0:2)+(x<3?0:1)]).flat()).flat();
 assert.deepEqual([...actual],expected);assert.deepEqual(source,before);
 assert.throws(()=>helpers.projectedPixels(source,2,2,0,4),/크기/);
});
