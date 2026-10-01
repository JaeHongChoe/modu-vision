import { expect, test } from './fixtures/test';
test.use({actionTimeout:15_000});

// Real renderer/owned backend; Fleet transport and field acknowledgements are
// fixtures. This does not demonstrate deployment to a physical field agent.
for (const mode of ['owner', 'reviewer', 'readback'] as const) {
  const canEmergency=mode!=='reviewer';
  test(`emergency rollback requires reason and authenticated capability (${mode})`, async ({ page, request, renderer, evidence }) => {
    const created=await request.post(`http://127.0.0.1:${renderer.port}/api/project/create`,{data:{name:'Fleet emergency fixture',task:'classification'}});
    expect(created.status()).toBe(200);
    const target={target_id:'cell-a',name:'검사 장비 A',url:'https://field.example.invalid',token_set:true};
    const old={deployment_id:'deployment-old',created_at:1720000000,reviewer:'prior',release:{manifest_sha256:'a'.repeat(64),device:'cpu'}};
    const restored={...old,deployment_id:'deployment-restored',restored_from:old.deployment_id};
    let livePermission=canEmergency;let capabilityUnavailable=false;let active=old;let liveReady=true;let readbackUnavailable=false;const events:any[]=[];const requests:any[]=[];let reads=0;let releasePost: (()=>void)|undefined;
    await page.route('**/api/fleet/**',async route=>{
      const url=new URL(route.request().url());const endpoint=url.pathname;
      if(endpoint==='/api/fleet/capabilities'&&capabilityUnavailable)return route.fulfill({status:503,json:{detail:'Capability unavailable'}});
      if(endpoint==='/api/fleet/capabilities')return route.fulfill({json:{authentication:'shared_account_session',actor_id:'owner-id',actor_name:'Fixture owner',actor_role:livePermission?'owner':'reviewer',can_rollback:true,can_emergency_rollback:livePermission}});
      if(endpoint==='/api/fleet/targets')return route.fulfill({json:{targets:[target]}});
      if(endpoint.endsWith('/emergency-rollback')){
        const payload=route.request().postDataJSON();requests.push(payload);
        const requestId=`request-${requests.length}`;
        const base={request_id:requestId,target_id:target.target_id,deployment_id:old.deployment_id,actor_id:'owner-id',actor_name:'Fixture owner',authentication:'shared_account_session',actor_role:'owner',reason:payload.reason,created_at:1720000010};
        events.push({...base,event_id:`attempt-${requestId}`,event:'attempted'});
        await new Promise<void>(resolve=>{releasePost=resolve;});
        if(payload.reason==='승인 철회 확인'){
          events.push({...base,event_id:`reject-${requestId}`,event:'rejected',detail:'Approval revoked'});
          return route.fulfill({status:409,json:{detail:'Approval revoked'}});
        }
        active=restored;events.push({...base,event_id:`commit-${requestId}`,event:'committed',result_deployment_id:restored.deployment_id});
        if(payload.reason==='응답 확인 지연')readbackUnavailable=true;
        return route.fulfill({json:{...restored,emergency:{request_id:requestId,event_id:`commit-${requestId}`,reason:payload.reason,actor_id:'owner-id',actor_name:'Fixture owner'}}});
      }
      if(endpoint==='/api/fleet/targets/cell-a'){
        reads++;
        if(readbackUnavailable)return route.fulfill({status:503,json:{detail:'Readback unavailable'}});
        return route.fulfill({json:{target,runtime:{status:liveReady?'ready':'stopped',device:'cpu',manifest_sha256:'a'.repeat(64)},active,history:[old],matches_active:liveReady,emergency_rollback_events:events}});
      }
      return route.fallback();
    });
    await page.goto(renderer.url);
    await expect(page.getByTitle('프로젝트 관리', { exact: true }), 'the backend current project opens on load').toContainText('Fleet emergency fixture');
    await page.getByRole('button',{name:'패키지·장치·진단',exact:true}).click();
    await page.getByText('중앙 · 현장 장비 모델 관리',{exact:true}).click();
    await page.getByLabel('현장 장비 선택').selectOption('cell-a');
    await page.getByLabel('현장 배포 복원 이력').selectOption('deployment-old');
    const section=page.getByRole('region',{name:'긴급 롤백'});
    await expect(section).toBeVisible({timeout:5000});
    const submit=section.getByRole('button',{name:'사유 기록 후 긴급 롤백'});
    await expect(submit).toBeDisabled();
    if(!canEmergency){
      await expect(section.getByText('프로젝트 소유자 권한이 필요합니다.')).toBeVisible();
      await expect(section.getByLabel('긴급 롤백 사유')).toBeDisabled();
      await page.getByLabel('현장 배포 검토자').fill('Reviewer');
      await expect(page.getByRole('button',{name:'장비 롤백',exact:true})).toBeEnabled();
      expect(requests).toEqual([]);
    }else{
      await section.getByLabel('긴급 롤백 사유').fill(mode==='readback'?'응답 확인 지연':'설비 검사 응답 지연');
      await submit.click();
      await expect(section.getByRole('status')).toContainText('긴급 롤백 응답 확인 중');
      await expect(submit).toBeDisabled();
      await expect.poll(()=>Boolean(releasePost)).toBe(true);
      releasePost!();
      if(mode==='readback'){
        await expect(section.getByRole('alert')).toContainText('요청 전송됨');
        await expect(section.getByRole('alert')).toContainText('적용 확인 필요');
        await expect(section.getByRole('alert')).not.toContainText('긴급 롤백 실패');
        readbackUnavailable=false;
        await page.getByRole('button',{name:'실제 상태 확인',exact:true}).click();
        await expect(section.getByRole('status')).toContainText('긴급 롤백 적용 응답·감사 기록 확인됨');
        await expect(section.getByText('기록된 사유: 응답 확인 지연',{exact:true})).toBeVisible();
        evidence.note('fleet_emergency_transport_fixture',{canEmergency,mode,requests,reads,events,live_field_rollout:false});
        await section.scrollIntoViewIfNeeded();await evidence.screenshot(page,'fleet-emergency-readback');
        return;
      }
      await expect(section.getByRole('status')).toContainText('긴급 롤백 적용 응답·감사 기록 확인됨');
      liveReady=false;
      await page.getByRole('button',{name:'실제 상태 확인',exact:true}).click();
      await expect(section.getByRole('status')).toHaveCount(0);
      await expect(section.getByRole('alert')).toContainText('적용 확인 필요');
      liveReady=true;
      await page.getByRole('button',{name:'실제 상태 확인',exact:true}).click();
      await expect(section.getByRole('status')).toContainText('긴급 롤백 적용 응답·감사 기록 확인됨');
      expect(requests[0]).toEqual({deployment_id:'deployment-old',reason:'설비 검사 응답 지연'});
      expect(reads).toBeGreaterThanOrEqual(3);
      await section.getByText(/선택 장비의 긴급 롤백 감사 기록/).click();
      await expect(section.getByText('Fixture owner · 설비 검사 응답 지연',{exact:false}).first()).toBeVisible();
      await section.getByLabel('긴급 롤백 사유').fill('승인 철회 확인');
      await expect(section.getByText('기록된 사유: 설비 검사 응답 지연',{exact:true})).toBeVisible();
      releasePost=undefined;await submit.click();
      await expect.poll(()=>Boolean(releasePost)).toBe(true);releasePost!();
      await expect(section.getByRole('alert')).toContainText('Approval revoked');
      await expect(section.getByText('거부됨 · Fixture owner · 승인 철회 확인',{exact:false})).toBeVisible();
      await section.getByLabel('긴급 롤백 사유').fill('응답 확인 지연');
      releasePost=undefined;await submit.click();
      await expect.poll(()=>Boolean(releasePost)).toBe(true);releasePost!();
      await expect(section.getByRole('alert')).toContainText('요청 전송됨');
      await expect(section.getByRole('alert')).toContainText('적용 확인 필요');
      await expect(section.getByRole('alert')).not.toContainText('긴급 롤백 실패');
      readbackUnavailable=false;
      await page.getByRole('button',{name:'실제 상태 확인',exact:true}).click();
      await expect(section.getByRole('status')).toContainText('긴급 롤백 적용 응답·감사 기록 확인됨');
      await expect(section.getByText('기록된 사유: 응답 확인 지연',{exact:true})).toBeVisible();
    }
    if(mode==='owner'){
      livePermission=false;
      await page.getByRole('button',{name:'실제 상태 확인',exact:true}).click();
      await expect(submit).toBeDisabled();
      await expect(section.getByLabel('긴급 롤백 사유')).toBeDisabled();
      await expect(section.getByText('프로젝트 소유자 권한이 필요합니다.')).toBeVisible();
      livePermission=true;
      await page.getByRole('button',{name:'실제 상태 확인',exact:true}).click();
      await expect(submit).toBeEnabled();
      capabilityUnavailable=true;
      await page.getByRole('button',{name:'실제 상태 확인',exact:true}).click();
      await expect(submit).toBeDisabled();
      await expect(page.getByRole('alert').filter({hasText:'롤백 권한 확인 실패'})).toBeVisible();
      await expect(section.getByRole('status')).toContainText('긴급 롤백 적용 응답·감사 기록 확인됨');
      await expect(section.getByText('기록된 사유: 응답 확인 지연',{exact:true})).toBeVisible();
    }
    evidence.note('fleet_emergency_transport_fixture',{canEmergency,requests,reads,events,live_field_rollout:false});
    await section.scrollIntoViewIfNeeded();
    await evidence.screenshot(page,canEmergency?'fleet-emergency-owner':'fleet-emergency-reviewer');
  });
}
