import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import type {Page} from '@playwright/test';
import {test,expect,type Workspace,type Evidence} from './fixtures/test';
import {installDesktopHostShim} from './fixtures/desktop-host-shim';
import {png} from './qa/appFlow';
test.use({actionTimeout:10_000});
type OwnedApi=(route:string,body?:unknown,method?:string)=>Promise<any>;
const hash=(data:Buffer)=>crypto.createHash('sha256').update(data).digest('hex');
const truth='  검사Ａ12\u00a0  ';
async function exercise(page:Page,workspace:Workspace,evidence:Evidence,api:OwnedApi,native:boolean,url?:string){
 const source=path.join(workspace.root,'truth-source');const files:string[]=[];const relative:string[]=[];
 for(let i=0;i<4;i++){const rel=`batch-${i}/part.png`,file=path.join(source,rel);fs.mkdirSync(path.dirname(file),{recursive:true});fs.writeFileSync(file,png(256,3,(x,y)=>[x,y,50+i*30]));files.push(file);relative.push(rel);}
 const originals=Object.fromEntries(files.map(file=>[file,hash(fs.readFileSync(file))]));
 const project=await api('/api/project/create',{name:'Human truth and fitting fixture',task:'classification'});
 await api('/api/project/update',{source_dataset_dir:source},'PUT');await api('/api/dataset/import',{folder_path:source,task:'classification'});
 const trainingPosts:string[]=[];page.on('request',request=>{if(request.method()==='POST'&&/\/(train|jobs)$/.test(new URL(request.url()).pathname))trainingPosts.push(request.url());});
 const open=async(card:string)=>{if(url)await page.goto(url);else await page.reload();await expect(page.getByTitle('프로젝트 관리',{exact:true})).toContainText('Human truth and fitting fixture');await page.getByRole('navigation',{name:'Workflow Stages'}).getByRole('button').nth(2).click();await page.getByRole('region',{name:'모델 학습 허브'}).getByRole('button',{name:new RegExp(`^${card}`)}).click();};
 await open('문자 인식');
 const ocr=page.locator('details').filter({has:page.locator('summary').filter({hasText:'문자 인식 모델 실험'})}).first();
 const expectedOCR=files.map((file,i)=>({image:relative[i],text:truth,split:['train','train','val','test'][i]}));
 for(let i=0;i<4;i++){await page.getByLabel('문자 원본 이미지 선택',{exact:true}).selectOption(files[i]);await page.getByLabel('OCR 실제 정답 문자열',{exact:true}).fill(truth);await page.getByLabel(/^독립 이미지 분할/).selectOption(expectedOCR[i].split);await page.getByRole('button',{name:'선택 이미지의 문자 정답 추가',exact:true}).click();}
 await page.locator('summary').filter({hasText:'정답 표·가져오기 상세 설정'}).click();
 const ocrTable=ocr.getByRole('textbox',{name:/^정답 표 · 한 줄에/});const expectedText=expectedOCR.map(row=>`${row.image}\t${row.text}\t${row.split}`).join('\n');
 await expect(ocrTable).toHaveValue(expectedText);
 const saveOCR=page.waitForResponse(r=>new URL(r.url()).pathname==='/api/ocr/prepare'&&r.request().method()==='POST');await page.getByRole('button',{name:'정답과 이미지 해시 저장',exact:true}).click();const ocrReply=await saveOCR;expect(ocrReply.status()).toBe(200);expect(ocrReply.request().postDataJSON().samples).toEqual(expectedOCR);const ocrSaved=await ocrReply.json();
 const verifyOCR=(row:any)=>{expect(row.samples.map(({image,text,split}:any)=>({image,text,split}))).toEqual(expectedOCR);expect(row.alphabet).toContain(' ');expect(row.alphabet).toContain('\u00a0');expect(row.alphabet).toContain('Ａ');for(const sample of row.samples)expect(sample.source_sha256).toBe(originals[path.join(source,sample.image)]);};
 verifyOCR(ocrSaved);await open('문자 인식');await page.getByRole('button',{name:'저장된 정답 읽기',exact:true}).click();await page.locator('summary').filter({hasText:'정답 표·가져오기 상세 설정'}).click();await expect(ocrTable).toHaveValue(expectedText);verifyOCR(await api(`/api/ocr/manifest?dataset_path=${encodeURIComponent(ocrSaved.dataset_path)}`));
 await evidence.screenshot(page,native?'native-exact-ocr-truth-reopened':'browser-exact-ocr-truth-reopened');
 await open('회전 객체 검출');
 const fitting=page.locator('section').filter({has:page.getByRole('heading',{name:'원본 이미지에서 회전 박스 그리기',exact:true})});
 const cases=[{mode:'center',button:'중심 기준',points:[[80,60],[100,60],[80,70]],direction:315,split:'train'},{mode:'face',button:'한 면 기준',points:[[60,50],[100,50],[60,70]],direction:0,split:'val'},{mode:'irregular',button:'불규칙 외곽',points:[[60,50],[100,50],[100,70],[60,70]],direction:180,split:'test'}];
 const fits:any[]=[];const expectedBoxes:any[]=[];
 for(let i=0;i<cases.length;i++){
  const entry=cases[i];const previewWait=page.waitForResponse(r=>new URL(r.url()).pathname==='/api/rotated-detection/fit-source'&&r.request().method()==='GET');await fitting.getByLabel(/^이미지/).selectOption(files[i]);const preview=await(await previewWait).json();expect(preview.source_sha256).toBe(originals[files[i]]);expect(preview.source_size).toEqual([256,256]);
  await fitting.getByRole('button',{name:entry.button,exact:true}).click();const svg=fitting.getByRole('img',{name:'회전 박스 정답 그리기',exact:true});await expect(svg.locator('image')).toHaveAttribute('href',preview.preview_data_url);await svg.scrollIntoViewIfNeeded();
  const delivered:number[][]=[];for(const [x,y]of entry.points){const b=(await svg.boundingBox())!;const scale=Math.min(b.width/256,b.height/256),left=b.x+(b.width-256*scale)/2,top=b.y+(b.height-256*scale)/2;const clientX=Math.floor(left+x*scale),clientY=Math.floor(top+y*scale);delivered.push([(clientX-left)/scale,(clientY-top)/scale]);await page.mouse.click(clientX,clientY);}
  // Real mouse events use CSS pixels. Independently derive the native-pixel box from those delivered clicks.
  const [first,second,third]=delivered;const expected=entry.mode==='center'?{cx:first[0],cy:first[1],width:2*(second[0]-first[0]),height:2*(third[1]-first[1]),angle_deg:0}:entry.mode==='face'?{cx:(first[0]+second[0])/2,cy:(first[1]+third[1])/2,width:second[0]-first[0],height:third[1]-first[1],angle_deg:0}:{cx:(Math.min(...delivered.map(p=>p[0]))+Math.max(...delivered.map(p=>p[0])))/2,cy:(Math.min(...delivered.map(p=>p[1]))+Math.max(...delivered.map(p=>p[1])))/2,width:Math.max(...delivered.map(p=>p[0]))-Math.min(...delivered.map(p=>p[0])),height:Math.max(...delivered.map(p=>p[1]))-Math.min(...delivered.map(p=>p[1])),angle_deg:0};expectedBoxes.push(Object.fromEntries(Object.entries(expected).map(([k,v])=>[k,Number(v.toFixed(4))])));
  const fitWait=page.waitForResponse(r=>new URL(r.url()).pathname==='/api/rotated-detection/fit-box'&&r.request().method()==='POST');await fitting.getByRole('button',{name:'회전 박스 맞추기',exact:true}).click();const fitReply=await fitWait;expect(fitReply.status()).toBe(200);const fit=await fitReply.json();expect(fit.mode).toBe(entry.mode);expect(fit.source_sha256).toBe(originals[files[i]]);
  for(const [key,value]of Object.entries(expected))expect(fit.box[key]).toBeCloseTo(value,3);
  expect(fit.polygon).toHaveLength(4);const expectedCorners=[[expected.cx-expected.width/2,expected.cy-expected.height/2],[expected.cx+expected.width/2,expected.cy-expected.height/2],[expected.cx+expected.width/2,expected.cy+expected.height/2],[expected.cx-expected.width/2,expected.cy+expected.height/2]].sort();const actualCorners=fit.polygon.sort();for(let j=0;j<4;j++)for(let k=0;k<2;k++)expect(actualCorners[j][k]).toBeCloseTo(expectedCorners[j][k],3);
  await fitting.getByLabel('라벨',{exact:true}).fill('검사 대상');await fitting.getByLabel(/^데이터 분할/).selectOption(entry.split);await fitting.getByLabel('회전 객체 독립 방향',{exact:true}).fill(String(entry.direction));await svg.scrollIntoViewIfNeeded();await evidence.screenshot(page,`${native?'native':'browser'}-${entry.mode}-fitted-box`);await fitting.getByRole('button',{name:'정답 표에 추가',exact:true}).click();fits.push({relative_path:relative[i],entry,delivered,independently_expected_box:expected,fit});
 }
 const obbTable=page.getByRole('textbox',{name:/^정답 표 · 이미지 상대 경로/});const expectedOBB=cases.map((entry,i)=>`${relative[i]}\t검사 대상\t${Object.values(expectedBoxes[i]).join(',')}\t${entry.split}\t${entry.direction}`).join('\n');await expect(obbTable).toHaveValue(expectedOBB);
 const saveOBB=page.waitForResponse(r=>new URL(r.url()).pathname==='/api/rotated-detection/manifest'&&r.request().method()==='POST');await page.getByRole('button',{name:'정답·이미지 해시 저장',exact:true}).click();const obbReply=await saveOBB;expect(obbReply.status()).toBe(200);const obbSaved=await obbReply.json();
 const verifyOBB=(row:any)=>{expect(row.samples).toHaveLength(3);for(let i=0;i<3;i++){const sample=row.samples.find((r:any)=>r.image===relative[i]);expect(sample.split).toBe(cases[i].split);expect(sample.source_sha256).toBe(originals[files[i]]);expect(sample.objects).toHaveLength(1);expect(sample.objects[0].label).toBe('검사 대상');expect(sample.objects[0].box).toEqual(expectedBoxes[i]);expect(sample.objects[0].direction_deg).toBe(cases[i].direction);}};
 verifyOBB(obbSaved);await open('회전 객체 검출');await expect(obbTable).toHaveValue(expectedOBB);verifyOBB(await api(`/api/rotated-detection/manifest?dataset_path=${encodeURIComponent(obbSaved.dataset_path)}`));
 await obbTable.scrollIntoViewIfNeeded();await evidence.screenshot(page,native?'native-three-fitting-modes-reopened':'browser-three-fitting-modes-reopened');
 for(const file of files)expect(hash(fs.readFileSync(file))).toBe(originals[file]);expect(trainingPosts).toEqual([]);
 for(const [family,saved]of [['ocr',ocrSaved],['rotated-detection',obbSaved]]as const){const manifest=path.join(saved.dataset_path,family==='ocr'?'ocr.json':'rotated_boxes.json');expect(fs.existsSync(manifest)).toBe(true);evidence.addFile(manifest);}
 evidence.note('truth_and_fitting',{project_id:project.id,source_images:Object.entries(originals).map(([path,sha256])=>({path,sha256})),human_truth:truth,ocr_prepared:ocrSaved,obb_prepared:obbSaved,fits,actual_clicks:true,exact_reopen:true,training_posts:trainingPosts,originals_unchanged:true,representative_model_quality:false});
}
test('human OCR text and three fitting modes save and reopen exact native content',async({page,request,renderer,workspace,evidence})=>{
 await installDesktopHostShim(page,renderer.port);const api:OwnedApi=async(route,body,method)=>{const response=await request.fetch(renderer.origin+route,{method:method||(body===undefined?'GET':'POST'),...(body===undefined?{}:{data:body})});expect(response.ok(),await response.text()).toBe(true);return response.json();};await exercise(page,workspace,evidence,api,false,renderer.url);
});
test('native human OCR text and three fitting modes save and reopen exact native content',{tag:'@electron'},async({electronSession,workspace,evidence})=>{
 const {window}=electronSession,backend=await electronSession.waitForBackend();const api:OwnedApi=(route,body,method)=>window.evaluate(async({port,route,body,method})=>{const response=await fetch(`http://127.0.0.1:${port}${route}`,{method:method||(body===undefined?'GET':'POST'),...(body===undefined?{}:{headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})});if(!response.ok)throw Error(`Owned truth fixture API: HTTP ${response.status}`);return response.json();},{port:backend.port,route,body,method});await exercise(window,workspace,evidence,api,true);
});
