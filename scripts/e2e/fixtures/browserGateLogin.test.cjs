const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const Module = require('node:module');
const ts = require('typescript');

function load(expect) {
  const file = path.join(__dirname, 'browserGateLogin.ts');
  const mod = new Module(file, module);
  mod.filename = file;
  mod.paths = Module._nodeModulePaths(__dirname);
  const original = mod.require.bind(mod);
  mod.require = name => name === './test' ? {expect} : original(name);
  mod._compile(ts.transpileModule(fs.readFileSync(file, 'utf8'), {
    fileName: file,
    compilerOptions: {module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022},
  }).outputText, file);
  return mod.exports;
}

async function fixture(options, run) {
  const config = {mode: 'shared', options: ['owned-project'], enabled: true,
    choiceAvailable: true, refuse: false, dismiss: true, charges: {}, deadline: 200,
    timers: false, ...options};
  let now = 100, loaded = false, mainCount = 1;
  const calls = [], choices = [];
  const originalPerformance = Object.getOwnPropertyDescriptor(globalThis, 'performance');
  const originalSetTimeout = globalThis.setTimeout, originalClearTimeout = globalThis.clearTimeout;
  const timers = new Map([[99, {foreign: true}]]), scheduled = [], cleared = [];
  let nextTimer = 0;
  if (config.timers) {
    globalThis.setTimeout = (callback, milliseconds) => {
      const id = nextTimer++;
      timers.set(id, {callback, milliseconds});
      scheduled.push({id, milliseconds});
      return id;
    };
    globalThis.clearTimeout = id => {cleared.push(id); timers.delete(id);};
  }
  Object.defineProperty(globalThis, 'performance', {
    configurable: true, value: {now: () => now},
  });
  const record = (operation, target, timeout) => {
    assert(Number.isInteger(timeout) && timeout > 0, 'each wait/action has a positive finite timeout');
    assert(timeout <= Math.floor(config.deadline - now), 'each wait/action spends the same original absolute frame');
    calls.push({operation, target, timeout});
    now += config.charges[operation] || 0;
  };
  const main = {kind: 'main', getByRole(role, selector) {
    assert.equal(role, 'region');
    assert.deepEqual(selector, {name: '로그인한 계정의 프로젝트 선택', exact: true});
    return region;
  }, getByLabel(label, selector) {
    assert.deepEqual(selector, {exact: true});
    assert.equal(label, config.mode === 'local_host' ? '작업할 로컬 프로젝트' : '작업할 프로젝트');
    return select;
  }};
  const region = {kind: 'choice'};
  const select = {kind: 'select', locator(selector) {
    const match = /^option\[value="([A-Za-z0-9_-]+)"\]$/.exec(selector);
    assert(match, 'the requested option uses only the exact owned ID selector');
    return {kind: 'option', count: config.options.filter(value => value === match[1]).length};
  }, async selectOption(value, settings) {
    record('select', value, settings?.timeout);
    assert(loaded && config.enabled, 'selection cannot precede the settled choice control');
    assert.equal(config.options.filter(option => option === value).length, 1);
    choices.push(value);
    if (config.refuse) throw new Error('controlled project selection refused');
    if (config.dismiss) mainCount = 0;
    return [value];
  }};
  const page = {getByRole(role, selector) {
    assert.equal(role, 'main');
    assert.deepEqual(selector, {name: '브라우저 계정 로그인', exact: true});
    return main;
  }};
  const expect = target => ({
    async toBeVisible(settings) {
      record('choice-visible', target.kind, settings?.timeout);
      assert.equal(target, region);
      if (!config.choiceAvailable) throw new Error('controlled account choice did not load');
      loaded = true;
    },
    async toBeEnabled(settings) {
      record('enabled', target.kind, settings?.timeout);
      assert.equal(target, select);
      if (!loaded || !config.enabled) throw new Error('controlled project control disabled');
    },
    async toHaveCount(expected, settings) {
      record(target.kind === 'option' ? 'option-count' : 'gate-count', target.kind, settings?.timeout);
      assert.equal(target.kind === 'option' ? target.count : mainCount, expected);
    },
  });
  try {
    const helper = load(expect);
    assert.equal(typeof helper.chooseBrowserProject, 'function',
      'the real login helper must prepare explicit project choice before the old gate-zero assertion');
    if (config.timers) assert.equal(typeof helper.browserLoginWithin, 'function',
      'the original workspace assertion must consume the same absolute login frame');
    await run({choose: (id = 'owned-project', mode = config.mode, deadline = config.deadline) =>
      helper.chooseBrowserProject(page, id, mode, deadline),
    within: (action, deadline = config.deadline) => helper.browserLoginWithin(action, deadline),
    calls, choices, now: () => now, advance: amount => {now += amount;},
    scheduled, cleared, timers, fire: id => {timers.get(id).callback();}});
  } finally {
    globalThis.setTimeout = originalSetTimeout;
    globalThis.clearTimeout = originalClearTimeout;
    if (originalPerformance) Object.defineProperty(globalThis, 'performance', originalPerformance);
    else delete globalThis.performance;
  }
}

for (const mode of ['shared', 'local_host']) {
  test(`exact ${mode} project is selected once after account choice loads`, () => fixture({mode}, async f => {
    await f.choose();
    assert.deepEqual(f.choices, ['owned-project']);
    assert.deepEqual(f.calls.map(call => call.operation),
      ['choice-visible', 'option-count', 'enabled', 'select', 'gate-count']);
  }));
}

for (const options of [[], ['other-project'], ['owned-project', 'owned-project']]) {
  test(`missing, wrong or duplicate owned option refuses selection: ${JSON.stringify(options)}`, () =>
    fixture({options}, async f => {
      await assert.rejects(f.choose(), assert.AssertionError);
      assert.deepEqual(f.choices, []);
      assert.equal(f.calls.some(call => call.operation === 'gate-count'), false);
    }));
}

test('disabled project choice cannot dispatch a selection', () => fixture({enabled: false}, async f => {
  await assert.rejects(f.choose(), /control disabled/);
  assert.deepEqual(f.choices, []);
}));

test('the account choice must finish loading before any project action', () =>
  fixture({choiceAvailable: false}, async f => {
    await assert.rejects(f.choose(), /choice did not load/);
    assert.deepEqual(f.choices, []);
    assert.deepEqual(f.calls.map(call => call.operation), ['choice-visible']);
  }));

test('expired or invalid caller deadlines cannot start preparation', () => fixture({}, async f => {
  for (const deadline of [100, 99, 100.5, NaN, Infinity]) {
    await assert.rejects(f.choose('owned-project', 'shared', deadline), /deadline expired/);
  }
  assert.deepEqual(f.calls, []);
  assert.deepEqual(f.choices, []);
}));

test('invalid mode and injected project selectors refuse before touching the page', () => fixture({}, async f => {
  await assert.rejects(f.choose('owned-project', 'unknown'), /project choice/);
  await assert.rejects(f.choose('owned-project"] option', 'shared'), /project choice/);
  assert.deepEqual(f.calls, []);
}));

test('all waits and the single selection share the original remaining deadline', () =>
  fixture({charges: {'choice-visible': 10, 'option-count': 11, enabled: 12, select: 13}}, async f => {
    await f.choose();
    assert.deepEqual(f.calls.map(call => call.timeout), [100, 90, 79, 67, 54]);
    assert.deepEqual(f.choices, ['owned-project']);
  }));

test('an await that consumes the original frame cannot start selection afterward', () =>
  fixture({charges: {enabled: 100}}, async f => {
    await assert.rejects(f.choose(), /deadline expired/);
    assert.deepEqual(f.choices, []);
  }));

test('a refused selection is not retried or reported as accepted', () => fixture({refuse: true}, async f => {
  await assert.rejects(f.choose(), /selection refused/);
  assert.deepEqual(f.choices, ['owned-project']);
  assert.equal(f.calls.some(call => call.operation === 'gate-count'), false);
}));

test('the old gate-zero assertion still refuses a choice that never opens its workspace', () =>
  fixture({dismiss: false}, async f => {
    await assert.rejects(f.choose(), assert.AssertionError);
    assert.deepEqual(f.choices, ['owned-project']);
    assert.equal(f.calls.filter(call => call.operation === 'gate-count').length, 1);
  }));

test('a gate result delivered after the original frame cannot report readiness', () =>
  fixture({charges: {'gate-count': 100}}, async f => {
    await assert.rejects(f.choose(), /deadline expired/);
    assert.deepEqual(f.choices, ['owned-project']);
  }));

test('selection retains the lowest original10s action cap inside a longer login frame', () =>
  fixture({deadline: 20100}, async f => {
    await f.choose();
    assert.equal(f.calls.find(call => call.operation === 'select').timeout, 10000);
    assert.deepEqual(f.choices, ['owned-project']);
  }));

test('an expired original workspace frame never starts the wrapped assertion or a timer', () =>
  fixture({timers: true}, async f => {
    let started = 0;
    await assert.rejects(f.within(() => {started++;}, 100), /deadline expired/);
    assert.equal(started, 0);
    assert.deepEqual(f.scheduled, []);
    assert.deepEqual(f.cleared, []);
  }));

test('wrapped assertion preserves its exact result and clears only its own deadline timer', () =>
  fixture({timers: true}, async f => {
    const result = {owned: true};
    assert.equal(await f.within(async () => result), result);
    assert.deepEqual(f.scheduled, [{id: 0, milliseconds: 100}]);
    assert.deepEqual(f.cleared, [0]);
    assert.equal(f.timers.has(99), true);
  }));

test('wrapped assertion rejects at the original deadline without retrying the action', () =>
  fixture({timers: true}, async f => {
    let started = 0;
    const pending = f.within(() => {started++; return new Promise(() => {});});
    f.fire(0);
    await assert.rejects(pending, /deadline expired/);
    assert.equal(started, 1);
    assert.deepEqual(f.cleared, [0]);
    assert.equal(f.timers.has(99), true);
  }));

test('wrapped assertion preserves a synchronous original refusal and clears its own timer', () =>
  fixture({timers: true}, async f => {
    const refusal = new Error('controlled original assertion refusal');
    await assert.rejects(f.within(() => {throw refusal;}), error => error === refusal);
    assert.deepEqual(f.cleared, [0]);
    assert.equal(f.timers.has(99), true);
  }));

test('a wrapped assertion delivered late cannot enlarge the original readiness frame', () =>
  fixture({timers: true}, async f => {
    await assert.rejects(f.within(async () => {f.advance(100); return true;}), /deadline expired/);
    assert.deepEqual(f.cleared, [0]);
    assert.equal(f.timers.has(99), true);
  }));
