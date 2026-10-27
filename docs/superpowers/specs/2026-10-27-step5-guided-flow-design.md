# Step 5 guided inspection preparation

The user approved improving Step 5 after reviewing the concrete recommendation: a beginner preparation sequence with the existing graph editor available as advanced editing. This implements that direction in the current React/Electron/local-web product without replacing its workflow engine.

## Intent and approach

The default simple view shows model → judgment rules → save → test-image evaluation → inspection. It summarizes the existing graph and saved evidence, exposes one next action and names the missing prerequisite. Model selection and rule changes open the existing advanced editor or recipe dialog. Basic recipe adoption continues to use its current explicit confirmation and source verification; no graph is silently replaced.

Alternatives considered: merely folding more graph controls retains the original learning burden; rebuilding a new execution engine duplicates evidence and authorization. The selected approach adds a presentation layer over the existing save, recipe, evaluation and navigation paths. Full view remains the advanced editor.

## Behavior

- Simple view starts with a Korean preparation panel. Advanced edit is explicitly reachable and returning preserves graph/selection/unsaved edits. Opening either view never saves, evaluates, approves or calls GPT.
- Show linked model labels/task, judgment rules, draft/saved status and current saved-version evaluation status. Derive the latter from existing exact version + graph hash + validity checks. A single-image run is never full-flow evaluation evidence.
- If no bound model, offer the current single-model recipe dialog; an invalid/custom graph routes to advanced editing rather than replacing it. Saving invokes the existing verification and reason dialog.
- The primary follows the earliest prerequisite. Busy/loading states prevent mutation, and current saved evaluation offers navigation to Step 6 without implying human approval or deployment. Model-quality acceptance and all backend/export guards remain unchanged.
- Evaluation explains Step 4 evaluates the model while Step 5 evaluates the saved complete inspection flow. Simple-mode actions use 시험 이미지 rather than 코호트. Advanced pickers and history remain reachable.
- Project/source/task changes clear view-local advanced selection. Existing model selection, current graph, version receipts and errors retain their canonical owners.

## Visual and delivery constraints

Reuse DESIGN.md workspace tokens, shared native buttons, Korean typography and light/dark adapters; no dependencies, new color system, provider calls or protocol changes. Ordered stages use written state and aria-current. One primary per visible preparation/evaluation surface, visible disabled reasons and no fixed card width.

Work in the existing isolated checkout on feat/step5-guided-flow-20261027, stacked on PR19 e6f5fa5e. Preserve PR19 and all user data/private QA. Controlled tests and browser/Electron UI use synthetic data only. Do not restart installed app, touch real localhost servers, launch model training or revive automation. Publish a separate draft PR after verification; merge/install are separate outcomes.
