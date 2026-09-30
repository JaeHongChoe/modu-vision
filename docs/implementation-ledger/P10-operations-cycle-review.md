# P10 actual automatic cycle and concurrent incumbent inference

2026-09-30; supplements root's P10 operations/fleet acceptance. No changes to `model_operations.py` were needed.

## Actual acceptance

`backend/tests/test_model_operations_real_cycle.py` ran a genuine cached pretrained DINOv3 small classification parent on CPU, with a generated six-image, two-class controlled fixture. The parent checkpoint and pretrained file are hash-bound. Policy explicitly enabled automatic retraining and disabled automatic labels, mandatory label review and automatic approval.

A fresh non-holdout image and adjacent LabelMe truth were added. The actual default `run_cycle` created a new immutable version, used parent-config fast retraining with a new optimizer, reevaluated both incumbent and candidate, compared the same two frozen test images, and persisted `awaiting_approval`. Store and API reopening retained that status. No approval or deployment result exists. Parent checkpoint and all original image/JSON bytes remained unchanged.

A separate owned loopback inspection-service process loaded a verified genuine incumbent package. After the candidate executed an actual optimizer batch, a progress barrier held the candidate thread active while the service accepted a known-image HTTP request and persisted `queued → running → completed` events with an actual model verdict. This verifies concurrent availability (F111), not throughput or latency under unrestricted load. The controlled test service was stopped afterward; no existing service/app was restarted.

## Evidence

- Actual cycle alone: **1 passed, 13.62 s**.
- Cycle plus independent-process incumbent inference: **1 passed, 1 warning, 19.06 s**; private log `p10_actual_operations_cpu_gate.log`.
- Public receipt: `P10-actual-cycle-manifest.json`, including immutable version/comparison/evaluation identities, parent/source manifest hashes, service job and package manifest hash. The private full receipt `p10_actual_operations_cpu_evidence_20260930.json` retains source paths and detailed events separately.
- Functional generated-input CPU proof only. Manufacturing model-quality approval, activation/rollback policy, natural OCR/orientation quality, CUDA/MPS/remote throughput and multi-device production service acceptance require separate evidence. Root owns those P10 boundaries.
