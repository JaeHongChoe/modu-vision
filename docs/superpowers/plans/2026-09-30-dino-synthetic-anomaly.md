# DINOv3 synthetic anomaly workflow

## Agreed intent

Add a normal-data anomaly recipe to the existing anomaly task. Preserve native
image patches, synthesize defects from normal training images, learn a binary
head on frozen DINOv3 features, and inspect full images in bounded batches.
Actual defects remain independent evaluation inputs. The output heatmap is a
patch score or explanation, not a trained pixel segmentation mask.

## Model and data contract

- `task=anomaly`, `anomaly_method=dino_synthetic`.
- Default verified DINOv3 S/16; B/16 and L/16 are explicit options.
- Pool CLS plus the mean of spatial tokens; train a binary MLP using BCE.
- RGB input is [0,1], with normalization once inside the encoder adapter.
- Native patch size 256, stride 128; tail patches cover the right and bottom.
- Equal normal/synthetic pairs per source and epoch-dependent deterministic seeds.
- Training contains only normal-labelled source images. Calibration uses only
  independent normal validation images and image maximum scores.
- Report synthetic training metrics separately from real validation metrics.
- Save full frozen features, head, geometry, input semantics and source receipts.
- Existing parent initialization remains distinct from exact optimizer resume.

## Implementation sequence and ownership

1. Engine: new `anomaly/dino_synthetic.py` and `synthetic_defects.py`; regression
   tests for normal-only gating, changing epoch samples, native coverage,
   cancellation, bounded inference batches and offline state reconstruction.
2. Operator controls/API: anomaly method selector, DINO backbone and geometry
   settings; validated request fields; compatible parent model identity and
   remote readiness. Preserve the existing project task and saved split flow.
3. Runtime: trainer progress and cancellation, native evaluation and inference,
   flow map semantics, packaged runtime reconstruction and standalone export.
4. Adjacent patch corrections: bounded full-image batches and a standalone
   runner preserving patch coordinates, normal class and maximum defect score.
5. Verification: focused regressions, backend suite, renderer typecheck/build,
   genuine local DINO smoke and readback of saved model and export results.

## Completion evidence

The native app must expose the new recipe. Training produces a saved anomaly
checkpoint and epoch history; cancellation has terminal state. Reloaded models
run full native images and preserve map dimensions. A packaged runner agrees
with in-app scoring on a known image. Normal-only validation does not claim
recall or segmentation quality. Remote support is checked against the worker
contract; no new GPU job is submitted merely to prove a UI capability.

## Integration constraints

No private dataset paths, host addresses, organization names, external source
code copies or pretrained weight binaries enter the public repository. Adopt
the algorithmic ideas in the existing product conventions. All mutations are
made in the existing linked worktree, then integrated into main after checks.

## Verified implementation

- Added the recipe, S/B/L controls, normal-only source guards, native patch
  batches, procedural defect synthesis, heldout normal calibration, full saved
  state restoration, parent initialization and selected-backbone remote contracts.
- Connected trainer, nullable telemetry/history, real evaluation, flow score maps,
  compressed original-size scores and TorchScript/ONNX standalone packages.
- Corrected supervised patch native-grid/export coverage and rejected unsupported
  or conflicting export task identity.
- Final backend gate: 1206 passed, 11 skipped, 108 warnings. Typecheck/build passed;
  model option tests 14/14, cancellation/recovery 17/17, packaging 30/30.
- Native generated-normal functional QA: 12 epochs completed, persisted history
  and restart recovery. Real job manager cancellation released its reservation.
- Actual defect input: 8192x5464 native image, 2646 patches. Independent package
  scores agreed within 1e-5 on a native region; both export formats kept verdicts
  and coordinates. Source hashes were preserved.
- Actual normal-data training and quality approval remain unverified. No new
  remote GPU training was submitted; optimizer resume and token attribution were
  not added. Full public scope is recorded in `docs/feature-completion.md`.
