# Bind recoverable retention to the reviewed preview

Continue the existing S7-01/U030 acceptance work and S5-09 retention contract. The native control currently sends only paths after a preview, so changed bytes at the same path can be moved without a fresh review. Keep recoverable movement and existing source/label/model/pin/lifecycle protection.

1. Reproduce stale file content, directory membership, policy, project scope and numeric/foreign preview refusals with private owned artifacts.
2. Return a canonical preview digest binding the project/root identity, effective policy, current pin set and selected path inventories/mtime. An explicitly supplied digest must still match under the existing lifecycle lock before any move starts. Existing explicit API callers may continue to request current-policy movement without a preview; the app always sends its preview digest.
3. Disable native movement unless its reviewed response has a valid digest. Preserve its error and original artifacts when stale; a new preview enables explicit retry. Do not add automatic deletion or retry.
4. Verify actual browser and macOS Electron stale preview, empty/invalid inputs, failed request, recoverable move/restore and reopen using owned inert artifacts. Bind clean-source receipts to exact actions; keep model/industrial/Windows/independent acceptance separate.
