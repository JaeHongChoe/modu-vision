const assert = require('node:assert/strict');
const fs = require('node:fs');
const Module = require('node:module');
const os = require('node:os');
const path = require('node:path');
const test = require('node:test');
const ts = require('typescript');

const sourcePath = path.resolve(__dirname, '../src/main/instanceLock.ts');
const sourceModule = new Module(sourcePath, module);
sourceModule.filename = sourcePath;
sourceModule.paths = Module._nodeModulePaths(path.dirname(sourcePath));
const source = fs.readFileSync(sourcePath, 'utf8');
sourceModule._compile(ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, esModuleInterop: true },
}).outputText, sourcePath);
const { acquireAppInstanceLock } = sourceModule.exports;

test('isolated absolute userData is created and active before the single-instance lock', () => {
  const temporary = fs.mkdtempSync(path.join(os.tmpdir(), 'modu-instance-'));
  const userData = path.join(temporary, 'isolated', 'qa');
  const calls = [];
  const app = {
    setPath(key, value) {
      assert.equal(key, 'userData');
      assert.equal(fs.statSync(value).isDirectory(), true);
      calls.push(['setPath', value]);
    },
    requestSingleInstanceLock() {
      assert.deepEqual(calls, [['setPath', userData]]);
      calls.push(['lock']);
      return true;
    },
  };
  try {
    assert.equal(acquireAppInstanceLock(app, userData), true);
    assert.deepEqual(calls, [['setPath', userData], ['lock']]);
  } finally {
    fs.rmSync(temporary, { recursive: true, force: true });
  }
});

test('unset userData preserves the default app path', () => {
  const app = {
    setPath() { throw new Error('must not override default userData'); },
    requestSingleInstanceLock() { return false; },
  };
  assert.equal(acquireAppInstanceLock(app, undefined), false);
});

test('relative userData fails before locking instead of sharing the live app profile', () => {
  const app = {
    setPath() { throw new Error('must not set a relative path'); },
    requestSingleInstanceLock() { throw new Error('must not acquire lock'); },
  };
  assert.throws(() => acquireAppInstanceLock(app, 'relative/qa'), /absolute/);
});
