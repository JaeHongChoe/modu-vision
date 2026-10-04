# Remaining implementation and execution evidence

This continuation advances the existing 82-item program. It keeps implementation, actual execution, representative model quality, Windows qualification and parent acceptance separate. No parent acceptance or field release is granted by this record.

## Integrated production changes

Base: `ce5172751c2921d5805092339f0a5f779918724d`.

### E06 sampling through capture intake

The existing intake sampler is now used by the production service-capture registration path. A project-scoped opt-in policy has immutable revisions, window/seed/quota accounting and replay receipts. A skipped event stages no image. Selected captures retain UNKNOWN truth and require the existing human review/adoption path. Held-byte accounting includes recoverable trash, and retention failure is visible. Local desktop token authority and shared-account owner authority remain distinct.

Actual macOS source-built Electron checks in an owned synthetic project saved policy revision 1, registered one synthetic service fixture through the production API and reopened it. The app showed one held capture (110 bytes), one selected event and unknown truth. This is a UI/API integration check, not a physical camera capture or a model inference claim.

### E08 durable import and backup

The existing JobStore now retains operation snapshots, expected revision, attempts, progress units, staged output and verified result references. Interrupted imports and backups resume through the same job identity. Source drift, revision drift, cancellation before sealing and invalid/expired download results are guarded. Backup source comparison uses committed logical SQLite snapshots, including WAL commits. Restore through this resumable-operation interface is explicitly unsupported; the existing verified archive/new-directory restore remains separate.

Actual macOS Electron started an import, closed/reopened its panel and retained the same job ID with two verified images and a result hash. Task Center started a project backup, showed its 19-file verified result and expiry, and saved the archive through the native dialog. The downloaded archive SHA256 matched the durable result reference. The owned app was then restarted with the same stores. Task Center retained both exact job IDs, attempt 1, result count/hash and backup expiry; no extra job or attempt was created. These private fixtures contain no user source data.

### Combined verification

The exact composed E06/E08 candidate passed 221 backend checks covering capture intake, import/backup recovery, archive compatibility and JobStore. The renderer suite passed 530 checks. Both TypeScript checks passed, and generated API types are current. These are scoped checks; their counts must not be added to overlapping historical runs or described as a new full backend pass.

An initial private setup error read a list-shaped lane manifest as an object and ran checks before composition. Those logs are retained as setup-only evidence and are excluded from these results. The combined source was then copied from the independently reviewed candidates, hashed and verified.

## Actual CUDA catalog coverage

At frozen source `ce5172751c2921d5805092339f0a5f779918724d`, twelve remaining catalog cases completed through the production job/coordinator/SSH worker path on the selected existing L40S GPU. This batch covers classification DINO base/EfficientNet, segmentation DINO small/base, four remaining patch backbones, two standard axis-aligned YOLO detectors and two remaining anomaly entries.

The earlier twelve distinct cases and this twelve-case batch are disjoint and match the public 24-case catalog. Training history retains 26 attempts, 25 successes and one earlier failed attempt. Minimum model-family coverage is 10/10. Each new run bound 260 frozen backend files, input/snapshot manifests, terminal job journals, physical GPU identity and locally retrieved artifact hashes. Independent local auditing checked these bytes and identities.

These are bounded synthetic one-epoch/fit functional checks. DINO and YOLO use random initialization here, not authentic pretrained weights. Standard YOLO is not OBB qualification. They do not establish representative quality, independent held-out acceptance, all native family workflows, Windows or field readiness. Raw remote status/container responses are represented by retained hashes/checked facts rather than every original response; four patch outputs retain verified relocated local artifacts without all original remote checkpoint bytes. Two anomaly receipts lack hardware-statistics names but retain the CUDA-bound execution metadata and frozen learner/allocator checks.

## Native saved flow and package execution

A separate owned native project used a completed ConvNeXt classification model, saved an input/inspection/decision/output flow and initiated ten remote CUDA image inspections through the actual app. The SQLite history, saved graph, model hash, remote input/code archives and ten returned output hashes were read back. All ten finished as NG; there were no human reviews. The inputs were synthetic validation images and this result is not a quality approval.

The same saved flow exported an unapproved CPU package and compared eight fixed inputs between the app engine and isolated Python package: all matched. Its manifest contains 205 hashed entries (the app's 206-file count includes the manifest). C++ Predictor/Executor binaries and the native library were compiled with the existing macOS compiler/Python development files. One C++ predictor input succeeded with the same NG summary as Python. The native app reopened that package and executed one image on CPU, retaining a device execution record with the same manifest hash.

Remote CUDA inspection and CPU package parity are distinct targets. Remote package parity is still being integrated. C#/.NET, OpenVINO, real Windows installer/SCM/reboot, physical camera/PLC/MES and representative quality remain unverified by this record. Private images, model weights, databases, profiles, source locations and execution logs are intentionally kept outside public source.
