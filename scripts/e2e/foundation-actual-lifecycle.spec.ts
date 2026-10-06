import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import {execFileSync} from 'node:child_process';
import {test, expect} from './fixtures/test';
const harness = require('./fixtures/harness.cjs');
const sha = (file: string) => crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex');

test('native authentic SAM2 grounding and DINO examples generate reject reopen and cancel',
  {tag: ['@electron', '@owned-model']}, async ({electronSession, workspace, evidence}) => {
  test.setTimeout(420_000);
  const sam = process.env.MV_E2E_SAM2_MODEL_DIR, grounding = process.env.MV_E2E_GROUNDING_MODEL_DIR;
  const dino = process.env.MV_E2E_DINO_WEIGHTS;
  test.skip(!sam || !grounding || !dino, 'Requires explicitly supplied authentic local SAM2 Grounding DINO and DINO weights');
  const {window} = electronSession, backend = await electronSession.waitForBackend();
  const api = (method: string, route: string, body?: any): Promise<any> => window.evaluate(async ({port, method, route, body}) => {
    const response = await fetch(`http://127.0.0.1:${port}${route}`, {method,
      ...(body === undefined ? {} : {headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)})});
    if (!response.ok) throw Error(`${response.status}: ${await response.text()}`);
    return response.json();
  }, {port: backend.port, method, route, body});
  const fixture = JSON.parse(execFileSync(harness.resolvePython(), [path.join(harness.REPO_ROOT,
    'scripts/e2e/fixtures/foundation_prompt_data.py'), workspace.root], {encoding: 'utf8', timeout: 15_000}));
  await api('POST', '/api/project/create', {name: 'Authentic native prompt lifecycle', task: 'segmentation'});
  const project = await api('PUT', '/api/project/update', {source_dataset_dir: fixture.source});
  await api('POST', '/api/dataset/import', {folder_path: fixture.source, task: 'segmentation'});
  const setup = await api('PUT', '/api/label-candidates/setup', {mask_model_dir: sam, model_dir: grounding,
    feature_backbone: 'dinov3_vits16', feature_checkpoint: dino, feature_sha256: sha(dino!)});
  expect(setup.providers.foundation.ready).toBe(true);
  expect(setup.providers.grounding_dino.ready).toBe(true);
  const open = async () => {
    await window.reload();
    await expect(window.getByTitle('프로젝트 관리', {exact: true})).toContainText(project.name);
    await window.getByRole('navigation', {name: 'Workflow Stages'}).getByRole('button').nth(0).click();
    await window.getByRole('button', {name: 'part-0.png 라벨링에서 열기', exact: true}).click();
    await window.getByRole('button', {name: '모델 보조 라벨링 패널', exact: true}).click();
  };
  await open();
  const panel = window.getByRole('region', {name: 'SAM2 기반 라벨링', exact: true});
  const review = window.getByRole('complementary', {name: '모델 보조 라벨링 검토', exact: true});
  await panel.locator('summary').filter({hasText: '이미지 영역 예시'}).click();
  await panel.getByLabel('예시 이미지 선택', {exact: true}).selectOption(fixture.files[1].path);
  await panel.getByLabel('예시 이미지 영역', {exact: true}).fill('50,35,190,160');
  await panel.getByRole('button', {name: 'positive 예시 추가', exact: true}).click();
  await panel.getByLabel('예시 이미지 선택', {exact: true}).selectOption(fixture.files[2].path);
  await panel.getByLabel('예시 이미지 영역', {exact: true}).fill('0,0,40,30');
  await panel.getByRole('button', {name: 'negative 예시 추가', exact: true}).click();
  const prompt = '표면의 검은 작은 결함을 찾되 정상 영역은 제외합니다. ' + 'white rectangle. black circle. '.repeat(12);
  await panel.getByLabel('텍스트 검출 프롬프트', {exact: true}).fill(prompt);
  await panel.getByLabel('후보 점수', {exact: true}).fill('0');
  await panel.getByLabel('텍스트 점수', {exact: true}).fill('0.1');
  await panel.getByLabel('최대 후보', {exact: true}).fill('2');
  const generatedResponse = window.waitForResponse(r => new URL(r.url()).pathname === '/api/label-candidates/generate'
    && r.request().method() === 'POST', {timeout: 180_000});
  await panel.getByRole('button', {name: '현재 이미지 SAM2 후보 생성', exact: true}).click();
  const response = await generatedResponse;
  expect(response.ok(), await response.text()).toBe(true);
  const generated = await response.json(), posted = response.request().postDataJSON();
  expect(posted.prompt).toBe(prompt);
  expect(posted.positive_examples).toHaveLength(1); expect(posted.negative_examples).toHaveLength(1);
  expect(generated.status).toBe('pending'); expect(generated.candidates.length).toBeGreaterThan(0);
  expect(generated.candidates.every((row: any) => row.provenance?.feature_metadata?.pretrained_sha256 === sha(dino!))).toBe(true);
  await expect(review).toContainText('실제 추론 후보를 만들었습니다', {timeout: 15_000});
  await evidence.screenshot(window, 'native-authentic-long-prompt-and-image-examples');
  await review.getByLabel('후보 검토자 이름', {exact: true}).fill('Automated control rejection');
  await review.getByRole('button', {name: '거절', exact: true}).click();
  await expect(review).toContainText('거절 완료');
  const rejected = await api('GET', '/api/label-suggestions/' + generated.id);
  expect(rejected.status).toBe('rejected');
  await open(); await expect(review).toContainText('거절 완료');
  await panel.getByLabel('텍스트 검출 프롬프트', {exact: true}).fill(prompt);
  await panel.getByLabel('후보 점수', {exact: true}).fill('0');
  await panel.locator('summary').filter({hasText: '키워드·예시 이미지 일괄 후보와 저장 기록'}).click();
  await panel.getByRole('button', {name: '가져온 이미지 전체 선택', exact: true}).click();
  const acceptedResponse = window.waitForResponse(r => new URL(r.url()).pathname === '/api/label-candidates/batches'
    && r.request().method() === 'POST');
  await panel.getByRole('button', {name: '비동기 일괄 후보 시작', exact: true}).click();
  const accepted = await acceptedResponse; expect(accepted.ok(), await accepted.text()).toBe(true);
  const batch = await accepted.json();
  await panel.getByRole('button', {name: '배치 취소', exact: true}).click();
  let stopped: any;
  await expect.poll(async () => {stopped = await api('GET', '/api/label-candidates/batches/' + batch.id);
    return stopped.status;}, {timeout: 90_000}).toBe('stopped');
  await expect(panel).toContainText('취소됨', {timeout: 15_000});
  await evidence.screenshot(window, 'native-authentic-batch-cancelled');
  await panel.locator('summary').filter({hasText: 'few-label 학습·refine'}).click();
  await panel.getByLabel('few-label epochs', {exact: true}).fill('2');
  const train = async (button: string) => {
    const pending = window.waitForResponse(r => new URL(r.url()).pathname === '/api/label-suggestions/feature-train'
      && r.request().method() === 'POST');
    await panel.getByRole('button', {name: button, exact: true}).click();
    const accepted = await pending; expect(accepted.ok(), await accepted.text()).toBe(true);
    const job = await accepted.json(); let measured: any;
    await expect.poll(async () => {measured = await api('GET', '/api/label-suggestions/feature-train/' + job.id);
      if (measured.status === 'failed') throw Error(JSON.stringify(measured));
      return measured.status;}, {timeout: 90_000}).toBe('completed');
    await expect(panel.getByLabel('few-label 모델 선택', {exact: true})).toHaveValue(measured.model_id, {timeout: 15_000});
    return measured;
  };
  const fitted = await train('새 분류기 학습');
  const firstModel = (await api('GET', '/api/label-suggestions/feature-models')).models.find((row: any) => row.id === fitted.model_id);
  const originalClassifier = sha(firstModel.checkpoint_path);
  expect(firstModel.classes).toEqual(['__background__', 'defect']);
  expect(firstModel.feature_metadata.pretrained_sha256).toBe(sha(dino!));
  const refined = await train('선택 모델 refine');
  const models = (await api('GET', '/api/label-suggestions/feature-models')).models;
  expect(models).toHaveLength(2);
  expect(models.find((row: any) => row.id === refined.model_id).parent_model_id).toBe(firstModel.id);
  expect(refined.model_id).not.toBe(fitted.model_id);
  expect(sha(firstModel.checkpoint_path)).toBe(originalClassifier);
  await evidence.screenshot(window, 'native-authentic-few-label-refine');
  for (const file of [...fixture.files, ...fixture.labels]) {expect(sha(file.path)).toBe(file.sha256); evidence.addFile(file.path);}
  evidence.note('native_authentic_prompt_lifecycle', {project, fixture, setup, generated, posted, rejected, batch, stopped, fitted, refined, models,
    actual_sam2_grounding_dino_dino_examples: true, actual_native_controls: true, source_unchanged: true,
    candidate_rejection_control: true, Korean_semantic_quality_approved: false, annotation_quality_approval: false});
});
