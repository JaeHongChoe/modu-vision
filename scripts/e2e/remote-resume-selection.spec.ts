import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import {test, expect} from './fixtures/test';

test('native selects the actually received terminal remote epoch state without submitting compute',
  {tag: ['@electron', '@owned-model']}, async ({electronSession, evidence}) => {
  const projectDir = process.env.MV_E2E_REMOTE_RESUME_PROJECT;
  const jobId = process.env.MV_E2E_REMOTE_RESUME_JOB;
  test.skip(!projectDir || !jobId, 'Requires an actually stopped owned remote worker with its verified epoch receipt');
  const directory = path.join(projectDir!, 'models', jobId!);
  const checkpoint = path.join(directory, 'latest_training_state.pt');
  const journalPath = path.join(directory, 'remote_job.json');
  const journal = JSON.parse(fs.readFileSync(journalPath, 'utf8'));
  const receipt = JSON.parse(fs.readFileSync(path.join(directory, 'training_state_receipt.json'), 'utf8'));
  const digest = (file: string) => crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex');
  const before = {checkpoint: digest(checkpoint), journal: digest(journalPath)};
  expect(journal.worker_exit_confirmed).toBe(true);
  expect(['aborted', 'completed']).toContain(journal.state);
  expect(receipt.sha256).toBe(before.checkpoint);
  const {window} = electronSession;
  const backend = await electronSession.waitForBackend();
  const api = (method: string, route: string, body?: any): Promise<any> => window.evaluate(async ({port, method, route, body}) => {
    const response = await fetch(`http://127.0.0.1:${port}${route}`, {method,
      ...(body === undefined ? {} : {headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)})});
    if (!response.ok) throw Error(`${response.status}: ${await response.text()}`);
    return response.json();
  }, {port: backend.port, method, route, body});
  const opened = await api('POST', '/api/project/open', {project_dir: projectDir});
  await api('POST', '/api/compute/profiles', journal.profile);
  await api('PUT', '/api/compute/selection', {compute_profile_id: journal.profile.id});
  const available = await api('GET', '/api/training/resume-states?' + new URLSearchParams({dataset_path: opened.source_dataset_dir, task: 'classification', compute_profile_id: journal.profile.id}));
  const state = available.states.find((row: any) => row.checkpoint_path === checkpoint);
  expect(state).toBeTruthy();
  expect(state.next_epoch).toBeGreaterThan(0);
  expect(state.global_step).toBeGreaterThan(0);
  await window.reload();
  await expect(window.getByTitle('프로젝트 관리', {exact: true})).toContainText(opened.name);
  await window.getByRole('navigation', {name: 'Workflow Stages'}).getByRole('button').nth(2).click();
  const choice = window.getByRole('combobox', {name: '정확한 학습 이어가기', exact: true});
  await expect(choice.locator('option').filter({hasText: `Step ${state.global_step}`})).toHaveCount(1);
  await window.getByRole('button', {name: '연결 검사', exact: true}).click();
  await expect(window.locator("#workflow-training").getByText(/준비 완료 ·/)).toBeVisible({timeout: 60_000});
  await choice.selectOption(checkpoint);
  await expect(choice).toHaveValue(checkpoint);
  await expect(window.getByText(`저장된 학습 설정 사용 · 전체 ${state.recipe.epochs} Epoch · 마지막 완료 Step ${state.global_step}`, {exact: true})).toBeVisible();
  await expect(window.getByRole('button', {name: '선택 설정으로 학습 시작', exact: true})).toBeEnabled();
  await expect(window.getByRole('combobox', {name: '학습 모델 구조', exact: true})).toBeDisabled();
  await evidence.screenshot(window, 'received-remote-epoch-state-native-selection');
  expect({checkpoint: digest(checkpoint), journal: digest(journalPath)}).toEqual(before);
  evidence.note('remote_epoch_selection', {actual_received_remote_state: true, worker_exit_confirmed: true,
    job_id: jobId, checkpoint_sha256: before.checkpoint, receipt,
    native_selection: true, submitted_remote_compute: false, model_quality_approval: false});
});
