# Remaining validation and labeling responsiveness

The user asked to finish the remaining work across easy use, labeling assistance and the embedded account-based agent. Continue autonomously under the existing authorization. Actual human participation, login/terms, real provider consumption, field truth and operational approval are not replaced by automated evidence.

## Chosen approach

Add an opt-in, local, in-memory study recorder to the existing first-use guide, fix the demonstrated SAM2 embedding-cache identity bug, and prepare a normal native-account acceptance runner. Reuse existing project, labeling, account and approval flows. A second wizard, broad telemetry service, automatic labeling adoption, new model downloads and tuning the already-exposed real-image diagnostic cohort are unnecessary.

The alternatives were paper-only measurement, which does not collect app interactions, and a backend telemetry store, which adds a persistent writer and private-data surface without helping this local study. The recorder exports an explicit JSON download instead.

## Recorder

- Modes are `human_self_reported` and `automated`; both export `acceptance: unverified`. Event trust is not proof of a human.
- Tasks are the first-project path, manual labeling and assisted draft review. One active session, duplicate starts refused, immutable completed/aborted snapshots.
- Elapsed time starts before setup and includes waits/background time. No pause or idle deduction. Closing the guide does not stop observation.
- Before data exists, count setup interactions. Bind the chosen project/source/task/label set/authority once explicitly after setup; following scope changes terminate an exportable partial. Bindings remain private and are never exported.
- Record only aggregate pointer/keyboard counts, fixed milestone enums/timestamps, optional nonnegative self-reported native-dialog action totals, and validated app/build metadata. No key names/codes, field contents, DOM labels, coordinates, paths, images, prompts, answers, credentials or raw store objects.
- Separate recorder-control actions from workflow actions. Native dialogs and system-browser actions are unobserved, not assumed zero. Target results remain observational/unverified when coverage is incomplete.
- JSON export is a user action with no server/provider communication or original-data writes. Manual/assisted per-image completion markers are self-reported, and comparison includes all elapsed time and corrections.

## SAM2

The adapter currently caches by PIL object identity, while each request decodes a fresh object. Use one bounded embedding entry identified by exact decoded mode, dimensions and pixel digest. Model-directory signature and device remain part of adapter identity. Preserve cancellation, coordinate/mask handling, per-request prompt decoding and serialization; cache no proposals or raw exported image data. Pixel changes, in-place mutation and shape changes must invalidate the embedding. Do not claim field accuracy or wall-clock speed from fake-model call counts.

## Real-account preparation

Reuse production main/preload/renderer and normal OS safe storage in a uniquely named isolated package. Preparation must not login, consent, load remote models, send a question or revoke credentials automatically. Do not inherit unrelated environment credentials/startup hooks. Keep fixture and real-account modes distinct. Any later real inference requires the person's explicit consent; a UI turn can make several provider calls. A one-request acceptance helper uses an empty tool list and contains no project/image context. Save only projected metadata and source identities, never authentication URLs, account labels, tokens or question/answer bodies.

## Evidence limits

Automated recorder QA is not a novice trial; repeated-click timing is not model quality; generated credentials are not a real account. Preserve old CI and receipts. Publish meaningful source changes together and qualify their exact final head before any merge. The stopped recurring monitor remains stopped.
