# Real-data functional QA · 2026-09-29

This records an early isolated CPU workflow smoke pass. The later [full-data functional QA](QA_WORKFLOW_FUNCTIONAL_2026-09-29.md) and [current feature QA](QA_WORKFLOW_PARITY_2026-09-30.md) cover subsequent model and inspection work. Source paths, customer image names, job identifiers, and artifact digests are kept in private QA receipts.

## Desktop workflow

An isolated Electron session imported 88 customer-provided inspection images: 80 paired LabelMe annotations and eight unannotated images. The saved split showed 56 train, 16 validation, and eight test images. The UI warned that only defect annotations were present, so overkill and production OK/NG quality could not be established. Stage 2 displayed an existing defect annotation. In Stage 3, a selected remote profile passed a live SSH/runtime/weight probe on an NVIDIA L40S GPU and enabled training. This first desktop pass did not submit GPU training.

Stage 5 was checked while switching recipes: classification changed to an anomaly inspection node; detection opened a detector-only flow. Saved graphs were scoped by dataset and recipe. Detector ROI inspection required a second model.

## Isolated remote CPU run

The 80 paired annotations were prepared into a portable 160-file snapshot. Local and remote SHA-256 values matched. An eight-pair subset was used in an isolated Docker workspace with a QA-only 2 CPU/8 GiB cap. One segmentation epoch completed and returned a hash-verified checkpoint. The same job completed evaluation on two test images, a single-model Stage 5 flow with preview, single-image inference, benchmark, TorchScript export, and ONNX export. A separate job was canceled after entering `running`; both receipts ended `aborted` with no checkpoint.

These results establish operation wiring and artifact return. They do not establish defect-detection performance. A representative OK set and separate GPU-training evidence are required for those claims.
