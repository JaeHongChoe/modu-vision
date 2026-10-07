import { expect, type Locator, type Page } from '@playwright/test';

export async function keyboardAction(page: Page, target: Locator, value = 'Enter') {
    // focus() itself does not wait for a disabled control. During draft save
    // it can leave focus on the image-picker opener and send Enter there.
    // Retry only the focus precondition; send the requested key exactly once.
    await expect.poll(async () => {
        if (!await target.isVisible() || !await target.isEnabled()) return false;
        await target.focus();
        return target.evaluate(element => element === document.activeElement && !element.matches(':disabled'));
    }).toBe(true);
    await page.keyboard.press(value);
}
