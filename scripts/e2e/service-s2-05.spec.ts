import type {Page} from '@playwright/test';
import {expect,test,type RendererServer,type Workspace} from './fixtures/test';
import {installDesktopHostShim} from './fixtures/desktop-host-shim';

// S2-05 on the actual renderer and harness backend: every node and connection shows its own problem, ports name what a
// connection carries, and a connection started from the wrong port is refused at once. No model is trained or run.
async function setup(page:Page,renderer:RendererServer,workspace:Workspace){
 const created=await page.request.post(`${renderer.origin}/api/project/create`,{data:{name:'S205 workspace',task:'anomaly'}});expect(created.ok()).toBe(true);
 expect((await page.request.put(`${renderer.origin}/api/project/update`,{data:{source_dataset_dir:workspace.dataset}})).ok()).toBe(true);
 await installDesktopHostShim(page,renderer.port);await page.goto(renderer.url);
 await page.getByRole('button',{name:/05.*플로우차트/}).click();
 await expect(page.getByRole('heading',{name:'검사 플로우 편집기',exact:true})).toBeVisible();
 await expect(page.locator('[data-flow-node-id]')).not.toHaveCount(0);
}
const ids=(page:Page)=>page.locator('[data-flow-node-id]').evaluateAll(nodes=>nodes.map(node=>node.getAttribute('data-flow-node-id')!));

test('every wire starts at its typed output port and ends at its input port, including older untyped edges',async({page,renderer,workspace,evidence})=>{
 await setup(page,renderer,workspace);
 const wires=await page.evaluate(()=>{
  const paths=[...document.querySelectorAll<SVGPathElement>('path[data-flow-edge]')];const svg=paths[0]?.ownerSVGElement;if(!svg)return [];
  const frame=svg.getBoundingClientRect(),scale=svg.clientWidth?frame.width/svg.clientWidth:1;
  const pin=(key:string)=>{const element=document.querySelector<HTMLElement>(`[data-flow-port="${key}"]`);if(!element)return null;const box=element.getBoundingClientRect();
   return {x:(box.left+box.width/2-frame.left)/scale,y:(box.top+box.height/2-frame.top)/scale};};
  return paths.map(path=>{const numbers=(path.getAttribute('d')||'').match(/-?\d+(?:\.\d+)?/g)!.map(Number);
   const from=pin(path.dataset.flowFrom!),to=pin(path.dataset.flowTo!);
   return {edge:path.dataset.flowEdge,from:path.dataset.flowFrom,to:path.dataset.flowTo,
    start:from?Math.hypot(from.x-numbers[0],from.y-numbers[1]):-1,end:to?Math.hypot(to.x-numbers[numbers.length-2],to.y-numbers[numbers.length-1]):-1};});});
 expect(wires.length).toBeGreaterThan(0);
 for(const wire of wires){expect(wire.start,`${wire.edge} starts at ${wire.from}`).toBeGreaterThanOrEqual(0);expect(wire.start).toBeLessThanOrEqual(2);
  expect(wire.end,`${wire.edge} ends at ${wire.to}`).toBeGreaterThanOrEqual(0);expect(wire.end).toBeLessThanOrEqual(2);}
 // the default flow's model -> decision edge is saved without a payload; it is still a result and leaves RESULT OUT
 const decision=await page.locator('[data-flow-node-id]').filter({hasText:'DECISION'}).first().getAttribute('data-flow-node-id');
 expect(wires.some(wire=>wire.to===`${decision}:in:0`&&wire.from?.endsWith(':out:1')),'the result wire leaves the RESULT OUT port').toBe(true);
 await evidence.screenshot(page,'s205-wires-at-ports');
});

test('an added model shows its own problem, the wrong output port is refused and the right one connects',async({page,renderer,workspace,evidence})=>{
 await setup(page,renderer,workspace);
 const before=await ids(page);
 // an existing model node: the one offering a RESULT output
 const existing=page.locator('[data-flow-node-id]').filter({has:page.getByRole('button',{name:/RESULT OUT$/})}).first();
 await expect(existing).toBeVisible();
 await page.getByRole('button',{name:'검사 모델',exact:true}).click();
 await expect.poll(async()=>(await ids(page)).length).toBe(before.length+1);
 const added=(await ids(page)).find(id=>!before.includes(id))!;const node=page.locator(`[data-flow-node-id="${added}"]`);
 await expect(node.getByRole('note',{name:/문제 1건: 모델 입력 연결선이 정확히 하나 필요합니다/})).toBeVisible();
 await expect(node.getByRole('button',{name:/IMAGE\/ROI IN$/})).toBeVisible();
 await evidence.screenshot(page,'s205-added-node-marked');

 await existing.getByRole('button',{name:/^Start connection from .* RESULT OUT$/}).click();
 await node.getByRole('button',{name:/^Connect to .* IMAGE\/ROI IN$/}).click();
 await expect(page.getByText(/결과 출력은 .*에 연결할 수 없습니다\. 이 입력은 이미지·ROI만 받습니다\./)).toBeVisible();
 await expect(node.getByRole('note',{name:/모델 입력 연결선이 정확히 하나/})).toBeVisible();
 await evidence.screenshot(page,'s205-wrong-port-refused');

 await existing.getByRole('button',{name:/^Start connection from .* IMAGE\/ROI OUT$/}).click();
 await node.getByRole('button',{name:/^Connect to .* IMAGE\/ROI IN$/}).click();
 await expect(page.getByText(/이 입력은 이미지·ROI만 받습니다/)).toHaveCount(0);
 await expect(node.getByRole('note',{name:/다음 모델, Blob, 집계 또는 판정 노드로 연결하세요/})).toBeVisible();
 await node.click();
 await expect(page.getByRole('list',{name:'선택한 항목의 문제'})).toContainText('다음 모델, Blob, 집계 또는 판정 노드로 연결하세요');
 await evidence.screenshot(page,'s205-connected-next-problem');
});
