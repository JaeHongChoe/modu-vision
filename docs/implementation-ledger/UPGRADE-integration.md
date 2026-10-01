# Shared design and functional acceptance

Date: 2026-10-01. Scope U026–U030 and integrated acceptance of U001–U033.

| ID | Delivery |
| --- | --- |
| U026 | A global workspace bar provides task center and delivery diagnostics. New workspaces consistently show loading, disabled actions, inline failure/status text and readable controls. Routers enforce existing project roles plus narrowly scoped new privileges. |
| U027 | Basic preparation uses project-image pickers and model selectors. Manual path/TSV/JSON and runtime identifiers are disclosed in details. Error actions explain and perform actual next-step changes; unknown actions remain unresolved. |
| U028 | Guidance collapses and remembers the choice. Flow images/debug panel use flexible width and larger text. Training plot title and loss legends describe the measured values. |
| U029 | Global focus-visible styles, reduced motion, focus-trapped dialogs with Escape/focus return, keyboard ROI movement/resize, semantic selectable image buttons and explicit textual status alongside color. |
| U030 | A separate 33-requirement JSON registry, path/status checker, owner ledgers and this functional acceptance ledger retain every approved item. Implementation, integration verification, native persistence/reopen/failure/handoff and external hardware acceptance remain distinct. |

## Combined contract verification

- Backend full regression: 1541 passed, 27 skipped, zero failures in 455.38 seconds. Supplied real-image read-only fixture enabled. No optional costly remote/GPU test enabled.
- Backend full log SHA256: `7fc9a276d931544ed59a0054d83dd144f8ef76a2a4fca138b25d5547dde1506e`.
- Frontend behavioral regression before final review delta: 70 passed, zero failures.
- Production build passed; unpacked native bundle verification: 40 passed, zero failures. Current-source equality checked for renderer, main process, backend and SDK files.
- Final review delta runs and native persistence/reopen results are appended below as they complete.

## Native application observations

Isolated owned desktop app and project. Supplied original source is read only. Fixture OCR truth below is explicitly a functional test label, not a manufacturing-quality assertion.

1. Connected actual dataset: 88 original images; 80 annotated, 8 unannotated. Segmentation split readback: 56 train,16 validation,8 test. Flat NG-only classification selection rejected with an actionable prerequisite error.
2. DINOv3 readiness returned locally cached weight presence and explicitly kept execution/model-quality approval unverified.
3. OCR project-image picker loaded 88 images. A basic image+human text row was added. A three-image test table rejected validation characters missing from the training alphabet. Corrected functional truth passed; immutable three-image prepared dataset appeared in the saved dataset picker.
4. Actual CPU OCR one-epoch task completed. Task center reopened exact saved task, showed epoch1/1, actual termination confirmation and reservation return independently. Checkpoint and job receipt persisted on disk. Found and repaired the specialist evaluation handoff defect instead of accepting the failed button action.
5. Flow workspace opened node palette/canvas/properties. Selected actual 8192×5464 image. ROI drawing/keyboard and numeric Enter confirmation worked; editing cleared old evidence. CPU selected-node execution returned one real crop; downstream model nodes remained skipped and whole-image production verdict stayed REVIEW.
6. Saved a processing-only ROI module through the native template button. Selected two actual fixed test images; test set showed 2/20. Actual ROI was 256×256 at original coordinates [4102,2323,4358,2579]. The debugger showed source input and the actual crop; result-node action returned to the editor. Full run/save stayed blocked while its model prerequisite was absent.
7. Reopened saved data diagnostics through the native readiness disclosure: 88 images, 8 unlabeled, 88 blur candidates and 54 near-duplicate candidates. These bounded-thumbnail diagnostic thresholds are review aids, not camera or manufacturing quality approval. Review found a stored split-display mismatch and added it to the final repair gate.
8. Native immutable image edit initially rejected the missing actor. Saved a functional QA actor, then generated a 90-degree derived image with one transformed polygon and persistent source/derived hashes. Review found its polygon's secondary bbox cache stale and added it to the final repair gate. The original was never overwritten.

## Original source preservation

After training, derived editing and delivery API acceptance, all 171 original file paths and SHA256 hashes matched the initial manifest. Final manifest digest: `cce2d763211ff5ce0a2dccec28014b5d02083aa21fb19f8003aacf31d96bf935`. This includes 88 images and 80 annotation files; the remaining files are source metadata. Private paths and filenames remain outside the public ledger.

## External acceptance limits

Physical GPU/MIG/NPU/Edge, camera/RTSP, customer PLC/MES addresses and external VLM credentials are not available in this QA environment. Their configurable adapters, role guards, failure cases and local real protocol transports are tested; they are not marked operationally approved. .NET-dependent C# execution remains gated by the absent SDK. Automatic update provider and signed installer distribution are separate configuration requirements and are shown as such in the UI.

## Final regression and native repair acceptance

Final source frozen after independent review. Earlier results above are intermediate snapshots; these are the final gates:

| Gate | Final result | SHA256 of private evidence log |
| --- | --- | --- |
| Complete backend suite with supplied read-only image fixture | 1555 passed, 27 skipped, 0 failed; 411.12 seconds | `1fbc3686b8c83e4a525a04c1391d9580d4148f08aaa1b6a20b0afde8c6301472` |
| All frontend behavioral suites | 115 passed, 0 failed | `f28713104c75eed5d34b23293cd330f052630b526adce72284807ecc7f031cac` |
| TypeScript and production build | passed | Build output inspected directly |
| Final unpacked native bundle integrity | 40 passed, 0 failed | `db01def53f69b68e0560c06d13a9ca48debba43ae8d9fb2429ed55415c680b1a` |
| Requirement inventory and independent review | All 33 IDs have source and evidence; no new blocking finding | Program checker and owner ledgers |

Two old backend tests initially failed after the new project-image boundary guard. Their external-path fixtures were replaced with images in the actual current project dataset; the application guard was retained. The successful full regression above includes both corrected fixtures. Optional unavailable device/runtime cases remain skipped, not passed.

### Actual desktop repairs and readback

1. Exact task handoff: the saved one-epoch OCR task reopened its own model and prepared three-image dataset, closed the utility dialog and selected the OCR workbench. After a newer task existed, selecting the older task again still restored the older model. Its held-out CPU evaluation ran on the supplied image-derived fixture. Test text and results are functional evidence only.
2. True cancellation: an owned CPU OCR task requested 500 epochs solely to test cancellation. The native stop button produced `stopping`, then `stopped` at epoch 20; the submit button returned. The task record separately confirmed termination acknowledgement and reservation return. Both remained visible after complete app restart. No unrelated job or GPU process was cancelled.
3. ROI persistence: an unfinished fixed-ROI flow saved automatically without a connected model. The explicit **초안 저장** button also succeeded. An additional edit survived stage navigation and complete app exit/relaunch. Final original-pixel bbox: `[4103,2323,4359,2579]` (256×256). Saved draft graph SHA256 before/after restart: `697d36b24b5dd7bf15de27c623818247f85126b8b88c30f77f96e34061aeb03d`. The UI showed **초안 저장됨 · 실행본 활성화 전**; execution-package handoff stayed disabled. A draft was not silently activated.
4. Fixed test selection: the same two original images restored as `2/20` after the backend's ephemeral port changed on restart. No migration from an older unrelated QA key was used.
5. Visual layout: actual native screenshots confirmed readable toolbar/resource rows and a nonzero canvas both with the comparison dock collapsed and expanded. The workspace scrolls instead of collapsing its canvas to zero height or overlapping controls.
6. Data repairs: rerun diagnosis showed current saved splits and cleared the stale-report notice. A second immutable rotated version preserved the original and recomputed the polygon bbox correctly. Details are in the data ledger.
7. Delivery: native Modbus and HTTP local test buttons each received a real loopback ACK. The UI explicitly distinguished this from physical equipment verification. A native save dialog exported diagnostics whose readback omitted personal paths, credentials and image data. Exported diagnostics SHA256: `93e8c7e2421881d1882784e2cb334f788128d373a80f689c662523ebd4c42588`.
8. Independent API acceptance separately exercised 31 delivery cases, including a real CPU package on three source images and ACK/reject/timeout cases over both local transports. The training ledger records genuine cached DINOv3 CLS/SEG/Patch and YOLO one-epoch CPU training, checkpoint reload and held-out inference. These do not approve manufacturing accuracy or a production model.

### Final preservation and publication scope

After the final native restart, all 171 original file paths and content hashes still exactly matched the pre-QA manifest. The final serialized manifest SHA256 is `e3d79f35552d28d0b86932a2ae271c0cb950b435f3eb3fa0dd4e8edfc01398ac` (serialization differs from the earlier snapshot; source content did not change).

Every requirement is integration verified. Native acceptance fields are marked only for observed routes; unexercised complete routes stay pending. The release bundle is an unsigned local QA build. New public changes were checked for company references, personal paths, secrets and accidental artifact/symlink inclusion. Existing remote commit history is preserved and is not claimed to have been scrubbed.

## Publication status

- Implementation commit `c603418` was fast-forwarded into the clean local `main` checkout.
- HTTPS push failed because no usable credential was found; explicit existing SSH identities also returned `Permission denied (publickey)`. This is an authentication prerequisite, not a test failure.
- Before authentication recovery, remote `main` remained at baseline `02b67318c834bd364610d409471c2306c2681d24` at the readback check.
- Authentication was then completed through the official CLI browser flow. The push succeeded, and remote HEAD readback matched local `main` at `d6c7c3a6c5dc6d3c89b29ee4d2ce014761b3095c`. The implementation and validation delivery is published. The repository's existing origin now uses HTTPS with the configured credential helper for subsequent normal pushes. No account password, access token or authorization code is stored in project files.
- The publication checkbox is complete. Existing history was preserved; no force push was performed. This receipt update is a documentation-only follow-up to the verified delivery.
