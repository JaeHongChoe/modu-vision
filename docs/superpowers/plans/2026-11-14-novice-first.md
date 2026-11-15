# Novice-first implementation plan

Spec: docs/superpowers/specs/2026-11-14-novice-first-design.md
Base: 332adfcce7d86953d92aaf69fc009dad69cf6ed5 (existing OSS integration draft).
Branch: feat/novice-first-20261114; existing isolated studio-cli-20261114 worktree.

## Global constraints
Preserve existing task contracts, authorization, source/label/revision identity, leases, receipts, fixed test split, running operations and all user data. No new job runner. Use semantic workspace styles and accessible native controls. Code/test ownership below is exclusive; no commits from workers. Do not run actual model/GPT/user-data actions for UI testing.

## Task 1 — Purpose-first start (worker)
Own ProjectWorkspaceDialog.tsx and its tests, FirstRunStudio.tsx and its tests, and new purpose selector/helper under onboarding. In simple view replace technical task select with four explicit result choices and examples/data requirements. Preserve full mode task selection and all create guards. Do not assume a default choice for novices; unclear selection offers explanatory help. Map to existing VisionTask only when explicitly chosen. First run shows three working paths (existing demo, new project, existing project) using existing workspace opening events/props. Keep onboarding dismissal/demo rules and guards intact. Test behavior before implementation.

## Task 2 — Beginner navigation and honest actions (worker)
Own WizardHeader.tsx, WizardFooter.tsx, a new beginnerJourney helper/tests, corresponding component tests. Simple header shows four goal groups mapping [1], [2,3], [4], [5,6], with internal substep navigation reachable and existing completed-state evidence preserved; full header unchanged. Do not add backend reads or change setStep guards. Footer primary navigation labels explicitly say open/check (never claim to execute training). Expose durable current-task progress via existing task store or shared component, do not create another scheduler. Root owns any separate task presentation component and AutoProgressDialog integration; coordinate a seam. Preserve keyboard/ARIA and narrow-width layout. Test mapping and navigation behavior.

## Task 3 — Learning preparation (worker)
Own TrainingPreparationPanel.tsx, RecipePresetSelector.tsx, sampleConfidence.ts and focused tests; inspect TrainingController but coordinate before touching. Simple mode: concrete input problem and one recovery action, details collapsed, no initial SHA/pip jargon. Preserve source checks and blocking conditions. Replace inaccurate longer=better and sample-count trust promises with limited, honest evidence language. Reuse current styles. Test meaningful state behavior; no mirrored source-text-only tests.

## Task 4 — Integration and verification (root)
Read shared execution store to connect one visible current-task card with existing oneclick and assistant tasks. Clarify pause/recovery text without changing execution semantics. Update design contract and user guide for new path. Run meaningful targeted tests, typecheck/build, renderer suite; preserve baseline failures by exact names. Visually verify isolated synthetic UI if available. Independent whole diff review, fix material findings, save results and publish reviewable branch/PR when ready. No merge/live app replacement claim.
