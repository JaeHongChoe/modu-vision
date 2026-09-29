# Workflow benchmark and real-data functional QA · 2026-09-29

## 한국어 요약

`/Users/kai/Documents/test_imgage`의 이미지 88장 중 LabelMe 라벨이 짝지어진 80장으로 데이터 등록부터 서버 42의 분할·검출 학습, 테스트 8장 평가, 두 모델 플로우 실행, 6단계 일괄 검사와 단독 ONNX 추론까지 확인했다. 6단계 네이티브 앱의 테스트 8장은 OK 2장·NG 6장으로 처리됐다. 학습은 기능 검증용 각 1 epoch이며, 정상(OK) 원본 표본이 없어 검사 성능이나 현장 적용 승인을 뜻하지 않는다. 상세 근거와 아직 지원하지 않는 기능은 아래에 기록했다.

## Scope and reference

- Source: `/Users/kai/Documents/test_imgage` (88 source images, 80 paired LabelMe images, eight unlabelled images; paired images contain the `Bow` defect class).
- Focus: the desktop workflow and its real-data/remote-model contracts. A one-epoch smoke checkpoint is evidence of functioning training and inference, not approval of model accuracy.
- Workflow's [Historical reference removed] presents data management → labeling → training → evaluation, plus multi-model Flowchart and pre-deployment Inference Center. Its [Historical reference removed] describes model connections and Inference Center validation. The [Historical reference removed] describes auto-labeling, keyword labeling, auto-selection, shape conversion, and image tags/flags. These are product descriptions, not a claim of equivalent implementation here.

## Real-data evidence

| Stage | Exercise | Result |
| --- | --- | --- |
| 1 Data | Import 88 source images and resolve 80 paired LabelMe images; save 56/16/8 train/val/test split | Desktop import and backend split readback succeeded. Flat LabelMe detection import/split now follows the same saved partitions. Class distribution now separates 80 labeled images from 111 `Bow` object annotations; its object share is 100%, not a misleading 139% of images. |
| 2 Label | Open source `Bow` annotation; draw/save/erase/reopen; shape-conversion, AutoSelector and zoom QA | The isolated native app verified brush → save → eraser → save, and a converted mask survived image navigation/reopen without a black overlay. AutoSelector on a QA copy created a new polygon, and Undo restored the previous count without saving. Zoom 9% → 10% → 9%, 1:1 → 100%, and Fit → 9% worked; the physical `F` key under Korean IME also returned 100% to 9%. A rotated box saved at angle 15° and its edited coordinate persisted after navigation/reopen on the isolated copy. |
| 3 Train | Server 42 selected profile, GPU 2 (NVIDIA L40S), isolated remote worker; full 80 paired images | Segmentation `job_1790683152_928a7d` and detection `job_1790683405_64398c` completed one epoch each. A separate QA-only 30-epoch segmentation run `job_1790687799_ff8b6c` was canceled through the native UI while running; UI returned STOPPING → ABORTED and enabled Start again. Remote status/receipt were aborted, its process ended, and GPU 2 returned to 0 MiB without disturbing GPU 0/1 workloads. |
| 4 Evaluate | Force evaluation of each completed checkpoint | Both selected **eight test images**, not the 16-image validation split. Paths in saved predictions are under `/test/`. The detection UI now separates image OK/NG at threshold τ, highest-box class agreement without τ/IoU, and mAP@IoU 0.5; its report uses the same distinction. |
| 5 Flowchart | Verify detector and segmentation checkpoint provenance, run detector → ROI segmentation and a parallel detector + full-image segmentation graph on one real-derived test crop | Both API runs returned 200. The ROI chain passed five steps with one ROI; the parallel graph ran both models, joined at the decision, routed NG to `output_ng`, and skipped the OK/REVIEW outputs. Editable graph UI QA covers connections/branches/delete/drag/zoom. A native RUN of the saved detector flow on real `ng_0007` returned NG, four ROIs and executed-node evidence. |
| 6 Inference Center | Saved source-scoped detector flow and test-split batch inspection | In the isolated native app, all 8/8 original test images completed: OK 2, NG 6, REVIEW 0, error 0, unrun 0. The operator clicked all six result filters and inspected an NG row with original preview, four ROIs, four defect ROI thumbnails and four node records. The Stop button disappeared on completion. |

Native Stage 6 evidence is saved locally at `models/qa-full-gpu-20260929/ui-control-evidence/stage6_batch_8of8.png` (gitignored because it displays customer imagery and local paths). The eight UI rows were:

Additional ignored local evidence includes `ui-control-evidence/cancel_job_1790687799_ff8b6c/` with the native aborted-state screenshot and local/remote receipts, `ui-control-evidence/final_detection_onnx_256_20260929/` with the exported model and real-image JSON result, and `ui-control-evidence/detection-evaluation-report.html` rendered from the actual evaluation JSON. These customer-data artifacts are not included in Git.

| Original image prefix | Verdict | ROI / defect ROI |
| --- | --- | ---: |
| `ng_0006` | OK | 0 / 0 |
| `ng_0007` | NG | 4 / 4 |
| `ng_0008` | NG | 3 / 3 |
| `ng_0009` | NG | 7 / 7 |
| `ng_0010` | NG | 1 / 1 |
| `ng_0011` | NG | 1 / 1 |
| `ng_0012` | NG | 5 / 5 |
| `ng_0023` | OK | 0 / 0 |

The segmentation checkpoint is 31,461,570 bytes with SHA-256 `ee79b3d8783cc9aa0c9f3a463bb88516b81bce2d9dce02cc86a982af15612b50`. The detection checkpoint is 76,020,922 bytes with SHA-256 `2a60cd7d7ddaa8b15c4950dd9217dbd11a025c8186e1e63aad52996edb73b004`. The remote artifact manifest verifies the returned weights. Receipts and full outputs remain in ignored `models/job_1790683152_928a7d`, `models/job_1790683405_64398c`, and `models/qa-full-gpu-20260929`.

## Functional corrections from this pass

- Flat LabelMe polygons and two-point rectangles can prepare portable COCO detection data without changing the source; explicit Studio `OK` records remain empty detection targets.
- Detection category IDs are mapped to dense foreground labels; trained head size includes the background class, and legacy checkpoints reconstruct from their saved predictor weight shape.
- The Stage 1 detection class card distinguishes object annotation counts from labeled image counts and uses object totals for its percentage.
- Detection train, validation, and evaluation now share the same class order, even when a class is absent from a split or COCO category IDs differ. A reproduced `Scratch`/`Bow` split had previously relabeled validation `Bow` as `Scratch`; the focused regression now preserves `Bow=2` in both splits.
- Detection and segmentation evaluation prefer the saved test partition. A forced evaluation previously read validation despite an eight-image test split.
- Stage 4 had presented detection highest-box class agreement as if it were the same as thresholded image verdicts and location mAP. The real test eight had 6/8 top-class matches, eight image-level false negatives at τ=0.5, and mAP@IoU 0.5 of zero. The UI, sample detail, and HTML report now label these as separate measures.
- Stage 5 supports a bounded executable graph: model nodes can be added, moved, connected, and deleted; multiple model paths can join a decision; OK, NG, and REVIEW outputs are routed by explicit edges. Invalid graphs are rejected before saving/running. Existing saved linear graphs still load.
- Stage 5's detector ROI padding is now used by the downstream crop. The `max_flaws_allowed` rule is blocked for segmentation because counting one full-image result as one flaw could produce a false OK.
- Stage 5 ROI detail now uses its producing model node's saved threshold. The real detector flow case `14.8%` score / `10.0%` threshold shows `+4.8%` rather than an unrelated `45.0%` threshold and negative delta. Editing the threshold after a run clears the old result.
- Stage 6 adds source-scoped, paginated, sequential real-image batch inspection with model provenance verification, per-image identity checks, stop state, progress, filters, a preview, and per-node evidence.
- Stage 6 invalidates an active batch and clears its old result when annotations, the split, or the flow context change. It discards late remote responses from the old source before running another image.
- The native Stage 6 now explains that an existing remote model executes on its training server, while the header Compute selection chooses the target for a new training job. It also explains that a standalone ONNX package exports one model, without the saved Stage 5 ROI chain or decision rules, and that its threshold must be compared with the flow threshold.
- The standalone detector was re-run on `sample_00005.png` at threshold 0.1 after the exporter fix: `NG`, score `0.2089`, predicted class `Bow`, and localized boxes in original-image coordinates. The UI-selected export had already returned `NG/0.2089`; the final regenerated client adds class and box detail. The default package threshold remains 0.5, so its verdict can differ from the saved flow unless aligned.
- The final native app exported both TorchScript at 224 px and ONNX at 256 px. The TorchScript self-test and real `sample_00005.png` inference succeeded; ONNX self-test and the same real-image inference returned `NG`, class `Bow`, score `0.2089`, and 37 boxes at threshold 0.1. The code-copy and synthetic forward benchmark buttons also responded. The L40S benchmark's 5.92 ms mean is model-forward time on synthetic input, not camera-to-decision production latency.
- The standalone detection runtime now interprets foreground labels 1..K correctly; multiclass classification sums the probabilities of all defect classes. Standalone anomaly export is blocked until it can apply the trained PaDiM/PatchCore statistics rather than score raw extractor features.
- The standalone detector now includes foreground class names and original-image boxes in its JSON result, and classification recognizes Korean `정상`/`양품` as normal classes. This closes a mismatch with the Stage 5 verdict logic.
- Stage 2 QA found and corrected shortcut, shape-conversion, saved-mask readback, and isolated desktop-instance issues. The rebuilt native app confirmed the physical `F` shortcut under Korean IME.
- Stage 3 labels the selected physical GPU profile separately from the worker's local `cuda:0` device number, so a GPU 2 reservation does not look like a GPU 0 assignment.

## Functional limits and next decisions

- This dataset is NG-oriented. There is no representative OK cohort, so false-positive behavior, operating thresholds, and production acceptance cannot be established from this pass. One epoch was deliberately used to prove the complete path, not to optimize performance.
- The current graph has supported node types and bounded model branching; it is not a general-purpose unrestricted workflow engine.
- Workflow also advertises [Historical reference removed], [Historical reference removed], and [Historical reference removed]. Their full equivalent behavior was not implemented or validated by this QA pass. The most useful next work for this dataset is an explicit OK set, label review/flags, and a repeatable candidate-versus-current evaluation before deployment.
- Batch results are currently held in the running app and disappear after restart. An exportable, source- and model-bound inspection history would improve real operator use; persistence was not implemented in this pass.

## Verification gates

- Backend: 587 passed and 2 skipped in the full suite using the Python environment with ONNX installed. After the Stage 4 report wording change, the focused report/class-mapping/exported-inference group passed 17 tests.
- Frontend: 19 relevant verification scripts passed, including graph editing, batch provenance, evaluation grain labels, labeling, dataset import/split, and remote compute; the focused remote-compute UI check passed 10 tests. `npm run typecheck`, `npm run build`, and the isolated macOS app package build completed.
- Native app: the final rebuild confirmed the corrected 80-image/111-object Stage 1 card, separated Stage 4 metrics, saved Stage 5 run and 10.0%/+4.8% ROI detail, and the server-42 cancellation return. Original source files were read-only; labeling experiments used isolated copies.
