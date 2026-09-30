# P02 foundation labeling implementation ledger

Scope: F001–F014, approved complete-feature-program spec and plan.

## Decisions and interface

- Ruling: Keep the existing workspace specified by the coordinator and do not create another checkout or publish changes. All team agents share this approved workspace.
- Ruling: `foundation` combines local SAM2 masks, local Grounding DINO text boxes, and frozen authentic DINOv3 visual features. Local dependencies and verified weights are prerequisites. OpenCV tools remain explicitly named alternatives.
- Ruling: Long text is split into bounded model input chunks, with the original text preserved in provenance. No arbitrary UI character limit or silent tokenizer truncation is permitted.
- Interface: Candidate generation is review-only and reuses existing selective acceptance, snapshots, annotation revisions and dataset fingerprints. Candidate batches and few-label feature models have separate persisted ownership and cancellation state.

## Progress

- Tests written before production changes: foundation prerequisite, original mask coordinates/holes, area filtering, outside prompts, long prompt preservation, positive/negative feature scoring, real trained classifier and cancellation/parent immutability.
- Foundation RED: 6 failing tests in `/private/tmp/p02_foundation_red.log`, then 6 passed.
- API RED: 3 failing tests in `/private/tmp/p02_foundation_api_red.log`, then 14 focused passed.
- Feature training / click / box RED: missing routes and a cancellation persistence race, then 41 related tests passed. The race fix serializes receipt read/write and terminal-state publication.
- Additional RED→GREEN: stopped-job artifacts cannot be selected; polygon size uses polygon area; text confidence participates in combined scoring; absent text results cannot fall back to point/grid proposals; completed-model size/device requests; changed DINO feature weights block review.
- Focused verification command:
  `/opt/anaconda3/bin/python -m pytest -q -p no:cacheprovider backend/tests/test_foundation_labeling.py backend/tests/test_foundation_labeling_api.py backend/tests/test_label_suggestions.py backend/tests/test_label_candidate_api.py backend/tests/test_label_candidate_constraints.py backend/tests/test_labeling_ai.py backend/tests/test_labeling_workflow_features.py backend/tests/test_annotation_path_security.py backend/tests/test_project_annotation_isolation.py`
  Initially **81 passed**. During concurrent dataset changes the same group became **77 passed, 4 failed**, all at `annotation_formats.py:115`, missing legacy LabelMe `imagePath`; these are recorded below and reported to the integration owner. New P02 tests passed.
- Full backend command: `/opt/anaconda3/bin/python -m pytest -q -p no:cacheprovider backend/tests` → **1271 passed, 11 skipped, 15 failed** in 239.86s. Log: `/private/tmp/p02_backend_gate.log`. This is a concurrent-workspace integration run, not a green gate.
- `compileall` for all new modules and owned routes passed.
- Final owned/related verification: `test_foundation_labeling.py`, `test_foundation_labeling_api.py`, `test_label_candidate_api.py`, `test_label_candidate_constraints.py`, `test_labeling_ai.py`, `test_labeling_workflow_features.py` → **48 passed**, one existing multipart deprecation warning. Log: `/private/tmp/p02_owned_final.log`. No further production edits followed this run.
- Coordinator subsequently reported fixes for legacy LabelMe normalization, missing Studio masks and internal Query defaults, with its focused 46-test run passing. The full integration gate is to be rerun after all concurrent packages settle; the failed historical run above remains retained.
- Review: focused self-review of provider prerequisites, source/weight/labelset binding, cancellation persistence and completed parent eligibility. Fresh integration review is coordinator-owned.

## Evidence and limitations

- Foundation-model and feature-training functional tests are separate from dataset-quality approval.
- Actual official SAM2-tiny CPU execution on the supplied read-only **8192×5464** image: one native-coordinate object mask, approximately **5.54 seconds**, original source SHA unchanged. Private receipt: `/private/tmp/p02_foundation_real_qa_20260930.json`. Official weight SHA256: `48c14467e5cf9e51870511feb72c89688e82dd74523142c0538b663e193ac2a7`. No source annotations were written by this QA.
- Actual cached authentic DINOv3-S extraction on explicitly generated red-square / blank-background fixtures: feature shape `[2,384]`; real classifier predictions `[1,0]`; cross entropy `0.693147` → `0.000003636`. Private receipt: `/private/tmp/p02_feature_fixture_qa_20260930/receipt.json`. These generated fixture labels are functional checks and are not manufacturing truth or quality acceptance.
- Full long-text, positive/negative combination, geometry, filtering and lifecycle regressions control only the external model boundary; mask preservation, review persistence, feature scoring and classifier optimization execute production code. They do not claim pretrained grounding accuracy on customer defects.

## Optional installation and model preparation

Install `backend/requirements-semantic-labeling.txt` into the **same Python environment used by the backend**. Use the interpreter configured for that backend, for example:

```sh
/path/to/backend-python -m pip install -r backend/requirements-semantic-labeling.txt
```

This task did not install dependencies globally. Real QA used the pre-existing isolated dependency target `/private/tmp/modu_prompt_dependencies_20260930`; therefore readiness in a different interpreter still truthfully reports missing dependencies.

- SAM2: configure `mask_model_dir` with a local Hugging Face model directory containing `config.json`, `preprocessor_config.json` / processor configuration and `model.safetensors`. Official reference: https://huggingface.co/facebook/sam2.1-hiera-tiny . Its current checkpoint is a video superset; the adapter loads the image model and rejects every missing/mismatched image parameter. Unused video memory weights do not become random image parameters.
- Text: configure `model_dir` with local Grounding DINO weights and tokenizer/processor files. Official model API: https://huggingface.co/docs/transformers/v4.57.1/model_doc/grounding-dino . Text model execution is local, uses `trust_remote_code=False`, and never downloads during inference.
- Visual examples / feature training: use authentic DINOv3 via the existing strict adapter. `feature_checkpoint` / `feature_sha256` can be configured; otherwise an existing local official Hugging Face cache is resolved with `local_files_only=True`. No random encoder fallback is allowed.
- Official SAM2 point/box/postprocessing API used: https://huggingface.co/docs/transformers/v4.57.1/model_doc/sam2 . Image embeddings are reused across grid prompts; masks are returned at original dimensions.

## API / UI contract

### Candidate provider setup

- `GET /api/label-candidates/setup`: retains legacy grounding readiness fields and adds `configuration`, `providers.foundation`, `providers.grounding_dino`, `labelset_id`, `labelset_version`.
- `PUT /api/label-candidates/setup`: partial update of `model_dir`, `mask_model_dir`, `feature_backbone`, `feature_checkpoint`, `feature_sha256`. Directories must exist; readiness explains missing optional dependencies/weights. Setup does not acquire models.

### Single and keyword-batch proposals

- `POST /api/label-candidates/generate`:
  - `backend`: `foundation`, `grounding_dino`, or explicitly deterministic `template_match`.
  - `image_path`, `label`, `prompt` (no character cap), `positive_examples` / `negative_examples`: arrays of `{image_path, roi:[x1,y1,x2,y2]}`; all example images must belong to the active imported dataset.
  - `points:[{x,y,label:0|1}]`, `boxes:[[x1,y1,x2,y2]]`; coordinates are original image pixels and invalid/outside coordinates are rejected.
  - `device`: `cpu`, `auto`, `mps`, or `cuda:N`; an unavailable requested device is rejected.
  - `threshold`, `text_threshold`, `min_area`, `max_area`, `min_width`, `max_width`, `min_height`, `max_height`, `max_candidates`.
  - `output_geometry`: `polygon`, `mask`, or `bbox`; `mask` creates a `brush_mask` annotation with PNG-alpha data URL in the existing `mask_rle` field, preserving holes. Candidates also carry a direct mask and outline polygon.
  - Optional `labelset_id`, `labelset_version` (dataset fingerprint returned by setup), `suggestion_model_id`.
- Returned persisted proposal: existing `id`, `status:'pending'`, image identity/revision/hashes, task, threshold, candidates, accepted IDs and backup fields; additionally labelset ID/version and provider request details. Each candidate has `confidence`, `annotation`, `area`, `source`, `provenance`, direct `polygon` and PNG `mask_rle`.
- `POST /api/label-candidates/batches`: same options plus `image_paths`; image_path may be omitted. Keyword text is the shared `prompt`.
- `GET /api/label-candidates/batches`, `GET /api/label-candidates/batches/{batch_id}`, `POST /api/label-candidates/batches/{batch_id}/cancel`.
- Batch fields: `total`, `processed`, `generated`, `zero_candidates`, `failed`, `proposals`, per-image `entries`, `request`, `review_fingerprint`. States: queued/running/cancelling/completed/stopped/failed/interrupted. Cooperative cancellation checks surround actual calls; no post-cancel proposal is published. Interruption after backend restart is explicit and partial proposals remain inspectable.
- Existing `GET /api/label-suggestions` and `GET /api/label-suggestions/{id}` reopen proposals. `POST /api/label-suggestions/{id}/review` selectively accepts/rejects IDs using the existing backup, annotation revision and source fingerprint contract. Source / example / model / feature hash changes block acceptance. Only successful own batch acceptance advances that batch's review fingerprint.

### Few-label learning and refinement

- `POST /api/label-suggestions/feature-train`: `{device,backbone,pretrained_checkpoint?,pretrained_sha256?,epochs,learning_rate,parent_model_id?,image_paths?}`. Default DINOv3-S; optional selected source images, otherwise all active source images are examined (local guard 5000).
- `GET /api/label-suggestions/feature-train/{job_id}` and `POST /api/label-suggestions/feature-train/{job_id}/cancel`.
- `GET /api/label-suggestions/feature-models`: only completed owned classifiers with matching current completed job receipt and pinned checkpoint hash.
- At least two explicitly labeled region classes are required; a one-target setup needs a manually labeled background class. Unlabeled pixels are not fabricated background truth. Bbox/polygon/brush-mask/tag annotations provide labeled crops. A real trainable linear softmax head is fitted to authentic frozen DINO features.
- Refinement creates a new ID/checkpoint, checks class order and feature weight identity, re-verifies its parent before publication, and preserves its parent's bytes. Metadata records source region receipts, training config/loss, labelset/version, parent ID/hash and feature provenance. Cancelled/interrupted owner receipts make artifacts ineligible.
- Use the returned completed feature model ID as `suggestion_model_id` in a foundation single/batch request. SAM2 supplies actual region masks and the fitted head scores their labels; background is filtered.

### Completed models and canvas tools

- Existing `POST /api/label-suggestions/generate` / `POST /api/label-suggestions/batches` retain completed-task-model proposals and now accept `device` and the same six object-size bounds. Existing cancellation finishes its current image and skips remaining images, preserving the established contract.
- `POST /api/annotations/auto-select`: legacy seed fields plus `backend:'foundation'`, `device`, optional additional positive/negative points. Explicit `backend:'opencv'` remains the deterministic alternative.
- `POST /api/annotations/shape-converter`: `backend:'foundation'` with a source image/bbox or bbox data produces actual mask/polygon evidence. Existing geometry and OpenCV converters remain available and explicitly named.

## Feature mapping

| IDs | Implemented boundary | Focused evidence |
|---|---|---|
| F001–F006 | Grounding text chunks, image positive/negative DINO scoring, combined prompt gates, SAM2 direct masks/polygons | long prompt, negative examples, combined gate, geometry/holes and proposal acceptance |
| F007–F009 | SAM2 points/boxes and canvas selection/conversion | native-coordinate adapter and click/box API tests |
| F010 | Real feature-head fitting and new immutable refinement candidate | fitting/loss decrease, API train/refine, stopped-artifact ineligibility; actual DINO fixture features |
| F011 | Existing completed-model single/batch proposal contract retained | model suggestion regression suite |
| F012 | Owned persisted prompt-provider batches and cancellation | provider cancellation / no published post-cancel proposals |
| F013 | Pixel area and bounding dimensions across provider/model proposals | polygon area and completed-model size tests |
| F014 | Explicit CPU / available GPU selection without silent fallback | selected CPU forwarding and unavailable CUDA rejection; actual CPU SAM2 |

## Limits carried to integration

- Grounding DINO targets English object phrases. General language reasoning, complex exclusions and microscopic defect accuracy are not claimed. Every original long prompt is stored; bounded model chunks are processed rather than silently truncated.
- SAM2 predicted IoU and DINO cosine similarity are not calibrated defect probabilities. Combined confidence is the minimum participating score; provenance names score semantics.
- Polygon outlines are editable approximations and do not represent interior holes; use the direct `mask` output for pixel-exact topology. No label mask is converted into an inspection defect verdict by this provider.
- Device availability is checked; actual SAM2/DINO GPU execution and hardware quality acceptance were not performed in this task.
- Resource guard: 128 megapixels per source; visual crops are resized individually to224, mask NMS storage is bit-packed, grid generation reuses image embeddings. Local job cancellation is cooperative around framework calls, not a hard process deadline.
- Native UI wiring and application reopen QA are owned by the coordinator / integration worker and are not claimed by backend tests.
- No remote GPU jobs, live service changes, Git publication, original-source edits or global dependency installation occurred.

## Concurrent integration failures from the full run

The following failures were reported to the coordinator. They are not omitted from the completion record:

- `test_geometry_flow_completion.py::test_fitted_oriented_roi_crops_native_axes_and_keeps_source_mapping`
- `test_geometry_flow_completion.py::test_curves_and_area_use_anisotropic_source_pixel_calibration`
- `test_geometry_flow_completion.py::test_measurement_rejects_invalid_or_mismatched_source_geometry[params0]`
- `test_geometry_flow_completion.py::test_measurement_rejects_invalid_or_mismatched_source_geometry[params1]`
- `test_geometry_flow_completion.py::test_measurement_rejects_invalid_or_mismatched_source_geometry[params2]`
- `test_geometry_flow_completion.py::test_measurement_rejects_invalid_or_mismatched_source_geometry[params3]`
- `test_geometry_flow_completion.py::test_learned_rotation_flow_uses_checkpoint_and_inverse_source_transform`
- `test_label_suggestions.py::test_bulk_defaults_to_only_unlabeled_images`
- `test_label_suggestions.py::test_bulk_unlabeled_selection_matches_stage1_when_studio_mask_is_missing`
- `test_label_suggestions.py::test_studio_ng_tag_is_labeled_in_stage1_and_excluded_from_default_batch`
- `test_model_comparisons.py::test_real_test_image_comparison_persists_hashes_disagreements_and_project_scope`
- `test_project_annotation_isolation.py::test_gallery_label_status_tracks_project_annotation_overlay`
- `test_remote_worker.py::test_remote_worker_runs_two_verified_models_in_sequential_graph[True]`
- `test_zero_escape_exporter.py::test_standalone_segmentation_tiles_match_flowchart_probability_and_area[onnx]`
- `test_zero_escape_exporter.py::test_standalone_segmentation_tiles_match_flowchart_probability_and_area[torchscript]`

## Integration follow-through

See `P02-P03-integration.md` for native labeling UI wiring, per-class editable masks, lossless mask exchange, DICOM reader/display, 118 passing related backend tests, 10 passing renderer contracts, and actual 8192×5464 CPU SAM2 acceptance/export readback. Native application acceptance and manufacturing quality approval remain pending. New optional `class_ids` candidate mapping retains names/IDs in provenance; `GET /api/label-suggestions/feature-jobs` reopens owned active-labelset training receipts.
