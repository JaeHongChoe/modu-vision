import fs from 'node:fs';
import path from 'node:path';
import {createHash} from 'node:crypto';
import {expect,test} from './fixtures/test';

const digest=(bytes:Buffer)=>createHash('sha256').update(bytes).digest('hex');
test('code preprocessing executes a local FFT preview, retires edited proof and prepares an unsent assistant request',async({page,request,renderer,workspace,evidence})=>{
  test.setTimeout(240_000);
  const create=await request.post(`${renderer.origin}/api/project/create`,{data:{name:'합성 · 코드 전처리',task:'classification'}});
  expect(create.ok(),await create.text()).toBe(true);
  const example=await request.post(`${renderer.origin}/api/onboarding/example-dataset`);expect(example.ok()).toBe(true);
  const {folder}=await example.json();expect(path.relative(workspace.root,folder)).not.toMatch(/^\.\./);
  expect((await request.put(`${renderer.origin}/api/project/update`,{data:{source_dataset_dir:folder}})).ok()).toBe(true);
  expect((await request.post(`${renderer.origin}/api/dataset/import`,{data:{folder_path:folder,task:'classification',validate_images:true}})).ok()).toBe(true);
  const hashes:Record<string,string>={};
  const collect=(dir:string)=>{for(const entry of fs.readdirSync(dir,{withFileTypes:true})){
    const file=path.join(dir,entry.name);if(entry.isDirectory())collect(file);else if(/\.(png|jpe?g)$/i.test(file))hashes[file]=digest(fs.readFileSync(file));
  }};collect(folder);expect(Object.keys(hashes).length).toBeGreaterThan(0);
  const executions:string[]=[],external:string[]=[],pageErrors:string[]=[];
  page.on('pageerror',error=>pageErrors.push(error.message));
  await page.route('**/*',async route=>{
    const req=route.request(),url=new URL(req.url());
    if(['http:','https:'].includes(url.protocol)&&url.origin!==renderer.origin){external.push(url.origin+url.pathname);await route.abort();return;}
    if(req.method()!=='GET'&&/^\/api\/(?:training\/start|automated-training\/start|assistant\/send|model-execution\/recipes|foundation\/)/.test(url.pathname)){
      executions.push(url.pathname);await route.abort();return;
    }
    await route.fallback();
  });
  await page.setViewportSize({width:1440,height:1024});await page.goto(renderer.url);
  await expect(page.locator('#workflow-project')).toContainText('코드 전처리');
  await page.getByRole('button',{name:'간단히 보기',exact:true}).click();
  await page.locator('[data-beginner-goal="teach"]').click();await page.locator('[data-beginner-step="3"]').click();
  await page.getByRole('tablist',{name:'학습 데이터 준비 단계'}).getByRole('tab',{name:/사진 전처리/}).click();
  const panel=page.getByRole('tabpanel').filter({has:page.getByRole('heading',{name:'사진 전처리',exact:true})});
  await panel.getByRole('button',{name:'+ 코드 블록',exact:true}).click();
  await panel.getByRole('button',{name:'FFT 예시 넣기',exact:true}).click();
  const disclosure=panel.locator('details').filter({has:page.locator('summary').filter({hasText:'코드 보기·직접 수정'})});
  if(!await disclosure.evaluate(node=>(node as HTMLDetailsElement).open))await disclosure.locator(':scope > summary').click();
  const editor=panel.getByRole('textbox',{name:'1번 전처리 코드',exact:true});
  const fft=await editor.inputValue();expect(fft).toContain('notch_mask');expect(fft).toContain('result =');
  const editorBox=await editor.boundingBox(),workspaceBox=await panel.locator('.training-input-editor__workspace').boundingBox();
  expect(editorBox).not.toBeNull();expect(workspaceBox).not.toBeNull();
  expect(editorBox!.width).toBeGreaterThan(workspaceBox!.width*.7);
  await panel.getByRole('button',{name:'+ 밝기',exact:true}).click();
  const otherSettings=await panel.locator('.training-input-editor__settings').boundingBox();
  expect(otherSettings!.width).toBeLessThan(workspaceBox!.width*.7);
  await panel.getByRole('button',{name:'2번 규칙 삭제',exact:true}).click();
  await expect(editor).toBeVisible();await editor.scrollIntoViewIfNeeded();
  await evidence.screenshot(page,'code-preprocessing-wide-editor');
  const save=panel.getByRole('button',{name:'규칙 저장',exact:true});await expect(save).toBeDisabled();
  const nextPreview=()=>page.waitForResponse(response=>new URL(response.url()).pathname==='/api/training/input-preparation/preview'&&response.request().method()==='POST');
  const successful=nextPreview();await panel.getByRole('button',{name:'실제 입력 미리보기',exact:true}).click();
  const response=await successful;expect(response.ok(),await response.text()).toBe(true);const preview=await response.json();
  expect(preview.input_recipe.operations).toEqual([{op:'code',language:'modu-numeric-v1',code:fft}]);
  expect(preview.input_preparation_contract.numeric_runtime).toBe('modu-numeric-v1');
  expect(preview.input_recipe_sha256).toMatch(/^[a-f0-9]{64}$/);
  const pictures=panel.locator('figure img');await expect(pictures).toHaveCount(2);
  await expect.poll(()=>pictures.evaluateAll(images=>images.every(image=>(image as HTMLImageElement).complete&&(image as HTMLImageElement).naturalWidth>0))).toBe(true);
  await expect(save).toBeEnabled();await save.click();await expect(panel).toContainText('다음 학습에 사용할 입력 규칙을 저장했습니다.');
  await pictures.first().scrollIntoViewIfNeeded();await evidence.screenshot(page,'code-preprocessing-actual-fft-preview');
  await editor.fill('import os\nresult = image');await expect(save).toBeDisabled();
  const rejected=nextPreview();await panel.getByRole('button',{name:'실제 입력 미리보기',exact:true}).click();
  expect((await rejected).status()).toBe(422);await expect(panel.getByRole('alert')).toBeVisible();await expect(save).toBeDisabled();
  const stored=await page.evaluate(()=>Object.entries(localStorage).filter(([key])=>key.startsWith('modu.input-preparation.v1:')).map(([,value])=>JSON.parse(value).at(-1)));
  expect(stored).toHaveLength(1);expect(stored[0].recipe.operations[0].code).toBe(fft);
  await editor.fill(fft+'\n# edited draft');await expect(save).toBeDisabled();
  await panel.getByRole('textbox',{name:'도우미에게 요청할 내용',exact:true}).fill('초음파 사진의 세로 반복 패턴을 FFT로 줄이고 싶어요');
  await panel.getByRole('button',{name:'도우미에게 요청',exact:true}).click();
  const assistant=page.getByRole('complementary',{name:'도우미',exact:true});await expect(assistant).toBeVisible();
  await expect(assistant.getByRole('textbox',{name:'도우미에게 묻기',exact:true})).toHaveValue(/초음파 사진의 세로 반복 패턴/);
  await assistant.getByRole('button',{name:'닫기',exact:true}).click();
  await page.setViewportSize({width:720,height:900});await editor.scrollIntoViewIfNeeded();
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1)).toBe(true);
  await evidence.screenshot(page,'code-preprocessing-compact-editor');
  for(const [file,hash] of Object.entries(hashes))expect(digest(fs.readFileSync(file))).toBe(hash);
  expect(executions).toEqual([]);expect(external).toEqual([]);expect(pageErrors).toEqual([]);
  evidence.note('code_preprocessing',{recipe_sha256:preview.input_recipe_sha256,source_images_preserved:Object.keys(hashes).length,real_backend_fft_preview:true,reject_import:true,edited_preview_retired:true,assistant_draft_only:true,provider_calls:0,model_calls:0,synthetic_only:true,quality_verified:false,executions,external,pageErrors});
});
