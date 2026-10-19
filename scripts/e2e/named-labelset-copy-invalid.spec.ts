import fs from 'node:fs';
import path from 'node:path';
import {createHash} from 'node:crypto';
import type {Request} from '@playwright/test';
import {test, expect} from './fixtures/test';
import {installDesktopHostShim} from './fixtures/desktop-host-shim';

test.use({actionTimeout: 10_000});
const POINTER = '/records/14/actions/0/scenarios/invalid';
const DETAIL = 'Label set contains a symbolic link';
const sha = (raw: Buffer) => createHash('sha256').update(raw).digest('hex');
const raw9 = (s: fs.BigIntStats) => [s.dev, s.ino, s.mode, s.nlink, s.size, s.mtimeNs, s.ctimeNs, s.uid, s.gid].map(String);
function regular(file: string) {
  const first = fs.lstatSync(file, {bigint: true});
  expect(first.isFile()).toBe(true); expect(first.isSymbolicLink()).toBe(false); expect(first.nlink).toBe(1n);
  const fd = fs.openSync(file, fs.constants.O_RDONLY | fs.constants.O_NOFOLLOW);
  let failed = false, primary: unknown, result: {sha256: string; size: number; raw9: string[]} | undefined;
  try {
    expect(raw9(fs.fstatSync(fd, {bigint: true}))).toEqual(raw9(first));
    const bytes = fs.readFileSync(fd); expect(BigInt(bytes.length)).toBe(first.size);
    expect(raw9(fs.fstatSync(fd, {bigint: true}))).toEqual(raw9(first));
    expect(raw9(fs.lstatSync(file, {bigint: true}))).toEqual(raw9(first));
    result = {sha256: sha(bytes), size: bytes.length, raw9: raw9(first)};
  } catch (error) {failed = true; primary = error;}
  finally {try {fs.closeSync(fd);} catch (error) {if (!failed) {failed = true; primary = error;}}}
  if (failed) throw primary;
  return result!;
}
function tree(root: string, ownedLink?: {file: string; target: string; raw9: string[]}) {
  const rows: Record<string, unknown> = {};
  const walk = (file: string) => {
    const st = fs.lstatSync(file, {bigint: true}), relative = path.relative(root, file).split(path.sep).join('/');
    if (st.isSymbolicLink()) {
      expect(file).toBe(ownedLink?.file); expect(fs.readlinkSync(file)).toBe(ownedLink?.target);
      expect(raw9(st)).toEqual(ownedLink?.raw9);
      rows[relative] = {kind: 'owned-link', target: fs.readlinkSync(file), raw9: raw9(st)};
    } else if (st.isDirectory()) {
      const names = fs.readdirSync(file).sort();
      rows[relative] = {kind: 'directory', names, identity: [st.dev, st.ino, st.mode, st.uid, st.gid].map(String)};
      for (const name of names) walk(path.join(file, name));
      expect(fs.readdirSync(file).sort()).toEqual(names);
    } else {expect(st.isFile()).toBe(true); rows[relative] = {kind: 'file', ...regular(file)};}
  };
  walk(root); return rows;
}
async function within<T>(operation: Promise<T>, deadline: number): Promise<T> {
  const remaining = deadline - performance.now();
  if (remaining <= 0) throw Error('Original F015 request exceeded its 10s completion deadline');
  let timer: ReturnType<typeof setTimeout> | undefined;
  try {return await Promise.race([operation, new Promise<never>((_, reject) => {
    timer = setTimeout(() => reject(Error('Original F015 request exceeded its 10s completion deadline')), remaining);
  })]);} finally {if (timer !== undefined) clearTimeout(timer);}
}

test('named labelset copy rejects its own invalid source tree with actual 422 and preserves original labels',
  async ({page, request, renderer, workspace, evidence}) => {
    test.setTimeout(240_000);
    await installDesktopHostShim(page, renderer.port);
    const fixtureCalls: unknown[] = [], rows: any[] = [], secondary: Array<{label: string; error: string}> = [];
    let context: any = null, project: any, source = '', annotations = '', registryFile = '', projectFile = '';
    let protectedBefore: any, withLinkBefore: any, link: {file: string; target: string; raw9: string[]} | undefined;
    let linkCreated = false, attached = false, completed = false, failed = false, primary: unknown;
    const api = async (route: string, body?: unknown, method?: string) => {
      const response = await request.fetch(renderer.origin + route, {timeout: 10_000,
        method: method || (body === undefined ? 'GET' : 'POST'),
        headers: context ? {'X-Vision-Project': context.project_id, 'X-Vision-Context': JSON.stringify(context)} : {},
        ...(body === undefined ? {} : {data: body})});
      const bytes = await response.body(); expect(response.status(), route + ': ' + bytes.toString()).toBe(200);
      const value = JSON.parse(bytes.toString());
      fixtureCalls.push({route, method: method || (body === undefined ? 'GET' : 'POST'), request: body ?? null,
        status: response.status(), body: value, body_sha256: sha(bytes), headers: response.headers()});
      return {value, headers: response.headers()};
    };
    const noNamedTarget = () => {
      try {fs.lstatSync(path.join(project.project_dir, 'labelsets'));}
      catch (error) {if ((error as NodeJS.ErrnoException).code === 'ENOENT') return; throw error;}
      throw Error('Rejected copy created or retained a named-labelset target directory');
    };
    const disk = (allowLink = false) => ({registry: regular(registryFile), project: regular(projectFile),
      source: tree(source), annotations: tree(annotations, allowLink ? link : undefined)});
    const storage = () => page.evaluate(() => ({local: {...localStorage}, session: {...sessionStorage}}));
    const observe = (req: Request) => {
      const address = new URL(req.url());
      if (address.origin !== renderer.origin || !address.pathname.startsWith('/api/')) return;
      const started = performance.now(), row: any = {ordinal: rows.length, method: req.method(), url: req.url(),
        request_body: req.postData(), headers: Object.fromEntries(Object.entries(req.headers()).map(([key, value]) =>
          [key, /^(authorization|cookie|x-vision-token)$/i.test(key) ? '[redacted]' : value])),
        started, deadline: started + 10_000, complete: false, failure: null};
      rows.push(row);
      row.pending = (async () => {
        try {
          expect(req.frame()).toBe(page.mainFrame());
          const response = await within(req.response(), row.deadline); expect(response).not.toBeNull();
          const bytes = await within(response!.body(), row.deadline);
          expect(await within(response!.finished(), row.deadline)).toBeNull();
          row.status = response!.status(); row.response_headers = response!.headers(); row.body_sha256 = sha(bytes);
          const json = /application\/json/i.test(row.response_headers['content-type'] || '');
          row.body = json ? JSON.parse(bytes.toString()) : {binary_bytes: bytes.length};
          if (json) row.response_body_text = bytes.toString('utf8');
          const rh = req.headers();
          if (row.method === 'GET') {
            expect(row.request_body).toBeNull(); expect(row.status).toBe(200);
            if (address.pathname === '/api/project/current') {
              expect(address.search).toBe(''); expect(rh['x-vision-project']).toBeUndefined();
              expect(rh['x-vision-context']).toBeUndefined(); expect(row.body.id).toBe(project.id);
              expect(row.body.active_labelset_id).toBe('default');
            } else if (address.pathname.startsWith('/api/dataset/thumbnail/')) {
              expect(workspace.images.map(image => fs.realpathSync(image.path))).toContain(address.searchParams.get('file_path'));
              expect(bytes.length).toBeGreaterThan(0);
            } else if (address.pathname.startsWith('/api/dataset/raw/')) {
              const image = workspace.images.find(item => address.pathname === '/api/dataset/raw/' + encodeURIComponent(path.basename(item.path))
                && address.searchParams.get('file_path') === fs.realpathSync(item.path));
              expect(image).toBeDefined(); expect([...address.searchParams.keys()]).toEqual(['file_path']);
              expect(rh['x-vision-project']).toBeUndefined(); expect(rh['x-vision-context']).toBeUndefined();
              expect(row.response_headers['content-type']).toMatch(/^image\/png(?:;|$)/i);
              expect(bytes.length).toBe(image!.bytes); expect(sha(bytes)).toBe(image!.sha256);
            } else {
              expect(rh['x-vision-project']).toBe(project.id); expect(JSON.parse(rh['x-vision-context'])).toEqual(context);
            }
          } else {
            expect(row.method).toBe('POST'); expect(address.pathname).toBe('/api/project/labelsets');
            expect(address.search).toBe(''); expect(rh['x-vision-project']).toBe(project.id);
            expect(JSON.parse(rh['x-vision-context'])).toEqual(context);
            expect(req.postDataJSON()).toEqual({name: 'Owned valid review copy'});
            expect(row.status).toBe(422); expect(json).toBe(true); expect(row.body).toEqual({detail: DETAIL});
          }
          if (row.response_headers['x-vision-context'] !== undefined)
            expect(JSON.parse(row.response_headers['x-vision-context'])).toEqual(context);
          row.finished = performance.now(); expect(row.finished).toBeLessThanOrEqual(row.deadline); row.complete = true;
        } catch (error) {row.failure = error;}
      })();
    };
    const settle = async () => {
      for (const row of [...rows]) {await row.pending; if (row.failure !== null) throw row.failure; expect(row.complete).toBe(true);}
    };
    const assertMutations = () => {
      const writes = rows.filter(row => row.method !== 'GET');
      expect(writes).toHaveLength(1); expect(writes[0].method).toBe('POST');
      expect(new URL(writes[0].url).pathname).toBe('/api/project/labelsets'); expect(writes[0].status).toBe(422);
      expect(rows.some(row => /\/labelsets\/[^/]+\/activate$/.test(new URL(row.url).pathname))).toBe(false);
    };
    try {
      project = (await api('/api/project/create', {name: 'Owned invalid named-labelset source', task: 'classification'})).value;
      source = fs.realpathSync(workspace.dataset);
      await api('/api/project/update', {source_dataset_dir: source}, 'PUT');
      await api('/api/dataset/import', {folder_path: source, task: 'classification'});
      const image = workspace.images.find(item => item.label === 'ng')!; expect(image).toBeDefined();
      const imagePath = fs.realpathSync(image.path), imageId = path.basename(imagePath, '.png');
      const annotationRoute = '/api/annotations/' + imageId + '?file_path=' + encodeURIComponent(imagePath);
      await api('/api/annotations/save', {image_id: imageId, image_path: imagePath, image_width: 32, image_height: 32,
        actor: 'owned-invalid-labelset-fixture', annotations: [{id: 'owned-invalid-labelset-tag', type: 'tag', label: 'ng', category_id: 1, is_normal: false}]});
      await api('/api/dataset/metadata?limit=10'); await api('/api/team-data');
      const current = await api('/api/project/current'); project = current.value;
      expect(current.headers['x-vision-context']).toBeDefined(); context = JSON.parse(current.headers['x-vision-context']);
      expect(context.project_id).toBe(project.id); expect(context.mode).toBe('local');
      expect(fs.realpathSync(path.dirname(project.project_dir))).toBe(fs.realpathSync(workspace.projects));
      expect(project.active_labelset_id).toBe('default'); expect(project.source_dataset_dir).toBe(source);
      annotations = project.annotations_dir; registryFile = path.join(project.project_dir, 'labelsets.json');
      projectFile = path.join(project.project_dir, 'project.json');
      expect(annotations).toBe(path.join(project.project_dir, 'annotations'));
      expect(fs.realpathSync(annotations)).toBe(annotations); noNamedTarget();
      const saved = (await api(annotationRoute)).value; expect(saved.annotations).toHaveLength(1);
      await page.goto(renderer.url);
      await expect(page.getByTitle('프로젝트 관리', {exact: true})).toContainText(project.name);
      await page.getByRole('navigation', {name: 'Workflow Stages'}).getByRole('button').nth(1).click();
      const focus = page.getByRole('button', {name: '집중 편집', exact: true});
      if (await focus.getAttribute('aria-pressed') === 'true') await focus.click();
      await page.locator('[data-labeling-filmstrip]').getByAltText(path.basename(imagePath), {exact: true}).click();
      await expect(page.getByTitle('라벨 삭제', {exact: true})).toHaveCount(1);
      await expect(page.getByTestId('annotation-save-button')).toHaveText('Saved');
      const active = page.getByLabel('활성 레이블셋', {exact: true});
      await expect(active).toHaveValue('default'); await expect(active).toBeEnabled();
      await expect(active.locator('option')).toHaveCount(1); await expect(active.locator('option')).toHaveText('기본 라벨');
      const name = page.getByLabel('새 레이블셋 이름', {exact: true}), copy = page.getByRole('button', {name: '현재 라벨 복제', exact: true});
      await expect(name).toBeVisible(); await expect(copy).toBeDisabled();
      const registry = (await api('/api/project/labelsets')).value;
      const selected = (await api('/api/project/current')).value, storageBefore = await storage();
      protectedBefore = disk(); noNamedTarget();
      for (const item of workspace.images) expect(regular(fs.realpathSync(item.path)).sha256).toBe(item.sha256);
      expect(workspace.images).toHaveLength(2);
      const targets = Object.keys(protectedBefore.annotations).filter(relative => relative.endsWith('.json'));
      expect(targets.length).toBeGreaterThan(0);
      const target = path.join(annotations, targets[0]), spelling = path.relative(annotations, target);
      expect(spelling).not.toBe(''); expect(spelling.startsWith('..')).toBe(false); expect(path.isAbsolute(spelling)).toBe(false);
      regular(target);
      const linkPath = path.join(annotations, '.owned-invalid-labelset-copy.link');
      expect(fs.existsSync(linkPath)).toBe(false);
      page.on('request', observe); attached = true;
      fs.symlinkSync(spelling, linkPath, 'file'); linkCreated = true;
      const st = fs.lstatSync(linkPath, {bigint: true}); expect(st.isSymbolicLink()).toBe(true);
      link = {file: linkPath, target: spelling, raw9: raw9(st)};
      expect(path.resolve(path.dirname(linkPath), fs.readlinkSync(linkPath))).toBe(target);
      withLinkBefore = disk(true);
      await name.fill('Owned valid review copy'); await expect(copy).toBeEnabled(); await copy.click();
      await expect(page.getByRole('alert').filter({hasText: DETAIL})).toBeVisible();
      await expect(copy).toBeEnabled(); await expect(name).toHaveValue('Owned valid review copy');
      await expect(active).toHaveValue('default'); await expect(active.locator('option')).toHaveCount(1);
      await settle(); assertMutations(); noNamedTarget(); expect(disk(true)).toEqual(withLinkBefore);
      expect((await api('/api/project/labelsets')).value).toEqual(registry);
      expect((await api('/api/project/current')).value).toEqual(selected);
      expect((await api(annotationRoute)).value).toEqual(saved); expect(await storage()).toEqual(storageBefore);
      await expect(page.getByTestId('annotation-save-button')).toHaveText('Saved');
      await evidence.screenshot(page, 'named-labelset-copy-invalid-real422-originals-preserved');
      await settle(); assertMutations(); noNamedTarget(); expect(disk(true)).toEqual(withLinkBefore);
      completed = true;
      evidence.note('named_labelset_copy_invalid', {selected_pending_pointer: POINTER, feature: 'F015', owner: 'S3-03',
        action: 'named-labelset-copy', dimension: 'invalid', invalid_kind: 'owned active annotation source contains a symlink',
        actual_original_backend_422: true, exact_detail: DETAIL, UI_visible_alert: true,
        registry, context, project_id: project.id, saved_annotation: saved, source_images: workspace.images,
        protected_before: protectedBefore, protected_with_link_before: withLinkBefore, protected_after_refusal: disk(true),
        owned_link: link, only_copy_POST: true, activate_requests: 0, named_targets_created: 0,
        response_mocked: false, renderer_state_injected: false, native_or_GPU_or_whole_feature_credit: false});
    } catch (error) {failed = true; primary = error;}
    finally {
      const attempt = async (label: string, work: () => unknown | Promise<unknown>) => {
        try {await work();} catch (error) {secondary.push({label, error: String(error)}); if (!failed) {failed = true; primary = error;}}
      };
      await attempt('detach observer', () => {if (attached) {page.off('request', observe); attached = false;}});
      await attempt('all observed original responses', settle);
      await attempt('remove only the exact newly owned link', () => {
        if (!linkCreated) return;
        expect(link).toBeDefined(); const st = fs.lstatSync(link!.file, {bigint: true});
        expect(st.isSymbolicLink()).toBe(true); expect(raw9(st)).toEqual(link!.raw9);
        expect(fs.readlinkSync(link!.file)).toBe(link!.target); fs.unlinkSync(link!.file); linkCreated = false;
      });
      if (protectedBefore) await attempt('source registry config and annotation originals after link removal', () => {
        expect(disk()).toEqual(protectedBefore); noNamedTarget();
      });
      evidence.note('named_labelset_copy_invalid_closure', {completed, first_exception: failed ? String(primary) : null,
        secondary, fixture_calls: fixtureCalls, link_created_still_present: linkCreated, observer_detached: !attached,
        all_observed_responses_complete: rows.every(row => row.complete && row.failure === null),
        rows: rows.map(({pending, failure, ...row}) => ({...row, failure: failure === null ? null : String(failure)})),
        final_originals_preserved: protectedBefore !== undefined && !linkCreated && !failed});
    }
    if (failed) throw primary;
    expect(completed).toBe(true); expect(linkCreated).toBe(false); expect(attached).toBe(false);
  });
