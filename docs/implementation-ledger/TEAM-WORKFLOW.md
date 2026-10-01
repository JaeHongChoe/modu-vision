# Team workflow completion ledger

Baseline: `89eb367cbc03f6de3e3a5ebe944ff86ecd6f87da`.
Spec: `docs/superpowers/specs/2026-10-01-team-workflow-completion-design.md`.
Plan: `docs/superpowers/plans/2026-10-01-team-workflow-completion.md`.

## Implemented scope

| Workstream | Delivered | Detail |
| --- | --- | --- |
| Team data | Versioned guidance, assignment/priority, owned edit leases, one/two reviewers, disagreement/adjudication, approved-only input and immutable provenance | [TEAM-DATA.md](TEAM-DATA.md) |
| Model workflow | Ten-family capability parity, selected execution target, full specialist config/parent, separate execution/model IDs and compatible flow handoff | [TEAM-MODEL-WORKFLOW.md](TEAM-MODEL-WORKFLOW.md) |
| Compatibility/delivery | Pre-write schema gate and recoverable migration, configured manual update integrity/signature evidence, durable native startup descriptors | [TEAM-DELIVERY.md](TEAM-DELIVERY.md) |
| Integration | Team workspace in labeling, preparation/review handoff, queue navigation, persisted project/source/labelset/API scoped wizard stage | Renderer and router integration |

The essential additions are guidance versioning, edit ownership/conflict protection, approval eligibility/preflight, and change invalidation/provenance. Existing projects retain their policy until explicitly enabled.

## Verification

Results overlap; do not add these test counts together.

| Verification | Result | Timing/scope |
| --- | --- | --- |
| Broad backend regression | **1,608 passed; 28 skipped** | After initial integration and CLI staging-labelset repair; before the later native-QA preflight and session-author repairs. |
| Post-native-QA workflow regression | **70 passed; 4 skipped** | Team APIs/engine, model workflow, training CLI/provenance and archives, including empty eligible train split and missing guidance-class rejection. |
| Post-review authorship regression | **76 passed** | Shared accounts/security, annotation storage/overlay, interoperability, masks and suggestions; omitted author single/batch/import saves derive authenticated identity and cannot self-approve. |
| Renderer regression | **143 passed** | Existing and new behavior contracts, including current-expiry renewal, delayed renewal after save, selected preset propagation and fresh-session stage restoration. |
| Independent follow-up review | **19 renderer + 3 API regressions passed** | Separate reviewer reproduced three findings and confirmed their repairs; no remaining Critical/Important issue found in those repairs. |
| Independent late-change review | **35 regressions passed** | Stage/source restoration, live loading guards, preserved splits, priority zero and empty queue reviewed; no Important issue found. |
| Typecheck/build | **Passed** | Main/preload and renderer; Vite reports existing bundle-size/static-and-dynamic-import warnings. |
| Private macOS package integrity | **40 passed; 0 failed** | Packaged renderer/main/preload and Python source hashes match the current build; examples/native SDK and exclusions checked. |

### Reproduced failures and repairs

- CLI staging/historical labelsets initially caused 13 broad regression failures. Independent CLI preparation now verifies the exact selected labelset while desktop training still rejects a changed active labelset.
- One approved validation image and zero approved training images initially passed model preparation. Preflight now checks the effective saved train/validation counts and reports the missing training cohort.
- A published book could omit a saved class while training binding still succeeded. Binding now rejects the same class mismatch shown by readiness without deleting labels.
- Lease renewal used an earlier expiry after a same-token extension. It now checks current ownership/expiry. A delayed renewal cannot replace a newer saved metadata revision or review state.
- Preparation omitted the selected precision/fast preset. The request and response scope now use the actual preset, including parent/cache checks.
- Omitted annotation author in shared mode defaulted to a local operator identity and bypassed self-review prevention. Save, batch, format/mask import and suggestion review now bind the authenticated session author.
- A restart initially returned to stage 1. Navigation now remembers an integer stage from 1 through 6 for the exact project, source, labelset, task and API identity; unavailable storage falls back safely. Restoring a later stage also reimports the saved source/effective split instead of requiring a visit to stage 1.
- Model-family navigation is disabled during project/data loading. Empty-cohort guidance reports actual train/validation counts and routes to data or review instead of suggesting an immediate repartition of held-out data.

## Native acceptance with supplied images

An isolated private macOS application and project used **88 supplied source images**. Native clicks observed:

1. Open source and selected original image; assign an operator/priority and configure edit/review policy.
2. Publish guidance with definition, inclusion/exclusion, instructions and a hash-bound original example.
3. Acquire edit ownership; exercise zoom/Fit/1:1, delete/Undo and save the project overlay.
4. Reject the author's own approval; submit two different reviewers' opposing votes; record a third reviewer's final adjudication.
5. Return to the same project in training preparation. One validation-only approved image leaves training disabled and preflight reports the missing eligible training split.
6. Inspect classification/detection/segmentation and specialist preparation navigation. Unsupported truth prerequisites remain visible; selector navigation does not count as completed training/evaluation.
7. Reopen the final adjudication and reason with further voting disabled. Filter the queue by assigned operator (one result) and assigned operator plus pending review (zero results).
8. Quit/restart the final build and return directly to stage 3 with **88 original images**, the same guidance/review eligibility, and effective **train 0 / validation 1 / test 0** restored. This is an intentional validation-only QA cohort; training remains blocked.
9. Inspect actual app/backend version **0.1.0**, schema **1**, update channel not configured and native startup not registered. The private QA app reports a failed native signature check; no publisher signing identity was available and no release certification is inferred.

Original image/adjacent annotation bytes are independently checked: **171 of 171 files unchanged**, with no added or missing source files. Review operators in local desktop mode are declared QA identities; shared session enforcement is separately exercised through authenticated API clients. These observations establish application behavior, not independent human label-quality approval.

## Acceptance boundaries

- New native training/evaluation across all ten model families is not claimed. See the per-family matrix. Supplied defect images do not establish a normal cohort, OCR strings, verified correction angles or independent improvement targets.
- Real CPU loopback OCR training/transfer/reopen/continuation was exercised in automated integration; a physical server/GPU run is a separate state.
- Automated candidate search remains local only and cannot silently use local compute when a server is selected.
- Guidance review checks the same saved label with one/two people. Independent double annotation with automatic consensus is not implemented.
- The private macOS package is unsigned and uses the configured development Python. Signed/notarized releases, a real HTTPS update channel and a frozen backend binary remain external delivery prerequisites.
- Native startup descriptors are implemented and tested with isolated OS responses. Actual global installation, reboot/logon persistence, PLC/MES equipment and Windows/Linux acceptance are not claimed.
- Source publication to Git is separate from release publication and operational/model approval.
