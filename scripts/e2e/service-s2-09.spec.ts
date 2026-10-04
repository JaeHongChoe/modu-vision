import fs from 'node:fs';
import path from 'node:path';
import { randomUUID } from 'node:crypto';
import { expect, test } from './fixtures/test';

// S2-09: a job reads the same way in the task center and in its model family's workbench. Actual renderer and backend;
// the job record is written as an app restart leaves it (a running record no process owns). No training runs.
test('a job left running by a restart reads as interrupted in the task center and in its workbench, with the same next action', async ({ page, request, renderer, workspace, evidence }) => {
  expect((await request.post(`${renderer.origin}/api/project/create`, { data: { name: 'S209 interrupted job', task: 'detection' } })).ok()).toBe(true);
  expect((await request.put(`${renderer.origin}/api/project/update`, { data: { source_dataset_dir: workspace.dataset } })).ok()).toBe(true);
  const project = await (await request.get(`${renderer.origin}/api/project/current`)).json();
  const jobId = randomUUID().replace(/-/g, '');
  const folder = path.join(project.models_dir, 'rotated_detection', jobId);
  fs.mkdirSync(folder, { recursive: true });
  fs.writeFileSync(path.join(folder, 'job_state.json'), JSON.stringify({
    job_id: jobId, status: 'running', epochs_completed: 2, total_epochs: 5, started_at: Date.now() / 1000, result: null, error: null,
    training_provenance: { labelset_id: 'default' }, dataset_path: path.join(project.project_dir, 'rotated-input'),
    source_dataset_path: project.source_dataset_dir, device: 'cpu', warm_start: null,
  }));

  await page.goto(renderer.url);
  await expect(page.getByTitle('프로젝트 관리', { exact: true })).toContainText('S209 interrupted job');
  await page.getByRole('button', { name: '작업 센터', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: '작업 센터' });
  const saved = dialog.getByRole('combobox', { name: '저장 작업 다시 열기' });
  const option = saved.locator('option', { hasText: '실행 주체 없음 · 재개 확인 필요' });
  await expect(option).toHaveCount(1, { timeout: 15000 });
  await saved.selectOption((await option.getAttribute('value'))!);
  const status = dialog.getByRole('status').filter({ hasText: '실행 주체 없음 · 재개 확인 필요' });
  await expect(status).toContainText('기록된 원인: Application stopped before training completed');
  await expect(status.getByRole('button', { name: '취소 요청' }), 'an interrupted job has nothing left to cancel').toHaveCount(0);
  // the task center reads the job the way the workbench does: family, progress and next action
  await expect(status).toContainText('회전 객체');
  await expect(status).toContainText('Epoch 2/5');
  await expect(status).toContainText('다음 행동:');
  await expect(status).toContainText('다시 학습하세요');
  await evidence.screenshot(page, 's209-task-center-interrupted');

  await status.getByRole('button', { name: '학습 화면으로 이동' }).click();
  await expect(dialog).not.toBeVisible();
  const view = page.getByRole('status', { name: '학습 작업 상태' });
  await expect(view).toContainText('실행 주체 없음 · 재개 확인 필요', { timeout: 15000 });
  await expect(view).toContainText('epoch 2/5');
  await expect(view).toContainText('이 컴퓨터');
  await expect(view).toContainText('다음 행동:');
  await expect(view).toContainText('다시 학습하세요');
  await expect(view.getByRole('button')).toHaveCount(0);
  await expect(view.getByRole('alert')).toContainText('Application stopped before training completed');
  await view.scrollIntoViewIfNeeded();
  await evidence.screenshot(page, 's209-rotated-workbench-interrupted');
  evidence.note('scope', { actual_renderer: true, actual_backend: true, training: false, remote_server: false, record: 'running job_state.json without an owning process' });
});

// The server is stubbed here (no server is contacted): its job API answers as a server whose connection dropped and came
// back. Everything else is the actual renderer and backend.
test('a saved stop whose server observation is refused stays unconfirmed without offering a second cancel',async({page,request,renderer,workspace,evidence})=>{
 expect((await request.post(`${renderer.origin}/api/project/create`,{data:{name:'S209 refused observation',task:'detection'}})).ok()).toBe(true);
 expect((await request.put(`${renderer.origin}/api/project/update`,{data:{source_dataset_dir:workspace.dataset}})).ok()).toBe(true);
 const next='중지 요청은 저장됐지만 작업자에게 전달되거나 종료된 것이 확인되지 않았습니다. 연결을 복구하면 같은 작업을 다시 확인합니다. 장비 예약은 유지됩니다.';
 const detail='The cancel intent was saved, but delivery and worker exit are unconfirmed: the job manager could not observe this job again.';
 let pending=false;const calls:string[]=[];
 const reply=()=>({job_id:'exec-refused',model_id:'model-refused',execution_job_id:'exec-refused',compute_profile_id:'synthetic-server',status:'disconnected',epoch:1,epochs:4,
  source_dataset_path:workspace.dataset,dataset_path:workspace.dataset,training_provenance:{labelset_id:'default'},
  ...(pending?{error:{message:detail},observation:{cause:'cancel_unconfirmed',next_action:next,cancel:{stage:'requested',requested_at:1,complete:false,exit_confirmed:null,reservation_released:false}}}:{})});
 await page.route('**/api/enhancement/jobs',r=>r.fulfill({json:{jobs:[{...reply(),job_id:'model-refused'}]}}));
 await page.route('**/api/compute/jobs/exec-refused',r=>r.fulfill({json:reply()}));
 await page.route('**/api/compute/jobs/exec-refused/cancel',r=>{calls.push('cancel');pending=true;return r.fulfill({status:409,json:{detail}});});
 await page.route('**/api/compute/jobs/exec-refused/reconnect',r=>{calls.push('reconnect');return r.fulfill({status:409,json:{detail:'The job manager could not reconnect this job'}});});
 await page.goto(renderer.url);await expect(page.getByTitle('프로젝트 관리',{exact:true})).toContainText('S209 refused observation');
 await page.getByRole('navigation',{name:'Workflow Stages'}).getByRole('button').nth(2).click();
 await page.getByRole('region',{name:'모델 학습 허브'}).getByRole('button',{name:/^이미지 개선/}).click();
 const view=page.getByRole('status',{name:'학습 작업 상태'});await expect(view).toContainText('연결 끊김 · 상태 미확인');
 await view.getByRole('button',{name:'취소 요청',exact:true}).click();
 await expect(page.getByRole('alert').filter({hasText:'cancel intent was saved'})).toBeVisible();
 await expect(view).toContainText('작업자에게 전달되거나 종료된 것이 확인되지 않았습니다.',{timeout:15_000});
 await expect(view.getByRole('button',{name:'취소 요청',exact:true})).toHaveCount(0);
 await expect(view.getByRole('button',{name:'종료 확인 중',exact:true})).toBeDisabled();
 await expect(view.getByLabel('취소 확인 단계')).toContainText('○ 예약 반환');
 await view.getByRole('button',{name:'같은 서버 작업 재연결',exact:true}).click();
 await expect(page.getByRole('alert').filter({hasText:'could not reconnect'})).toBeVisible();expect(calls).toEqual(['cancel','reconnect']);
 await view.scrollIntoViewIfNeeded();await evidence.screenshot(page,'s209-refused-stop-observation');
 evidence.note('scope',{actual_renderer:true,actual_backend:true,server_job_api:'stubbed',server:false,training:false,calls,cancel_delivered:false,reservation_release_claimed:false});
});
for (const family of [
  { button: '회전 객체 검출', list: '**/api/rotated-detection/jobs', row: (source: string) => ({ job_id: 'model-r', execution_job_id: 'exec-r', compute_profile_id: 'gpu-lab', status: 'disconnected', epochs_completed: 1, total_epochs: 4, result: null, error: null, training_provenance: { labelset_id: 'default' }, source_dataset_path: source, dataset_path: source }) },
  { button: '이미지 개선', list: '**/api/enhancement/jobs', row: (source: string) => ({ job_id: 'model-r', execution_job_id: 'exec-r', compute_profile_id: 'gpu-lab', status: 'disconnected', epoch: 1, epochs: 4, error: null, training_provenance: { labelset_id: 'default' }, source_dataset_path: source, dataset_path: source }) },
]) for (const order of ['reconnect first', 'cancel while disconnected'] as const) test(`${family.button}: a disconnected server job is reopened and, ${order}, handled through the server job API`, async ({ page, request, renderer, workspace, evidence }) => {
  expect((await request.post(`${renderer.origin}/api/project/create`, { data: { name: `S209 server job ${family.button} ${order}`, task: 'detection' } })).ok()).toBe(true);
  expect((await request.put(`${renderer.origin}/api/project/update`, { data: { source_dataset_dir: workspace.dataset } })).ok()).toBe(true);
  const project = await (await request.get(`${renderer.origin}/api/project/current`)).json();
  let server = 'disconnected';
  const calls: string[] = [];
  const reply = () => ({ job_id: 'exec-r', execution_job_id: 'exec-r', model_id: 'model-r', compute_profile_id: 'gpu-lab', status: server, current_epoch: 1, total_epochs: 4, training_provenance: { labelset_id: 'default' }, source_dataset_path: project.source_dataset_dir });
  await page.route(family.list, route => route.fulfill({ json: { jobs: [family.row(project.source_dataset_dir)] } }));
  let reads = 0;
  await page.route('**/api/compute/jobs/exec-r', route => { reads++; return route.fulfill({ json: reply() }); });
  await page.route('**/api/compute/jobs/exec-r/reconnect', route => { calls.push('reconnect'); server = 'running'; return route.fulfill({ json: reply() }); });
  await page.route('**/api/compute/jobs/exec-r/cancel', route => { calls.push('cancel'); server = 'stopping'; return route.fulfill({ json: reply() }); });

  await page.goto(renderer.url);
  await expect(page.getByTitle('프로젝트 관리', { exact: true })).toContainText(`S209 server job ${family.button} ${order}`);
  await page.getByRole('navigation', { name: 'Workflow Stages' }).getByRole('button').nth(2).click();
  await page.getByRole('region', { name: '모델 학습 허브' }).getByRole('button', { name: new RegExp(`^${family.button}`) }).click();
  const view = page.getByRole('status', { name: '학습 작업 상태' });
  await expect(view, 'a job whose connection dropped is reopened').toContainText('연결 끊김 · 상태 미확인', { timeout: 15000 });
  await expect(view).toContainText('연결을 복구하면 같은 작업을 다시 관찰합니다');
  if (order === 'cancel while disconnected') {
    await expect.poll(() => reads, { timeout: 15000, message: 'a disconnected job keeps being read' }).toBeGreaterThanOrEqual(2);
    await view.getByRole('button', { name: '취소 요청' }).click();
    await expect(view).toContainText('취소 요청 · 종료 확인 중');
    await expect(view.getByRole('button', { name: '종료 확인 중' })).toBeDisabled();
    expect(calls).toEqual(['cancel']);
    return;
  }
  await view.getByRole('button', { name: '같은 서버 작업 재연결' }).click();
  await expect(view).toContainText('실행 중');
  await expect(view.getByRole('button', { name: '같은 서버 작업 재연결' })).toHaveCount(0);
  await view.getByRole('button', { name: '취소 요청' }).click();
  await expect(view).toContainText('취소 요청 · 종료 확인 중');
  await expect(view.getByRole('button', { name: '종료 확인 중' })).toBeDisabled();
  expect(calls).toEqual(['reconnect', 'cancel']);
  await view.scrollIntoViewIfNeeded();
  await evidence.screenshot(page, `s209-server-job-${family.list.includes('rotated') ? 'rotated' : 'enhancement'}`);
  evidence.note('scope', { actual_renderer: true, actual_backend: true, server_job_api: 'stubbed', training: false });
});
