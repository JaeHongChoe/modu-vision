# P09 — runtime devices, deadlines and native SDKs

Baseline: `a4d3610`. No commit/push, costly GPU job, live service change, or write to the original image dataset was performed. Temporary OpenVINO/NNCF and .NET build tools are isolated under `/private/tmp`; no global runtime installation is implied.

## Evidence boundaries

The paths below are implemented and integrated. Actual CPU inference and native compilation are verified. Production model quality, native desktop interaction, CUDA/Jetson and Intel GPU/iGPU/NPU field acceptance are separate pending gates. Deterministic CPU network weights and generated training rasters are integration fixtures and do not establish industrial accuracy.

| ID | Implemented path | Evidence | Remaining acceptance |
|---|---|---|---|
| F043 | Saved whole-DAG runtime options, bounded threads, measured source/optimized latency and artifact size; CPU/CUDA Edge profiles | Real OpenVINO conversion; edge target/dependency/hash tests | Native UI and target board performance |
| F044 | Authentic FP32/FP16-weight IR and NNCF calibrated INT8; distinct calibration/heldout sources; preserved source weights | Actual CPU INT8 convolution model, quantized operation count, finite heldout drift, saved conversion metrics | Production task accuracy and operator approval |
| F092 | Hard wall time deadline includes initialization and every node; owned process group is killed; late verdict becomes REVIEW | Child process/pid termination, Python/C++/C# 1ms deadline, cancellation regression | Windows process-tree and GPU timing |
| F093 | Embedded CPython C ABI and C++ Predictor/Executor, pre-import checksums, complete JSON evidence | Actual C++17 shared-library build, UNet and rotation → segmentation → calibrated measurement parity; actual GAN composition | Native UI and Windows/Linux ABI builds |
| F094 | C# Predictor/Executor P/Invoke against same embedded native library | Actual .NET8 build, complete result parity, deadline termination | Windows/Linux .NET/native deployment |
| F095 | Python Predictor/Executor with strict JSON input and persisted options | Actual isolated whole-flow execution and reopened package | Native workflow acceptance |
| F096 | One native/Python call executes all saved dependencies/ROI/branches; separate generator Executor contract | Full DAG and GAN seed/pixel/source-provenance equality | Production family chains |
| F097 | CPU PyTorch and OpenVINO CPU inference, explicit CPU thread bounds | Real saved UNet/OCR full flow, process deadline and source raster tests | Industrial accuracy/latency |
| F098 | Explicit CUDA device, same saved weights/DAG, actual availability check, Linux CUDA Edge profile | Unavailable device refusal and Linux/x86_64/arm64 declarations | Actual CUDA GPU execution pending |
| F099 | OpenVINO IR for all connected model tasks; actual devices and supported plugin properties; CPU/GPU/iGPU/NPU selection, no CPU fallback | Real CPU compiled inference; unavailable NPU refusal; OCR height/width and inverted letterbox regression | Intel GPU/iGPU/NPU hardware execution pending |
| F100 | Linux arm64 CUDA/Jetson package uses matching vendor PyTorch/JetPack environment, strict target and dependency checks | CUDA/Jetson profile tests; CPU Edge install/run/service tests | Real board installation/execution pending |
| F101 | Original checkpoints and saved DAG are reusable on PyTorch CPU/CUDA; portable IR runs on available OpenVINO target without graph conversion | Exact package hashes/parity, explicit device overrides/preflight, reviewed device fixation | Target architecture/dependency/hardware acceptance |

## Explicit precision acceptance

A completed conversion candidate records exact original package/checkpoint/calibration/heldout hashes, source fingerprint and saved split identity. Approval requires a named reviewer, meaningful reason, explicit boolean holdout review, finite numeric maximum absolute drift, every current active model approval revision and its exact checkpoint SHA. Changed source/split/candidate/active approval is rejected. The API creates a fresh approved copy, records `runtime_acceptance.json`, reseals the manifest and writes a separate manifest-bound external policy. Candidate and prior releases stay unchanged. Conversion never inherits model or precision approval. Managed/independent services require the reviewed device and matching acceptance hash; unconverted packages cannot report ready on OpenVINO.

## Contracts and dependencies

- Export `runtime_config={device,cpu_threads,deadline_ms}` is checksum-bound. Python and native Predictor default to a finite 300000ms budget for legacy packages without a saved deadline. Export UI defaults to30000ms.
- C ABI `mv_create`, `mv_execute`, `mv_predict`, `mv_release`, `mv_free`; C++ RAII wrapper; C# P/Invoke; no HTTP transport. Native initialization/package verification precede the per-image budget. The owned execution subprocess is terminated on timeout.
- Generator native request requires a fresh `output_dir`, integer count/seed, optional source/hash/explicit regions. Actual generator pixels/provenance remain synthetic_unreviewed; timeout publishes no candidate directory.
- `/api/export/runtime-capabilities` returns actual Torch/OpenVINO devices. Project-owned optimization journals support cancellation/reopen; orphan workers become interrupted.
- OpenVINO/NNCF are optional conversion/runtime dependencies. CPU FP16 means compressed weights with explicit FP32 CPU compute, not a claimed FP16 CPU kernel. Original Torch statistics remain for anomaly families while actual NN features/logits use compiled IR. GPU/NPU plugin settings use supported properties.
- An actual macOS Arrow/OpenVINO native library collision was reproduced. Owned image-inference/conversion workers disable the unused optional Arrow import before sklearn/pandas initialization. Direct unsafe in-process OpenVINO initialization reports the prerequisite. No host environment mutation or CPU fallback is used.

## RED → GREEN and verification

- Explicit numeric/boolean approval request reproduced coercion RED; strict validators GREEN.
- Native create imported a tampered bridge before its later hash check (marker executed) RED; stdlib pre-import checksum/path bootstrap GREEN.
- Unconverted OpenVINO package could become ready RED; package/managed/independent runtime guards GREEN.
- OCR IR used swapped saved dimensions and incorrect generic preprocessing RED; saved height/width and exact inverted letterbox transform GREEN. Numeric artifact comparisons tolerate finite differences <=1e-4 while discrete verdicts/masks/labels remain exact.
- Actual native SDK/API/profile group:9 passed,72.18s (C++ and C# both compiled/executed).
- Real OpenVINO CPU/INT8/approval group:6 passed,52.31s; actual OCR focused gate:1 passed,20.91s.
- Real C++ multifamily + GAN seed/source/pixel/deadline group:2 passed,29.68s.
- Service/package/edge/API group:63 passed,2 optional OpenVINO skips,53.53s.
- Renderer flow suite:30 passed. Runtime API/SSR explicit-review suite:2 passed.
- Typecheck and renderer build passed; existing chunk-size/dynamic-import warnings remain.

## Heldout workflow and original source readback

Conversion now stores checksum-bound original and compiled full-DAG outputs for every selected heldout image, including ROI/branch/rule/measurement effects. A saved index binds exact source image SHA and each complete output hash. The project-scoped heldout-results route verifies completed candidate integrity before readback. Native approval UI presents original and converted ROI/mask/verdict evidence side by side; precision acceptance additionally binds the heldout workflow receipt hash. Missing/changed measured workflow evidence is rejected.

The missing heldout output file was reproduced RED; actual CPU OpenVINO/approval group GREEN:8 passed,56.06s. Final actual CPU OpenVINO/OCR/INT8/precision-approval group:9 passed,77.46s. This includes a genuine INT8 UNet full-flow run with distinct actual calibration pixels, quantized operations, saved whole-DAG holdout comparison and reopened inference. Final native Python/C++/C# + mixed DAG/GAN group:7 passed,85.85s; actual C# facade compiled successfully. Latest typecheck/renderer build and API/SSR suite passed; `git diff --check` passed.

Read-only original input:8192×5464; SHA256 `66d3216e10619e12a56d105eb83da2189c2aeae102e50238e29076b1c28bb9e2`. Actual C++ full-DAG execution matched Python; a1ms deadline terminated the owned process and returned REVIEW; original SHA remained unchanged. Private receipt: the original-input native SDK acceptance journal. The original file name/pixels are not tracked.

## Independent P10 integration review

Reviewed bound immutable flow selection, all-node checkpoint approvals, new version save and recipe/active-pointer restoration on failed apply. Identified that an arbitrary OpenVINO inference device could make an unconverted Torch package report ready. Runtime preflight now refuses this before apply; operations owner also rejects unconverted automatic-cycle targets. Field-agent staging owner carries the identical runtime acceptance hash into its external policy. No automatic precision approval is inferred from model approval.


Final integration review also reproduced that independent service CPU selection was not forwarded to the actual package call, so a saved CUDA default could override the selected CPU. The service now passes its actual acknowledged device for every inspection. Final independent/managed-service and optimization API group:23 passed,20.81s.

The optional conversion dependency recipe is `backend/requirements-openvino.txt`. Set `VISION_OPENVINO_PYTHON` to an interpreter containing the package's base runtime dependencies plus OpenVINO/NNCF when the desktop backend interpreter lacks them. Discovery, conversion and optimized inference use that explicit interpreter; unavailable dependencies are reported rather than silently substituted.

Projectless runtime controls return explicit409; focused optimization API group:3 passed,4.92s.

Shared authorization review found that the new precision approval POST path matched generic export permission, allowing a trainer role (HTTP200 RED). The middleware owner classified this exact path exclusively as review permission. Shared role regression GREEN:7 passed, including labeler/trainer403, reviewer/owner200 and authenticated reviewer identity binding.

## Native desktop DINOv3 export parity regression

Actual saved DINOv3 segmentation checkpoint SHA256 `e42ba14916f88fab764345b1e048052f1a317b0f314eeef970d54b118f4cec51`, fixed64×64 ROI, actual owned source raster SHA256 `19d002393c157d6d4643a068b22bf4e5374beecccc563134dd1658b922a94793`: CPU reference threads5 versus package threads1 produced maximum probability drift1.7881393e-7. Verdict, source-coordinate masks and areas matched; compressed float32 probability strings caused false mismatches in `execution_steps[2].artifacts` and `crops[0].segmentation_classes`.

Decoded float32 raster comparisons now require finite values with absolute difference<=1e-6. Integer masks, areas, verdicts and branch evidence remain exact; malformed shape and nonfinite arrays fail. Existing scalar numerical comparison tolerance<=1e-4 remains separate. Regression RED→GREEN also proves identical NaN rasters cannot pass. Fresh saved-DAG export parity and an actual compiled C++ SDK invocation both passed against the exact saved DINOv3 model; source SHA remained unchanged. No model weights or architecture changed. Private receipt `/private/tmp/p09_dinov3_fresh_export_receipt.json`.

Flow and GAN exports now require all10 C++/C#/build SDK source files and explicitly reject missing/incomplete desktop resources. The desktop packaging owner adds these resources and their source hash gate. Focused parity/package group:26 passed,30.82s. Final raster parity, missing/incomplete SDK, actual native mixed-DAG/GAN and source composition group:20 passed,23.88s. `git diff --check` passed. The [final desktop report](COMPLETION_20261001.md#데스크톱-앱에서-직접-확인한-흐름) and [native workflow manifest](NATIVE-workflow-manifest.json) record actual app export of141 files including10 SDK sources, exact-input whole-DAG parity, completed CPU device identity, clean quit and reopened saved inspection history. These observations cover the recorded workflow; all hardware and control combinations remain separate acceptance gates.
