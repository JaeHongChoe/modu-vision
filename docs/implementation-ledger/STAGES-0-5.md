# Stages 0–5 execution ledger

Baseline: `91e5a1ad362cb6fc30557a07b0256f6eba009548` on `main`.
Approved plan: [product stages 0–5](../superpowers/plans/2026-10-01-product-stages-0-5.md).

| Stage | Work | State | Evidence / prerequisite |
|---|---|---|---|
| 0 | Shared class semantics, explicit truth, job states, separate activation, owned-worker death | Implemented and checked | Ordered class/task/role contracts; reviewed UNKNOWN stays excluded; cancelled/interrupted states persist; opening history does not activate it |
| 1 | Whole-flow evaluation, manual approval binding, cohort/target parity, reference cycle | Implemented; genuine reference executed | Two heldout NG images, immutable graph/checkpoints/ROI artifacts, error-review queue and CPU independent-runner cohort parity |
| 2 | Full artifact debugger, node editing clarity, impact, target A/B | Implemented; native checks and regressions | Node images/masks/ROI path, class choices and area units, edit impact, explicit target/device, restored comparison history |
| 3 | Remote cancellation/recovery/transfer/environment, local subprocess | Implemented; actual server checks | Genuine DINO segmentation and YOLO training, interrupted transfer, owned cancellation, lost launch acknowledgment recovery, same-worker execution and released reservations |
| 4 | Capture intake, review/adoption, compatible parent and regression evidence | Implemented; actual cycle executed | Real HTTP capture → review → owned dataset version → verified parent → candidate training → same frozen two-image comparison; capture remains UNKNOWN/not_used |
| 5 | Frozen packaging, release readiness, interrupted update/rollback | Implemented; genuine macOS runtime checks passed | Frozen dependency/health/restart checks and two known-image service executions; isolated unsigned macOS directory package built; publisher/other OS/equipment prerequisites retained |

## Execution decisions

- The user already approved this roadmap and direct `main` integration. The clean existing checkout is retained so desktop implementation and native QA use the same files.
- File-disjoint implementation may run concurrently; shared files remain with the coordinator. This avoids competing changes while permitting parallel progress.
- Source QA data is read-only; prepared projects and results use separate project folders.
- No release signing, hardware execution or quality approval is claimed from unit tests.

## Verification and publication

Final checks on the frozen implementation, 2026-10-01:

- Complete backend regression gate: **2,269 passed / 29 skipped**, exit 0, 900.32 seconds. The skipped cases retain their optional-environment prerequisites.
- Renderer regression gate: **160 passed / 0 failed** across 30 test files. TypeScript checking, the application build and signing/update checks passed.
- Frozen backend build identity: `b5ad6b60ea2ff8e12050f0a8c1b2f5b1ade1cf146b52f64ec152f47f4d65d709`; executable SHA-256: `d89ebdd0cbace5202e0ef93396d92daa2102edb35c0effbd0b0306fdc7b773d7`.
- The actual remote archive contains **209 production backend source files**, with every source hash matching the native build inventory. One HTTP inference from this frozen executable launched one owned server worker, loaded the unchanged candidate checkpoint and executed on the configured physical GPU. Exit was confirmed before releasing reservations; the active project and original source bytes stayed unchanged.
- The final exported reference package has manifest SHA-256 `ab268129434db3bcdd1dce4941bd913cc7b13ca8e515278c4a410c560fc19aeb`. Its independent runner completed successfully; frozen service start, stop, restart and known-image inference passed.
- The final macOS directory application opened its actual bundled renderer and backend. The native diagnostic panel displayed the same build identity and confirmed required dependencies. Publisher verification correctly rejected this unsigned diagnostic artifact.

Detailed final validation and publication receipts stay outside the repository. Publication is checked by comparing the pushed `main` SHA with the local commit and checking the working tree after the ordinary push.

### Actual reference evidence

- The supplied original source stayed read-only: all **171 files** match the before/after manifest. Eight actual Bow ROI crops from eight distinct parents produced **train 4 / val 2 / test 2**. There are no known normal images in this reference.
- DINO segmentation checkpoint: `ba7d923e21e40d1460adb0b21c7a86c8f35b8b672de653fe9f38b725ebdf7c5a`.
- YOLO checkpoint: `46fc83b85cb46ca110a4819274933a029e04d30c483903f72018caf3865c49f6`.
- Warm-start candidate checkpoint: `ee66d6a9070b15de9a6b93aadbeba7c68e02eb4db9268375215ae6727890051a`; one epoch/two steps, exact parent weight initialization, no optimizer-resume claim.
- Actual two-model detection-ROI → segmentation executions returned REVIEW when no ROI was found. Missing detection never silently became OK. Pure segmentation executed successfully but missed both known NG images.
- Whole-flow evaluation records **2 known NG / 0 normal / 0 unknown**, **2 escapes**, and unavailable overkill. Error images retain run/node/ROI origin in the review queue.
- Parent/candidate comparison uses the same frozen images and original scoped truth. Both models miss both NG images; there are zero disagreements. This is functional evidence and **does not approve either model**.
- A real HTTP capture was stored, reviewed and copied into an owned dataset version. It remains UNKNOWN, needs label review and is excluded from training. Original heldout images/split, parent checkpoint and original source bytes remain unchanged.
- Actual acknowledgment interruption recovered the same worker with **one launch**, on the configured GPU identity, then confirmed exit before releasing its reservation. Connection uncertainty alone retains the lease.
- Detailed input/output hashes and private run receipts are stored outside Git. No source images, checkpoints, credentials or correspondence are published with this ledger.

### Review corrections

- A terminal result no longer releases GPU capacity before the exact owned worker/session has exited. Missing launch acknowledgment can recover a spec-bound ownership receipt without relaunching.
- Comparison hashes are captured before truth/model loading, and checked before inference and after execution. Metadata and checkpoint ordered class vocabularies and roles must agree.
- Reviewed UNKNOWN survives equivalent alias/explicit role provenance. A different vocabulary or role scope requires fresh review; descriptive folder labels cannot silently replace an existing scoped declaration. Increasing semantic revisions preserve the latest declaration after wall-clock rollback.
- Mixed-task comparison and whole-flow evidence bind the participating model tasks and their human-declared truth hashes. A newer participating review invalidates older mixed truth until explicit re-review; it never transfers OK/NG between different task scopes. Legacy mixed cohorts without this dependency identity require a new freeze. Homogeneous cohort hashes remain unchanged.
- Mixed truth review sends the same participating-task context as execution, preserves merged class roles and displays the re-review reason. Cohort selection checks that task context rather than confusing view-only context with the stored canonical scope.
- Multiple approved models of the same task can be explicitly selected. Revoked/rolled-back revisions cannot authorize an export. Packages changed during parity keep failure evidence.
- Only passed 2–64-image cohort evidence produces an ordinary trusted release policy. OpenVINO precision uses separate measured heldout evidence and explicit drift review; it cannot create an ordinary parity pass.
- Authenticated fleet transport preserves the separately bound receipt, approved policy, selected device and manifest identity. Missing/mismatched evidence is rejected before apply.
- Native signing diagnostics distinguish ad-hoc/absent publisher identities from a trusted publisher. Changed source/build bytes invalidate prior native acceptance.
- Frozen resources include all production backend sources at their original paths. Native diagnostics execute the actual remote-code bundler and verify every archive entry against the frozen inventory, so a missing remote worker cannot pass readiness.
- Prepared evaluation images are mapped back to their unchanged source by full relative identity and SHA-256. Cached remote results receive the same mapping; absent or changed provenance disables label editing. Native QA reopened the actual `bow_06` FN in step 2 with its original Bow annotation, without editing source labels.

### Boundaries

- Actual reference training covers DINO segmentation and YOLO detection, plus one DINO warm-start candidate. It does not establish model quality or a representative production benchmark.
- The data has no known OK truth, so normal accuracy/overkill and model promotion prerequisites remain unmet.
- Optional native OpenVINO execution is unavailable in the current test interpreter; those genuine conversion cases are explicitly skipped.
- Signed publisher delivery, native Windows/Linux installer execution, physical camera/PLC/MES acceptance and a configured update channel require their own target evidence. macOS diagnostic packaging is not such approval.
