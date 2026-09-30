# P03 data, preferences, evaluation and shared accounts

Implementation remains under integration; no full phase acceptance is claimed.

## Added contracts

- Gallery labels retain every annotated class, including flat and saved split filters.
- Statistics report native image counts and ratios for labeled/unlabeled, each class, train/validation/test/unused/unassigned. Multiple classes do not increase the total image count. Empty editable annotations and absent masks do not fabricate labeled truth.
- `usage_state: active | not_used` is revision checked and audited. Unused originals remain on disk and are excluded from actual train/preparation enumerators.
- Project preferences record tag colors, label-set/model flags, author, audit and revision. The dataset controls render tag colors and update flags.
- Evaluation history binds the evaluation label set separately from the historical training label set, retains parent model identity and threshold settings, supports label-set selection and groups evaluations under each model.
- Prepared OCR evaluation maps copied pixels back to the verified canonical original for metadata and records the actual evaluation file separately. Ownership and source hashes are checked before immutable archival. Prepared OCR/OBB and Rotation reevaluation are routed to owned inputs.
- Optional shared server uses persistent accounts, scrypt password hashes, hashed expiring bearer sessions and explicit project membership. Selection is request scoped. Shared file reads, training job control and websocket job events remain within the authorized project. Each shared telemetry frame checks current session and membership.
- Electron retains the bearer token in its main process and attaches it only to the exact authenticated API/websocket origin. Connection requires HTTPS or localhost HTTP for a tunnel. The renderer receives public account/connection information only.
- `python -m backend.accounts_cli --help` documents local first-admin setup without passwords in command arguments. See `docs/shared-project-server.md`.

## Verification recorded

- Multi-class gallery/import regression group: 15 passed.
- Source usage, summary, patch preparation, comparison and preference group: 13 passed.
- Legacy annotation/project isolation/geometry regression group after integration repair: 46 passed.
- Prepared OCR/Rotation/comparison group: 9 passed.
- Shared-account/checkpoint isolation/telemetry/evaluation-recovery group: 47 passed. Initial isolation RED: global checkpoint was readable and websocket authorization filter was absent; both reproduced and repaired.
- Training receipt/review/cancellation regressions after stricter checkpoint ownership: 29 passed.
- Evaluation label-set/history/lifecycle/OCR/recovery group: 57 passed. Initial label-set selector RED was reproduced before implementation.
- Main-process session mock test and accounts CLI help executed successfully. These do not establish a real network deployment.

## Remaining acceptance

Independent shared-server security review; native UI save/reopen and multi-account connection; actual imported input statistics/flags and evaluation-label-set selection; no manufacturing quality approval. Broader gate is run after concurrent modules settle. Optional DICOM/mask work has a separate labeling worker ledger.
