import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import {execFileSync} from 'node:child_process';
import type {Request, Response} from '@playwright/test';
import {test, expect} from './fixtures/test';
import {installDesktopHostShim} from './fixtures/desktop-host-shim';
import {png} from './qa/appFlow';

test.use({actionTimeout: 10_000});
test.setTimeout(240_000);
const sha = (raw: Buffer) => crypto.createHash('sha256').update(raw).digest('hex');
const raw9 = (s: fs.BigIntStats) => [s.dev, s.ino, s.mode, s.nlink, s.size, s.mtimeNs, s.ctimeNs, s.uid, s.gid].map(String);
function remaining(deadline: number) {const left = deadline - performance.now(); if (left <= 0) throw Error('Original 10s fitting frame expired'); return Math.max(1, Math.floor(left));}
async function within<T>(work: Promise<T>, deadline: number, label: string): Promise<T> {
  let timer: NodeJS.Timeout | undefined;
  try {const value = await Promise.race([work, new Promise<never>((_, reject) => {timer = setTimeout(() => reject(Error('Original 10s fitting frame expired: ' + label)), remaining(deadline));})]); expect(performance.now()).toBeLessThanOrEqual(deadline); return value;}
  finally {clearTimeout(timer);}
}
type FilePin = {kind: 'file'; raw9: string[]; sha256: string};
type Tree = Record<string, FilePin | {kind: 'directory'; raw9: string[]}>;

// Only the three pending F029 fitting handoff cells. Existing cancel cells are
// already verified. No response interception, in-flight cancellation or model work.
const modes = [
  {mode: 'center', button: '중심 기준', points: [[80, 60], [100, 60], [80, 70]], boxPrecision: 6},
  {mode: 'face', button: '한 면 기준', points: [[64, 64], [192, 64], [64, 128]], boxPrecision: 6},
  {mode: 'irregular', button: '불규칙 외곽', points: [[64, 64], [192, 64], [192, 128], [64, 128]], boxPrecision: 4},
] as const;
for (const variant of modes) {
test(variant.mode + ' fitting proposal stays scoped through ordinary A B A project handoff and exact saved truth reopen', async ({page, request, renderer, workspace: w, evidence: e}) => {
  await installDesktopHostShim(page, renderer.port);
  const head = execFileSync('git', ['rev-parse', 'HEAD'], {encoding: 'utf8'}).trim(); expect(head).toMatch(/^[a-f0-9]{40}$/);
  const custody = {opened: 0, closed: 0, uncertain: [] as string[]};
  const proofs: any[] = [], writes: Array<{method: string; route: string; body: unknown}> = [], callbackErrors: string[] = [];
  const pageErrors: string[] = [], consoleErrors: string[] = [], non2xx: unknown[] = [];
  const pageError = (error: Error) => pageErrors.push(error.message);
  const consoleError = (message: {type(): string; text(): string}) => {if (message.type() === 'error') consoleErrors.push(message.text());};
  const failedResponse = (response: Response) => {try {const u = new URL(response.url()); if (u.origin === renderer.origin && u.pathname.startsWith('/api/') && response.status() >= 400) non2xx.push({method: response.request().method(), route: u.pathname, status: response.status()});} catch (error) {callbackErrors.push(String(error));}};
  page.on('pageerror', pageError); page.on('console', consoleError); page.on('response', failedResponse);
  const file = (name: string, retainBytes?: (bytes: Buffer) => void): FilePin => {
    const before = fs.lstatSync(name, {bigint: true}); expect(before.isSymbolicLink()).toBe(false); expect(before.isFile()).toBe(true); expect(before.nlink).toBe(1n);
    const descriptor = fs.openSync(name, fs.constants.O_RDONLY | fs.constants.O_NOFOLLOW); custody.opened++;
    let primary: unknown, failed = false, result: FilePin | undefined;
    try {expect(raw9(fs.fstatSync(descriptor, {bigint: true}))).toEqual(raw9(before)); const bytes = fs.readFileSync(descriptor); expect(BigInt(bytes.length)).toBe(before.size);
      expect(raw9(fs.fstatSync(descriptor, {bigint: true}))).toEqual(raw9(before)); expect(raw9(fs.lstatSync(name, {bigint: true}))).toEqual(raw9(before)); result = {kind: 'file', raw9: raw9(before), sha256: sha(bytes)};retainBytes?.(bytes);
    } catch (error) {failed = true; primary = error;}
    finally {try {fs.closeSync(descriptor); custody.closed++;} catch (error) {custody.uncertain.push(String(error)); if (!failed) {failed = true; primary = error;}}}
    if (failed) throw primary; return result!;
  };
  const tree = (root: string): Tree => {
    expect(fs.realpathSync(root)).toBe(root); const rows: Tree = {};
    const walk = (directory: string) => {const before = fs.lstatSync(directory, {bigint: true}); expect(before.isDirectory()).toBe(true); expect(before.isSymbolicLink()).toBe(false);
      const names = fs.readdirSync(directory).sort(); rows[path.relative(root, directory) || '.'] = {kind: 'directory', raw9: raw9(before)};
      for (const name of names) {const member = path.join(directory, name), info = fs.lstatSync(member); expect(info.isSymbolicLink()).toBe(false); if (info.isDirectory()) walk(member); else rows[path.relative(root, member)] = file(member);}
      expect(fs.readdirSync(directory).sort()).toEqual(names); expect(raw9(fs.lstatSync(directory, {bigint: true}))).toEqual(raw9(before));
    }; walk(root); return rows;
  };
  const api = async (route: string, data?: unknown, method = data === undefined ? 'GET' : 'POST') => {
    const deadline = performance.now() + 10_000;
    const response = await within(request.fetch(renderer.origin + route, {method, ...(data === undefined ? {} : {data}), timeout: remaining(deadline)}), deadline, 'fixture API');
    const raw = await within(response.body(), deadline, 'fixture full body'); expect(response.status()).toBe(200); expect(raw.length).toBeLessThanOrEqual(1024 * 1024);
    const body = JSON.parse(raw.toString('utf8')); proofs.push({kind: 'fixture API', method, route, request: data ?? null, status: 200, response_sha256: sha(raw), response: body}); return body;
  };
  const wire = (route: string, method: string, match: (r: Request, u: URL) => boolean) => {
    let original: Request | undefined, accept!: (r: Response) => void, refuse!: (error: unknown) => void;
    const reply = new Promise<Response>((resolve, reject) => {accept = resolve; refuse = reject;}); void reply.catch(() => undefined);
    const poison = (error: unknown) => {callbackErrors.push(String(error)); refuse(error);};
    const timer = setTimeout(() => poison(Error('Original 10s UI response missing: ' + route)), 10_000);
    const started = (r: Request) => {try {const u = new URL(r.url()); if (u.origin === renderer.origin && u.pathname === route && r.method() === method && match(r, u)) {expect(r.frame()).toBe(page.mainFrame()); expect(original).toBeUndefined(); original = r;}} catch (error) {poison(error);}};
    const responded = (r: Response) => {try {if (r.request() === original) accept(r);} catch (error) {poison(error);}};
    page.on('request', started); page.on('response', responded);
    return {reply, original: () => original, close: () => {clearTimeout(timer); page.off('request', started); page.off('response', responded);}};
  };

  type Scope = {tag: 'A'|'B'; project: any; source: string; sourceHashes: Record<string,string>; saved: any; table: string; context?: any};
  const scopes: Scope[] = [];
  const transitions: any[] = [], fits: any[] = [], previews: any[] = [];
  const activationChanges: any[] = [], screenshotGuards: any[] = [];
  const activationMetadata = () => Object.fromEntries(['.active_project.json','.recent_projects.json'].map(name=>{
    let bytes:Buffer|undefined;const pin=file(path.join(w.projects,name),raw=>{bytes=Buffer.from(raw);});expect(bytes).toBeDefined();
    return [name,{pin,body:JSON.parse(bytes!.toString('utf8')),bytes_hex:bytes!.toString('hex')}];
  }));
  const panel = () => page.locator('details').filter({has: page.locator('summary').filter({hasText: '회전 객체 위치 모델'})}).first();
  const fitting = () => panel().locator('section').filter({has: page.getByRole('heading', {name: '원본 이미지에서 회전 박스 그리기', exact: true})});
  const picker = () => fitting().getByRole('combobox', {name: '이미지', exact: true});
  const fitButton = () => fitting().getByRole('button', {name: '회전 박스 맞추기', exact: true});
  const append = () => fitting().getByRole('button', {name: '정답 표에 추가', exact: true});
  const table = () => panel().getByRole('textbox', {name: /^정답 표 · 이미지 상대 경로/});
  const summary = () => fitting().locator('p.text-cyan-300');
  const responseBody = async (response: Response, deadline: number, label: string, original: Request|undefined, scope: Scope, selection=false) => {
    const own=response.request();expect(original).toBeDefined();expect(own).toBe(original);expect(own.frame()).toBe(page.mainFrame());expect(new URL(response.url()).origin).toBe(renderer.origin);expect(response.status()).toBe(200);
    const projectHeader=await within(own.headerValue('x-vision-project'),deadline,'own project header');
    const contextHeader=await within(own.headerValue('x-vision-context'),deadline,'own context header');
    // api.request() deliberately omits bound context for project selection.
    if(selection){expect(projectHeader).toBeNull();expect(contextHeader).toBeNull();}
    else{expect(projectHeader).toBe(scope.project.id);expect(contextHeader).not.toBeNull();expect(JSON.parse(contextHeader!)).toEqual(scope.context);}
    const encoded=await within(response.headerValue('x-vision-context'),deadline,'resulting owning response context');expect(encoded).not.toBeNull();
    const context=JSON.parse(encoded!);expect(Object.keys(context).sort()).toEqual(['actor_id','mode','project_id','workspace_id']);expect(context.project_id).toBe(scope.project.id);expect(context.mode).toBe('local');
    if(scope.context)expect(context).toEqual(scope.context);else scope.context=context;
    const bytes=await within(response.body(),deadline,'same original full body');expect(bytes.length).toBeLessThanOrEqual(1024*1024);expect(await within(response.finished(),deadline,'same original HTTP completion')).toBeNull();
    const result=JSON.parse(bytes.toString('utf8')),fileName=path.join(w.logs,label+'-original-response.json');fs.writeFileSync(fileName,bytes,{flag:'wx'});e.addFile(fileName);
    proofs.push({kind:'original UI response',label,owner:scope.tag,method:own.method(),url:response.url(),request_body:own.postDataJSON(),request_project:projectHeader,request_context:contextHeader===null?null:JSON.parse(contextHeader),response_context:context,status:200,response:result,response_sha256:sha(bytes),bytes:bytes.length,raw_file:fileName,same_Request:true,main_frame:true,finished:true,deadline,completed:performance.now()});
    return result;
  };
  const readSaved=async(scope:Scope,label:string)=>{
    const capture=wire('/api/rotated-detection/manifest','GET',(_r,u)=>u.searchParams.get('dataset_path')===scope.saved.dataset_path),deadline=performance.now()+10_000;
    try{
      await within(panel().getByRole('button',{name:'저장된 정답 읽기',exact:true}).click({timeout:remaining(deadline)}),deadline,'ordinary saved truth read');
      expect(await responseBody(await within(capture.reply,deadline,'own manifest GET'),deadline,label,capture.original(),scope)).toEqual(scope.saved);
    }finally{capture.close();}
    const visual=performance.now()+10_000;
    await within(expect(table()).toHaveValue(scope.table,{timeout:remaining(visual)}),visual,'exact three saved rows');
    await within(expect(panel().getByRole('combobox',{name:'프로젝트에 저장된 회전 박스 정답',exact:true})).toHaveValue(scope.saved.dataset_path,{timeout:remaining(visual)}),visual,'own saved manifest selector');
  };
  const enter=async(scope:Scope,label:string)=>{
    const deadline=performance.now()+10_000;
    await within(expect(page.getByTitle('프로젝트 관리',{exact:true})).toContainText(scope.project.name,{timeout:remaining(deadline)}),deadline,'own visible project');
    await within(page.getByRole('navigation',{name:'Workflow Stages'}).getByRole('button').nth(2).click({timeout:remaining(deadline)}),deadline,'ordinary training stage');
    await within(page.getByRole('region',{name:'모델 학습 허브'}).getByRole('button',{name:/^회전 객체 검출/}).click({timeout:remaining(deadline)}),deadline,'ordinary rotated workbench');
    // Let the original automatic owner manifest finish rendering before the
    // explicit read capture, so a second request cannot be mistaken for it.
    await within(expect(table()).toHaveValue(scope.table,{timeout:remaining(deadline)}),deadline,'automatic saved truth hydrated');
    await within(expect(picker().locator('option')).toHaveCount(4,{timeout:remaining(deadline)}),deadline,'own three source images');
    expect((await picker().locator('option').evaluateAll(rows=>rows.map(row=>(row as HTMLOptionElement).value))).sort()).toEqual(['',...['train.png','val.png','test.png'].map(name=>path.join(scope.source,name))].sort());
    await readSaved(scope,label);
  };
  const openProject=async(scope:Scope,label:string)=>{
    const previousMetadata=activationMetadata(),previousNames=fs.readdirSync(w.projects).sort();
    const capture=wire('/api/project/open','POST',r=>r.postDataJSON()?.project_dir===scope.project.project_dir),deadline=performance.now()+10_000;
    let result:any;
    try{
      await within(page.getByTitle('프로젝트 관리',{exact:true}).click({timeout:remaining(deadline)}),deadline,'ordinary project manager');
      const dialog=page.getByRole('dialog',{name:'프로젝트 관리',exact:true});
      await within(dialog.getByRole('button',{name:'최근 프로젝트',exact:true}).click({timeout:remaining(deadline)}),deadline,'ordinary recent projects');
      const item=dialog.getByRole('button').filter({has:page.locator('span[title]').filter({hasText:scope.project.project_dir})});
      await within(expect(item).toHaveCount(1,{timeout:remaining(deadline)}),deadline,'exact owned project item');
      await within(item.click({timeout:remaining(deadline)}),deadline,'ordinary project selection');
      const response=await within(capture.reply,deadline,'original project open');
      expect(capture.original()!.postDataJSON()).toEqual({project_dir:scope.project.project_dir});
      result=await responseBody(response,deadline,label,capture.original(),scope,true);expect(result).toEqual(scope.project);
    }finally{capture.close();}
    const visual=performance.now()+10_000;await within(expect(page.getByRole('dialog',{name:'프로젝트 관리',exact:true})).toHaveCount(0,{timeout:remaining(visual)}),visual,'project dialog closed');
    await enter(scope,label+'-saved-truth');
    const nextMetadata=activationMetadata(),row={id:scope.project.id,name:scope.project.name,task:scope.project.task,project_dir:scope.project.project_dir,updated_at:scope.project.updated_at};
    const expected={'.active_project.json':{project_dir:scope.project.project_dir},'.recent_projects.json':[row,...previousMetadata['.recent_projects.json'].body.filter((entry:any)=>entry.project_dir!==scope.project.project_dir)].slice(0,20)};
    for(const name of ['.active_project.json','.recent_projects.json']as const){
      expect(nextMetadata[name].body).toEqual(expected[name]);expect(nextMetadata[name].bytes_hex).toBe(Buffer.from(JSON.stringify(expected[name],null,2),'utf8').toString('hex'));
      for(const index of [0,2,3,7,8])expect(nextMetadata[name].pin.raw9[index]).toBe(previousMetadata[name].pin.raw9[index]);
      expect(Number(nextMetadata[name].pin.raw9[4])).toBe(Buffer.from(JSON.stringify(expected[name],null,2),'utf8').length);
    }
    expect(fs.readdirSync(w.projects).sort()).toEqual(previousNames);
    activationChanges.push({label,owner:scope.tag,after_protected_baseline:baseline!==undefined,before:previousMetadata,after:nextMetadata,expected_body:expected,exact_serialized_bytes:true,allowed_raw9_changes:['inode','size matching exact serialized body','mtime_ns','ctime_ns'],project_root_names:previousNames,namespace_unchanged:true});
    return {owner:scope.tag,project_id:result.id,project_dir:result.project_dir};
  };
  const selectImage=async(scope:Scope,label:string)=>{
    const image=path.join(scope.source,'train.png'),capture=wire('/api/rotated-detection/fit-source','GET',(_r,u)=>u.searchParams.get('image_path')===image),deadline=performance.now()+10_000;
    let preview:any;
    try{
      await within(picker().selectOption(image,{timeout:remaining(deadline)}),deadline,'ordinary own source selection');
      preview=await responseBody(await within(capture.reply,deadline,'original fit-source GET'),deadline,label,capture.original(),scope);
      expect(preview.source_size).toEqual([256,256]);expect(preview.source_sha256).toBe(scope.sourceHashes['train.png']);
    }finally{capture.close();}
    const visual=performance.now()+10_000;
    await within(expect(fitting().getByRole('img',{name:'회전 박스 정답 그리기',exact:true}).locator('image')).toHaveAttribute('href',preview.preview_data_url,{timeout:remaining(visual)}),visual,'own original preview rendered');
    previews.push({owner:scope.tag,image,preview});return {image,preview};
  };
  const assertNoProposal=async(scope:Scope,hasPreview:boolean)=>{
    const deadline=performance.now()+10_000;await within(expect(picker()).toHaveValue(hasPreview?path.join(scope.source,'train.png'):'',{timeout:remaining(deadline)}),deadline,'owner fitting source reset');
    const svg=fitting().getByRole('img',{name:'회전 박스 정답 그리기',exact:true});
    await within(expect(svg).toHaveCount(hasPreview?1:0,{timeout:remaining(deadline)}),deadline,'previous preview retired');
    if(hasPreview){await within(expect(svg.locator('circle')).toHaveCount(0,{timeout:remaining(deadline)}),deadline,'new owner has zero points');await within(expect(svg.locator('polyline')).toHaveAttribute('points','',{timeout:remaining(deadline)}),deadline,'new owner path empty');await within(expect(svg.locator('polygon')).toHaveCount(0,{timeout:remaining(deadline)}),deadline,'new owner has no old proposal');}
    await within(expect(summary()).toHaveCount(0,{timeout:remaining(deadline)}),deadline,'old box summary retired');
    await within(expect(fitButton()).toBeDisabled({timeout:remaining(deadline)}),deadline,'fit requires new own points');
    await within(expect(append()).toBeDisabled({timeout:remaining(deadline)}),deadline,'old proposal cannot append');
    await within(expect(fitting().getByRole('alert')).toHaveCount(0,{timeout:remaining(deadline)}),deadline,'no stale fitting error');
    await within(expect(table()).toHaveValue(scope.table,{timeout:remaining(deadline)}),deadline,'same owner saved truth exact');
  };
  const fittingScreenshot=async(name:string,anchor:'proposal'|'reset')=>{
    // The original HTTP capture is already closed; this independent visual
    // frame does not renew its ten-second body deadline.
    const deadline=performance.now()+10_000,svg=fitting().getByRole('img',{name:'회전 박스 정답 그리기',exact:true}),target=anchor==='proposal'?summary():append();
    await within(target.scrollIntoViewIfNeeded({timeout:remaining(deadline)}),deadline,'screenshot owning anchor viewport');
    await within(svg.scrollIntoViewIfNeeded({timeout:remaining(deadline)}),deadline,'screenshot original canvas viewport');
    await within(expect(svg).toBeVisible({timeout:remaining(deadline)}),deadline,'screenshot actual canvas visible');
    await within(expect(svg).toBeInViewport({ratio:1,timeout:remaining(deadline)}),deadline,'screenshot whole original canvas in viewport');
    await within(expect(target).toBeVisible({timeout:remaining(deadline)}),deadline,'screenshot actual owning anchor visible');
    await within(expect(target).toBeInViewport({ratio:1,timeout:remaining(deadline)}),deadline,'screenshot whole owning anchor in viewport');
    const canvas=await within(svg.boundingBox(),deadline,'screenshot canvas geometry'),owningAnchor=await within(target.boundingBox(),deadline,'screenshot owning anchor geometry');
    expect(canvas).not.toBeNull();expect(owningAnchor).not.toBeNull();screenshotGuards.push({name,anchor,canvas,owning_anchor:owningAnchor,viewport:page.viewportSize(),whole_canvas_and_anchor_in_viewport:true});
    await e.screenshot(page,name);
  };
  const protectedTrees=()=>Object.fromEntries(scopes.map(scope=>[scope.tag,{source:tree(scope.source),project:tree(scope.project.project_dir),prepared:tree(scope.saved.dataset_path)}]));
  let baseline:ReturnType<typeof protectedTrees>|undefined,primary:unknown,failed=false,proof:any,observing=false;
  const secondary:string[]=[];
  const observe=(own:Request)=>{try{const u=new URL(own.url());if(u.origin===renderer.origin&&u.pathname.startsWith('/api/')&&!['GET','HEAD','OPTIONS'].includes(own.method()))writes.push({method:own.method(),route:u.pathname,body:own.postDataJSON()});}catch(error){callbackErrors.push(String(error));}};
  try{
    for(const tag of ['A','B']as const){
      const source=path.join(fs.realpathSync(w.root),'owned-'+variant.mode+'-handoff-'+tag),relative=['train.png','val.png','test.png'];fs.mkdirSync(source);
      for(const[i,name]of relative.entries()){const target=path.join(source,name);fs.writeFileSync(target,png(256,3,(x,y)=>[x,y,(tag==='A'?31:47)+i*67]),{flag:'wx'});e.addFile(target);}
      const sourceHashes=Object.fromEntries(relative.map(name=>[name,file(path.join(source,name)).sha256]));
      const created=await api('/api/project/create',{name:'Owned '+variant.mode+' fitting handoff '+tag,task:'classification'});
      await api('/api/project/update',{source_dataset_dir:source},'PUT');const project=await api('/api/project/current');expect(project.id).toBe(created.id);expect(project.source_dataset_dir).toBe(source);expect(path.dirname(project.project_dir)).toBe(w.projects);
      expect((await api('/api/dataset/import',{folder_path:source,task:'classification',validate_images:true})).total_images).toBe(3);
      for(const name of relative)await api('/api/dataset/metadata/image?image_path='+encodeURIComponent(path.join(source,name)));
      await api('/api/team-data/readiness');
      const samples=relative.map((image,i)=>({image,split:['train','val','test'][i],label:'owned-'+tag+'-saved-box',box:{cx:80,cy:60,width:40,height:20,angle_deg:0},direction_deg:(tag==='A'?15:115)+i}));
      const saved=await api('/api/rotated-detection/manifest',{dataset_path:source,samples});expect(saved.sample_count).toBe(3);expect(saved.split_counts).toEqual({train:1,val:1,test:1});
      expect(path.dirname(saved.dataset_path)).toBe(path.join(project.dataset_dir,'rotated_detection'));expect(path.basename(saved.dataset_path)).toMatch(/^[a-f0-9]{32}$/);
      for(const sample of saved.samples){const supplied=samples.find(row=>row.image===sample.image)!;expect(sample.source_sha256).toBe(sourceHashes[sample.image]);expect(sample.split).toBe(supplied.split);expect(sample.objects??[{label:sample.label,box:sample.box,direction_deg:sample.direction_deg}]).toEqual([{label:supplied.label,box:supplied.box,direction_deg:supplied.direction_deg}]);}
      expect(await api('/api/rotated-detection/manifest?dataset_path='+encodeURIComponent(saved.dataset_path))).toEqual(saved);
      const expectedTable=samples.map(row=>[row.image,row.label,[80,60,40,20,0].join(','),row.split,row.direction_deg].join('\t')).join('\n');
      scopes.push({tag,project,source,sourceHashes,saved,table:expectedTable});
    }
    const[A,B]=scopes;expect(A.project.id).not.toBe(B.project.id);expect(A.table).not.toBe(B.table);expect(new Set([...Object.values(A.sourceHashes),...Object.values(B.sourceHashes)]).size).toBe(6);
    await page.goto(renderer.url);await openProject(A,'warm-A');await openProject(B,'warm-B');await openProject(A,'initial-A');
    await assertNoProposal(A,false);
    baseline=protectedTrees();
    page.on('request',observe);observing=true;
    await page.evaluate(()=>{const state={clicks:[]as any[],pointers:[]as any[]};(window as any).__f029FittingHandoff=state;document.addEventListener('click',event=>{const element=event.target instanceof Element?event.target:null,button=element?.closest('button');if(button&&button.textContent?.trim()==='회전 박스 맞추기')state.clicks.push({text:'회전 박스 맞추기',trusted:event.isTrusted});if(element?.closest('svg[aria-label="회전 박스 정답 그리기"]'))state.pointers.push({client:[event.clientX,event.clientY],trusted:event.isTrusted});},true);});
    const{image}=await selectImage(A,'A-own-fit-preview');
    await fitting().getByRole('button',{name:variant.button,exact:true}).click();
    const svg=fitting().getByRole('img',{name:'회전 박스 정답 그리기',exact:true}),delivered:number[][]=[],clients:number[][]=[];
    const capture=wire('/api/rotated-detection/fit-box','POST',r=>r.postDataJSON()?.image_path===image&&r.postDataJSON()?.mode===variant.mode);
    let fitted:any,actualRequest:any,expected:any;
    try{
      const deadline=performance.now()+10_000;
      await within(svg.scrollIntoViewIfNeeded({timeout:remaining(deadline)}),deadline,'original fitting viewport');
      const geometry=await within(svg.boundingBox(),deadline,'actual SVG geometry');expect(geometry).not.toBeNull();
      const scale=Math.min(geometry!.width/256,geometry!.height/256),left=geometry!.x+(geometry!.width-256*scale)/2,top=geometry!.y+(geometry!.height-256*scale)/2;
      for(const[index,[x,y]]of variant.points.entries()){expect(await within(svg.boundingBox(),deadline,'same original SVG')).toEqual(geometry);const cx=Math.floor(left+x*scale),cy=Math.floor(top+y*scale);clients.push([cx,cy]);delivered.push([(cx-left)/scale,(cy-top)/scale]);await within(page.mouse.click(cx,cy),deadline,'trusted original source point');await within(expect(svg.locator('circle')).toHaveCount(index+1,{timeout:remaining(deadline)}),deadline,'point delivery acknowledged');}
      await within(expect(fitButton()).toBeEnabled({timeout:remaining(deadline)}),deadline,'valid original geometry available');await within(fitButton().click({timeout:remaining(deadline)}),deadline,'ordinary fitting click');
      fitted=await responseBody(await within(capture.reply,deadline,'same original geometric fit POST'),deadline,'A-positive-'+variant.mode+'-fit',capture.original(),A);
      actualRequest=capture.original()!.postDataJSON();expect(actualRequest).toEqual({image_path:image,mode:variant.mode,points:actualRequest.points,source_sha256:A.sourceHashes['train.png']});expect(actualRequest.points).toHaveLength(variant.points.length);
      for(let i=0;i<variant.points.length;i++)for(let axis=0;axis<2;axis++)expect(actualRequest.points[i][axis]).toBeCloseTo(delivered[i][axis],8);
      const[a,b,c]=delivered;
      if(variant.mode==='center'){const dx=b[0]-a[0],dy=b[1]-a[1],length=Math.hypot(dx,dy);expected={cx:a[0],cy:a[1],width:2*length,height:2*Math.abs((c[0]-a[0])*(-dy/length)+(c[1]-a[1])*(dx/length)),angle_deg:(Math.atan2(dy,dx)*180/Math.PI+90)%180-90};}
      else if(variant.mode==='face'){expect(b[1]).toBe(a[1]);expect(c[0]).toBe(a[0]);expected={cx:(a[0]+b[0])/2,cy:(a[1]+c[1])/2,width:b[0]-a[0],height:c[1]-a[1],angle_deg:0};}
      else{const d=delivered[3];expect(b[1]).toBe(a[1]);expect(c[0]).toBe(b[0]);expect(d).toEqual([a[0],c[1]]);const xs=delivered.map(p=>p[0]),ys=delivered.map(p=>p[1]);expected={cx:(Math.min(...xs)+Math.max(...xs))/2,cy:(Math.min(...ys)+Math.max(...ys))/2,width:Math.max(...xs)-Math.min(...xs),height:Math.max(...ys)-Math.min(...ys),angle_deg:0};expect(expected.width).toBeGreaterThan(expected.height);}
      expect(fitted.mode).toBe(variant.mode);expect(fitted.source_size).toEqual([256,256]);expect(fitted.source_sha256).toBe(A.sourceHashes['train.png']);
      for(const[key,value]of Object.entries(expected))expect(fitted.box[key]).toBeCloseTo(value as number,variant.boxPrecision);
      const radians=expected.angle_deg*Math.PI/180,co=Math.cos(radians),si=Math.sin(radians);
      const corners=[[-1,-1],[1,-1],[1,1],[-1,1]].map(([sx,sy])=>[expected.cx+sx*expected.width/2*co-sy*expected.height/2*si,expected.cy+sx*expected.width/2*si+sy*expected.height/2*co]);
      const actual=[...fitted.polygon].sort((p:number[],q:number[])=>p[0]-q[0]||p[1]-q[1]),sorted=corners.sort((p,q)=>p[0]-q[0]||p[1]-q[1]);expect(actual).toHaveLength(4);
      for(let i=0;i<4;i++)for(let axis=0;axis<2;axis++)expect(actual[i][axis]).toBeCloseTo(sorted[i][axis],3);
    }finally{capture.close();}
    const visual=performance.now()+10_000;
    await within(expect(svg.locator('polygon')).toHaveCount(1,{timeout:remaining(visual)}),visual,'original proposal rendered');
    await within(expect(append()).toBeEnabled({timeout:remaining(visual)}),visual,'unapplied proposal append available');
    await within(expect(summary()).toHaveText('중심 ('+fitted.box.cx.toFixed(1)+', '+fitted.box.cy.toFixed(1)+') · '+fitted.box.width.toFixed(1)+' × '+fitted.box.height.toFixed(1)+' px · '+fitted.box.angle_deg.toFixed(1)+'°',{timeout:remaining(visual)}),visual,'original proposal summary');
    const renderedPoints=await within(svg.locator('circle').evaluateAll(rows=>rows.map(row=>[Number(row.getAttribute('cx')),Number(row.getAttribute('cy'))])),visual,'original rendered source points');expect(renderedPoints).toEqual(actualRequest.points);
    const polygonText=await within(svg.locator('polygon').getAttribute('points',{timeout:remaining(visual)}),visual,'actual rendered returned polygon');expect(polygonText).not.toBeNull();
    const renderedCorners=polygonText!.trim().split(/\s+/).map(pair=>pair.split(',').map(Number));expect(renderedCorners).toEqual(fitted.polygon);
    const trusted=await page.evaluate(()=>(window as any).__f029FittingHandoff);expect(trusted.clicks).toEqual([{text:'회전 박스 맞추기',trusted:true}]);expect(trusted.pointers).toEqual(clients.map(client=>({client,trusted:true})));
    fits.push({owner:'A',mode:variant.mode,delivered,clients,rendered_points:renderedPoints,rendered_corners:renderedCorners,actual_fit_request:actualRequest,independently_expected_box:expected,fitted,trusted});
    expect(await table().inputValue()).toBe(A.table);expect(protectedTrees()).toEqual(baseline);
    await fittingScreenshot('f029-'+variant.mode+'-A-unsent-proposal-before-handoff','proposal');
    transitions.push(await openProject(B,'handoff-to-B'));await assertNoProposal(B,false);expect(protectedTrees()).toEqual(baseline);
    await selectImage(B,'B-own-source-no-A-proposal');await assertNoProposal(B,true);expect(protectedTrees()).toEqual(baseline);
    await fittingScreenshot('f029-'+variant.mode+'-B-own-truth-preview-points-reset','reset');
    transitions.push(await openProject(A,'handoff-back-A'));await assertNoProposal(A,false);expect(protectedTrees()).toEqual(baseline);
    await selectImage(A,'A-reselected-source-still-no-old-proposal');await assertNoProposal(A,true);expect(protectedTrees()).toEqual(baseline);
    await fittingScreenshot('f029-'+variant.mode+'-A-return-original-truth-no-unsent-fit','reset');
    expect(writes).toEqual([{method:'POST',route:'/api/rotated-detection/fit-box',body:actualRequest},...transitions.map(row=>({method:'POST',route:'/api/project/open',body:{project_dir:row.project_dir}}))]);
    expect(pageErrors).toEqual([]);expect(consoleErrors).toEqual([]);expect(non2xx).toEqual([]);expect(callbackErrors).toEqual([]);
    proof={schema:'modu-vision.F029-fitting-project-handoff/v1',cells:['F029.obb-native-'+variant.mode+'-fit-save-reopen.handoff'],source_head:head,mode:variant.mode,owners:scopes,transitions,fits,previews,original_UI_responses:proofs,
      after_baseline_nonGET_requests:writes,protected_before:baseline,protected_after:protectedTrees(),activation_metadata_changes:activationChanges,screenshot_viewport_guards:screenshotGuards,
      actual_ordinary_UI_A_B_A:true,saved_truth_each_owner_three_rows_exact:true,source_sha_and_original_manifest_fullbody_exact:true,old_unapplied_proposal_points_preview_and_summary_reset:true,
      errors:{page:pageErrors,console:consoleErrors,non2xx,callbacks:callbackErrors},persistent_label_manifest_model_mutations_after_setup:0,
      fitting_scope:'Original PIL preview and geometric fit only',training_started:false,actual_model_inference:false,response_interception:false,
      cancel_cell_credit:false,HTTP_abort_claim:false,in_flight_response_isolation_claim:false,native_or_installed_GUI_claim:false,quality_approved:false,parent_completion_credit:false,registry_credit:false,natural_backend_closure_required:true};
  }catch(error){failed=true;primary=error;}
  finally{
    const cleanups:Array<[string,()=>void]>=[['request observer',()=>{if(observing)page.off('request',observe);}],['page-error observer',()=>page.off('pageerror',pageError)],['console observer',()=>page.off('console',consoleError)],['response observer',()=>page.off('response',failedResponse)],['protected trees',()=>{if(baseline)expect(protectedTrees()).toEqual(baseline);}],['activation metadata',()=>{if(baseline){const last=activationChanges[activationChanges.length-1];expect(activationMetadata()).toEqual(last.after);expect(fs.readdirSync(w.projects).sort()).toEqual(last.project_root_names);}}]];
    for(const[label,cleanup]of cleanups){try{cleanup();}catch(error){secondary.push(label+': '+String(error));if(!failed){failed=true;primary=error;}}}
    e.note('f029_fitting_handoff_progress',{proof,original_UI_responses:proofs,writes,activation_metadata_changes:activationChanges,screenshot_viewport_guards:screenshotGuards,errors:{page:pageErrors,console:consoleErrors,non2xx,callbacks:callbackErrors}});
    e.note('f029_fitting_handoff_closure',{FD_custody:custody,protected_baseline_established:baseline!==undefined,source_project_prepared_preserved:baseline!==undefined&&!secondary.some(row=>row.startsWith('protected trees:')),secondary,first_exception:failed?String(primary):null,natural_backend_stop_is_existing_fixture_responsibility:true});
  }
  if(failed)throw primary;expect(custody.opened).toBe(custody.closed);expect(custody.uncertain).toEqual([]);e.note('f029_fitting_project_handoff',proof);
});
}

