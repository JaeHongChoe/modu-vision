// Test helper: plainLabels.ts compiled for the module stubs of renderer regression files that load components alone.
const fs = require('node:fs'), path = require('node:path'), Module = require('node:module'), ts = require('typescript');
const file = path.join(__dirname, 'plainLabels.ts'), compiled = new Module(file, module);
compiled.filename = file;
compiled._compile(ts.transpileModule(fs.readFileSync(file, 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText, file);
module.exports = compiled.exports;
