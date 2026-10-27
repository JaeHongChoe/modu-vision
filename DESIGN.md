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
    fontFamily: '"Pretendard Variable", Pretendard, Inter, "Apple SD Gothic Neo", "Malgun Gothic", "Noto Sans KR", sans-serif'
  mono:
    fontFamily: '"JetBrains Mono", ui-monospace, "SFMono-Regular", Menlo, Monaco, Consolas, monospace'
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
components:
  button: {}
  section: {}
  badge: {}
  dialog: {}
  assistant-task: {}
---

# Vision AI Studio Design System

## Overview

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

### Token ownership/runtime mapping

The existing runtime is canonical (Model B). This file documents accepted shared values; it does not generate CSS. Change the runtime owner and update this document together for a future system decision.

| Role | Canonical owner | Runtime consumers / adapter |
| --- | --- | --- |
| Workspace surfaces, text, border, focus and status colors | `src/renderer/styles/design-tokens.css`, `--workspace-*` | `workspace-section`, `workspace-record`, `workspace-badge`, `WorkspaceDialog`, assistant task card |
| Workspace spacing, type sizes and container radius | `src/renderer/styles/design-tokens.css`, `--workspace-space-*`, `--workspace-font-*`, `--workspace-radius` | Shared workspace CSS and token-consuming feature markup |
| Button default/hover/primary recipes | `.workspace-button` and `.workspace-button--primary` in `design-tokens.css` | Native buttons with shared classes; do not recreate the recipe locally |
| Light theme | `src/renderer/styles/theme-light.css`, `html[data-theme="light"]` | Overrides workspace semantic variables and shared button/badge recipes |
| Font, type floor, global focus, Korean wrapping, reduced motion and scrollbar baseline | `src/renderer/index.css` | Application document and every new scroll region |
| Existing studio palette and utility aliases | `src/renderer/index.css`, `tailwind.config.js` | Legacy studio surfaces; preserve their established theme adapter |

Drift review compares these owners with this file and the shared components. A feature must use semantic workspace variables and shared button classes rather than duplicate hex values. No export/generation pipeline or `designmd` executable is currently configured in package scripts.

## Colors

The dark palette uses layered blue steel: background `#101722`, surface `#182332`, inset `#0d141e`, border `#405166`, text `#edf2f8` and muted text `#afbdce`. Accent `#9cddff` identifies the active step; focus `#7dd3fc` identifies keyboard focus. Success, warning and danger use their semantic workspace variables and always have a written state name.

The light adapter maps background/surface/inset to `#F8FAFC` / `#FFFFFF` / `#F1F5F9`, text/muted to `#0F172A` / `#475569`, accent/focus to `#0369A1` / `#0284C7`, border to `#CBD5E1`, and success/warning/danger to `#047857` / `#B45309` / `#B91C1C`. Use variable references so both themes inherit these mappings.

## Typography

Use the bundled Korean-capable Pretendard Variable face from `index.css` for headings, controls and body copy. The established hierarchy uses weight and spacing within that family. Technical records may use the existing mono stack; they remain selectable.

Workspace body is `.875rem` (14px at the normal root), secondary copy `.8125rem` (13px), and workspace dialog title `1.125rem`. Task headings use the existing `workspace-section h3` 1rem recipe. Do not add a text size below 13px to the assistant. Existing global rules enlarge type on wide monitors and preserve tabular digits. Korean prose keeps words together; exceptional long identifiers may wrap anywhere.

## Layout

Use the implemented `.25rem`, `.5rem`, `.75rem`, `1rem`, `1.5rem` spacing scale. The assistant lives within the existing `26rem` sidebar with `max-width: 100%`; a task card has no fixed width and permits wrapped buttons and identifiers. Preserve the parent panel's scrolling and footer geometry.

The task list shows at most the contract's 24 steps in a bounded `20rem` scroll area with a stable scrollbar gutter. Current-step information remains outside that scroll area. A malformed oversized plan cannot be approved from a partial preview. Loading keeps the card content and action position present; loading text belongs in its reserved status area. No synthetic percentage is displayed.

## Elevation & Depth

Separate work surfaces with tone and a one-pixel border. A task card stays in the sidebar's normal flow. The existing sidebar owns its shadow; dialogs use the shared workspace overlay and dialog shadow. Do not add a floating task overlay or a second card shadow.

## Shapes

Shared section/dialog radius is `.625rem`, controls `.4rem`, and records `.5rem`. Badges use the existing pill recipe. The current-step rail is a two-pixel semantic accent; it encodes sequence position rather than decoration.

## Components

### Foundational visual states

Written status distinguishes planned, running, waiting for a person, waiting for a job, paused, uncertain, failed and completed. Badges support that text with semantic tones. Inline errors persist beside the affected work. Loading uses plain status text and `aria-busy`; the card introduces no animated loader or skeleton.

### Buttons and actions

Every task-card button uses `workspace-button`. Exactly one action in a card uses `workspace-button--primary`; the parent assistant coordinates this with its composer and other pending actions. Use the normal `2.25rem` minimum height, visible focus ring, wrapped labels and shared disabled recipe. A disabled handler must also reject an invoked callback. Pause is a safe workflow action and never borrows danger styling from training cancellation.

### Navigation and data display

Plans are native ordered lists. The current step has `aria-current="step"`; status changes use a compact polite live region. Saved results are small definition lists of allowlisted identifiers, counts and actual training settings. Never render full prediction arrays, images, file paths, hashes, binding strings or protocol objects as result copy. Goal and step labels remain React text.

### Forms and overlays

`WorkspaceDialog` is the canonical existing workspace dialog primitive; it owns focus entry, Tab containment, Escape and focus restoration. The assistant is a persistent non-modal sidebar and must not trap focus. The task card creates no form, select, native alert, confirmation or dialog.

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

### Step 5 guided preparation

The simple view starts with an ordered inspection preparation surface: connected models, judgment rules, saved flow, saved-version evaluation and the next inspection step. `FlowPreparationPanel` uses the same workspace records, buttons, semantic text/status tokens and native focus behavior as the task card. Technical job IDs live behind a closed disclosure; numeric score/count criteria remain readable in the main summary. The full graph is retained as advanced editing, with the existing model and rule owners.

Switching views changes presentation only and preserves the mounted graph, unsaved edits and stored evidence. Returning focuses the preparation section; entering advanced editing focuses its edit tab. Busy preparation blocks mutations while a separate read-only status action preserves access to an ongoing inspection's recovery controls. The simple evaluation surface uses plain 시험 이미지 wording and explains why model evaluation and complete-flow evaluation differ. This is not a new palette, global layout migration or approval policy.

## Migration notes

This contract documents the existing runtime and the assistant slice. It is not a whole-application accessibility or visual certification. The legacy scrollbar baseline is WebKit-specific and has hard-coded colors; standards-based scrollbar properties and a complete forced-colors scrollbar recipe remain a global follow-up outside this feature. Legacy studio utility colors and generated light-theme rules remain owned by their existing files; do not spread those duplicates into new workspace components.
