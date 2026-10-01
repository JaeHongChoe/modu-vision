# Product workflow upgrade design

## Goal
Finish the approved end-to-end usability and operational improvements without omitting a supported model family or treating an unverified capability as accepted.

## Architecture
Extend the existing project/dataset/model/flow/runtime contracts. Preserve IDs, immutable originals, scope checks, active graph identity, cancellation acknowledgements, candidate review and explicit model approval. Add persistent inventories and narrowly scoped APIs rather than independent mock workbenches. CLS, SEG and Patch use genuine pretrained DINOv3; detection uses pretrained YOLO.

## User experience
Stage 5 is the inspection composition workspace: node/template palette, canvas, image ROI editor, linked execution evidence, fixed test set comparison and version state. Other stages provide common model preparation, task monitoring and clear next actions. Technical paths and IDs remain available in details. Operator view exposes product/recipe, input health, REVIEW handling and active runtime identity.

## Requirements and ownership
- **U001** (training): Action-specific error resolution with verified effects.
- **U002** (training): Common GUI preparation and workflow for every supported model family.
- **U003** (training): Unified resumable task center and honest cancel/resource states.
- **U004** (training): Model and runtime readiness assistant.
- **U005** (delivery): Persistent package library with optimization and deployment re-entry.
- **U006** (flow): Node-linked input/output debugger.
- **U007** (flow): Image-based ROI editor and live crop preview.
- **U008** (flow): Fixed-set flow version comparison.
- **U009** (flow): Run to selected node and invalidate stale results.
- **U010** (flow): Reusable subgraph templates and explicit cross-project mapping.
- **U011** (flow): Human-readable decision evidence.
- **U012** (flow): Flow workspace navigation and draft/saved/deployed states.
- **U013** (data): Unified data readiness including blur/exposure/near duplicate diagnostics.
- **U014** (data): Non-destructive image edit with annotation transforms and version history.
- **U015** (data): Review prioritization for errors/disagreements/threshold cases.
- **U016** (data): Resumable review queue and origin return.
- **U017** (training): Auto-training budgets and progress.
- **U018** (data): Korean condition labeling with image examples and configurable real VLM provider.
- **U019** (training): Explicit anomaly image-versus-region purpose and evaluation profile.
- **U020** (data): Independent object direction targets alongside axial OBB angle.
- **U021** (delivery): Generic server connection wizard with real input preflight.
- **U022** (delivery): Device capability, live verification and approval state separation.
- **U023** (delivery): PLC/MES forms, advanced JSON and local receiver tests.
- **U024** (delivery): Dedicated operator inspection workspace.
- **U025** (delivery): Install/update/compatibility checks and redacted diagnostics.
- **U026** (shared): Common buttons/forms/status/loading/errors.
- **U027** (shared): Names and next actions in basic view, technical details collapsed.
- **U028** (shared): Collapsible guidance, larger imagery and legible training visualization.
- **U029** (shared): Text-and-color states and keyboard focus/navigation.
- **U030** (acceptance): GUI, persistence, reopen, error/cancel and downstream handoff acceptance.
- **U031** (delivery): SDK dependency and installation readiness for Python/C++/C#.
- **U032** (training): Clear replica-DDP, frozen-encoder and finite search capability boundaries.
- **U033** (delivery): Explicit hardware verification matrix for GPU/MIG/NPU/Edge.

## Persistence and interfaces
- Product upgrade registry is separate from the prior feature program. Status distinguishes planned, implemented, integration_verified and accepted. Evidence must name actual execution, never a passing count as equivalence.
- New backend routers are registered by the integration owner. Frontend shared mounts (App.tsx and global CSS) are integration-owned. Agents report exported components and router names.
- Dataset edits create derived files in project storage, preserve original bytes and source/version metadata, and transform all supported shapes. Reject ambiguous unsupported transforms rather than silently dropping labels.
- Flow partial runs identify original graph hash, requested stop node and executed subgraph. Any graph/image change invalidates prior evidence. A/B uses identical immutable input IDs/hashes and explicitly selected saved versions.
- Templates store graph schema and class/model requirements. Import across projects requires mappings and validates compatibility before save.
- Package inventory binds project/task/source/flow version and content hash. Deployment and optimization use selected saved package, not arbitrary stale UI result.
- Task center preserves source/transport identity and reconnects the same job. Requested cancellation is distinct from a terminal acknowledgement and reservation release.
- External VLM providers are opt-in configured providers; results remain candidates requiring review. Secrets never appear in diagnostics or committed examples.

## Limits and acceptance
Physical equipment and unavailable hardware remain unverified. Local protocol mocks prove contracts only. DDP replicates the encoder/model; no sharded model-parallel claim. Finite search and frozen pretrained encoders are explicit. C++/C# bridge installation includes Python runtime prerequisites. Manufacturing accuracy is outside functional acceptance.
