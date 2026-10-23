// Audit F-07/F-12/F-20/A5/A7: step 3 names the real device, offers weights ahead with progress, and lists stopped runs.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),Module=require('node:module'),ts=require('typescript'),test=require('node:test');
function load(file,mocks){const name=path.join(__dirname,file);const mod=new Module(name,module);mod.filename=name;mod.paths=Module._nodeModulePaths(__dirname);
 mod.require=ref=>ref in mocks?mocks[ref]:require(ref);
 mod._compile(ts.transpileModule(fs.readFileSync(name,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.ReactJSX}}).outputText,name);return mod.exports;}
const prefetch=load('WeightsPrefetch.tsx',{'../../services/api':{request:async()=>null},'./ProgramWorkbenchControls':{programButton:''}});
test('only official downloadable weights are offered ahead',()=>{
 assert.equal(prefetch.prefetchSupported('dinov3_vits16'),true);assert.equal(prefetch.prefetchSupported('yolo26n'),true);
 assert.equal(prefetch.prefetchSupported('resnet18'),false);assert.equal(prefetch.prefetchSupported('dino_synthetic'),false);
});
test('progress reads as megabytes and percent, and a failure keeps its Korean next step',()=>{
 const mb=1024*1024;
 assert.equal(prefetch.prefetchProgressText({model:'m',state:'downloading',bytes:21*mb,total:84*mb,error:null}),'받는 중 21.0MB / 84.0MB (25%)');
 assert.equal(prefetch.prefetchProgressText({model:'m',state:'downloading',bytes:mb,total:null,error:null}),'받는 중 1.0MB');
 assert.equal(prefetch.prefetchProgressText({model:'m',state:'done',bytes:84*mb,total:84*mb,error:null}),'받기 완료 · 84.0MB');
 assert.equal(prefetch.prefetchProgressText({model:'m',state:'failed',bytes:0,total:null,error:'인터넷 연결을 확인하세요'}),'인터넷 연결을 확인하세요');
});
test('the card names the device the check resolved, including auto',()=>{
 const source=fs.readFileSync(path.join(__dirname,'TrainingPreparationPanel.tsx'),'utf8');
 const start=source.indexOf('export function deviceLabel');const body=source.slice(start,source.indexOf('\n}',start)+2);
 const m=new Module('deviceLabel.ts',module);m._compile(ts.transpileModule(body.replace('export function','function')+'\nmodule.exports={deviceLabel};',{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,'deviceLabel.ts');
 const {deviceLabel}=m.exports;
 assert.equal(deviceLabel('auto',null),'자동 선택');
 assert.equal(deviceLabel('auto',{runtime:{available:true,device:'mps',auto:true}}),'MPS (자동 선택)');
 assert.equal(deviceLabel('cpu',{runtime:{available:true,device:'cpu'}}),'CPU');
 assert.equal(deviceLabel('cuda',{runtime:{available:false,device:'cuda'}}),'CUDA');
 assert.match(source,/request<Readiness>\(`\/api\/training-workspace\/readiness\?\$\{query\}`\)/,'the automatic check is a read, never a write');
});
test('step 3 passes auto through and re-reads the resume list when a run stops',()=>{
 const controller=fs.readFileSync(path.join(__dirname,'TrainingController.tsx'),'utf8');
 assert.match(controller,/device=\{nextDevice==='mps'\|\|nextDevice==='cuda'\|\|nextDevice==='cpu' \? nextDevice : 'auto'\}/);
 assert.match(controller,/\['completed','aborted','failed','cancelled','stopped'\]\.includes\(status\) \? `\$\{jobId\}:\$\{status\}`/);
 assert.match(controller,/이어갈 수 없는 중단 작업 \{resumeExcluded\.length\}개/);
 assert.match(controller,/timer=setTimeout\(\(\)=>read\(false\),1500\)/);
});
