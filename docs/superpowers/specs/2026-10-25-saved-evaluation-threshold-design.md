# Saved evaluation threshold and preview

## Intent and scope

The user requested the two deferred judgment/count fixes. This change handles
only Step 4's saved decision authority. The brush-mask import count fix is a
separate change/PR. Preview controls must not change the footer, assistant
readiness or automatic progression until a threshold has actually been saved.

## Approach

Keep `confidenceThreshold` as the interactive preview and add an evaluation-scoped
`persistedThreshold: number | null` to the existing evaluation store. A verdict
snapshot was considered, but it duplicates derived state and complicates scope
replacement. Reading `metrics.active_threshold` directly was rejected because
legacy classification/detection responses omit it and saved calibration uses a
different top-level carrier.

- Accept a valid saved calibration (`zero_underkill_calibrated` and top-level
  `optimal_threshold`), checking matching `calibration_evidence.threshold` when
  that evidence is present. Retain compatibility with older saved calibration
  markers without introducing a new fingerprint requirement.
- Otherwise use valid `metrics.active_threshold`. Preserve the existing .50
  baseline only when the field is absent for non-anomaly probability evaluation.
  Missing/invalid anomaly or distance authority, explicitly invalid thresholds,
  and conflicting saved evidence remain unknown. Preserve zero and distance >1.
- Update saved authority only after an accepted evaluation read or a valid,
  source-fenced successful calibration POST with `apply_to_eval_results:true`.
  Pending, failed or stale calibration does not change saved authority.
- Clear authority at read replacement, invalidation, missing model and errors.
- Compute readiness from accepted prediction rows and recorded class roles with
  the saved threshold, excluding independently arriving recommendation analysis.
- Keep preview cards/filtering/heatmaps interactive. Explain any preview/saved
  mismatch in the evaluation UI; keep manual navigation unchanged.
- The footer's saved missed-defect action must reveal the same accepted saved
  misses, including when the preview escape tab is already selected. Preserve
  ordinary preview filtering and manual stage-arrow navigation. A saved-review
  filter keeps accepted verdicts and heatmaps on saved authority until an explicit
  preview interaction; the viewport receives that verdict from Studio, including
  strict patch-score equality and unresolved REVIEW, without duplicating scoring.

## Verification and boundaries

First reproduce preview .20 incorrectly clearing a saved .50 miss (score .30),
then test the reverse, zero/distance boundaries, recommendation contamination,
calibration persistence/reload/failure, and scope replacement. Exercise actual
store/hook/verdict behavior with controlled API responses. Run affected Node
tests, full renderer/main suite, types/build, evidence gates and focused browser
QA. No real account, source images, model training, deployment or acceptance work.
