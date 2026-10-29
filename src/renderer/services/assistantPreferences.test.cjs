const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),Module=require('node:module'),ts=require('typescript');
function load(){const file=path.join(__dirname,'assistantPreferences.ts'),m=new Module(file,module);m.filename=file;m.paths=Module._nodeModulePaths(path.dirname(file));m._compile(ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,file);return m.exports;}
test('reasoning preferences are isolated by account/model and retain no content',()=>{
 const previous=Object.getOwnPropertyDescriptor(globalThis,'localStorage'),entries=new Map();Object.defineProperty(globalThis,'localStorage',{configurable:true,value:{getItem:k=>entries.get(k)??null,setItem:(k,v)=>entries.set(k,v),removeItem:k=>entries.delete(k)}});
 try {const {readAssistantEffort,saveAssistantEffort}=load(),a=JSON.stringify(['chatgpt','opaque-a','model-a']),b=JSON.stringify(['chatgpt','opaque-b','model-a']),c=JSON.stringify(['chatgpt','opaque-a','model-b']);
 saveAssistantEffort(a,'high');assert.equal(readAssistantEffort(a),'high');assert.equal(readAssistantEffort(b),undefined);assert.equal(readAssistantEffort(c),undefined);
 saveAssistantEffort(b,'low');assert.equal(readAssistantEffort(a),'high');assert.equal(readAssistantEffort(b),'low');assert.deepEqual([...entries.values()],['high','low']);
 saveAssistantEffort(c,'arbitrary-content');assert.equal(readAssistantEffort(c),undefined);assert.ok(![...entries.values()].includes('arbitrary-content'));
 saveAssistantEffort(a,undefined);assert.equal(readAssistantEffort(a),undefined);assert.equal(readAssistantEffort(b),'low');
 entries.set('vision.assistant.effort.v1.'+encodeURIComponent(c),'unsupported');assert.equal(readAssistantEffort(c),undefined);
 } finally {if(previous)Object.defineProperty(globalThis,'localStorage',previous);else delete globalThis.localStorage;}
});
test('unavailable browser storage leaves current reasoning selection usable',()=>{
 const previous=Object.getOwnPropertyDescriptor(globalThis,'localStorage');Object.defineProperty(globalThis,'localStorage',{configurable:true,get(){throw Error('storage unavailable');}});
 try {const {readAssistantEffort,saveAssistantEffort}=load();assert.equal(readAssistantEffort('scope'),undefined);assert.doesNotThrow(()=>saveAssistantEffort('scope','high'));}finally{if(previous)Object.defineProperty(globalThis,'localStorage',previous);else delete globalThis.localStorage;}
});
