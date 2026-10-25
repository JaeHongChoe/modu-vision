const test = require('node:test'), assert = require('node:assert/strict'), fs = require('node:fs'), path = require('node:path'), Module = require('node:module'), ts = require('typescript');

test('messages and late callbacks from a retired scope stay retired after A to B to A', () => {
  let cursor = 0; const slots = [];
  const react = {
    useState(initial) {const i=cursor++;if (!(i in slots)) slots[i]=initial;return [slots[i], value=>{slots[i]=value;}];},
    useRef(initial) {const i=cursor++;return slots[i]??(slots[i]={current:initial});},
  };
  const file=path.join(__dirname,'useScopedError.ts'), m=new Module(file,module);m.require=name=>name==='react'?react:require(name);
  m._compile(ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,file);
  const render=scope=>{cursor=0;return m.exports.useScopedError(scope);};
  const [,oldA]=render('A');oldA('처음 A의 오류');assert.equal(render('A')[0],'처음 A의 오류');
  const [,oldB]=render('B');assert.equal(render('B')[0],null);
  const [,newA]=render('A');assert.equal(render('A')[0],null);
  oldA('늦게 온 A 오류');assert.equal(render('A')[0],null);
  oldB('늦게 온 B 오류');assert.equal(render('A')[0],null);
  newA('현재 오류');assert.equal(render('A')[0],'현재 오류');
});
