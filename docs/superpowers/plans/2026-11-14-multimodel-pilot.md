# Multi-model pilot implementation plan

Goal: integrated, reviewable pilot candidate preserving all supported model families while improving novice data/model guidance and terminology accessibility.
Architecture: existing capability API and readiness owners; narrowly adopted Radix Popover and dev-only axe; existing isolated Playwright fixtures.
Tech stack: React 18, TypeScript, Electron, Python backend, Playwright, existing node:test.
Spec: docs/superpowers/specs/2026-11-14-multimodel-pilot-design.md

## Global constraints
- Preserve all model families and existing quality, authorization, source, split and deployment guards.
- Never manufacture field acceptance, human truth, actual-model receipts or success from mock tests.
- Preserve installed app/web, user data, other worktrees and original branches. No live GPT/image requests.
- Source evidence hashes may change only for changed files; retain historic records.
- Changes must fit DESIGN.md and UX-CONTRACT.md, use plain Korean actions, and never auto-start from selection/navigation.

## Review focus
1. Source photos present with zero trainable labels are not called absent.
2. Specialist/generation-only and missing dependency states are visible and truthful.
3. Keyboard help works inside existing modal/focus layers without starting a parent action.
4. Original polygon and evidence histories survive integration.
5. Automated/synthetic checks are distinguished from per-model and human qualification.

## Task 1: Integrate existing branches
- [x] Create isolated candidate in current linked worktree; merge PR26 into PR29 ancestry, preserve both document sections and pins.
- [x] Verify provenance and types; commit integration.

## Task 2: Multi-model guidance and imported-photo recovery
Files: ModelFamilyCatalog.tsx and focused tests; dataset/importChecklist.ts and tests; DatasetStudio.tsx call site. Do not edit package files or evidence JSON.
- [x] Add failing behavior tests for source count/zero labels and per-family visible summary including missing dependencies, generation-only and failed capability load.
- [x] Implement concise summary from existing capabilities, preserve all family selection and execution guards.
- [x] Distinguish photos imported from trainable labels. Keep split/class restrictions, show only appropriate next actions.
- [x] Run focused tests and report changed files and limitations.

## Task 3: Accessible terminology and pilot browser checks
- [x] Adopt pinned Radix Popover for JargonTooltip; retain content/locale and inspect all callers for nested interactive controls.
- [x] Add keyboard/focus and axe checks to isolated beginner fixture. Fix introduced/relevant violations; no broad exclusions.
- [x] Verify compact/desktop light/dark screenshots and shared focus behavior as applicable.

## Task 4: Pilot delivery and final candidate verification
- [x] Update manual/pilot matrix and blank study record across all catalog families, with exact verified/unverified boundaries.
- [x] Run integrated renderer/types/provenance/evidence checks plus focused browser paths and independent review.
- [x] Publish candidate branch/draft PR #30; original branches preserved.
- [ ] Start exact-head full once after fast success; record run ids. First fast failed fixture integration checks; repaired locally and pending new-head CI.
- [x] Inspect isolated package feasibility and execute safe candidate smoke when dependencies permit. Record actual artifact scope; do not replace live services.

## Execution boundary

Local source, UI, review and isolated native smoke completed; package feasibility work remains an isolated acceptance attempt. Read [dated evidence](../../verification/2026-11-14-multimodel-pilot.md) for failures and pending qualification. No human/field quality or installed runtime replacement is claimed.
