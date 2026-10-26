# Final beginner guidance and owned metadata refresh

The remaining product changes are corrections to existing behavior and guidance,
not new automatic workflow, model, account or acceptance policy.

## Controller choices

- Use one Korean split action name, '이 비율로 나누기', in simple and full views.
  Keeping the advanced label and neutralizing the one warning is smaller, but
  consistent visible naming makes existing beginner guidance directly usable.
  Five browser locators evolve; four existing historical declarations retain the
  old exact executed blobs. Backend-coupled class minimum/train text stays exact.
- Missed-defect guidance is neutral review/correct labels/retrain/evaluate advice.
  It must not tell an unsupported evaluation to lower a threshold. Explain the
  actual calibration-unavailable reason in that panel without changing saved
  readiness authority or eligibility rules.
- Give canonical mAP_50/mAP_50_95/miou/mdice clear labels; preserve case visually
  (no CSS uppercase on metric titles). The .50:.05:.95 AP tooltip is separate
  from the .50 tooltip. Classification imbalance guidance reflects class weights;
  other tasks get general data-review guidance, no universal Focal claim.
- Reuse isLoading for the actual owned annotation-summary read. Its consumers
  already block unsettled dataset actions. A new independent flag would require
  roughly25 consumers and is unnecessary. The summary commits isLoading:false
  atomically with new data BEFORE loadImages, keeping gallery ownership intact.
- A browser capability hint beside its path input persists independently of
  project-scoped picker errors. Do not weaken error retirement/source scope.

## Owned refresh rules

Capture folder/key/authority and latestImportRequest before dispatch. Atomically
set loading, zero splits, clear class counts and mark stale before downstream
invalidation; recheck ownership after subscribers run. LatestImageRequest retires
old gallery responses. Only the fully current request can commit fresh summary
and clear loading or publish its load error. Do not clear a newer operation in
finally. Suppress stale checklist advice while loading, keep store-level split
and training start refusals, and preserve existing split-completion ownership.

## Evidence and guide

Preserve saved threshold vs preview, saved-miss CTA/selected verdict/heatmap,
manual continuation, source/labelset/actor authority and all historical receipts.
Only DatasetStudio's current source declaration changes in the action registry;
scenario states/execution evidence remain unchanged. Current E2E locator edits
use the existing historical-source mechanism, without new acceptance credit.

The private guide rev76 export remains immutable. Deliver exact correction text,
final-code capture provenance and replacement raw screenshots separately. A green
OK in a saved ESCAPE is a valid model misclassification and does not prove stale
pixels; capture validation must bind selected image, actual request/response and
threshold, or explicit unavailable/error. Do not use a fixed sleep as proof.

Native/browser synthetic UI checks cannot substitute for a first-time person's
trial, real field quality, a new actual-account grant/request, publisher signing,
notarization or Windows installation. Prepare those concrete acceptance steps and
retain their unverified status until the appropriate evidence is supplied.
