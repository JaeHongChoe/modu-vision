// 5.4 item 6 (d): a panel's explanation reads as one line, the rest behind 자세히.
const test = require('node:test'), assert = require('node:assert/strict'), fs = require('node:fs'), path = require('node:path'), Module = require('node:module'), ts = require('typescript');

function load() {
  const filename = path.join(__dirname, 'Explain.tsx'), m = new Module(filename, module);
  m.filename = filename;
  m.require = name => name === 'react/jsx-runtime' ? {jsx: (type, props) => ({type, props}), jsxs: (type, props) => ({type, props})} : require(name);
  m._compile(ts.transpileModule(fs.readFileSync(filename, 'utf8'), {compilerOptions: {module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX}}).outputText, filename);
  return m.exports;
}

test('the first sentence stands alone and the rest goes behind 자세히; one sentence stays a plain line', () => {
  const {splitExplanation, Explain} = load();
  assert.deepEqual(splitExplanation('한 페이지는 한 묶음입니다. 타일을 눌러 빼거나 넣습니다. 열기는 한 장씩 봅니다.'),
    {first: '한 페이지는 한 묶음입니다.', rest: '타일을 눌러 빼거나 넣습니다. 열기는 한 장씩 봅니다.'});
  assert.deepEqual(splitExplanation('  한 문장뿐입니다.  '), {first: '한 문장뿐입니다.', rest: ''});
  const long = Explain({text: '첫 문장입니다. 나머지입니다.'});
  assert.equal(long.type, 'details');
  const plain = Explain({text: '한 문장뿐입니다.'});
  assert.equal(plain.type, 'p');
});
