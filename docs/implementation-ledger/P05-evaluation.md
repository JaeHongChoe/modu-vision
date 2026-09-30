# P05 — evaluation and model comparison

## Scope

F047–F058, F062 and F063 are integrated. Evaluation uses the saved independent test cohort, checkpoint hashes, source mapping and active label revision. API results, desktop panels, saved history and report exports use the same evidence.

| Features | Implementation | Focused evidence |
|---|---|---|
| F047–F050 | Original image, truth/prediction, image metrics, class metrics and confusion cell selection | Existing evaluation studio/history/viewport; real checkpoint reevaluation and renderer evaluation recovery contracts |
| F051 | Per-class pixel TP/FP/FN, lossless indexed truth/prediction and error overlays | `test_multiclass_label_inference.py`: actual three-class network, class2 TP64/FP960, class1 FN64; saved LabelMe test split retains native pixels |
| F052 | Class-aware one-to-one object IoU matches; missing and extra box selection | `test_evaluation_object_pixel_character.py`; detection evaluator archives object evidence |
| F053 | Unicode character alignment; missing, extra and substituted character filters | Same focused test; OCR family evaluation history and original-image mapping |
| F054–F056 | Class score bins, source/model area units, scored ROC and threshold operating counts | Five backend evidence tests and ten renderer evidence tests; ROC refuses one-class truth |
| F057 | Same-kind, full independent test, async progress/cancel/reopen and image-by-image differences | `test_model_comparisons.py`; owner PID/time/command recovery |
| F058 | Different task families share exact image paths, hashes and known OK/NG truth | `test_cross_task_comparison.py`: actual CLS+SEG networks, sync/async/reopen; `test_owned_patch_comparison.py`: owned patch holdout mapping/conflict rejection |
| F062–F063 | Confidence-ranked OBB AP@.5 and .5:.95; class IoU matching; angle-error scatter | Actual CPU OBB checkpoint evaluation; ranked duplicate-box FP regression and two-box/per-class tests |

## Integration repairs

- Segmentation inference now gathers the probability of each predicted class and creates a lossless mask candidate for every foreground class. Class2 predictions retain their own confidence/name and holes when adopted as brush labels.
- Saved-split LabelMe reevaluation uses the original manifest-selected pixels and class mapping. It no longer enters the legacy binary crop preparation path.
- Prepared patch comparisons map independent crops back to the same original image cohort, verify source hashes and reject differing truth/partitions.
- Comparison recovery binds process creation time and command hash, preventing a reused/unrelated live PID from keeping jobs active forever.

## Verification boundaries

Focused backend and renderer tests use actual networks and raster inputs as functional fixtures. Native desktop acceptance and manufacturing accuracy are separate. Original-only NG inputs do not establish specificity or optimal thresholds. Broad final regression and native observations are recorded in the final acceptance ledger.
