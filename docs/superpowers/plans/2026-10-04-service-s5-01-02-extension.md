# S5-01 / S5-02 SCM and durable input extension

This slice implements the existing October 2 service plan in the isolated
checkout based on `422a2d2`. It adds service behavior and CPU/simulator evidence.
Windows Session0, reboot, GPU/camera/network hardware and process quality
acceptance remain pending. No SCM registration, privilege/account provisioning,
machine resets or GPU training was performed during implementation.

## Contract and production paths

- Source SCM executable: `python -m backend.engine.windows_inspection_service
  --project-dir <protected-project> --service-name <owned-name>` connects with
  `StartServiceCtrlDispatcherW`. Its ServiceMain registers HandlerEx, reports
  pending/running/stopped states, gates readiness and supervises one owned child.
  STOP/SHUTDOWN signal an event; the handler returns promptly. Cleanup requests
  authenticated graceful HTTP shutdown, waits for exit, and terminates the owned
  Windows Job Object only on timeout. An inherited event prevents child package
  and GPU startup until job assignment completes. A retained process handle is
  used; there is no PID-only termination.
- Frozen dispatch is `--windows-inspection-service`; the integration owner wires
  this before normal Studio startup in `backend/main.py`. Python availability and
  packaging remain explicit for the source entry.
- `/api/runtime-services/scm/preflight` and `/scm/prepare` stage/check a separate
  SCM configuration. `/scm/activate` is the explicit elevated registration action.
  Existing Windows logon startup is still identified as `scheduled_task`, and an
  existing user registration must be removed explicitly before SCM activation.
- Preflight checks actual administrator context and protected ProgramData
  project/state ACLs, dedicated virtual service account or pre-provisioned gMSA,
  project access, and protected interpreter/runtime code paths. User-supplied
  booleans are never native evidence. Network access with the service identity,
  service-logon rights, reboot without login, Session0 GPU warmup/camera access and
  physical devices require target evidence. No passwords or new accounts are
  accepted or created. Personal Studio remains non-admin.
- `/v1/readiness` reports worker liveness, package verification and GPU warmup
  separately from Session0/hardware acceptance. CUDA readiness requires a
  configured successful warmup image under the allowed input root. `/health`
  keeps its prior runtime identity response for compatibility.
- HTTP file/upload/device, folder and camera admission share the SQLite inbox
  capacity transaction. `max_outstanding` defaults to 100 and counts queued,
  running and pending delivery rows. Admission backpressure returns HTTP 429 with
  Retry-After; duplicate keys return the original row even at capacity, while
  conflicting bytes/path/image/product/lot return 409. Unaccepted upload blobs
  are removed. A settled corrupt folder file is stored once as REVIEW evidence.
  The folder watcher keeps at most 256 settlement entries and a streaming native
  directory cursor, with a 256-entry work budget per scan. Directory handles scale
  with traversal depth. Completed duplicates cannot grow the observation map;
  continuously changing front files are deferred after a bounded hold so settled
  tail files and later arrivals still receive admission opportunities.
- `runtime_binding_json` and SHA-256 freeze package manifest, model hashes,
  device, pipeline, runtime build and optional recipe ID/revision/product/lot at
  admission. The worker re-verifies A's bytes and executes A after B activation or
  restart. Result and outbound payload runtime identity retain A. Retry does not
  select B. The original image hash must still match before execution.
- Queue admission deadlines, inference timeouts, bounded interrupted/ordinary
  retries and bounded result deliveries preserve REVIEW on failure. A late ACK
  remains recorded as sent while the operational verdict remains REVIEW.
  `/v1/jobs/{id}/replay` requires an operator/reason and creates an auditable new
  ID pinned to the original release, with parent/root/count and a bounded lineage.
  `/v1/results/export?format=json|csv` exports identity and provenance; CSV formula
  cells are quoted as literals.
- Schema migration adds columns without deleting prior jobs/events/results.
  Historical completed evidence stays readable. Pending legacy inputs whose
  original recipe cannot be proved become REVIEW with `LEGACY_RECIPE_UNKNOWN`;
  the current release is never attributed to old input. An operator must submit
  a new explicitly bound input for these records.

## Plan and evidence

1. Inspect production admission/runtime/native control paths and Microsoft SCM
   references; design the file/API contract with the integration owner.
2. Write failing S5-02 admission/capacity/recipe/deadline/legacy/export tests.
   Receipt: `s5-02-red.txt` (9 failures).
3. Implement the shared inbox and immutable runtime binding; verify initial
   contract green (`s5-02-green-initial.txt`, 9 passes).
4. Write failing SCM lifecycle/preflight tests (`s5-01-red.txt`, 6 failures,
   existing shutdown route pass), implement dispatcher/manager integration and
   retain user startup compatibility.
5. Add actual A-to-B package apply/reopen, old schema, upload cleanup, manager
   prepare and readiness regressions (`s5-added-contracts.txt`, 23 passes).
6. Reproduce replay-lineage bypass and late ACK timeout behavior
   (`inbox-hardening-red.txt`, 2 failures); close both boundaries.
7. Independent review reproduced unbounded folder observation state with 300
   completed duplicates. Add bounded streaming traversal and unsettled-front
   fairness (`folder-bound-red.txt`, 2 failures; `folder-bound-green.txt`, 3 passes).
8. Focused and affected compatibility gates are saved under
   the external `extension-batch-20261004/service-inbox` evidence directory.
   Initial broad runs hit filesystem disk I/O errors while the disk was nearly
   full; use explicit private basetemp for final gates. Native compatibility
   rerun: `native-compat-green.txt`, 17 passes. Affected compatibility gate:
   `final-affected-green.txt`, 113 passes and 1 skip. The final focused gate after
   the folder correction (`contract-review-final-green.txt`) has 33 passes:
   27 SCM/inbox contracts, 2 independent role/actor API checks, 2 independent
   native adapter trust/snapshot checks and 2 OCR flow evidence checks. These
   receipts overlap and are not an aggregate unique test count. Root owns combined
   CPU verification and Git integration; this slice does not stage or commit.
   Independent adapter review also reproduced download-capable local YAML before
   native construction (`adapter-yaml-red.txt`); the adapter owner restricted
   selection to trusted local `.pt` weights. `adapter-review-final-green.txt`
   confirms all 3 independent native trust/snapshot/YAML checks pass.

## Reviewer checklist

- Real SCM callback ABI, own-process status and one final STOPPED report.
- Readiness timeout/child failure is a nonzero SCM exit; handler does not block.
- Native configuration/account/command ownership before stop or delete.
- No registration/credential/elevation side effects on preparation or import.
- Atomic inbox capacity and duplicate-before-capacity behavior for every input.
- Bounded folder observation/traversal work and fair settled-tail admission.
- Exact A package/model/device/recipe binding after B activation/restart/retry.
- Dead-letter/replay root bound; retain original errors/results/ACK evidence.
- Additive migration; no invented legacy recipe identity.
- Native Windows target validation and Session0/hardware acceptance still pending.

Microsoft references consulted: [dispatcher](https://learn.microsoft.com/en-us/windows/win32/api/winsvc/nf-winsvc-startservicectrldispatcherw),
[ServiceMain](https://learn.microsoft.com/en-us/windows/win32/services/writing-a-servicemain-function),
[HandlerEx registration](https://learn.microsoft.com/en-us/windows/win32/api/winsvc/nf-winsvc-registerservicectrlhandlerexw),
[status reporting](https://learn.microsoft.com/en-us/windows/win32/api/winsvc/nf-winsvc-setservicestatus),
[SCM access rights](https://learn.microsoft.com/en-us/windows/win32/services/service-security-and-access-rights),
[Job Objects](https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects).
