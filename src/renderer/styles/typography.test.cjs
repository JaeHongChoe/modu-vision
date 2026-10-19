// The beginner floor: no text renders below 12px, secondary text keeps AA contrast, the document is Korean and the
// font stack names Korean faces. These are guards on the stylesheet, not on every component.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),test=require('node:test');
const css=fs.readFileSync(path.resolve(__dirname,'../index.css'),'utf8');
const html=fs.readFileSync(path.resolve(__dirname,'../../../index.html'),'utf8');
const tokens=fs.readFileSync(path.resolve(__dirname,'design-tokens.css'),'utf8');

const lum=hex=>{const c=hex.replace('#','');const [r,g,b]=[0,2,4].map(i=>parseInt(c.slice(i,i+2),16)/255).map(v=>v<=0.03928?v/12.92:((v+0.055)/1.055)**2.4);return 0.2126*r+0.7152*g+0.0722*b;};
const contrast=(a,b)=>{const [x,y]=[lum(a),lum(b)];return (Math.max(x,y)+0.05)/(Math.min(x,y)+0.05);};

test('arbitrary 7-11px text classes are raised to the 12px floor',()=>{
 for(const size of [7,8,9,10,11])assert.match(css,new RegExp(`\\.text-\\\\\\[${size}px\\\\\\][^{]*\\{[^}]*font-size:\\s*0?\\.75rem`),`text-[${size}px] is not raised`);
 assert.match(css,/\.text-xs\s*\{[^}]*font-size:\s*0?\.8125rem/);
});

test('secondary slate-500 text is lifted to AA contrast on every surface',()=>{
 const match=css.match(/\.text-slate-500\s*\{[^}]*color:\s*(#[0-9a-fA-F]{6})/);
 assert.ok(match,'text-slate-500 override missing');
 for(const surface of ['#0B0E14','#131822','#1A212E'])assert.ok(contrast(match[1],surface)>=4.5,`${match[1]} on ${surface} is ${contrast(match[1],surface).toFixed(2)}:1`);
});

test('the document is Korean and the font stack names Korean faces',()=>{
 assert.match(html,/<html[^>]*lang="ko"/);
 assert.match(css,/font-family:[^;]*(Pretendard|Apple SD Gothic Neo|Malgun Gothic|Noto Sans KR)/);
});

test('the bundled Korean font ships with its licence and is declared with font-display optional (no swap after first paint)',()=>{
 const fonts=path.resolve(__dirname,'../../../public/fonts');
 assert.ok(fs.existsSync(path.join(fonts,'PretendardStdVariable.woff2')),'font file missing');
 assert.match(fs.readFileSync(path.join(fonts,'PRETENDARD-LICENSE.txt'),'utf8'),/SIL Open Font License, Version 1\.1/);
 assert.match(css,/@font-face\s*\{[^}]*Pretendard Variable[^}]*font-display:\s*optional[^}]*PretendardStdVariable\.woff2/s);
});

test('workspace tokens keep a 14px body and 36px controls',()=>{
 assert.match(tokens,/--workspace-font-body:\s*\.875rem/);
 assert.match(tokens,/\.workspace-button\s*\{[^}]*min-height:\s*2\.25rem/);
});

test('the Tailwind sans stack starts with the bundled Korean face, so font-sans does not override it',()=>{
 const config=fs.readFileSync(path.resolve(__dirname,'..','..','..','tailwind.config.js'),'utf8');
 assert.match(config,/sans:\s*\[\s*'Pretendard Variable',\s*'Pretendard',/);
});
