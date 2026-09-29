# Real-data functional QA · 2026-09-29

This records the first isolated CPU smoke pass. The later full-dataset GPU training, editable flowchart, and native batch-inspection results are in [QA_NEUROT_FUNCTIONAL_2026-09-29.md](QA_NEUROT_FUNCTIONAL_2026-09-29.md).

Source: `/Users/kai/Documents/test_imgage`. Scope: workflow and remote-compute behavior, not model quality approval.

## Desktop workflow

In an isolated QA Electron session, the Stage 1 folder picker imported 88 images: 80 paired LabelMe annotations and eight unannotated images. The saved split showed 56 train, 16 validation, and eight test images. The UI warned that the data contained defect annotations only, so overkill and production OK/NG quality cannot be established. Stage 2 displayed an existing `Bow` annotation on the first image. In Stage 3, the selected Server 42 profile passed a live SSH/runtime/weight probe on NVIDIA L40S GPU 2 and enabled the training button. The idle telemetry panel now identifies that selected server instead of showing local Apple Silicon as the intended training device. No GPU training was started.

Stage 5 was checked by switching recipes in the desktop UI: classification changed to anomaly with a matching inspection node; detection opened a four-node detector-only flow. The saved-flow API now retains separate graphs for dataset folder and recipe. Detector ROI inspection remains an optional two-model flow.

## Isolated remote CPU run

The 80 paired annotations were prepared into a portable full snapshot (160 files, 3,255,889-byte archive). Its local and Server 42 SHA-256 both equal `311cdd615b885fa9649eb22b974c82308ada94707e429dc4a853bb500c55807e`. An eight-pair subset of the same source data was used for a functional smoke run in Server 42's isolated Docker workspace with a QA-only 2 CPU/8 GiB cap.

Job `job_1790680649_remoteqa` completed one segmentation epoch and returned a hash-verified 31,460,034-byte checkpoint and local receipt. The same job completed remote evaluation on two test images, a four-step Stage 5 segmentation flow with preview, single-image inference, benchmark, and TorchScript and ONNX exports. Job `job_1790681070_cancelqa` was cancelled after its worker reported `running` at step 1; remote status and local receipt ended `aborted`, with no checkpoint. These results prove operation wiring and artifact return, not defect-detection performance.

## Limits

The desktop Start button was enabled after connection and data checks, but no full-dataset GPU training was submitted. The detector-only flow was exercised through backend tests and desktop template display; no real detector checkpoint was trained with this dataset. All images are NG-oriented, so a representative OK set is still needed for overkill and production acceptance testing.
