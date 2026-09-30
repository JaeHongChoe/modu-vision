# P01 flow correctness

## Scope and state

Backend flow implementation for F027, F073, F074, F079, F122 and F123 is ready for integration review. F021 dataset distribution/filtering is owned by the dataset integration work. Native controls, API TypeScript declarations, user workflow acceptance and registry status are owned by integration; this ledger does not claim those gates have passed.

| Feature | Implemented evidence |
| --- | --- |
| F027 | Tile and ROI inference retain every segmentation channel, including background, with recorded class names and source-coordinate lossless mask/probability rasters. Secondary classes can select a class predicate branch. |
| F073 | Explicit `defect_presence` and `required_structure` Blob modes, class selection, component counts and areas, per-class bounds and saved measurement/violation records. Measured zero differs from absent evidence. |
| F074 | Mean RGB-to-grayscale intensity from original source pixels: component intensity filtering for defects, area-weighted intensity bounds for required structures. |
| F079 | Original and corrected OCR transcripts, explicit character correction map and zero-based allowed/fixed-character position rules. Original recognition is preserved. |
| F122 | Class-specific probability thresholds preserve unfiltered probability rasters while controlling thresholded class evidence. |
| F123 | Per-class connected-component minimum/maximum area filters use original-image pixels, including after transformed ROI projection. |

## Changed files

- `backend/engine/flowchart_engine.py`
- `backend/engine/segmentation_evidence.py` (new)
- `backend/engine/rule_evaluation.py` (new)
- `backend/tests/test_flow_class_evidence.py` (new)
- `backend/tests/test_flowchart_flow_contract.py` (one existing area fixture now uses source-sized model input)
- This ledger.

## Integration contract

### Segmentation inspection

Optional node parameters:

```json
{
  "class_ids": [1, 2],
  "class_rules": [
    {"class_id": 2, "probability_threshold": 0.7, "min_area_px": 8, "max_area_px": 1000}
  ],
  "min_defect_area_px": 8
}
```

- `class_ids` and rule IDs are unique foreground integers greater than zero. Omission selects every foreground class. Background channel zero is retained as evidence and is never selected into the defect union.
- Class probability comparison is strict `>`, preserving the previous segmentation boundary. Area ranges are inclusive. Class area limits filter individual 8-connected components; the existing `min_defect_area_px` is the union area required for NG. Optional `max_defect_area_px` bounds that union gate.
- Checkpoint `classes` supplies names. Legacy two-channel checkpoints without names use `background`/`defect`; larger unnamed models use indexed names. An optional `class_names` override must match stored checkpoint names and channel count.
- `crop.segmentation_classes` contains `{class_id,class_name,selected,area_px,raw_area_px,probability_threshold,confidence,mean_grayscale,bbox,source_transform,mask,probability}` for every channel.
- `mask` and `probability` are `{dtype,encoding:"zlib_base64",shape:[height,width],data}`. Masks use uint8; probabilities use float32. Raster indices are relative to the crop's original-image `bbox`. The homogeneous translation in `source_transform` maps those indices to source coordinates. Mean grayscale is null when the selected class mask has zero area.
- `crop.mask` remains a bounded union preview; it is not the lossless per-class raster. `map_semantics` is `segmentation_probability`. A measured empty segmentation has `defect_area_px:0`, rather than null.

### Blob node

```json
{
  "rule_mode": "required_structure",
  "class_rules": [
    {"class_id": 1, "min_count": 1, "max_count": 2,
     "min_area_px": 100, "max_area_px": 2000,
     "min_mean_grayscale": 80, "max_mean_grayscale": 200}
  ]
}
```

- Default `rule_mode` is `defect_presence`. Existing nodes without class selection/rules still count connected components of the foreground union, preserving touching-class and binary behavior. `min_blob_area_px` and `min_blob_count_for_ng` retain their existing defaults of one.
- `required_structure`: count bounds apply to the measured class components; area bounds apply to their total area; grayscale bounds apply to their area-weighted mean. Default minimum count is one. Any violated bound yields NG. A measured empty class is a zero count/area; it can fail a required minimum. Missing class rasters yield REVIEW instead.
- `defect_presence`: area and grayscale bounds select individual components. `min_count` overrides the legacy NG trigger count; an explicit `max_count` also triggers NG when exceeded. An empty measured mask stays OK even when the configured minimum trigger is zero.
- Optional `class_ids` restricts the measured classes; otherwise explicit rule IDs are measured. Class count/area bounds are nonnegative integers; intensity bounds are finite numbers in `[0,255]`. Inverted bounds, boolean IDs and explicit null numeric bounds are rejected before inference.
- `crop.blob_measurements` saves `{class_id,class_name,evidence_present,count,area_px,largest_blob_area_px,mean_grayscale,components,rule_mode,violations,verdict}`. Each component saves its measured area and mean grayscale. Existing `blob_count` and `largest_blob_area_px` remain available.

### OCR inspection

```json
{
  "expected_text": "AOB",
  "correction_map": {"0": "O"},
  "position_rules": [{"index": 1, "allowed_chars": "OX", "fixed_char": "O"}]
}
```

- Existing exactly-one `expected_text` or `regex` requirement remains. Character corrections are explicit, simultaneous one-character mappings; no implicit correction is applied.
- The expected/regex and position rules operate on the corrected transcript. Positions are zero-based Unicode character indices. Missing required positions fail. `allowed_chars` is a nonempty string; `fixed_char` is exactly one character and must belong to an accompanying allowed set.
- `recognized_text` and `original_text` preserve model output. New fields are `corrected_text`, `correction_applied` and `rule_violations`; violated position records include their index and observed character.

### Package integration

The package builder recursively includes engine modules, so the two new helpers are copied with the flow runtime. New class/Blob/OCR fields are present in serialized crops and node artifacts. Integration has extended explicit package parity comparisons with `segmentation_classes`, `blob_measurements`, `original_text`, `corrected_text`, `correction_applied` and `rule_violations`. A regression independently compares actual packaged class rasters and Blob records, in addition to checking final verdict.

## Verification evidence

Test-first observations:

- The initial 17 focused tests failed on the previous implementation: secondary foreground class discarded, model-input area instead of source area, empty-mask area recorded as null, absent required-structure/brightness rules and absent OCR correction/position evidence.
- Additional failing checks covered background-channel retention, legacy unnamed binary class labels, explicit null numeric thresholds and zero-count defect rules inventing NG.
- The focused five-file gate passed **104 tests, 1 skipped**. The final fresh expanded 17-file flow regression gate passed **208 tests, 1 skipped in 48.38 seconds** after the source-coordinate label/message cleanup.
- Raster tests use actual RGB arrays and tensor predictions. Package parity uses a real three-channel UNet checkpoint with deterministic weights, exports the complete flow and runs its CPU CLI in a separate process with an empty external `PYTHONPATH`; masks, probabilities and Blob measurements match the reference exactly. This is functional evidence, not an accuracy claim.
- New modules and the new test file pass Ruff. Existing minimum-area regression now pins model dimensions to the source (60×40), so its one-pixel boundary represents an original pixel rather than an unresolvable single model pixel in a 224×224 resize.

Relevant gate command:

```sh
PYTHONDONTWRITEBYTECODE=1 /opt/anaconda3/bin/python -m pytest -q -p no:cacheprovider \
  backend/tests/test_flow_class_evidence.py backend/tests/test_flowchart_engine.py \
  backend/tests/test_flowchart_flow_contract.py backend/tests/test_flowchart_typed_completion.py \
  backend/tests/test_flowchart_aggregate_nodes.py backend/tests/test_flowchart_editable_graph.py \
  backend/tests/test_flowchart_patch_nodes.py backend/tests/test_flowchart_model_verification.py \
  backend/tests/test_flowchart_drafts.py backend/tests/test_flowchart_execution_target.py \
  backend/tests/test_flow_model_handoff.py backend/tests/test_flow_specialized_api.py \
  backend/tests/test_flow_package.py backend/tests/test_remote_portable_flow.py \
  backend/tests/test_specialized_flow_export_route.py backend/tests/test_dino_flow_threshold_precision.py \
  backend/tests/test_report_flow_contract.py
```

## Remaining acceptance and risks

- Full backend, TypeScript and native save/reopen/end-to-end acceptance remain integration gates. No native application or remote job was manipulated for this package.
- Lossless evidence size grows with source resolution and class count. Thumbnail caps do not cap the complete result JSON; no fixed payload-size guarantee is made.
- Area is now consistently original-pixel area for ROI segmentation. A threshold previously chosen using resized model pixels may require an explicit user review of its unit; numeric defaults and binary probability/component behavior are retained.
- Learned rotation preprocessing is a P06 follow-up; this package verifies existing fixed-angle preprocessing and source-coordinate restoration.
- No operational or model quality approval, hardware performance claim, commit or publication is included.
