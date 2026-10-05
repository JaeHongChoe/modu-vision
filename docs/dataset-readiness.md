# Dataset readiness and grouped split

## Common-original groups

Split preview unions identical content hashes and every nonempty common-original `group`, even when the selected grouping is product or Lot. Crops, derived images and synthetic variants imported from outside the application must declare the same group as their original. Blank groups are unknown provenance; names and visual similarity do not establish independence.

Requested train/validation/test partitions require enough independent groups. An unavailable held-out partition is refused before writing a split. Anomaly normal-only selection and task-specific loader rules remain in effect.

## Frozen split qualification

The saved receipt binds task, labelset, selected grouping, seed, ratios, exact image/content/annotation/mask identities and workflow metadata. Reopening reads the original assignment and qualification. Changes to current source membership or metadata mark it stale without rewriting historical assignments. Applying a preview rechecks its fingerprint before and after the guarded operation; a moved preview is refused. Legacy splits without a qualified receipt display unavailable qualification.

## Review diagnostics

The readiness panel reports byte duplicates, perceptual similarity, blur, exposure, resolution, class counts and task schema consistency. Unreadable images, incompatible tags/object geometry, out-of-bounds rotated boxes and mismatched or linked segmentation masks are visible errors. Diagnostics do not approve label truth or representative model/data quality. Class coverage can be multilabel and ratios must be interpreted accordingly.

Current project/source/account generation guards refuse retained actions and late results. Diagnostic reads do not rewrite source images or annotations.

## Approved training cohort

Existing approved-only selection is enforced in primary and specialist/prepared loader contracts. Immutable training receipts bind review, guideline, policy and eligibility/cohort revisions. Revoking approval or changing those revisions invalidates the binding. A current actual classification loader qualification decodes only the two approved train/validation images and persists the disk receipt; it does not submit a training job or establish model quality.

## Qualification scope

Actual Chrome and macOS development Electron cases qualify grouped preview/apply/reopen, stale warning and diagnostic reopen using owned synthetic images. Independent review, representative human/data acceptance, installed/physical targets and complete parent acceptance remain pending. Windows tests are excluded.
