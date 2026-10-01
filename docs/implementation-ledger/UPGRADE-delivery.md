# Delivery and operator workflow implementation ledger

Date: 2026-10-01. Scope: U005, U021–U025, U031, U033.

## Requirement evidence

| ID | Implemented user action | Persistence and validation evidence | Remaining acceptance boundary |
| --- | --- | --- | --- |
| U005 | Open the saved package library, reopen a package, inspect its flow/version/model/hash, then enter optimization or deployment with that package selected. The same picker is available for managed service and fleet deployment. | Package receipts and account-specific selection persist in project delivery storage. Checksums and canonical project/source scope are checked on discovery and selection. Changed files and another source are rejected. An actual saved CPU package executes an original image through the API and reopens its execution receipt. | Native application click-through is a separate combined QA gate. Legacy packages without a matching saved source/flow receipt require reconnection before selection. |
| U021 | Choose or edit a generic SSH compute profile, probe its runtime, select a source image, run input preflight, and select the verified server as the target for new jobs. | Actual source bytes are hashed locally and remotely, decoded remotely, and converted into a finite CPU tensor. Large images are transferred into a new request-owned scratch directory; success and transfer failure remove that directory while preserving unrelated data. No training job is created. | Preflight is an input/runtime check, not a training or GPU benchmark. Hardware-specific validation requires the target server. |
| U022 | View configured capability, recorded actual execution, and active approved runtime as separate device states; run one source image to record actual execution. | Device receipts include source scope, manifest hash, input hash, verdict, and time. A changed package invalidates its live verification. Approval is shown only when evidence and the actual managed service manifest/device match an approved active release. | Capability alone does not establish execution or operational acceptance. |
| U023 | Enter PLC/MES connection fields, retain advanced JSON mappings and ACK options, save configuration, and exercise success/rejection/timeout against owned local receivers. | Basic-field round trips preserve advanced mappings and intentionally absent sequence registers. Numeric fields reject invalid values. Real HTTP and Modbus adapters exchange with loopback receivers; all six success/reject/timeout cases pass. Existing MES credential retention/clear contracts remain verified. | Local exercises use the default protocol contract, not customer mappings or field addresses. Physical equipment and site mappings need target validation. |
| U024 | Enter the dedicated operator workspace, inspect recipe/runtime/input health, configure manual/folder/camera input, explicitly start/stop the service, submit an image, inspect saved results, record REVIEW decisions, and retry failed ACK delivery. | Results come from the real managed inspection database. Reviewer records persist separately and never overwrite model verdicts. Input configuration is source-scoped, survives reopen, and is passed to both service start and native install commands. Runtime manifest must match the approved active release before submission. Viewer controls are disabled; API role gates remain enforced. | Camera/RTSP and physical PLC/MES connectivity require actual equipment. Saving input settings requires the next explicit service start to apply. |
| U025 | Check actual installation dependencies and project format compatibility, save a project backup before changing versions, choose diagnostic sections, and download redacted JSON. | Unsupported schema is reported without migration. Section whitelist excludes unselected information and source images. Recursive redaction removes authentication values, URL credentials/query parameters and personal file paths. Selection and viewer permission tests pass. | Automatic update provider and signed distribution certification are not configured; the screen states this explicitly. Backup and compatibility checks do not certify an installer. |
| U031 | Read Python/C++/C# bridge prerequisites and distinguish missing compiler, headers, .NET, and runtime dependencies before SDK use. | Required packages, Python version, compiler/CPython headers and .NET are checked locally. C++/C# are presented as embedded-Python/C ABI bridges. Existing packaged Python and compiled C++ full-graph tests pass. | C# compilation/execution is skipped in this environment because a .NET SDK is absent. HTTP clients remain a separate interface. |
| U033 | Read a hardware verification matrix for CPU, available CUDA/MPS/OpenVINO devices, and unverified MIG/NPU/Edge targets. | Each row displays configured/live-verified/approved separately, with actual package/input hash evidence. No local CPU result is used as evidence for a different physical device. | MIG, NPU, Edge boards and unavailable GPUs require execution on those targets before acceptance. |

## Focused verification

Use an isolated QA interpreter with the repository-declared FastAPI/Starlette versions; no base-environment package changes were made.

```text
$QA_PYTHON -m pytest -q backend/tests/test_product_delivery.py
22 passed in 12.86s

$QA_PYTHON -m pytest -q backend/tests/test_managed_service.py backend/tests/test_fleet.py backend/tests/test_runtime_optimization_api.py backend/tests/test_specialized_flow_export_route.py backend/tests/test_runtime_deadline_sdk.py backend/tests/test_field_adapters.py
27 passed, 1 skipped in 48.80s

node --test src/renderer/components/runtime/productDelivery.test.cjs
6 passed, 0 failed

npm run typecheck
exit 0
```

The skipped test is `test_csharp_pinvoke_calls_the_same_native_executor`; its gate requires a .NET 8 SDK. Focused regression includes actual managed process apply/readback/restart/rollback, field agent release inference/reopen/rollback, owned optimization cancellation and persisted receipt, specialized saved-flow export, Python/C++ full-graph SDK execution, and protocol ACK behavior.

## Observed failing cases before fixes

- Persistent delivery engine and API were absent; initial backend/API tests failed before implementation.
- Form handoff helpers were absent; initial frontend contract tests failed before implementation.
- Account selections overwrote each other; independent saved selections now pass.
- Changing a manifest retained live verification; manifest integrity now invalidates the receipt.
- Large-source preflight initially rejected the input; original-byte transfer and hash verification now pass.
- Selected diagnostic output initially contained every section; whitelist filtering now passes.
- Operator permission response was absent and viewer start controls were enabled; explicit permission response and disabled UI controls now pass.
- A deliberately absent sequence register returned the default register; null round trip now passes.
- Transfer failure left partial preflight scratch files; cleanup now runs for failure while preserving another run directory.

## Integration contracts

- Router: `backend.api.routes_product_delivery.router`, prefix `/api/product-delivery`; registered by the application integration owner.
- Workspace exports: `ProductDeliveryWorkspace` and `OperatorWorkspace`; mounted globally by the integration owner. The operator workspace replaces the main workflow while open, with the application's return action.
- Existing `FlowPackagePanel`, `RuntimeServicePanel`, `FleetPanel`, and `ComputeServerPanel` mount the new library, pickers, forms, and wizard. Existing export/apply/rollback/optimization routes remain intact.
- `optimization_records(project)` returns source-scoped persisted optimization task rows and honest cancellation flags for the unified task center. Export receipt creation uses `record_package` without modifying package integrity files.
- Read routes and account-private package selection allow project readers. Package execution and protocol exercises require owner/reviewer/trainer; compute preflight requires administrator. Operator inspection allows owner/reviewer/trainer/labeler; input setup, review and ACK retry require owner/reviewer.
- Context guards reset package/deployment/operator/fleet selections when project, source, task, labelset or transport revision changes and suppress stale responses.

## Safety and delivery state

No costly GPU job, model download, unknown job cancellation, field-equipment write, commit, push, or native application acceptance is claimed by this ledger. Loopback receivers and inference subprocesses are test-owned. Original customer data is read only. Combined regression, native screen acceptance and Git publication are tracked by the integration owner.

## Additional real-input API acceptance

A separate test-owned project used a byte-preserved, genuinely trained one-epoch CPU OCR checkpoint and three unchanged real source images. Its preparation truth is a functional fixture; no model-quality or operational approval was created. Package construction retained the checkpoint metadata and recorded explicit test import provenance, with a real saved flow version and canonical original-source binding.

- **31 checks passed, 0 failed**, process exit 0; 17.74 seconds.
- **3 actual original-image CPU executions** through the saved package API; package selection and three execution receipts survived a new application instance. Configured, actually executed, and approved states remained separate; approval stayed false.
- **6 local protocol receiver cases**: HTTP and Modbus each acknowledged success and rejected denial/timeout. These are protocol-contract results, not physical equipment verification.
- Operator submission with unavailable runtime returned 409. Folder input and separate reviewer records survived a new application instance. The test-owned inspection store was populated with the actual package API results; an approved managed service was not started or fabricated.
- Selected installation/error diagnostics were persisted, redacted, and reopened; source images and unselected package/hardware sections were absent. An explicitly labelled test error checked authentication/path redaction.
- A real project backup included original source files and the saved library, diagnostics, inspection database and separate reviews. **171 original files** retained their hashes, and a separate post-run inventory-name comparison matched exactly. Checkpoint origin files were unchanged.

Private acceptance artifact SHA-256: `9bc86a00e209a40c1c2dc7673b58597437cd428cdfa0ca6dd2b688cc868b2001`. Only counts and content hashes are published here; filenames, paths, full results and provenance stay in the private artifact. This API evidence supplements, rather than replaces, native GUI and physical-hardware acceptance.

## Saved task downstream reopening

The final integration review found that export, optimization and inspection task actions reached stage 6 without consuming the exact saved record. This was reproduced before correction: package A stayed selected after opening export B, and the requested optimization record was not displayed.

- `PackageLibraryPanel` and `SavedPackagePicker` consume the scoped export task ID, call the real selection route, and display the selected package. A missing or invalid target raises an error rather than choosing the newest package.
- `FlowPackagePanel` verifies the optimization journal ID, canonical source receipt and input package, reselects that package, and opens `RuntimeOptimizationPanel` with the exact job ID. The job ID and input package are visible; dependency readiness does not replace the saved record.
- `BatchInspectionPanel` consumes the selected inspection ID, opens that source-matched run and shows its existing run identity/results. An absent ID or mismatched response is rejected rather than replaced with the newest inspection.
- Re-selecting the same task receives a new selection identity so an already-open workspace can restore it after another manual selection.

`node --test src/renderer/components/inference/deliveryTaskHandoff.test.cjs`: **7 passed, 0 failed**. The renderer behavior tests exercise the real panels/hooks with the external API boundary supplied by test fixtures; they do not claim a native application click or an additional model execution. They cover package A/B reopening, the deployment picker, exact optimization restoration, source mismatch and stale-request rejection, the actual flow package panel, and the actual batch history panel. Native click-through remains a separate acceptance gate.

## Flow editor viewport regression

The integration owner observed an expanded bottom workspace consuming the graph height and compressing toolbar/resource rows in the native application. The editor now allows vertical overflow, reserves a nonshrinking 240-pixel minimum graph/results row, and preserves the natural heights of toolbar, resources, status, input and error rows. The existing bottom workspace retains its own bounded scroll area. This correction changes layout classes only and leaves graph/model behavior intact.

`flowStudioLayout.test.cjs` reproduced both missing scroll/minimum-height protection and shrinking fixed regions before the correction, then passed **2 tests**. Combined flow layout, graph history, workspace and viewport-contract regression passed **21 tests**. These tests render the editor structure and verify its flex/overflow contracts; actual screen geometry and the expanded-panel native screenshot are checked separately by the integration owner.
