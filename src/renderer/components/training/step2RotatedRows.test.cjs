// Audit F-16 / B3: step-2 rotated boxes fill the rotated detection answer table without overwriting typed rows.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),Module=require('node:module'),ts=require('typescript'),test=require('node:test');
function load(file){const name=path.join(__dirname,file);const m=new Module(name,module);m.filename=name;m.paths=Module._nodeModulePaths(__dirname);
 m.require=ref=>ref.startsWith('./')?load(ref.slice(2)+'.ts'):ref==='../../services/specializedApi'?{}:require(ref);
 m._compile(ts.transpileModule(fs.readFileSync(name,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,name);return m.exports;}
const {mergeRotatedRows,step2ImportNotice}=load('step2RotatedRows.ts');
const sample=(image,split='train')=>({image,split,objects:[{label:'chip',box:{cx:20,cy:20,width:10,height:6,angle_deg:-60}}]});
test('an empty table takes the imported rows; typed rows stay and only new images are added',()=>{
 assert.equal(mergeRotatedRows('',[sample('a.png')]),'a.png\tchip\t20,20,10,6,-60\ttrain');
 const typed='a.png\tchip\t1,1,2,2,0\tval';
 assert.equal(mergeRotatedRows(typed,[sample('a.png'),sample('b.png','test')]),typed+'\nb.png\tchip\t20,20,10,6,-60\ttest');
 assert.throws(()=>mergeRotatedRows('broken row',[sample('a.png')]),/고칠 곳이 있어/);
});
test('the notice says how many, where the split came from, and that nothing is saved yet',()=>{
 const notice=step2ImportNotice({samples:[sample('a.png')],skipped:[],unlabeled_images:3,directions_dropped:1,split_source:'proposed',counts:{images:1,objects:1}});
 assert.match(notice,/회전 박스 1개\(이미지 1장\)/);assert.match(notice,/제안한 분할\(70\/15\/15/);assert.match(notice,/방향 1개는 쓰지 않았습니다/);assert.match(notice,/아직 저장 전입니다/);
 assert.match(step2ImportNotice({samples:[],skipped:[],unlabeled_images:4,directions_dropped:0,split_source:'none',counts:{images:0,objects:0}}),/그린 회전 박스가 없습니다/);
});
test('the workbench offers the import and steps 1, 2 and 4 say where specialist families live',()=>{
 const read=file=>fs.readFileSync(path.join(__dirname,'..',file),'utf8');
 assert.match(read('training/RotatedDetectionPanel.tsx'),/2단계에서 그린 회전 박스 가져오기/);
 assert.match(read('training/RotatedDetectionPanel.tsx'),/setRowsText\(mergeRotatedRows\(rowsText, result\.samples\)\)/);
 assert.match(read('dataset/DatasetStudio.tsx'),/<SpecialistFamilyNote step=\{1\} \/>/);
 assert.match(read('labeling/LabelingStudio.tsx'),/projectTask === 'detection' && <SpecialistFamilyNote step=\{2\} \/>/);
 assert.match(read('evaluation/EvaluationStudio.tsx'),/<SpecialistFamilyNote step=\{4\} \/>/);
});
