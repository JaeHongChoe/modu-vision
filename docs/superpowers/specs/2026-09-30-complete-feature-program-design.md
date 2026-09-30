# Complete industrial feature program design

## Accepted intent and scope

Implement every audited capability, make it usable in the native application, and preserve the user's functional workflow from supplied images to saved inspection results and independent deployment. `docs/feature-program.json` contains F001–F123 and is the authoritative scope. No item may disappear when implementation is difficult. Existing implementations also need acceptance evidence.

The user requested planning and full execution in this conversation. Their previous constraints remain: DINOv3 pretrained classification/patch/segmentation, YOLO pretrained general detection, configurable remote servers, polished native workflow, and no benchmark-company names or private research/data in public Git.

## Architecture

Keep Electron/React, FastAPI, project stores, and PyTorch. Add small modules for segmentation evidence/rules, foundation-label providers, data statistics/model flags, automated training trials, evaluation grains/plots, compute allocation, CLI, hardware export, and operational pipelines. Reuse dataset snapshots, job lifecycle, checkpoint lineage and signed package manifests rather than bypassing them.

There are ten work packages, P01–P10, followed by integration and end-to-end acceptance. Every feature is assigned exactly once; shared interfaces are documented in the implementation ledger. The complete scope is retained when hardware or source truth is unavailable; record implementation and verification separately.

## P01: decision and data correctness

- Preserve every segmentation class, its class name, mask, probability, area and original coordinates through tile/ROI inference, Blob rules, saved inspection and complete-flow package execution.
- Preserve previous binary defaults, while new rule modes support defect presence and required normal structure. Include class selection, count/area bounds and mean grayscale bounds. Zero evidence must be distinguished from a measured zero count.
- Dataset class filtering and distribution count all annotations for the active label set, deduplicating each image within a class. Background-only and multiple-class inputs must not produce invented defects.
- OCR rule evaluation keeps original transcript and corrected transcript separate and supports per-position allowed/fixed characters with an explicit correction map.

## P02: foundation labeling

- One candidate request supports text, positive image ROIs, negative image ROIs, point/box prompts, source labelset ID/version, device, score and size bounds. Returned candidates contain source, geometry, score and provenance.
- Use actual foundation-model mask generation and semantic image/text providers. Dependencies/weights and device availability are explicit; do not label OpenCV fallback as foundation inference.
- Support long prompt segmentation, multiple positive/negative examples, mask/polygon output, batch tasks, cancellation, small-label training/refinement, review and selective acceptance.
- A missing provider is an actionable prerequisite failure, not synthetic model output. Existing deterministic tools remain explicitly named alternatives.

## P03: data and shared project versions

- Add tag colors, labelset/model user flags, labeled/unlabeled and assignment distributions, selectable statistics, evaluation labelset/version binding and model-grouped history.
- Import/export supported raster masks with explicit class mapping, orientation and bit-depth handling. DICOM windowing and metadata must not silently corrupt source pixels.
- Shared projects have authenticated users, roles and optimistic edit conflicts; reviewer names are attributable. Separate model deployment approval from arbitrary user flags.

## P04: all model families and automated training

- Classification, patch classification, segmentation, object detection, oriented detection, OCR, rotation, anomaly classification/region analysis, GAN and enhancement each expose data preparation, training/cancel, evaluation, graph compatibility and export.
- Patch classification uses DINOv3. Rotation is a learned angle/direction task, not fixed image warping. Retain explicit anomaly map semantics.
- Automated trials search compatible structures, hyperparameters and augmentations within a declared budget, record candidate objective and latency, and select a measured winner. Parent reuse preserves checkpoint and training configuration provenance.
- Quantization uses actual calibration/conversion and metric comparison. Device/latency optimization must record measured tradeoffs, not just accept flags.

## P05: evaluation

- Store matched/unmatched objects, all per-class pixel errors and aligned OCR character edits. Images may be returned for any failing secondary class.
- Probability/area distributions, ROC points and OBB IoU/angle scatter are inspectable and linked to the source images.
- Compare same-type and different-type models using the same immutable image set. Common image verdicts are comparable; task-specific metrics retain their own meaning.
- Every run binds evaluated data, labelset/version, checkpoint, preprocessing and thresholds. Re-evaluation does not overwrite its parent run.

## P06: native flow, geometry and generation

- Maintain typed DAG connections, model selectors, branches, aggregation, undo/redo and versions. Provide dependency-aware concurrent execution with bounded resource use.
- Implement fitted oriented ROI, alignment, patch extraction and original-coordinate evidence. Geometry tools cover OBB center/face/irregular input, length and calibrated area.
- GAN generates actual crops and composites them into selected source-image ROIs, including multiple regions, blending masks, seed/provenance and review. Generated data is distinct from collected originals.

## P07: compute

- Route every supported family and labeling/inference task through device allocation, owned remote jobs and cancellation/recovery.
- Reservations include task, host, device identity, memory budget and ownership. Share a GPU only within explicit capacity; expose MIG devices when supported.
- Independent jobs across GPUs and one-model distributed training are separate modes with separate progress/evidence. Unsupported hardware produces a clear error.

## P08: independent engine

- Provide a common CLI and REST contract for input images/labels folders, task/options, output folder, quick/trial training, progress, metrics, cancel and reproducible settings.
- Export model/checkpoint, training config, evaluation and individual predictions separately. CLI output and exit codes must be useful without the desktop app.

## P09: runtime

- Runtime deadlines terminate owned inference execution and return a bounded timeout result; socket or join timeout does not substitute for a model deadline.
- Python and native C++/C# predictor/executor interfaces consume verified packages and expose the same documented input/output semantics. HTTP examples remain a separate integration method.
- Add actual OpenVINO device conversion/execution, CPU/GPU targets, quantization and install/preflight for available Edge targets. Hardware declarations are not proof of board acceptance.

## P10: operations

- Build a persisted pipeline: collect → propose labels → review or explicit policy → train candidate → evaluate same holdout → approval/policy → apply → monitor/rollback.
- Track central project/device state and deployment acknowledgements. Never overwrite a currently approved model simply because a new job finished.
- Services survive desktop closure and record pending/running/cancelled/recovered work. Real camera/PLC/MES acceptance requires actual equipment; generic adapters still need runnable loopback contracts.

## Visual and usability contract

Use the established design system: compact navigation, task-specific workbench, clear prerequisites and inline validation, consistent status/primary actions, separate details drawers, synchronized result viewers, and reversible editing. Avoid exposing internal job IDs as required operator input. Unsupported prerequisites explain what to configure without pretending the feature is unavailable by design.

## Verification and completion

For each ID record implementation, regressions, API/UI wiring, saved/reopened state, real-input evidence and hardware/data limitations. Test correctness before visual polish, then verify the complete native flow. Raw source images and parent checkpoints remain unchanged. Public commits contain no private sources, credentials or competitor references.

The project is complete only when no feature is silently omitted and each deliverable has truthful evidence; functional implementation, dataset quality approval and hardware acceptance are separate states.
