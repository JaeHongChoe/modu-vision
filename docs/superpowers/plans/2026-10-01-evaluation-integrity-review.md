# Evaluation and annotation integrity review

Baseline: `18a29162e9570fa448de39dbe5d5adeae3b6f131`.

## Scope

Independent review reproduced three failures. Keep source images unchanged and
preserve the existing training/validation support for flat class folders.

1. Classification `test` must never include automatically selected training
   images. Remove whole-dataset evaluation fallbacks. Record the actual split
   and warn when validation data overlaps checkpoint selection. Shared local and
   remote evaluators must carry the same evidence.
2. Validate and encode annotations before changing live files. Protect PNG,
   JSON and review-ledger commits with staged replacements and rollback under
   the existing OS lock, including nested imports. Preserve immediate post-save
   fingerprint reads. Do not claim process-crash atomicity without a journal.
3. Return unavailable AUROC for single-class anomaly truth. Use an explicit
   model/configuration threshold for that cohort, report why threshold search
   is unavailable, and show the limitation in evaluation.
4. Verify calibration inputs through the current evaluation contract; record
   the actual calibration cohort, scored-truth hash and overlap with model
   selection. Reject changed evidence and failed persistence. Transfer a
   threshold to a runtime package only when its scoring semantics agree.
   Treat Korean normal aliases consistently and reject unknown truth.

## Implementation and acceptance

- [x] Add failing split, empty-holdout and single-class regressions.
- [x] Add failing annotation validation/publication/ledger regressions.
- [x] Repair backend behavior and visible evaluation evidence.
- [x] Run affected regressions, renderer checks and the full backend suite.
- [x] Obtain independent follow-up review and resolve concrete findings.
- [x] Record verified results and limitations; commit and push to `main`.

Verification timing and scope are recorded in
[EVALUATION-INTEGRITY.md](../../implementation-ledger/EVALUATION-INTEGRITY.md).

No physical-server training, release signing or field-equipment acceptance is
part of this review round.
