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
test('fixed input recipe overlays undo rotation and crop without painting excluded pixels',()=>{
 const hash='a'.repeat(64),geometry={source_size:[4,3],prepared_size:[2,3],steps:[{op:'crop',before:[4,3],box:[1,0,4,2]},{op:'rotate90',before:[3,2],turns:1}]};
 const evidence={shape:[3,2],coordinate_space:'model_input',mapping:{kind:'fixed_input_recipe',source_size:[4,3],geometry,input_recipe_sha256:'b'.repeat(64)}};
 assert.deepEqual(helpers.projectionSize(evidence,{image_sha256:hash,original_size:[4,3]},hash),[4,3]);
 const data=new Uint8ClampedArray([3,6,2,5,1,4].flatMap(n=>[n,0,0,255]));
 const actual=helpers.projectRecipePixels(data,2,3,evidence.mapping);
 assert.deepEqual([...actual].filter((_,i)=>i%4===0),[0,1,2,3,0,4,5,6,0,0,0,0]);
 assert.equal(actual[3],0);assert.equal(actual[7],255);assert.equal(data[0],3);
 const invalid={...evidence,mapping:{...evidence.mapping,geometry:{...geometry,steps:[{op:'crop',before:[4,3],box:[-1,0,3,2]}]}}};
 assert.throws(()=>helpers.projectionSize(invalid,{image_sha256:hash,original_size:[4,3]},hash),/좌표/);
});
test('fixed recipe geometry validates chain, declared dimensions and recipe identity',()=>{
 const hash='a'.repeat(64),geometry={source_size:[6,4],prepared_size:[3,2],steps:[{op:'brightness',before:[6,4]},{op:'resize',before:[6,4]}]},mapping={kind:'fixed_input_recipe',source_size:[6,4],geometry,input_recipe_sha256:'b'.repeat(64)},evidence={shape:[2,3],coordinate_space:'model_input',mapping};
 const preview={image_sha256:hash,original_size:[6,4]};assert.deepEqual(helpers.projectionSize(evidence,preview,hash),[6,4]);
 for(const change of [{...mapping,input_recipe_sha256:undefined},{...mapping,geometry:{...geometry,source_size:[4,6]}},{...mapping,geometry:{...geometry,prepared_size:[0,2]}},{...mapping,geometry:{...geometry,steps:[{op:'rotate90',turns:9,before:[6,4]}]}}])assert.throws(()=>helpers.projectionSize({...evidence,mapping:change},preview,hash),/좌표/);
 const pixels=new Uint8ClampedArray(3*2*4).fill(255);assert.equal(helpers.projectRecipePixels(pixels,3,2,mapping).length,6*4*4);
 assert.throws(()=>helpers.projectRecipePixels(pixels,2,3,mapping),/좌표|크기/);
});
