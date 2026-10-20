// Audit B5 / F-22: the gallery's thumbnail reads leave a trace of how they ended, with file names only.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),Module=require('node:module'),ts=require('typescript'),test=require('node:test');
function load(){const file=path.join(__dirname,'thumbnailDiagnostics.ts'),m=new Module(file,module);m.filename=file;
 m._compile(ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,file);return m.exports;}
test('outcomes are counted, explanations kept newest first, and only the file name is stored',()=>{
 const d=load();let heard=0;const stop=d.subscribeThumbnailDiagnostics(()=>heard++);
 d.recordThumbnailRead('/api/dataset/thumbnail/a.png?file_path=%2Fsecret%2Fa.png','P','ok');
 d.recordThumbnailRead('/api/dataset/thumbnail/b.png?file_path=%2Fsecret%2Fb.png','P','failed','HTTP 401');
 d.recordThumbnailRead('/api/dataset/thumbnail/c.png?file_path=%2Fsecret%2Fc.png','Q','stale','바뀜');
 stop();const view=d.thumbnailDiagnostics();
 assert.deepEqual(view.counts,{ok:1,failed:1,stale:1,aborted:0});assert.equal(heard,3);
 assert.deepEqual(view.records.map(row=>[row.file,row.project,row.outcome,row.detail]),[['c.png','Q','stale','바뀜'],['b.png','P','failed','HTTP 401']]);
 assert.ok(!JSON.stringify(view).includes('secret'),'local paths never enter the record');
});
test('the gallery read records every ending and the header shows it under 기술 상세',()=>{
 const thumb=fs.readFileSync(path.join(__dirname,'..','components','common','ScopedDatasetThumbnail.tsx'),'utf8');
 for(const outcome of ["'ok'","'stale'","'aborted'"])assert.ok(thumb.includes(`recordThumbnailRead(options.path, project, ${outcome}`),outcome);
 assert.match(thumb,/current\(\) \? 'failed' : 'stale'/);
 assert.match(fs.readFileSync(path.join(__dirname,'..','components','wizard','WizardHeader.tsx'),'utf8'),/<ThumbnailDiagnostics \/>/);
});
