/** Test runner fallback for real repo TS dependencies of manually compiled fixtures.
 * Fixture-specific m.require mocks run before Node reaches these extension handlers. */
const fs = require('node:fs');
const path = require('node:path');
const ts = require('typescript');
const sourceRoot = fs.realpathSync(path.join(__dirname, '..', 'src')) + path.sep;
for (const extension of ['.ts', '.tsx']) {
  require.extensions[extension] = (loaded, filename) => {
    const resolved = fs.realpathSync(filename);
    if (!resolved.startsWith(sourceRoot) || resolved.split(path.sep).includes('node_modules')) {
      const error = new Error('Test TypeScript dependency is outside repository sources');
      error.code = 'TEST_TS_DEPENDENCY_OUT_OF_SCOPE';
      throw error;
    }
    const result = ts.transpileModule(fs.readFileSync(resolved, 'utf8'), {
      fileName: resolved,
      compilerOptions: {module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022,
        jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true},
    });
    loaded._compile(result.outputText, filename);
  };
}
