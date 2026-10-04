import fs from 'node:fs';
import path from 'node:path';
import type { Page } from '@playwright/test';
import { expect, test } from './fixtures/test';

// Real ReactDOM component/store regression. Only transport replies are fixtures; no backend activation,
// model verification, inference, remote requests or deployment is claimed. The test-only bundle exposes
// state transitions without adding a test hook to production code.
// eslint-disable-next-line @typescript-eslint/no-require-imports
const { build } = require('esbuild');
let bundle: string;

test.beforeAll(async () => {
  const result = await build({
    stdin: { resolveDir: process.cwd(), loader: 'tsx', contents: `
      import React, {useState} from 'react';
      import {createRoot} from 'react-dom/client';
      import {FlowchartStudio} from './src/renderer/components/flowchart/FlowchartStudio';
      import {createFlowRecipe} from './src/renderer/components/flowchart/flowRecipes';
      import {useFlowchartStore} from './src/renderer/stores/useFlowchartStore';
      import {useDatasetStore} from './src/renderer/stores/useDatasetStore';
      import {useProjectStore} from './src/renderer/stores/useProjectStore';
      import {useComputeStore} from './src/renderer/stores/useComputeStore';
      import {useEvaluationStore} from './src/renderer/stores/useEvaluationStore';
      import {api,setProjectContext,setSharedApiBase} from './src/renderer/services/api';
      import {flowDraft} from './src/renderer/services/flowDraft';
      const model='activation-transport-fixture';
      const graph=createFlowRecipe('fixed','anomaly');
      graph.nodes.find(n=>n.data.node_type==='inspection').data.model_job_id=model;
      const earlier=structuredClone(graph);
      earlier.nodes.find(n=>n.data.node_type==='inspection').data.threshold=0.7;
      let source='/fixture/source-a', active='current-a', activeGraph=graph;
      let resolveActivation, rejectActivation, settled=false, requests=[], readbackStarted=false, rejectReadback;
      const activation=new Promise((resolve,reject)=>{resolveActivation=resolve;rejectActivation=reject;});
      const versions=()=>({pipelines:[active==='earlier-a'?'current-a':active,'earlier-a'].map(version=>({
        version_id:version,pipeline_id:'fixture-pipeline',name:version,recipe_task:'anomaly',source_dataset_path:source,
        created_at:'2026-10-04T00:00:00Z',saved_at:'2026-10-04T00:00:00Z',node_count:graph.nodes.length,model_count:1,
        is_latest:version===active,is_active:version===active})),total:2});
      const notFound=()=>Promise.reject(Object.assign(new Error('no saved draft fixture'),{status:404}));
      flowDraft.get=notFound;
      Object.assign(api.compute,{listProfiles:async()=>({profiles:[]}),getSelection:async()=>({compute_profile_id:null})});
      Object.assign(api.flowchart,{
        modelCatalog:async()=>({models:[{job_id:model,task:'anomaly',label:'Transport fixture',
          preset:null,created_at:null,best_metric:null,source_dataset_path:source,class_names:[],class_ids:[],score_spec:null}],total:1}),
        verifyModels:async()=>({verified_job_ids:[model]}),
        listPipelines:async()=>versions(),
        activeVersionId:async()=>({version_id:active}),
        getActivePipelineRecord:async()=>({version_id:active,pipeline:activeGraph}),
        getPipelineVersion:async()=>earlier,
        previewChange:async()=>({parent_revision:active,stale:false,layout_only:false,
          semantic_delta:{changes:[{kind:'node_changed',node_id:'node_inspect',field:'threshold',before:0.5,after:0.7}],
            layout_only:false,semantic_sha256_before:'a'.repeat(64),semantic_sha256_after:'b'.repeat(64)}}),
        activatePipelineVersion:async(version,folder,change)=>{
          requests.push({version,folder,change});
          try {const reply=await activation;return reply;} finally {settled=true;}
        }
      });
      // Any dependency missed by the transport fixtures must fail visibly, never reach a real service.
      window.fetch=async()=>{throw new Error('unexpected network request in activation component fixture');};
      const project=()=>({id:'project-a',name:'Activation fixture',task:'anomaly',project_dir:'/fixture/project-a',
        source_dataset_dir:source,active_labelset_id:'default'});
      useProjectStore.setState({project:project(),projectDir:'/fixture/project-a',task:'anomaly',language:'ko',isProjectBusy:false});
      useDatasetStore.setState({folderPath:source,datasetKey:source+'\\0anomaly',hasSelectedFolder:true,isLoading:false,importError:null});
      useComputeStore.setState({isLoaded:true,profiles:[],selectedProfileId:null});
      useEvaluationStore.setState({allowLatestRecovery:false,jobId:null});
      let remount;
      function Fixture(){
        const [mount,setMount]=useState(0);remount=()=>setMount(n=>n+1);
        const base=useFlowchartStore(s=>s.baseVersionId);
        const loading=useFlowchartStore(s=>s.isLoading);
        return <><output aria-label="Observed editor base">{base===undefined?'unknown':base??'none'}</output>
          <output aria-label="Observed editor mount">{mount}</output><output aria-label="Observed editor loading">{String(loading)}</output>
          <FlowchartStudio key={mount}/></>;
      }
      window.activationFixture={
        snapshot:()=>({requests,settled,readbackStarted,base:useFlowchartStore.getState().baseVersionId}),
        resolve:()=>{active='earlier-a';resolveActivation({status:'active',version_id:'earlier-a',pipeline:earlier});},
        reject:()=>rejectActivation(Object.assign(new Error('old activation transport refused'),{status:409})),
        switchSource:()=>{
          source='/fixture/source-b';active='current-b';activeGraph=structuredClone(graph);
          useProjectStore.setState({project:{...project(),id:'project-b',project_dir:'/fixture/project-b'},projectDir:'/fixture/project-b'});
          useDatasetStore.setState({folderPath:source,datasetKey:source+'\\0anomaly'});
          useFlowchartStore.getState().invalidateForDataChange();
        },
        changeAuthority:()=>{
          setProjectContext({workspace_id:'fixture',project_id:'project-a',actor_id:'new-authority',mode:'local'});
          useProjectStore.setState({project:{...project(),name:'New authority fixture'}});
        },
        transportRoundTrip:()=>{
          setSharedApiBase('http://127.0.0.1:65534/fixture');setSharedApiBase(null);
          useProjectStore.setState({project:{...project(),name:'New transport fixture'}});
        },
        reset:()=>{activeGraph=earlier;useFlowchartStore.getState().invalidateForDataChange();},
        remount:()=>{activeGraph=earlier;remount();},
        holdReadback:()=>{api.flowchart.listPipelines=()=>{readbackStarted=true;return new Promise((_resolve,reject)=>{rejectReadback=reject;});};},
        rejectReadback:()=>rejectReadback(new Error('old activation readback refused'))
      };
      createRoot(document.getElementById('root')).render(<Fixture/>);
    ` },
    bundle: true, write: false, format: 'iife', platform: 'browser', target: 'es2022',
    define: { 'process.env.NODE_ENV': '"development"' }, metafile: true,
  });
  bundle = result.outputFiles[0].text;
  const out = process.env.MV_E2E_RUN_DIR!;
  fs.writeFileSync(path.join(out, 'activation-component-fixture.js'), bundle);
  fs.writeFileSync(path.join(out, 'activation-component-inputs.json'), JSON.stringify(result.metafile, null, 2));
});

async function control(page: Page, action: string) {
  return page.evaluate(action => {
    const fixture = (window as unknown as { activationFixture: Record<string, () => unknown> }).activationFixture;
    return fixture[action]();
  }, action);
}

async function beginActivation(page: Page) {
  await page.route('http://127.0.0.1:65534/activation-fixture', route => route.fulfill({
    contentType: 'text/html', body: '<!doctype html><html><body><div id="root"></div></body></html>',
  }));
  await page.goto('http://127.0.0.1:65534/activation-fixture');
  await page.addScriptTag({ content: bundle });
  await expect(page.getByLabel('Observed editor base')).toHaveText('current-a');
  await page.getByLabel('저장 버전', { exact: true }).selectOption('earlier-a');
  await page.getByRole('button', { name: '이 버전 활성화', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: '저장 버전 활성화' });
  await dialog.getByLabel('변경 사유').fill('controlled activation race');
  await dialog.getByRole('button', { name: '활성화', exact: true }).click();
  await expect.poll(async () => (await control(page, 'snapshot') as { requests: unknown[] }).requests.length).toBe(1);
}

const races = [
  { change: 'switchSource', base: 'current-b', outcome: 'resolve' },
  // Identical pipeline/base values deliberately remain: namespace epoch, reset revision and unmount still invalidate it.
  { change: 'changeAuthority', base: 'current-a', outcome: 'reject' },
  { change: 'transportRoundTrip', base: 'current-a', outcome: 'resolve' },
  { change: 'reset', base: 'current-a', outcome: 'resolve' },
  { change: 'remount', base: 'current-a', outcome: 'resolve' },
];
for (const race of races) {
  test(`late activation ${race.outcome} cannot change the current editor after ${race.change}`, async ({ page, evidence }) => {
    await beginActivation(page);
    await control(page, race.change);
    if (race.change === 'remount') {
      await expect(page.getByLabel('Observed editor mount')).toHaveText('1');
      await page.evaluate(() => new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
      await expect(page.getByLabel('Observed editor loading')).toHaveText('false');
    }
    await expect(page.getByLabel('Observed editor base')).toHaveText(race.base);
    await control(page, race.outcome);
    await expect.poll(async () => (await control(page, 'snapshot') as { settled: boolean }).settled).toBe(true);
    await page.evaluate(() => new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
    const observed = await control(page, 'snapshot');
    evidence.note('activation_transport_fixture', { race, observed, backend_activation_called: false });
    await expect(page.getByLabel('Observed editor base')).toHaveText(race.base, { timeout: 2000 });
    await expect(page.getByRole('dialog', { name: '저장 버전 활성화' })).toHaveCount(0, { timeout: 2000 });
    await expect(page.getByRole('alert').filter({ hasText: 'old activation transport refused' })).toHaveCount(0, { timeout: 2000 });
    await evidence.screenshot(page, `e04-late-${race.change}-${race.outcome}`);
  });
}

test('late activation readback errors cannot appear under a new authority', async ({ page, evidence }) => {
  await beginActivation(page);
  await control(page, 'holdReadback');
  await control(page, 'resolve');
  await expect.poll(async () => (await control(page, 'snapshot') as { readbackStarted: boolean }).readbackStarted).toBe(true);
  await control(page, 'changeAuthority');
  await control(page, 'rejectReadback');
  await page.evaluate(() => new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
  await expect(page.getByRole('alert').filter({ hasText: 'old activation readback refused' })).toHaveCount(0, { timeout: 2000 });
  await expect(page.getByLabel('Observed editor base')).toHaveText('earlier-a');
  evidence.note('late_readback_transport_fixture', await control(page, 'snapshot'));
});

test('same-scope activation success advances the editor base and refreshes the active version', async ({ page, evidence }) => {
  await beginActivation(page);
  await control(page, 'resolve');
  await expect(page.getByLabel('Observed editor base')).toHaveText('earlier-a');
  await expect(page.getByRole('dialog', { name: '저장 버전 활성화' })).toHaveCount(0);
  await expect(page.getByLabel('저장 버전', { exact: true }).locator('option:checked')).toContainText('● 활성');
  evidence.note('same_scope_activation', await control(page, 'snapshot'));
});

test('same-scope activation error remains visible and keeps the editor base', async ({ page }) => {
  await beginActivation(page);
  await control(page, 'reject');
  await expect(page.getByRole('alert').filter({ hasText: 'old activation transport refused' })).toBeVisible();
  await expect(page.getByLabel('Observed editor base')).toHaveText('current-a');
  await expect(page.getByRole('dialog', { name: '저장 버전 활성화' })).toHaveCount(0);
});
