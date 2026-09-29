# Remote Compute Implementation Plan

> **For agentic workers:** Use `superpowers:subagent-driven-development` or `superpowers:executing-plans` task by task. Each task has its own tests and review.

**Goal:** Add configurable SSH compute servers, starting with a remote QA server, while preserving the local six-stage workflow and model provenance.

**Architecture:** The local FastAPI daemon remains the renderer's only API. It stages task-ready input, launches a versioned remote worker over verified SSH, tracks durable job state, and retrieves hash-checked artifacts. Compute operations use the server associated with their model job; local mode remains the default.

**Tech Stack:** Electron/React/TypeScript, FastAPI/Python, OpenSSH, Python worker, PyTorch, optional Docker CUDA runtime.

**Spec:** `docs/superpowers/specs/2026-09-29-remote-compute-design.md`

## Global constraints

- Do not expose a remote HTTP API or renderer credential.
- Do not hard-code the remote QA server into product logic; configure it as one SSH profile.
- Preserve local dataset/annotation/split ownership and source fingerprint.
- Never infer cancellation or completion from a lost SSH connection.
- Do not start a GPU training job on the remote QA server during implementation without a separate resource decision.

## Review focus

1. A LabelMe Studio annotation and saved split must enter the remote training snapshot, not just the source folder.
2. A symlinked image must be transferred as content without escaping the selected dataset.
3. A remote completion must remain unregistered when artifact hash validation fails.
4. Stop during upload and stop during GPU training must reach distinct, verified terminal states.
5. Server switch or app restart must not attach one server's results to another job.

## Task 1: Profile store and SSH transport

**Files:** Create `backend/remote/profiles.py`, `backend/remote/ssh_transport.py`, `backend/api/routes_compute.py`, `backend/tests/test_remote_profiles.py`; modify `backend/main.py`.

**Interfaces:** `ComputeProfile(id, name, ssh_target, ssh_port, remote_root, runtime_kind, runtime_value, gpu_selector)`; `ProfileStore.list/save/delete/get_selected/set_selected`; `SSHTransport.probe(profile)`, `exec(profile, argv)`, `upload(profile, local, remote_relative)`, `download(profile, remote_relative, local)`.

- [ ] Write API/validation tests for saved profiles, strict SSH host checking, path rejection, and no credential fields; run them red.
- [ ] Implement profile storage with atomic JSON writes under app user data and subprocess argument vectors.
- [ ] Add `/api/compute/profiles`, `/api/compute/selection`, and `/api/compute/profiles/{id}/probe`; run focused tests green.
- [ ] Review error codes and verify a read-only SSH probe on the remote QA server without starting a job.

## Task 2: Portable snapshot and worker protocol

**Files:** Create `backend/remote/snapshot.py`, `backend/remote/worker.py`, `backend/tests/test_remote_snapshot.py`, `backend/tests/test_remote_worker.py`.

**Interfaces:** `build_snapshot(source: Path, destination: Path, cancel: Event) -> SnapshotManifest`; worker CLI `train --spec <path>`; atomic `status.json` and `artifacts.json`; cancel sentinel `<run>/cancel`.

- [ ] Write red tests for LabelMe-prepared content, structured layouts, symlink dereference, relative-path manifest SHA-256, cancellation, and archive traversal rejection.
- [ ] Implement snapshot/archive creation and worker status writer with protocol version.
- [ ] Implement `train` with `UnifiedAutoMLTrainer` callback, cancellation watcher, and checkpoint manifest.
- [ ] Run focused worker tests green with a stub trainer; package code bundle without caches/tests.

## Task 3: Remote training coordination and provenance

**Files:** Create `backend/remote/coordinator.py`, `backend/tests/test_remote_training.py`; modify `backend/api/routes_training.py`, `backend/engine/checkpoint_paths.py` only if required.

**Interfaces:** Optional `compute_profile_id` on `TrainingStartRequest`; existing start/status/stop and WS events retained; local journal stores job/profile/run/source/snapshot; `RemoteCoordinator.start/status/cancel/recover`.

- [ ] Write red tests for start response, preparation/upload/progress, cancel at both boundaries, disconnect/reconnect, restart recovery, and target binding.
- [ ] Implement coordination with remote run ID, journal, status polling, and no automatic local fallback.
- [ ] Download and hash-verify checkpoint/metadata, then atomically write a local completed receipt with the original source path/fingerprint and local dataset path.
- [ ] Run focused tests and existing cancellation/provenance tests green.

## Task 4: Server selection in the desktop UI

**Files:** Create `src/renderer/stores/useComputeStore.ts`, `src/renderer/components/compute/ComputeServerPanel.tsx`, `scripts/verify-remote-compute-ui.js`; modify `src/renderer/services/api.ts`, `src/renderer/components/wizard/WizardHeader.tsx`, `src/renderer/components/training/TrainingController.tsx`, `src/renderer/stores/useTrainingStore.ts`.

**Interfaces:** local/server selector, profile create/edit/delete/test, preflight readiness, job-bound location and states, `compute_profile_id` passed only for newly started jobs.

- [ ] Write a red UI contract test for selection, preflight, start, reconnect, and no silent fallback.
- [ ] Implement the profile panel and persistent selected target; show exact server/GPU and transfer progress.
- [ ] Update Stage 3 cancellation/status copy for remote states and keep old local flow intact.
- [ ] Run frontend tests, typecheck, and build.

## Task 5: Remote evaluation and inspection operations

**Files:** Extend `backend/remote/worker.py`, `backend/remote/coordinator.py`, `backend/api/routes_evaluation.py`, `backend/api/routes_flowchart.py`; create `backend/tests/test_remote_evaluation_flowchart.py`.

**Interfaces:** `evaluate(job_id, snapshot_id) -> local results`; `flowchart_run(job_ids, image, pipeline) -> local JSON/preview` with server-bound model references.

- [ ] Write red tests for source mapping, an actual selected image, two-model ownership, remote disconnect, and local preview paths.
- [ ] Implement worker commands and local remote dispatch for jobs belonging to one server.
- [ ] Download/verify preview and result assets, rewrite paths to local files, and retain provenance checks.
- [ ] Run focused Stage 4/5 tests and local flowchart regressions.

## Task 6: Remote benchmark/export and packaging

**Files:** Extend `backend/remote/worker.py`, `backend/api/routes_evaluation.py`, `backend/api/routes_export.py`, `backend/api/routes_report.py` where needed; create `backend/tests/test_remote_export.py`; add `build/remote/Dockerfile`, `docs/REMOTE_COMPUTE.md`.

**Interfaces:** `benchmark(job_id, settings) -> server-labeled metrics`; `export(job_id, settings) -> hash-checked local package`.

- [ ] Write red tests for server-bound benchmark, remote export return path, and failed/missing artifact.
- [ ] Implement worker operations and local result download.
- [ ] Document Python and Docker runtimes, SSH setup, data transfer, resource selection, cleanup, and remote QA server profile fields.
- [ ] Run backend suite, frontend suite, typecheck/build, and packaged-app checks.

## Task 7: Remote QA server connection proof

- [ ] Configure an isolated profile under the desktop app's user data and provision its worker runtime without altering existing services.
- [ ] Verify SSH host identity, worker protocol/runtime imports, accelerator visibility, writable remote root, free space, and a no-GPU worker probe.
- [ ] In the packaged app, verify profile selection and clear readiness/error states. Run a GPU training smoke only after explicit resource approval; otherwise report that boundary.
- [ ] Check source-data hashes, Git diff, full test output, and remote branch HEAD before reporting completion.
