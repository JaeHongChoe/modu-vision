# Real-data functional QA · 2026-09-29

This is the historical full-data workflow pass. The [latest feature matrix](FEATURE_STATUS_2026-09-30.md) and [subsequent QA](QA_FEATURES_2026-09-30.md) supersede its implementation status. Detailed source paths, customer image names, checkpoint IDs, artifact digests, and receipts are stored outside Git.

## Scope

The test set contained 88 inspection images, including 80 paired LabelMe annotations and eight unannotated images. The saved train/validation/test split was 56/16/8. The labeled cohort was defect-oriented without a representative normal/OK group. One-epoch models in this pass were used to verify product function, not model quality or production acceptance.


## Real-data workflow observations

| Stage | Verified behavior | Limit |
| --- | --- | --- |
| 1 Data | Desktop import read 88 images, resolved 80 paired annotations, and reopened the 56/16/8 split. The class card distinguished labeled image count from object annotation count. | Unannotated images stayed gallery-only. |
| 2 Label | On an isolated copy, brush, eraser, shape conversion, AutoSelector, undo, zoom, and rotated-box save/reopen were exercised. | Customer originals remained unchanged. |
| 3 Train | Segmentation and detection each completed one epoch on the selected external GPU. A separate run was canceled through the native UI; remote receipt and device state confirmed abort and resource return. | One epoch does not show useful model accuracy. |
| 4 Evaluate | Both completed models evaluated the eight saved test images. Detection image verdict, top-box class agreement, and location mAP were shown as distinct measures. | The normal cohort was insufficient for overkill analysis. |
| 5 Flowchart | Detector ROI inspection and a parallel detector plus full-image segmentation graph executed with real-derived data. A native run on one original test image returned NG with four ROIs and node trace. | This pass did not prove five distinct model checkpoints or unrestricted graph types. |
| 6 Inference | The native app completed an eight-image batch, displayed per-image ROI/node evidence and filters, and exported single-model TorchScript and ONNX packages with successful smoke inference. | The standalone single-model exports did not contain the whole graph. |

The historical eight-image batch produced six NG and two OK decisions. Because ground truth here is NG-oriented, those results must not be interpreted as model-quality approval. Current saved-flow inspection and durable review behavior are documented in the [subsequent QA](QA_FEATURES_2026-09-30.md).

## Corrections established by this pass

- Flat LabelMe polygons and rectangles prepared detection targets without changing the source.
- Detection train, validation, and evaluation shared a stable class order and foreground-label mapping.
- Evaluation used the saved test partition; reports separated image verdicts from location metrics.
- Stage 5 added a bounded editable graph with model nodes, connections, branches, typed validation, and saved-model provenance.
- Stage 6 gained source-scoped sequential batch inspection, image identity checks, progress, filters, previews, and node evidence.
- Standalone detection results included foreground class names and original-image boxes. Anomaly export remained blocked until trained statistics could be applied.

## Verification boundary

The historical full backend suite reported 587 passed and two skipped, and focused frontend scripts, typecheck, renderer build, and isolated macOS packaging completed. The latest suite results and current implementation limits are in the [subsequent QA](QA_FEATURES_2026-09-30.md). Customer data and full receipts remain outside Git.
