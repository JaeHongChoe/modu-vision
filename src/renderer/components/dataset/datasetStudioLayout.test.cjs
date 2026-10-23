const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const postcss=require('postcss'),tailwind=require('tailwindcss');

// The step-1 sidebar and the gallery share one flex row. `w-84` is not on this Tailwind 3 scale, so it emitted no rule:
// the sidebar grew to its longest unwrapped line and the gallery beside it was left zero pixels wide.
test('the step-1 sidebar keeps a fixed width that Tailwind emits and never shrinks, so the gallery keeps its room',async()=>{
 const studio=fs.readFileSync(path.join(__dirname,'DatasetStudio.tsx'),'utf8');
 const aside=studio.match(/Main Studio Body[\s\S]*?<aside (?:data-surface="page" )?className="([^"]+)"/);assert.ok(aside,'the step-1 sidebar is found');
 const classes=aside[1].split(/\s+/).filter(Boolean);
 const config=require(path.resolve(__dirname,'../../../../tailwind.config.js'));
 const {css}=await postcss([tailwind({...config,content:[{raw:`<aside class="${aside[1]}"></aside>`,extension:'html'}]})]).process('@tailwind utilities;',{from:undefined});
 const emitted=new Set([...css.matchAll(/^\.((?:\\.|[^\s{>:\\])+)/gm)].map(match=>match[1].replace(/\\(.)/g,'$1')));
 assert.deepEqual(classes.filter(name=>!emitted.has(name)),[],'every sidebar class has a CSS rule');
 assert.ok(classes.some(name=>/^w-(\d+|\[\d+(\.\d+)?(rem|px)\])$/.test(name)),'the sidebar has a fixed width');
 assert.ok(classes.includes('shrink-0'),'the sidebar width never shrinks');
});

// On a 1024x768 screen the panels above the toolbar took the whole step and this body row shrank to 0 px (its
// `overflow-hidden` lets it), so the sidebar showed only its padding. The fix measures the row and folds the panels
// (datasetLayout.ts); the row's own box stays as it is: a `min-h-[…]` under the step root's `overflow-hidden` would
// keep the panels and clip the gallery's bottom rows instead, which the lazy loader can never reach.
test('the step-1 body row keeps its flex box, is measured by the focus view and takes no minimum height',()=>{
 const studio=fs.readFileSync(path.join(__dirname,'DatasetStudio.tsx'),'utf8');
 const body=studio.match(/Main Studio Body[^\n]*\n\s*<div ref=\{bodyRef\} className="([^"]+)"([^>]*)>/);assert.ok(body,'the measured body row follows the comment');
 const classes=body[1].split(/\s+/).filter(Boolean);
 for(const name of ['flex-1','flex','overflow-hidden'])assert.ok(classes.includes(name),`the body row keeps ${name}`);
 assert.ok(/\bdata-dataset-work-area\b/.test(body[2]),'the body row is the measured work area');
 assert.deepEqual(classes.filter(name=>name.startsWith('min-h-[')),[],'no pixel minimum: it would clip the gallery, not restore it');
 assert.ok(!/<aside className="[^"]*min-h-\[/.test(studio.slice(body.index)),'the sidebar takes no pixel minimum either');
});

// The full view keeps every step-1 panel open unless the viewer presses 집중 보기: the one-time fold of a body measured
// under UNUSABLE_BODY_HEIGHT is the simple view's (a 1440x900 full view folded the data readiness panel by itself).
test('only the simple view folds the step-1 panels by itself; the full view waits for the press',()=>{
 const studio=fs.readFileSync(path.join(__dirname,'DatasetStudio.tsx'),'utf8');
 assert.match(studio,/const full = useProjectStore\(state => state\.viewMode\) === 'full';/);
 assert.match(studio,/const focus = focusChoice \?\? \(autoFolded && !full\);/);
 assert.match(studio,/const foldStatus = focusChoice === null && autoFolded && !full \? AUTO_FOLD_STATUS : focusStatus\(focus\);/);
});

// 5.4-3: in 모두 보기 at 1024px the step-1 toolbar's left group was wider than the window and pushed 집중 보기 and the split
// counts past the edge. It now scrolls sideways there, its buttons keeping one line (squeezed, 합성 데이터 생성기 wrapped
// to three lines and the row grew to 105 px). The fold/narrow hints start at width 0 and take only the leftover room
// (at their text width they pushed 데이터 버전 out of view at 1440x900); 집중 보기 and the counts never shrink; the
// simple view keeps its menu.
test('the step-1 toolbar keeps its right group on screen in the full view by letting the left group scroll',async()=>{
 const studio=fs.readFileSync(path.join(__dirname,'DatasetStudio.tsx'),'utf8');
 const toolbar=studio.slice(studio.indexOf('Top Action Toolbar'));
 assert.match(toolbar,/<div className="p-3 bg-\[#131822\] border-b border-\[#2B3547\] flex items-center justify-between gap-3">/);
 assert.match(toolbar,/<div className=\{`flex items-center space-x-3\$\{full \? ' min-w-0 overflow-x-auto whitespace-nowrap' : ''\}`\}>/);
 assert.match(toolbar,/<div className="flex shrink-0 grow items-center justify-end gap-2">\s*\{focus && <span title=\{foldStatus\}[^\n]*\n\s*\{narrowNotice && <span id=\{NARROW_NOTICE_ID\}[^\n]*\n\s*<button type="button" aria-pressed=\{focus\}/);
 assert.match(toolbar,/<span title=\{foldStatus\} className="w-0 min-w-0 max-w-sm grow truncate text-right /);
 assert.match(toolbar,/role="status" title=\{narrowNotice\} className="w-0 min-w-0 max-w-xs grow truncate text-right /);
 assert.match(toolbar,/aria-pressed=\{focus\} onClick=\{toggleFocus\}[\s\S]{0,300}className=\{`shrink-0 rounded border/);
 assert.match(toolbar,/\$\{simpleView \? 'hidden' : 'flex'\} shrink-0 items-center space-x-3/);
 const config=require(path.resolve(__dirname,'../../../../tailwind.config.js'));
 const {css}=await postcss([tailwind({...config,content:[{raw:'<div class="gap-3 min-w-0 overflow-x-auto whitespace-nowrap shrink-0 grow w-0 justify-end text-right"></div>',extension:'html'}]})]).process('@tailwind utilities;',{from:undefined});
 for(const name of ['gap-3','min-w-0','overflow-x-auto','whitespace-nowrap','shrink-0','grow','w-0','justify-end','text-right'])assert.ok(css.includes('.'+name+' '),`${name} is emitted`);
});
