const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const Module = require('node:module');
const ts = require('typescript');
function load(file, cache = new Map()) {
  if (cache.has(file)) return cache.get(file).exports;
  const loaded = new Module(file, module); loaded.filename = file; loaded.paths = Module._nodeModulePaths(path.dirname(file)); cache.set(file, loaded);
  const original = loaded.require.bind(loaded);
  loaded.require = name => name.startsWith('.') && fs.existsSync(path.resolve(path.dirname(file), name) + '.ts') ? load(path.resolve(path.dirname(file), name) + '.ts', cache) : original(name);
  loaded._compile(ts.transpileModule(fs.readFileSync(file, 'utf8'), {compilerOptions: {module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022}}).outputText, file);
  return loaded.exports;
}
const filename = path.join(__dirname, 'catalog.ts');
const catalog = fs.existsSync(filename) ? load(filename) : {};
const plan = () => ({goal: '검사 준비', binding: 'actor/session/local-project', steps: [{action: 'save_split', args: {train_percent: 70, val_percent: 20, test_percent: 10}, label: '자료 나누기'}]});

test('a valid plan is copied and the catalog exposes only executable, non-authority actions', () => {
  assert.equal(typeof catalog.validateTaskPlan, 'function');
  const input = plan(), result = catalog.validateTaskPlan(input);
  assert.deepEqual(result, input); result.steps[0].args.train_percent = 80;
  assert.equal(input.steps[0].args.train_percent, 70);
  assert.deepEqual(catalog.taskActionCatalog().map(row => row.id), ['create_project','import_dataset','save_split','configure_training','configure_input_recipe','generate_label_drafts','start_training','evaluate_model','save_flow','evaluate_flow','inspect_test','export_report','open_step']);
});

test('unknown, forbidden and provider-issued approval fields never enter a plan', () => {
  for (const action of ['approve_model','adopt_drafts','deploy','delete_project','format_disk']) {
    const input = plan(); input.steps[0].action = action; assert.throws(() => catalog.validateTaskPlan(input));
  }
  for (const extra of ['approval','confirmed','plan_sha256']) assert.throws(() => catalog.validateTaskPlan({...plan(), [extra]: true}));
  assert.throws(() => catalog.validateTaskPlan({...plan(), steps: [{...plan().steps[0], confirmed: true}]}));
});

test('action schemas reject unknown fields, paths, nonfinite values and contradictory draft modes', () => {
  const bad = [
    ['start_training', {confirmed: true}], ['create_project', {name:'검사', task:'classification', project_dir:'/tmp/provider'}],
    ['import_dataset', {folder_path:'/tmp/provider'}], ['save_split', {train_percent:70,val_percent:20,test_percent:20}],
    ['save_split', {train_percent:NaN,val_percent:20,test_percent:10}], ['configure_training', {}],
    ['configure_training', {preset:'balanced'}], ['configure_training', {batch_size:0}], ['configure_training', {batch_size:129}],
    ['generate_label_drafts', {mode:'phrase'}], ['generate_label_drafts', {job_id:'job-1',prompt:'균열'}],
    ['generate_label_drafts', {mode:'trained',prompt:'균열'}], ['evaluate_model', {cohort_id:'other'}],
    ['evaluate_flow', {cohort_id:'other'}], ['export_report', {format:'pdf'}], ['open_step', {step:7}],
    ['open_step', {step:2,target:'javascript:alert(1)'}],
  ];
  for (const [action,args] of bad) assert.throws(() => catalog.validateTaskAction(action,args), `${action} ${JSON.stringify(args)}`);
  assert.deepEqual(catalog.validateTaskAction('generate_label_drafts',{mode:'phrase',prompt:'균열',threshold:0.4}),{mode:'phrase',prompt:'균열',threshold:0.4});
  assert.deepEqual(catalog.validateTaskAction('configure_training',{preset:'precision',device:'cpu',batch_size:32}),{preset:'precision',device:'cpu',batch_size:32});
});

test('plan bounds refuse empty, oversized and non-JSON structures', () => {
  for (const steps of [[], Array.from({length:25}, () => ({action:'start_training',args:{},label:'학습'}))]) assert.throws(() => catalog.validateTaskPlan({...plan(),steps}));
  assert.throws(() => catalog.validateTaskPlan({...plan(),binding:''}));
  assert.throws(() => catalog.validateTaskPlan({...plan(),binding:'x'.repeat(16385)}));
  assert.throws(() => catalog.validateTaskPlan({...plan(),goal:'x'.repeat(4097)}));
  assert.throws(() => catalog.validateTaskAction('start_training',Object.create({confirmed:true})));
});
