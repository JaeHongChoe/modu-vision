import path from 'node:path';
import type {Request} from '@playwright/test';
import {test, expect} from './fixtures/test';
import {browserTeamServer} from './fixtures/browser_team_server';

test.use({ignoreHTTPSErrors: true, actionTimeout: 15000});

type Wire = {
  sequence: number; phase: string; method: string; route: string;
  project: string | null; context: string | null; body: string | null; bodyPresent: boolean;
  status: number | null; finished: boolean; finishError: string | null;
};
const accountReads = ['/api/accounts/me', '/api/accounts/users'];
const discovery = ['/api/compute/profiles', '/api/compute/selection'];
const scopedRead = '/api/compute/reservations';
const captured = new Set([...accountReads, ...discovery, scopedRead, '/api/accounts/select-project', '/api/accounts/login']);

// Real mounted renderer + existing HTTPS cookie-session backend. The seeded
// profile is inert loopback metadata: no SSH probe, GPU job or inference runs.
// This is not native bearer-owned-admission or full-provider qualification.
test('browser account login automatically hydrates the mounted Header and account reopen keeps project authority',
  async ({page, rendererBuild, workspace, evidence, loopbackGuard}) => {
    test.setTimeout(240000);
    const server = await browserTeamServer(workspace, rendererBuild.outDir);
    loopbackGuard.allow(server.port);
    evidence.redact(server.backend.token);
    const username = 'headeradministrator', password = 'owned browser header fixture 123';
    evidence.redact(password);
    const profile = {id: 'owned-header-hydration-profile', name: 'Owned stored header target',
      ssh_target: '127.0.0.1', ssh_port: 1, remote_root: path.join(workspace.root, 'inert-header-target'),
      runtime_kind: 'python', runtime_value: 'python3', gpu_selector: null};
    const wires: Wire[] = [], mutations: Array<{method: string; route: string}> = [];
    const byRequest = new WeakMap<Request, Wire>();
    let phase = 'initial-unauthenticated', navigationCount = 0;
    const requestSeen = (request: Request) => {
      const url = new URL(request.url());
      if (url.origin !== server.origin || !url.pathname.startsWith('/api/')) return;
      if (!['GET', 'HEAD', 'OPTIONS'].includes(request.method())) mutations.push({method: request.method(), route: url.pathname});
      if (!captured.has(url.pathname)) return;
      const headers = request.headers();
      // Never retain password/login body, bearer value, cookie value or CSRF.
      const row: Wire = {sequence: wires.length + 1, phase, method: request.method(), route: url.pathname,
        project: headers['x-vision-project'] ?? null, context: headers['x-vision-context'] ?? null,
        body: url.pathname === '/api/accounts/select-project' ? request.postData() : null,
        bodyPresent: request.postData() !== null,
        status: null, finished: false, finishError: null};
      wires.push(row); byRequest.set(request, row);
    };
    const responseSeen = (response: import('@playwright/test').Response) => {
      const row = byRequest.get(response.request());
      if (!row) return;
      row.status = response.status();
      void response.finished().then(error => {
        row.finished = error === null;
        if (error) row.finishError = error.message;
      }, error => {row.finishError = String(error);});
    };
    const navigationSeen = (frame: import('@playwright/test').Frame) => {
      if (frame === page.mainFrame() && new URL(frame.url()).origin === server.origin) navigationCount++;
    };
    const rows = (wantedPhase: string, route: string) => wires.filter(row => row.phase === wantedPhase && row.route === route);
    const complete = (wantedPhase: string, route: string, status: number) => rows(wantedPhase, route)
      .filter(row => row.status === status && row.finished && row.finishError === null);
    const assertGlobal = (row: Wire, expectedMethod: string) => {
      expect(row.method).toBe(expectedMethod);
      expect(row.status).toBe(200);
      expect(row.finished).toBe(true);
      expect(row.finishError).toBeNull();
      expect(row.project, `${row.route} must not carry the accepted project header`).toBeNull();
      expect(row.context, `${row.route} must not carry the accepted context header`).toBeNull();
      if (expectedMethod === 'GET') expect(row.bodyPresent).toBe(false);
    };
    let primary: unknown, hasPrimary = false;
    const cleanupErrors: unknown[] = [];
    try {
      await server.call('/api/accounts/bootstrap', {username, password});
      const administrator = await server.call('/api/accounts/login', {username, password});
      evidence.redact(administrator.token);
      const project = await server.call('/api/project/create', {name: 'Owned header hydration project', task: 'classification'}, administrator.token);
      await server.call('/api/compute/profiles', profile, administrator.token);
      await server.call('/api/compute/selection', {compute_profile_id: profile.id}, administrator.token, 'PUT');
      const beforeProfiles = await server.call('/api/compute/profiles', undefined, administrator.token);
      const beforeSelection = await server.call('/api/compute/selection', undefined, administrator.token);
      const me = await server.call('/api/accounts/me', undefined, administrator.token);
      const context = {workspace_id: me.workspace_id, project_id: project.id, actor_id: me.user.id, mode: 'team'};

      page.on('request', requestSeen);
      page.on('response', responseSeen);
      page.on('framenavigated', navigationSeen);
      await page.goto(server.origin);
      const target = page.getByLabel('Compute location', {exact: true});
      // Drain the original finite retry budget before login. A surviving old
      // 401 retry must not accidentally make the unpatched Header look fixed.
      // Source contract: initial attempt + four retries at 500/1000/1500/2000ms.
      await expect.poll(() => complete('initial-unauthenticated', discovery[0], 401).length,
        {timeout: 20000, intervals: [100, 250, 500]}).toBe(5);
      await expect.poll(() => complete('initial-unauthenticated', discovery[1], 401).length,
        {timeout: 20000, intervals: [100, 250, 500]}).toBe(5);
      expect(rows('initial-unauthenticated', discovery[0])).toHaveLength(5);
      expect(rows('initial-unauthenticated', discovery[1])).toHaveLength(5);
      await expect(target).toBeDisabled();
      await page.getByRole('button', {name: '서버 관리', exact: true}).click();
      await page.getByText('공동 작업 서버', {exact: true}).click();
      await expect(page.getByLabel('공동 작업 서버 주소', {exact: true})).toHaveValue(server.origin);
      await page.getByLabel('공유 계정 이름', {exact: true}).fill(username);
      await page.getByLabel('공유 계정 비밀번호', {exact: true}).fill(password);
      phase = 'ui-login';
      await page.getByRole('button', {name: '연결·로그인', exact: true}).click();
      await expect(page.getByLabel('공동 작업 프로젝트', {exact: true})).toHaveValue(project.id);
      await expect(page.getByTitle('프로젝트 관리', {exact: true})).toContainText(project.name);
      await expect.poll(() => complete('ui-login', discovery[0], 200).length).toBe(1);
      await expect.poll(() => complete('ui-login', discovery[1], 200).length).toBe(1);
      for (const route of discovery) {
        const own = rows('ui-login', route);
        expect(own).toHaveLength(1);
        expect(own[0].method).toBe('GET');
        expect(own[0].project).toBe(project.id);
        expect(JSON.parse(own[0].context!)).toEqual(context);
      }
      await expect(target).toHaveValue(profile.id);
      await expect(target).toBeEnabled();
      await expect(target.locator(`option[value="${profile.id}"]`)).toHaveText(profile.name);
      await page.getByRole('dialog', {name: '컴퓨팅 서버 관리', exact: true})
        .getByRole('button', {name: '닫기', exact: true}).click();
      await evidence.screenshot(page, 'account-login-header-auto-hydration');

      phase = 'ui-reopen';
      await page.getByRole('button', {name: '서버 관리', exact: true}).click();
      await expect(page.getByLabel('공동 작업 프로젝트', {exact: true})).toHaveValue(project.id);
      for (const route of accountReads) {
        await expect.poll(() => complete('ui-reopen', route, 200).length).toBe(1);
        expect(rows('ui-reopen', route)).toHaveLength(1);
        assertGlobal(rows('ui-reopen', route)[0], 'GET');
      }
      const assertScopedRows = () => {
        const observed = rows('ui-reopen', scopedRead);
        expect(observed.length).toBeGreaterThanOrEqual(1);
        for (const row of observed) {
          expect(row.status).toBe(200);
          expect(row.finished).toBe(true);
          expect(row.finishError).toBeNull();
          expect(row.method).toBe('GET');
          expect(row.bodyPresent).toBe(false);
          expect(row.project).toBe(project.id);
          expect(JSON.parse(row.context!)).toEqual(context);
        }
      };
      await expect.poll(() => rows('ui-reopen', scopedRead).length).toBeGreaterThanOrEqual(1);
      await expect.poll(() => rows('ui-reopen', scopedRead).every(row => row.finished && row.finishError === null)).toBe(true);
      assertScopedRows();
      const selects = rows('ui-login', '/api/accounts/select-project');
      expect(selects).toHaveLength(1);
      assertGlobal(selects[0], 'POST');
      expect(JSON.parse(selects[0].body!)).toEqual({project_id: project.id});
      expect(rows('ui-reopen', '/api/accounts/select-project')).toHaveLength(0);
      expect(rows('ui-reopen', '/api/accounts/login')).toHaveLength(0);
      await expect(target).toHaveValue(profile.id);
      await expect(target).toBeEnabled();
      expect(mutations).toEqual([{method: 'POST', route: '/api/accounts/login'},
        {method: 'POST', route: '/api/accounts/select-project'}]);
      expect(navigationCount).toBe(1);
      expect(await server.call('/api/compute/profiles', undefined, administrator.token)).toEqual(beforeProfiles);
      expect(await server.call('/api/compute/selection', undefined, administrator.token)).toEqual(beforeSelection);
      const cookies = await page.context().cookies(server.origin);
      expect(cookies.find(cookie => cookie.name === 'vision_session'))
        .toMatchObject({httpOnly: true, secure: true, sameSite: 'Strict'});
      const storage = await page.evaluate(() => JSON.stringify({local: {...localStorage}, session: {...sessionStorage}}));
      for (const secret of [password, administrator.token, server.backend.token]) expect(storage).not.toContain(secret);
      await evidence.screenshot(page, 'account-reopen-global-reads-context-preserved');
      // Recheck every observed row after the last await; a late failed or
      // unfinished poll cannot disappear behind the earlier successful read.
      assertScopedRows();
      evidence.note('account_header_browser_scope', {actual_renderer_ui: true, actual_cookie_backend: true,
        context, profile_id: profile.id, source_build: rendererBuild.index,
        initial_auth_retry_budget_exhausted: true, manual_compute_refresh_clicked: false,
        page_reload_after_login: false, fixture_profile_and_selection_unchanged: true,
        actual_ssh_or_gpu_execution: false, native_bearer_owned_admission: false,
        full_helper_or_native_provider_qualification: false});
    } catch (error) {hasPrimary = true; primary = error;}
    finally {
      page.off('request', requestSeen);
      page.off('response', responseSeen);
      page.off('framenavigated', navigationSeen);
      evidence.note('account_header_browser_wire', {wires, mutations, navigation_count: navigationCount});
      try {evidence.note('account_header_browser_stop', await server.close());}
      catch (error) {cleanupErrors.push(error);}
      evidence.addFile(server.backend.logs.stdout);
      evidence.addFile(server.backend.logs.stderr);
      if (cleanupErrors.length) evidence.note('account_header_browser_cleanup_errors', cleanupErrors.map(String));
      if (hasPrimary) throw primary;
      if (cleanupErrors.length) throw new AggregateError(cleanupErrors, 'Owned account/header browser fixture cleanup failed');
    }
  });
