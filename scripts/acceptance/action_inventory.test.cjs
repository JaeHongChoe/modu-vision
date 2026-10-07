const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),os=require('node:os'),path=require('node:path');
const {buildInventory}=require('./action_inventory.cjs');
function fixture(t){
 const root=fs.mkdtempSync(path.join(os.tmpdir(),'modu-action-inventory-'));t.after(()=>fs.rmSync(root,{recursive:true,force:true}));
 fs.mkdirSync(path.join(root,'src/renderer'),{recursive:true});
 const program={legacy_coverage:[...Array.from({length:123},(_,i)=>`F${String(i+1).padStart(3,'0')}`),...Array.from({length:33},(_,i)=>`U${String(i+1).padStart(3,'0')}`)].map(id=>({legacy_id:id,title:id,service_requirements:['S7-01'],source_files:['src/renderer/App.tsx']}))};
 return {root,program,write:(name,raw)=>fs.writeFileSync(path.join(root,'src/renderer',name),raw)};
}
test('follows real local helper imports and inventories their keyboard paths without executing them',t=>{
 const f=fixture(t);f.write('App.tsx',`import {shortcut} from './shortcuts'; export function App(){return <button onKeyDown={shortcut}>Open</button>}`);
 f.write('shortcuts.ts',`export function shortcut(event: KeyboardEvent){ const key=event.key.toLowerCase(); if((event.ctrlKey||event.metaKey)&&key==='z'){event.preventDefault();return 'undo';} if(event.code==='KeyF')return 'fit'; }`);
 const r=buildInventory(f);assert.equal(r.records.length,156);assert.equal(r.sources.length,2);
 const keys=r.sources.flatMap(s=>s.keyboard_candidates).map(a=>a.key);assert.deepEqual(keys.sort(),['KeyF','z']);
 assert.equal(r.sources.flatMap(s=>s.ui_actions).length,1);assert.equal(r.records[0].keyboard_candidates.length,2);
 assert.equal(r.accepted,0);assert.ok(r.sources.flatMap(s=>s.keyboard_candidates).every(a=>a.execution_state==='pending'));
});
test('keeps generated menu labels unresolved and deduplicates sources across feature owners',t=>{
 const f=fixture(t);f.write('App.tsx',`export function App(){return <div>{items.map(item=><button role="menuitem" onClick={()=>choose(item.id)}>{item.label}</button>)}</div>}`);
 const r=buildInventory(f);assert.equal(r.sources.length,1);const actions=r.sources[0].ui_actions;
 assert.equal(actions.length,1);assert.equal(actions[0].dynamic_label,true);assert.equal(actions[0].generated,true);
 assert.equal(actions[0].label,null);assert.equal(r.records.length,156);assert.equal(r.unique_actions,1);
 assert.ok(r.records.every(row=>row.accepted===false));
});
test('input fields, lazy local imports and keyboard aliases are inventoried; test modules are excluded',t=>{
 const f=fixture(t);f.write('App.tsx',`const dialog=()=>import('./dialog');export function App(){return <input aria-label="Name"/>}`);
 f.write('dialog.tsx',`import './ignored.test';export function Dialog(){return <textarea aria-label="Note" onKeyDown={(e: KeyboardEvent)=>{if(e.key==='Escape')e.preventDefault()}}/>}`);
 f.write('ignored.test.ts',`throw new Error('Never evaluate test code');`);
 const r=buildInventory(f);assert.equal(r.sources.length,2);assert.equal(r.unique_actions,2);
 assert.deepEqual(r.sources.flatMap(s=>s.keyboard_candidates).map(a=>a.key),['Escape']);
});
test('refuses missing or linked declared UI code instead of silently producing a partial inventory',t=>{
 const f=fixture(t);assert.throws(()=>buildInventory(f),/Missing/);
 const elsewhere=path.join(f.root,'elsewhere.tsx');fs.writeFileSync(elsewhere,'export const x=1;');
 fs.symlinkSync(elsewhere,path.join(f.root,'src/renderer/App.tsx'));
 assert.throws(()=>buildInventory(f),/linked|Missing/);
});
test('a missing helper cannot be silently omitted from the source graph',t=>{
 const f=fixture(t);f.write('App.tsx',`import './missing-helper';export function App(){return <button>Open</button>}`);
 assert.throws(()=>buildInventory(f),/Missing local UI dependency/);
});
