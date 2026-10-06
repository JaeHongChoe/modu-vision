# Source CI and authentic model qualifications

The public Linux CPU source job runs the renderer/type/build gates, selected CPU
contract/recovery regressions, and browser tests except `@owned-model`. It uses a
read-only repository token and supplies no GPU, deployment, signing or private
model credentials. The existing Windows native workflow excludes the same model
tag; workflow source review is not proof of a Windows execution.

The DINO classification, patch-recipe, dense-segmentation, YOLO detection and anomaly-calibration qualifications are
tagged `@owned-model` in both browser and development Electron modes. They train
with an explicitly supplied, existing authentic checkpoint and compare saved
evaluation, whole-flow and offline candidate results. They are not replaced by a
fake checkpoint or an automatic download in public CI.

Run these locally after installing the project dependencies and supplying the
existing checkpoint and Python interpreter:

```sh
export MV_E2E_PYTHON=/absolute/path/to/python
export MV_E2E_DINO_WEIGHTS=/absolute/path/to/model.safetensors
export MV_E2E_DINO_CHECKPOINT="$MV_E2E_DINO_WEIGHTS"
export MV_E2E_YOLO_WEIGHTS=/absolute/path/to/yolo26n.pt
npx playwright test --grep @owned-model
```

The anomaly qualification also requires the existing torchvision default ResNet18
checkpoint in the local Torch cache. Its file hash and every official tensor are
checked independently; the test does not download a replacement.

Use `--project=browser` or `--project=electron` to select one app mode. These tests
create isolated owned data and model outputs; they do not upload input data or
approve a model for production. Synthetic CPU training is software workflow
evidence, not representative quality or remote GPU acceptance.

The public job preserves `ci-browser-selection.json`,
`ci-owned-model-selection.json`, the exact Playwright execution report and
`ci-receipt.json`. The receipt records selected/excluded test names, source-file
and report hashes, and passed/failed/skipped/missing outcomes. Missing or skipped
browser results cannot produce a passing browser lane. An earlier failed gate
records browser execution as `not_recorded`; it does not claim model coverage.
Mixed selections, unexpected executed tests and multiple execution directories
are refused instead of choosing a convenient result.
