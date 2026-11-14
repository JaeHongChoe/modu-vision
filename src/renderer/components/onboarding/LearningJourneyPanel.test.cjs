const test = require('node:test'), assert = require('node:assert/strict'), fs = require('node:fs'), path = require('node:path'), Module = require('node:module'), ts = require('typescript'), React = require('react');
function fixture({oneclick, assistant, projectReady = true, hasData = true} = {}) {
  const empty = {task:null,contextMatches:true,error:null};
  const calls = [];
  const mocks = {
    react:{...React,useState:()=>[false,v=>calls.push(['dialog',v])]},
    '../../stores/useAssistantTaskStore':{useOneclickTaskProgressStore:()=>oneclick||empty,useAssistantTaskStore:()=>assistant||empty},
    '../../stores/useAssistantStore':{useAssistantStore:{getState:()=>({open:()=>calls.push(['assistant'])})}},
    '../../stores/useProjectStore':{useProjectStore:select=>select({projectDir:projectReady?'/test':null,project:{},isProjectBusy:false})},
    '../../stores/useDatasetStore':{useDatasetStore:select=>select({hasSelectedFolder:hasData,isLoading:false,importError:null})},
    '../wizard/AutoProgressDialog':{AutoProgressDialog:()=>null},
  };
  function load(file) {const m=new Module(file,module);m.filename=file;m.paths=Module._nodeModulePaths(path.dirname(file));const original=m.require.bind(m);m.require=name=>name in mocks?mocks[name]:name.startsWith('.')?load(path.resolve(path.dirname(file),name)+'.ts'):original(name);m._compile(ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.ReactJSX,esModuleInterop:true}}).outputText,file);return m.exports;}
  return {tree:load(path.join(__dirname,'LearningJourneyPanel.tsx')).LearningJourneyPanel(),calls};
}
const nodes = tree => !tree || typeof tree !== 'object' ? [] : [tree, ...[].concat(tree.props?.children||[]).flat(Infinity).flatMap(nodes)];
const text = tree => tree == null || typeof tree === 'boolean' ? '' : typeof tree === 'string'||typeof tree === 'number'?String(tree):[].concat(tree.props?.children||[]).flat(Infinity).map(text).join('');
const task = {id:'t1',goal:'학습',status:'waiting_job',updated_at:'2026-11-14T00:00:00Z',steps:[],message:'학습 실행 중',current_step_id:null};
test('empty data explains the prerequisite and cannot start a job',()=>{const f=fixture({hasData:false});assert.match(text(f.tree),/사진을 가져와/);assert.equal(nodes(f.tree).find(n=>n.type==='button').props.disabled,true);assert.deepEqual(f.calls,[]);});
test('local preparation opens existing automatic dialog without starting execution',()=>{const f=fixture();nodes(f.tree).find(n=>n.type==='button').props.onClick();assert.deepEqual(f.calls,[['dialog',true]]);assert.match(text(f.tree),/GPT 연결 없이/);});
test('assistant work opens its existing progress and never sends a provider request',()=>{const f=fixture({assistant:{task,contextMatches:true,error:null}});nodes(f.tree).find(n=>n.type==='button').props.onClick();assert.deepEqual(f.calls,[['assistant']]);assert.match(text(f.tree),/학습 실행 중/);});
test('foreign project progress is absent from the rendered current task',()=>{const f=fixture({oneclick:{task,contextMatches:false,error:null}});assert.doesNotMatch(text(f.tree),/학습 실행 중/);});
