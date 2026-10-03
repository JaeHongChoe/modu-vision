# Operator image choices and source failure provenance

This records bounded follow-ups to S2-07, S3-01 and S6-05. Parent task acceptance states stay unchanged.

## S2-07 operator image choices

- One browser-local manual choice is saved with image UUID and SHA-256. Backend, workspace, actor, project ID/directory, source, task and label set define its scope.
- Restore, explicit refresh and explicit inspection revalidate identity. Changed, moved, missing and unreadable choices remain visible and block inspection until explicit reselection or removal. No first-image substitution or automatic inspection occurs.
- Identity resolution checks the accepted index revision. It does not rehash live source bytes; the changed-content browser case rebuilds and accepts the revision before checking the saved choice.
- The current image can be explicitly selected again from the current library page even when its path has stayed the same. Native select controls alone do not emit a change for that case.
- Malformed storage, malformed resolver answers and transient failures preserve previous saved bytes. Clear persists an explicit empty selection. Late authority, request and picker callbacks cannot enter a newer scope.
- Indexed identity takes precedence over stale legacy paths. Only explicit legacy selection opts into the independent path namespace; its first-64 listing cannot detect modified image bytes, and the UI states this limit.
- Existing permission and active-runtime checks remain. Browser verification uses a fixture health response with real owned library/revision APIs; it performs no inspection POST or service start.

## S3-01 failed source labels

- Failed LabelMe, COCO and YOLO parse/decode/class validation retains source-relative bindings with SHA-256 of exactly the completed read bytes. A read that fails before completion creates no digest.
- Cached valid or invalid documents retain the digest paired with the cached parse. Replacing a source file does not change the provenance of previously consumed bytes.
- Default containment refusal remains; explicit `follow_links=True` retains its existing allowed-link behavior and corresponding bindings. Successful YOLO binding ordering and raster-mask recording remain unchanged.
- Failed rows retain their error/reject policy and do not acquire accepted ground truth. Source files remain unchanged. The UTF-8-only error message no longer repeats UTF-8 as its own fallback.

## S6-05 source geometry failures

- The missing-size LabelMe geometry path recognizes reproduced plain Pillow messages for truncated WebP, unsupported BMP header/depth/compression/bitfields and the existing truncated-header message.
- Exact `OSError` type with no OS errno or Windows error code is required. Permission, missing-file, OS I/O, Windows sharing and unknown failures remain visible errors.
- Damaged images remain in the saved-split gallery without geometry; valid neighboring Korean labels retain their regions. Training image decoding and DICOM behavior are unchanged.
- The failure-binding regression joins both CPU CI lists. Unknown decoder messages and native Windows acceptance remain open.

## Verification and limits

The final development tree passed 278 affected backend tests, 36 focused operator tests, all 461 renderer tests, renderer/main TypeScript checks, the browser-test TypeScript check and renderer/main build. The Linux CI-selected list passed 750 tests with two Windows file-sharing skips. The Windows-selected list passed 527 with the same two skips on this Mac; these overlapping lists are not summed. Three actual-renderer browser scenarios passed: operator choice, package cohort and invalid-image handling. The test harness also passed its 14 bootstrap checks. Each of the three source changes passed independent review, including reproduced fixes from those reviews.

These fixtures execute on macOS CPU and Chrome with the desktop host shim. Local execution of a Windows-selected list is not native Windows evidence; hosted runs of this commit remain a separate gate. The build reports the existing bundle-size advisory.

This slice does not establish GPU training/model quality, native Windows installation, field operation, cross-browser selection synchronization or completion of the full service plan. The existing containment-check/open race remains outside this scope.
