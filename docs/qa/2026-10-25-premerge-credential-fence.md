# Premerge credential-clear failure fence

Review of PR #10 found that an uncertain rotating refresh could settle after its
replacement save and credential clearing both failed. The session's refresh
promise was then empty, so disposal released the store lease while the old
ciphertext remained reusable after restart. Failed sign-out during a pending
refresh had the same ownership-detachment risk.

The connection now retains a record-level unsafe-credential marker until clearing,
a replacement login save, or confirmed store recovery succeeds. Disposal retries
clearing marked records as well as pending refreshes. If any clear still fails,
it retains the store lease. A detached refresh cannot clear a newer login.

## Local evidence

- The first three restart regressions failed against unmodified `db8f575a`.
  A related pending-refresh/sign-out regression also failed before its fix.
- Five focused regressions now pass: uncertain transport plus failed clearing,
  rotated replacement save plus failed clearing, transient-clear retry on
  disposal, failed sign-out with a detached refresh, and a persisted reconnect.
- The tests use the real filesystem-backed `SiwcStore` and lease with generated
  credentials, a fake encryptor, and injected transport responses. They make no
  real account or provider requests and read no credential profiles.
- Connection/store/protocol/integration/IPC suite: 120 passed, 0 failed.
- Complete `npm run test:renderer` Node suite: 2,132 passed, 0 failed.
- Renderer/main typecheck, E2E typecheck, build, service-plan source gate,
  action-evidence gate, and `git diff --check` passed. The build retains its
  existing mixed-import and large-chunk warnings.
- Independent source review approved the narrow fix with no remaining Important
  blocker for this credential fence issue.

These results cover local implementation behavior. Real provider behavior,
representative model quality, novice measurements, and whole-product human
acceptance remain separate. Earlier fast/full/gate successes qualify only
`db8f575a`; the commit containing this fix requires its own hosted CI before
merging. Existing deferred product limitations remain recorded in PR #10.
