# Training workflow implementation ledger

Date: 2026-10-01. Approved plan: `docs/superpowers/plans/2026-10-01-product-workflow-upgrade.md`, Task 1. Scope: U001–U004, U017, U019, U032. Status below is **implemented with focused contract verification**; GUI acceptance and hardware/model-quality acceptance are separate.

## Per-requirement delivery

| ID | Actual implementation | Focused evidence | Remaining acceptance |
|---|---|---|---|
| U001 | Error actions halve the actual next-request batch size, preserve the recipe preset, select and read back local transport before setting CPU, open real data distribution review or synthetic preparation. Unknown actions remain unresolved. Batch defaults match backend fast=16 / precision=8. Source switches block stale async application. The training store submits the changed batch and local device. | Frontend action contracts, actual Zustand next-submission test, backend preset readback. | Root native GUI click/readback; error actions affect the next training request, not a running job. |
| U002 | Shared preparation panel for all ten families. OCR and rotation use paginated project images with explicit human truth and split selectors. GAN consumes explicit project bbox/polygon defect labels with original-coordinate bounds. Specialist prepare actions save existing family manifests/copies and real train actions use their returned dataset paths. Basic four families use existing project annotations, saved split and immutable training version preparation. Expert TSV/path entry is collapsed. | Source-relative sample bounds, Korean OCR truth preservation, explicit GAN crop contracts; real API OCR prepare → controlled worker submit → persisted reopen with source hashes unchanged. Existing family/prepare regression. | Root GUI preparation, disk reopen and handoff for all ten families. No assertion that labels or resulting model quality are adequate. |
| U003 | TaskCenter inventories main/patch, specialized and automatic training, persisted labeling batches/features, inspection runs, saved export packages and optimization jobs. Identity retains kind, transport and original ID. Selected row persists by project/source/labelset/connection. Requests, termination acknowledgement and lease readback are distinct. Same server training reconnects by original ID. Project/transport changes immediately hide stale rows. Completed synchronous inspection/export records have no invented cancel action. | Task identity/scope/lifecycle/selection contracts; API stopping → stopped acknowledgement, source isolation and new-app disk reopen. | Root native task selection/reopen/cancel UI; physical remote disconnection or GPU lease release requires actual hardware evidence. Saved inspection/export records are read-only evidence. |
| U004 | Local readiness checks selected architecture, required imports, requested runtime and cached/local weight presence without downloads or trainer launch. Native file picker copies a weight into project storage, verifies SHA256 and restores the recorded import. Main and patch training consume saved imported paths. Missing dependencies expose setup instructions for the configured Python/Docker environment and the official PyTorch installation page. File presence explicitly does not claim content compatibility, model execution or quality approval. | Missing/cache-free readiness, import preserving source bytes, SHA restore and tamper rejection. | Root native file picker/readback. Official checkpoint compatibility is checked by actual trainer load; server runtime requires its existing server probe/preflight. |
| U017 | Candidate count, total epochs, elapsed time and memory limits validated before submission; candidate epochs cannot exceed total budget. Each request persists its own budget. Live progress includes consumed epochs, elapsed seconds and measured memory. CUDA/MPS allocation or whole-backend CPU RSS cap requests cooperative cancellation; a cap already exceeded stops before any candidate. GPU scheduler receives memory reservation amount. | Invalid budget frontend/backend contracts; real CPU 1 MB gate stops before candidate with no winner; controlled progress callback receives active elapsed/memory/budget values. Existing automatic-search regression. | Root budget GUI. Time/memory checks are cooperative, not OS hard limits; CPU RSS includes the whole backend and GPU readings are process allocations, not total device memory. No costly search was submitted. |
| U019 | Image versus region purpose selector submits validated `anomaly_mode`, records image-score versus region-mask evaluation profile and explains independent normal/defect test images or pixel-mask prerequisites. DINO patch maps are identified separately from pixel mask truth. Automatic anomaly search carries the selected purpose too. | Frontend mode payload, backend validation retention and invalid-purpose 422 before preparation; existing anomaly evaluation-evidence regression. | Root GUI purpose readback/evaluation route. Region quality needs real independent masks and model evaluation. |
| U032 | Catalog states frozen DINO encoder/head training, YOLO configured training, replica DDP with divided batches and no model sharding. AutoDL states finite declared candidate search, no guaranteed optimum and cooperative time boundaries. | Source/catalog integration plus typecheck; existing search contracts. | No DDP, remote CUDA, model-sharding, physical hardware or model-quality acceptance claimed. |

## Actual common GUI preparation routes

Start in a project with connected source images and an active labelset. Step 3 family tiles select the workbench. “데이터 확인” and “정답 검토” return to the actual Step 1/2 workspaces. All saved output locations below are relative to the owning project, never source folders.

| Family | Basic preparation action / required input | Persisted input used by actual training |
|---|---|---|
| classification | Step 2 image classes → Step 1 split → Step 3 selected model | Existing dataset version and label snapshot used by `/api/training/start` |
| segmentation | Step 2 polygons/pixel truth → split → selected model | Existing prepared segmentation version used by `/api/training/start` |
| detection | Step 2 object bbox → split → selected model | Existing prepared detection version used by `/api/training/start` |
| anomaly | Normal train images, purpose selector; independent normal/defect test and masks for region profile | Existing prepared anomaly version plus recorded `anomaly_mode` |
| patch_classification | Region labels, original-image split; “영역 라벨에서 패치 준비” | `/api/patch-classification/prepare` → `dataset/patch/<id>/patches.json`; returned path is submitted by real patch train |
| ocr | Project image → “문자 정답” string and train/val/test → “선택 이미지의 문자 정답 추가”; repeat with distinct train/val/test originals → “정답과 이미지 해시 저장” | `/api/ocr/prepare` → `dataset/ocr/<id>/`; saved picker and real OCR train use returned path |
| rotated_detection | Data-owner rotated fitting UI reads project labels; optional `direction_deg` remains separate from axial box angle → “정답·이미지 해시 저장” | `/api/rotated-detection/prepare` → `dataset/rotated_detection/<id>/`; existing real train uses returned path |
| rotation | Project image → explicit counterclockwise correction angle + split → add row → “원본과 보정각 준비” | `/api/rotation/prepare` → `dataset/rotation/<id>/rotation.json`; saved picker and real rotation train use returned path |
| defect_gan | Project image → existing reviewed bbox/polygon defect region + original-image split → “선택 정답 영역을 학습 표에 추가” → “원본을 보존하고 학습 복사본 준비” | `/api/defect-gan/prepare` → `dataset/defect_gan/<id>/`; real GAN train uses returned path; generated images still require review |
| enhancement | At least three distinct project originals → noise amount → “현재 데이터로 정답 쌍 준비” | `/api/enhancement/prepare` → `dataset/enhancement/<id>/`; existing saved pair picker and real train use returned path |

GUI fixtures should use distinct original bytes in train/val/test. Duplicate-content split validation rejects the same pixels across boundaries. OCR/rotation truth is explicit human input, not inferred from a filename or a bbox. Unsupported GAN shapes need an explicit defect crop rather than discarded annotations.

## RED → GREEN evidence

- `node --test src/renderer/components/training/trainingWorkflow.test.cjs`: initial 7 contracts failed for absent purpose/action/task/budget behavior. Later added source-row, project-artifact, stale CPU and explicit GAN-crop contracts failed before implementation. Actual-store request and preset-batch tests were added; the latter failed with missing `trainingPresetBatchSize`. The final stale-snapshot test failed with missing `taskSnapshotForScope` before its implementation. Final result: **14 passed, 0 failed**.
- `$QA_PYTHON -m pytest backend/tests/test_product_training_workflow.py -q`: initial budget relationship and validated anomaly mode failed; readiness/task module checks failed before creation. Import action and memory-cap test failed before those implementations. Invalid regular anomaly purpose initially returned 400 instead of required 422; gate implemented before preparation. Live progress test failed with `KeyError: duration_seconds`; journal callback now persists live elapsed/memory/epochs. Final focused file: **9 passing contracts**, included in the combined run below.
- Combined focused backend command:

  ```sh
  $QA_PYTHON -m pytest backend/tests/test_product_training_workflow.py backend/tests/test_automated_trials.py backend/tests/test_specialist_automated_trials.py backend/tests/test_training_receipt_recovery.py backend/tests/test_training_preparation_cancellation.py backend/tests/test_anomaly_evaluation_evidence.py backend/tests/test_specialized_training_jobs.py -q
  ```

  Result: **53 passed in 9.48s**.
- `npm run typecheck`: passed after final TaskCenter scope change.
- `git diff --check`: passed. Root owns combined regression/build and actual desktop acceptance evidence.

The API lifecycle test uses a controlled CPU runner to expose cancellation requested separately from acknowledged release; it is not a model-quality training experiment. Preparation uses temporary test images and verifies original hashes unchanged. No GPU job, network model download, existing remote job cancellation or physical acceptance occurred.

## Integration hooks and files

- Exported `TaskCenter({initialOpen?: boolean})` from `src/renderer/components/training/TaskCenter.tsx`; root mounts it globally/in the task overlay with `initialOpen`.
- Router `backend.api.routes_training_workspace.router`; prefix `/api/training-workspace`. Root registers router and authorization prefix.
- Common setup exports: `TrainingPreparationPanel`, `ProjectImagePicker`, preparation/action/scope helpers. TrainingController mounts the preparation panel for all families; patch workbench also mounts its chosen-backbone weight import.
- Primary changes: common ErrorModal/errorActions; useTrainingStore and training API request type; TrainingController/modelTrainingOptions/ModelFamilyCatalog; AutoDLWorkbench; OCR/Rotation/GAN/Patch workbenches; task center/helpers; new backend workspace router/engine; automated budget engine/API; anomaly trainer metadata; local import consumption in training/patch routes.
- Do not merge worker copies over shared files. App, global CSS, backend registration, program registry and rotated direction UI are other owners’ changes. No commits made by this worker.

## Honest pending items

Root must record native GUI source-switch/error/cancel/reopen/handoff evidence and update the master status per requirement. Weight-file existence/import hash proves saved bytes, not official architecture suitability. Dependency instructions do not install packages. Local checks do not verify a selected remote server. Shared reservation readback is shown independently from terminal job status. Functional contract results do not establish manufacturing accuracy, optimum anomaly thresholds, physical protocol readiness or hardware certification.

## Independent flow review follow-up (root delegated 2026-10-01)

Root explicitly delegated two verified review fixes and the two existing fixture adjustments. Training UI/files remain frozen and the data owner owns subsequent TaskCenter handoff changes.

- `backend/api/routes_flowchart.py`: a request with an active project now resolves the inspection path and requires it under current `project_dir`, `dataset_dir` or registered `source_dataset_dir` before any model/engine work. A symlink resolving outside those roots is rejected with 422. Standalone `request=None` preserves explicit arbitrary-image operation. Desktop HTTP outside-image debug is rejected; valid project/dataset/source debug is accepted. Existing shared authorization already rejects outside paths at middleware; new shared handler tests prove the same boundary for in-process scoped requests rather than claiming a previously demonstrated shared HTTP bypass.
- `backend/engine/flow_workspace.py`: catalog vocabulary normalizes recorded class names and runtime IDs; detection foreground IDs start at 1, other list vocabularies start at 0, explicit IDs are retained independently even without names. `routes_flowchart._catalog_model_settings` exposes this known metadata to real template imports. Model and downstream class-rule scopes validate mapped names/IDs against nearest mapped-model vocabulary. Missing legacy vocabulary still permits an explicit mapping without inventing metadata or claiming compatibility was verified.
- New `backend/tests/test_flow_review_guardrails.py`: 10 cases cover outside/symlink rejection before execution, real desktop HTTP allow/reject, standalone compatibility, invalid target names/IDs, valid mapping, metadata-free legacy mapping, catalog identity, downstream blob rule IDs and explicit IDs without names.
- Initial RED: **6 failed, 2 passed** (outside requests reached execution, desktop returned 200, unknown names/IDs did not raise, catalog metadata absent). Explicit-ID-only follow-up first failed with `DID NOT RAISE ValueError`, then passed after preserving independent ID metadata.
- Expanded regression initially **119 passed, 2 failed** because two old active-flow tests used an image outside their created project while testing missing-model 409. Root authorized moving only those fixture images under `project_a`; missing-model/active-flow intent is retained.
- Final command:

  ```sh
  $QA_PYTHON -m pytest backend/tests/test_flow_review_guardrails.py backend/tests/test_flow_workspace_upgrade.py backend/tests/test_flowchart_model_verification.py backend/tests/test_flowchart_execution_target.py backend/tests/test_remote_debug_flow_upgrade.py backend/tests/test_flowchart_editable_graph.py backend/tests/test_flowchart_flow_contract.py backend/tests/test_flowchart_drafts.py -q
  ```

  **122 passed in 7.31s** (includes the 10 new cases). Targeted `git diff --check` passed. No GUI or GPU/model-quality claim; source fixtures are temporary and no source data changed.

Review reports delivered separately: initial selection storage overwrite, missing partial-decision graph hash/output scope and non-frozen A/B input were fixed/tested by root. One additional read-only finding was reported in `FlowNodeDebugger.tsx`: it displays parent artifacts from inactive conditional edges as actual input despite a skipped child reporting input_count=0. This worker did not edit that file.

## Additional cached-weight CPU functional smoke (2026-10-01)

Root separately authorized six distinct real-source 128 px crops in private temporary storage. All original image and paired label SHA-256 values matched before/after. Classes, the designated patch normal class and rectangular regions are artificial functional-test truth; they are not manufacturing labels or normal-data evidence.

Actual `UnifiedAutoMLTrainer` ran sequentially with cached authentic weights, CPU threads 2, batch 1, loader workers 0, augmentation disabled and one epoch (2 train steps, 2 validation samples, 2 held-out fixture test crops). Real checkpoint reconstruction and `trainer.infer` on one known test crop succeeded for all four:

| Model / task | Training input | Train + reopen + inference process time |
| --- | --- | --- |
| DINOv3 small / classification | 64 × 64 | 6.232 s |
| DINOv3 small / segmentation | 64 × 64 | 5.288 s |
| DINOv3 small / patch classification | 64 × 64 patches | 5.224 s |
| YOLO26n / detection | 128 × 128 | 4.114 s |

All training/validation losses, saved floating tensors and reconstructed outputs were finite. Actual trainable parameter hashes changed; each DINO encoder hash remained unchanged. Metadata recorded the supplied cached pretrained SHA-256. Each process blocked network connections and observed zero connection attempts. YOLO's test inference returned zero boxes at threshold 0.5; this confirms execution, not detector effectiveness. No GPU, download, existing-job cancellation or app-source edit occurred.

Private evidence records contain source/crop/cache/checkpoint/overlay hashes and exact outputs. Summary SHA-256: `703c33acaee68b41cdeccb77a1035d13a73f943a0671906a4e89f36c04c15aa3`. A fresh readback asserted all four completed/reopened/inferred statuses, 2 optimizer-step callbacks each, real parameter changes, cached hash identity and unchanged originals. `quality_approved=false` and `operational_approved=false`; this direct engine smoke does not establish GUI/server job acceptance, accuracy, specificity, threshold suitability or physical approval.

## Final delegated flow/transport review fixes (2026-10-01)

Root subsequently authorized these narrowly scoped fixes. App transport-key remount remains owned by root; the API persistence-identity helper remains owned by the data agent.

- `SharedProjectPanel`: saves current edits before switching, then advances the transport epoch after the actual API server/project transition. Login advances once when the server connection commits and again when a shared project commits, so a failed project inventory also discards the previous server's cache. Activation/disconnection advance after their IPC/base transition. Controlled real-component/real-compute-store tests reproduced old-server remount requests and late stale cache publication; activation, login, disconnect and post-login inventory failure now retain only the final transport/project's profiles.
- `FlowWorkspacePanel`: A/B execution explicitly describes the connected backend CPU, including a shared server, instead of claiming desktop CPU. A delayed comparison from an old transport cannot publish result/location state.
- Fixed-image persistence uses a stable v2 key containing sanitized API identity, compute profile, project ID/directory, source, task and labelset. The async context additionally contains the ephemeral transport epoch. Actual panel save/restore tests first reproduced losing two selections after epoch 7→0 and inheriting another server's identical project/path selections. Both now pass, alongside pure-key persistence/isolation contracts. The data-owned identity helper excludes URL credentials/query/fragment and local runtime port changes. Ambiguous old epoch keys are not migrated across servers.
- `flow_workspace.map_template`: after explicit source name/ID validation and semantic ID mapping, segmentation's declared full class-name list follows the target checkpoint's exact channel order, including extra target classes. Original class-ID selections remain explicitly mapped. Two controlled CPU engine tests first produced empty inspection evidence from the channel-order configuration error; both now inspect the intended `scratch` class at target ID 2. Unknown target names/IDs and metadata-free legacy behavior remain covered.

RED/GREEN: transport 3 failing races; A/B location attribution failing; panel persistence/isolation 2 failing behaviors; stable-key helper absent; channel-order CPU execution 2 failing cases; post-login inventory failure failing cache invalidation. Final focused frontend command (the SharedProjectPanel, FlowWorkspacePanel and flowWorkspace test files): **15 passed, zero failures**. Expanded flow/backend command listed above: **124 passed in 6.52s**, including the two new real-engine class-order cases. `npm run typecheck` and targeted `git diff --check` passed. Root owns the new combined regression/build/package/native reopen acceptance. No original data or real shared server was mutated by these tests; no GPU or quality approval claim. Files frozen for root integration.

### Final full-suite fixture scope correction

Root's subsequent full regression exposed two legacy fixtures outside their active project: `test_api_flowchart_endpoints` and `test_flowchart_pipeline_lifecycle`. A fresh two-case RED run reproduced **2 failed** (correct input-scope 422 reached before intended missing-model 409). Root explicitly authorized moving only these synthetic inspection inputs into the isolated app's actual `dataset_dir`, read from `/api/project/current`. Both still assert 409 and the missing-model detail; no production guard or backend source changed. GREEN: `test_flowchart_engine.py`, `test_labeling_workflow_features.py` and `test_flow_review_guardrails.py`: **31 passed, 1 skipped in 7.87s**. The skipped opt-in real manufacturing image case had no source directory configured in this focused run; the separate cached-weight real-source CPU smoke remains recorded above. Targeted diff check passed. Test files frozen again for root's final gate.

## U012 native draft-persistence repair (final delegated scope)

Root's native check reproduced an edited 256 px ROI reverting to the default tile pipeline after leaving the flow step, quitting and relaunching. Project-switch persistence alone did not cover this path. Root authorized a model-independent draft save control and guarded autosave; backend draft APIs were retained unchanged.

- `FlowDraftControls` is mounted in the real flow toolbar with Korean **초안 저장**. It uses the existing draft PUT plus verified GET, independently of missing models or executable graph validity. Initial/default graphs have no persisted hash and say **편집 초안 · 저장 전**. A read-back draft receives a SHA-256 marker and says **초안 저장됨 · 실행본 활성화 전**; later edits remain visibly unsaved until readback succeeds.
- Dirty graphs save after a 650 ms debounce. A safe stage unmount flushes edits if the timer has not fired. Loading, running, saving, node-history drag, graph viewport drag and ROI pointer drag block automatic/manual persistence. Source/task/labelset/project identity and API identity/transport epoch are rechecked before submission and readback publication. Failed graphs retain dirty status and are not retried in a loop; the explicit button permits retry.
- Draft PUT never creates or activates an executable version. The flow package/deployment handoff also requires `pipelineIsDraft=false`, so autosave clearing the dirty bit cannot expose an earlier selected active version as the new draft's deployment. An unchanged draft that already matches an active saved graph retains that existing backend-reported identity; no new activation occurs.
- Tests run the actual controls component and actual Zustand flow store against an isolated persisted draft API boundary, then invalidate/reopen the store to verify exact ROI coordinates. Model-independent click, debounce save, early stage exit, four busy/drag guards, stale transport response and failed save are covered. Native relaunch and viewport acceptance remain root's separate gate; an immediate forced process termination before any draft request completes is not claimed durable.

RED: two new store tests failed because the initial persisted marker was absent and stale transport ownership still reported successful readback. Nine control tests could not find the absent UI/hook implementation. GREEN: `FlowDraftControls.test.cjs`, `flowchartDraft.test.cjs`, `flowchartHistory.test.cjs`, `flowWorkspace.test.cjs`, `FlowWorkspacePanel.test.cjs` and the delivery owner's `flowStudioLayout.test.cjs`: **35 passed, zero failures in 3.50s**. Backend `test_flowchart_drafts.py`, `test_flow_workspace_upgrade.py`, `test_flow_review_guardrails.py`: **26 passed in 4.33s**. Final `npm run typecheck` and `git diff --check` passed. No backend production changes, original data mutations, executable activation, GPU job or model-quality approval occurred. This worker's files are frozen for root rebuild/native verification.
