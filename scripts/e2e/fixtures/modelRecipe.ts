import {expect, type Locator, type Page} from '@playwright/test';

type TrainingNavigationOptions = {deadline?: number};

// An existing fixed-frame case passes its absolute monotonic deadline. Each
// actual UI action consumes that same frame; this helper creates no new budget.
function navigationOptions(options: TrainingNavigationOptions) {
  if (options.deadline === undefined) return {};
  const remaining = Math.floor(options.deadline - performance.now());
  if (remaining <= 0) throw new Error('Original training navigation frame expired');
  return {timeout: remaining};
}

/** Open the model block through the same visible tab a user selects. */
export async function openTrainingModelBlock(page: Page, options: TrainingNavigationOptions = {}): Promise<void> {
  const screen = page.locator('#workflow-training');
  await expect(screen).toBeVisible(navigationOptions(options));
  const tab = screen.getByRole('tablist', {name: '학습 데이터 준비 단계', exact: true})
    .getByRole('tab', {name: /^모델과 학습/});
  if (await tab.getAttribute('aria-selected', navigationOptions(options)) !== 'true') {
    await tab.click(navigationOptions(options));
  }
  await expect(screen.getByRole('region', {name: '모델 학습 허브', exact: true}))
    .toBeVisible(navigationOptions(options));
}

/** Reveal advanced new-job settings without changing a setting or starting work. */
export async function openTrainingCoreSettings(page: Page, options: TrainingNavigationOptions = {}): Promise<void> {
  await openTrainingModelBlock(page, options);
  const summary = page.locator('#workflow-training').locator('summary').filter({hasText: /^고급 학습 설정/});
  if (await summary.locator('..').getAttribute('open', navigationOptions(options)) === null) {
    await summary.click(navigationOptions(options));
  }
  await expect(page.getByRole('combobox', {name: '학습 모델 구조', exact: true}))
    .toBeVisible(navigationOptions(options));
}

export function trainingFamilyOption(page: Page, family: string): Locator {
  if (!new Set(['classification', 'segmentation', 'detection', 'anomaly', 'patch_classification',
    'ocr', 'rotated_detection', 'defect_gan', 'enhancement', 'rotation']).has(family)) {
    throw new Error('Unknown training family: ' + family);
  }
  return page.locator('#workflow-training').getByRole('region', {name: '모델 학습 허브', exact: true})
    .locator(`input[type="radio"][value="${family}"]`);
}

/** Click a real card. The caller still handles/validates any task-impact dialog
 * and checks the committed selection or its refusal; no selection is forced. */
export async function selectTrainingFamily(page: Page, family: string, options: TrainingNavigationOptions = {}): Promise<void> {
  await openTrainingModelBlock(page, options);
  const hub = page.getByRole('region', {name: '모델 학습 허브', exact: true});
  const summary = hub.locator('summary').filter({hasText: /^모델 종류 (바꾸기|선택)/});
  if (await summary.locator('..').getAttribute('open', navigationOptions(options)) === null) {
    await summary.click(navigationOptions(options));
  }
  await trainingFamilyOption(page, family).click(navigationOptions(options));
}

/** Observe the persisted execution recipe the renderer actually submits.
 * Match the exact family, action and completed model so a different job's
 * evaluation cannot satisfy the qualification. No endpoint is mocked. */
export function modelRecipeResponse(page:Page,task:string,stage:string,jobId:string) {
  return page.waitForResponse(response=>{
    const request=response.request();
    if(new URL(response.url()).pathname!=='/api/model-execution/recipes'||request.method()!=='POST')return false;
    const body=request.postDataJSON();
    return body?.task===task&&body?.stage===stage&&body?.params?.job_id===jobId
      &&body?.execution_target==='local'&&body?.device==='cpu';
  });
}
