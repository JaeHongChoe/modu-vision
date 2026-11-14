import { expect, test } from './fixtures/test';

test('project dialog contains keyboard focus and restores connected project opener after guide handoff', async ({ page, request, renderer, evidence }) => {
  expect((await request.post(`${renderer.origin}/api/project/create`, { data: { name: 'Owned project keyboard', task: 'classification' } })).ok()).toBe(true);
  await page.goto(renderer.url);
  await expect(page.locator('#workflow-project')).toContainText('Owned project keyboard');
  await page.getByRole('button', { name: '간단히 보기', exact: true }).click();
  await page.getByText('더 보기', { exact: true }).first().click();
  await page.getByRole('button', { name: '처음 시작 안내', exact: true }).click();
  await page.getByRole('dialog', { name: '처음 시작하기' }).getByRole('button', { name: '내 사진으로 만들기', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: '프로젝트 관리', exact: true });
  const name = dialog.getByLabel(/프로젝트 이름/);
  await expect(name).toBeFocused();
  await expect(name).toHaveCSS('outline-style', 'solid');
  const close = dialog.getByRole('button', { name: '프로젝트 관리 닫기', exact: true });
  const optional = dialog.locator('summary').filter({ hasText: '설명·저장 위치' });
  await close.focus();
  await page.keyboard.press('Shift+Tab');
  await expect(optional).toBeFocused();
  await page.keyboard.press('Tab');
  await expect(close).toBeFocused();
  await optional.focus();
  await page.keyboard.press('Enter');
  const path = dialog.getByPlaceholder('기본 프로젝트 폴더 사용');
  await path.focus();
  // A browser has no native path picker: the disabled picker is skipped at the boundary.
  await page.keyboard.press('Tab');
  await expect(close).toBeFocused();
  await optional.focus();
  await page.keyboard.press('Enter');
  await page.keyboard.press('Escape');
  await expect(dialog).toHaveCount(0);
  await expect(page.locator('#workflow-project')).toBeFocused();
  await page.keyboard.press('Enter');
  await expect(dialog).toBeVisible();
  await close.focus();
  await page.keyboard.press('Shift+Tab');
  await expect.poll(() => dialog.evaluate(node => node.contains(document.activeElement))).toBe(true);
  await page.keyboard.press('Escape');
  await expect(dialog).toHaveCount(0);
  await expect(page.locator('#workflow-project')).toBeFocused();
  await evidence.screenshot(page, 'project-keyboard-focus-restored');
  evidence.note('project_keyboard', { guide_handoff: true, tab_both_directions: true, closed_disclosure_skipped: true, disabled_controls_skipped: true, escape_restores_opener: true, model_execution: false });
});

// Real React/portal composition, isolated from app accounts and services. This verifies the
// shared primitive contract; it is not evidence of a specific production nested-modal flow.
test('nested portalled workspace modal owns interior Tab and restores its parent opener', async ({ page, evidence }) => {
  const { buildSync } = require('esbuild');
  const compiled = buildSync({
    stdin: {
      contents: `import React, {useState} from 'react';
        import {createRoot} from 'react-dom/client';
        import {WorkspaceDialog} from './src/renderer/components/common/WorkspaceDialog';
        function Fixture(){const [child,setChild]=useState(false);return <WorkspaceDialog title="Parent workspace" onClose={()=>{}}>
          <button onClick={()=>setChild(true)}>Open child workspace</button><button>Parent last</button>
          {child&&<WorkspaceDialog title="Child workspace" onClose={()=>setChild(false)}>
            <button>Child middle</button><div role="dialog" aria-label="Inline nonmodal help"><button>Child last</button></div>
          </WorkspaceDialog>}
        </WorkspaceDialog>;}createRoot(document.getElementById('root')).render(<Fixture/>);`,
      loader: 'tsx', resolveDir: process.cwd(), sourcefile: 'nested-workspace-keyboard-fixture.tsx',
    },
    bundle: true, write: false, format: 'iife', platform: 'browser', jsx: 'automatic',
    define: { 'process.env.NODE_ENV': '"production"' },
  }).outputFiles[0].text;
  await page.setContent('<!doctype html><html><body><div id="root"></div></body></html>');
  await page.addScriptTag({ content: compiled });
  const parent = page.getByRole('dialog', { name: 'Parent workspace', exact: true });
  const opener = parent.getByRole('button', { name: 'Open child workspace', exact: true });
  await opener.click();
  const child = page.getByRole('dialog', { name: 'Child workspace', exact: true });
  const close = child.getByRole('button', { name: 'Child workspace 닫기', exact: true });
  await expect(close).toBeFocused();
  await page.keyboard.press('Tab');
  await expect(child.getByRole('button', { name: 'Child middle', exact: true })).toBeFocused();
  await page.keyboard.press('Tab');
  await expect(child.getByRole('button', { name: 'Child last', exact: true })).toBeFocused();
  await page.keyboard.press('Tab');
  await expect(close).toBeFocused();
  await page.keyboard.press('Shift+Tab');
  await expect(child.getByRole('button', { name: 'Child last', exact: true })).toBeFocused();
  await page.keyboard.press('Escape');
  await expect(child).toHaveCount(0);
  await expect(parent).toBeVisible();
  await expect(opener).toBeFocused();
  await page.keyboard.press('Tab');
  await expect(parent.getByRole('button', { name: 'Parent last', exact: true })).toBeFocused();
  await page.keyboard.press('Tab');
  await expect(parent.getByRole('button', { name: 'Parent workspace 닫기', exact: true })).toBeFocused();
  await evidence.screenshot(page, 'nested-workspace-parent-focus');
  evidence.note('nested_workspace_keyboard', { real_react_portals: true, child_interior_tab: true, child_wrap_both_directions: true, escape_keeps_parent: true, restores_parent_opener: true, production_flow_acceptance: false });
});

// Production components, dictionary, Radix and renderer CSS in a controlled composition.
// This is not a claim that a particular application route currently nests this help here.
test('real terminology popover keeps keyboard ownership inside a workspace modal', async ({ page, renderer, rendererBuild, evidence }) => {
  const fs = require('node:fs');
  const { buildSync } = require('esbuild');
  const compiled = buildSync({
    stdin: {
      contents: `import React, {useState} from 'react';
        import {createRoot} from 'react-dom/client';
        import {WorkspaceDialog} from './src/renderer/components/common/WorkspaceDialog';
        import {JargonTooltip} from './src/renderer/components/common/JargonTooltip';
        import {useProjectStore} from './src/renderer/stores/useProjectStore';
        useProjectStore.setState({language:'ko'});
        function Fixture(){const [open,setOpen]=useState(false);return <>
          <button className="workspace-button" onClick={()=>setOpen(true)}>Open terminology workspace</button>
          {open&&<WorkspaceDialog title="Terminology workspace" onClose={()=>setOpen(false)}>
            <p>실제 용어 도움말과 작업창의 키보드 조합 확인</p>
            <JargonTooltip termKey="learning_rate">학습률</JargonTooltip>
          </WorkspaceDialog>}
        </>;}createRoot(document.getElementById('root')).render(<Fixture/>);`,
      loader: 'tsx', resolveDir: process.cwd(), sourcefile: 'workspace-terminology-keyboard-fixture.tsx',
    },
    bundle: true, write: false, format: 'iife', platform: 'browser', jsx: 'automatic',
    define: { 'process.env.NODE_ENV': '"production"' },
  }).outputFiles[0].text;
  const fixtureUrl = `${renderer.origin}/__workspace_terminology_keyboard__`;
  await page.route(fixtureUrl, route => route.fulfill({ contentType: 'text/html', body: '<!doctype html><html lang="ko" data-theme="dark"><body><div id="root"></div></body></html>' }));
  await page.goto(fixtureUrl);
  const styleLinks = [...fs.readFileSync(rendererBuild.index.path, 'utf8').matchAll(/<link[^>]+href="([^"]+\.css)"[^>]*>/g)].map(match => match[1]);
  expect(styleLinks.length).toBeGreaterThan(0);
  for (const href of styleLinks) await page.addStyleTag({ url: new URL(href, renderer.origin).href });
  const apiRequests: string[] = [];
  page.on('request', request => { if (new URL(request.url()).pathname.startsWith('/api/')) apiRequests.push(request.url()); });
  await page.addScriptTag({ content: compiled });
  const launcher = page.getByRole('button', { name: 'Open terminology workspace', exact: true });
  await launcher.focus();
  await page.keyboard.press('Enter');
  const parent = page.getByRole('dialog', { name: 'Terminology workspace', exact: true });
  const closeParent = parent.getByRole('button', { name: 'Terminology workspace 닫기', exact: true });
  const help = parent.getByRole('button', { name: '용어 설명: 인공지능 학습 보폭 (학습률)', exact: true });
  await expect(closeParent).toBeFocused();
  await page.keyboard.press('Tab');
  await expect(help).toBeFocused();
  await page.keyboard.press('Enter');
  const popup = parent.getByRole('dialog', { name: /인공지능 학습 보폭.*용어 설명/ });
  const closeHelp = popup.getByRole('button', { name: '용어 설명 닫기', exact: true });
  await expect(popup).toBeVisible();
  await expect(popup).not.toHaveAttribute('aria-modal', 'true');
  await expect(popup).toContainText('한 번 학습할 때 모델의 값을 얼마나 크게 바꿀지 정하는 수치입니다.');
  await expect(closeHelp).toBeFocused();
  // This rich help has one interactive control. Radix's real looping focus scope
  // keeps either Tab direction on Close until the person dismisses the help.
  await page.keyboard.press('Tab');
  await expect(closeHelp).toBeFocused();
  await expect(popup).toBeVisible();
  await page.keyboard.press('Shift+Tab');
  await expect(closeHelp).toBeFocused();
  await expect.poll(() => parent.evaluate(node => node.contains(document.activeElement))).toBe(true);
  await evidence.screenshot(page, 'workspace-real-terminology-keyboard');
  await page.keyboard.press('Escape');
  await expect(popup).toHaveCount(0);
  await expect(parent).toBeVisible();
  await expect(help).toBeFocused();
  await page.keyboard.press('Tab');
  await expect(closeParent).toBeFocused();
  await page.keyboard.press('Shift+Tab');
  await expect(help).toBeFocused();
  await page.keyboard.press('Escape');
  await expect(parent).toHaveCount(0);
  await expect(launcher).toBeFocused();
  expect(apiRequests).toEqual([]);
  evidence.note('workspace_real_terminology_keyboard', { production_components: ['WorkspaceDialog', 'JargonTooltip'], real_dictionary: 'learning_rate', real_radix: true, production_renderer_css: styleLinks, keyboard_open: true, help_tab_containment_both_directions: true, help_escape_keeps_parent: true, trigger_focus_restored: true, parent_wrap_both_directions: true, next_escape_closes_parent: true, api_requests: apiRequests, production_route_acceptance: false });
});
