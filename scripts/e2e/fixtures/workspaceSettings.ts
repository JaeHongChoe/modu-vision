import type {Page} from '@playwright/test';

/** Open the real utility menu before using a header control, in either workspace view or language. */
export async function openWorkspaceSettings(page:Page):Promise<void> {
  const summary=page.locator('summary').filter({hasText:/^(설정·도구|Settings & tools)$/});
  const menu=summary.locator('..');
  if(await menu.getAttribute('open')===null) await summary.click();
}
