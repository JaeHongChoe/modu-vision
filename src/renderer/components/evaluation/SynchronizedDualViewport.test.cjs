const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const Module = require('node:module');
const test = require('node:test');
const ts = require('typescript');
function harness() {
  let cursor = 0; const states = []; const refs = [];
  const react = { createElement: (type, props, ...children) => ({type, props: props || {}, children: children.flat()}),
    useState(initial) { const i = cursor++; if (!(i in states)) states[i] = initial;
      return [states[i], next => { states[i] = typeof next === 'function' ? next(states[i]) : next; }]; },
    useRef(initial) { const i = cursor++; if (!(i in states)) { states[i] = {current: initial}; refs.push(states[i]); } return states[i]; },
    useEffect() {}, useCallback(fn) { return fn; } };
  const load = file => { const m = new Module(file, module); m.filename=file; m.paths=Module._nodeModulePaths(path.dirname(file));
    m.require = name => name === 'react' ? react : name === 'lucide-react' ? new Proxy({}, {get:(_, k)=>k})
      : name.endsWith('/services/api') ? {resolveApiUrl: value => value}
      : name === './PhysicalScaleOverlay' ? {PhysicalScaleOverlay: 'Scale'}
      : name.endsWith('/utils/coordinateMath') ? load(path.resolve(path.dirname(file), name+'.ts'))
      : Module.prototype.require.call(m,name);
    m._compile(ts.transpileModule(fs.readFileSync(file,'utf8'), {compilerOptions: {jsx:ts.JsxEmit.React, module:ts.ModuleKind.CommonJS,
      target:ts.ScriptTarget.ES2022, esModuleInterop:true}}).outputText,file); return m.exports; };
  const {SynchronizedDualViewport} = load(path.join(__dirname,'SynchronizedDualViewport.tsx'));
  const render = () => { cursor=0; return SynchronizedDualViewport({task:'segmentation', prediction:{image_id:'a',thumbnail_url:'/raw'},
    confidenceThreshold:.5,onThresholdChange(){},heatmapOverlayBase64:'/heatmap',heatmapLoading:false}); };
  const nodes = tree => { const result=[]; const walk=n=>{if(n&&typeof n==='object'){result.push(n); n.children?.forEach(walk);}};walk(tree);return result; };
  const stages = tree => nodes(tree).filter(n=>n.props.onWheel);
  const transforms = tree => nodes(tree).filter(n=>n.props.style?.transform).map(n=>n.props.style.transform);
  const click = (tree,title) => nodes(tree).find(n=>n.props.title===title).props.onClick();
  const event = more => ({button:0, clientX:100,clientY:80,deltaY:-1,preventDefault(){},currentTarget:{getBoundingClientRect:()=>({left:0,top:0})},...more});
  return {render,nodes,stages,transforms,click,event,refs};
}
test('locked zoom synchronizes; unlocked wheel and pan change only the selected viewport; relock uses the active viewport',()=>{
  const h=harness(); let view=h.render();
  h.stages(view)[0].props.onWheel(h.event()); view=h.render();
  assert.equal(...h.transforms(view));
  h.click(view,'Dual Viewports Synchronized'); view=h.render();
  const original=h.transforms(view)[0];
  h.stages(view)[1].props.onWheel(h.event()); view=h.render();
  assert.equal(h.transforms(view)[0],original); assert.notEqual(h.transforms(view)[1],original);
  const beforePan=h.transforms(view)[1].match(/translate3d\(([-\d.]+)px, ([-\d.]+)px/).slice(1).map(Number);
  h.stages(view)[1].props.onMouseDown(h.event()); view=h.render();
  h.stages(view)[1].props.onMouseMove(h.event({clientX:130,clientY:120})); view=h.render();
  assert.equal(h.transforms(view)[0],original); const afterPan=h.transforms(view)[1].match(/translate3d\(([-\d.]+)px, ([-\d.]+)px/).slice(1).map(Number);
  assert.ok(Math.abs(afterPan[0]-beforePan[0]-30)<1e-9); assert.ok(Math.abs(afterPan[1]-beforePan[1]-40)<1e-9);
  h.stages(view)[1].props.onMouseUp(h.event()); view=h.render();
  const active=h.transforms(view)[1]; h.click(view,'Viewports Unlocked'); view=h.render();
  assert.deepEqual(h.transforms(view),[active,active]);
});
test('toolbar wraps controls and unlocked 1:1 affects the active image only',()=>{
  const h=harness(); let view=h.render();
  const toolbar=h.nodes(view).find(n=>n.children.some(c=>c?.children?.some(x=>x?.props?.title==='Fit to Screen')));
  assert.match(toolbar.props.className,/flex-wrap/); assert.doesNotMatch(toolbar.props.className,/(?:^|\s)h-12(?:\s|$)/);
  h.stages(view)[0].props.onWheel(h.event()); view=h.render(); h.click(view,'Dual Viewports Synchronized'); view=h.render();
  h.stages(view)[1].props.onWheel(h.event()); view=h.render(); const raw=h.transforms(view)[0];
  h.click(view,'1:1 Pixel Native'); view=h.render();
  assert.equal(h.transforms(view)[0],raw); assert.match(h.transforms(view)[1],/scale\(1\)/);
});

test('fit uses actual canvas dimensions after toolbar wrapping and preserves the other unlocked viewport',()=>{
  const h=harness(); let view=h.render();
  const surfaces=h.nodes(view).filter(node=>node.props.ref);
  surfaces[0].props.ref.current={clientWidth:320,clientHeight:240};
  surfaces[1].props.ref.current={clientWidth:280,clientHeight:180};
  h.click(view,'Fit to Screen'); view=h.render();
  assert.equal(h.transforms(view)[0],h.transforms(view)[1]);
  assert.match(h.transforms(view)[0],/scale\(0.1875\)/);
  h.click(view,'Dual Viewports Synchronized'); view=h.render();
  h.stages(view)[1].props.onMouseDown(h.event()); view=h.render();
  h.stages(view)[1].props.onMouseUp(h.event()); view=h.render();
  const raw=h.transforms(view)[0]; h.click(view,'Fit to Screen'); view=h.render();
  assert.equal(h.transforms(view)[0],raw); assert.match(h.transforms(view)[1],/scale\(0.12890625\)/);
});
