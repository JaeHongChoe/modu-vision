# Flow workspace implementation ledger

Date: 2026-10-01. Approved scope U006–U012. Implementation is complete; contract verification and native acceptance are recorded independently.

| ID | Implemented behavior | Evidence |
| --- | --- | --- |
| U006 | Selecting a graph node opens its actual selected-edge inputs, outputs, masks, counts, skip reason and timing. Inactive branch artifacts are excluded. | `flowWorkspace.test.cjs`; actual source-image CPU ROI run in native QA. |
| U007 | Draw ROI in original pixel coordinates, preview the crop, edit numerical coordinates, use arrow keys/Shift resize. Moving into an image boundary preserves size; images smaller than 16 pixels have an explicit error. | Reverse drag, minimum bounds and boundary movement contracts; native 8192×5464 input with coordinate-confirmed 256×256 crop. |
| U008 | Choose two immutable saved flow versions and up to twenty fixed inputs. Both versions open one isolated read-only input snapshot; graph and input hashes, per-image verdict/ROI/reason/node differences and intermediate results persist. Saved selections are written only after context-specific restore completes. | Actual disk reopen, changed input rejection, source mutation between A/B, no activation mutation tests. Native version comparison acceptance remains recorded in the combined ledger. |
| U009 | Execute the chosen node and ancestors on an explicitly selected computer/server. Downstream models are not required. Partial results remain REVIEW and never route a production verdict. Editing graph/image invalidates previous evidence. | Input/ROI/decision stop contracts, actual portable CPU worker transport, existing stale-result store tests; native source ROI execution and edit clearing. |
| U010 | Save full flows or processing subgraphs with immutable hashes and boundary ports. Import explicitly maps compatible model IDs and node-specific class names/IDs; numeric labels and IDs remain separate. Target vocabularies are validated when model metadata is available. Inserted modules remap every internal ID. | Roundtrip, missing/incompatible mapping, two-model class scope, target-vocabulary and ID remapping tests. Metadata-free legacy models retain explicit mapping compatibility. |
| U011 | Explain measured Blob counts/area against configured required/maximum values; show OCR text, decision reason and explicit condition/no-ROI/debug skip text. | Measured count/bound frontend contract; existing Blob/measurement/decision execution tests. |
| U012 | Left node palette, graph canvas, right properties/debug/crop and collapsible template/A/B dock. Distinguish unsaved draft, saved inactive version and active saved version; preserve undo/redo and validation; package handoff goes to step 6. | Native workspace/layout inspection, existing history/drafts/graph suites; final shared typecheck/build/package gate. |

## Behavioral checks

- Engine stop-node behavior initially failed because the execution keyword was unsupported, then passed. Decision-stop test reproduced downstream output selection before suppression; now downstream output is skipped with `outside_debug_scope` and graph identity retained.
- A/B test changed original bytes after A then restored after B. It reproduced different inspected bytes under one receipt. Both runs now read an isolated snapshot; the source remains untouched and unexpected persistent changes reject publication.
- Separate per-model class mapping and numeric label names initially failed with global mapping. Mappings now have node/name/ID scope.
- ROI boundary movement and inactive branch input display had failing frontend behavioral tests before their fixes.
- Root flow/remote/graph/execution-target batch: 58 passed before the final input/class review guards. Final delta is recorded separately in the combined acceptance ledger.
- Native flow QA executed a supplied full-resolution source image to ROI only, edited numerical coordinates with Enter, observed old node evidence removed, and reran the crop. This is a debug result, not a completed production inspection.

## Boundaries

Live remote hardware is separate from the portable worker/transport test. No costly GPU training, unrelated process stop or source mutation is performed. A/B comparison is bounded to twenty inputs and uses the explicit CPU button in the basic UI. Templates with incomplete/disconnected module ports remain editable drafts and cannot bypass graph validation. Model quality and operational approval are separate from functional execution.
