# Remaining work execution — 2026-10-24

Scope: continue the approved easy-use design and Claude handoff in an isolated
worktree, while the existing PR #6 exact-head regression/merge worker runs.
Do not change its checkout, tested head, CI, or original data.

1. Make annotation deletion recoverable. Capture the JSON/mask bytes and actor
   before removing active files; expose explicit restoration without overwriting
   newer labels. Preserve project scoping, team leases/revisions, review
   invalidation, audit identity and exception rollback. Test byte-for-byte
   recovery, refusals and injected publication/ledger failures first.
2. Improve the documented 1024×768 full-view canvas without removing controls,
   warnings or changing the user's persisted view choice. Verify renderer
   geometry, types, and a stable build in the real application.
3. Verify OS-protected key storage with dummy credentials and separate process
   restarts in a uniquely named isolated Electron profile. Stop at an OS consent
   prompt; never read another application's credentials or call a provider.
4. Resolve the local DICOM optional-runtime gap and run only its three previously
   unverified browser cases, using an isolated interpreter and bounded QA data.
5. Reduce the repeated SAM2 point actions with a session-only explicit opt-in.
   Generate a preview after a new point, keep custom options across the closed
   popover, and preserve manual adoption, scope/labelbook/fingerprint guards,
   single-flight ownership, and late-response suppression. No automatic retries,
   adoption, next-image movement or new model download.
6. Refresh the official ChatGPT sign-in feasibility/terms evidence. Keep live
   OAuth/client registration/terms acceptance disabled until the user's own
   login and consent; prepare protocol tests only if its prerequisites allow it.
7. Integrate related checks and actual Electron UI QA; review the resulting
   diff independently. Update the handoff/design status with evidence and limits.
   Commit/push a meaningful source bundle, and obtain exact-head hosted checks
   after the existing PR is settled; do not start duplicate full regressions.

Human outcomes remain distinct: real-key/paid/outbound-image consent, novice
first-use timing, representative field ground truth, model-quality acceptance,
and operational deployment approval. Prepare concrete procedures and fixtures
without treating scripted/synthetic checks as those human outcomes.


Progress at source freeze:
- Steps 1–5 implemented and locally verified. Recovery/transaction/team group82,
  existing version/annotation integration29, renderer1791, focused layout56,
  geometry/popover7, safeStorage unit8, DICOM browser3 in each isolated and
  project runtime, public point browser1, final actual offline CPU SAM2 browser1.
  Counts are separate scopes and are not added into a single acceptance total.
- Real uniquely named Electron safeStorage dummy save/restart/remove/restart and
  actual isolated Electron small-window UI follow-up completed. No real key,
  paid provider, field-quality or novice measurement claim.
- Step6 official sources refreshed; live SIWC registration/login still requires
  the user/organization's terms and account decisions. Human validation worksheet
  is prepared and the representative-data question remains pending.
- Step7 independent review and local final renderer/types/build/evidence gates
  passed. Meaningful commit/PR and exact-head hosted regression follow source
  freeze; old PR6 success does not qualify this work. Keep ux monitoring paused.

Whole-design audit follow-up (user correction, 2026-10-24):
- The first source bundle was not the complete design. Exact-head full source
  regression39992275090 failed its Korean refusal check while browser passed;
  merge worker safely held. Four newly added recovery strings were corrected,
  with the unchanged refusal test and scoped recovery/security/transaction/team
  tests99 passing.
- Implement missing assistant scope/preview/image/receiver binding and async
  response guards, including ABA epochs and failed IPC grants. Add actual-store
  lifecycle tests and scoped mutation/read/navigation checks.
- Implement existing design intents for training readiness/settings, ordered
  pending draft opening, and evidence-bound evaluation threshold guidance.
  Wire split prefill to the already-mounted calibrator without a split write.
- Close evaluated UI omissions: shared evaluation/flow errors, folded technical
  detection metrics, existing-label import entry, and concrete auto-start summary.
- Prepare offline SIWC protocol contracts/fixtures only. Live registration,
  browser login, terms acceptance, account/plans and application integration remain
  distinct conditional work; never reuse another application's auth material.
- Integrate focused/renderer/types/build/evidence and narrow actual browser QA,
  review the final source, update PR10, and run one new exact-head full regression.
  No new model/training, existing held QA or recurring monitor is started for this.

Follow-up source freeze:
- Missing easy-use/assistant paths and scoped consent guards implemented.
- Offline SIWC module20 synthetic cases; deliberately no production import/live
  transport/account/persistence. Full app connection remains conditional work.
- Full renderer/main Node suite1878/1878; renderer/main/E2E types, build and
  source/action evidence gates passed. Scoped backend99 and targeted real browser1
  passed. Earlier integration failures and exact-head source failure are retained
  in the follow-up QA record rather than counted as passes.
- New source publication/exact-head hosted qualification follows. Existing
 15-minute ux monitoring remains paused.
