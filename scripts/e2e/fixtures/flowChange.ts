import type { Page } from '@playwright/test';

/** E04: saving a flow shows the inspection-rule difference and asks for a reason before anything is saved. */
export async function confirmFlowSave(page: Page, reason: string): Promise<void> {
  const dialog = page.getByRole('dialog', { name: '검사 규칙 변경 저장' });
  await dialog.getByLabel('변경 사유').fill(reason);
  await dialog.getByRole('button', { name: '변경 저장', exact: true }).click();
  await dialog.waitFor({ state: 'detached' });
}
