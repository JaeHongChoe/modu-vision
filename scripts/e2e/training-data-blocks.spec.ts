import fs from 'node:fs';
import path from 'node:path';
import {createHash} from 'node:crypto';
import {expect,test} from './fixtures/test';

const sha=(value:Buffer)=>createHash('sha256').update(value).digest('hex');
test.use({actionTimeout:15_000});
function imageHashes(root:string):Record<string,string>{
  const result:Record<string,string>={};
  const visit=(directory:string)=>{for(const row of fs.readdirSync(directory,{withFileTypes:true})){
    const file=path.join(directory,row.name);
    if(row.isDirectory())visit(file);else if(row.isFile()&&/\.(png|jpe?g)$/i.test(row.name))result[path.relative(root,file)]=sha(fs.readFileSync(file));
  }};visit(root);return result;
}

test('real training data blocks preserve splits and preprocessing drafts before any model execution',async({page,request,renderer,workspace,evidence})=>{
  test.setTimeout(240_000);
  const create=await request.post(`${renderer.origin}/api/project/create`,{data:{name:'합성 · 학습 준비 블록',task:'classification'}});
  expect(create.ok(),await create.text()).toBe(true);
  const example=await request.post(`${renderer.origin}/api/onboarding/example-dataset`);expect(example.ok()).toBe(true);
  const {folder}=await example.json();
  expect(path.relative(workspace.root,folder)).not.toMatch(/^\.\./);
  expect((await request.put(`${renderer.origin}/api/project/update`,{data:{source_dataset_dir:folder}})).ok()).toBe(true);
  expect((await request.post(`${renderer.origin}/api/dataset/import`,{data:{folder_path:folder,task:'classification',validate_images:true}})).ok()).toBe(true);
  const initialSplitResponse=await request.post(`${renderer.origin}/api/dataset/split`,{data:{folder_path:folder,task:'classification',train_ratio:.7,val_ratio:.2,test_ratio:.1,seed:42}});
  expect(initialSplitResponse.ok(),await initialSplitResponse.text()).toBe(true);
  const initialSplit=(await initialSplitResponse.json()).split;
  const originals=imageHashes(folder);expect(Object.keys(originals).length).toBeGreaterThan(0);

  const splitWrites:unknown[]=[],previewBodies:unknown[]=[],executions:string[]=[],external:string[]=[],pageErrors:string[]=[];
  page.on('pageerror',error=>pageErrors.push(error.message));
  await page.route('**/*',async route=>{
    const request=route.request(),url=new URL(request.url());
    if(['http:','https:'].includes(url.protocol)&&url.origin!==renderer.origin){external.push(url.origin+url.pathname);await route.abort();return;}
    const execution=request.method()!=='GET'&&/^\/api\/(training\/start|automated-training\/start|model-execution\/recipes|labeling-jobs|label-suggestions\/batches|assistant\/send|foundation\/.*(?:predict|segment))/.test(url.pathname);
    if(execution){executions.push(request.method()+' '+url.pathname);await route.abort();return;}
    if(request.method()==='POST'&&url.pathname==='/api/dataset/split')splitWrites.push(request.postDataJSON());
    if(request.method()==='POST'&&url.pathname==='/api/training/input-preparation/preview')previewBodies.push(request.postDataJSON());
    await route.fallback();
  });
  await page.setViewportSize({width:1440,height:1024});
  await page.goto(renderer.url);
  await expect(page.locator('#workflow-project')).toContainText('학습 준비 블록');
  await page.getByRole('button',{name:'간단히 보기',exact:true}).click();
  await page.locator('[data-beginner-goal="teach"]').click();
  await page.locator('[data-beginner-step="3"]').click();
  const rail=page.getByRole('tablist',{name:'학습 데이터 준비 단계'});
  await expect(rail.getByRole('tab')).toHaveCount(5);
  expect(await rail.locator('.training-data-workflow__title').allTextContents()).toEqual(['사진 준비','정답 확인','데이터 나누기','사진 전처리','모델과 학습']);
  const selectBlock=async(title:string)=>{
    const tab=rail.getByRole('tab',{name:new RegExp(title)});await tab.click();await expect(tab).toHaveAttribute('aria-selected','true');
    const panel=page.getByRole('tabpanel').filter({has:page.getByRole('heading',{name:title,exact:true})});
    await expect(panel).toBeVisible();return panel;
  };
  let splitPanel=await selectBlock('데이터 나누기');
  await expect(splitPanel).toContainText(`학습 ${initialSplit.train}`);
  await expect(splitPanel.getByRole('button',{name:'데이터 나누기 저장',exact:true})).toHaveCount(0);
  await selectBlock('사진 준비');await selectBlock('정답 확인');splitPanel=await selectBlock('데이터 나누기');
  expect(splitWrites).toEqual([]);
  const beforeChange=await request.post(`${renderer.origin}/api/dataset/import`,{data:{folder_path:folder,task:'classification',validate_images:false}});
  expect((await beforeChange.json()).split).toMatchObject(initialSplit);
  await splitPanel.getByRole('button',{name:'분할 비율 바꾸기',exact:true}).click();
  await splitPanel.getByLabel('학습 데이터 나누기 비율',{exact:true}).selectOption('80');
  const splitSaved=page.waitForResponse(response=>new URL(response.url()).pathname==='/api/dataset/split'&&response.request().method()==='POST');
  await splitPanel.getByRole('button',{name:'데이터 나누기 저장',exact:true}).click();
  const changed=await splitSaved;expect(changed.ok(),await changed.text()).toBe(true);
  const changedSplit=(await changed.json()).split;
  await expect(splitPanel).toContainText('저장된 분할을 확인했습니다.');
  await expect(splitPanel.getByRole('button',{name:'데이터 나누기 저장',exact:true})).toHaveCount(0);
  expect(splitWrites).toHaveLength(1);
  expect(splitWrites[0]).toMatchObject({train_ratio:.8,val_ratio:.1,test_ratio:.1,folder_path:folder,task:'classification'});
  const persisted=await request.post(`${renderer.origin}/api/dataset/import`,{data:{folder_path:folder,task:'classification',validate_images:false}});
  expect(await persisted.json()).toMatchObject({split:changedSplit,split_state:'saved'});
  const trainImages=await request.get(`${renderer.origin}/api/dataset/images?${new URLSearchParams({folder_path:folder,task:'classification',split:'train',limit:'100'})}`);
  expect(trainImages.ok()).toBe(true);
  const trainingPaths=(await trainImages.json()).items.map((image:{file_path:string})=>image.file_path) as string[];

  let preprocessing=await selectBlock('사진 전처리');
  // Tune the demonstration on a saved training-partition image, not the held-out sample default.
  const availablePreviewPaths=await preprocessing.getByRole('combobox',{name:'실제 미리보기 이미지',exact:true}).locator('option').evaluateAll(options=>options.map(option=>(option as HTMLOptionElement).value));
  const trainingImage=trainingPaths.find(file=>availablePreviewPaths.includes(file));
  if(!trainingImage)throw new Error('The current gallery page must include a saved training-partition image');
  await preprocessing.getByRole('combobox',{name:'실제 미리보기 이미지',exact:true}).selectOption(trainingImage);
  await preprocessing.getByRole('button',{name:'+ 고정 ROI',exact:true}).click();
  await preprocessing.getByLabel('1번 ROI 오른쪽',{exact:true}).fill('0.5');
  await preprocessing.getByRole('button',{name:'+ 고정 회전',exact:true}).click();
  await preprocessing.getByRole('button',{name:'+ 밝기',exact:true}).click();
  await preprocessing.getByLabel('3번 밝기 배율',{exact:true}).fill('1.3');
  await preprocessing.getByRole('button',{name:'3번 규칙 위로',exact:true}).click();
  await expect(preprocessing.locator('[data-input-operation="1"]')).toBeFocused();
  await preprocessing.getByRole('button',{name:'+ 대비',exact:true}).click();
  await expect(preprocessing.locator('[data-input-operation="3"]')).toBeFocused();
  await preprocessing.getByRole('button',{name:'4번 규칙 삭제',exact:true}).click();
  await expect(preprocessing.locator('[data-input-operation="2"]')).toBeFocused();
  await preprocessing.locator('[data-input-operation="1"]').click();
  expect(await preprocessing.locator('[data-input-operation]').allTextContents()).toEqual(['1. 고정 ROI','2. 밝기','3. 고정 회전']);
  // All block contents stay mounted: navigating away cannot discard an unsaved recipe.
  await selectBlock('정답 확인');preprocessing=await selectBlock('사진 전처리');
  expect(await preprocessing.locator('[data-input-operation]').allTextContents()).toEqual(['1. 고정 ROI','2. 밝기','3. 고정 회전']);
  await expect(preprocessing.getByLabel('2번 밝기 배율',{exact:true})).toHaveValue('1.3');
  const previewResponse=page.waitForResponse(response=>new URL(response.url()).pathname==='/api/training/input-preparation/preview'&&response.request().method()==='POST');
  await preprocessing.getByRole('button',{name:'실제 입력 미리보기',exact:true}).click();
  const previewResult=await previewResponse;expect(previewResult.ok(),await previewResult.text()).toBe(true);
  const preview=await previewResult.json();
  expect(previewBodies).toHaveLength(1);
  expect(previewBodies[0]).toMatchObject({task:'classification',image_path:trainingImage,input_recipe:{version:1,operations:[{op:'crop',roi:[0,0,.5,1]},{op:'brightness',factor:1.3},{op:'rotate90',turns:1}]}});
  expect(preview.geometry.steps.map((row:{op:string})=>row.op).slice(0,3)).toEqual(['crop','brightness','rotate90']);
  expect(preview.input_recipe_sha256).toMatch(/^[a-f0-9]{64}$/);
  expect(sha(Buffer.from(preview.original_image.split(',')[1],'base64'))).not.toBe(sha(Buffer.from(preview.prepared_image.split(',')[1],'base64')));
  const previewImages=preprocessing.locator('figure img');await expect(previewImages).toHaveCount(2);
  await expect.poll(()=>previewImages.evaluateAll(images=>images.every(image=>(image as HTMLImageElement).complete&&(image as HTMLImageElement).naturalWidth>0))).toBe(true);
  await preprocessing.getByRole('button',{name:'규칙 저장',exact:true}).click();
  await expect(preprocessing).toContainText('다음 학습에 사용할 입력 규칙을 저장했습니다.');
  const recipeRecords=await page.evaluate(()=>Object.entries(localStorage).filter(([key])=>key.startsWith('modu.input-preparation.v1:')).map(([,value])=>JSON.parse(value)));
  expect(recipeRecords).toHaveLength(1);expect(recipeRecords[0].at(-1)).toMatchObject({sha256:preview.input_recipe_sha256,recipe:{operations:[{op:'crop'},{op:'brightness'},{op:'rotate90'}]}});
  await evidence.screenshot(page,'training-blocks-real-preprocessing-preview');
  await previewImages.first().scrollIntoViewIfNeeded();
  await evidence.screenshot(page,'training-blocks-real-before-after-comparison');
  // A newly edited value is a draft again; switching blocks preserves it and does not save it implicitly.
  await preprocessing.getByLabel('2번 밝기 배율',{exact:true}).fill('1.4');
  await selectBlock('모델과 학습');
  await expect(page.getByLabel('선택 모델 안내')).toBeVisible();
  const settings=page.locator('#workflow-training details').filter({has:page.locator('summary').filter({hasText:'고급 학습 설정'})}).first();
  await settings.locator('summary').first().click();await expect(page.locator('#training-model-settings')).toBeVisible();
  preprocessing=await selectBlock('사진 전처리');await expect(preprocessing.getByLabel('2번 밝기 배율',{exact:true})).toHaveValue('1.4');
  await expect(preprocessing).toContainText('저장하지 않은 변경');
  expect(previewBodies).toHaveLength(1);expect(splitWrites).toHaveLength(1);

  await page.setViewportSize({width:820,height:900});
  await rail.getByRole('tab',{name:/사진 전처리/}).focus();
  await page.keyboard.press('Home');await expect(rail.getByRole('tab').first()).toHaveAttribute('aria-selected','true');
  await page.keyboard.press('End');await expect(rail.getByRole('tab').last()).toHaveAttribute('aria-selected','true');
  preprocessing=await selectBlock('사진 전처리');
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1)).toBe(true);
  const overflow=await page.locator('.training-data-workflow__rail').evaluate(el=>({client:el.clientWidth,scroll:el.scrollWidth,overflow:getComputedStyle(el).overflowX}));
  expect(['auto','scroll']).toContain(overflow.overflow);
  await preprocessing.scrollIntoViewIfNeeded();await evidence.screenshot(page,'training-blocks-narrow-preprocessing-draft');
  await selectBlock('데이터 나누기');await evidence.screenshot(page,'training-blocks-narrow-saved-split');
  expect(imageHashes(folder)).toEqual(originals);
  expect(executions).toEqual([]);expect(external).toEqual([]);expect(pageErrors).toEqual([]);
  evidence.note('training_data_blocks',{initialSplit,changedSplit,splitWrites,previewRequests:previewBodies,preview_partition:'train',recipe_sha256:preview.input_recipe_sha256,preview_geometry:preview.geometry,source_image_hashes:originals,overflow,executions,external,pageErrors,synthetic_only:true,real_backend_preview:true,model_training:false,native_localhost_acceptance:false});
});
