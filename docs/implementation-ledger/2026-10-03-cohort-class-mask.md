# Package comparison, saved class rules and source masks

This records bounded follow-ups to S2-07, S2-05 and S3-01. The three parent tasks retain their existing acceptance states.

## S2-07 package comparison choices

- Browser-local choices restore by image UUID and SHA-256, up to 64 entries including unresolved ones. Backend, workspace, actor, project, source, task and label set define the storage scope.
- Changed, missing, moved and unreadable images remain visible. A comparison package is blocked until they are removed or explicitly selected again. Late responses and actions from a previous authority cannot enter the next scope.
- When no accepted index exists, paths have an independent storage key and the UI states the first-32-image limitation. Explicit empty choices remain empty after reopening.
- Failed network reads or malformed saved data preserve the original selection bytes. An explicit clear action provides recovery; restoration never silently overwrites damaged storage.
- Browser QA uses the actual renderer and an owned backend with synthetic source images: choose, reload, replace a source image, accept a new revision, preserve the warning through another reload, reselect, clear and reopen, and switch between projects.

## S2-05 save-time class rules

- The API checks predicates and segmentation/Blob class settings against recorded, completed, source-matched model metadata before writing recipe, version or active-flow files.
- Exact class names, foreground IDs, segmentation channel order and inconsistent aliases are validated. Missing legacy vocabulary remains unknown and unbound drafts remain supported.
- A connected model defines its own class scope. An unbound inspection model cannot inherit an upstream detector's vocabulary.
- Standard checkpoint weights are not loaded for this metadata check. Existing specialized CPU resolution reports malformed checkpoint decode as a validation error.
- API tests verify 422 refusals leave saved files unchanged and the previous active flow can still be reopened. Independent review found and closed two metadata edge cases.

## S3-01 source raster masks

- Accepted segmentation index revisions bind the trainer-selected grayscale PNG mask and optional class map by source-relative path and SHA-256.
- Native image/mask dimensions, PNG channels, class naming and link containment use the existing reject/exclude policy. Existing LabelMe, COCO and YOLO bindings retain precedence.
- Supported layouts include flat `images/` and `images/{train,val,test}`, including the trainer's `masks/train/` fallback. A background-only mask records no foreground labels and does not create an OK ground-truth verdict.
- Classification and detection do not gain raster mask bindings. Source files are read without modification. Palette/color masks continue through the explicit mask exchange workflow.
- Independent review reproduced and closed the flat-image mask fallback omission; tests verify changed bytes alter the index manifest and malformed replacement masks are rejected.

## Verification and remaining scope

The development checks ran on macOS with CPU fixtures: 408 affected backend tests, 426 renderer tests, TypeScript checks, renderer/main build and the package-cohort browser scenario. The new backend tests are registered in both CPU CI workflows. Local runs of a Windows-selected test list are not native Windows evidence.

This slice does not establish GPU model quality, real-device training, Windows installation acceptance, field operation, cross-browser selection sync or completion of the full service plan. Package selections remain a local convenience; accepted data revisions and execution receipts retain their existing authority.
