# Foreground class counts for saved brush masks

## Intent and scope

Fix the user's deferred brush-mask class-count bug. Import currently ignores
brush rows without vector geometry and substitutes defect, or loses brush classes
beside vectors. Statistics also loses palette-only labels and retains stale brush
labels on a zero raster. These are one class-count consistency change/PR.

## Approach

Add one read-only foreground-label resolver in backend/engine/dataset_summary.py,
shared locally by the flat segmentation import counter. Resolve actual positive
pixel IDs using the existing _mask_pixels normalization, active mask palette and
non-tag annotation ID/name mapping. Reject the same conflicting names as the
manifest training reader. Return distinct present labels; no pixel means no
foreground occurrence. Unmapped overlay IDs retain class_N fallback.

A route-only fallback fix was rejected because it contradicts statistics for
palette-only and zero rasters. Mutating _annotations or synthesizing training
regions was rejected because counts must not change training/evaluation inputs.

- Only use a real canonical raster under the active Studio annotation scope when
  its overlay declares mask_file; do not open arbitrary saved pointer targets.
- Import counts foreground labels once per image and leaves count_images=False
  detection object counts unchanged.
- Statistics replaces only raster-region labels, preserving explicit tag labels
  and existing labeled/background policies. Keep all non-raster/classification/
  raw-source paths unchanged.
- ID0 remains background; an empty raster never invents OK truth. Preserve
  explicit palette ID255 versus unnamed binary normalization, unused palettes,
  missing-mask behavior, schema tolerance and I/O error propagation.

## Verification and boundaries

Use real project/TestClient middleware and annotation saves. RED then GREEN for
brush-only, mixed/multiclass/dedup, unused palette, zero raster, palette-only,
active-labelset switching, conflicting mapping and ID255. Hash source images,
source labels and active overlays/raster before and after read-only counts and
reopen. Add an independent saved two-class brush browser regression for import
and reopen counts; retain the historical brush/erase/undo test bytes unchanged.
No original data, live app, models, accounts or policy changes.
