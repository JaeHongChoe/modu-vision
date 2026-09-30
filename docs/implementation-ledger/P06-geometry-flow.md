# P06 — flow, geometry and GAN composition

Baseline: `a4d3610cc9e462d5718418d3e34598f45c2727c2`. No commit/push, live service restart, costly GPU job or write to the original dataset was performed.

## Evidence boundaries

Implementation and API/renderer contract checks are complete for the rows below. Native desktop interaction, trained-model quality approval and field hardware acceptance remain separate. CPU integration fixtures exercise real networks and real raster pixels; their deterministic weights are test inputs and do not establish production accuracy.

## Individual feature evidence

| ID | Implemented path | Verified evidence | Remaining acceptance |
|---|---|---|---|
| F064 | Editable DAG nodes, edges, payloads, validation, history, saved versions | `flowchartAggregate.test.cjs`, `flowchartHistory.test.cjs`, `test_flowchart_flow_contract.py` saved graph/reopen cases | Native desktop graph editing and reopen |
| F065 | Model-result ROI/image chaining and source transforms | `test_flowchart_flow_contract.py`; learned rotation → segmentation → measurement standalone parity in `test_geometry_flow_completion.py` | Native model chooser and a quality-approved production chain |
| F066 | Present/absent class predicates with confidence and explicit skipped steps | `test_flow_class_evidence.py::test_all_observed_segmentation_classes_can_select_result_branch`; graph class-condition UI | Native branch edit and inspection history readback |
| F067 | Same checkpoint model cache, synchronized loading, per-branch deep copies | `test_flowchart_editable_graph.py` verified cache/path identities, remote worker regressions and bounded parallel network fixture | Native shared-model workflow with real production classes |
| F068 | any/all NG aggregation retaining REVIEW | `flowchartAggregate.test.cjs`, graph/backend contracts | Native aggregate workflow |
| F069 | Native pixel fixed ROI → model | `test_flow_package.py::test_fixed_roi_package_runs_same_source_pixel_rectangle_standalone`; actual 8192×5464 source used in read-only smoke | Native selection and stored history |
| F070 | Detected source ROI → subsequent model; no ROI means REVIEW | `test_flowchart_flow_contract.py::test_zero_detected_rois_requires_review_instead_of_ok`; remote verified two-model regression | Production detector specificity and native saved results |
| F071 | Minimum-area oriented polygon fit, native aligned crop, inverse source mapping; learned RotationNet preprocessor/catalog/export dependencies | Pixel-conditioned network rotation; native fit tests and actual source 48×24 aligned crop; whole-flow subprocess parity | Production angle/OBB model validation and native editing |
| F072 | Native ROI patch grid with overlap and propagated source transforms | `test_flowchart_typed_completion.py::test_patch_split_covers_source_and_persists_node_images`, `test_flowchart_patch_nodes.py` and native patch package contracts; renderer split controls | Native patch workflow on selected production ROI |
| F075 | Polyline and adaptive cubic Bezier source-coordinate lengths; anisotropic mm calibration; source preview and point canvas | `test_curves_and_area_use_anisotropic_source_pixel_calibration`; malformed point/calibration rejection; actual source edge length 64px | Native drawing/visual inspection and externally measured calibration |
| F076 | Source polygon/mask area; mm² from explicit X/Y calibration; measurement node/results | Calibrated polygon and class-mask tests, original image measurement smoke, SSR saved measurement units | Native area/calibration workflow and metrology accuracy |
| F077 | Real generator patch composed into one selected source ROI; source snapshot/hash, seed/blend provenance, review status | Real CPU GAN API and saved-review reopen; original image hash unchanged; portable subprocess parity | Native one-region drawing/review and generated defect quality approval |
| F078 | 1–32 independently generated regions, opacity/feather/polygon masking, unchanged outside pixels | Two-region real GAN raster equality/reproducibility tests; renderer validates regions and sends source hash | Native multiple-region review and quality approval |
| F080 | Source center + width/height controls → OBB (model integration owner) | `test_rotated_detection_api.py::test_three_box_fitting_modes_preserve_native_coordinates_and_source`; `RotatedBoxFitting.tsx` center selector/click preview | Native center drawing interaction |
| F081 | Source face endpoints + perpendicular depth → OBB (model integration owner) | Same API native coordinates/hash test and face selector | Native face drawing interaction |
| F082 | Irregular source polygon → minimum-area OBB (model integration owner) | Same API bounds/foreign-source rejection; polygon click preview | Native irregular drawing interaction |
| F090 | Saved whole DAG executes through one standalone package call; all model files/hashes bundled | Learned rotation + UNet + measurement real subprocess test; real 8192×5464 source receipt reports parity passed | Native export/reopen and target-machine preflight |
| F091 | Dependency layers; bounded CPU workers/device semaphore; preserved ContextVars; stable ordered steps and copied predecessor state | Real network overlap tests: capacity2 overlaps, capacity1 caps; active execution guard; API config affects actual explicit CPU run engine | GPU reservation integration/owned hardware measurements (default GPU1 retained) |
| F119 | Active saved version used for single/batch pre-inspection and result identity | Saved-version/API/restart tests in `test_flowchart_flow_contract.py`, saved graph/export parity; existing inference history controls | Native stage5 → stage6 single/batch persisted result readback |

## P01 review and integration repairs

- Reviewed source masks/classes, required-structure zero evidence/count/area/gray semantics, and original/corrected OCR position rules. Focused P01/flow group: **78 passed**.
- Reproduced legacy remote worker `min_defect_area_px=0` rejection and ONNX/TorchScript tiled foreground shape/area regression. Restored nonnegative legacy minimum and the public foreground-map helper contract while inspection explicitly retains all classes. Focused sequential remote + standalone tile parity: **4 passed**.
- Added native source class-rule controls and saved OCR/Blob evidence display. SSR verifies original `A0`, corrected `AO`, 12.25mm path and class gray measurement independently.

## New workflow contracts

- `preprocess.params.operation = learned_rotation`, task `rotation`, completed source-verified model catalog and package model discovery.
- `preprocess.params.operation = fitted_roi` consumes an upstream oriented source polygon, fits/crops the original raster and retains inverse source mapping.
- `measurement` consumes one model result; `paths` are explicit original-image polyline or four-control Bezier points. Optional calibration is `{unit:mm, mm_per_pixel_x, mm_per_pixel_y, source_size:[W,H]}`. Invalid/nonfinite or mismatched source calibration is rejected.
- Pipeline `execution_config={max_workers:1..8,device_slots:1..8}` defaults to1. `/api/flowchart/execution-resources?device=cpu` and PUT `{device:cpu,device_slots}` use the persistent CPU execution engine. Changes during execution return409; GPU capacity is not expanded without scheduler reservations.
- `/api/defect-gan/prepare` copies explicit rows/native images into project `dataset/defect_gan/<uuid>`; top canonical source mapping and copied/original hashes are verified on reopen. `/datasets` discovers only active-source owned prepared data. Training passes canonical source plus prepared family dataset. UI uses this path, device selector and measured AutoDL workbench.
- Source composition uses `/source-preview` → generation `{source_image_path,source_sha256,regions,count,seed,device}`. Candidates preserve full original raster dimensions and are `synthetic_unreviewed`; adoption requires explicit human decisions. Original/snapshot changes reject review/adoption. CLI generation package supports `--source-image`, `--regions`, `--source-sha256`.
- F061 contribution: catalog includes recorded threshold settings, training label revision and parent job; picker shows threshold/lineage badges. Authenticated shared catalog enumerates only project roots.

## Verification runs

- Current geometry/GAN/catalog/specialist flow focused group: **31 passed**, 1 warning, 12.14s.
- Shared-account/catalog/execution-target + three-mode OBB group: **18 passed**, 1 warning, 6.32s.
- Flow renderer Node suite: **30 passed**; GAN parser/provenance service suite: **3 passed**.
- `npm run typecheck`: passed. `npm run build:renderer`: passed (existing bundle-size/dynamic-import warnings).
- Historical broad P06 gate: **1302 passed, 14 skipped, 3 failed** (OCR archive/background and Patch search issues owned by other workers). This run is not a green final gate. Model integration later broad gate had **1327 passed,15 skipped,1 in-flight test expectation failure**; focused native GAN test expectation was corrected from32 to64 because source is64×64.
- Read-only actual input: **8192×5464**, source SHA256 `66d3216e10619e12a56d105eb83da2189c2aeae102e50238e29076b1c28bb9e2`. Native fitted ROI **48×24**, edge measurement64px, real standalone flow parity passed, original hash unchanged. Private receipt is retained in the local QA artifact store; private machine paths, images and file names are not tracked.

## Review handoff

Independent P04 review identified orphan automated-training journals remaining active after process restart and stale completion callbacks on source/project switch. Model integration owns their tests/fixes. Completion here does not imply native acceptance or model-quality approval.
