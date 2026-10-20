// Audit A2 / F-04: the class picker shows the class training reads, where it comes from, and changes it through the API.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),Module=require('node:module'),ts=require('typescript'),test=require('node:test');
const React=require('react');const {renderToStaticMarkup}=require('react-dom/server');
function load(file,mocks){const name=path.resolve(__dirname,file);const mod=new Module(name,module);mod.filename=name;mod.paths=Module._nodeModulePaths(path.dirname(name));
 mod.require=ref=>ref in mocks?mocks[ref]:require(ref);
 mod._compile(ts.transpileModule(fs.readFileSync(name,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.ReactJSX}}).outputText,name);return mod.exports;}
function render({task='classification',image}){
 const calls=[];const store=state=>selector=>selector(state);
 const mocks={'../../services/api':{api:{dataset:{setImageClass:async body=>{calls.push(body);return{class:body.class_name||'OK',class_source:body.class_name?'override':'folder'};}}}},
  '../../stores/useAnnotationStore':{useAnnotationStore:store({currentImage:image,applyImageClass:()=>{}})},
  '../../stores/useDatasetStore':{useDatasetStore:Object.assign(store({classes:{OK:5,NG:7}}),{getState:()=>({annotationsChanged:async()=>{}})})},
  '../../stores/useProjectStore':{useProjectStore:store({task})},
  '../../stores/workflowEvidence':{bumpWorkflowEvidence:()=>{}}};
 const {ClassificationClassPicker}=load('ClassificationClassPicker.tsx',mocks);
 return {html:renderToStaticMarkup(React.createElement(ClassificationClassPicker)),calls};
}
test('a folder class is shown as the folder class, with one button per class',()=>{
 const {html}=render({image:{file_path:'/s/OK/a.png',label:'OK',class_source:'folder',folder_label:'OK'}});
 assert.match(html,/이 이미지의 클래스/);assert.match(html,/aria-pressed="true"[^>]*>OK</);assert.match(html,/>NG</);assert.match(html,/폴더 이름 기준/);
 assert.doesNotMatch(html,/폴더 클래스로 되돌리기/);
});
test('a chosen class names the folder it overrides and offers the way back',()=>{
 const {html}=render({image:{file_path:'/s/OK/a.png',label:'NG',class_source:'override',folder_label:'OK'}});
 assert.match(html,/2단계에서 지정함 · 폴더는 OK/);assert.match(html,/폴더 클래스로 되돌리기/);assert.match(html,/aria-pressed="true"[^>]*>NG</);
});
test('nothing is shown outside classification',()=>{
 assert.equal(render({task:'detection',image:{file_path:'/s/a.png',label:'scratch'}}).html,'');
});
