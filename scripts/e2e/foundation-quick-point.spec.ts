import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import type {Request,Route} from '@playwright/test';
import type {FoundationProposal,FoundationSetup} from '../../src/renderer/services/foundationLabelingApi';
import {test,expect} from './fixtures/test';
import {installDesktopHostShim} from './fixtures/desktop-host-shim';

const sha=(file:string)=>crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex');
function files(root:string):Record<string,string>{
  if(!fs.existsSync(root))return {};
  return Object.fromEntries(fs.readdirSync(root,{withFileTypes:true}).sort((a,b)=>a.name.localeCompare(b.name)).flatMap(entry=>{
    const file=path.join(root,entry.name);expect(entry.isSymbolicLink()).toBe(false);
    return entry.isDirectory()?Object.entries(files(file)).map(([name,hash])=>[`${entry.name}/${name}`,hash]):[[entry.name,sha(file)]];
  }));
}

// Public CI control: project/image/annotation reads, Canvas, stores, popover and preview are real. Readiness,
// generation and the in-memory proposal list are explicitly mocked HTTP boundaries; this certifies no learned model.
test('opt-in canvas points survive a closed panel and keep custom options without adopting mocked drafts',async({page,request,renderer,workspace,evidence})=>{
  test.setTimeout(120_000);
  const api=async(route:string,body?:unknown,method?:string)=>{
    const reply=await request.fetch(renderer.origin+route,{method:method||(body===undefined?'GET':'POST'),...(body===undefined?{}:{data:body})});
    expect(reply.ok(),await reply.text()).toBe(true);return reply.json();
  };
  await installDesktopHostShim(page,renderer.port);
  await api('/api/project/create',{name:'Public quick-point UI control',task:'segmentation'});
  const project=await api('/api/project/update',{source_dataset_dir:workspace.dataset},'PUT');
  await api('/api/dataset/import',{folder_path:workspace.dataset,task:'segmentation'});
  const image=workspace.images[0];
  const mockSetup:FoundationSetup={configuration:{},labelset_id:project.active_labelset_id||'default',labelset_version:'controlled-browser-version',providers:{
    foundation:{ready:true,error:null,limits:'Explicit browser mock; no model loaded.',dependency_available:true,state:'ready',weights_check:'ok'},
    grounding_dino:{ready:true,error:null,limits:'Explicit browser mock; no model loaded.',dependency_available:true},
  }};
  const proposals:FoundationProposal[]=[],generatedRequests:Array<{body:Record<string,unknown>;headers:Record<string,string>}>=[],writes:Array<{path:string;body:string|null}>=[];
  let setupReads=0,listReads=0;
  const setupRoute=async(route:Route)=>{setupReads++;await route.fulfill({status:200,json:mockSetup});};
  const listRoute=async(route:Route)=>{
    listReads++;const selected=new URL(route.request().url()).searchParams.get('image_path');
    await route.fulfill({status:200,json:{suggestions:proposals.filter(row=>!selected||row.image_path===selected)}});
  };
  const generateRoute=async(route:Route)=>{
    const body=route.request().postDataJSON() as Record<string,unknown>;generatedRequests.push({body,headers:route.request().headers()});
    const next:FoundationProposal={id:`controlled-point-${generatedRequests.length}`,status:'pending',created_at:'2026-10-24T00:00:00Z',
      image_path:String(body.image_path),image_id:path.basename(String(body.image_path),'.png'),image_width:32,image_height:32,
      job_id:'controlled-http-provider',task:'segmentation',threshold:Number(body.threshold),confidence:.9,latency_ms:0,
      backend:'foundation',labelset_id:mockSetup.labelset_id,labelset_version:mockSetup.labelset_version,
      support_limits:'Controlled browser HTTP fixture; no learned inference or quality claim.',accepted_candidate_ids:[],backup_version_id:null,
      candidates:[{id:'controlled-candidate',confidence:.9,source:'controlled_http_fixture',annotation:{id:'controlled-shape',type:'polygon',
        label:String(body.label),category_id:1,polygon:[[8,8],[24,8],[16,24]],color:'#22d3ee'}}],
    };
    proposals.push(next);await route.fulfill({status:200,json:next});
  };
  const setupUrl=renderer.origin+'/api/label-candidates/setup',generateUrl=renderer.origin+'/api/label-candidates/generate';
  const listUrl=(url:URL)=>url.origin===renderer.origin&&url.pathname==='/api/label-suggestions';
  await page.route(setupUrl,setupRoute);await page.route(generateUrl,generateRoute);await page.route(listUrl,listRoute);
  const observe=(wire:Request)=>{const url=new URL(wire.url());if(url.origin===renderer.origin&&url.pathname.startsWith('/api/')&&!['GET','HEAD','OPTIONS'].includes(wire.method()))writes.push({path:url.pathname,body:wire.postData()});};
  try{
    await page.goto(renderer.url);await expect(page.getByTitle('프로젝트 관리',{exact:true})).toContainText(project.name);
    await page.getByRole('navigation',{name:'Workflow Stages'}).getByRole('button').nth(0).click();
    const raw=page.waitForResponse(reply=>new URL(reply.url()).pathname.startsWith('/api/dataset/raw/')&&new URL(reply.url()).searchParams.get('file_path')===image.path);
    await page.getByRole('button',{name:path.basename(image.path)+' 라벨링에서 열기',exact:true}).click();
    expect((await raw).status()).toBe(200);
    const open=page.getByRole('button',{name:'모델 보조 라벨링 패널',exact:true});
    const review=page.getByRole('complementary',{name:'모델 보조 라벨링 검토',exact:true});
    const panel=page.getByRole('region',{name:'SAM2 기반 라벨링',exact:true});
    const canvas=page.locator('[data-canvas-container="true"] > canvas').nth(2);
    const centerPoint=async()=>{
      await page.getByTitle('화면 맞춤 (F)',{exact:true}).click();await expect(canvas).toBeVisible();
      const box=await canvas.boundingBox();expect(box).not.toBeNull();expect(box!.width).toBeGreaterThan(80);expect(box!.height).toBeGreaterThan(80);
      await canvas.click({position:{x:box!.width/2,y:box!.height/2}});
    };
    await open.click();await expect(panel.getByLabel('점 입력 후 후보 만들기',{exact:true})).toBeEnabled();
    await expect(panel.getByLabel('점 입력 후 후보 만들기',{exact:true})).not.toBeChecked();
    const before={source:files(workspace.dataset),annotations:files(project.annotations_dir)};page.on('request',observe);
    // The default-off mode records a real Canvas point but submits no inference request.
    await panel.getByRole('button',{name:'캔버스 점 prompt',exact:true}).click();await review.getByRole('button',{name:'닫기',exact:true}).click();
    await centerPoint();await open.click();await expect(panel).toContainText('현재 원본 좌표 · 점 1 / 박스 0');expect(generatedRequests).toHaveLength(0);
    await panel.getByRole('button',{name:'점·박스 지우기',exact:true}).click();
    await panel.getByLabel('텍스트 검출 프롬프트',{exact:true}).fill('controlled scratch.');
    await panel.getByLabel('SAM2 후보 클래스',{exact:true}).fill('Controlled scratch');
    await panel.getByLabel('라벨링 실행 장치',{exact:true}).selectOption('auto');
    await panel.getByLabel('SAM2 출력 형태',{exact:true}).selectOption('polygon');
    await panel.getByLabel('후보 점수',{exact:true}).fill('0.73');await panel.getByLabel('텍스트 점수',{exact:true}).fill('0.31');await panel.getByLabel('최대 후보',{exact:true}).fill('3');
    await panel.locator('summary').filter({hasText:'원본 px 면적·너비·높이 필터'}).click();await panel.getByLabel('SAM2 최소 면적',{exact:true}).fill('5');
    await panel.locator('summary').filter({hasText:'현재 이미지 제외 영역'}).click();await panel.getByLabel('제외할 원본 영역',{exact:true}).fill('2,3,10,12');await panel.getByRole('button',{name:'제외 영역 추가',exact:true}).click();
    await panel.getByLabel('SAM2 점 positive negative',{exact:true}).selectOption('0');
    await panel.getByLabel('점 입력 후 후보 만들기',{exact:true}).check();
    await review.getByRole('button',{name:'닫기',exact:true}).click();await expect(review).toHaveCount(0);
    const generated=page.waitForResponse(reply=>reply.url()===generateUrl&&reply.request().method()==='POST');
    await centerPoint();expect((await generated).status()).toBe(200);
    await expect(review).toBeVisible();const preview=review.getByAltText('모델 라벨 제안 미리보기',{exact:true});await expect(preview).toBeVisible();
    await expect(preview).toHaveJSProperty('naturalWidth',32);await expect(review.locator('svg polygon')).toHaveAttribute('points','8,8 24,8 16,24');
    await expect(review.getByRole('button',{name:'초안 검토에서 채택',exact:true})).toBeVisible();
    expect(generatedRequests).toHaveLength(1);const posted=generatedRequests[0].body;
    expect(posted).toMatchObject({backend:'foundation',image_path:image.path,label:'Controlled scratch',prompt:'controlled scratch.',device:'auto',output_geometry:'polygon',threshold:.73,text_threshold:.31,max_candidates:3,
      min_area:5,min_width:0,min_height:0,exclude_regions:[[2,3,10,12]],positive_examples:[],negative_examples:[],boxes:[],labelset_id:mockSetup.labelset_id,labelset_version:'controlled-browser-version'});
    const points=posted.points as Array<{x:number;y:number;label:number}>;expect(points).toHaveLength(1);expect(points[0].label).toBe(0);
    // Fit centers the actual 32x32 source on the Canvas: its midpoint is source pixel (16,16), independently of zoom.
    expect(points[0].x).toBeCloseTo(16,0);expect(points[0].y).toBeCloseTo(16,0);
    expect(generatedRequests[0].headers['x-vision-project']).toBe(project.id);
    const assertOptions=async()=>{
      await expect(panel.getByLabel('점 입력 후 후보 만들기',{exact:true})).toBeChecked();await expect(panel.getByLabel('SAM2 후보 클래스',{exact:true})).toHaveValue('Controlled scratch');
      await expect(panel.getByLabel('텍스트 검출 프롬프트',{exact:true})).toHaveValue('controlled scratch.');await expect(panel.getByLabel('라벨링 실행 장치',{exact:true})).toHaveValue('auto');await expect(panel.getByLabel('SAM2 출력 형태',{exact:true})).toHaveValue('polygon');
      await expect(panel.getByLabel('후보 점수',{exact:true})).toHaveValue('0.73');await expect(panel.getByLabel('텍스트 점수',{exact:true})).toHaveValue('0.31');await expect(panel.getByLabel('최대 후보',{exact:true})).toHaveValue('3');
      await expect(panel.getByLabel('SAM2 최소 면적',{exact:true})).toHaveValue('5');await expect(panel.locator('summary').filter({hasText:'현재 이미지 제외 영역'})).toHaveText('현재 이미지 제외 영역 · 1개');
    };
    await assertOptions();await review.getByRole('button',{name:'닫기',exact:true}).click();await open.click();await assertOptions();await expect(preview).toBeVisible();
    expect(generatedRequests).toHaveLength(1);expect(writes.map(row=>row.path)).toEqual(['/api/label-candidates/generate']);
    expect({source:files(workspace.dataset),annotations:files(project.annotations_dir)}).toEqual(before);
    expect((await api('/api/label-suggestions')).suggestions).toEqual([]); // The HTTP mock never persisted a real backend draft.
    await evidence.screenshot(page,'public-quick-point-controlled-preview-custom-options');
    for(const row of workspace.images){expect(sha(row.path)).toBe(row.sha256);evidence.addFile(row.path);}
    evidence.note('quick_point_browser_control',{project,setup_mocked:true,inference_mocked:true,proposal_list_in_memory:true,actual_model_inference:false,
      real_project_image_canvas_stores_panel_preview:true,default_off_no_request:true,generatedRequests,setupReads,listReads,writes,source_and_annotation_files_unchanged:true,
      no_adoption:true,no_image_advance:true,original_coordinate_midpoint:[16,16],quality_approved:false});
  }finally{
    page.off('request',observe);await page.unroute(setupUrl,setupRoute);await page.unroute(generateUrl,generateRoute);await page.unroute(listUrl,listRoute);
  }
});
