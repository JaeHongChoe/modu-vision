# Interrupted control-store restoration

Accounting remains **66 software verified /16 pending /0 finally accepted**.
Windows installation/use QA is waived, leaving15 actionable parents. This
closes a DB interruption-recovery component of S1-08/S6-04; application-installer
cutover, live-worker adoption and real publisher trust are still pending.

## Observed defect and repair

Restoration formerly published the replacement generation before recording its
identity. An interruption after that pointer change left a journal referring to
the retired generation. A fresh recovery command refused to continue, while a
failure before publication could create a second unrelated restoration target.

Restore now writes and synchronizes a `restoring` intent with the exact sealed
generation, content hash and next fence before publishing the active pointer.
An explicit repeated `recover --action restore` checks that same target and
finishes it without creating another generation. It can also acknowledge the
same completed restoration again while the recorded bytes are unchanged.
After a pointer-directory synchronization failure, retry confirms directory
durability before recording completion.

Original files and verified backups are preserved. Later source/current/target
writes, changed generations, malformed identities/fences, linked targets,
modified seals or backups refuse replay. They require forward recovery rather
than overwriting newer data. Original account identities and users survive;
copied sessions remain revoked. Initial and forward restoration use the same
durable replay path. This remains an explicit offline operation on the declared
owned/drained POSIX installation; it does not discover, adopt, shut down or
automatically restore another installation or worker.

## Qualification

- Initial qualification:4 expected failures and2 passes; failure artifacts retained.
- First implementation:8 failures and35 passes. The source hash remained valid,
  but the initial preview's deliberate second-activation refusal was incorrectly
  interpreted as restore permission. Correction binds the originally validated
  drained source by its unchanged hash; it does not permit a second activation.
- Durability-order qualification:1 expected failure before the directory-sync repair.
- Final affected run:116 passes,0 failures/errors/skips,75.814 seconds.
- CI/source-selection follow-up:18 passes,0 failures/errors/skips,3.435 seconds.

The17 restoration cases include real abrupt process exits before/after pointer
publication followed by a fresh production CLI, initial/forward retry, exact
generation reuse, new-write refusal, tamper/link controls, preserved original
bytes and revoked sessions. Controlled fault injection is distinct from a
physical power-loss or Windows qualification. This is root review, not an
independent reviewer or manufacturing-quality approval.

## CI preservation

The new cases are registered in the public CPU lane. Source concurrency now
preserves both running and pending qualifications with `queue: max` and
`cancel-in-progress: false`; a later push must not replace the only pending
exact-source run. This follows the
[official GitHub concurrency contract](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/control-workflow-concurrency).
Actual hosted results and their source commits remain separate from these
local passing controls.

## Remaining work

The complete task inventory is in `WORK-STATUS.md`. Application/DB coordinated
installation and signed publisher provisioning are not claimed by this repair.
The independent72-hour CPU run continues on its original frozen source and
owned external APFS scratch. These recovery tests do not add elapsed time to it.
