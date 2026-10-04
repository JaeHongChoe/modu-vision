# Remaining implementation and execution evidence

This continuation advances the existing 82-item program. It keeps implementation, actual execution, representative model quality, Windows qualification and parent acceptance separate. No parent acceptance or field release is granted by this record.

## Integrated production changes

Initial E06/E08 base: `ce5172751c2921d5805092339f0a5f779918724d`. The later combined snapshot starts from `4824ba4f2fae47cfc1390a9b20b745a054df73a1` and retains the independently reviewed E01/E02, Task 7/8/9/10 and UTF-8 corrections described below. These snapshot checks grant no parent acceptance or publication status.

### E06 sampling through capture intake

The existing intake sampler is now used by the production service-capture registration path. A project-scoped opt-in policy has immutable revisions, window/seed/quota accounting and replay receipts. A skipped event stages no image. Selected captures retain UNKNOWN truth and require the existing human review/adoption path. Held-byte accounting includes recoverable trash, and retention failure is visible. Local desktop token authority and shared-account owner authority remain distinct.

Actual macOS source-built Electron checks in an owned synthetic project saved policy revision 1, registered one synthetic service fixture through the production API and reopened it. The app showed one held capture (110 bytes), one selected event and unknown truth. This is a UI/API integration check, not a physical camera capture or a model inference claim.

### E08 durable import and backup

The existing JobStore now retains operation snapshots, expected revision, attempts, progress units, staged output and verified result references. Interrupted imports and backups resume through the same job identity. Source drift, revision drift, cancellation before sealing and invalid/expired download results are guarded. Backup source comparison uses committed logical SQLite snapshots, including WAL commits. A reviewed `project_restore` operation now uses the same durable ledger, actor/project authority, backup parent lineage and immutable archive binding. It requires an explicit fresh unlinked directory, owned staging/reservation/inode proof, full verified inventory and a fenced no-replace publication. Same-ID interruption recovery and cancellation refuse foreign/replaced paths. The result is never activated automatically; shared-server restore is refused.

Actual macOS Electron started an import, closed/reopened its panel and retained the same job ID with two verified images and a result hash. Task Center started a project backup, showed its 19-file verified result and expiry, and saved the archive through the native dialog. The downloaded archive SHA256 matched the durable result reference. The owned app was then restarted with the same stores. Task Center retained both exact job IDs, attempt 1, result count/hash and backup expiry; no extra job or attempt was created. These private fixtures contain no user source data.



A later actual native backup contained 882 files. Task Center submitted its restore to a fresh owned directory and displayed a verified 883-file result (the restored project adds its rebind record), attempt 1, expiry and unchanged backup parent identity. Independent full inventory verified 446,844,571 bytes and output SHA-256 `c4eeb36dabaccd867a80014945342067fb96012310ce44bc19b878f31c38c96e`; the restored completed-model checkpoint matched its original hash. Original project/active flow/version/model files remained byte-identical. After owned application restart the durable ledger retained the same completed restore ID/spec/output and one attempt. The native Task Center list then remained empty, so UI reopen acceptance is pending correction of its refresh handling. Private native/inventory readback SHA-256: `051376b7f70d37f9219628a7f3682a006be153b57d38c64da815013dfd772bb5`. No real-user dataset or operational activation occurred.

### E01 capture groups and autonomous deadline outcomes

The production service carries explicit part/trigger/view/time identity and frozen recipe/policy revisions through file, upload and device-event admission. It reuses the existing capture-group provider. Duplicate identical captures reuse their job; missing identity, changed pixels/recipe, unknown views and closed groups refuse ungrouped execution. OPEN groups retain REVIEW and suppress frame-only delivery; terminal joins use the canonical whole-part result.

The existing idle worker now reconciles persisted EXPIRED/INCOMPLETE closures into the existing jobs/outbox. Stable event identity, bounded rotating sweeps and transactional enqueue prevent duplicate local outcomes across restart. Rejected capture attempts are excluded from admitted-frame provenance and the original runtime anchor. Deadline HTTP retries keep the same idempotency key/body. Destination effects remain at-least-once unless the destination honors that key. These delivery tests use controlled HTTP transport; real MES/PLC/webhook acceptance remains pending. A blocking inference call can delay the idle sweep.

### E02 fixture references through flow and package execution

The fixed ROI path now resolves copied, project-owned fixture references with immutable pixel/metadata/provider hashes and revision checks. The existing pose provider rectifies supported reference-space ROIs and retains source transforms, polygons and pose receipts. Missing, stale, mutated, ambiguous or unsupported references yield REVIEW before downstream inspection; out-of-frame configured search regions also yield explicit REVIEW.

Draft/run/debug/export/package paths share the resolver scope. Export copies and verifies the exact reference artifacts, and package runtime uses those immutable copies. Renderer controls save/select references and edit ROI on the reference preview. Synthetic CPU production tests cover classifier and segmentation geometry plus package parity; real camera robustness, Windows and representative model quality are separate checks.

### S4-11 selected remote package target

Remote cohort export now accepts an explicit supported compute profile and reuses the existing operation coordinator, worker, leases, reconnect journal and artifact validation. Package/input/code/profile hashes bind the uploaded operation. Reference inference and an independent package process run on the same selected target; retained device/UUID/PID evidence and completed-image counts must agree. Exclusive CUDA allocator budgets are divided between those two processes without changing global profile settings.

Unsupported modes/profiles fail explicitly before transfer. Controlled transport tests include actual CPU package execution; CUDA identity checks use controlled fixtures. The rebuilt native app submitted a two-image CUDA package comparison on the selected GPU. It failed before either image completed because the independent package lacked `backend.remote.file_replace`, which capture-policy validation imports. The failed package and receipt remain preserved; the remote operation confirmed worker exit and the owned container was absent on fresh readback. The portable helper dependency closure is now implemented and independently reviewed. Eight package-root/isolation and refusal checks plus 150 affected checks passed. The next actual GPU comparison completed both images on the same physical GPU with independent child processes; it retained mismatch status because fixture evidence used tuples in the app and JSON arrays in the isolated package. That failed package is preserved. A causal actual fixture-flow JSON-boundary regression reproduced the mismatch; emitting the three coordinate fields as arrays corrected it without changing comparator tolerances, geometry or reference hashes. The affected fixture/package gate passed 81 checks. Actual GPU execution of this final producer correction remains pending. The existing worker monitoring bound and per-image package subprocess bound remain; a new hard total reference-engine deadline was not introduced.

### S1-08 guarded project migration and recovery

Supported legacy project-manifest normalization now has full project/source inventory, verified backup, source bindings and a durable prepared/cutover/recovery journal. Cooperative API/lifecycle admission blocks writers during cutover; running/unknown jobs, open attempts, uncertain leases and external ownership prevent activation. Logical SQLite snapshots include committed WAL content without opening the original database for backup.

Recovery verifies full membership/count/bytes/hashes and restores only this transaction's manifest output before later writes. Original labels, models, results, approvals and external source artifacts are preserved. Global-store inventory is read-only; global activation, copied worker adoption, real installed migration and Windows/power-loss qualification remain unsupported or unexecuted. This manifest recovery remains separate from durable backup/new-directory restore.

On the source-bound rebuilt macOS app, an owned four-file synthetic project was inspected, backed up, converted to schema 1 and restored through the actual UI. All four restored files and their backup copies match the original SHA-256 and size; the restored manifest again has no schema field. The current GPU QA project was unchanged. The private readback receipt SHA-256 is `62f495a31e9d7c56bc78188d6eb2de787beabc4f51f3fe3a7bb4d9c2913b4d80`. This is a legacy-manifest round trip, not migration of an installed global store.

### Explicit recipe adoption and stale-save protection

An explicit recipe/template replacement observes the active version before model verification, then rechecks project/namespace, graph, cancellation and idle authority after both awaits. Graph and observed base are adopted atomically, clearing the earlier basis's undo/redo/drag history. Ordinary edits retain their existing base and undo behavior, and edits after adoption can undo/redo within the new basis.

A real Studio/store regression found that the initial correction allowed undo to restore a stale draft with the refreshed base. That escape is now closed. Failed verification leaves graph/base unchanged; a concurrent activation retains the earlier observed base so the existing backend compare-and-swap still refuses saving over it. Automated Studio/dialog/store proof has independent review.

The rebuilt native app explicitly adopted a fixed-ROI recipe and completed model, cleared the prior history basis, saved a reasoned semantic change and reopened the new active version `7537f063dec145eeb57b77f83053c6fb`. Its saved graph hash is `087ee308ad47a22c0995e8fee99ab8608d24bc4f60fdc22a5a7ca2c7da155127`. The saved fixture reference, capture policy, ROI and completed-model hash were read back from stored artifacts. CPU execution of one synthetic training image passed fixture location and downstream model execution, returning NG. The private readback receipt SHA-256 is `ca8accdb90d97c4e88ff53c399ffa6c576516e4eea59791c9a740c77180f5a7d`. This establishes save/reopen/execution on this source snapshot, not representative model quality or operational approval.

### Windows packaged qualification implementation

A manual-only hosted Windows Server2025 workflow builds unsigned NSIS/portable artifacts with publishing disabled and existing release/acceptance hooks intact. Separate hash-pinned build tooling leaves runtime locks unchanged. The CI-only harness requires the actual delivered executable/resources, frozen backend and CPU child hashes, all four production preflight stages, identical persisted evidence on restart and owned cleanup. No private userData is uploaded. Independent review reproduced a false passing receipt on cleanup failure; corrected finalization now retains typed failure before receipt publication, and the inventory cannot promote it. Final local refusal/receipt gates passed nine Node and five Python checks. Actual hosted packaging/execution is pending; clean Windows11 installation, SCM/session0/reboot, signing, devices and model quality remain unverified.

### Training input and metadata picker closure

Unsaved classification and equivalent anomaly folder discovery now use the accepted inventory's canonical path eligibility, excluding hidden/service entries while preserving saved assignments, usage and explicit decode-error behavior. Actual DatasetIndex/loader regressions reproduced the earlier count mismatch; 58 affected checks passed. This is path eligibility parity, not proof that every arbitrary folder is a validated immutable revision.

The validated image picker now exposes existing tag/product/Lot query filters. Cursor ownership binds pagination to the exact query so a filter change cannot send the previous cohort cursor. Production component tests cover the three filters, first-page reset and retained image UUID selection. Native picker checks remain pending.

### Combined verification

Independent SPEC/QUALITY reviews and source-composition receipts cover the additional slices above. Shared exporter/runtime/flow hunks were composed with the fixture and capture-policy changes preserved. The latest private combined snapshot binds 1,201 source files; every file was rechecked against its inventory SHA-256. The inventory receipt SHA-256 is `4dbafe7d853169cef322aeab9028361898dfec77e5364c91402759acde9c58bf`.

| Latest composed-source check | Result | Scope |
| --- | --- | --- |
| Affected backend suite | 380 passed in 314.54 seconds | Selected regression checks on the composed integration source |
| Renderer suite | 555 passed, 0 failures | Composed renderer source and existing component/module suite |
| TypeScript checks | Passed, exit 0 | Renderer and Node build configurations |
| Generated API types | Current | Combined backend request/response models |

The hosted/source encoding failure was reproduced on default text reads in capture intake, fixture and capture-group modules. Explicit UTF-8 reads corrected it; the affected encoding/feature gate passed 51 checks before the fresh combined suite. The original failure remains retained as historical evidence.

Earlier E06/E08-only 221 backend/530 renderer and E01/E02 intermediate gates belong to earlier snapshots. They are not added to the fresh results or described as a full backend, native, GPU, Windows or field pass. An initial private setup mistake ran checks before composition; those logs remain setup-only evidence and are excluded.

## Actual CUDA catalog coverage

At frozen source `ce5172751c2921d5805092339f0a5f779918724d`, twelve remaining catalog cases completed through the production job/coordinator/SSH worker path on the selected existing L40S GPU. This batch covers classification DINO base/EfficientNet, segmentation DINO small/base, four remaining patch backbones, two standard axis-aligned YOLO detectors and two remaining anomaly entries.

The earlier twelve distinct cases and this twelve-case batch are disjoint and match the public 24-case catalog. Training history retains 26 attempts, 25 successes and one earlier failed attempt. Minimum model-family coverage is 10/10. Each new run bound 260 frozen backend files, input/snapshot manifests, terminal job journals, physical GPU identity and locally retrieved artifact hashes. Independent local auditing checked these bytes and identities.

These are bounded synthetic one-epoch/fit functional checks. DINO and YOLO use random initialization here, not authentic pretrained weights. Standard YOLO is not OBB qualification. They do not establish representative quality, independent held-out acceptance, all native family workflows, Windows or field readiness. Raw remote status/container responses are represented by retained hashes/checked facts rather than every original response; four patch outputs retain verified relocated local artifacts without all original remote checkpoint bytes. Two anomaly receipts lack hardware-statistics names but retain the CUDA-bound execution metadata and frozen learner/allocator checks.

## Native saved flow and package execution

A separate owned native project used a completed ConvNeXt classification model, saved an input/inspection/decision/output flow and initiated ten remote CUDA image inspections through the actual app. The SQLite history, saved graph, model hash, remote input/code archives and ten returned output hashes were read back. All ten finished as NG; there were no human reviews. The inputs were synthetic validation images and this result is not a quality approval.

The same saved flow exported an unapproved CPU package and compared eight fixed inputs between the app engine and isolated Python package: all matched. Its manifest contains 205 hashed entries (the app's 206-file count includes the manifest). C++ Predictor/Executor binaries and the native library were compiled with the existing macOS compiler/Python development files. One actual C++ Predictor input and one actual C++ Executor JSON request each succeeded using the saved CPU package and the same image. Both returned success/NG and one defective ROI; their defect scores agree. The SDK uses the bundled Python package runtime; this is macOS build/execution evidence. The native app reopened that package and executed one image on CPU, retaining a device execution record with the same manifest hash.

Remote CUDA inspection and CPU package parity are distinct targets. Selected-target remote package parity is implemented and reviewed in the composed source; the first rebuilt-app CUDA comparison failed at independent package dependency import as recorded above. C#/.NET, OpenVINO, real Windows installer/SCM/reboot, physical camera/PLC/MES and representative quality remain unverified by this record. Private images, model weights, databases, profiles, source locations and execution logs are intentionally kept outside public source.

## Pending acceptance on the latest combined source

- Selected remote target: successful actual CUDA package parity after the reviewed coordinate serialization correction, with parent/child GPU identity readback; preserve both failed receipts.
- E08 restore UI restart readback: the fresh-directory operation, full inventory and same-ID ledger restart checks passed, while the blank Task Center list requires refresh correction.
- Hosted unsigned Windows packaging/execution and native metadata filter checks remain pending.
- Remote-model evaluation on a separately frozen common cohort remains a distinct implementation follow-up.

Earlier saved-flow CUDA, CPU package parity and SDK execution remain their own verified snapshots. They add no training attempts, no parent completion promotion and no acceptance of the latest combined native/GPU paths. This documentation update changes no global program counters.
