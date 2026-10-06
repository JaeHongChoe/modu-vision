# Maintenance and release policy

Maintain separate queues for security, data integrity, incorrect verdicts,
compatibility regressions and usability. A public report must use redacted
reproduction data; private vulnerability handling follows `SECURITY.md`.

Every patch names its source revision, supported combinations, changed contracts,
regressions, dependency/license changes and known issues. Publish only after the
release checklist is satisfied; development builds stay explicitly unsigned or
unqualified where applicable. Never rewrite a user's data or Git history to make
an evidence check pass.

Schema/protocol deprecation requires a migration dry-run, retained backup,
interruption recovery and a compatibility window documented before removal.
After new writes, an old backup is not an unconditional restore strategy.

Practice backup/restore and patch rollback on owned isolated installations before
setting a production cadence. Assign maintenance responsibility and measure
actual service availability before promising an SLA. Pilot feedback and incident
fixes feed the next release; no operating owner, SLA or quality approval is
asserted by this development policy.
