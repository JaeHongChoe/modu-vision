// Design 5.4-6 (f): step 1's '데이터 관리 도구' sheets open one at a time, like step 2's popovers.
const test = require('node:test'), assert = require('node:assert/strict'), fs = require('node:fs'), path = require('node:path'), Module = require('node:module'), ts = require('typescript');

function loadStore() {
  const filename = path.join(__dirname, '..', '..', 'stores', 'useStepSheetStore.ts'), m = new Module(filename, module);
  m.filename = filename; m.paths = Module._nodeModulePaths(path.dirname(filename));
  m._compile(ts.transpileModule(fs.readFileSync(filename, 'utf8'), {compilerOptions: {module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022}}).outputText, filename);
  return m.exports;
}

test('opening a sheet names it, and every other sheet closes when the name is not its own', () => {
  const {useStepSheetStore} = loadStore();
  assert.equal(useStepSheetStore.getState().open, null);
  useStepSheetStore.getState().show('capture');
  assert.equal(useStepSheetStore.getState().open, 'capture');
  for (const [file, name] of [['DatasetWorkflowPanel.tsx', 'workflow'], ['CaptureIntakePanel.tsx', 'capture'], ['DerivedDatasetPanel.tsx', 'derived']]) {
    const source = fs.readFileSync(path.join(__dirname, file), 'utf8');
    assert.match(source, new RegExp(`if\\(open\\)useStepSheetStore\\.getState\\(\\)\\.show\\('${name}'\\);`), file);
    assert.match(source, new RegExp(`if\\(open&&shownSheet&&shownSheet!=='${name}'`), file);
  }
  assert.match(fs.readFileSync(path.join(__dirname, 'DatasetWorkflowPanel.tsx'), 'utf8'), /shownSheet!=='workflow'&&!activeExport\.current\)setOpen\(false\)/,
    'a label export still running is not cancelled by opening another sheet');
});
