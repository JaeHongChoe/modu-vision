'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const crypto = require('node:crypto');
const test = require('node:test');
const ts = require('typescript');
const {expect} = require('@playwright/test');
const sha = value => crypto.createHash('sha256').update(value).digest('hex');
const origin = 'http://127.0.0.1:8123', source = '/owned/source';
function validator(file, name) {
  const sourceFile = path.resolve(__dirname, '../e2e', file);
  const parsed = ts.createSourceFile(sourceFile, fs.readFileSync(sourceFile, 'utf8'), ts.ScriptTarget.Latest, true);
  const names = new Set([name, 'assertDraftFailure']);
  const body = parsed.statements.filter(node => ts.isFunctionDeclaration(node) && names.has(node.name?.text))
    .map(node => node.getText(parsed)).join('\n');
  const exports = {};
  vm.runInNewContext(ts.transpileModule(body, {compilerOptions:{module:ts.ModuleKind.CommonJS}}).outputText,
    {exports, expect, sha, URLSearchParams, Buffer});
  return exports[name];
}
function fixture(draft) {
  const reply = (route, value) => ({status:200, url:origin+route, body:JSON.stringify(value)});
  const before = Object.fromEntries([
    ['/api/project/current', {task:'anomaly', source_dataset_dir:source}],
    ['/api/dataset/revisions', {active_revision:null, revisions:[]}],
    ['/api/flowchart/pipeline/active-version', {version_id:null}],
    ['/api/flowchart/pipelines?source_dataset_path='+encodeURIComponent(source), {pipelines:[], total:0}],
  ].map(([route, value]) => [route, reply(route, value)]));
  const row = (url, status, value, method='GET', request_body=null) => {
    const raw = JSON.stringify(value);
    return {method, url:origin+url, request_body, status, raw, raw_sha256:sha(raw),
      raw_size:Buffer.byteLength(raw), started:1, deadline:10001, finished:2};
  };
  const rows = [
    row('/api/dataset/library/images?state=valid&limit=120',409,
      {detail:'No validated dataset revision is active; validate the source and accept a revision first'}),
    row('/api/flowchart/models/catalog?source_dataset_path='+encodeURIComponent(source),200,{models:[],total:0}),
  ];
  if(draft) rows.push(row('/api/flowchart/draft',503,{detail:'Controlled exact owning draft PUT unavailable'},'PUT','{}'));
  return {before, rows, row, pipeline:{nodes:[{data:{model_job_id:null}}]}};
}
for (const [file, name, draft] of [
  ['flow-draft-save-error.spec.ts','assertObservedDraftReplies',true],
  ['flow-history-input-boundaries.spec.ts','assertObservedHistoryReplies',false],
]) {
  const validate = validator(file, name);
  test(`${file}: real empty model catalog is optional discovery, not a missing selected-job error`, () => {
    const f = fixture(draft); validate(f.rows,origin,source,f.before,f.pipeline);
  });
  for (const damage of ['missing-catalog','old-no-job-404','malformed-catalog','deadline','wrong-revision-body'])
    test(`${file}: still refuses ${damage}`, () => {
      const f = fixture(draft);
      if(damage==='missing-catalog') f.rows.splice(1,1);
      if(damage==='old-no-job-404') f.rows.push(f.row('/api/evaluation/results?'+new URLSearchParams({source_dataset_path:source,source_task:'anomaly'}),404,{detail:'No completed training job has been selected'}));
      if(damage==='malformed-catalog') f.rows[1]=f.row('/api/flowchart/models/catalog?source_dataset_path='+encodeURIComponent(source),200,{models:[{}],total:0});
      if(damage==='deadline') f.rows[1].finished=10002;
      if(damage==='wrong-revision-body') f.rows[0]=f.row('/api/dataset/library/images?state=valid&limit=120',409,{detail:'Another conflict'});
      assert.throws(() => validate(f.rows,origin,source,f.before,f.pipeline));
    });
}
