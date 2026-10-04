# Service extension implementation evidence

This record covers the additional implementation batch selected from the existing 82-item service program. It records implementation and executed checks separately from parent acceptance, model quality, hardware qualification and deployment. The parent program status is unchanged by this document.

Base commit: `422a2d2cefbb53caf2b4f6d8b4a00dd475992e3e`.

## Implemented slices

| Requirement | Production path and behavior | Executed evidence | Remaining qualification |
| --- | --- | --- | --- |
| S4-06 OCR | Versioned crop/detect-recognize recipe, horizontal projection proposals, per-region CTC recognition, multiline ordering, normalized CER/WER and explicit rules. Flow results preserve original coordinates and rule decisions. | CPU recognizer/recipe, API, original geometry and typed flow regression fixtures. | Learned detector, vertical text, representative scene/Korean quality and native app import-to-training-to-evaluation. |
| S4-07 OBB | Optional explicit local Ultralytics `.pt` adapter, empty and >32-object manifests, polygon/axial-angle conversion and safe outer checkpoint envelope. Existing fixed-slot default remains compatible. Native deserialization requires exact-byte trust. | Manifest, dispatch, checksum/TOCTOU, trust and compatibility regressions; actual Ultralytics 8.4.41 scratch OBB training, prediction and held-out evaluation on eight synthetic images using CPU. | Representative model quality, runtime/weight license approval, field-host digest approval and remote weight transfer. |
| S4-01 DINO | Head-only, partial and full trainability controls reach actual timm parameter groups and saved metadata. | Offline CPU timm gradients and parameter-update checks; actual remote RTX 3090 scratch DINOv3 partial-block training through the production API/coordinator. | Authentic pretrained fine-tuning quality, representative evaluation and native import-to-training-to-evaluation qualification. |
| S4-12 AutoDL/resume | Selected remote profile runs through the existing coordinator/worker; measured trial results and artifact hashes are read back. Seeds and completed-trial reuse preserve search identity. Local supervised epoch-boundary resume restores optimizer, scheduler, scaler, RNG, step and best state and rejects identity mismatch. Resume lineage hashes the exact loaded bytes. | Actual worker REST pipeline under fake transport/loopback CPU, uninterrupted-vs-resumed CPU state and snapshot mutation regression; actual production API-to-SSH RTX 3090 search/training with measured trial result and matching returned artifact hashes. | Native UI-initiated remote training and representative model quality. Remote/DDP/anomaly/MPS exact resume is explicitly unsupported; warm-start is separate. Remote measured native OBB/multiline OCR requires artifact-transfer implementation. |
| S3-08 Non-destructive edits | Brightness derives a new immutable image/version, keeps geometry/mask/labels and source hash, and uses the existing review/adoption path. | Pixel/geometry/mask/invalid-input regressions and actual macOS Electron save at factor 0.5; original hash and two labels preserved, derived pixels verified. | Representative task quality and other operating systems. |
| S3-09 Review queue | Pending review precedes reviewed rows; error, model disagreement, REVIEW outcome and explicitly typed near-threshold fraction rank candidates. Admission recipe/run/source identity stays visible. | Actual SQLite queue/API fixtures, malformed scores and stable ordering. Native app entry observed. | Native queue with representative captured jobs and human label adoption. Predictions never become truth automatically. |
| S5-01 Windows SCM | Real ctypes dispatcher/ServiceMain/control handler and status reporting; retained process handle/Job Object; explicit preflight, preparation and activation controls. Frozen app entry dispatches before Studio/Torch startup. | Windows API substitutes, startup gate, warmup, account/config validation and existing manager/autostart compatibility. | Real Windows service registration, reboot/Session0, GPU/camera/network identity and signed distribution. No actual service registered by this batch. |
| S5-02 Inbox/backpressure | Transactional admission binds package/device/model/recipe before queuing. Duplicate-before-cap, conflict, bounded pending capacity, deadlines, dead letters and explicit replay/retry. Folder adapter settles inputs with fair bounded traversal; HTTP adapter cleans up refused uploads. | Admit under A, switch to B, restart and emit under A; duplicate/cap/deadline/replay/corrupt/continuous-write/fairness fixtures. | Real continuous input load and deployed service soak. Legacy unknown bindings remain REVIEW. |
| S5-06 Staged fleet | Durable revision CAS, target URL snapshots, explicit canary approval, bounded batches, failure/offline pause, fresh committed receipts and reverse rollback. Unresolved apply intents cannot silently escape rollback. | Fake-device failure/crash/resume/adoption/reverse rollback, including unchanged independent regression. | Actual device readback, field rollout and rollback. No field deployment performed. |
| S5-08 Drift | Immutable hashed reference versions; actual decoded luminance histograms, rate deltas, strata, model/source/recipe identity and exclusion accounting. Tampered/stale baselines fail closed. | Literal luminance/rate deltas, changed-source and review-role API checks. Native panel entry observed. | Representative field baseline and native end-to-end freeze/report. Prediction rate is not ground-truth quality; no automatic retraining/promotion. |
| S5-09 Retention/restore | Durable legal/runtime/fleet/drift pins and shared project lock; policy/quota visibility, recoverable trash and original-path restore. Source/annotation protection remains enforced. Backup validation and fresh-target restore produce distinct receipts, rebind deployment/drift paths and preserve pending review state. | Temporary-file/pin/archive fixtures, unchanged fleet-root/restored-path regressions, and actual Electron preview → trash → restore; restored 53-byte fixture SHA256 matches original. | Real backup recovery drill and operational retention policy. No permanent purge endpoint; trash still consumes space. Unregistered external source references fail closed. |
| S7-01 / S6-09 Evidence | Source hashes, test commands/results, independent reproductions and public candidate inspection. | Final gates below and scoped regression files in this commit. | Whole-program coverage/acceptance and release approval remain separate. |

Sampling E06 remains in the separately owned implementation lane. This batch does not duplicate it or declare its handoff accepted.

## Verification receipts

Source files were hashed before the combined final gate. Private receipts and native screenshots remain outside the repository; no user images, model files, credentials, databases or private logs are published here.

| Check | Result |
| --- | --- |
| `npm run test:renderer` | 469 passed, 0 failed |
| `npm run typecheck` | Passed |
| `python scripts/generate_api_types.py --check` | Current |
| `npm run build` | Passed; existing large-chunk warning remains |
| Complete backend gate before final compatibility corrections | 3,422 collected: 3,375 passed, 16 failed, 31 skipped; all three disjoint file shards completed. This run is not a full-suite pass. |
| Final affected-file and extension gate | 189 passed, 6 skipped, 0 failed across 26 files; all 16 previously failing nodes passed. Source hashes match before/after. |
| Resume snapshot + resume/API focused gate | 17 passed |
| Independent unchanged regressions | 3 passed; before/after source hashes identical |
| Native brightness | Actual save/readback; original unchanged and derived pixels match factor 0.5 |
| Native retention | Actual preview/move/restore; ledger `restored`, original-path bytes/hash match |

Focused lane checks were also executed: 42 training, 43 retention/archive (one deselected live service test), 12 model trust/review, 11 fleet/review, 33 inbox/service/review, 113 compatibility (one skipped), 17 manager/autostart, 29 data/drift, and 13 typed OCR flow checks. These sets overlap; they must not be added into a claimed unique test total. The complete backend gate owns its pre-correction count; subsequent affected-file checks remain separate.

The 16 failures comprise ten obsolete test-fixture route inspections, four fake-trainer tests that waited only two seconds for unrelated real device-cache cleanup, one shared child-process isolation contract check, and one text-encoding guard. Production terminal status/receipt ordering remains unchanged. The router fixtures use the already registered public endpoints; fake-trainer tests isolate cache cleanup and separately test a blocked cleanup boundary. Windows children use the common session-isolation helper, and app JSON / OS tool pipes name their encodings. All 16 exact failed nodes passed in the final affected-file gate. The six skips are two Windows-only file-sharing checks and four checks requiring an approved local DINOv3 pretrained checkpoint. The earlier complete suite was not repeated after these narrow corrections, and its overlapping counts are not added to the final gate.

The tested source-map SHA256 is `ce810f753798462a23e1f5b18fc30255ba2c31579b5936f6b7889a9d4e4f2349` (compact sorted JSON mapping source paths to file SHA256 values). All 944 mapped source/test/config files match before and after the final gate. Publication removes one trailing blank line from each of the S3-08/S3-09 extension test files; their Python ASTs are identical and no executable statement changed. The publication source-map SHA256 is `4b0d130f7dc73a94bb720ce96e6125e7b5bf3abb2f5404288db40680822e1c49`. The 323 frontend/build/config files relevant to the renderer, type and build checks are unchanged from their verified generation.

## Extension closure checks on the integrated source

The earlier receipt table above is historical. These subsequent checks use the published extension branch plus published onboarding/QA changes, with isolated stores and synthetic input. They are distinct checks and their overlapping counts are not summed.

| Check | Observed result and scope |
| --- | --- |
| Renderer, types and build | 494 renderer tests passed; both TypeScript checks and build passed. Existing large-chunk warning remains. |
| Actual macOS Electron transport | 2 tests passed; main/preload/backend used an isolated application profile. Harness: 14 passed. |
| Browser transport and anomaly flow | 33 transport checks and all 5 S2-04 checks passed. The anomaly fixture now uses train/good and test/good/defect, asserts import success and five gallery images. |
| Hosted Linux CPU source run | Run 37163658340 passed at branch commit cd3a6713fb37562ed19a6c8abd16d3a957fc8863. Later source changes require fresh CI. |
| Initial integrated full backend gate | At d3b6f641757c77c8a3f54f155640faa8814c2055, all 3,464 collected nodes were executed in three disjoint shards: 3,354 passed, 64 failed, 11 errors, 35 skipped. Offline-cache and private harness corrections followed; this initial run was not green. |
| Corrected per-node follow-up | All initial failed/error nodes passed after approved public pretrained weights were copied to a private offline cache and only verified local fake-tool operations were allowed. The exact collection composite is 3,437 passed, 27 skipped, no failures/errors. This is a composite follow-up, not one full exit-zero run. |
| Windows file-identity fix | Eight exact SQLite overflow regressions reproduced before correction; full signed 64-bit and wider Windows identifiers round-trip exactly. 189 related source-mask, binding, COCO and index tests passed locally. Fresh hosted Windows remains required. |
| Backup and delivery portability | Exact owned coordination lock files are excluded from archives while user/source lock files and committed WAL data are retained. 27 archive checks passed. Controlled-clock delivery expiry file: 17 passed. |
| Actual remote CUDA smoke | Production automated-training API, SSH transport and selected existing RTX 3090 profile completed one trial/one epoch on eight synthetic images: DINOv3 vits16, pretrained=false, partial_blocks=1. CUDA execution, measured result, two returned artifact hashes and post-run empty reservation/GPU process readback were checked. This is API-initiated functional training, not native UI training or model quality. |
| Actual optional OBB runtime | Local scratch Ultralytics OBB train/predict/evaluate completed on CPU; held-out original-coordinate contract and unchanged source hashes checked. Untrained synthetic metrics do not establish detector quality or license acceptance. |
| Latest native data checks | Actual native project creation, folder-dialog import of eight images, brightness 0.5 save, validation and revision adoption passed. Every original hash stayed unchanged and derived pixels/provenance matched. All eight rows remain UNSPLIT; no completed model exists and the review queue correctly requires saved evaluation. |

The final integrated backend gate and updated hosted Windows run must have their own source-bound receipts before closure. Private logs, images, models and profiles remain outside this public document.

## Independently reproduced and repaired defects

- Crash after a target commit but before plan persistence could omit that target from rollback. Unresolved intent now requires fresh receipt adoption before rollback.
- Fleet package retention used the wrong project root. Target ledgers now share the actual project retention lock and pins.
- Fresh archive restore retained old fleet release/ack/plan paths. Paths now rebind to the new root while pending actions retain review state.
- Arbitrary YOLO YAML could request pretrained downloads during model construction. Only an explicit local regular `.pt` is accepted.
- Native checkpoint checks could verify one byte sequence and load a later sequence. Envelope/trusted inputs now load the verified snapshot.
- Reviewer middleware blocked the exact drift-reference endpoint despite the route role contract. The exact route is allowed; generic retention mutations remain owner-only.
- A moving `latest` checkpoint could produce resume lineage for different bytes than the restored state. Lineage now retains the loaded snapshot digest.
- A conditional local fingerprint import shadowed the module import and broke ordinary training starts. The redundant import was removed; the affected direct-training/resume compatibility files passed 75 checks.

## Operational boundaries

This implementation does not approve model quality, license terms, native model digests on a field host, administrative service registration, real deployment or permanent data deletion. The executed synthetic remote GPU smoke below proves its own API/coordinator path; it does not establish model-quality or native-workflow acceptance. Explicit unsupported modes return actionable errors rather than silently substituting local execution or warm-start. Real platform and model checks must update their own receipts before parent acceptance or release promotion.
