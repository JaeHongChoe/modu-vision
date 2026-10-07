import { test, expect } from './fixtures/test';
import { keyboardAction } from './fixtures/keyboard-action';

test('keyboard activation waits for a saving-disabled target instead of activating its opener', async ({ page, evidence }) => {
    await page.setContent(`
        <button id="opener" onclick="document.querySelector('#wrong').textContent = String(Number(document.querySelector('#wrong').textContent) + 1)">Image picker</button>
        <button id="target" disabled onclick="document.querySelector('#added').textContent = String(Number(document.querySelector('#added').textContent) + 1)">Fixed ROI</button>
        <output id="wrong">0</output><output id="added">0</output>
    `);
    const opener = page.getByRole('button', { name: 'Image picker', exact: true });
    const target = page.getByRole('button', { name: 'Fixed ROI', exact: true });
    await opener.focus();
    await expect(opener).toBeFocused();
    await expect(target).toBeDisabled();
    // Keep the save gate closed even if the keyboard call returns early. The
    // controlled release permits one real key activation, never a retry.
    const activation = keyboardAction(page, target);
    const early = await Promise.race([
        activation.then(() => true),
        new Promise<false>(resolve => setTimeout(() => resolve(false), 250)),
    ]);
    await page.evaluate(() => { (document.querySelector('#target') as HTMLButtonElement).disabled = false; });
    await activation;
    expect(early, 'a key must not be sent to the previously focused opener while the target is disabled').toBe(false);
    await expect(page.locator('#wrong')).toHaveText('0');
    await expect(page.locator('#added')).toHaveText('1');
    await expect(target).toBeFocused();
    await evidence.screenshot(page, 'saving-disabled-keyboard-activation');
    evidence.note('saving_keyboard_precondition', {
        controlled_disabled_target: true, exact_key_activations: 1,
        opener_activated: false, product_flow_execution: false,
    });
});
