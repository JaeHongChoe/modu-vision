const assert = require('node:assert/strict');
const fs = require('node:fs');
const Module = require('node:module');
const path = require('node:path');
const test = require('node:test');
const ts = require('typescript');

function service(request) {
  const filename = path.resolve(__dirname, 'flowEvaluation.ts');
  assert.ok(fs.existsSync(filename), 'Flow evaluation service is not implemented');
  const loaded = new Module(filename, module); loaded.filename = filename;
  loaded.paths = Module._nodeModulePaths(__dirname);
  loaded.require = name => { if(name === './api') return {request}; throw new Error(name); };
  loaded._compile(ts.transpileModule(fs.readFileSync(filename,'utf8'),{
    compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022},
  }).outputText, filename);
  return loaded.exports;
}

test('unavailable overkill stays unavailable and real zero remains zero', () => {
  const {formatFlowMetric} = service(() => {});
  assert.equal(formatFlowMetric(null), '계산 불가');
  assert.equal(formatFlowMetric(0), '0.0%');
  assert.equal(formatFlowMetric(.125), '12.5%');
});

test('truth read preserves complete vocabulary and explicit role scope', async () => {
  let url;
  const {flowEvaluation} = service(async path => {url=path;return {verdict:'UNKNOWN'};});
  const result=await flowEvaluation.truth('/data/A & B.png',{task:'segmentation',classes:['background','crack'],
    class_semantics:{roles:{background:'normal',crack:'defect'},basis:{background:'explicit',crack:'explicit'}}});
  const parsed=new URL(url,'http://local');
  assert.equal(result.verdict,'UNKNOWN');
  assert.equal(parsed.searchParams.get('image_path'),'/data/A & B.png');
  assert.deepEqual(parsed.searchParams.getAll('classes'),['background','crack']);
  assert.deepEqual(JSON.parse(parsed.searchParams.get('class_roles')),{background:'normal',crack:'defect'});
});

test('truth write binds only one selected image and optimistic revisions', async () => {
  let body;
  const {flowEvaluation} = service(async (path,options) => {body=JSON.parse(options.body);return body;});
  const current={image_path:'/data/one.png',truth_revision:3,image_revision:8,scope:{task:'segmentation',classes:['background','crack'],
    class_semantics:{roles:{background:'normal',crack:'defect'},basis:{background:'alias',crack:'default'}}}};
  await flowEvaluation.declareTruth(current,'OK',[],'Reviewer','Reviewed pixels');
  assert.equal(body.image_path,'/data/one.png');
  assert.equal(body.expected_revision,3);assert.equal(body.expected_image_revision,8);
  assert.equal(body.verdict,'OK');assert.deepEqual(body.defect_classes,[]);
  assert.equal(body.class_roles,null);
  assert.equal(Object.hasOwn(body,'participating_tasks'),false);
});

test('mixed truth read retains participating tasks so reviewed uncertainty cannot disappear', async () => {
  let url;
  const {flowEvaluation}=service(async path=>{url=path;return {verdict:'UNKNOWN',invalidated:true};});
  const result=await flowEvaluation.truth('/data/part.png',{task:'mixed',classes:['background','good'],
    participating_tasks:['classification','segmentation'],
    class_semantics:{roles:{background:'normal',good:'defect'},basis:{background:'explicit',good:'explicit'}}});
  const params=new URL(url,'http://local').searchParams;
  assert.deepEqual(params.getAll('participating_tasks'),['classification','segmentation']);
  assert.equal(JSON.parse(params.get('class_roles')).good,'defect');
  assert.equal(result.verdict,'UNKNOWN');
});

test('mixed re-review retains the effective truth task context after reading an image', async () => {
  let body;
  const {flowEvaluation}=service(async (path,options)=>{body=JSON.parse(options.body);return body;});
  await flowEvaluation.declareTruth({image_path:'/data/part.png',image_revision:11,truth_revision:1,
    participating_tasks:['classification','segmentation'],scope:{task:'mixed',classes:['background','good'],
      class_semantics:{roles:{background:'normal',good:'defect'},basis:{background:'explicit',good:'explicit'}}}},
    'NG',['good'],'Reviewer','Reviewed participating tasks');
  assert.deepEqual(body.participating_tasks,['classification','segmentation']);
  assert.equal(body.class_roles.good,'defect');
  assert.equal(body.expected_image_revision,11);
  assert.deepEqual(body.defect_classes,['good']);
});

test('cohort selection matches mixed task context without confusing it with canonical truth scope', () => {
  const {isCompatibleFlowCohort}=service(()=>{});
  const canonical={project_id:'p',source_dataset_path:'/data',labelset_id:'default',task:'mixed',
    classes:['background','good'],class_semantics:{version:1,roles:{background:'normal',good:'defect'},
      basis:{background:'explicit',good:'explicit'}}};
  const scope={...canonical,participating_tasks:['classification','segmentation']};
  const current={scope:canonical,participating_tasks:['segmentation','classification']};
  assert.equal(isCompatibleFlowCohort(current,scope),true);
  assert.equal(isCompatibleFlowCohort({scope:canonical},scope),false);
  assert.equal(isCompatibleFlowCohort({...current,participating_tasks:['detection','segmentation']},scope),false);
  assert.equal(isCompatibleFlowCohort({...current,scope:{...canonical,classes:['good','background']}},scope),false);
  assert.equal(isCompatibleFlowCohort({...current,scope:{...canonical,class_semantics:{...canonical.class_semantics,
    roles:{background:'normal',good:'normal'}}}},scope),false);
  const single={...canonical,task:'segmentation'};
  assert.equal(isCompatibleFlowCohort({scope:single},single),true);
});
