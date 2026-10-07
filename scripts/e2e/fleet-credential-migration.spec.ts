import path from 'node:path';
import {execFileSync} from 'node:child_process';
import {test,expect} from './fixtures/test';
import {installDesktopHostShim} from './fixtures/desktop-host-shim';
const harness=require('./fixtures/harness.cjs');

test('legacy fleet credentials migrate from the app with failure recovery and reopen',async({page,renderer,workspace,evidence})=>{
  const call=async(route:string,body?:unknown)=>{
    const response=await page.request.fetch(renderer.origin+route,{method:body?'POST':'GET',data:body});
    expect(response.ok(),await response.text()).toBe(true);return response.json();
  };
  const project=await call('/api/project/create',{name:'Owned fleet credential migration',task:'classification'});
  const unavailable=await harness.closedLoopbackUrl();
  const ids=['a'.repeat(32),'b'.repeat(32)];
  const seeded=JSON.parse(execFileSync(harness.resolvePython(),['-c',`
import json,sys
from pathlib import Path
from backend.engine.fleet import FleetRegistry
item=FleetRegistry(sys.argv[1])
with item.connect() as db:
 for identifier,name in zip(('a'*32,'b'*32),('Legacy device A','Legacy device B')):
  db.execute('INSERT INTO targets VALUES(?,?,?,?)',(identifier,name,sys.argv[2],'controlled-legacy-agent-credential'))
print(json.dumps({'targets':item.targets(),'owned_project':str(item.root.parent)}))
`,project.project_dir,unavailable],{cwd:harness.REPO_ROOT,env:{...process.env,VISION_AI_STUDIO_USER_DATA_DIR:workspace.userData},encoding:'utf8'}).trim());
  expect(path.resolve(seeded.owned_project).startsWith(path.resolve(workspace.projects)+path.sep)).toBe(true);
  expect(seeded.targets.every((target:any)=>target.credential_storage==='legacy_project_database'&&!('token' in target))).toBe(true);
  await installDesktopHostShim(page,renderer.port);await page.goto(renderer.url);
  const open=async()=>{await page.getByRole('button',{name:'패키지·장치·진단',exact:true}).click();await page.getByText('중앙 · 현장 장비 모델 관리',{exact:true}).click();};
  await open();await page.getByLabel('현장 장비 선택',{exact:true}).selectOption(ids[0]);
  const section=page.getByRole('region',{name:'현장 장비 인증값 보관'});
  await expect(section).toContainText('프로젝트 안에 보관');
  const reason=section.getByLabel('인증값 이동 사유',{exact:true});
  const move=section.getByRole('button',{name:'인증값을 서버 저장소로 옮기기',exact:true});
  await expect(move).toBeDisabled();await reason.fill('short');await expect(move).toBeDisabled();
  const entered='Move this configured credential outside the portable project';await reason.fill(entered);
  const migration='**/api/fleet/targets/'+ids[0]+'/credentials/migrate';
  let posts=0;await page.route(migration,async route=>{posts++;await route.fulfill({status:409,contentType:'application/json',body:JSON.stringify({detail:'Controlled secret store is temporarily unavailable'})});});
  await move.click();await expect(section.getByRole('alert')).toContainText('이동을 확인하지 못했습니다');await expect(reason).toHaveValue(entered);
  expect((await call('/api/fleet/targets')).targets[0].credential_storage).toBe('legacy_project_database');
  await page.unroute(migration);
  // Lose the POST response after the real backend commits. The UI must recover
  // through the separate registered-target readback, without a second mutation.
  await page.route(migration,async route=>{posts++;const result=await route.fetch();expect(result.ok()).toBe(true);await route.abort('failed');});
  await move.click();await expect(section.getByRole('status')).toHaveText('인증값 이동 확인됨');
  expect(posts).toBe(2);await expect(section.getByText('이 서버에 보관됩니다.',{exact:false})).toBeVisible();
  const rows=(await call('/api/fleet/targets')).targets;expect(rows[0].credential_storage).toBe('server_secret_v1');expect(rows[1].credential_storage).toBe('legacy_project_database');
  const audit=JSON.parse(execFileSync(harness.resolvePython(),['-c',`
import json,sys
from backend.engine.fleet import FleetRegistry
item=FleetRegistry(sys.argv[1])
with item.connect() as db:events=[dict(row) for row in db.execute('SELECT target_id,event,actor_id,reason FROM credential_events')]
assert item.secret('a'*32)=='controlled-legacy-agent-credential'
print(json.dumps({'events':events,'credential_value_unchanged':True,'targets':item.targets()}))
`,project.project_dir],{cwd:harness.REPO_ROOT,env:{...process.env,VISION_AI_STUDIO_USER_DATA_DIR:workspace.userData},encoding:'utf8'}).trim());
  expect(audit.events).toHaveLength(1);expect(audit.events[0].reason).toBe(entered);expect(audit.events[0].target_id).toBe(ids[0]);
  await page.unroute(migration);await page.reload();await open();await page.getByLabel('현장 장비 선택',{exact:true}).selectOption(ids[0]);
  await expect(section).toContainText('이 서버에 보관됩니다.');await expect(section.getByLabel('인증값 이동 사유',{exact:true})).toHaveCount(0);
  await page.setViewportSize({width:700,height:820});await page.getByLabel('현장 장비 선택',{exact:true}).selectOption(ids[1]);await expect(section).toContainText('프로젝트 안에 보관');
  const secondReason=section.getByLabel('인증값 이동 사유',{exact:true});await secondReason.fill('Keep the second configured target scoped to this selection');await secondReason.press('Tab');await expect(section.getByRole('button',{name:'인증값을 서버 저장소로 옮기기',exact:true})).toBeFocused();
  await section.scrollIntoViewIfNeeded();await evidence.screenshot(page,'fleet-credential-migration-narrow');
  evidence.note('credential_migration',{audit,posts,agent_online_required:false,actual_backend:true,post_response_loss_reconciled:true,second_target_unchanged:true,native_windows:false});
});
