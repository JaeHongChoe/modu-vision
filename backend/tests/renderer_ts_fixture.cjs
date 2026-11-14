/** Test-only CommonJS loader for renderer TypeScript and its real runtime dependencies.
 * Each fixture gets one module cache, so api.ts and consumers observe the same authority/trace state.
 * Relative TS imports are transpiled; package and builtin imports use Node's ordinary require.
 * No global require hooks, SDK mocks, services, or production module changes are installed.
 */
const fs = require('node:fs');
const path = require('node:path');
const Module = require('node:module');
const ts = require('typescript');

function createRendererLoader(root) {
  const cache = new Map();
  function load(relative) {
    const filename = path.resolve(root, relative);
    if (cache.has(filename)) return cache.get(filename).exports;
    const loaded = new Module(filename, module);
    loaded.filename = filename;
    loaded.paths = Module._nodeModulePaths(path.dirname(filename));
    const nativeRequire = loaded.require.bind(loaded);
    loaded.require = request => {
      if (request.startsWith('.')) {
        const base = path.resolve(path.dirname(filename), request);
        const dependency = [base, `${base}.ts`, `${base}.tsx`, path.join(base, 'index.ts')]
          .find(candidate => /\.tsx?$/.test(candidate) && fs.existsSync(candidate) && fs.statSync(candidate).isFile());
        if (dependency) return load(dependency);
      }
      return nativeRequire(request);
    };
    // Publish before compilation to preserve CommonJS identity even for a circular import.
    cache.set(filename, loaded);
    try {
      loaded._compile(ts.transpileModule(fs.readFileSync(filename, 'utf8'), {
        compilerOptions: {module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022},
      }).outputText, filename);
      loaded.loaded = true;
      return loaded.exports;
    } catch (error) {
      cache.delete(filename);
      throw error;
    }
  }
  return load;
}
module.exports = {createRendererLoader};
