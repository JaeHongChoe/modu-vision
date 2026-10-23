// 한 장에 하나씩: with the option on, a finished box saves the image and moves to the next one, so labelling a detection
// image takes one drag instead of a drag and a next. Off by default; remembered on this device.
const test = require('node:test'), assert = require('node:assert/strict'), fs = require('node:fs'), path = require('node:path');
const read = name => fs.readFileSync(path.join(__dirname, name), 'utf8');

test('the store keeps the choice per device and counts each request to move on', () => {
  const store = read('../../stores/useAnnotationStore.ts');
  assert.match(store, /advanceOnBox: \(\(\) => \{ try \{ return typeof localStorage !== 'undefined' && localStorage.getItem\('modu-advance-on-box'\) === '1'; \} catch \{ return false; \} \}\)\(\)/, 'off unless the person turned it on');
  assert.match(store, /requestAdvance: \(\) => set\(\(state\) => \(\{ advanceRequest: state\.advanceRequest \+ 1 \}\)\)/);
});

test('only a finished box asks to move on, and the filmstrip moves once per request through its own forward step', () => {
  const canvas = read('LabelingCanvas.tsx');
  const box = canvas.slice(canvas.indexOf("type: isRotated ? 'rotated_bbox' : 'bbox'"), canvas.indexOf('bboxDraftRef.current = null;', canvas.indexOf("type: isRotated ? 'rotated_bbox' : 'bbox'")));
  assert.match(box, /addAnnotation\(newAnn\);\s*\/\/[^\n]*\n\s*if \(useAnnotationStore\.getState\(\)\.advanceOnBox\) useAnnotationStore\.getState\(\)\.requestAdvance\?\.\(\);/);
  assert.equal(canvas.match(/requestAdvance/g).length, 1, 'polygons, masks and other tools never move on by themselves');
  const strip = read('ImageFilmstrip.tsx');
  assert.match(strip, /if \(!advanceRequest \|\| advanceRequest === handledAdvance\.current\) return;\s*handledAdvance\.current = advanceRequest;\s*if \(canGoForward\) void goForward\(\);/);
  const selector = read('CategorySelector.tsx');
  assert.match(selector, /\{detection && setAdvanceOnBox && <label/);
  assert.match(selector, /그리면 다음 이미지로/);
});
