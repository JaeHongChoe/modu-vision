import {expect,test} from './fixtures/test';
import AxeBuilder from '@axe-core/playwright';

// Real renderer + isolated backend and synthetic harness photos. No model training, GPT or installed app changes.
test('selected novice workspace keeps six stages, task tools and next action usable',async({page,request,renderer,workspace,evidence})=>{
  const response=await request.post(`${renderer.origin}/api/project/create`,{data:{name:'디자인 검증 · 표면 검사',task:'classification'}});
  expect(response.ok()).toBe(true);
  const generated=await request.post(`${renderer.origin}/api/onboarding/example-dataset`);expect(generated.ok()).toBe(true);
  const {folder}=await generated.json();
  expect((await request.put(`${renderer.origin}/api/project/update`,{data:{source_dataset_dir:folder}})).ok()).toBe(true);
  expect((await request.post(`${renderer.origin}/api/dataset/import`,{data:{folder_path:folder,task:'classification',validate_images:true}})).ok()).toBe(true);
  await page.setViewportSize({width:1440,height:1024});
  await page.goto(renderer.url);
  await expect(page.locator('#workflow-project')).toContainText('디자인 검증');
  await page.getByRole('button',{name:'간단히 보기',exact:true}).click();
  await page.getByText('설정·도구',{exact:true}).click();
  const light=page.getByRole('button',{name:'밝은 화면',exact:true});
  if(await light.isVisible())await light.click();else await page.keyboard.press('Escape');
  await expect(page.locator('html')).toHaveAttribute('data-theme','light');
  const next=page.getByRole('complementary',{name:'다음 할 일'});
  await expect(next).toBeVisible();
  await expect(page.getByRole('region',{name:'처음 만드는 AI 진행'})).toHaveCount(0);
  const goals=['photos','teach','teach','check','use','use'];
  for(let i=0;i<6;i++){
    await page.locator(`[data-beginner-goal="${goals[i]}"]`).click();
    await page.locator(`[data-beginner-step="${i+1}"]`).click();
    await expect(page.locator(`[data-beginner-step="${i+1}"]`)).toHaveAttribute('aria-current','step');
    if(i===0){
      await expect(page.locator('[data-dataset-gallery] img').first()).toBeVisible();
      await expect.poll(()=>page.locator('[data-dataset-gallery] img').evaluateAll(images=>images.slice(0,6).length===6&&images.slice(0,6).every(img=>(img as HTMLImageElement).complete&&(img as HTMLImageElement).naturalWidth>0))).toBe(true);
      const image=await page.locator('[data-dataset-gallery] img').first().boundingBox();expect(image!.y).toBeLessThanOrEqual(450);
    }
    await expect(next.locator('[data-state="checking"]')).toHaveCount(0);
    if(i===2)await expect(page.getByLabel('선택 모델 안내')).toContainText('필요한 자료');
    await expect(next.getByRole('button',{name:/권장 행동:/})).toBeInViewport({ratio:1});
    expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
    await expect.poll(()=>page.evaluate(()=>document.getAnimations().filter(a=>a instanceof CSSTransition&&a.playState==='running').length)).toBe(0);
    await evidence.screenshot(page,`workspace-light-step-${i+1}`);
  }
  await page.locator('[data-beginner-goal="photos"]').click();
  const accessibility=await new AxeBuilder({page}).withTags(['wcag2a','wcag2aa']).analyze();
  evidence.note('accessibility',accessibility.violations.map(v=>({id:v.id,impact:v.impact,nodes:v.nodes.map(n=>n.target)})));
  expect(accessibility.violations.filter(v=>v.impact==='critical'||v.impact==='serious')).toEqual([]);
  await page.setViewportSize({width:1100,height:780});
  await page.getByText('설정·도구',{exact:true}).click();
  await page.getByRole('button',{name:'어두운 화면',exact:true}).click();
  await expect(page.locator('html')).toHaveAttribute('data-theme','dark');
  await evidence.screenshot(page,'workspace-dark-compact');
  await next.getByRole('button',{name:'도우미 열기',exact:true}).click();
  await expect(page.getByRole('heading',{name:'ChatGPT 도우미',exact:true})).toBeVisible();
  await expect(next.getByRole('button',{name:/권장 행동:/})).toBeInViewport({ratio:1});
  const columns=await page.locator('.modu-workspace-body').evaluate(el=>getComputedStyle(el).display);expect(columns).toBe('flex');
  await evidence.screenshot(page,'workspace-dark-assistant');
  // Close the existing drawer; never sends an account request.
  await page.getByRole('button',{name:'닫기',exact:true}).last().click();
  await page.getByText('설정·도구',{exact:true}).click();
  await page.getByRole('button',{name:'용어 사전',exact:true}).click();
  await expect(page.getByRole('dialog')).toBeVisible();
  await page.keyboard.press('Escape');
  await page.locator('#workflow-project').click();
  await expect(page.getByRole('dialog',{name:'프로젝트 관리'})).toBeVisible();
  await evidence.screenshot(page,'workspace-project-dialog');
  await page.keyboard.press('Escape');
  await expect(page.getByRole('dialog',{name:'프로젝트 관리'})).not.toBeVisible();
  await page.setViewportSize({width:820,height:900});
  await expect(next.getByRole('button',{name:/권장 행동:/})).toBeInViewport({ratio:1});
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
  await evidence.screenshot(page,'workspace-narrow');
  await page.setViewportSize({width:1440,height:1024});
  await page.getByRole('button',{name:'모두 보기',exact:true}).click();
  await page.locator('summary').filter({hasText:'폴더 경로로 가져오기'}).click();
  await expect(page.getByRole('textbox',{name:'이미지 폴더 경로'})).toBeInViewport({ratio:1});
  await page.getByRole('textbox',{name:'이미지 폴더 경로'}).fill('/tmp/qa-not-submitted');
  await evidence.screenshot(page,'workspace-full-path-fallback');
  evidence.note('scope','Synthetic images, real isolated backend and renderer. Six-stage navigation and layout; no training, account request, deployment, or field-quality claim.');
});


test('selected workspace also renders in an isolated native window', {tag:'@electron'}, async({electronSession,evidence})=>{
  const backend=await electronSession.waitForBackend();expect(backend.health.status).toBe('ok');
  const page=electronSession.window;
  await electronSession.app.evaluate(({BrowserWindow})=>BrowserWindow.getAllWindows()[0].setSize(1440,1024));
  await expect(page.getByRole('navigation',{name:'프로젝트 작업 공간'})).toBeVisible();
  await page.getByRole('button',{name:'간단히 보기',exact:true}).click();
  await page.getByText('설정·도구',{exact:true}).click();
  const light=page.getByRole('button',{name:'밝은 화면',exact:true});
  if(await light.isVisible())await light.click();else await page.keyboard.press('Escape');
  await expect(page.locator('html')).toHaveAttribute('data-theme','light');
  await expect(page.getByRole('complementary',{name:'다음 할 일'})).toBeVisible();
  await expect(page.locator('[data-beginner-goal]')).toHaveCount(4);
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
  await evidence.screenshot(page,'native-selected-workspace');
  await page.locator('#workflow-project').click();
  await expect(page.getByRole('dialog',{name:'프로젝트 관리'})).toBeVisible();
  await evidence.screenshot(page,'native-project-dialog');
  await page.keyboard.press('Escape');
  await expect(page.getByRole('dialog',{name:'프로젝트 관리'})).not.toBeVisible();
  evidence.note('scope','Owned Electron source build and isolated profile. Window, native inset, four-goal navigation and dialog only; no installed package replacement or model requests.');
});
