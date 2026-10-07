import fs from 'node:fs';
import path from 'node:path';
import {createHash} from 'node:crypto';
import type {Page,Route} from '@playwright/test';
import {test,expect,type Workspace,type Evidence} from './fixtures/test';
import {installDesktopHostShim} from './fixtures/desktop-host-shim';
import {png} from './qa/appFlow';
type Api=(route:string,body?:unknown,method?:string)=>Promise<any>;
const sha=(raw:Buffer)=>createHash('sha256').update(raw).digest('hex');

// Reviewers and labels are synthetic fixtures. No human truth is approved.
async function exercise(page:Page,w:Workspace,e:Evidence,api:Api,native:boolean,url?:string){
 const source=path.join(w.root,'review-refusal-source');fs.mkdirSync(source);
 const image=path.join(source,'part.png');fs.writeFileSync(image,png(256,3,(x,y)=>[x,y,100]));const sourceHash=sha(fs.readFileSync(image));
 const project=await api('/api/project/create',{name:'Owned review refusal controls',task:'segmentation'});
 await api('/api/project/update',{source_dataset_dir:source},'PUT');await api('/api/dataset/import',{folder_path:source,task:'segmentation'});
 await api('/api/team-data/books',{expected_version:0,actor:'fixture-owner',title:'Controlled original book',categories:[{id:0,name:'OK',color:'#10b981'},{id:2,name:'Scratch',color:'#f59e0b'}]});
 await api('/api/team-data/settings',{expected_revision:1,actor:'fixture-owner',changes:{review_enabled:true,required_reviews:2,prevent_self_review:true,approved_only_training:true}},'PUT');
 const saved=await api('/api/annotations/save',{image_id:'part',image_path:image,image_width:256,image_height:256,actor:'fixture-labeler',annotations:[{id:'controlled-label',type:'bbox',label:'Scratch',category_id:2,bbox:[2,3,20,21]}]});
 const uuid=saved.metadata.image_uuid,query='/api/annotations/part?file_path='+encodeURIComponent(image),imageRoute='/api/team-data/images/'+uuid;
 const annotationDir=path.join(project.annotations_dir,'by_dataset',sha(Buffer.from(fs.realpathSync(source))).slice(0,16)),annotationFile=path.join(annotationDir,'part.json'),maskFile=path.join(annotationDir,'masks','part.png');
 expect(fs.lstatSync(annotationFile).isFile()).toBe(true);expect(fs.lstatSync(annotationFile).isSymbolicLink()).toBe(false);
 const annotationFiles=()=>({json:{path:annotationFile,sha256:sha(fs.readFileSync(annotationFile))},mask:fs.existsSync(maskFile)?{path:maskFile,sha256:sha(fs.readFileSync(maskFile))}:null});
 const annotation=()=>api(query);const read=async()=>({annotation:await annotation(),annotation_files:annotationFiles(),image:await api(imageRoute),workspace:await api('/api/team-data'),queue:await api('/api/team-data/queue?offset=0&limit=30')});
 const dialog=page.getByRole('dialog',{name:'팀 데이터 작업',exact:true});
 const open=async()=>{await page.getByRole('button',{name:'팀 작업 · 라벨 기준·검수',exact:true}).click();await expect(dialog).toBeVisible();await expect(dialog.getByRole('region',{name:'현재 이미지 팀 작업',exact:true})).toContainText('part.png');};
 const close=async()=>{await dialog.getByRole('button',{name:'팀 데이터 작업 닫기',exact:true}).click();await expect(dialog).toHaveCount(0);};
 if(url)await page.goto(url);else await page.reload();await expect(page.getByTitle('프로젝트 관리',{exact:true})).toContainText(project.name);
 await page.getByRole('navigation',{name:'Workflow Stages'}).getByRole('button').nth(1).click();await page.getByRole('button',{name:'집중 편집',exact:true}).click();await open();
 const work=dialog.getByRole('region',{name:'현재 이미지 팀 작업',exact:true}),actor=dialog.getByLabel('팀 작업자 이름',{exact:true}),reason=work.getByLabel('검수 이유',{exact:true});
 const approve=work.getByRole('button',{name:'승인 표 제출',exact:true}),reject=work.getByRole('button',{name:'반려 표 제출',exact:true}),adjudicate=work.getByRole('button',{name:'최종 승인 조정',exact:true});
 const baseline=await read();expect(baseline.annotation.annotations).toHaveLength(1);expect(baseline.annotation.annotations[0]).toMatchObject({id:'controlled-label',bbox:[2,3,20,21]});expect(baseline.annotation.metadata.team.reviews).toEqual([]);expect(baseline.annotation_files.mask).toBeNull();
 const mutations:Array<{method:string;path:string;body:any}>=[];
 const observe=(request:any)=>{const pathname=new URL(request.url()).pathname;if(request.method()!=='GET'&&(pathname.startsWith('/api/team-data')||pathname.startsWith('/api/annotations')||pathname.startsWith('/api/training')))mutations.push({method:request.method(),path:pathname,body:request.postDataJSON()});};
 const controlled:Array<{path:string;body:any;status:number}>=[],responses:Array<{action:string;status:number}>=[];
 const failure=async(route:Route)=>{if(route.request().method()!=='POST'){await route.continue();return;}const pathname=new URL(route.request().url()).pathname,body=route.request().postDataJSON();expect(pathname===imageRoute+'/review'||pathname===imageRoute+'/adjudicate').toBe(true);expect(body.expected_revision).toBeGreaterThan(0);expect(body.actor).toBe('fixture-reviewer');controlled.push({path:pathname,body,status:503});await route.fulfill({status:503,json:{detail:'Controlled review POST transport failure'}});};
 const clickReply=async(button:any,action:string,endpoint:string,status:number)=>{const promise=page.waitForResponse(r=>r.request().method()==='POST'&&new URL(r.url()).pathname===imageRoute+endpoint);await button.click();const response=await promise;expect(response.status()).toBe(status);responses.push({action,status});return response;};
 const unchanged=async(expected:any)=>{expect(await read()).toEqual(expected);expect(sha(fs.readFileSync(image))).toBe(sourceHash);};
 page.on('request',observe);
 try{
  await actor.fill('');await reason.fill('Synthetic unsent opinion');await expect(approve).toBeDisabled();await expect(reject).toBeDisabled();await unchanged(baseline);expect(mutations).toEqual([]);
  await actor.fill('fixture-reviewer');await reason.fill('');await expect(reject).toBeDisabled();await expect(approve).toBeEnabled();await unchanged(baseline);expect(mutations).toEqual([]);
  await work.scrollIntoViewIfNeeded();await e.screenshot(page,`${native?'native':'browser'}-review-empty-actor-or-rejection-reason-no-post`);
  await actor.fill('fixture-labeler');await reason.fill('Synthetic self-review refusal');
  for(const [button,action] of [[approve,'approve-vote'],[reject,'reject-vote']] as const){await clickReply(button,action,'/review',422);await expect(dialog.getByRole('alert')).toContainText('자신이 수정한 라벨은 다른 사람이 검토해야 합니다.');await unchanged(baseline);}
  await e.screenshot(page,`${native?'native':'browser'}-actual-self-review-refused-original-labels-and-no-votes`);
  await actor.fill('fixture-reviewer');await reason.fill('Synthetic valid opinion not submitted');await page.route('**/api/team-data/images/*/review',failure);
  for(const [button,action] of [[approve,'approve-vote'],[reject,'reject-vote']] as const){await clickReply(button,action,'/review',503);await expect(dialog.getByRole('alert')).toContainText('Controlled review POST transport failure');await unchanged(baseline);}
  await page.unroute('**/api/team-data/images/*/review',failure);await e.screenshot(page,`${native?'native':'browser'}-review-exact-valid-post-503-preserves-original`);
  expect(mutations).toHaveLength(4);expect(controlled.map(x=>x.body.decision)).toEqual(['approve','reject']);expect(mutations.every(x=>x.path===imageRoute+'/review')).toBe(true);
  // Two real fixture votes make an adjudication available. These deliberate
  // setup writes are separate from failed UI requests and from human approval.
  page.off('request',observe);await close();let current=await annotation();
  const first=await api(imageRoute+'/review',{expected_revision:current.metadata.revision,actor:'fixture-opinion-a',decision:'approve',reason:'Synthetic fixture opinion A'});
  current=await annotation();const second=await api(imageRoute+'/review',{expected_revision:current.metadata.revision,actor:'fixture-opinion-b',decision:'reject',reason:'Synthetic fixture opinion B'});
  expect(first.image.team.reviews).toHaveLength(1);expect(second.image.team.reviews).toHaveLength(2);expect(second.image.team.review_status).toBe('disputed');
  await page.reload();await open();const disputed=await read();expect(disputed.annotation.annotations).toEqual(baseline.annotation.annotations);expect(disputed.annotation_files).toEqual(baseline.annotation_files);expect(disputed.annotation.metadata.team.reviews).toEqual(second.image.team.reviews);expect(disputed.annotation.metadata.team.adjudication).toBeFalsy();
  page.on('request',observe);await actor.fill('');await reason.fill('Synthetic unsent adjudication');await expect(adjudicate).toBeDisabled();await unchanged(disputed);expect(mutations).toHaveLength(4);
  await actor.fill('fixture-reviewer');await reason.fill('');await expect(adjudicate).toBeDisabled();await unchanged(disputed);expect(mutations).toHaveLength(4);
  await actor.fill('fixture-labeler');await reason.fill('Synthetic self-adjudication refusal');await clickReply(adjudicate,'adjudicate','/adjudicate',422);await expect(dialog.getByRole('alert')).toContainText('자신이 수정한 라벨은 다른 사람이 판정 조정해야 합니다.');await unchanged(disputed);
  await actor.fill('fixture-reviewer');await reason.fill('Synthetic valid adjudication not submitted');await page.route('**/api/team-data/images/*/adjudicate',failure);await clickReply(adjudicate,'adjudicate','/adjudicate',503);await expect(dialog.getByRole('alert')).toContainText('Controlled review POST transport failure');await unchanged(disputed);await page.unroute('**/api/team-data/images/*/adjudicate',failure);
  await work.scrollIntoViewIfNeeded();await e.screenshot(page,`${native?'native':'browser'}-adjudication-empty-self-review-and-transport-refusals`);
  await reason.fill('Unsent adjudication draft');await close();await unchanged(disputed);expect(mutations).toHaveLength(6);
  await page.reload();await open();await expect(work).toContainText('의견 불일치');await expect(adjudicate).toBeDisabled();const reopened=await read();expect(reopened).toEqual(disputed);expect(mutations).toHaveLength(6);
  expect(controlled.map(x=>({path:x.path,decision:x.body.decision,status:x.status}))).toEqual([{path:imageRoute+'/review',decision:'approve',status:503},{path:imageRoute+'/review',decision:'reject',status:503},{path:imageRoute+'/adjudicate',decision:'approve',status:503}]);
  await e.screenshot(page,`${native?'native':'browser'}-disputed-original-reviews-reopened-without-adjudication`);
  e.note('team_review_refusal_controls',{record_id:'F024',actions:{'approve-vote':['empty','invalid','error'],'reject-vote':['empty','invalid','error'],'adjudicate':['empty','invalid','error','cancel','reopen']},project_id:project.id,image_uuid:uuid,image_path:image,source_sha256:sourceHash,baseline,disputed,reopened,explicit_fixture_vote_setup:{first,second,count:2,human_truth:false},empty:{blank_actor_disabled:true,blank_rejection_reason_disabled:true,blank_adjudication_reason_disabled:true,no_ui_POST:true},invalid:{actual_self_review_422:2,actual_self_adjudication_422:1,original_labels_and_current_votes_preserved:true},error:{controlled_valid_POST_503:3,original_POST_not_dispatched:true,original_labels_and_current_votes_preserved:true},cancel:{explicit_close_unsent_adjudication:true,no_POST:true,dispatched_mutation_not_cancelled:true},reopen:{actual_page_reload:true,exact_disputed_review_records_retained:true,no_adjudication:true},responses,controlled_failures:controlled,ui_mutations:mutations,actual_source_ui:true,source_electron:native,synthetic_review_controls:true,quality_or_human_truth_approved:false,actual_model_inference:false,installed_target_verified:false,gpu_used:false});
 }finally{page.off('request',observe);if(!page.isClosed()){await page.unroute('**/api/team-data/images/*/review',failure);await page.unroute('**/api/team-data/images/*/adjudicate',failure);}}
}
test('review and adjudication input self-review and transport refusals preserve exact records across reopen',async({page,renderer,workspace,evidence})=>{
 const api:Api=async(route,body,method)=>{const response=await page.request.fetch(renderer.origin+route,{method:method||(body?'POST':'GET'),data:body});expect(response.ok(),await response.text()).toBe(true);return response.json();};await installDesktopHostShim(page,renderer.port);await exercise(page,workspace,evidence,api,false,renderer.url);
});
test('native review and adjudication refusals preserve labels and original disputed records',{tag:'@electron'},async({electronSession,workspace,evidence})=>{
 const page=electronSession.window,status=await electronSession.waitForBackend();const api:Api=(route,body,method)=>page.evaluate(async({port,route,body,method})=>{const response=await fetch(`http://127.0.0.1:${port}${route}`,{method:method||(body?'POST':'GET'),headers:{'Content-Type':'application/json'},body:body?JSON.stringify(body):undefined});if(!response.ok)throw Error(`Owned review fixture HTTP ${response.status}`);return response.json();},{port:status.port,route,body,method});await exercise(page,workspace,evidence,api,true);
});
