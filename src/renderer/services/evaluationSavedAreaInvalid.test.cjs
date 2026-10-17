const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),Module=require('node:module'),ts=require('typescript');
const file=path.join(__dirname,'evaluationEvidence.ts'),m=new Module(file,module);m.filename=file;m.paths=Module._nodeModulePaths(__dirname);
m._compile(ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,file);
const {savedEvidenceIssue,areaHistograms}=m.exports;
const message='저장된 객체 좌표나 결함 면적이 유효하지 않아 분포와 필터를 표시할 수 없습니다.';
const sample=()=>({file_path:'/controlled/case.png',object_evidence:{per_class:{Scratch:{tp:1,fn:0,fp:0}},predicted:[{label:'Scratch',box:[1,2,4,6]}],truth:[]},pixel_evidence:{per_class:{Scratch:{tp:1,fn:0,fp:0,class_id:1,predicted_area_px:2.5}}}});
const bad=[
 ['reversed axial extents',r=>r.object_evidence.predicted[0].box=[4,2,1,6]],
 ['numeric string axial coordinate',r=>r.object_evidence.predicted[0].box=[1,2,'4',6]],
 ['negative rotated extent',r=>r.object_evidence.predicted[0].box={cx:3,cy:4,width:-2,height:5,angle_deg:30}],
 ['overflowed rotated area',r=>r.object_evidence.predicted[0].box={cx:3,cy:4,width:Number.MAX_VALUE,height:2,angle_deg:30}],
 ['negative pixel area',r=>r.pixel_evidence.per_class.Scratch.predicted_area_px=-1],
 ['numeric string pixel area',r=>r.pixel_evidence.per_class.Scratch.predicted_area_px='2.5'],
];
for(const [name,change] of bad)test('saved area refuses '+name+' and preserves original evidence',()=>{const r=sample();change(r);const original=structuredClone(r);assert.equal(savedEvidenceIssue([r]),message);assert.deepEqual(r,original);});
test('valid fractional zero and absent areas keep their measurement semantics',()=>{
 const rows=[sample(),sample(),{file_path:'/controlled/absent.png',pixel_evidence:{per_class:{Scratch:{tp:null,predicted_area_px:null}}}}];
 rows[1].object_evidence.predicted[0].box={cx:3,cy:4,width:0,height:2.5,angle_deg:-90};rows[1].pixel_evidence.per_class.Scratch.predicted_area_px=0;
 assert.equal(savedEvidenceIssue(rows),null);const groups=areaHistograms(rows,'all');
 assert.deepEqual(groups.find(g=>g.unit==='original_image_px2').bins.flatMap(b=>b.items.map(i=>i.area)).sort((a,b)=>a-b),[0,12]);
 assert.deepEqual(groups.find(g=>g.unit==='model_input_px2').bins.flatMap(b=>b.items.map(i=>i.area)).sort((a,b)=>a-b),[0,2.5]);
});
