// Declared UI action inventory, not executed or accepted behavior evidence.
const fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto'),ts=require('typescript');
const root=path.resolve(__dirname,'../..');
const registry=JSON.parse(fs.readFileSync(path.join(root,'docs/service-upgrade-program.json'),'utf8'));
const scenarios=['success','empty','invalid','error','cancel','reopen','handoff'];
const cache=new Map();
function actions(relative){
 if(cache.has(relative))return cache.get(relative);
 const file=path.join(root,relative);
 if(!relative.endsWith('.tsx'))return [];
 if(!fs.existsSync(file)||fs.lstatSync(file).isSymbolicLink()||!fs.lstatSync(file).isFile())throw Error('Missing declared UI source: '+relative);
 const raw=fs.readFileSync(file,'utf8'),sha=crypto.createHash('sha256').update(raw).digest('hex'),source=ts.createSourceFile(relative,raw,ts.ScriptTarget.Latest,true,ts.ScriptKind.TSX),result=[];
 function visit(node){
  if(ts.isJsxOpeningElement(node)||ts.isJsxSelfClosingElement(node)){
   const tag=node.tagName.getText(source),attrs=node.attributes.properties.filter(ts.isJsxAttribute);
   const events=attrs.map(a=>a.name.getText(source)).filter(name=>['onClick','onSubmit','onKeyDown','onChange'].includes(name));
   const role=attrs.find(a=>a.name.getText(source)==='role')?.initializer;
   if(events.length||tag==='button'||role?.text==='menuitem'){
    const aria=attrs.find(a=>a.name.getText(source)==='aria-label')?.initializer;
    const title=attrs.find(a=>a.name.getText(source)==='title')?.initializer;
    const children=ts.isJsxElement(node.parent)?node.parent.children:[];
    const text=children.filter(ts.isJsxText).map(n=>n.text.trim()).filter(Boolean).join(' ').slice(0,160);
    const staticName=aria&&ts.isStringLiteral(aria)?aria.text:title&&ts.isStringLiteral(title)?title.text:text;
    result.push({source:relative,source_sha256:sha,line:source.getLineAndCharacterOfPosition(node.getStart(source)).line+1,
      element:tag,events,label:staticName||null,dynamic_label:!staticName||children.some(ts.isJsxExpression),
      execution_state:'pending',scenarios:Object.fromEntries(scenarios.map(s=>[s,{state:'pending'}]))});
   }
  }
  ts.forEachChild(node,visit);
 }
 visit(source);cache.set(relative,result);return result;
}
const ids=registry.legacy_coverage.map(r=>r.legacy_id);
if(ids.length!==156||new Set(ids).size!==156)throw Error('Preserved 156-ID scope differs');
const report={schema_version:1,kind:'declared_ui_actions_not_execution_evidence',
 scope:'All F001-F123/U001-U033 mappings; literal events are candidates, not a complete runtime menu/shortcut trace.',
 records:registry.legacy_coverage.map(row=>({id:row.legacy_id,title:row.title,owners:row.service_requirements,
   declared_ui_actions:row.source_files.flatMap(actions),remaining_dynamic_actions:'Runtime-generated menus/shortcuts and helper-owned actions require an executed trace',accepted:false}))};
const output=process.argv[2];
if(!output)throw Error('Provide a new output file');
fs.writeFileSync(path.resolve(output),JSON.stringify(report,null,2)+'\n',{flag:'wx'});
console.log(JSON.stringify({records:report.records.length,unique_ui_sources:cache.size,unique_actions:[...cache.values()].reduce((n,a)=>n+a.length,0),accepted:0}));
