# Team data quality and workflow completion

## Intent and scope
Finish the selected existing-function workflow and team-data quality priorities. Extend the current six-stage project rather than add disconnected utilities. Scope includes shared labeling guidance, assignment, safe concurrent edits, two-person review, approved-data training readiness, exact model handoffs, project compatibility and configured distribution readiness. Hardware adapters and new model families are outside this change.

## Constraints
- Preserve source image and annotation bytes. All editable overlays, books, leases and receipts live in project storage.
- Public code, docs, tests and commit messages use generic product terms; no comparison-company names, private source paths or credentials.
- Existing projects retain their training eligibility policy until team review is enabled explicitly.
- Execution job IDs and saved model IDs are distinct. Cancellation addresses the execution; evaluation and flow address the saved model.
- Any authorization-enabled request derives its actor from the authenticated session. Local desktop mode supports named operators.
- API, integration, native UI and physical-device acceptance are different evidence states.
- Signed distribution requires a real signing identity and configured release channel; software must never invent that evidence.

## A. Team-data server contracts
Store labelbook versions and policy in project storage. Each immutable version contains stable category IDs, names, colors, definition, inclusion/exclusion guidance, annotation guidance and source-relative example image references with content hashes. Active labelset scopes assignments, edit leases and reviews.

`/api/team-data` exposes current book/history, publish, settings, image assignment, lease acquire/renew/release, review vote/adjudication, work queue and readiness. Mutations use expected versions/revisions and actor identity. The implementation owns metadata/annotation write guards so the check and write occur under the same metadata transaction.

An edit lease has owner, unpredictable token and expiry. When team editing is enabled, annotation/category save requires a valid owned token and matching revision. Another operator receives a readable conflict and cannot overwrite. Expired leases may be acquired; stale tokens cannot write or release a later lease. Existing projects with team editing disabled preserve current revision protection.

Two-person review means distinct people inspect one saved annotation. Votes bind to image, annotation and mask hashes, labelbook version and policy. Reject duplicate reviewers and self-review if enabled. Rejection/disagreement needs a reason; explicit adjudication records the deciding reviewer and reason. Content changes, restoration or guidance changes invalidate prior votes and approval. Direct metadata approval must not bypass the policy. This is not described as independent double annotation.

Assignments validate project members in shared-account mode; local operator names remain supported. Roles separate book/policy management, assignment, edit and review. Example paths must resolve inside the registered source and cannot follow links outside it.

## B. One training and model handoff flow
All ten families advertise the same actual remote capability as the runner. Their selected architecture, parent, full config, prepared dataset and execution target survive submission. Preflight checks the selected local/server environment without downloading models or launching training.

Task inventory supplies `execution_job_id`, `model_id`, family, prepared input and provenance. Preserve exact identity across task center, evaluation and flow; do not deduplicate records merely because their names look alike. A selected specialist model enters a scoped flow handoff. The flow offers adding/selecting a compatible model node without replacing the user's DAG. GAN retains generation/review/adoption semantics.

Training readiness reports counts of approved, pending, rejected and unused images and concrete blockers. With approved-only policy enabled, loaders must exclude unapproved samples rather than just display a warning. The training binding records book/policy/review eligibility identity separately from image fingerprints. Held-out splits are preserved; error feedback does not silently enter the test set or train from it.

## C. Team-data user experience
One team panel is mounted in labeling. It shows the active book and history, project members/operators, assigned work, review progress and the selected image's lease/review state. Basic forms use image selectors and class names, with IDs/hashes in details. Buttons show busy, failure and stale-state outcomes.

Use the existing error-review queue and return-to-origin flow. Add a transition from completed review to training readiness/parent model selection. A queue can filter assignee, pending/rejected/approved, errors and uncertain/disagreement candidates. Prioritization is based on stored evidence, never fabricated confidence.

Label changes visibly warn that approval and downstream data/model/flow evidence may need refresh. Existing version/history and invalidation mechanisms remain authoritative. Destructive actions preserve recoverable project records.

## D. Compatibility and delivery
Project opening validates schema before any relocation, labelset migration, activation or manifest write. Missing legacy schema is migrated to current schema 1 with a durable original-manifest backup and transaction receipt. Preserve unrecognized metadata; reject unknown future versions without changing files. Explicit preview/apply is idempotent and recoverable after a failed write.

Readiness uses the actual application version and exposes real native signing evidence and configured update state. A configured channel validates update version, platform, architecture and package hash before handoff. If automatic update support or signatures are unavailable the UI states the exact prerequisite. No release is published automatically.

Background service registration must be idempotent, persist across reboot and resolve the current approved release instead of pinning an obsolete package. Platform commands and supported-platform states are explicit; never mark an untested OS as native verified.

## Acceptance
Focused concurrency tests use two independent clients and stale tokens/revisions. Review tests cover self-review, duplicate vote, disagreement, book/content mutation, restoration and approval bypass. Training tests cover enabled/disabled eligibility and pinned provenance. Handoff tests cover remote execution/model ID aliases, stale project selection and config parity. Compatibility tests cover future schema, missing schema, relocation, interrupted migration and repeated apply.

Run affected backend/frontend tests, typecheck/build, broader regression once integration is stable and a fresh independent review. Exercise selected routes in an isolated native app with source originals read-only. Track each of ten families separately; incomplete native routes remain pending. Real signing, distribution, external equipment and full training-quality approval remain separate requirements.
