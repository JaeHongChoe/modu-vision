/** Owned model-free saved evidence -> actual advisory API -> actual existing queue UI. */
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import {execFileSync} from 'node:child_process';
import {test,expect} from './fixtures/test';
import {installDesktopHostShim} from './fixtures/desktop-host-shim';
import {png} from './qa/appFlow';
const harness=require('./fixtures/harness.cjs');
const sha=(file:string)=>crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex');

// The probability matrix and model receipt identities are controlled evidence,
// never inference. Cohort/version/source metadata use the actual owned engines.
const seed=String.raw`
from pathlib import Path
from types import SimpleNamespace
import hashlib,json,sys
from backend.engine.annotation_storage import set_request_annotation_root,set_request_project_root,reset_request_annotation_root,reset_request_project_root
from backend.engine.dataset_loaders import split_root_scope
from backend.engine.dataset_metadata import metadata_for_path
from backend.engine.evaluation_history import EvaluationHistory
from backend.remote.evaluation_cohort import freeze_cohort,digest
root,project_dir,source=map(lambda value:Path(value).resolve(),sys.argv[1:4])
if not project_dir.is_relative_to(root) or not source.is_relative_to(root):raise ValueError('Owned workspace only')
project=json.loads((project_dir/'project.json').read_text())
version_id=sys.argv[4]
a=set_request_annotation_root(Path(project['annotations_dir']));p=set_request_project_root(project_dir)
try:
 with split_root_scope(Path(project['dataset_dir'])/'splits'):
  selection={hashlib.sha256(image.read_bytes()).hexdigest() for partition in ('train','val') for image in (source/partition).rglob('*.png')}
  cohort=freeze_cohort(project,version_id,source,'classification',{'task':'classification','classes':['NG','OK']},SimpleNamespace(selection_sha256=selection))
  rows=[]
  for index,sample in enumerate(cohort['descriptor']['ordered_samples']):
   image=source/sample['source_relative_path'];metadata=metadata_for_path(project_dir,source,image,Path(project['annotations_dir']))
   scores={'NG':.51,'OK':.49} if sample['truth']=='NG' else {'NG':.01,'OK':.99}
   predicted=max(scores,key=scores.get)
   rows.append({**{key:metadata[key] for key in ('image_uuid','revision','content_hash')},'sample_id':sample['sample_id'],'file_path':str(image),'input_sha256':sample['sha256'],'truth_sha256':digest(sample['truth']),'ground_truth':sample['truth'],'predicted_class':predicted,'confidence':scores[predicted],'class_scores':scores,'is_correct':sample['truth']==predicted})
  common={'cohort_sha256':cohort['descriptor']['cohort_sha256'],'dataset_version_id':version_id,'image_count':len(rows),'split':'test'}
  receipt={key:cohort[key] for key in ('archive_sha256','snapshot_manifest_sha256','version_manifest_sha256')}
  receipt.update(checkpoint_sha256='a'*64,training_snapshot_sha256='b'*64,metadata_sha256='c'*64)
  result={'task':'classification','job_id':'controlled-saved-probabilities-no-model','test_predictions':rows,'common_cohort':common,'confusion_matrix':{'classes':['NG','OK']},'metrics':{'evaluated_split':'test','selection_overlap':False},'evaluation_binding_sha256':'d'*64,'input_receipt':receipt,'fixture_kind':'controlled_full_probabilities_no_model_inference'}
  binding={'source_dataset_path':str(source),'task':'classification','labelset_id':project.get('active_labelset_id','default'),'common_cohort':common,'checkpoint_sha256':'a'*64,'input_receipt':receipt,'evaluation_binding_sha256':'d'*64}
  history=EvaluationHistory(Path(project['reports_dir'])/'evaluations');record=history.append(result,binding)
  print(json.dumps({'evaluation_id':record['evaluation_id'],'cohort_sha256':common['cohort_sha256'],'rows':rows,'saved_evaluation_path':str(history.directory/(record['evaluation_id']+'.json')),'seeded_probabilities_not_inference':True}))
finally:reset_request_annotation_root(a);reset_request_project_root(p)
`;

test('embedded label-quality ranking preserves correctly predicted low margin and resumes historical review',async({page,request,renderer,workspace,evidence})=>{
 await installDesktopHostShim(page,renderer.port);
 const api=async(route:string,body?:unknown,method?:string)=>{const response=await request.fetch(renderer.origin+route,{method:method||(body===undefined?'GET':'POST'),...(body===undefined?{}:{data:body})});expect(response.ok(),await response.text()).toBe(true);return response.json();};
 const source=path.join(workspace.root,'quality-source');const inputs:{path:string;sha256:string}[]=[];
 for(const [partition,offset] of [['train',11],['val',43],['test',79]] as const)for(const [label,color] of [['NG',130],['OK',20]] as const){const file=path.join(source,partition,label,'sample.png');fs.mkdirSync(path.dirname(file),{recursive:true});fs.writeFileSync(file,png(64,3,(x,y)=>[color,(x+offset)%255,(y+offset)%255]));inputs.push({path:file,sha256:sha(file)});}
 const project=await api('/api/project/create',{name:'Embedded saved label quality',task:'classification'});
 await api('/api/project/update',{source_dataset_dir:source},'PUT');await api('/api/dataset/import',{folder_path:source,task:'classification'});
 const version=await api('/api/dataset/versions',{name:'Owned heldout label-quality fixture'});
 const fixture=JSON.parse(execFileSync(harness.resolvePython(),['-c',seed,workspace.root,project.project_dir,source,version.id],{cwd:harness.REPO_ROOT,encoding:'utf8',timeout:60_000,env:{...process.env,VISION_AI_STUDIO_USER_DATA_DIR:workspace.userData}}));
 expect(fixture.rows).toHaveLength(2);expect(fixture.rows.every((row:any)=>row.is_correct&&Object.keys(row.class_scores).length===2)).toBe(true);
 const savedEvaluationSha=sha(fixture.saved_evaluation_path);
 const evaluations=await api('/api/data-workbench/review-evaluations');expect(evaluations.evaluations.find((row:any)=>row.id===fixture.evaluation_id)?.label_quality_available).toBe(true);
 const navigate=async()=>{await page.goto(renderer.url);await expect(page.getByTitle('프로젝트 관리',{exact:true})).toContainText(project.name);await page.getByRole('navigation',{name:'Workflow Stages'}).getByRole('button').nth(1).click();const focus=page.getByRole('button',{name:'집중 편집',exact:true});if(await focus.getAttribute('aria-pressed')==='true')await focus.click();await page.getByText('저장 검토 큐 · 오류·불일치·라벨 재확인',{exact:true}).click();};
 await navigate();const panel=page.getByRole('region',{name:'저장된 검토 큐'});
 await expect(panel.getByRole('combobox',{name:'검토 큐 원본 평가',exact:true})).toHaveValue(fixture.evaluation_id);
 const create=panel.getByRole('button',{name:'라벨 재확인 순위',exact:true});await expect(create).toBeEnabled();
 const analysisResponse=page.waitForResponse(response=>new URL(response.url()).pathname==='/api/data-workbench/label-quality-analyses'&&response.request().method()==='POST');
 const queueResponse=page.waitForResponse(response=>new URL(response.url()).pathname==='/api/data-workbench/review-queues'&&response.request().method()==='POST');
 await create.click();const analysisHttp=await analysisResponse;expect(analysisHttp.status()).toBe(200);const analysis=await analysisHttp.json();const queueHttp=await queueResponse;expect(queueHttp.status()).toBe(200);const queue=await queueHttp.json();
 expect(queue.mode).toBe('label_quality');expect(queue.items.map((row:any)=>row.relative_path)).toEqual(['test/NG/sample.png','test/OK/sample.png']);expect(queue.items[0].given_class).toBe('NG');expect(queue.items[0].predicted_class).toBe('NG');expect(queue.items[0].quality_score).toBeCloseTo(.51);expect(queue.items[0].reasons).toEqual(['label_quality']);
 await expect(panel).toContainText('0.510');await expect(panel).toContainText('과거 LOT·그룹 독립성은 확인되지 않았습니다');await expect(panel).toContainText('검토 진행 0 / 2');
 const analysisFile=path.join(project.dataset_dir,'data_workbench',crypto.createHash('sha256').update(fs.realpathSync(source)).digest('hex').slice(0,24),'label_quality',analysis.id+'.json');const immutableAnalysisSha=sha(analysisFile);
 await evidence.screenshot(page,'label-quality-ranked-correct-low-margin');
 const row=queue.items[0];await api('/api/dataset/metadata/class-override',{image_path:row.file_path,class_name:'OK',actor:'Owned browser reviewer'},'PUT');
 const changed=await api('/api/data-workbench/review-queues/'+queue.id);expect(changed.basis_changed).toBe(true);expect(changed.items[0].basis_changed).toBe(true);expect(changed.items[0].given_class).toBe('NG');
 const fresh=await request.post(renderer.origin+'/api/data-workbench/review-queues',{data:{analysis_id:analysis.id,mode:'label_quality'}});expect(fresh.status()).toBe(409);
 await navigate();await expect(panel).toContainText('분석 후 라벨·자료 기록이 변경되었습니다');await expect(panel.getByRole('combobox',{name:'저장 검토 큐 선택',exact:true})).toHaveValue(queue.id);
 await panel.getByRole('button',{name:'현재 항목 열기',exact:true}).click();
 await page.getByRole('button',{name:'이미지 정보·검토',exact:true}).click();await page.getByRole('textbox',{name:'작업자·검토자 이름',exact:true}).fill('Owned browser reviewer');await page.getByRole('button',{name:'이미지 정보·검토',exact:true}).click();
 const advance=panel.getByRole('button',{name:'검토 완료 · 다음',exact:true});await expect(advance).toBeEnabled();await advance.click();await expect(panel).toContainText('검토 진행 1 / 2');
 await navigate();await expect(panel).toContainText('검토 진행 1 / 2');await expect(panel).toContainText('분석 후 라벨·자료 기록이 변경되었습니다');
 const reopened=await api('/api/data-workbench/review-queues/'+queue.id);expect(reopened.cursor).toBe(1);expect(reopened.history[0].actor).toBe('Owned browser reviewer');expect(reopened.items[0].given_class).toBe('NG');
 expect(sha(analysisFile)).toBe(immutableAnalysisSha);expect(sha(fixture.saved_evaluation_path)).toBe(savedEvaluationSha);for(const input of inputs)expect(sha(input.path)).toBe(input.sha256);
 await evidence.screenshot(page,'label-quality-historical-review-resumed');
 evidence.note('embedded_label_quality',{project_id:project.id,evaluation_id:fixture.evaluation_id,cohort_sha256:fixture.cohort_sha256,analysis_id:analysis.id,queue_id:queue.id,ranked_items:queue.items,reopened_cursor:reopened.cursor,basis_changed:reopened.basis_changed,history:reopened.history,source_bytes_unchanged:true,saved_evaluation_unchanged:true,immutable_analysis_unchanged:true,seeded_full_probability_matrix_and_model_receipt:true,actual_cohort_freeze_source_metadata_analysis_ranking_queue_UI_label_edit_API_and_resume:true,no_model_training_or_inference:true,no_provider_calls:true,no_label_approval:true});
});
