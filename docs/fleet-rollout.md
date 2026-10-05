# Fleet deployment qualification

## An acknowledged release

Register an authenticated HTTPS agent origin, or an HTTP loopback tunnel. Tokens are omitted from target-list responses. The central registry checks current project/source approval eligibility, exact manifest and supported device before sending a release. The receiving agent verifies the full archive and separately trusted approval/device/cohort policy.

The central side requires an exact `staged` manifest receipt, then a ready apply ACK with the requested manifest/device, then independent runtime readback. An HTTP success alone is insufficient. Rejected ACKs never become an active central deployment. Recovery preserves a previously acknowledged release when its exact policy and runtime can still be verified.

## Canary and bounded batches

A saved plan fixes selected targets, endpoint URLs, package identity, device, canaries and batch size. First apply only the canaries; subsequent batches require explicit canary confirmation. Each action pins the current plan revision and rechecks previously applied targets. Changed endpoints, unavailable readback, stale approvals or mismatched releases stop advancement. Pause records a reason; resume requires fresh readback. Reopening reads persisted plan and audit events.

Rollback proceeds in reverse bounded batches to each target's previously acknowledged deployment. Current eligibility and receipt/readback checks still apply. Interrupted operations require explicit receipt reconciliation and cannot silently count as completed.

## Central connection and offline operation

Losing the agent connection makes central observations unavailable and pauses new commands. It does not stop an already acknowledged standalone inspection runtime. The software qualification measures a completed CPU inspection while the owning agent connection is absent, followed by reconnect, canary/batch deployment and rollback. It does not establish an operational availability guarantee.

## Scope

Actual Chrome and macOS development Electron qualify registration, two actual owned loopback OS agents, deterministic CPU inference, disconnect continuity, explicit canary approval, pause/resume, plan reopen and reverse rollback. Synthetic model weights and controlled approval reports prove software wiring. Representative process quality, physical devices, long-duration/reboot behavior, installed field targets, independent review and full acceptance remain pending. Windows tests are excluded.

Project/account/source changes invalidate retained screen actions and delayed replies. Shared server middleware supplies the authenticated reviewer rather than trusting a typed identity.
