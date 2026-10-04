import fs from 'node:fs';
import path from 'node:path';
import type { Page } from '@playwright/test';
import { expect, test } from './fixtures/test';

// Actual ReactDOM GoldLabelReview and Zustand stores. Only API transport replies
// are controlled; no backend, GPU, training or remote service is started.
// eslint-disable-next-line @typescript-eslint/no-require-imports
const { build } = require('esbuild');
let bundle: string;

test.beforeAll(async () => {
  const result = await build({
    stdin: { resolveDir: process.cwd(), loader: 'tsx', contents: `
      import React, {useState} from 'react';
      import {createRoot} from 'react-dom/client';
      import {GoldLabelReview} from './src/renderer/components/labeling/GoldLabelReview';
      import {teamDataScope} from './src/renderer/components/labeling/teamDataWorkflow';
      import {teamDataApi} from './src/renderer/services/teamDataApi';
      import {datasetWorkflow} from './src/renderer/services/datasetWorkflow';
      import {api,setSharedApiBase,setProjectContext,getProjectContextGeneration,getApiPersistenceIdentity} from './src/renderer/services/api';
      import {useProjectStore} from './src/renderer/stores/useProjectStore';
      import {useDatasetStore} from './src/renderer/stores/useDatasetStore';
      import {useAnnotationStore} from './src/renderer/stores/useAnnotationStore';
      import {useComputeStore} from './src/renderer/stores/useComputeStore';
      const counts={missing:1,extra:0,class:0,geometry:0,not_comparable:0};
      let requests=[],pending=[],holdNext=new Set(),profiles=new Map(),reports=new Map(),callbackCount=0;
      let setVisible,setManage;
      const owner=()=>useProjectStore.getState().project.id+':'+useProjectStore.getState().project.active_labelset_id;
      const profile=(key,created=false)=>({profile_id:(created?'b':'a').repeat(32)+'-'+key,
        created_at:'2026-10-04T00:00:00Z',actor:'Park',task:'detection',reference_labelset:key+'-ref',candidate_labelset:key+'-candidate',
        reference_snapshot:[{relative_path:'sample-a.png',annotation_sha256:'a'.repeat(64),mask_sha256:null}],
        guideline_revision:null,class_match:'exact',shape_metric:'shape_iou',tolerance:0.5,profile_sha256:'b'.repeat(64)});
      const metadata=()=>({image_uuid:'image-a',file_path:useProjectStore.getState().project.source_dataset_dir+'/sample-a.png',
        relative_path:'sample-a.png',content_hash:'a'.repeat(64),content_version:1,width:40,height:30,revision:1,
        tags:[],product:'',lot:'',group:'',workflow_state:'approved',reviewer:'Kim',usage_state:'active',review_history:[],audit:[]});
      const report=(key,profileId=profile(key).profile_id)=>({report_id:'c'.repeat(32)+'-'+key,profile_id:profileId,
        created_at:'2026-10-04T00:00:00Z',task:'detection',images:[{relative_path:'sample-a.png',
          image_path:useProjectStore.getState().project.source_dataset_dir+'/sample-a.png',labeled:false,
          conflicts:[{conflict_id:'d'.repeat(64),kind:'missing',reference_object:'reference-'+key,label:'scratch',bbox:[1,1,10,10]}]}],
        counts,agreeing_images:0,limitations:[],stale:false,stale_reasons:[],current:true,candidate_changes:[],passes:false,approval_eligible:false});
      const saved=(key)=>({reports:(reports.get(key)||[report(key)]).map(row=>({...row,images:row.images.length}))});
      const reply=async(op,args,value)=>{
        const record={op,args,owner:owner(),contextGeneration:getProjectContextGeneration(),settled:false};requests.push(record);
        try{
          if(holdNext.delete(op))return await new Promise((resolve,reject)=>pending.push({op,resolve,reject,value:structuredClone(value)}));
          return structuredClone(value);
        }finally{record.settled=true;}
      };
      Object.assign(teamDataApi,{
        qualityProfiles:()=>reply('profiles',{}, {profiles:profiles.get(owner())||[profile(owner())],gold_policy:{include_gold_in_training:false}}),
        queue:filters=>reply('queue',filters,{items:[metadata()],total:1,offset:0,limit:200}),
        qualityReports:()=>reply('reports',{},saved(owner())),
        qualityReport:id=>reply('report',{id},(reports.get(owner())||[report(owner())]).find(row=>row.report_id===id)||report(owner())),
        createQualityProfile:async body=>{const key=owner();const made=await reply('create',body,profile(key,true));
          profiles.set(key,[...(profiles.get(key)||[profile(key)]),made]);return made;},
        runQualityReport:async id=>{const key=owner();const made=await reply('runReport',{id},report(key,id));
          reports.set(key,[made]);return made;},
        retireQualityProfile:async(id,actor)=>{const key=owner();const result=await reply('retire',{id,actor},{profile_id:id,actor,at:'2026-10-04T00:00:00Z'});
          profiles.set(key,(profiles.get(key)||[profile(key)]).filter(row=>row.profile_id!==id));return result;},
        setGoldPolicy:(include,actor)=>reply('policy',{include,actor},{include_gold_in_training:include})
      });
      api.project.listLabelsets=()=>reply('labelsets',{}, {active_id:owner()+'-ref',labelsets:[
        {id:owner()+'-ref',name:'Reference '+owner(),source_id:null,created_at:''},
        {id:owner()+'-candidate',name:'Candidate '+owner(),source_id:null,created_at:''}]});
      Object.assign(datasetWorkflow,{
        image:path=>reply('image',{path},{...metadata(),file_path:path}),
        annotations:(id,path)=>reply('annotations',{id,path},{image_id:id,annotations:[],image_width:40,image_height:30,metadata:{...metadata(),file_path:path}})
      });
      window.fetch=async()=>{throw new Error('unexpected network request in gold review component fixture');};
      const project=(id='project-a',labelset='default')=>({id,name:'Gold fixture '+id,task:'detection',
        project_dir:'/fixture/'+id,source_dataset_dir:'/fixture/source-'+id,active_labelset_id:labelset});
      const select=(id='project-a',labelset='default')=>{
        const next=project(id,labelset);
        setProjectContext({workspace_id:'fixture',project_id:id,actor_id:'Park',mode:'local'});
        useProjectStore.setState({project:next,projectDir:next.project_dir,task:'detection',activeStep:2,language:'ko',isProjectBusy:false});
        useDatasetStore.setState({folderPath:next.source_dataset_dir,datasetKey:next.source_dataset_dir+'\\0detection',images:[],hasSelectedFolder:true});
      };
      select();useAnnotationStore.setState({reviewerName:'Park',isDirty:false,isSaving:false,currentImage:null,images:[]});
      useComputeStore.setState({isLoaded:true,profiles:[],selectedProfileId:null});
      function Fixture(){
        const [visible,show]=useState(true);setVisible=show;
        const [canManage,manage]=useState(true);setManage=manage;
        const projectState=useProjectStore();const computeState=useComputeStore();
        const actor=useAnnotationStore(state=>state.reviewerName);
        const scope=teamDataScope({...projectState,...computeState,apiTransportIdentity:getApiPersistenceIdentity()});
        return <><output aria-label="Observed owner">{owner()}</output><output aria-label="Observed actor">{actor}</output>
          <output aria-label="Observed manager">{String(canManage)}</output>
          {visible&&<GoldLabelReview scope={scope} actor={actor} canManage={canManage} onImageOpened={()=>{callbackCount++;show(false);}}/>}</>;
      }
      window.goldReviewFixture={
        hold:op=>holdNext.add(op),
        snapshot:()=>({requests,pending:pending.map(row=>row.op),callbackCount,
          annotationPath:useAnnotationStore.getState().currentImage?.file_path||null,contextGeneration:getProjectContextGeneration()}),
        release:op=>{const index=pending.findIndex(row=>row.op===op);if(index<0)throw new Error('no pending '+op);
          const row=pending.splice(index,1)[0];row.resolve(row.value);},
        reject:op=>{const index=pending.findIndex(row=>row.op===op);if(index<0)throw new Error('no pending '+op);
          pending.splice(index,1)[0].reject(new Error('old gold transport refused'));},
        switchProject:()=>select('project-b'),switchLabelset:()=>select('project-a','other'),
        projectABA:()=>{select('project-b');select('project-a');},
        transportABA:()=>{setSharedApiBase('http://127.0.0.1:65534/other');setSharedApiBase(null);},
        actorRole:()=>{useAnnotationStore.getState().setReviewerName('New reviewer');setManage(false);},
        unmount:()=>setVisible(false),remount:()=>setVisible(true)
      };
      if(window.goldReviewInitialHold)holdNext.add(window.goldReviewInitialHold);
      createRoot(document.getElementById('root')).render(<Fixture/>);
    ` },
    bundle: true, write: false, format: 'iife', platform: 'browser', target: 'es2022',
    define: { 'process.env.NODE_ENV': '"development"' }, metafile: true,
  });
  bundle = result.outputFiles[0].text;
  const out = process.env.MV_E2E_RUN_DIR!;
  fs.writeFileSync(path.join(out, 'gold-review-component-fixture.js'), bundle);
  fs.writeFileSync(path.join(out, 'gold-review-component-inputs.json'), JSON.stringify(result.metafile, null, 2));
});

type Snapshot = { requests: { op: string; args: unknown; owner: string; contextGeneration: number; settled: boolean }[];
  pending: string[]; callbackCount: number; annotationPath: string | null; contextGeneration: number };

async function control(page: Page, action: string, argument?: string): Promise<unknown> {
  return page.evaluate(({ action, argument }) => {
    const fixture = (window as unknown as { goldReviewFixture: Record<string, (argument?: string) => unknown> }).goldReviewFixture;
    return fixture[action](argument);
  }, { action, argument });
}
const snapshot = (page: Page) => control(page, 'snapshot') as Promise<Snapshot>;
const review = (page: Page) => page.getByRole('region', { name: '정답 기준 라벨 검수', exact: true });
async function flush(page: Page) {
  await page.evaluate(() => new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
}
async function mount(page: Page, initialHold?: string) {
  await page.route('http://127.0.0.1:65534/gold-review-fixture', route => route.fulfill({
    contentType: 'text/html', body: '<!doctype html><html><body><div id="root"></div></body></html>',
  }));
  await page.goto('http://127.0.0.1:65534/gold-review-fixture');
  if (initialHold) await page.evaluate(op => { (window as unknown as { goldReviewInitialHold: string }).goldReviewInitialHold = op; }, initialHold);
  await page.addScriptTag({ content: bundle });
  await expect(review(page)).toBeVisible();
  if (!initialHold) await expect(review(page).getByLabel('비교할 라벨셋')).toHaveValue('project-a:default-candidate');
}
async function beginCreate(page: Page) {
  await mount(page);
  await control(page, 'hold', 'create');
  await review(page).getByRole('button', { name: '모두 선택', exact: true }).click();
  await review(page).getByRole('button', { name: '검수 기준 만들고 실행 (1장)', exact: true }).click();
  await expect.poll(async () => (await snapshot(page)).pending).toContain('create');
}

for (const change of ['unmount_project', 'transportABA', 'projectABA', 'actorRole', 'switchLabelset']) {
  test(`late create cannot issue report writes after ${change}`, async ({ page, evidence }) => {
    await beginCreate(page);
    if (change === 'unmount_project') {
      await control(page, 'unmount');
      await expect(review(page)).toHaveCount(0);
      await control(page, 'switchProject');
    } else await control(page, change);
    await flush(page);
    const beforeRelease = await snapshot(page);
    await control(page, 'release', 'create');
    await flush(page);
    const observed = await snapshot(page);
    evidence.note('late_create_transport_fixture', { change, beforeRelease, observed, real_backend_called: false });
    expect(observed.requests.filter(row => row.op === 'runReport')).toHaveLength(0);
    expect(observed.requests.slice(beforeRelease.requests.length)).toHaveLength(0);
  });
}

test('late initial profiles cannot overwrite a newly selected project', async ({ page, evidence }) => {
  await mount(page, 'profiles');
  await expect.poll(async () => (await snapshot(page)).pending).toContain('profiles');
  await control(page, 'switchProject');
  await expect(page.getByLabel('Observed owner')).toHaveText('project-b:default');
  await control(page, 'release', 'profiles');
  await flush(page);
  evidence.note('late_profile_transport_fixture', await snapshot(page));
  await expect(review(page).getByLabel('비교할 라벨셋')).toHaveValue('project-b:default-candidate', { timeout: 2000 });
  await expect(review(page)).not.toContainText('Candidate project-a:default');
});

test('late report read cannot appear in a new labelset', async ({ page, evidence }) => {
  await mount(page);
  await control(page, 'hold', 'report');
  await review(page).getByRole('button', { name: /누락 1 · 추가 0/ }).click();
  await expect.poll(async () => (await snapshot(page)).pending).toContain('report');
  await control(page, 'switchLabelset');
  await flush(page);
  await control(page, 'release', 'report');
  await flush(page);
  evidence.note('late_report_transport_fixture', await snapshot(page));
  await expect(review(page).getByRole('region', { name: '라벨 검수 결과', exact: true })).toHaveCount(0);
});

for (const outcome of ['release', 'reject']) {
  test(`late policy ${outcome} cannot affect a new actor or role`, async ({ page, evidence }) => {
    await mount(page);
    await control(page, 'hold', 'policy');
    await review(page).getByLabel('정답 이미지도 학습·시험에 사용').click();
    await expect.poll(async () => (await snapshot(page)).pending).toContain('policy');
    await control(page, 'actorRole');
    await expect(page.getByLabel('Observed manager')).toHaveText('false');
    await control(page, outcome, 'policy');
    await flush(page);
    evidence.note('late_policy_transport_fixture', await snapshot(page));
    await expect(review(page).getByLabel('정답 이미지도 학습·시험에 사용')).not.toBeChecked();
    await expect(review(page).getByRole('alert').filter({ hasText: 'old gold transport refused' })).toHaveCount(0);
  });
}

test('late real image-open continuation cannot close a replacement review', async ({ page, evidence }) => {
  await mount(page);
  await review(page).getByRole('button', { name: /누락 1 · 추가 0/ }).click();
  const result = review(page).getByRole('region', { name: '라벨 검수 결과', exact: true });
  await expect(result).toBeVisible();
  await control(page, 'hold', 'image');
  await result.getByRole('button', { name: 'sample-a.png', exact: true }).click();
  await expect.poll(async () => (await snapshot(page)).pending).toContain('image');
  await control(page, 'unmount');
  await expect(review(page)).toHaveCount(0);
  await control(page, 'remount');
  await expect(review(page)).toBeVisible();
  await control(page, 'release', 'image');
  await flush(page);
  const observed = await snapshot(page);
  evidence.note('late_image_callback_transport_fixture', { observed, real_project_store_method: true, backend_called: false });
  expect(observed.callbackCount).toBe(0);
  await expect(review(page)).toBeVisible();
});

test('same-scope create, saved report, policy and actual image opening remain usable', async ({ page, evidence }) => {
  await beginCreate(page);
  await control(page, 'release', 'create');
  const result = review(page).getByRole('region', { name: '라벨 검수 결과', exact: true });
  await expect(result).toBeVisible();
  await expect.poll(async () => (await snapshot(page)).requests.filter(row => row.op === 'runReport').length).toBe(1);
  const policy = review(page).getByLabel('정답 이미지도 학습·시험에 사용');
  await expect(policy).toBeEnabled();
  await policy.click();
  await expect(policy).toBeChecked();
  await result.getByRole('button', { name: 'sample-a.png', exact: true }).click();
  await expect.poll(async () => (await snapshot(page)).callbackCount).toBe(1);
  await expect(review(page)).toHaveCount(0);
  const observed = await snapshot(page);
  expect(observed.annotationPath).toBe('/fixture/source-project-a/sample-a.png');
  expect(observed.requests.filter(row => row.op === 'create')[0].args).toMatchObject({
    actor: 'Park', reference_labelset: 'project-a:default-ref', candidate_labelset: 'project-a:default-candidate',
    gold_images: ['/fixture/source-project-a/sample-a.png'], tolerance: 0.5, task: 'detection',
  });
  evidence.note('same_scope_gold_review_transport_fixture', { observed, backend_called: false });
});

test('same-scope saved result read, rerun and retirement remain usable', async ({ page, evidence }) => {
  await mount(page);
  await review(page).getByRole('button', { name: /누락 1 · 추가 0/ }).click();
  await expect(review(page).getByRole('region', { name: '라벨 검수 결과', exact: true })).toBeVisible();
  await review(page).getByRole('button', { name: '다시 실행', exact: true }).click();
  await expect.poll(async () => (await snapshot(page)).requests.filter(row => row.op === 'runReport').length).toBe(1);
  await expect(review(page).getByRole('button', { name: '그만 쓰기', exact: true })).toBeEnabled();
  await review(page).getByRole('button', { name: '그만 쓰기', exact: true }).click();
  await expect(review(page).getByRole('button', { name: '다시 실행', exact: true })).toHaveCount(0);
  await expect(review(page)).toContainText('아직 검수 기준이 없습니다.');
  const observed = await snapshot(page);
  expect(observed.requests.filter(row => row.op === 'retire')).toHaveLength(1);
  expect(observed.requests.filter(row => row.op === 'report')).toHaveLength(3);
  evidence.note('same_scope_rerun_retire_transport_fixture', { observed, backend_called: false });
});
