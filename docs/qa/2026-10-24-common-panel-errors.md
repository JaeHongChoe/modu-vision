# Primary workflow shared-error follow-up — 2026-10-24

## Problem and scope

The original product direction (§2.3, §4.1 and §5.4) says a person and the optional assistant should read the same current screen errors. A handoff/design audit found primary errors in DatasetStudio, TrainingController, PhraseDraftCreate, ModelAssistPanel and BatchInspectionPanel only appeared inside those panels. The shared footer and `read_error_details` could not read them.

The follow-up connects those existing displayed errors to the existing owned `registerPanelError` registry. It does not add a provider, automatically run work, accept drafts, or change data fingerprints. Specialist child workbenches are outside this five-panel change.

- Dataset import/source-save/split/open errors, including failed imports without an accepted source authority.
- Training start/status/stop/split/saved-job, compute/configuration and resume/parent-list errors. A failed training job now recommends opening error details instead of showing a ready start action.
- Phrase setup/batch errors and the opened model-assist panel, including quick-point errors tied to the originating image context.
- Inspection count/saved-flow/run/history and the selected failed image.
- Owned registrations retire on close/unmount. Scoped local errors and request cancellation reject retired project/source/task/label-set requests, including A→B→A visits.

## Verification and corrections

Focused component tests use the production components and production shared registry. Initial tests failed because the registry was empty. Independent review also found two training edge cases: current form validation was incorrectly retired with an old scope, and a prior label-set resume request could publish its failure after navigation. Both were reproduced with failing component tests, then corrected. The reviewer also found a retained same-transport compute error disappearing after a dataset change; its error ownership now follows the connection rather than the dataset, with another RED→GREEN regression.

The first integrated renderer run was 1969 passed / 7 failed of 1976. Five failures came from pre-existing training harnesses lacking the newly imported dataset authority helper; two handoff harness failures occurred before their pending effects could settle. Those test adapters were updated to load the real scoped hook and registry while preserving their original assertions. This is not evidence of a runtime application failure.

The corrected integrated renderer suite passes **1980/1980**, with zero failures or skipped tests (40.3 seconds). Renderer/main and e2e type checks pass, and the production build passes. Existing build chunk-size and static/dynamic-import warnings remain separate from test outcomes.

## Actual browser UI

`scripts/e2e/assistant-common-errors.spec.ts`: **1 passed, 14.2 seconds** with an owned isolated backend and the built production renderer.

The test generates 20 isolated images, imports them, and controls only two same-origin split responses (HTTP 503). It verifies the primary alert and footer's `데이터 준비` source/text match, then verifies normal retry saves a real split and clears the error. It verifies leaving the step removes its error, returning restores the still unresolved current error, and creating another project clears the previous project's errors. Original image hashes remain unchanged. External requests, model/training runs and provider calls are zero.

Saved screenshots were visually read. The browser uses a desktop host shim; this is not native Electron IPC, actual credentials, human timing or field-model quality approval. Private screenshots/manifests are retained locally rather than committed with source paths.

## Handoff closure boundaries

Claude's exact §4.2 list was reconciled with current sources and prior preserved receipts: short-window workspace repair, optional SAM2 point preview and the missing DICOM dependency were implemented in earlier commits. The cross-batch fingerprint policy remains intentionally strict. ChatGPT connection code supersedes the old handoff's “unimplemented” status, but real account login, terms, paid usage and external-image consent remain user participation. Novice timing and representative field-quality acceptance also remain separate; the actual local-image diagnostic does not meet quality expectations.

This follow-up still requires its own exact-head hosted CI qualification before merge. The old head's CI is not reused for the changed source. The 15-minute monitoring automation stays paused.

The action inventory source hashes were refreshed only for the four changed registered panel sources. Historical scenario receipts and acceptance states were not rebound or promoted; the registry continues to report zero accepted complete features.
