# P10 — data, model and field operations

F108–F111 are integrated through project-owned policy/journal, native controls, separate owned workers and authenticated field agents.

## Implemented workflow

1. Bind a completed parent checkpoint, active label set, source hashes, policy revision and an immutable independent test cohort.
2. Discover new or changed non-test pixels/labels. Reject byte-identical copies of holdout inputs. Enroll new train/val images only in owned split storage.
3. Adopt only high-confidence semantic suggestions into editable overlays, preserving automatic backup/version, original image hash and reviewer identity. Adopted labels remain `needs_review`.
4. Use the parent's recorded configuration for budgeted real retraining. Persist cancellation and model lineage. Evaluate incumbent and candidate against the fixed cohort; keep candidates awaiting explicit approval unless a reviewed automatic approval policy was authorized.
5. Automatic activation still passes existing model approval gates, required sample counts and explicit metric bounds. Specialist families require their own reviewed test metrics. GAN adoption always requires human image review.
6. Automatic deployment binds an exact saved flow version/hash, verifies every model's current approval, replaces only configured parent nodes, verifies application/package inference parity, saves a new flow version and obtains a separate service application acknowledgement. Pre-application failure/cancellation restores the prior active graph.
7. Preserve truthful durable approval/deployment receipts when cancellation arrives after an applied side effect.
8. Keep the project watcher and inspection service independent of the desktop renderer, with cooperative cancellation, persistent journal and owned-process recovery.

## Field model management

- General HTTPS or loopback targets, private tokens, checksum-bound package upload and authenticated runtime readback.
- Full-flow release verification, approved service staging, persistent central/device deployment histories and restoration of a previous version.
- CPU, CUDA and available OpenVINO devices are explicit; an unavailable target does not silently use another device.
- Agent archive extraction rejects traversal, links, duplicate/special entries and unbounded expansion.
- Lifecycle locks and exact PID/time/command identity preserve unrelated processes.

## Actual functional evidence

- `test_model_operations.py`: actual classification inference adopts one reviewable label; untouched source bytes; backup/journal reopen; changed holdout/duplicates blocked.
- `test_model_operations_real_cycle.py`: genuine cached DINOv3 parent, new image/JSON, actual optimizer fit, immutable input version, both reevaluations, full fixed cohort comparison and persistent `awaiting_approval`.
- Same actual cycle keeps a separate HTTP inference process alive: queued/running/completed real prediction while the candidate has executed an optimizer batch and remains actively training. This establishes independent functional execution, not production throughput.
- `test_operations_security_review.py`: concurrent watcher launch, unrelated live owner recovery and cancellation at approval/deployment acknowledgement.
- `test_fleet.py`: two real network packages, authenticated agent boundary, actual separate inference service process, persisted inference, central reopen, second apply and prior-version rollback.

## Acceptance boundaries

Controlled small CPU trials are functional evidence. Physical PLC/MES/camera hardware, distributed CUDA/MIG resources, Edge/NPU devices and manufacturing model quality need their own acceptance inputs. Native UI observations and final regression are recorded separately. No production policy was enabled by these tests.
