# Model workflow completion

## Delivered behavior

- Capability inventory advertises the actual remote runner support for all ten model families.
- All six specialist workbenches submit to the selected local/server execution target. Visible model settings, prepared dataset path and verified parent selection stay attached to that submission.
- Execution ID controls remote status/cancellation; saved model ID controls reopen/evaluation/flow selection. Task receipts retain prepared family input even after source transfer.
- First-use preparation checks the selected execution environment and preset, dependencies, observed pretrained bytes and prepared/parent compatibility without registering a job, creating versions, or downloading models. File/hash observation is separate from model loading and quality acceptance.
- Basic-model preparation checks the current review policy and saved train/validation split. Team guidance/class mismatches block creation of training bindings.
- Scoped model handoff offers an exact compatible existing node or a new unconnected node, preserving the existing DAG. The user still connects, validates and saves the flow. GAN remains in generation/review/adoption.
- Bounded automated candidate search remains local only. A selected server cannot silently launch local search; the screen and README state this boundary.
- Defaults remain DINOv3 for classification/segmentation/patch classification and YOLO for detection. Alternative supported architectures remain explicit.

## Evidence

- Backend tests cover ten-family capability parity, target-aware dependency/weight preflight, project-owned prepared input, specialist portable-parent hash/signature validation and review eligibility.
- Real CPU loopback worker/coordinator regression covers OCR initial training, transfer/reopen and parent-to-child continuation. This verifies the remote process/transfer path without claiming acceptance of a physical server or GPU.
- Renderer tests cover all six remote config payloads, exact execution cancellation, local selection, model handoff scope, preservation of an existing DAG, and rejection of implicit local automated search.
- Broad and post-native-QA regression results are recorded in `TEAM-WORKFLOW.md`.

## Family acceptance matrix for this increment

| Family | Common workflow implementation | Native real-data acceptance in this increment |
| --- | --- | --- |
| Classification | Target/preparation/parent/task/flow contracts covered | Selector/preparation observed; supplied NG-only source reports the required task-specific OK data. New training/evaluation not claimed. |
| Segmentation | Target/preparation/parent/task/flow contracts covered | Supplied images, team review and preparation/preflight/restart checked; new completed model not claimed. |
| Detection | Target/preparation/parent/task/flow contracts covered | Family selector and preparation navigation checked; new completed model not claimed. |
| Anomaly | Statistical refit and learned synthetic route remain distinct | No new normal training cohort supplied; training not claimed. |
| Patch classification | Prepared source mapping and DINOv3 continuation covered | New native training/evaluation not claimed. |
| OCR | Prepared text truth, exact IDs and portable parent covered | Real loopback API training is separate; supplied defect images do not provide verified OCR strings. |
| Rotated detection | Project preparation/config/task/flow covered | Supplied boxes do not establish manually verified rotation truth; new native fit not claimed. |
| Rotation | Angle preparation/config/task/preprocess covered | Verified correction angles are a separate prerequisite. |
| Enhancement | Paired/prepared input/config/task/preprocess covered | Independent improvement targets are a separate prerequisite. |
| Defect GAN | Crop preparation/config/task/generation/adoption covered | Generated defect quality/adoption and new native fit not claimed. |
