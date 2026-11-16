# Training preparation blocks implementation plan

Goal: put the real data and preprocessing flow before training, using Supervisely's selected-step wizard and connected preview blocks as references.

Architecture: a connected five-block rail (photos, labels, split, transforms, model) controls one visible settings panel; panels remain mounted. Existing stores, immutable input recipe previews, task-specific workbenches, source custody, split policies, model binding, and start guards retain authority. This is a real configuration surface for existing executable operations, not an arbitrary graph executor or claimed new model support.

Visual direction: preserve Modu Vision's paper/forest tokens, Pretendard, and one primary action. Reference Supervisely's whitespace, clear step names, current-step panel, input/output preview. No copied platform frontend, brand assets, or unlicensed application source. Platform is proprietary; SDK Apache-2.0 is a distinct scope.

Tasks:
- [x] Add TrainingDataWorkflow accessible connected-block navigation with preserved mounted editors, scope resets, real readiness text and responsive layout; behavior tests.
- [x] Add TrainingDataSplitBlock using canonical applySplit, explicit edits to saved/source-folder partitions, scope and busy fences; behavior tests.
- [x] Refactor TrainingInputPreparation into operation blocks + focused settings, retain before/after preview qualification and save/conflict guards; meaningful tests.
- [x] Integrate preparation first in TrainingController; model/catalog and readiness are the last block; specialist workbenches remain available. Preserve all start/recovery controls and existing model families.
- [x] Test recipe drafts vs saved inputs, unsupported-family truth, empty/stale source, late completion after project/account changes, editor continuity, keyboard behavior, and current-job recovery visibility.
- [x] Validate renderer/types/build, focused synthetic browser interaction and small/narrow layouts; independent code review; update DESIGN and user docs.
- [x] Commit feature on feat/training-data-blocks-20261116 and publish separate PR; distinguish source verification from installed/runtime rollout and pending CI.

Existing PR32 is a separate defect fix. Its full source succeeded but browser preflight source pins failed; minimum pin repair is being tested before branching. No existing CI is rerun or cancelled. No production service restart or data mutation is part of this implementation.


## REA-informed follow-up (2026-11-16)

The user's next direction additionally authorized a compatible isolated Node and actual REA use. Public page captures and official embedded reference screenshots informed the persistent left-step wizard, task choice cards, scoped recovery reveal, and selected-prefix preprocessing previews. See [the actual observation and implementation report](../../verification/2026-11-16-rea-supervisely.md). Core next-run settings moved inside the model pane; current runtime controls remain outside. Final local validation:3443PASS/2existingoptinSKIP, types/build and real synthetic browser flow pass. Remote CI and runtime rollout remain separate qualification stages.
