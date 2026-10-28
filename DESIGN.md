---
version: alpha
name: "Vision AI Studio"
description: "Korean inspection workspace with quiet steel surfaces, readable controls and explicit evidence."
colors:
  primary: "#0f4c6b"
  background: "#101722"
  surface: "#182332"
  inset: "#0d141e"
  border: "#405166"
  text: "#edf2f8"
  muted: "#afbdce"
  accent: "#9cddff"
  focus: "#7dd3fc"
  success: "#9ce5bc"
  warning: "#ffd28a"
  danger: "#ffb5bd"
typography:
  sans:
    fontFamily: '"Pretendard Variable", Pretendard, Inter, "Apple SD Gothic Neo", "Malgun Gothic", "Noto Sans KR", -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif'
  mono:
    fontFamily: '"JetBrains Mono", ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace'
rounded:
  DEFAULT: "0.625rem"
  control: "0.4rem"
  record: "0.5rem"
spacing:
  quarter: "0.25rem"
  half: "0.5rem"
  group: "0.75rem"
  section: "1rem"
  panel: "1.5rem"
  dialog-max: "72rem"
components:
  button: {}
  section: {}
  badge: {}
  dialog: {}
  assistant-task: {}
  WorkspaceDialog:
    rounded: "0.625rem"
    width: "72rem"
  ProjectWorkspaceDialog: {}
  SharedProjectPanel: {}
---

# Vision AI Studio Design System

## Overview

This as-built context combines the existing assistant task slice with the shared
workspace and project-readiness context. It introduces no rebrand, runtime token,
permission policy, or whole-product acceptance claim. The application name is
**Vision AI Studio**; repository documentation also uses **Modu Vision**. The
six-step navigation remains the studio's operational signature: data preparation,
labeling, training, evaluation, inspection-flow construction and inspection.

### Creative North Star

An inspection workbench: the source image and the person's decision take priority; controls explain the current operation, its evidence and its next step. The assistant's ordered plan is a real execution sequence. Its current-step rail is the only additional visual emphasis.

### Product context and register

- Audience and primary job: Korean-speaking people who prepare images, review labels, train a model and inspect results without writing code.
- Market evidence: the maintained product workflow and Korean interface establish language and inspection use. They do not establish a country-specific legal or commercial scope.
- Language: plain Korean actions; preserve familiar model/device names and saved record identifiers where they help investigation.
- Usage scene: desktop Electron and local browser workspaces, with dense tools and a persistent assistant sidebar.
- Register: product workspace. Follow the existing shared surfaces and controls; this feature introduces no new brand palette, font or animation.
- Restraint: no decorative dashboard statistics, completion estimates, gradients or oversized promotional headings in a task card.
- Anti-references: a terminal transcript that exposes protocol fields, and a chat bubble that claims success without saved results.

### Existing locale, platform and workflow context

The implemented language type is `ko | en`, owned by
`src/renderer/types/index.ts` and `useProjectStore`; `WizardHeader` exposes the
language switch. Korean is dominant and some screens retain Korean literals.
The switch does not establish complete English localization or Japanese-language
acceptance.

`README.md` and
`docs/superpowers/specs/2026-10-27-local-web-desktop-parity-design.md` describe the
workflow and local-browser design. `docs/ux-beginner-pass.md` is a historical UX
ledger; statements predating native browser capabilities are not current platform
policy. Verified API/store guards remain the authority for data operations.

The local browser runner uses HTTPS on localhost, local accounts, and a headless
native helper; this is not a LAN hosting contract. Per `docs/local-browser.md`, the
Windows web runner is blocked pending private-key ACL validation and Linux runtime
execution is unverified. Desktop packages need their own target acceptance under
`docs/release-platform-matrix.md`. Source/build checks, actual execution,
signing/installation, model quality and human acceptance are separate. Observed
macOS/Chromium checks do not establish phone or all-OS support.

### Token ownership/runtime mapping

The existing runtime is canonical (Model B). This file documents accepted shared values; it does not generate CSS. Change the runtime owner and update this document together for a future system decision.

| Role | Canonical owner | Runtime consumers / adapter |
| --- | --- | --- |
| Workspace surfaces, text, border, focus and status colors | `src/renderer/styles/design-tokens.css`, `--workspace-*` | `workspace-section`, `workspace-record`, `workspace-badge`, `WorkspaceDialog`, assistant task card |
| Workspace spacing, type sizes and container radius | `src/renderer/styles/design-tokens.css`, `--workspace-space-*`, `--workspace-font-*`, `--workspace-radius` | Shared workspace CSS and token-consuming feature markup |
| Button default/hover/primary recipes | `.workspace-button` and `.workspace-button--primary` in `design-tokens.css` | Native buttons with shared classes; do not recreate the recipe locally |
| Light theme | `src/renderer/styles/theme-light.css`, generated by `scripts/generate_light_theme.cjs`; `html[data-theme="light"]` | Overrides workspace semantic variables and shared button/badge recipes; regenerate rather than hand-edit |
| Font, type floor, global focus, Korean wrapping, reduced motion and scrollbar baseline | `src/renderer/index.css` | Application document and every new scroll region |
| Existing studio palette and utility aliases | `src/renderer/index.css`, `tailwind.config.js` | Legacy studio surfaces; preserve their established theme adapter |

Drift review compares these owners with this file and the shared components. A feature must use semantic workspace variables and shared button classes rather than duplicate hex values. No export/generation pipeline or `designmd` executable is currently configured in package scripts.

## Colors

The dark palette uses layered blue steel: background `#101722`, surface `#182332`, inset `#0d141e`, border `#405166`, text `#edf2f8` and muted text `#afbdce`. Accent `#9cddff` identifies the active step; focus `#7dd3fc` identifies keyboard focus. Success, warning and danger use their semantic workspace variables and always have a written state name.

The light adapter maps background/surface/inset to `#F8FAFC` / `#FFFFFF` / `#F1F5F9`, text/muted to `#0F172A` / `#475569`, accent/focus to `#0369A1` / `#0284C7`, border to `#CBD5E1`, and success/warning/danger to `#047857` / `#B45309` / `#B91C1C`. Use variable references so both themes inherit these mappings.

### Studio chassis palette

Frontmatter colors name the workspace palette above. The separate existing
studio chassis and Tailwind aliases remain owned by `index.css` and
`tailwind.config.js`; their status LEDs are not interchangeable with the softer
workspace status-text colors.

| Existing role | Dark runtime value | Source / meaning |
| --- | --- | --- |
| Chassis / panel / card | `#0B0E14` / `#131822` / `#1A212E` | `index.css`, studio surfaces |
| Border / hover / active | `#2B3547` / `#222B3D` / `#2A364D` | `index.css`, separation and interaction |
| Body text / metric readout | `#E2E8F0` / `#F8FAFC` | Body copy and instrument values |
| Metric dim / unit | `#94A3B8` / `#64748B` | Secondary instrument hierarchy |
| Pass / fail / standby | `#10B981` / `#EF4444` / `#F59E0B` | Annunciator states with written status |
| Running / offline | `#3B82F6` / `#4B5563` | Annunciator states with written status |

The generated light adapter maps chassis to `#F8FAFC`, surface/card to `#FFFFFF`,
border to `#CBD5E1` and metric readout to `#0F172A`. Existing screen-specific
literals remain; this document does not certify all contrast or theme coverage.

## Typography

Use the bundled Korean-capable Pretendard Variable face from `index.css` for headings, controls and body copy. The established hierarchy uses weight and spacing within that family. Technical records may use the existing mono stack; they remain selectable.

Workspace body is `.875rem` (14px at the normal root), secondary copy `.8125rem` (13px), and workspace dialog title `1.125rem`. Task headings use the existing `workspace-section h3` 1rem recipe. Do not add a text size below 13px to the assistant. Existing global rules enlarge type on wide monitors and preserve tabular digits. Korean prose keeps words together; exceptional long identifiers may wrap anywhere.

The frontmatter sans stack mirrors the full body declaration in `index.css`.
Tailwind's `font-sans` utility is separately declared in `tailwind.config.js` and
has its own shorter fallback list; it is not generated from this document. The
frontmatter mono stack mirrors that config's `font-mono` utility and does not
mean every listed face is bundled. Existing global CSS floors older tiny console
classes at 12px, sets `text-xs` to 13px/20px, and raises small simple-view text to
13px; the assistant's existing 13px minimum above remains its specific contract.
`product-workbench` uses 14px/1.5. Large-monitor root sizing changes at 1920px and
2400px. Declarations alone are not zoom, fallback-font or localization acceptance.

## Layout

Use the implemented `.25rem`, `.5rem`, `.75rem`, `1rem`, `1.5rem` spacing scale. The assistant lives within the existing `26rem` sidebar with `max-width: 100%`; a task card has no fixed width and permits wrapped buttons and identifiers. Preserve the parent panel's scrolling and footer geometry.

The task list shows at most the contract's 24 steps in a bounded `20rem` scroll area with a stable scrollbar gutter. Current-step information remains outside that scroll area. A malformed oversized plan cannot be approved from a partial preview. Loading keeps the card content and action position present; loading text belongs in its reserved status area. No synthetic percentage is displayed.

The application shell owns the viewport while content panels own scrolling.
Shared workspace dialogs cap width at 72rem and height at `100dvh - 2rem`, keep
their header fixed, and scroll their body. Their auto-fit grid uses a 22rem
minimum; the existing 600px breakpoint reduces dialog padding. The workspace
spacing scale above does not claim every legacy screen follows it.

Step 2 has a compact toolbar below 1439px width or 820px height. Preserve the
panel's established scroll owner when changing state; a table's sizing does not
establish a sibling form's layout. This adaptation does not establish phone
support.

## Elevation & Depth

Separate work surfaces with tone and a one-pixel border. A task card stays in the sidebar's normal flow. The existing sidebar owns its shadow; dialogs use the shared workspace overlay and dialog shadow. Do not add a floating task overlay or a second card shadow.

Workspace overlays retain their dark scrim and `0 24px 80px #0008` shadow.
Nested `WorkspaceDialog` instances derive their layer above their opener without
introducing another global error/modal layer.

## Shapes

Shared section/dialog radius is `.625rem`, controls `.4rem`, and records `.5rem`. Badges use the existing pill recipe. The current-step rail is a two-pixel semantic accent; it encodes sequence position rather than decoration.

## Components

### Canonical project-readiness behavior

| Capability | Canonical owner | Source of truth | Allowed variants | Verification |
| --- | --- | --- | --- | --- |
| Project-changing controls | `projectSwitchRefusal` and `subscribeProjectSwitchRefusal` in `useProjectStore.ts` | Existing running-operation/save guards, also called by `saveOpenEdits` | `ProjectWorkspaceDialog` and `SharedProjectPanel` subscribe to the same predicate; initial `localAuthenticationOnly` login retains its existing exception | Deterministic component transitions and the original F064 browser A/B/A fixture; local evidence is distinct from hosted qualification |
| Select/Listbox | Existing native select in `SharedProjectPanel` | Platform-owned popup and account/project selection handler | Native popup; no authored popup geometry or all-platform acceptance asserted | Existing selection/authority tests and browser scenario |
| Workspace overlay | `WorkspaceDialog.tsx` | Existing focus entry/restoration, Tab boundary, Escape and portal implementation | Nested workspace layer above opener; assistant sidebar remains non-modal | Existing workspace tests; no universal legacy-dialog certification |
| Scrollbar | Global `index.css` rules | Existing WebKit track/thumb/hover baseline | Component-owned geometry may differ | Static inspection; standards-based completeness remains a gap |

The refusal predicate covers model-assist operations, batch inspection, dataset
load/split/generation, training and flow run/load/save. Shared-project controls
retain local busy and project-busy checks, and request-time guards still apply
if state changes after rendering. Enabled appearance grants no permission:
server/account/project, registration, workspace, actor and transport checks remain
authoritative. Initial `localAuthenticationOnly` login preserves its existing
exception from inherited busy state; this does not exempt project switching or
saving. Shared-project failures keep their existing inline `role="alert"`.

### Foundational visual states

Written status distinguishes planned, running, waiting for a person, waiting for a job, paused, uncertain, failed and completed. Badges support that text with semantic tones. Inline errors persist beside the affected work. Loading uses plain status text and `aria-busy`; the card introduces no animated loader or skeleton.

### Buttons and actions

Every task-card button uses `workspace-button`. Exactly one action in a card uses `workspace-button--primary`; the parent assistant coordinates this with its composer and other pending actions. Use the normal `2.25rem` minimum height, visible focus ring, wrapped labels and shared disabled recipe. A disabled handler must also reject an invoked callback. Pause is a safe workflow action and never borrows danger styling from training cancellation.

### Navigation and data display

Plans are native ordered lists. The current step has `aria-current="step"`; status changes use a compact polite live region. Saved results are small definition lists of allowlisted identifiers, counts and actual training settings. Never render full prediction arrays, images, file paths, hashes, binding strings or protocol objects as result copy. Goal and step labels remain React text.

The current executor is limited to the 12 actions in
`src/renderer/services/assistantTasks/catalog.ts`; labels and model text do not
create additional execution authority. Its existing validator permits 1–24 steps
and rejects oversized plans. This records the current catalog, not a new UI or
permission policy.

### Step 5 guided preparation

The simple view starts with an ordered inspection preparation surface: connected models, judgment rules, saved flow, saved-version evaluation and the next inspection step. `FlowPreparationPanel` uses the same workspace records, buttons, semantic text/status tokens and native focus behavior as the task card. Technical job IDs live behind a closed disclosure; numeric score/count criteria remain readable in the main summary. The full graph is retained as advanced editing, with the existing model and rule owners.

Switching views changes presentation only and preserves the mounted graph, unsaved edits and stored evidence. Returning focuses the preparation section; entering advanced editing focuses its edit tab. Busy preparation blocks mutations while a separate read-only status action preserves access to an ongoing inspection's recovery controls. The simple evaluation surface uses plain 시험 이미지 wording and explains why model evaluation and complete-flow evaluation differ. This is not a new palette, global layout migration or approval policy.

### Forms and overlays

`WorkspaceDialog` is the canonical existing workspace dialog primitive; it owns focus entry, Tab containment, Escape and focus restoration. The assistant is a persistent non-modal sidebar and must not trap focus. The task card creates no form, select, native alert, confirmation or dialog.

Other workspace views retain their existing fields, password masking, account
scope, focus outline, hover and `not-allowed` disabled treatment. Existing
operational path displays outside the task card do not broaden the assistant's
allowlisted result-copy contract. This merge changes no confirmation, credential,
role, native-dialog or legacy form behavior.

### Iconography

The existing application uses Lucide React. Task actions retain text labels and do not require new icons. An icon alone must not carry task state or action meaning.

### Motion

Keep existing tactile color transitions (75–100ms). The task card adds no motion. Global `prefers-reduced-motion` rules remain authoritative.

### Content and data visualization

Use verbs matching the actual operation: plan confirmation, saved-result check, review screen, continue, pause. “완료” means the plan's recorded actions completed; it does not mean model quality, truth, operational approval or deployment was accepted. Counts use `ko-KR` formatting. Percentages are allowed for requested split settings, never as an invented execution estimate.

## Do's and Don'ts

- Do preserve Korean workspace language, both themes and the shared button hierarchy.
- Do place the canonical action title before a provider-written label and show the meaningful approved settings.
- Do keep the current action and recovery choice readable when the plan scrolls.
- Don't make pause look like training cancellation or imply that it cancels a job already started.
- Don't replace a saved-result check with a conversational success claim.

- Do reuse canonical project-readiness subscriptions with unchanged request-time guards and preserve the initial-login distinction.
- Do preserve original images, labels, saved flows and refusal reasons.
- Don't introduce a screen-local busy model or a wait/retry that hides a rejected operation.

## Migration notes

This contract documents the existing runtime and the assistant slice. It is not a whole-application accessibility or visual certification. The legacy scrollbar baseline is WebKit-specific and has hard-coded colors; standards-based scrollbar properties and a complete forced-colors scrollbar recipe remain a global follow-up outside this feature. Legacy studio utility colors and generated light-theme rules remain owned by their existing files; do not spread those duplicates into new workspace components.

The scoped project-readiness repair has local component RED/GREEN, related suites,
typecheck, renderer and original F064 fixture evidence under
`.superpowers/sdd/2026-10-28-shared-project-readiness-fix/`. Existing instructions,
provider/account participation and source-bound execution order remain unchanged.
A local pass or static audit is not hosted-regression qualification, merge/release
completion, keyboard-popup/theme/locale coverage, 200% zoom, or field acceptance.

### Local browser authentication and location reconnect

`LocalProjectGate` is the authenticated variant of `SharedProjectPanel`'s initial
browser gate. Login retains the account session while the workspace stays empty;
a person explicitly chooses a project. Only the fixed structured location 409
offers the native folder reconnect action. Other 409, 401 and 403 responses do not
authorize recovery. The existing native select is retained; the OS owns its popup.

Recovery uses the existing workspace section/button/status tokens. Native chooser
and specific app-owned confirmation identify the account, project and folder and
explain the local-session logout. Successful enrollment returns to login, then
explicit project selection; it does not mount a workspace or approve inspection
results. Interrupted recovery remains blocked until a fresh confirmation. Shared
registrations, active work and independent service metadata require their stated
review or idle state. No project files, permissions or saved operational history
are removed by this flow. Same-boot disposable tests do not establish reboot,
packaging, visual-browser or field acceptance.

After an actor has approved location recovery, the existing local tab nonce also
binds ordinary project reads and new sockets to that tab's current cookie session.
A cookie refreshed by another tab does not refresh the stale tab's memory. Native
bootstrap still needs current-session CSRF, and the proof is kept out of URLs and
persistent browser storage. This is a local recovery boundary, with no shared
server or remote context format change.

Local project acceptance prepares its distinct, memory-only image read ticket
before committing the context and rendering the workspace. The central resolver
adds this reference only to same-origin image/mask/derived reads; ordinary native
capabilities still retire on project change. An expired or unprepared ticket does
not fall back to a bare image URL. Two ZIP exports stream through a fixed POST
form in a hidden download target, keeping the ticket and CSRF value out of the
download URL. Responses disable caching and referrers, and diagnostics redact the
image reference. These controls do not qualify actual browser download behavior.

## 2026-10-29 continuous execution and input preparation

The scoped automatic mode replaces routine plan/review checkpoints for ready data and labels. Legacy/manual plans retain explicit confirmation, while policy decisions keep their own provenance instead of manufacturing human review. Required inputs, failures, active jobs, explicit pauses and unknown outcomes retain distinct visible states.

Stage3 adds TrainingInputPreparation as the first training section; the existing six-stage navigation and Stage5 inspection/decision role remain. It uses workspace surfaces/buttons and native bounded number/select fields, with a real source-bound before/after preview. No new palette or UI library is introduced.

The assistant adds local single-image attachment/paste/current-image selection, thumbnail/removal, external recipient disclosure and supported-model reasoning effort. The sidebar remains nonmodal, keyboard reachable and 13px minimum. Native effort dropdown geometry is intentionally platform-owned, like existing account/model controls. No unsupported model or API provider is silently selected.
