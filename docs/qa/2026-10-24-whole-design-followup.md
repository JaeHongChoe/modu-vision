# Whole-design follow-up — 2026-10-24

This continues PR #10 after the user correctly distinguished the first remaining-work
bundle from the complete easy-use design. Original evidence remains in
[the first bundle record](2026-10-24-remaining-work.md).

## Implemented gaps

- Assistant conversation authority and history belong to the accepted project,
  source, labelset and transport. Late provider, preview and tool replies do not
  write into another project's UI/history/run log. Image/settings ABA transitions
  invalidate old confirmations through monotonic revisions.
- A write requires a successful `beginRun`. Its post-write authority check allows
  its own metadata/count/status changes, while still rejecting a different
  project/source/labelset/transport. Failed cleanup reports the executed outcome
  instead of silently losing it and inviting an accidental repeat.
- Image confirmation binds the selected image's identity/revision and the complete
  receiving provider/endpoint/model/format. Reading pixels remains after consent;
  local-read transitions, single-use, expiration and settings changes are checked.
- New assistant tools read training readiness, prefill next settings, list/open
  pending drafts in confidence order, and explain current evaluation threshold
  evidence. They reuse existing stores/APIs and never adopt drafts, apply a
  threshold, download weights, or start training without a separate write preview.
- Split prefill updates an already-mounted calibrator without changing the saved
  split; a consumed request does not undo a later manual edit.
- Evaluation/flow errors feed the common guidance with owned scope cleanup and
  late-response guards. Detection technical metrics fold in simple view while
  image-level error counts remain. Draft review links to existing label import.
  Auto progress names the concrete current/default model, preset, execution
  location and weight source, without claiming unknown preparation is ready.

## Verification boundaries

The previous exact-head full run39992275090 passed browser but failed source:
`test_beginner_errors_korean... [routes_annotation.py]`, with5048 passed/17 skipped/
1 failed. The merge worker held and no merge occurred. Four new recovery refusal
strings were corrected without weakening the test. The scoped Korean/recovery/
path-security/save-transaction/team backend group passed99 tests.

The first combined renderer run found19 old component harness imports that did
not yet load the new shared-error hook, one helper/test edit collected while it
was being finalized, and a generated theme mismatch. The component assertions
were retained; the two harness adapters now load the real hook, the train/val
readiness helper preserves an absent test count as null, and the theme was
regenerated. These failures are not represented as product runtime failures or
as a green whole suite. Final combined results follow source freeze.

Narrow browser QA uses the real built renderer/backend, real synthetic project,
images and stores. Only the assistant provider IPC bridge is scripted; there are
no external provider calls, real credentials, learned model calls or training.
The saved80% split and20 source image hashes remain unchanged when the already
open bar is prefilled to70%; the original label exchange is opened without an
import write; moving to the next actual image removes the previous image offer
without any image send. The fixture and screenshots are recorded by
`assistant-easy-use-supplemental.spec.ts`.

Offline SIWC protocol contracts are implemented in `src/main/assistant/siwcProtocol.ts`
and tested with20 synthetic-only cases: S256/state/nonce/one-use callback, issuer/
audience/signature/expiry identity checks, rotating refresh single flight, local
sign-out and explicit remote-revocation uncertainty, namespace/function-result
replay, required completed SSE output, malformed/truncated/failed streams, size
limits, cancellation and timeout. Transport and trusted JWKS are injected; tokens
are memory-only. No production provider/service/IPC/UI imports this module.
No app registration, browser login, terms acceptance, token file reading, real
account, plan request or user key storage is performed by this follow-up.
Discovery/JWKS acquisition, protected persistence, complete application integration,
HTTP/error-header handling and actual account validation remain conditional work.

## Still distinct from implementation

- Novice user30-minute/40-click and per-image timing measurements.
- Representative field images, confirmed ground truth, missed-defect/false-alarm
  tradeoffs and human model-quality approval.
- Real-key/paid/outbound-image consent and provider/account checks.
- Conditional live ChatGPT integration, terms/login and account-level validation.
- Exact new-head hosted regression and merge qualification. Prior-head fast or
  browser success cannot qualify changed code.

The15-minute `ux` monitor remains paused. There is no deployment or service registration.

## Final local source qualification

- Full renderer/main Node discovery:1878 tests,1878 passed,0 failed/skip
  (41.02s). This includes the20 offline SIWC protocol cases.
- Renderer/main type checks, E2E type checks, production build and diff whitespace
  checks passed. The build retains its existing large-chunk/import warnings.
- Source/service-plan gate: ok, accepted work packages0. Action evidence gate:
  ok,207 curated actions/1014 verified scenarios/435 pending scenarios,
  accepted features0. These gates validate references and scope, not whole-product
  or human acceptance.
- Narrow actual browser case:1 passed (8.1s body/17.8s including fixture), provider
  IPC scripted,0 provider requests/image sends/page errors/console errors.
  Fixture-owned backend exited0, port closed,0 process leftovers.
- Korean/refusal/recovery/security/transaction/team backend scope:99 passed.
  Existing broader source regression remains a hosted exact-new-head check.
- Independent reviews covered assistant authority/ABA/IPC and offline SIWC
  contracts; identified defects were reproduced before repair. No additional
  important finding remained at source freeze.

A new exact-head hosted run must qualify the published commit. No claim of merge
or whole-product acceptance follows from these local results alone.

A second, separate real-browser integration case verifies the confirmed write:
declining the first split preview causes0 split POSTs; confirming the next preview
causes1 real split POST and persists14/4/2. The production conversation/runtime/
dataset stores finish with refused/ok run-log entries and a completed answer.
Source image20 hashes are preserved, with0 external requests or page/console
errors. This one new case passed in13.9s (24.6s with fixture); its owned backend
exited0 with port closed and0 leftovers. The approval bridge is scripted, so it
does not test the native main-process assistant-run header or real OS consent.
`assistant-confirmed-split.spec.ts` was run once; the earlier browser case was not
repeated for this check.
