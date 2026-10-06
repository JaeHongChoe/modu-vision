import {test,expect} from './test';
test('closed owned native window refuses backend status immediately',{tag:'@electron'},async({electronSession,evidence})=>{
  test.setTimeout(60_000);
  await electronSession.waitForBackend();
  await evidence.screenshot(electronSession.window,'owned-window-before-controlled-close');
  await electronSession.app.evaluate(({BrowserWindow})=>BrowserWindow.getAllWindows()[0].close());
  await expect.poll(()=>electronSession.window.isClosed()).toBe(true);
  const started=Date.now();
  await expect(electronSession.waitForBackend(3_000)).rejects.toThrow(/Native application window closed/);
  expect(Date.now()-started).toBeLessThan(2_000);
  evidence.note('controlled_closed_native_window',{actual_native_window_closed:true,no_status_retry:true});
});
