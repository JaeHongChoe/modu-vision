import type {Page} from '@playwright/test';
import {test,expect,type Workspace,type Evidence} from './fixtures/test';
type Api=(route:string,body?:unknown)=>Promise<any>;
const stageNames:Record<string,string>={evaluate:'평가',predict:'단일 예측',benchmark:'모델 forward 계측',generate:'생성'};
async function exercise(page:Page,workspace:Workspace,evidence:Evidence,api:Api,url?:string){
  await api('/api/project/create',{name:'Execution support catalog control',task:'classification'});
  await api('/api/project/update',{source_dataset_dir:workspace.dataset});
  await api('/api/dataset/import',{folder_path:workspace.dataset,task:'classification'});
  const catalog=await api('/api/models/capabilities'),matrix=await api('/api/model-execution/capabilities');
  expect(catalog.families).toHaveLength(10);expect(Object.keys(matrix.families)).toHaveLength(10);
  if(url)await page.goto(url);else await page.reload();
  await page.getByRole('navigation',{name:'Workflow Stages'}).getByRole('button').nth(2).click();
  const hub=page.getByRole('region',{name:'모델 학습 허브'});
  let projectTask='classification';
  for(const family of catalog.families){
    expect(family.execution).toEqual(matrix.families[family.task]);
    await hub.getByRole('button',{name:new RegExp('^'+family.label)}).click();
    if(['classification','segmentation','detection','anomaly'].includes(family.task)&&family.task!==projectTask){
      const impact=page.getByRole('dialog',{name:'검사 작업 변경 영향'});
      await expect(impact).toBeVisible();
      await impact.getByRole('button',{name:'영향 확인 후 변경',exact:true}).click();
      await expect(impact).not.toBeVisible();projectTask=family.task;
    }
    await expect(hub.getByRole('button',{name:new RegExp('^'+family.label)})).toHaveAttribute('aria-pressed','true');
    const details=hub.locator('details').first();if(!await details.evaluate(el=>(el as HTMLDetailsElement).open))await details.locator('summary').click();
    const support=hub.getByLabel(family.label+' 실행 위치 지원');await expect(support).toBeVisible();
    for(const stage of family.execution.native_recipe_stages)await expect(support).toContainText(stageNames[stage]);
    await expect(support).toContainText('상단 Compute에서 실행 위치 선택');
    if(family.task==='defect_gan'){
      expect(family.execution.predict).toBe(false);expect(family.execution.flow).toBe(false);expect(family.execution.generate).toBe(true);
      await expect(support).toContainText('검사 플로우 모델로 사용할 수 없습니다.');await expect(support).not.toContainText('단일 예측');
    }else expect(family.execution.predict).toBe(true);
  }
  await evidence.screenshot(page,'ten-family-execution-support-catalog');
  evidence.note('execution_support_catalog',{catalog,matrix,all_ten_families_visible:true,generation_only_guard:true,actual_model_execution_in_this_display_case:false});
}
test('ten family support follows actual recipe contracts',{tag:'@source-display'},async({page,request,renderer,workspace,evidence})=>{
  const api:Api=async(route,body)=>{const response=body===undefined?await request.get(renderer.origin+route):route.endsWith('/update')?await request.put(renderer.origin+route,{data:body}):await request.post(renderer.origin+route,{data:body});expect(response.ok(),await response.text()).toBe(true);return response.json();};
  await exercise(page,workspace,evidence,api,renderer.url);
});
test('native model hub shows all ten supported execution contracts',{tag:['@electron','@source-display']},async({electronSession,workspace,evidence})=>{
  const {window}=electronSession,backend=await electronSession.waitForBackend();
  const api:Api=(route,body)=>window.evaluate(async({port,route,body})=>{const response=await fetch(`http://127.0.0.1:${port}${route}`,{...(body===undefined?{}:{method:route.endsWith('/update')?'PUT':'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})});if(!response.ok)throw Error(await response.text());return response.json();},{port:backend.port,route,body});
  await exercise(window,workspace,evidence,api);
});
