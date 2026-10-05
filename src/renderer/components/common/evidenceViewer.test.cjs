const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),Module=require('node:module'),ts=require('typescript');
const file=path.join(__dirname,'evidenceViewer.ts'),m=new Module(file,module);m.filename=file;m.paths=Module._nodeModulePaths(__dirname);
function model(){m._compile(ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS}}).outputText,file);return m.exports;}
test('viewer bounds zoom and rejects invalid ROI geometry instead of drawing another coordinate space',()=>{
 const v=model();assert.equal(v.boundedZoom(Infinity),1);assert.equal(v.boundedZoom(100),8);assert.equal(v.boundedZoom(.01),.25);
 assert.equal(v.validBox([10,20,40,50],[100,100]),true);assert.equal(v.validBox([40,20,10,50],[100,100]),false);
 assert.equal(v.validBox([0,0,120,100],[100,100]),false);assert.equal(v.validBox([0,0,NaN,10],[100,100]),false);
});
test('viewer layers accept only inline image snapshots and blend only equal coordinate spaces',()=>{
 const v=model(),a={id:'original',image:'data:image/png;base64,AA==',space:'source',size:[100,50]},b={...a,id:'overlay'};
 assert.equal(v.safeSnapshot(a.image),true);for(const value of ['https://foreign.example/image.png','file:///private/image.png','data:image/svg+xml,<svg/>'])assert.equal(v.safeSnapshot(value),false);
 assert.equal(v.canBlend(a,b),true);assert.equal(v.canBlend(a,{...b,space:'roi'}),false);assert.equal(v.canBlend(a,{...b,size:[50,25]}),false);
});
