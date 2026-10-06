# Process quality acceptance

Status: pending human quality approval. Functional tests and synthetic scenarios
cannot approve manufacturing quality.

## Required decision record

Record one decision per process, product and task with:

- immutable source revision, dataset/cohort manifest and label-review revision;
- train/validation/test separation and an untouched heldout cohort;
- product and Lot groups, normal/defect/unknown counts and unresolved truth;
- exact checkpoint, flow, thresholds, calibration and package SHA-256;
- escape and overkill definitions, limits agreed by the process owner, measured
  counts and denominators, and unavailable metrics with reasons;
- reviewer identity, decision time, approved scope and expiry/review triggers.

A decision is eligible only while all bindings still match current truth and
permissions. An unknown label is not normal. Test-set exploratory tuning requires
a new untouched heldout and cannot serve as deployment-quality evidence.

## Gate

`backend/engine/release_eligibility.py` and
`backend/engine/runtime_release_evidence.py` enforce software evidence freshness.
They do not replace the process owner's quality decision. Keep current failures,
class/Lot breakdowns and false-OK cases in the private review record; publish only
approved aggregate data. No quality thresholds or reviewer approval have been
invented by this document.
