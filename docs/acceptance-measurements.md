# Reproducible acceptance measurements

## Scale and fault controls

`scripts/acceptance/scale_and_soak.py` measures an owned 10k/100k metadata-only
SQLite fixture, actual separate large-image decode, host memory/disk, p95 search
and keyset paging. Its durable queue controls exercise duplicates, backpressure
and reopen with REVIEW results and no model execution. Metadata rows are not a
validated photographic dataset, and a short loop is not 72-hour operation.

`scripts/acceptance/failure_matrix.py` runs exact existing selectors for ten
failure scenarios. Source files are hashed before/after; JUnit skipped, absent,
failed or errored controls leave a scenario pending. Injected disk-full/OOM,
transport and recovery controls remain separate from real hardware fault
qualification. A model deployment recovery ledger is not an application
installer/database cutover recovery receipt.

## Actual model endurance

`scripts/acceptance/inspection_endurance.py` requires pinned local checkpoint
and image-manifest hashes. It uses the actual CPU flow engine and durable inbox
at a bounded cadence with one CPU computation thread, records every prediction,
reopens the same queue and checks duplicate/backpressure controls. Every control
result is published as REVIEW. It never activates a production recipe or sends
camera/PLC/MES output. Source/checkpoint/input changes, missed intervals,
interruption, disk reserve or output quota invalidate the run and retain a
failure receipt. Short durations cannot set `soak_72h_completed`.

The default duration is 259200 seconds and interval 60 seconds. `receipt.json`
is an atomic heartbeat and `intervals.jsonl` retains per-input latency/verdict.
The host must remain awake throughout. Completed CPU model endurance is still
distinct from approved standalone service operation, target hardware takt,
network reconnect, delivery acknowledgment and process-quality approval.
Run those on the declared deployment target with its accepted package.

## Exact CUDA resume

`scripts/acceptance/cuda_resume_control.py` measures a bounded real CUDA model
with optimizer, scheduler, AMP, early-stopping and Python/NumPy/Torch/CUDA RNG
restore. It compares the exact next loss and every state against uninterrupted
execution, and refuses changed recipe identity without altering the checkpoint.
Use an owned GPU reservation and frozen code. This is a component resume control;
it is not a completed Studio AutoDL search, DDP or model-quality evaluation.

## Owned global forward recovery

The explicit `backend.engine.global_migration` CLI now provides `preview-forward`
and `advance --expected-source-sha256` for an already owned, drained, exact-current-
schema generation. It preserves post-cutover control writes, checks the original
installation identity, namespace authority and source CAS, retains prior generations
and backups, revokes copied login sessions and increments the admission fence.
Prepared pointer cutover can be finished from its journal. Restore is refused after
normal target writes; a new forward snapshot is required instead of losing them.

These controls operate on explicitly initialized isolated installations. They do
not adopt a live training worker, reconcile an uncertain lease, convert an unknown
historical schema or perform installed application/database upgrade cutover. Those
operations remain unsupported and block automatic migration.

## Required detector objects and qualified measurement verdicts

Axis-aligned detector nodes may explicitly set `params.object_requirements` to
1–64 unique recorded foreground classes, each with integer `min_count` and an
optional `max_count` (0–1,000,000). They feed the decision directly through
unconditional result edges using `any_defect_is_ng`. Completed trained detector
boxes that pass confidence filtering are counted across the selected input ROIs.
Overlapping input ROIs can count one physical object more than once; these are
box counts, not a physical-object deduplication guarantee.

Missing/excess counts produce NG without fabricating an image region. Per-class
counts and bounds remain in `execution_steps[].count_rule_results`, including
zero. Missing vocabulary, unsupported routes and invalid bounds are refused.
Untrained output cannot satisfy the count rule, and incomplete execution retains
REVIEW (or the explicit conservative NG policy). The actual YOLO GUI training
scenario checks rule editing, stored draft/reopen and both count outcomes.

A physical-unit threshold without a recorded calibration reference and verified
acquisition cannot produce a qualified measurement verdict: the crop and default
flow outcome are REVIEW, and the viewer uses original pixel values with a clear
notice. Historical raw arithmetic remains preserved as evidence.

Selected ROI/model subgraphs are saved and remapped through the actual Chrome
and development Electron UI. Their boundary ports require explicit wiring;
insertion does not activate a saved execution version.
