# NG_labelme QA remediation (2026-09-29)

This change addresses reproducible defects found while testing the desktop application with manufacturing LabelMe data. The source images and JSON files are never modified by the application tests.

## Behavior corrected

| Area | Change |
| --- | --- |
| Training | Removed the job-manager lock deadlock. Flat paired LabelMe images are converted into defect-centered segmentation image/mask pairs under the job output directory. Unsupported flat-folder tasks return 422. |
| Training cancellation | Stop requests return immediately with `stopping`. The active slot remains occupied until the worker and device cleanup finish; the UI confirms the terminal status by polling and shows a retryable error if confirmation is delayed. Cancellation checks also cover validation and final checkpoint boundaries. |
| Dataset | Three-way split counts are based on files and saved for gallery filtering. The 80 paired images are separated from 8 images without JSON; the latter remain visible in the gallery and are excluded from training splits. AppleDouble files are ignored. |
| Annotation | Brush and eraser pixels are serialized as a PNG-backed mask, merged on save, and restored on reload. The zoom controls target the actual canvas; image loading no longer reruns on each zoom. |
| Evaluation | Missing predictions no longer produce a synthetic zero-underkill curve or calibration. Empty viewports show no score or verdict. Segmentation defect scores now use the same foreground probability as the standalone runtime. Report errors appear in the UI. |
| Flowchart | Execution requires explicitly selected trained checkpoints and a real image. Checkpoint task/weights are verified. Empty results show no fabricated 19-ROI PCB inspection or latency. The output step is marked skipped because no PLC dispatch occurs. |
| Export | A real selected checkpoint is required. Normalization matches training (`RGB / 255`), and the calibration flag requires a validated two-class evaluation. FP16 and unverified native C#/C++ snippets are unavailable; the package contains the Python client and model artifact. |
| Display | Gallery cards use source dimensions rather than thumbnail dimensions. Physical units are hidden until measured calibration is entered. The initial train/validation/test ratio sums to 100%; operator guidance describes validation and export limits. |

## Real-data checks

- Full folder: 88 source JPG files, 80 paired LabelMe JSON files, 8 images without annotations. A 70/20/10 split yields 56/16/8 paired images, with 88 images visible in the unfiltered gallery.
- API integration: eight read-only linked pairs yielded 6 train and 2 validation crops. One MPS epoch completed, evaluation produced two predictions, and a TorchScript package ran standalone inference. Source file SHA-256 values were unchanged. The evaluation and standalone defect scores differed by 0.000014 in the parity run.
- Reproduce the smoke check with `PYTHONPATH=. python qa_artifacts/ng_labelme_api_smoke.py --source /path/to/NG_labelme --work-dir /tmp/modu-vision-qa`.
- Cancellation regression checks: immediate stop acknowledgement, `stopping` until worker/device cleanup, no overlapping job, and an abort after the final training batch. The renderer checks cover pending stop, stop failure/retry, and cancellation before the start response supplies a job ID. Verification: `380 passed, 2 skipped` in the Python suite; `npm run test:training-cancel`, `npm run typecheck`, and `npm run build` passed.
- Real-data cancellation smoke: eight linked LabelMe JPG/JSON pairs, CPU training start, immediate stop acknowledgement, `stopping` while the worker is active, then `aborted` after it exits. All 16 source SHA-256 hashes matched before and after. Reproduce with `PYTHONPATH=. python qa_artifacts/ng_labelme_cancel_smoke.py --source /Volumes/backup/MicoCeramics_QC_데이터/NG_labelme`.

## Acceptance limits

This NG-only dataset cannot establish overkill, OK/NG discrimination, a zero-underkill operating threshold, or production approval. The one-epoch training run is an integration smoke test. PLC communication, native client parity, FP16 conversion, and full-dataset model quality remain unverified. The application must not present any of these as accepted production results.
