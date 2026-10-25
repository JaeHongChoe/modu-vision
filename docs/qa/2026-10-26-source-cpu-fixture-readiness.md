# SOURCE CPU fixture completion readiness

PR12 base: `3944fa545c352404c149175b4112a87c2fc3e449`.

Hosted fast run `40211797091` refused CPU completion with `LeaseTransitionBusy` before its drain/replay action. Its exact lock holder and controller entry were not recorded. The local evidence establishes a sufficient interleaving; it does not identify that hosted ordering or establish a production defect.

The actual SOURCE completion post-send validator can retain the pinned transition flock while either original controller verification or later receipt publication enters. Two fresh pre-repair cases failed the original CPU-success/replay path with that exact genuine busy refusal. Separate permanent busy controls continue to require the original `BlockingIOError` cause, durable `recovery_required`, no receipt, no returned SOURCE settlement and no drain trigger.

The repair changes only the managed test fixture's ordering. Its existing service-thread profile is composed with a one-shot observer of the original post-send validator's **normal return**. Selection checks original validator/service code and structural callsite, thread, retained cache, queue, producer state, request, root, nonce and unique fixture token. An exception-unwind profile return cannot publish readiness. The controller waits before its original verification call, then runs genuine verification, publication and returned SOURCE settlement unchanged.

The parent captures the original 50-second observation budget immediately before startup. A pending observation carries the genuine action deadline; the wait takes the earlier bound and never renews it. A ready file only schedules the fixture: it cannot replace any owner, intent, proof, receipt, source, lease or settlement check. Already-present expired or foreign observations fail closed. The original 3-second/4-second drain boundaries, ordinary stop, replay/nonce refusal, registry, tombstone and source/control-byte assertions remain in the complete original test bodies.

Final runtime controls:

| Controller entry | Actual post-send lock held | Released through genuine readiness |
| --- | --- | --- |
| `verify_completion` | Original busy/refusal assertions passed | Complete original identical-replay body passed |
| `_publish_cpu_execution` | Original busy/refusal assertions passed | Complete original identical-replay body passed |

Both released controls recorded validator normal return before original controller entry and genuine returned-settlement completion. Both busy controls recorded the same pinned lock device/inode and the unchanged busy cause. Readiness cannot convert a busy failure into success.

The focused helper negatives cover foreign code/thread/cache/queue/request/root/nonce/token, pre-send callsite, unsent/wrong-phase state, expired/nonfinite deadlines, true normal return versus exception unwind, stale/foreign/error markers, previous-profile restoration and owned-gate cleanup. Failure diagnostics contain bounded function/line/type/phase observations and marker existence/SHA256 values, never exception text, raw bootstrap/private proof, environment or credentials.

Validation: **4/4 final runtime controls**, **29/29 final helper/cleanup controls**, and **82/82 affected checks** (51 existing quiescence cases, 28 then-current helper cases and 3 named CPU execution cases). The final 29-helper run additionally includes the marker-failure cleanup regression. The runtime cases were not repeated after helper-test observation and cleanup-only corrections.

Exact counts, commands and attempt boundaries are in the retained implementation report. The ignored evidence directory is `.superpowers/sdd/2026-10-26-source-cpu-fixture-readiness/evidence/`; all attempts use distinct retained roots. It includes two meaningful pre-repair failures, a diagnostic timestamp assertion correction, a profile-unit event-filter correction, and a separate RED/GREEN cleanup regression proving stop still runs after marker failure. These authoring/cleanup failures are not additional product or contention evidence. Successful integration runs have the existing `python_multipart` pending-deprecation warning.

All seven pinned production CPU/lease/handshake/controller/relay and Node bridge/supervisor source hashes match the base. No production source, dependency, workflow, timeout, generic busy policy, frozen-only eligibility, request dispatch or CI selection changed. Validation used the existing environment and small controlled CPU OCR fixture with CPU thread limits of 1; no installation, external network, GPU, account or field-data action was performed.

This boundary orders the observed completion validator's holder. Another real holder can still legitimately refuse entry and would require new evidence. Independent review, final PR12 delta review and hosted validation of a published new head remain separate work owned by the parent task.

## Shared-fixture compatibility follow-up

At `a1dd6ee5`, hosted fast run `40216240400` exposed two legacy consumers that call `managed_stack` and then the original `fixtures.start_cpu_stack` directly. The generic stack had published a partial readiness configuration (`token`/`root`); only the opt-in startup wrapper added its deadline. The legacy shared receipt helper therefore raised `KeyError('deadline')`, and the SOURCE relay case also reported a secondary missing ordinary-stop artifact. Both exact failures were reproduced locally before this correction. This was a fixture activation regression introduced by the first repair.

`managed_stack` now publishes no readiness configuration. Its generated backend returns the exact retained original profile when unconfigured, covering both CPU and preflight shared consumers. Only `_start_ready_cpu_stack` explicitly creates a complete token/root/one 50-second deadline before startup, using exclusive publication. The controlled schedules are supplied through that same startup helper. The ordinary enrollment case uses its original direct startup because its plain backend has no readiness observer. Configured state is validated strictly: partial, malformed, expired, nonfinite or foreign-root observations cannot fall back to a fresh/default deadline, and existing configured state cannot be overwritten or renewed.

Read-only inventory covered all four external `managed_stack` callers: the two CPU cases in `test_application_owned_source_cpu_relay_actual.py` and `test_application_cpu_writer_lifetime.py`, plus the direct-Popen preflight cases in `test_worker_preflight_writer_lifetime.py` and `test_application_preflight_child_relay_actual.py`. Other imports use cleanup or inert assertion helpers. The preflight stage bodies were not run in this wave; retained-profile identity controls check their shared unconfigured startup boundary.

Compatibility validation: **46/46 helper controls** (29 prior plus 17 new), then **11/11 targeted runtime cases**: the two exact legacy cases, four real held/released readiness controls, and five original quiescence cases. The same dependency warning remains. All 181 assertion nodes in the 41 prior test functions are unchanged. Production/workflow sources and the four external consumer files remain byte-identical. Exact RED/GREEN commands, caller inventory, hashes and retained failures are appended to the implementation report under `compat-wave1/`; no CI, branch, publication or dependency action was performed by this correction.
