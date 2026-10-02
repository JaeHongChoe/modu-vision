const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),Module=require('node:module'),ts=require('typescript');
function load(file){const name=path.join(__dirname,file),m=new Module(name,module);m.filename=name;m.paths=Module._nodeModulePaths(__dirname);m.require=ref=>ref.startsWith('.')?load(ref+'.ts'):require(ref);m._compile(ts.transpileModule(fs.readFileSync(name,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,name);return m.exports;}
const v=load('virtualWindow.ts');
test('only the visible rows and a small overscan are rendered, whatever the list size',()=>{
 assert.deepEqual(v.visibleRows({scrollTop:0,viewportHeight:400,rowHeight:100,rowCount:100000,overscan:2}),{start:0,end:9,offsetTop:0,totalHeight:10000000});
 const middle=v.visibleRows({scrollTop:50000,viewportHeight:400,rowHeight:100,rowCount:100000,overscan:2});
 assert.equal(middle.start,498);assert.equal(middle.offsetTop,49800);assert.ok(middle.end-middle.start<=10,'a hundred thousand rows render about ten');
 const last=v.visibleRows({scrollTop:9999900,viewportHeight:400,rowHeight:100,rowCount:100000});
 assert.equal(last.end,100000);
 assert.deepEqual(v.visibleRows({scrollTop:0,viewportHeight:400,rowHeight:100,rowCount:0}),{start:0,end:0,offsetTop:0,totalHeight:0});
 assert.equal(v.visibleRows({scrollTop:-30,viewportHeight:400,rowHeight:100,rowCount:5}).start,0,'elastic scrolling above the top');});
test('columns fit the width and the next page is requested near the end',()=>{
 assert.equal(v.columnsFor(700,160,12),4);assert.equal(v.columnsFor(100,160,12),1);assert.equal(v.columnsFor(0,160),1);
 assert.equal(v.nearEnd({start:0,end:9,offsetTop:0,totalHeight:0},10),true);
 assert.equal(v.nearEnd({start:0,end:9,offsetTop:0,totalHeight:0},40),false);
 assert.equal(v.nearEnd({start:0,end:0,offsetTop:0,totalHeight:0},0),true,'an empty list asks for its first page');});
