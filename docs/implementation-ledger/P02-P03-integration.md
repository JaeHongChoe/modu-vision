# P02 labeling and P03 mask / DICOM integration

Scope: F001–F014, F116, F118. Shared approved checkout; no commit, remote GPU job, running-service change or global dependency installation.

## Independent foundation review

Reviewed local SAM2/DINO provider boundaries, source/model/example hash checks, active labelset/version binding, owned cancellation and terminal receipts, feature-head parent immutability, selective acceptance and snapshots against `P02-labeling.md`. Fresh review gate: 27 passed across foundation, API, candidate constraints and labeling workflow tests. The existing text/template UI and first-mask-only canvas editing were the remaining integration work.

## Implemented UI and contracts

- `CandidateProviderControls` uses `providers.foundation.ready`; a nonempty text prompt also requires `providers.grounding_dino.ready`. It captures actual positive/negative SAM2 points and native-coordinate box prompts, accepts arbitrary-length text, combines positive/negative image-region examples, and selects pixel mask / direct polygon / bbox output.
- Local model configuration uses exactly `model_dir`, `mask_model_dir`, `feature_backbone`, `feature_checkpoint`, `feature_sha256`. Folder selection and explicit checkpoint/hash save are available. Inference never downloads models. Model preparation requirements remain in `P02-labeling.md`; install optional requirements only in the backend interpreter.
- Point and box prompts are separate canvas tools. The existing OpenCV wand is named explicitly. No OpenCV output is represented as SAM2 output.
- Both foundation and completed-task-model single/batch UI requests carry device and six native-pixel size bounds. Foundation batches share keyword/image examples; image-specific point/box prompts are used individually.
- Asynchronous foundation batches expose queued/running/cancelling/completed/stopped/failed/interrupted state, per-image errors, cancellation and persisted reopen/review. Feature train/refine has epochs, learning rate, immutable parent selection, completed model selector, cancellation and persisted receipt reopening.
- Added `GET /api/label-suggestions/feature-jobs`, scoped to current project/source and active labelset. Existing model eligibility/hash verification is retained.
- Optional candidate `class_ids` maps names to unique IDs 1–255. The UI sends its current class palette, and candidate provenance records the mapping, so accepted masks retain the same trainable class IDs.
- Review previews render actual PNG-alpha masks. Candidate source/provenance/area, labelset/version and checkpoint identity are inspectable. Selected acceptance reloads editable labels, preserves backup version, and leaves image workflow in `needs_review`. Image approval uses the existing individual reviewer action.
- Canvas editing chooses the selected brush mask or active-category mask. It retains other class masks and unused palette entries, and sequentially decodes other masks without redecoding every pan/zoom. Mask and polygon editing use the existing history/save path.

## Lossless external masks (F118)

New routes: `backend.api.routes_mask_exchange.router` at `/api/dataset/masks`; registered by the integration coordinator.

- `/import`: an external directory containing `mask_manifest.json` and 8-bit L/P PNG class masks; preview/apply, reject/replace/merge conflict policy, expected image revisions, optional expected manifest+mask hash signature (the UI always sends the preview signature).
- Preview displays class IDs/names/colors and each actual alpha mask. Apply writes project-owned Studio annotations and raster masks, creates an automatic version backup, rejects stale revisions or a changed supplied signature, and rolls back all changed files/metadata on failure.
- `/export` and owned `/download/{id}` provide exact class-ID PNGs plus explicit class/name/palette mapping, original relative source names/dimensions/SHA, and optional byte-exact originals. Full source filenames are retained in mask paths to avoid same-stem collisions.
- Class IDs 0–255, sparse IDs, unused class palette entries, holes and native geometry are preserved. Imported class masks become editable `brush_mask` labels in the same training path. Grouped segmentation preserves explicit ID 255 as a class instead of converting it to binary 1. Merge retains existing box/polygon regions as trainable class pixels.
- The format deliberately rejects RGB/RGBA label rasters, unsupported bit depth, unmapped IDs, unsafe paths, geometry/source-hash mismatch and conflicting class mappings. A palette/name mapping is required; colors are never guessed into classes.

Minimal manifest shape:

```json
{"schema_version":1,"classes":[{"id":0,"name":"background","color":"#000000"},{"id":7,"name":"scratch","color":"#ef4444"}],"images":[{"file_name":"images/example.png","mask_file":"masks/example-mask.png","width":100,"height":80}]}
```

## DICOM reader and display (F116)

New routes: `backend.api.routes_dicom.router` at `/api/dataset/dicom`; registered by the integration coordinator.

- Optional dependency: `backend/requirements-dicom.txt` (`pydicom>=3,<4`). Functional fixture QA installed pydicom 3.0.1 only under `/private/tmp/p02_dicom_dependencies_20260930`; no global package change.
- `.dcm` and `.dicom` extend the existing JPG/JPEG/PNG/BMP/TIF/TIFF/WebP set. Source dimensions, health validation, gallery thumbnails/raw display, grouped training, industrial and foundation readers use the source helper without replacing original identity.
- Signed/16-bit grayscale values use rescale slope/intercept, DICOM linear windowing and MONOCHROME1 inversion. Metadata exposes dimensions, native pixel spacing, frame count/index, bit depth, modality, transfer syntax, window and source SHA.
- `/view` creates an owned lossless PNG and receipt with native dimensions, identity transform and view hash; `/views/{id}` serves it within the active project. The labeling panel selects window/frame and inspects metadata/hash.
- Missing pydicom or compressed-pixel decoder yields an actionable error. Multi-frame decoding requires an explicit frame index; default dataset import/training does not silently choose a frame. A chosen display window is distinct from the default source window used in training/inference, and the UI states that difference.
- Original DICOM bytes are never overwritten. The decoder guards 128 megapixels. Production DICOM/vendor/compressed-file acceptance remains pending.

## RED → GREEN and verification

- Initial mask/DICOM feature gate: 7 failed due to absent modules, then 7 passed.
- Additional RED gates: missing feature-job reopen route, same-stem mask filename collision, class-ID mapping, sparse/unused/255 palette training, selected/category mask edit behavior, complete-model option forwarding, and mixed bbox/mask training pixels; each was reproduced before the corresponding implementation.
- Latest focused integration: **118 passed**, one existing python-multipart deprecation warning. Log `/private/tmp/p02_p03_integration_green.log`.
- Command: `PYTHONPATH=/private/tmp/p02_dicom_dependencies_20260930 /opt/anaconda3/bin/python -m pytest -q -p no:cacheprovider backend/tests/test_mask_exchange.py backend/tests/test_dicom_input.py backend/tests/test_foundation_labeling.py backend/tests/test_foundation_labeling_api.py backend/tests/test_label_candidate_api.py backend/tests/test_label_candidate_constraints.py backend/tests/test_grouped_dataset_views.py backend/tests/test_dataset_loaders.py backend/tests/test_dataset_metadata_api.py backend/tests/test_annotation_training_overlay.py backend/tests/test_labeling_workflow_features.py backend/tests/test_labeling_ai.py backend/tests/test_annotation_formats.py backend/tests/test_project_annotation_isolation.py`.
- Renderer behavior/conversion verification: `node --test scripts/verify-foundation-labeling-ui.cjs scripts/verify-converted-annotation.js` → **10 passed**.
- `npm run build:renderer` → success, existing dynamic-import/chunk-size warnings. Owned Python modules compiled successfully.
- Fresh global typecheck during concurrent edits failed first on unused imports in `RotatedDetectionPanel.tsx` and later on in-flight `FlowGeometryEditors.tsx` syntax at lines 30/70. No owned labeling/mask/DICOM type errors were reported. Final typecheck and whole-backend gate remain coordinator-owned after the concurrent packages settle.

## Actual supplied-image CPU workflow evidence

Official local SAM2 executed on a private byte-exact copy of a supplied **8192×5464** image. The original source hash was verified unchanged. Readback verified the selected mask, owned version backup, 299,100 class-7 pixels, native geometry, exact exported PNG pixels and byte-exact exported original image.

- Project `9b8db6d0`.
- Proposal `suggestion_98ffee0cd5004368bccd5f9a`; selected candidate suffix `_c1`.
- Backup `v_20260930_121620_61e8dff4`.
- Receipt `/private/tmp/p02_p03_real_api_qa_20260930/receipt.json`; the receipt retains source mapping/hash, provider/weight provenance and export archive identity outside public source files.
- Workflow state `needs_review`; `quality_approved=false`.
- Initial private QA harness omitted production scope middleware; it was corrected and only its owned temporary legacy overlay was removed. The verified artifacts above reside in the private project scope.

## Remaining acceptance boundary

Native application click/drag, saved project reopen, batch cancellation/reopen, feature classifier train/refine selector, and human image-approval screen QA are pending coordinator acceptance. Actual Grounding DINO text/combined manufacturing input, real positive/negative target discrimination, GPU/device execution and heldout quality are not claimed by the controlled external-model boundary tests. The existing authentic DINO fixture receipt in `P02-labeling.md` proves feature fitting, not defect-quality approval. Renderer/API implementation and these functional checks do not make all feature rows `accepted`.
