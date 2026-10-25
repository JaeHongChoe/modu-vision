// labelCategoryPalette's project classes: each one the palette lacks joins it under the lowest free id; without them the
// palette is what it was (image labels first, then the existing classes).
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),Module=require('node:module'),ts=require('typescript');
const file=path.join(__dirname,'foundationRequest.ts'),m=new Module(file,module);m.filename=file;
m._compile(ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,file);
const {labelCategoryPalette}=m.exports;
const OK={id:0,name:'OK',color:'#10b981'};

test('without project classes the palette is unchanged: image labels first, then the existing classes',()=>{
 const labels=[{id:'a',type:'bbox',label:'dent',category_id:3,color:'#123456'}];
 // An existing class whose id an image label holds moves to the lowest free id, as before.
 assert.deepEqual(labelCategoryPalette([OK,{id:3,name:'Crack',color:'#ef4444'}],labels).map(c=>[c.id,c.name]),[[3,'dent'],[0,'OK'],[1,'Crack']]);
});

test('missing project classes join under the lowest free ids, most used first, matching names case-insensitively',()=>{
 const result=labelCategoryPalette([OK,{id:2,name:'Scratch',color:'#f59e0b'}],[],[],['scratch','defect','dent']);
 assert.deepEqual(result.map(c=>[c.id,c.name,c.color]),[[0,'OK','#10b981'],[2,'Scratch','#f59e0b'],[1,'defect','#3b82f6'],[3,'dent','#ef4444']]);
});

test('ids stop at 255',()=>{
 const existing=Array.from({length:255},(_,i)=>({id:i+1,name:`c${i+1}`,color:'#000000'}));
 assert.equal(labelCategoryPalette([OK,...existing],[],[],['late']).length,256,'no id is left for another class');
});
