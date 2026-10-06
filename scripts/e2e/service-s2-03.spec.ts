import { expect, test } from './fixtures/test';

// Actual mounted renderer. Synthetic project and controlled read-only receipts;
// no training, model inference, quality approval or target deployment is run.
for(const viewport of [{width:1366,height:768},{width:1920,height:1080}])test(`six stages expose readiness without blocking manual navigation at ${viewport.width}`, async ({ page, request, renderer, evidence }) => {
  await page.setViewportSize(viewport);
  expect((await request.post(`${renderer.origin}/api/project/create`,{data:{name:'S203 readiness fixture',task:'classification'}})).ok()).toBe(true);
  await page.goto(renderer.url);
  await expect(page.getByTitle('프로젝트 관리', { exact: true }), 'the backend current project opens on load').toContainText('S203 readiness fixture');
  for(let stage=1;stage<=6;stage++){
    await page.getByRole('navigation',{name:'Workflow Stages'}).getByRole('button').nth(stage-1).click();
    const footer=page.getByRole('contentinfo');
    await expect(footer.getByRole('status')).toContainText(/완료 입력/,{timeout:5000});
    await expect(footer.getByRole('button',{name:/권장 행동:/})).toHaveCount(1);
    await expect(footer.getByRole('status')).toContainText(/부족|확인 필요|확인 중/);
    expect(await footer.evaluate(el=>el.scrollWidth<=el.clientWidth)).toBe(true);
  }
  await evidence.screenshot(page,`s203-sixth-stage-${viewport.width}`);
  evidence.note('scope',{actual_renderer:true,synthetic_project:true,training:false,target_deployment:false});
});

test('task change previews impact; cancel preserves task and confirm applies once',async({page,request,renderer,evidence})=>{
  expect((await request.post(`${renderer.origin}/api/project/create`,{data:{name:'S203 task fixture',task:'classification'}})).ok()).toBe(true);
  await page.goto(renderer.url);
  await expect(page.getByTitle('프로젝트 관리', { exact: true }), 'the backend current project opens on load').toContainText('S203 task fixture');
  let mutations=0;
  page.on('request',req=>{if(req.method()==='PUT'&&new URL(req.url()).pathname==='/api/project/update')mutations++;});
  const selector=page.getByRole('combobox',{name:'검사 작업 종류'});
  await selector.selectOption('segmentation');
  const dialog=page.getByRole('dialog',{name:'검사 작업 변경 영향'});
  await expect(dialog).toBeVisible({timeout:5000});
  expect(mutations).toBe(0);
  await expect(selector).toHaveValue('classification');
  await expect(dialog).toContainText('이전 결과');
  await dialog.getByRole('button',{name:'취소',exact:true}).click();
  await expect(dialog).not.toBeVisible();expect(mutations).toBe(0);
  await selector.selectOption('segmentation');
  await dialog.getByRole('button',{name:'영향 확인 후 변경',exact:true}).click();
  await expect(dialog).not.toBeVisible();
  await expect(selector).toHaveValue('segmentation');expect(mutations).toBe(1);
  await evidence.screenshot(page,'s203-confirmed-task');
});

test('task refusal preserves active selection and exposes the existing failure',async({page,request,renderer})=>{
  expect((await request.post(`${renderer.origin}/api/project/create`,{data:{name:'S203 refused task fixture',task:'classification'}})).ok()).toBe(true);
  await page.goto(renderer.url);await expect(page.getByTitle('프로젝트 관리', { exact: true }), 'the backend current project opens on load').toContainText('S203 refused task fixture');
  await page.route('**/api/project/update',route=>route.fulfill({status:409,json:{detail:'S203 existing policy refuses task change'}}));
  const selector=page.getByRole('combobox',{name:'검사 작업 종류'});
  await selector.selectOption('segmentation');
  await page.getByRole('dialog',{name:'검사 작업 변경 영향'}).getByRole('button',{name:'영향 확인 후 변경',exact:true}).click();
  await expect(selector).toHaveValue('classification');
  await expect(page.getByRole('alert').filter({hasText:'S203 existing policy refuses task change'}).first()).toBeVisible();
});

test('new readiness reply wins and failed read stays unverified',async({page,request,renderer,workspace,evidence})=>{
  expect((await request.post(`${renderer.origin}/api/project/create`,{data:{name:'S203 receipt order fixture',task:'classification'}})).ok()).toBe(true);
  expect((await request.put(`${renderer.origin}/api/project/update`,{data:{source_dataset_dir:workspace.dataset}})).ok()).toBe(true);
  let release:(()=>Promise<void>)|undefined,calls=0,fail=false;
  await page.route('**/api/team-data/readiness',async route=>{
    calls++;
    if(calls===1){await new Promise<void>(resolve=>{release=async()=>{await route.fulfill({json:{ready:true,counts:{eligible:2},blockers:[]}});resolve();};});return;}
    if(fail){await route.fulfill({status:503,json:{detail:'S203 readiness unavailable'}});return;}
    await route.fulfill({json:{ready:false,counts:{eligible:0},blockers:['NEW 검수 입력 부족']}});
  });
  await page.goto(renderer.url);await expect(page.getByTitle('프로젝트 관리', { exact: true }), 'the backend current project opens on load').toContainText('S203 receipt order fixture');
  await page.getByRole('navigation',{name:'Workflow Stages'}).getByRole('button').nth(1).click();
  const footer=page.getByRole('contentinfo');
  await expect.poll(()=>Boolean(release)).toBe(true);
  await footer.getByRole('button',{name:'입력 상세',exact:true}).click();
  await footer.getByRole('button',{name:'현재 입력 다시 확인',exact:true}).click();
  await expect(footer.getByRole('status')).toContainText('NEW 검수 입력 부족');
  const response=page.waitForResponse(r=>new URL(r.url()).pathname==='/api/team-data/readiness');
  await release!();await (await response).finished();
  await expect(footer.getByRole('status')).toContainText('NEW 검수 입력 부족');
  fail=true;await footer.getByRole('button',{name:'현재 입력 다시 확인',exact:true}).click();
  await expect(footer.getByRole('status')).toContainText('S203 readiness unavailable');
  await expect(footer.getByRole('button',{name:'권장 행동: 준비도 다시 확인',exact:true})).toBeVisible();
  await evidence.screenshot(page,'s203-evidence-failed');
  evidence.note('receipt_order',{readiness_transport_fixture:true,old_reply_ignored:true,failed_read_not_ready:true});
});

test('blocked training and deployment expose a visible recovery destination',async({page,request,renderer,evidence})=>{
  expect((await request.post(`${renderer.origin}/api/project/create`,{data:{name:'S203 recovery fixture',task:'classification'}})).ok()).toBe(true);
  await page.goto(renderer.url);await expect(page.getByTitle('프로젝트 관리', { exact: true }), 'the backend current project opens on load').toContainText('S203 recovery fixture');
  await page.getByRole('navigation',{name:'Workflow Stages'}).getByRole('button').nth(2).click();
  const train=page.getByRole('button',{name:'선택 설정으로 학습 시작',exact:true});
  await expect(train).toBeDisabled();
  await expect(page.getByLabel('학습 시작 준비도')).toContainText('학습 시작 보류');
  await page.getByRole('button',{name:'데이터·분할 확인 (1단계)',exact:true}).click();
  await expect(page.getByRole('contentinfo')).toContainText('1/6');
  await page.getByRole('button',{name:'패키지·장치·진단',exact:true}).click();
  const dialog=page.getByRole('dialog',{name:'패키지·장치·설치·진단',exact:true});
  await expect(dialog.getByRole('button',{name:'승인 패키지 적용',exact:true})).toBeDisabled();
  await expect(dialog).toContainText('적용 보류: 승인 포함 패키지가 필요합니다.');
  await dialog.getByRole('button',{name:'승인·패키지 확인 (6단계)',exact:true}).first().click();
  await expect(dialog).not.toBeVisible({timeout:5000});
  await expect(page.getByRole('contentinfo')).toContainText('6/6');
  await evidence.screenshot(page,'s203-recovery-visible');
});

test('model family choice uses the same cancellable task impact preview',async({page,request,renderer,evidence})=>{
  expect((await request.post(`${renderer.origin}/api/project/create`,{data:{name:'S203 family fixture',task:'classification'}})).ok()).toBe(true);
  await page.route('**/api/models/capabilities',route=>route.fulfill({json:{families:['classification','segmentation'].map(task=>({task,label:task==='classification'?'S203 분류':'S203 분할',model:'fixture catalog',architectures:['fixture'],devices:['cpu'],default_architecture:'fixture',prerequisite:'fixture',remote_training:false,continuation:'none',stages:[],missing_dependencies:[]}))}}));
  await page.goto(renderer.url);await expect(page.getByTitle('프로젝트 관리', { exact: true }), 'the backend current project opens on load').toContainText('S203 family fixture');
  await page.getByRole('navigation',{name:'Workflow Stages'}).getByRole('button').nth(2).click();
  const family=page.getByRole('button',{name:'S203 분할 fixture catalog',exact:true});
  const current=page.getByRole('button',{name:'S203 분류 fixture catalog',exact:true});
  await family.click();const dialog=page.getByRole('dialog',{name:'검사 작업 변경 영향'});
  await expect(dialog).toBeVisible();await expect(current).toHaveAttribute('aria-pressed','true');
  await evidence.screenshot(page,'s203-family-preview');
  await dialog.getByRole('button',{name:'취소',exact:true}).click();await expect(current).toHaveAttribute('aria-pressed','true');
  await page.route('**/api/project/update',route=>route.fulfill({status:409,json:{detail:'S203 family change refused'}}));
  await family.click();await dialog.getByRole('button',{name:'영향 확인 후 변경',exact:true}).click();
  await expect(dialog).not.toBeVisible();await expect(current).toHaveAttribute('aria-pressed','true');
  await expect(page.getByRole('combobox',{name:'검사 작업 종류'})).toHaveValue('classification');
  await expect(page.getByText(/모델 종류 변경 후 확인이 필요합니다:.*S203 family change refused/)).toBeVisible();
});

test('approval missing comparison has a focused recovery link',async({page,request,renderer,workspace,evidence})=>{
  expect((await request.post(`${renderer.origin}/api/project/create`,{data:{name:'S203 approval fixture',task:'classification'}})).ok()).toBe(true);
  expect((await request.put(`${renderer.origin}/api/project/update`,{data:{source_dataset_dir:workspace.dataset}})).ok()).toBe(true);
  await page.goto(renderer.url);await expect(page.getByTitle('프로젝트 관리', { exact: true }), 'the backend current project opens on load').toContainText('S203 approval fixture');
  await page.getByRole('navigation',{name:'Workflow Stages'}).getByRole('button').nth(3).click();
  await expect(page.getByRole('button',{name:'후보 승인 revision 저장',exact:true})).toBeDisabled();
  await expect(page.getByText('승인 보류: 저장된 두 모델 비교가 필요합니다.',{exact:true})).toBeVisible();
  await page.getByRole('button',{name:'평가·모델 비교 확인 (4단계)',exact:true}).click();
  await expect(page.getByLabel('저장된 후보 비교')).toBeFocused();
  await evidence.screenshot(page,'s203-approval-recovery');
});
