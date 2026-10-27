const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),Module=require('node:module'),ts=require('typescript');
function load(){const file=path.join(__dirname,'connection.ts'),m=new Module(file,module);m.filename=file;m.paths=Module._nodeModulePaths(__dirname);m._compile(ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,file);return m.exports;}
const config=()=>({active:'chatgpt',providers:[{provider:'chatgpt',ready:true,model:'chosen-model',connectionAuthority:'live-a'}],chatgpt:{active:'account-a',authority:'generation-a',accounts:[{recordId:'account-a',signedIn:true,planUsageEnabled:true,usagePaused:false,model:'chosen-model'}]}});
test('task authority follows the actual connected ChatGPT account/model without credentials',()=>{
 const m=load(),c=config();assert.equal(m.taskConnectionReady(c),true);const key=m.taskConnectionKey(c);
 c.chatgpt.authority='fresh-session';assert.equal(m.taskConnectionKey(c),key);assert.notEqual(m.taskConnectionGeneration(c),m.taskConnectionGeneration(config()));
 c.chatgpt.active='another-account';assert.equal(m.taskConnectionReady(c),false);assert.notEqual(m.taskConnectionKey(c),key);
});
test('paused, disconnected, model-mismatched and legacy API settings cannot execute task plans',()=>{
 const m=load();for(const mutate of [c=>c.chatgpt.accounts[0].usagePaused=true,c=>c.chatgpt.accounts[0].signedIn=false,c=>c.providers[0].model='another-model',c=>c.active='openai',c=>c.chatgpt.accounts[0].planUsageEnabled=false]){const c=config();mutate(c);assert.equal(m.taskConnectionReady(c),false);}
 assert.equal(m.taskConnectionReady(null),false);
});
