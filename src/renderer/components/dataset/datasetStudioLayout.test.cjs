const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const postcss=require('postcss'),tailwind=require('tailwindcss');

// The step-1 sidebar and the gallery share one flex row. `w-84` is not on this Tailwind 3 scale, so it emitted no rule:
// the sidebar grew to its longest unwrapped line and the gallery beside it was left zero pixels wide.
test('the step-1 sidebar keeps a fixed width that Tailwind emits and never shrinks, so the gallery keeps its room',async()=>{
 const studio=fs.readFileSync(path.join(__dirname,'DatasetStudio.tsx'),'utf8');
 const aside=studio.match(/Main Studio Body[\s\S]*?<aside className="([^"]+)"/);assert.ok(aside,'the step-1 sidebar is found');
 const classes=aside[1].split(/\s+/).filter(Boolean);
 const config=require(path.resolve(__dirname,'../../../../tailwind.config.js'));
 const {css}=await postcss([tailwind({...config,content:[{raw:`<aside class="${aside[1]}"></aside>`,extension:'html'}]})]).process('@tailwind utilities;',{from:undefined});
 const emitted=new Set([...css.matchAll(/^\.((?:\\.|[^\s{>:\\])+)/gm)].map(match=>match[1].replace(/\\(.)/g,'$1')));
 assert.deepEqual(classes.filter(name=>!emitted.has(name)),[],'every sidebar class has a CSS rule');
 assert.ok(classes.some(name=>/^w-(\d+|\[\d+(\.\d+)?(rem|px)\])$/.test(name)),'the sidebar has a fixed width');
 assert.ok(classes.includes('shrink-0'),'the sidebar width never shrinks');
});
