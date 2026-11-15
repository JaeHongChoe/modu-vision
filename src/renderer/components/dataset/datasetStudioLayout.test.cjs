const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const studio = fs.readFileSync(path.join(__dirname, 'DatasetStudio.tsx'), 'utf8');

test('data workspace gives the gallery flexible height and a named bounded management disclosure', () => {
  assert.match(studio, /ref=\{bodyRef\} className="flex-1 flex flex-col min-h-0 overflow-hidden" data-dataset-work-area/);
  assert.match(studio, /<details[^>]+max-h-\[45%\][^>]+data-dataset-management open=\{full\}/);
  assert.match(studio, /<main data-dataset-gallery className="flex-1 min-h-0 min-w-0/);
  assert.doesNotMatch(studio, /w-\[21rem\]/, 'no permanent statistics sidebar narrows the gallery');
});

test('gallery preserves natural image ratios and shows complete uncropped thumbnails', () => {
  assert.match(studio, /aspectRatio: w > 0 && h > 0/);
  assert.match(studio, /className="w-full h-full object-contain"/);
  assert.doesNotMatch(studio, /aspect-square|object-cover/);
  assert.match(studio, /useState<'S' \| 'M' \| 'L'>\('L'\)/);
});

test('full view retains explicit focus preference while simple management remains reachable', () => {
  assert.match(studio, /const focus = focusChoice \?\? \(autoFolded && !full\);/);
  assert.match(studio, /\{\(!focus \|\| simpleView\) && <>/);
  assert.match(studio, /aria-pressed=\{focus\} onClick=\{toggleFocus\}/);
  for (const token of ['modu-stage-header', 'modu-stage-toolbar', 'modu-stage-summary', 'modu-stage-disclosure']) assert.ok(studio.includes(token));
});

test('photo preparation keeps field deployment and specialist guidance in mounted management tools', () => {
 const management=studio.slice(studio.indexOf('data-dataset-management'),studio.indexOf('<main data-dataset-gallery'));
 for(const component of ['<FieldReadinessPanel />','<SpecialistFamilyNote step={1} />']) assert.ok(management.includes(component),component+' remains mounted in management');
 const gallery=studio.slice(studio.indexOf('<main data-dataset-gallery'));
 assert.ok(!gallery.includes('<FieldReadinessPanel />')&&!gallery.includes('<SpecialistFamilyNote step={1} />'),'deployment guidance does not displace the photo gallery');
 assert.match(gallery,/<p className="sr-only" title=\{folderPath\}>/,'redundant source counters stay available to assistive technology');
 assert.match(gallery,/data-dataset-check-summary/);assert.match(gallery,/data-dataset-checklist/);
});
