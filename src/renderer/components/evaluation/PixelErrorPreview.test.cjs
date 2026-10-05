const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),Module=require('node:module'),ts=require('typescript');

function fixture(images={}){
 let width=8,height=8,pixels=new Uint8ClampedArray(8*8*4).fill(99),cleanup;const errors=[];
 const canvas={get width(){return width;},set width(value){width=value;pixels=new Uint8ClampedArray(width*height*4);},get height(){return height;},set height(value){height=value;pixels=new Uint8ClampedArray(width*height*4);},getContext(){return ctx;}};
 const ctx={drawImage(img){for(let y=0;y<Math.min(height,img.height);y++)for(let x=0;x<Math.min(width,img.width);x++)pixels.set(img.data.slice((y*img.width+x)*4,(y*img.width+x+1)*4),(y*width+x)*4);},getImageData(){return {width,height,data:pixels.slice()};},createImageData(w,h){return{width:w,height:h,data:new Uint8ClampedArray(w*h*4)};},putImageData(data){pixels=data.data.slice();}};
 const react={useRef:()=>({current:canvas}),useState:value=>[value,next=>errors.push(next)],useEffect(fn){cleanup?.();cleanup=fn();},useMemo:fn=>fn()};
 class Image {set src(value){const data=images[value];if(!data){queueMicrotask(()=>this.onerror?.());return;}Object.assign(this,data);queueMicrotask(()=>this.onload?.());}}
 const prior=global.Image;global.Image=Image;
 const file=path.join(__dirname,'EvaluationEvidencePanel.tsx'),m=new Module(file,module);m.filename=file;m.paths=Module._nodeModulePaths(__dirname);const req=m.require.bind(m);
 m.require=name=>name==='react'?react:name.endsWith('/services/api')?{resolveApiUrl:x=>x}:name.endsWith('/services/evaluationEvidence')?{}:name==='./pixelEvidencePreview'?helper():req(name);
 m._compile(ts.transpileModule(fs.readFileSync(file,'utf8').replace('function PixelErrorPreview(', 'export function PixelErrorPreview('),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.ReactJSX}}).outputText,file);
 return {canvas,errors,pixels:()=>pixels,async render(evidence,className='all'){m.exports.PixelErrorPreview({row:{pixel_evidence:evidence},className});await new Promise(setImmediate);},restore(){cleanup?.();global.Image=prior;}};
}
const mask=(width,height,id)=>({width,height,data:Uint8ClampedArray.from({length:width*height*4},(_,i)=>i%4===3?255:id)});
function helper(){const file=path.join(__dirname,'pixelEvidencePreview.ts'),m=new Module(file,module);m.filename=file;m._compile(ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,file);return m.exports;}
test('a missing truth mask clears the previously displayed pixel evidence',async()=>{
 const f=fixture();try{await f.render({prediction_mask:'pred',coordinate_space:'model_input_px'});assert.equal(f.canvas.width,0);assert.equal(f.canvas.height,0);assert(f.errors.some(message=>String(message).includes('정답')));}finally{f.restore();}
});
test('different mask dimensions refuse comparison and leave no previous canvas',async()=>{
 const f=fixture({pred:mask(4,4,7),truth:mask(2,2,0)});try{await f.render({prediction_mask:'pred',truth_mask:'truth',shape:[4,4]});assert.equal(f.canvas.width,0);assert.equal(f.canvas.height,0);assert(f.errors.some(message=>String(message).includes('크기')));}finally{f.restore();}
});
test('truth, prediction and errors preserve exact sparse class IDs and all pixel counts',()=>{
 const {pixelEvidencePixels}=helper();const pred=mask(4,1,0),truth=mask(4,1,0);[0,7,23,0].forEach((id,i)=>pred.data.set([id,id,id,255],4*i));[0,7,0,23].forEach((id,i)=>truth.data.set([id,id,id,255],4*i));const evidence={shape:[1,4],per_class:{Scratch:{class_id:7},Crack:{class_id:23}}};
 assert.deepEqual([...pixelEvidencePixels(pred,truth,evidence,'all','truth')],[35,35,35,255,74,222,128,255,35,35,35,255,74,222,128,255]);
 assert.deepEqual([...pixelEvidencePixels(pred,truth,evidence,'Crack','prediction')],[35,35,35,255,35,35,35,255,34,211,238,255,35,35,35,255]);
 assert.deepEqual([...pixelEvidencePixels(pred,truth,evidence,'all','error')],[35,35,35,255,35,35,35,255,35,180,255,255,244,35,35,255]);
 assert.deepEqual([...pixelEvidencePixels(pred,truth,evidence,'Scratch','error')],Array.from({length:4},()=>[35,35,35,255]).flat());
});
test('declared shape, unknown class ID and non-index PNG pixels are explicit refusals',()=>{
 const {pixelEvidencePixels}=helper(),pred=mask(4,2,7),truth=mask(4,2,0);
 assert.throws(()=>pixelEvidencePixels(pred,truth,{shape:[4,2]},'all','error'),/크기/);
 assert.throws(()=>pixelEvidencePixels(pred,truth,{},'unrecorded','error'),/ID/);
 pred.data[1]=8;assert.throws(()=>pixelEvidencePixels(pred,truth,{},'all','truth'),/클래스 ID/);
});
