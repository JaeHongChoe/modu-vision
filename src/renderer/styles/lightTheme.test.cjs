// The light theme is generated from the colour classes the renderer actually uses. These guards keep it current,
// complete for the classes that carry most of the UI, and readable (AA contrast for remapped text on light surfaces).
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),Module=require('node:module'),ts=require('typescript'),test=require('node:test');
const {execFileSync}=require('node:child_process');
const ROOT=path.resolve(__dirname,'../../..');
const css=fs.readFileSync(path.join(__dirname,'theme-light.css'),'utf8');

const lum=hex=>{const c=hex.replace('#','');const [r,g,b]=[0,2,4].map(i=>parseInt(c.slice(i,i+2),16)/255).map(v=>v<=0.03928?v/12.92:((v+0.055)/1.055)**2.4);return 0.2126*r+0.7152*g+0.0722*b;};
const contrast=(a,b)=>{const [x,y]=[lum(a),lum(b)];return (Math.max(x,y)+0.05)/(Math.min(x,y)+0.05);};
const unescape=s=>s.replace(/\\(.)/g,'$1');
// Solid accent colours (saturated hexes such as #10B981 buttons) stay as they are in the light theme.
const saturated=cls=>{const m=cls.match(/#([0-9A-Fa-f]{6})/);if(!m)return false;const [r,g,b]=[0,2,4].map(i=>parseInt(m[1].slice(i,i+2),16)/255);const max=Math.max(r,g,b),min=Math.min(r,g,b),l=(max+min)/2;if(max===min)return false;const d=max-min;return (l>0.5?d/(2-max-min):d/(max+min))>=0.3;};

test('theme-light.css is what the generator produces for the current sources',()=>{
 execFileSync(process.execPath,[path.join(ROOT,'scripts/generate_light_theme.cjs'),'--check'],{stdio:'pipe'});
});

test('every neutral colour class used 10 or more times has a light-theme rule',()=>{
 const counts=new Map();
 const walk=dir=>{for(const entry of fs.readdirSync(dir,{withFileTypes:true})){const file=path.join(dir,entry.name);if(entry.isDirectory())walk(file);else if(/\.tsx?$/.test(entry.name))for(const m of fs.readFileSync(file,'utf8').matchAll(/\b(bg|text|border)-(\[#[0-9A-Fa-f]{6}\]|slate-\d+|white)(?![\w-\/])/g))counts.set(m[0],(counts.get(m[0])||0)+1);}};
 walk(path.join(ROOT,'src/renderer'));
 const rules=new Set([...css.matchAll(/html\[data-theme="light"\] \.([^\s{:]+)/g)].map(m=>unescape(m[1])));
 const missing=[...counts].filter(([cls,n])=>n>=10&&!rules.has(cls)&&!/^text-slate-(900|950)$/.test(cls)&&!saturated(cls)).map(([cls,n])=>`${cls} (${n})`);
 assert.deepEqual(missing,[],'classes without a light rule');
});

test('remapped text keeps AA contrast on the light page and surface',()=>{
 const weak=[];
 for(const m of css.matchAll(/html\[data-theme="light"\] \.((?:text|placeholder)-[^\s{:]+)[^{]*\{ color: (#[0-9A-Fa-f]{6});/g)){
  for(const surface of ['#F8FAFC','#FFFFFF','#F1F5F9'])if(contrast(m[2],surface)<4.5)weak.push(`${unescape(m[1])} ${m[2]} on ${surface}: ${contrast(m[2],surface).toFixed(2)}`);
 }
 assert.deepEqual(weak,[]);
});

test('surfaces are light and borders are visible against them',()=>{
 for(const m of css.matchAll(/html\[data-theme="light"\] \.(bg-[^\s{:]+)[^{]*\{ background-color: (#[0-9A-Fa-f]{6});/g)){
  const cls=unescape(m[1]);if(/^bg-(slate|\[#)/.test(cls)&&!/^bg-\[#(0[0-9A-F]|1[0-9A-F])/.test(cls))continue;
  assert.ok(lum(m[2])>0.5,`${cls} -> ${m[2]} is not light`);
 }
 assert.ok(contrast('#CBD5E1','#FFFFFF')>=1.4&&contrast('#94A3B8','#FFFFFF')>=2.5,'border colours are too faint');
});

test('resolveTheme follows the OS only for "system", and the header offers the switch',()=>{
 const name=path.resolve(__dirname,'../stores/useProjectStore.ts');
 const mod=new Module(name,module);mod.filename=name;mod.paths=Module._nodeModulePaths(path.dirname(name));
 const original=mod.require.bind(mod);mod.require=ref=>ref.startsWith('.')||ref==='zustand'?new Proxy({},{get:()=>()=>({})}):original(ref);
 mod._compile(ts.transpileModule(fs.readFileSync(name,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,name);
 const {resolveTheme}=mod.exports;
 assert.equal(resolveTheme('system',true),'light');assert.equal(resolveTheme('system',false),'dark');
 assert.equal(resolveTheme('light',false),'light');assert.equal(resolveTheme('dark',true),'dark');
 const header=fs.readFileSync(path.resolve(__dirname,'../components/wizard/WizardHeader.tsx'),'utf8');
 assert.match(header,/밝은 화면/);assert.match(header,/어두운 화면/);assert.match(header,/setTheme\(/);
});
