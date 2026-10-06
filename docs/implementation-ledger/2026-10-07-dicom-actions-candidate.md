# DICOM runtime, action evidence and candidate inputs

Parent accounting remains **66 software verified /16 pending /0 finally
accepted**. Windows actual-use QA is waived, leaving15 actionable parents.
This batch advances S6-03, S7-01 and S7-07 without replacing their remaining
provider, complete-action, signing, target or human approval conditions.

## Actual optional runtime and two production corrections

The pydicom3.0.2 wheel matches the existing hashed CPU dependency lock and
provider SHA-256. Its original complete license notice is retained. The
2,379,963-byte pack is inventoried, installed inactive and verified on repeat.
Only a separate owned Python environment installs the reviewed local wheel;
the installed application environment and activation pointer are unchanged.

Five malformed-header cases initially fail: pixel decoding occurred before
dimension/frame validation. Rows/columns/frame checks now precede decoding,
and an explicitly selected frame is decoded directly rather than allocating
every frame. Three actual HTTP cases initially accept boolean/float/string
frame values; strict integer request validation now refuses them without
creating a view or changing the source.

Actual GUI qualification exposes a separate error-reporting defect. The API
transport throws the FastAPI validation array directly with HTTP status;
the renderer previously fell back to a generic error. The formatter now shows
bounded field/message information for both direct and wrapped validation
arrays and coded messages. It excludes input, context and URL values.

Final checks:52 related backend tests;144 action/GUI/plan/CI reference checks;
849 renderer/main tests; production and E2E types. The final source correction
is90f6c7c37aa031231554d8c1d85bd034bcfce13a. Clean-source browser and actual macOS
Electron cases pass without retries in27.1 seconds, with six screenshot hashes.
They apply the explicit200/400 window, reject zero width, retain the previous
view on actual network refusal, and regenerate the exact PNG after reload.
Original synthetic non-patient DICOM bytes remain unchanged.

## Action coverage with retained cases

`docs/service-action-evidence.json` lists all156 feature IDs.19 curated actions
now have32 verified scenario references and101 explicitly pending scenarios.
The remaining152 feature action lists are unreviewed. These are scenario
references, not complete feature acceptance. Empty/cancel/handoff and other
unexecuted paths are never inferred from a case's success.

`scripts/check_action_evidence.py` checks the exact seven scenario names,
owner, actual clean GUI receipt/case/hash, current UI source hash, duplicate
actions and reviewed waiver reasons. Ten causal controls exercise its missing
implementation and refusal paths. The source CI now runs the gate and tests.
Malformed or missing data cannot become a silent pass.

The feature-dimension registry separately gains15 measured GUI/persistence/
reopen/failure entries for U010, U012, F024 and F116. The declaration-only AST
inventory now includes the relevant helper panels:58 unique UI sources and795
declared actions across156 feature mappings. Declared actions supply no
execution evidence. The large inventory and all failed attempts remain in
private external scratch rather than inflating the public source tree.

## Candidate disposition

The actual source-bound development dependency inventory records590 components
and53 unresolved conditions. Every artifact scope remains held by the release
gate: source, installer, optional pack, remote worker, runtime download and
trained export. Named distribution authority, source assets/weights and
matching shipped notices remain required. This development interpreter's
inventory is diagnostic input; it must not replace the locked frozen build's
SBOM or rewrite that build's notices.

The DICOM pack's exact notice and qualified combination are now documented.
There is still no final delivered signed artifact, complete156-action ledger,
human pilot or industrial target/model-quality receipt. The independently
running72-hour inspection remains the final duration gate; previous failed
attempt time is not added to it. The public paired receipt retains these
pending conditions and the input hashes. No release or deployment occurs.
