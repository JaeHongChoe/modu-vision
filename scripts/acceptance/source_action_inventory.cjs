// Source declarations only: no source is imported, executed or accepted.
const fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto'),ts=require('typescript');
const hash=raw=>crypto.createHash('sha256').update(raw).digest('hex');
const scenarios=['success','empty','invalid','error','cancel','reopen','handoff'];
const eligible=relative=>relative.startsWith('src/')&&/\.(tsx?|mts|cts)$/.test(relative)&&!/(?:\.test\.|\.spec\.|\.d\.ts$)/.test(relative);

function buildInventory({root,program}){
 root=path.resolve(root);
 const expected=[...Array.from({length:123},(_,i)=>`F${String(i+1).padStart(3,'0')}`),...Array.from({length:33},(_,i)=>`U${String(i+1).padStart(3,'0')}`)];
 const ids=program.legacy_coverage.map(r=>r.legacy_id);
 if(ids.length!==156||new Set(ids).size!==156||ids.some(id=>!expected.includes(id)))throw Error('Preserved 156-ID scope differs');
 const cache=new Map();
 function regular(relative){
  const file=path.resolve(root,relative);
  if(!file.startsWith(root+path.sep)||relative.includes('\\')||relative.split('/').includes('..'))throw Error('UI source leaves repository: '+relative);
  for(let current=file;current!==root;current=path.dirname(current))if(fs.existsSync(current)&&fs.lstatSync(current).isSymbolicLink())throw Error('UI source is linked: '+relative);
  if(!fs.existsSync(file)||!fs.lstatSync(file).isFile())throw Error('Missing declared UI source: '+relative);
  return file;
 }
 function dependency(relative,value){
  if(!value.startsWith('.'))return null;
  const base=path.resolve(root,path.dirname(relative),value);
  for(const candidate of [base,base+'.ts',base+'.tsx',base+'.d.ts',path.join(base,'index.ts'),path.join(base,'index.tsx'),path.join(base,'index.d.ts')]){
   const rel=path.relative(root,candidate).split(path.sep).join('/');
   if(fs.existsSync(candidate)&&fs.lstatSync(candidate).isFile()){
    if(eligible(rel))return rel;
    return null; // Existing asset/test code is deliberately outside this graph.
   }
  }
  if(!path.extname(value)||/\.(tsx?|mts|cts)$/.test(value))throw Error('Missing local UI dependency: '+relative+' -> '+value);
  return null;
 }
 function parse(relative){
  if(!eligible(relative))return null;
  if(cache.has(relative))return cache.get(relative);
  if(cache.size>=4096)throw Error('UI source graph exceeds 4096 files');
  const raw=fs.readFileSync(regular(relative),'utf8'),sha=hash(raw),source=ts.createSourceFile(relative,raw,ts.ScriptTarget.Latest,true,relative.endsWith('.tsx')?ts.ScriptKind.TSX:ts.ScriptKind.TS);
  const result={source:relative,source_sha256:sha,imports:[],ui_actions:[],keyboard_candidates:[]};cache.set(relative,result);
  const aliases=new Set(),maps=new Map(),keyboardSource=/\bKeyboardEvent\b|onKeyDown|addEventListener\s*\(\s*['"]keydown|Shortcut/.test(raw);
  const isKey=node=>!!node&&((ts.isPropertyAccessExpression(node)&&['key','code'].includes(node.name.text)&&/^(event|e|evt)$/.test(node.expression.getText(source)))||(ts.isIdentifier(node)&&aliases.has(node.text)));
  function prelim(node){
   if(ts.isVariableDeclaration(node)&&ts.isIdentifier(node.name)&&node.initializer){
    if(/^(event|e|evt)\.(key|code)(?:\.|$)/.test(node.initializer.getText(source)))aliases.add(node.name.text);
    if(ts.isObjectLiteralExpression(node.initializer))maps.set(node.name.text,node.initializer);
   }
   if((ts.isImportDeclaration(node)||ts.isExportDeclaration(node))&&node.moduleSpecifier&&ts.isStringLiteral(node.moduleSpecifier)){
    const rel=dependency(relative,node.moduleSpecifier.text);if(rel)result.imports.push(rel);
   }
   if(ts.isCallExpression(node)&&node.expression.kind===ts.SyntaxKind.ImportKeyword&&node.arguments[0]&&ts.isStringLiteral(node.arguments[0])){
    const rel=dependency(relative,node.arguments[0].text);if(rel)result.imports.push(rel);
   }
   ts.forEachChild(node,prelim);
  }
  prelim(source);result.imports=[...new Set(result.imports)].sort();
  function base(node,kind){return {source:relative,source_sha256:sha,line:source.getLineAndCharacterOfPosition(node.getStart(source)).line+1,
    id:`${relative}:${kind}:${node.getStart(source)}`,execution_state:'pending',scenarios:Object.fromEntries(scenarios.map(s=>[s,{state:'pending'}]))};}
  const keysSeen=new Set();
  function keyboard(node,key){
   if(!keyboardSource)return;const id=node.getStart(source)+':'+key;if(keysSeen.has(id))return;keysSeen.add(id);
   let condition=node;for(let parent=node.parent;parent&&!ts.isSourceFile(parent);parent=parent.parent)if(ts.isIfStatement(parent)){condition=parent.expression;break;}
   const text=condition.getText(source);result.keyboard_candidates.push({...base(node,'key'),key,condition:text.slice(0,1000),condition_sha256:hash(text),
    modifiers:['ctrlKey','metaKey','altKey','shiftKey'].filter(name=>text.includes('.'+name)),requires_runtime_trace:true});
  }
  function visit(node){
   if(ts.isJsxOpeningElement(node)||ts.isJsxSelfClosingElement(node)){
    const tag=node.tagName.getText(source),attrs=node.attributes.properties.filter(ts.isJsxAttribute),attr=name=>attrs.find(a=>a.name.getText(source)===name)?.initializer;
    const events=attrs.map(a=>a.name.getText(source)).filter(name=>['onClick','onSubmit','onKeyDown','onChange','onPointerDown','onDoubleClick'].includes(name));
    if(events.length||['button','input','select','textarea'].includes(tag)||attr('role')?.text==='menuitem'){
     const children=ts.isJsxElement(node.parent)?node.parent.children:[],aria=attr('aria-label'),title=attr('title');
     const text=children.filter(ts.isJsxText).map(n=>n.text.trim()).filter(Boolean).join(' ').slice(0,160);
     const label=aria&&ts.isStringLiteral(aria)?aria.text:title&&ts.isStringLiteral(title)?title.text:text||null;
     let generated=false;for(let parent=node.parent;parent&&!ts.isSourceFile(parent);parent=parent.parent){if(ts.isCallExpression(parent)&&ts.isPropertyAccessExpression(parent.expression)&&parent.expression.name.text==='map'){generated=true;break;}}
     result.ui_actions.push({...base(node,'ui'),element:tag,events,label,dynamic_label:!label||children.some(ts.isJsxExpression),generated,requires_runtime_trace:generated||!label});
    }
   }
   if(ts.isBinaryExpression(node)&&[ts.SyntaxKind.EqualsEqualsEqualsToken,ts.SyntaxKind.EqualsEqualsToken].includes(node.operatorToken.kind)){
    if(isKey(node.left)&&ts.isStringLiteral(node.right))keyboard(node,node.right.text);if(isKey(node.right)&&ts.isStringLiteral(node.left))keyboard(node,node.left.text);
   }
   if(ts.isSwitchStatement(node)&&isKey(node.expression))for(const clause of node.caseBlock.clauses)if(ts.isCaseClause(clause)&&ts.isStringLiteral(clause.expression))keyboard(clause,clause.expression.text);
   if(ts.isCallExpression(node)&&ts.isPropertyAccessExpression(node.expression)&&node.expression.name.text==='includes'&&isKey(node.arguments[0])&&ts.isArrayLiteralExpression(node.expression.expression))for(const key of node.expression.expression.elements)if(ts.isStringLiteral(key))keyboard(key,key.text);
   if(ts.isElementAccessExpression(node)&&isKey(node.argumentExpression)&&ts.isIdentifier(node.expression)&&maps.has(node.expression.text))for(const property of maps.get(node.expression.text).properties)if(ts.isPropertyAssignment(property)&&ts.isStringLiteral(property.name))keyboard(property,property.name.text);
   ts.forEachChild(node,visit);
  }
  visit(source);for(const imported of result.imports)parse(imported);return result;
 }
 function closure(declared){const seen=new Set();function include(relative){const value=parse(relative);if(!value||seen.has(relative))return;seen.add(relative);for(const imported of value.imports)include(imported);}for(const relative of declared)include(relative);return [...seen].sort().map(relative=>cache.get(relative));}
 const records=program.legacy_coverage.map(row=>{
  const sources=closure(row.source_files);return {id:row.legacy_id,title:row.title,owners:row.service_requirements,
   source_graph:sources.map(s=>({source:s.source,source_sha256:s.source_sha256})),declared_ui_actions:sources.flatMap(s=>s.ui_actions),keyboard_candidates:sources.flatMap(s=>s.keyboard_candidates),
   remaining_dynamic_actions:'Source declarations are pending. Generated instances, helper dispatch and OS/library menus require an executed trace.',accepted:false};
 });
 const sources=[...cache.values()].sort((a,b)=>a.source.localeCompare(b.source));
 for(const source of sources)if(hash(fs.readFileSync(regular(source.source)))!==source.source_sha256)throw Error('UI source changed during inventory: '+source.source);
 return {schema_version:1,kind:'declared_ui_actions_not_execution_evidence',
  scope:'All F001-F123/U001-U033 local source graphs; no executed or accepted behavior.',records,sources,
  unique_actions:sources.reduce((n,s)=>n+s.ui_actions.length,0),unique_keyboard_candidates:sources.reduce((n,s)=>n+s.keyboard_candidates.length,0),accepted:0};
}
module.exports={buildInventory};
