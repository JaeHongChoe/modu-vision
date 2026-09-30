# Workflow completion design

## Intent and acceptance

Implement the user's approved foundation and phase 1–4 feature table in the existing desktop studio. The purpose is complete operator workflows, not benchmark accuracy. A feature counts as implemented only when its persisted inputs, UI, execution, errors, and downstream handoff work. A model family additionally needs label → train → evaluate → flow → whole-flow package. Real hardware acceptance and model-quality approval remain separately recorded.

## Shared contracts

- Keep Electron/FastAPI, existing project isolation and verified package hashes. Add small domain modules rather than duplicate execution logic.
- Preserve legacy graphs and data. Migrate lazily with stable UUID identities and optimistic revisions; source images are never modified.
- Source ownership, labelset, dataset fingerprint, model hash, graph hash and inspection row identity must remain inspectable after restart.
- Missing ROI, malformed rule, absent model, device disconnect or failed inference is REVIEW/error and cannot become OK.
- Korean UI follows existing compact desktop panels, consistent labels, accessible focus, clear empty/error/progress states. Do not embed implementation jargon in operator actions.
- No third-party product/company references, private data, credentials, checkpoints or images in Git. Preserve required dependencies and license notices.

## A. Data, labeling and identity

Persist project-scoped image UUID/content version, free tags, product, lot, group, workflow state (`unworked`, `needs_review`, `approved`), reviewer and append-only audit records. Annotation changes invalidate approval. Reject stale concurrent edits. Metadata and history must be included in backups and dataset versions. Expose review and provenance in dataset/label screens; jump from evaluation/inspection errors to the exact source image in stage 2.

Support LabelMe, COCO and YOLO annotation import/export with image-coordinate preservation, safe paths, explicit task limits, conflict preview, and round-trip tests. Split by product/lot/group without leakage; detect identical-content duplicates across splits before training. Model candidates remain unapproved until a human accepts them. Text/keyword constraints and image-exemplar proposals must state their actual backend; unavailable semantic model support must never fabricate proposals.

## B. Typed flow and specialized models

Add class-based conditional edge predicates alongside OK/NG/REVIEW. Add reusable patch-split and preprocessing operators (rotation, alignment, image improvement), typed validation, and source-coordinate transforms. Persist per-node image/mask/evidence so the editor can inspect each executed/skipped node.

Connect OCR recognition and expected-text/regex rules to catalog, flow, evaluation and package. Extend rotated detection beyond the current single box where model architecture permits. Preserve anomaly maps for pixel evaluation and Blob/mask consumers, with distinct classification/region modes. GAN generation requires review/adoption into training data rather than automatic labeling. All tasks exposed as supported must use the same checkpoint loading/execution path in app and exported runtime.

## C. Evaluation and field runtime

Persist immutable reevaluation runs and full-dataset comparison jobs with progress/cancel/restart states. Aggregate errors by class/product/lot from real metadata. Keep release approval strict and independently attributable. Add explicit approved-release deployment and rollback to a managed service, with transactional package verification, runtime readback and old package preservation.

Add configurable Modbus TCP and HTTP MES/device adapters with timeout, result acknowledgment and disconnect REVIEW; no unsolicited writes to real hardware. Provide non-Python HTTP clients and installation/launch controls for the independent service. Use a process-shared SQLite reservation ledger with heartbeat/lease/reconciliation for compute, maintaining cancellation and remote reconnect behavior. Add explicit CPU/CUDA/MPS runtime selection with fail-fast availability and CPU-compatible packaging; unsupported Edge hardware remains explicit.

## D. Operator integration and acceptance

The unified provenance view links image/label/split/model/flow/result IDs and hashes. Every new workflow must be reachable through the native application and survive reopen. Verify original real images through saved graphs, exported packages and service restart; do not invent OCR transcripts, OK labels or hardware test results. Representative CPU training is authorized; shared GPU ownership must be freshly proved before use. Existing user jobs/apps are preserved.

## Execution decisions

The user has repeatedly approved the feature table and now explicitly requested all remaining work. Continue the existing architectural plan without a new approval cycle. Implement independent domains in parallel under disjoint file ownership; the controller owns app registration, shared REST client interfaces and final integration. History-replacing Git publication remains separately pending its existing approval request.
