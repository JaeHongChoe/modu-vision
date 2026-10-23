import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import {execFileSync} from 'node:child_process';
import type {Request} from '@playwright/test';
import {test,expect} from './fixtures/test';
import {installDesktopHostShim} from './fixtures/desktop-host-shim';
const harness=require('./fixtures/harness.cjs');
const sha=(file:string)=>crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex');
function files(root:string):Record<string,string>{
  if(!fs.existsSync(root))return {};
  return Object.fromEntries(fs.readdirSync(root,{withFileTypes:true}).sort((a,b)=>a.name.localeCompare(b.name)).flatMap(entry=>{
    const file=path.join(root,entry.name);expect(entry.isSymbolicLink()).toBe(false);
    return entry.isDirectory()?Object.entries(files(file)).map(([name,hash])=>[`${entry.name}/${name}`,hash]):[[entry.name,sha(file)]];
  }));
}

// One local SAM2 call through the real browser Canvas/controller/backend/persistence. No HTTP provider mocks,
// Grounding DINO, feature encoder, training, adoption or external images. This is a control, not mask-quality approval.
test('closed-panel opt-in point creates one real pending SAM2 preview without adoption',
  {tag:'@owned-model'},async({page,request,renderer,workspace,evidence})=>{
  test.setTimeout(240_000);
  const sam=process.env.MV_E2E_SAM2_MODEL_DIR;
  test.skip(!sam,'Requires explicitly supplied existing local SAM2 model directory');
  expect(process.env.HF_HUB_OFFLINE).toBe('1');expect(process.env.TRANSFORMERS_OFFLINE).toBe('1');
  const modelFiles=Object.fromEntries(['config.json','preprocessor_config.json','model.safetensors'].map(name=>[name,sha(path.join(sam!,name))]));
  const source=path.join(workspace.root,'sam2-point-input'),image=path.join(source,'dark-square.png');
  fs.mkdirSync(source);
  execFileSync(harness.resolvePython(),['-c',"from PIL import Image,ImageDraw; import sys; im=Image.new('RGB',(256,256),(244,244,244)); ImageDraw.Draw(im).rectangle((64,64,192,192),fill=(24,24,24)); im.save(sys.argv[1])",image],{timeout:15_000});
  fs.writeFileSync(image.replace(/\.png$/,'.json'),JSON.stringify({imageWidth:256,imageHeight:256,shapes:[
    {label:'baseline mark',shape_type:'rectangle',points:[[8,8],[24,24]]},
  ]})+'\n');
  const originalSources=files(source);
  const api=async(route:string,body?:unknown,method?:string)=>{
    const reply=await request.fetch(renderer.origin+route,{method:method||(body===undefined?'GET':'POST'),...(body===undefined?{}:{data:body})});
    expect(reply.ok(),await reply.text()).toBe(true);return reply.json();
  };
  await installDesktopHostShim(page,renderer.port);
  await api('/api/project/create',{name:'Owned offline SAM2 point',task:'segmentation'});
  const project=await api('/api/project/update',{source_dataset_dir:source},'PUT');
  await api('/api/dataset/import',{folder_path:source,task:'segmentation'});
  const setup=await api('/api/label-candidates/setup',{mask_model_dir:sam},'PUT');
  expect(setup.providers.foundation.ready).toBe(true);expect(setup.configuration.model_dir||null).toBeNull();
  const annotationUrl='/api/annotations/dark-square?file_path='+encodeURIComponent(image);
  const initialAnnotation=await api(annotationUrl);
  expect(initialAnnotation.annotations).toHaveLength(1);expect(initialAnnotation.annotations[0].label).toBe('baseline mark');
  await page.goto(renderer.url);await expect(page.getByTitle('프로젝트 관리',{exact:true})).toContainText(project.name);
  await page.getByRole('navigation',{name:'Workflow Stages'}).getByRole('button').nth(0).click();
  await page.getByRole('button',{name:'dark-square.png 라벨링에서 열기',exact:true}).click();
  await page.getByTitle('화면 맞춤 (F)',{exact:true}).click();
  await page.getByRole('button',{name:'모델 보조 라벨링 패널',exact:true}).click();
  const panel=page.getByRole('region',{name:'SAM2 기반 라벨링',exact:true});
  const review=page.getByRole('complementary',{name:'모델 보조 라벨링 검토',exact:true});
  const optIn=panel.getByLabel('점 입력 후 후보 만들기',{exact:true});
  await expect(optIn).toBeEnabled();await expect(optIn).not.toBeChecked();
  // The first team workspace GET initializes default settings in workflow.json. Finish that normal UI setup
  // before comparing ALL annotation bytes; retain source bytes from before project import and all image metadata.
  const team=await api('/api/team-data');expect(team.scope.project_id).toBe(project.id);expect(team.settings.revision).toBe(1);
  const annotationBefore=await api(annotationUrl);expect(annotationBefore).toEqual(initialAnnotation);
  const before={source:originalSources,annotations:files(project.annotations_dir),annotation:annotationBefore};
  const ledgerName=Object.keys(before.annotations).find(name=>name.endsWith('/metadata/workflow.json'));expect(ledgerName).toBeTruthy();
  const ledgerFile=path.join(project.annotations_dir,ledgerName!),ledgerBefore=JSON.parse(fs.readFileSync(ledgerFile,'utf8'));
  const imageMetadataBefore=ledgerBefore.images['dark-square.png'];expect(imageMetadataBefore).toEqual(annotationBefore.metadata);
  expect(ledgerBefore.team_data.settings).toEqual(team.settings);expect(imageMetadataBefore.revision).toBe(1);
  expect(imageMetadataBefore.audit.map((row:{action:string})=>row.action)).toEqual(['registered']);expect(imageMetadataBefore.annotation_hash).toBe(sha(image.replace(/\.png$/,'.json')));
  evidence.note('owned_sam2_before',{...before,ledger:ledgerBefore});
  await panel.getByLabel('텍스트 검출 프롬프트',{exact:true}).fill('');await panel.getByLabel('SAM2 후보 클래스',{exact:true}).fill('Dark square');
  await panel.getByLabel('라벨링 실행 장치',{exact:true}).selectOption('cpu');await panel.getByLabel('SAM2 출력 형태',{exact:true}).selectOption('polygon');
  await panel.getByLabel('후보 점수',{exact:true}).fill('0');await panel.getByLabel('최대 후보',{exact:true}).fill('1');
  await panel.getByRole('button',{name:'캔버스 점 prompt',exact:true}).click();
  await panel.getByLabel('SAM2 점 positive negative',{exact:true}).selectOption('1');await optIn.check();
  await review.getByRole('button',{name:'닫기',exact:true}).click();await expect(review).toHaveCount(0);
  const writes:Array<{path:string;body:string|null}>=[];
  const observe=(wire:Request)=>{const url=new URL(wire.url());if(url.origin===renderer.origin&&url.pathname.startsWith('/api/')&&!['GET','HEAD','OPTIONS'].includes(wire.method()))writes.push({path:url.pathname,body:wire.postData()});};
  page.on('request',observe);
  const response=page.waitForResponse(reply=>new URL(reply.url()).pathname==='/api/label-candidates/generate'&&reply.request().method()==='POST',{timeout:180_000});
  const canvas=page.locator('[data-canvas-container="true"] > canvas').nth(2);await expect(canvas).toBeVisible();
  const box=await canvas.boundingBox();expect(box).not.toBeNull();await canvas.click({position:{x:box!.width/2,y:box!.height/2}});
  const reply=await response;expect(reply.ok(),await reply.text()).toBe(true);const generated=await reply.json(),posted=reply.request().postDataJSON();
  evidence.note('owned_sam2_generation',{setup,project,posted,generated,model_files_sha256:modelFiles,provider_http_mocked:false});
  expect(posted).toMatchObject({backend:'foundation',image_path:image,label:'Dark square',prompt:'',device:'cpu',threshold:0,max_candidates:1,output_geometry:'polygon',boxes:[],positive_examples:[],negative_examples:[]});expect(posted.suggestion_model_id||null).toBeNull();
  expect(posted.points).toHaveLength(1);expect(posted.points[0].label).toBe(1);expect(posted.points[0].x).toBeCloseTo(128,0);expect(posted.points[0].y).toBeCloseTo(128,0);
  expect(generated.status).toBe('pending');expect(generated.candidates).toHaveLength(1);expect(generated.accepted_candidate_ids).toEqual([]);
  expect(generated.candidates[0]).toMatchObject({source:'sam2',annotation:{type:'polygon',label:'Dark square'},provenance:{provider:'sam2',device:'cpu',model_dir:sam}});
  await expect(review).toContainText('후보 1개를 만들었습니다. 검토 후 선택하세요.');
  const preview=review.getByAltText('모델 라벨 제안 미리보기',{exact:true});await expect(preview).toBeVisible();await expect(preview).toHaveJSProperty('naturalWidth',256);
  await preview.scrollIntoViewIfNeeded();await expect(preview).toBeInViewport();
  await expect(review.locator('svg polygon')).toHaveCount(1);await expect(review.getByRole('button',{name:'초안 검토에서 채택',exact:true})).toBeVisible();
  const saved=await api('/api/label-suggestions/'+generated.id);expect(saved).toEqual(generated);
  const savedFile=path.join(project.project_dir,'label_suggestions',generated.id+'.json');expect(JSON.parse(fs.readFileSync(savedFile,'utf8'))).toEqual(generated);evidence.addFile(savedFile);
  expect((await api('/api/label-suggestions')).suggestions.map((row:{id:string;status:string})=>({id:row.id,status:row.status}))).toEqual([{id:generated.id,status:'pending'}]);
  expect({source:files(source),annotations:files(project.annotations_dir),annotation:await api(annotationUrl)}).toEqual(before);
  const ledgerAfter=JSON.parse(fs.readFileSync(ledgerFile,'utf8'));expect(ledgerAfter).toEqual(ledgerBefore);
  expect(ledgerAfter.images['dark-square.png']).toMatchObject({revision:imageMetadataBefore.revision,audit:imageMetadataBefore.audit,annotation_hash:imageMetadataBefore.annotation_hash});
  expect(writes.map(row=>row.path)).toEqual(['/api/label-candidates/generate']);await expect(review).toContainText('현재 이미지 · dark-square.png');page.off('request',observe);
  expect(Object.fromEntries(Object.keys(modelFiles).map(name=>[name,sha(path.join(sam!,name))]))).toEqual(modelFiles);
  const sourceFiles=['scripts/e2e/foundation-quick-point-owned.spec.ts','src/renderer/components/labeling/foundationQuickPointController.ts','src/renderer/components/labeling/CandidateProviderControls.tsx','src/renderer/components/labeling/ModelAssistPanel.tsx','src/renderer/stores/useFoundationPromptStore.ts','src/renderer/components/labeling/LabelingCanvas.tsx','backend/api/routes_label_candidates.py','backend/engine/foundation_labeling.py'];
  evidence.note('owned_sam2_quick_point',{setup,project,posted,generated,saved,writes,model_files_sha256:modelFiles,source_files_sha256:Object.fromEntries(sourceFiles.map(file=>[file,sha(path.join(harness.REPO_ROOT,file))])),
    offline:true,actual_local_cpu_sam2:true,provider_http_mocked:false,inference_count:1,original_coordinate_midpoint:[128,128],source_and_existing_label_bytes_unchanged:true,annotation_before:before.annotation,
    auto_adoption_count:0,no_image_advance:true,ledger_before:ledgerBefore,ledger_after:ledgerAfter,training:false,quality_approved:false});
  evidence.addFile(image);evidence.addFile(image.replace(/\.png$/,'.json'));await evidence.screenshot(page,'owned-offline-sam2-point-pending-preview');
});
