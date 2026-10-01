# Team data quality implementation

## Delivered behavior

- Project/source/labelset scoped, immutable label guidance versions retain class IDs, definitions, inclusion/exclusion rules, annotation instructions, example source hashes and publication actor.
- Image assignment and priority feed a filtered, paged work queue. Shared mode lists project members and derives actors from authenticated sessions, including omitted author fields in single/batch/import saves; request bodies cannot impersonate another reviewer.
- Owned edit leases require an exact image, current revision, actor and token. Renewal preserves token/content revision and advances expiry; reacquisition rotates ownership. Expired, foreign and stale saves fail without discarding the editor's draft. Public API responses exclude token hashes.
- One- or two-person review applies to the same saved label. Self review can be prevented. Disagreement/rejection requires a recorded final adjudication; its actor, decision and reason appear in the workspace. This is not independent double annotation or automatic label consensus.
- Original/annotation/mask/guidance/policy changes invalidate prior votes. Direct approval cannot bypass enabled team review. Version/archive restoration clears live leases and votes while retaining guidance history.
- Approved-only training is opt-in. The backend filters source selection and verifies all six specialist source mappings. Saved prepared data cannot consume a source that is no longer approved. Training stores immutable guidance, policy and eligible-cohort receipts/hashes.
- A guidance/class mismatch blocks both readiness and creation of a training binding. Basic-model preflight also checks actual eligible train/validation partitions; approved validation images alone cannot make an empty training partition appear ready.
- Existing desktop training rejects active-labelset switches. Independent folder/JSON workspaces can validate staged/historical preparations while retaining the same project identity, labelset storage, input hashes and review checks.

## Verification and boundaries

- Lease conflicts, actor spoofing, class mismatch, receipt mutation, scope changes, all-six-family source rejection/acceptance, archive/version invalidation and same-token renewal are covered by engine/API tests.
- Renderer behavior tests exercise the actual lease-start/save handlers, delayed old-project responses, project reset, expiry after renewal, delayed renewal after a newer save, guidance palette preservation, and final adjudication visibility.
- Packaged native macOS acceptance used 88 supplied source images in an isolated project: assignment, guidance/example publication, policy controls, owned edit/save/undo, self-review rejection, two-reviewer disagreement, final adjudication and transition to training preparation were observed.
- Local desktop actor names are declared operator identities. Shared authenticated-account/role enforcement was checked separately through independent API clients.
- Model quality, independent human agreement and operational approval are separate acceptance states. Source bytes are verified independently in the integration ledger.
