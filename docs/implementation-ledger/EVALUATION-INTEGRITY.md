# Evaluation and annotation integrity

Baseline: `18a29162e9570fa448de39dbe5d5adeae3b6f131`.
Plan: [Evaluation integrity review](../superpowers/plans/2026-10-01-evaluation-integrity-review.md).

## Repairs

- Flat class folders keep their training/validation split but do not invent an
  independent test set. Evaluation requires saved partitions, never all
  training images. Each result states test/validation and checkpoint-selection
  overlap. Empty, fully paired test folders can use named validation data;
  incomplete test image/label pairs still fail explicitly.
- Single-class anomaly truth has unavailable AUROC and threshold search. It
  uses the saved model/configured threshold, with an explicit explanation in
  the evaluation screen. Previous evaluation caches are invalidated by the
  versioned evaluation contract and current checkpoint/data binding.
- Annotation PNG/JSON bytes are validated and staged before publication.
  Earlier contents are retained until the outer review-ledger commit succeeds.
  I/O failures roll back writes/deletions while the writer lock is held,
  including nested imports and waiting writers. Publication backups are
  excluded from data fingerprints, version inventory and project archives.
- Batches for one active project/source commit together and roll back every
  image if a later save fails. Mixed or legacy batches retain per-item commits
  but include completed results/count and the failed image/index in HTTP error
  details. Legacy locks cover inherited palette reads through publication;
  private lock/backup files do not break legacy/project label comparisons.
- Calibration records the actual cohort, scored-truth hash, calibration role
  and need for a separate independent test. Changed evidence is rejected;
  persistence failure no longer reports success. Normal aliases in Korean
  match evaluation history, and unknown truth is not counted as OK/NG.
- Exported calibration checks the current scored-truth hash, binding and
  checkpoint bytes. It applies only to binary classification with one normal
  and one defect class, or detection whose foreground classes are defects,
  with agreement between evaluation and runtime class meanings. Other tasks
  retain their saved model threshold and record why calibration was not
  transferred. DINO anomaly records its actual saved threshold source. Config,
  package receipt and README all report the same applied state.
- Remote evaluation requests/results carry the current contract version. The
  version changes the operation journal identity; old operations cannot be
  relabeled as new evaluation evidence. Unversioned or unspecified-split
  responses are rejected before local publication.

## Verification

Results overlap and must not be added together.

| Verification | Result | Timing/scope |
| --- | --- | --- |
| Broad backend regression | **1,897 passed; 28 skipped; 108 warnings** | 468.23 seconds. After batch rollback, legacy palette-lock and migration repairs; before the final mixed-batch generic-exception disclosure supplement. All 26 changed source/test hashes matched the start of this gate at completion. |
| Final batch supplement regression | **34 passed** | After the last seven-line error-handler change: 11 batch, 17 annotation transaction and 6 metadata API cases. Real ledger replacement and rollback failures retain error causes and disclose earlier committed saves; an atomic batch ledger failure restores both images and the ledger. |
| Renderer regression | **143 passed** | Evaluation evidence changes and existing renderer behavior contracts. |
| Typecheck/build | **Passed** | Main/preload and renderer; existing bundle-size/import warnings remain. |
| Export/calibration regression | **104 passed** | New evidence and export integrity cases, including actual tiny CPU ONNX/TorchScript packages, rejected scoring mismatches, changed evidence and Korean normal labels. |
| Follow-up review | **Completed; concrete findings resolved** | Separate review reproduced initial failures and checked integrated repairs. The last review confirmed batch rollback, lock scope and migration handling, then identified missing partial-result disclosure for non-HTTP ledger failures in mixed batches. The final supplement was reviewed and regression-tested after a failing reproduction. |

Only the batch route and its regression file changed after the broad gate.
Warnings and passing fixture tests do not certify trained model output parity,
model quality or physical deployment.

## Limits

- Annotation recovery covers raised exceptions and serialized writers. It is
  not a multi-file journal or process-crash guarantee. Readers that ignore the
  lock can observe publication in progress. Legacy or mixed batches retain
  per-item commit behavior with explicit partial-result disclosure. Persistent
  Windows replacement failures retain recovery files; native Windows retry and
  process-crash recovery acceptance are not established.
- A calibration helper validates stated evidence. Current jobs also pass
  checkpoint/source binding checks; explicitly imported directory results do
  not gain file verification or independent-test certification from the helper.
- Segmentation resize/max-pixel evaluation and tiled/area runtime decisions,
  multiclass classification scoring and patch scoring still have distinct
  semantics. This repair prevents unverified calibration transfer; it does not
  certify those deployment decisions from the evaluation curve.
- This review uses isolated regression fixtures, including real image bytes
  and tiny CPU checkpoint boundaries. It does not establish all-family real
  training, physical-server acceptance, signed releases or equipment readiness.
