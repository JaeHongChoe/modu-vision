import fs from 'node:fs';
import path from 'node:path';
import {createHash} from 'node:crypto';
import {execFileSync} from 'node:child_process';
import {test,expect} from './fixtures/test';
const harness=require('./fixtures/harness.cjs');

function hashes(root:string):Record<string,string>{
 const result:Record<string,string>={};
 function visit(folder:string){for(const item of fs.readdirSync(folder,{withFileTypes:true})){
  const file=path.join(folder,item.name);if(item.isDirectory())visit(file);
  else if(item.isFile())result[path.relative(root,file).split(path.sep).join('/')]=createHash('sha256').update(fs.readFileSync(file)).digest('hex');
  else throw Error('Unexpected linked fixture content');
 }}visit(root);return result;
}

test('native actual converted flow review, library reopen and managed IR execution',{tag:'@electron'},async({electronSession,workspace,evidence})=>{
 const status=await electronSession.waitForBackend(),page=electronSession.window;
 const api=(route:string,body?:unknown,method?:string)=>page.evaluate(async({port,route,body,method})=>{
  const response=await fetch(`http://127.0.0.1:${port}${route}`,{method:method||(body?'POST':'GET'),headers:{'Content-Type':'application/json'},body:body?JSON.stringify(body):undefined});
  const value=await response.json();if(!response.ok)throw Error(`${route}: ${response.status} ${JSON.stringify(value)}`);return value;
 },{port:status.port,route,body,method});
 const capabilities=await api('/api/export/runtime-capabilities');
 if(process.env.MV_E2E_OPENVINO_PYTHON)expect(capabilities.openvino.available,JSON.stringify(capabilities.openvino)).toBe(true);
 test.skip(!capabilities.openvino.available,'Actual OpenVINO interpreter dependency is not available on this host');
 expect(capabilities.openvino.devices).toContain('CPU');
 const fixture=JSON.parse(execFileSync(harness.resolvePython(),[path.join(harness.REPO_ROOT,'scripts/e2e/fixtures/converted_flow_control.py'),workspace.root],{
  cwd:harness.REPO_ROOT,env:{...process.env,VISION_AI_STUDIO_USER_DATA_DIR:workspace.userData},encoding:'utf8',timeout:120_000}).trim());
 evidence.note('synthetic_original_flow',fixture);
 await api('/api/project/open',{project_dir:fixture.project.project_dir});
 try{
  await page.reload();await page.getByRole('button',{name:'패키지·장치·진단',exact:true}).click();
  const library=page.getByRole('region',{name:'저장된 검사 패키지 보관함'});
  await library.getByRole('article').filter({hasText:'original_cpu_control'}).getByRole('button',{name:'최적화로 이동',exact:true}).click();
  const optimization=page.getByRole('region',{name:'Runtime 최적화와 양자화'});
  const choices=optimization.getByRole('group',{name:'변환 오차 검증 · test / val'});
  await expect(choices.getByRole('checkbox')).toHaveCount(16);
  for(const checkbox of await choices.getByRole('checkbox').all())await checkbox.uncheck();
  await choices.getByRole('checkbox',{name:'ok_00.png',exact:true}).check();
  await choices.getByRole('checkbox',{name:'ng_00.png',exact:true}).check();
  await optimization.getByRole('button',{name:'독립 후보 패키지 생성',exact:true}).click();
  await expect(optimization.getByRole('status').first()).toContainText('completed',{timeout:160_000});
  await expect(optimization.getByText('2 / 2 동일 판정·공간 결과',{exact:false})).toBeVisible();
  await expect(optimization.getByText('원본 모델 · OK',{exact:false})).toBeVisible();
  await expect(optimization.getByText('변환 모델 · OK',{exact:false})).toBeVisible();
  await optimization.getByRole('button',{name:'다음',exact:true}).click();
  await expect(optimization.getByText('2 / 2 · passed',{exact:false})).toBeVisible();
  const precision=optimization.getByRole('group',{name:'정밀도·Runtime 별도 승인'});
  await precision.getByLabel('검토자',{exact:true}).fill('Synthetic fixture authority');
  await precision.getByLabel('검토 근거',{exact:true}).fill('Actual two-image CPU and IR process control; no manufacturing quality acceptance');
  await precision.getByRole('checkbox').check();
  await precision.getByRole('button',{name:'검토 후 새 승인 패키지 생성',exact:true}).click();
  const review=optimization.getByRole('group',{name:'변환 후 전체 흐름 검토'});
  await expect(review.getByText('정상 1 · 불량 1',{exact:false})).toBeVisible({timeout:40_000});
  await review.getByLabel('변환 전체 흐름 검토자',{exact:true}).fill('Synthetic fixture authority');
  await review.getByLabel('변환 전체 흐름 검토 이유',{exact:true}).fill('Actual same frozen OK/NG cohort under permissive synthetic policy; not human quality acceptance');
  await review.getByLabel('변환 전체 흐름 직접 검토',{exact:true}).check();
  await review.getByRole('button',{name:'변환 전체 흐름 검토 저장',exact:true}).click();
  await expect(review.getByRole('status')).toContainText('변환 후 전체 흐름 검토를 저장했습니다.');
  await evidence.screenshot(page,'native-converted-full-flow-review-saved');
  await library.getByRole('button',{name:'새로고침',exact:true}).click();
  const approved=library.getByRole('article').filter({hasText:/approved_runtime_/});
  await expect(approved).toHaveCount(1);
  await approved.getByRole('button',{name:'다시 열기',exact:true}).click();
  const saved=(await api('/api/product-delivery/packages')).packages.find((row:any)=>row.name.startsWith('approved_runtime_'));
  expect(saved.scope_matches&&saved.approval_present&&saved.integrity==='verified').toBe(true);
  const approvedFiles=hashes(saved.package_path);
  await page.reload();await page.getByRole('button',{name:'패키지·장치·진단',exact:true}).click();
  const reopened=page.getByRole('region',{name:'저장된 검사 패키지 보관함'});
  await expect(reopened.getByRole('group',{name:'변환 후 전체 흐름 검토'}).getByText('저장된 변환 후 검토가 현재 근거와 일치합니다.')).toBeVisible({timeout:40_000});
  await evidence.screenshot(page,'native-converted-review-library-reopened');
  const prepare=reopened.getByRole('article').filter({hasText:/approved_runtime_/}).getByRole('button',{name:'배포 준비',exact:true});
  await prepare.click();
  // This selects through HTTP before remounting the service panel. Wait for
  // that handoff to finish so inputs are entered into the selected panel.
  await expect(prepare).toBeEnabled();
  const service=page.getByRole('region',{name:'검사 서비스 배포'});
  await expect(service.locator(':scope > div').getByLabel('배포할 저장 패키지',{exact:true})).toHaveValue(saved.package_id);
  await service.getByLabel('서비스 실행 장치',{exact:true}).selectOption('openvino:CPU');
  await service.getByLabel('서비스 검토자',{exact:true}).fill('Synthetic fixture authority');
  await service.getByRole('button',{name:'승인 패키지 적용',exact:true}).click();
  await expect(service.getByText('현재 응답: ready · 장치 openvino:CPU',{exact:false})).toBeVisible({timeout:60_000});
  const state=await api('/api/runtime-services');
  expect(state.active.release.manifest_sha256).toBe(saved.manifest_sha256);
  expect(state.runtime.manifest_sha256).toBe(saved.manifest_sha256);
  expect(state.active.release.whole_flow_review.runtime_review_sha256).toMatch(/^[0-9a-f]{64}$/);
  const config=JSON.parse(fs.readFileSync(path.join(fixture.project.project_dir,'runtime_service/service.json'),'utf8'));
  evidence.redact(config.token);
  const upload=await fetch(`http://127.0.0.1:${config.port}/v1/jobs/upload`,{method:'POST',headers:{'X-Vision-Token':config.token},body:fs.readFileSync(fixture.heldout[1])});
  expect(upload.status).toBe(202);const jobId=(await upload.json() as any).job_id;
  let job:any;
  await expect.poll(async()=>{const response=await fetch(`http://127.0.0.1:${config.port}/v1/jobs/${jobId}`,{headers:{'X-Vision-Token':config.token}});job=await response.json();return ['completed','error'].includes(job.state);},{timeout:40_000}).toBe(true);
  expect(job.state,JSON.stringify(job)).toBe('completed');
  expect(job.model_verdict).toBe('OK'); // These intentionally all-OK weights miss the synthetic NG input.
  expect(hashes(saved.package_path)).toEqual(approvedFiles);
  expect(hashes(fixture.package.package_path)).toEqual(fixture.original_package_files);
  expect(hashes(fixture.source)).toEqual(fixture.images);
  expect(hashes(fixture.project.models_dir)).toEqual(fixture.model_files);
  await evidence.screenshot(page,'native-converted-managed-ir-ready');
  evidence.note('actual_converted_flow',{capabilities,saved,state,job,approved_files:approvedFiles,
   actual_native_app:true,actual_conversion:true,actual_managed_IR_inference:true,synthetic_control:true,
   quality_accepted:false,device_accepted:false,physical_target_verified:false,windows_excluded:true});
 }finally{evidence.note('owned_service_cleanup',await api('/api/runtime-services/stop',{}));}
});
