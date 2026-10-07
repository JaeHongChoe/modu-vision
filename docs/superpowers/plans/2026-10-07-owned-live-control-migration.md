# Owned live control migration

> Execution: continue the existing S1-08 plan in this isolated checkout.

**Goal:** Transfer a running, positively identified local basic worker's control stores in the same explicitly owned POSIX installation without relaunching it or freeing its reservation.

**Architecture:** Ordinary offline migration remains drained and refuses stale handles. A separate live path admits only the new cooperative control protocol. Short control mutations share maintenance admission; an exclusive migration copies and seals the declared stores. Model outputs stay in their original registered project. Lease and job observers follow only a migration record that names their exact original owner, job and attempt fence. Uncertain reservations remain uncertain. No process is signalled, suspended or replaced.

**Tech stack:** Python, SQLite, POSIX flock, psutil, existing basic training CLI and job ledger.

**Spec:** S1-08 in service-upgrade-program.json and the phase-1 platform plan.

## Constraints

- Same directory identity and installation ID; no copied home, external path or remote authority adoption.
- Current exact schemas only. Existing historical and drained paths are retained.
- Fail before activation for unknown workers, unsupported protocols, namespace mismatch, changed specs, orphan reservations or stale review.
- Preserve job IDs, attempts, attempt fences, cancellation and reservation uncertainty. A later backend reattachment continues to fence an earlier observer out.
- No blanket refresh of stale account/context/store handles or resurrection of copied sessions.
- Test only new isolated CPU jobs; preserve the independent soak and existing GPU jobs.

## Review focus

1. Identity and authority: original project/actor, PID creation time, command, owned session/token, immutable spec.
2. Race boundaries: resolve paths under shared admission; all copied control writers must cooperate.
3. Recovery: a prepared transaction cannot restore/replay a live attempt; post-cutover writers are preserved.
4. Reservation: no new execution authority from uncertainty; exact owner/fence continuity.
5. Evidence: actual child process survives cutover and finishes once; old stores and source data remain intact.

## Tasks

1. Add failing tests for cooperative admission and narrowly bound lease following; existing stale handles must still fail.
2. Add the worker protocol opt-in and journal admission; preserve protocol-1 terminal compatibility.
3. Add an explicit live preview/apply CLI with complete store, worker and reservation inspection, backup, CAS, seal and pointer publication.
4. Rebind only the existing training observer for a carried job/fence; verify later reattachment rejects it.
5. Run an actual owned CPU CLI training across cutover plus failure, uncertainty, duplicate-launch and original-byte regressions.
6. Inspect the diff, run related migration/training tests and publish scoped code and evidence. Keep unsupported remote/old protocol and external acceptance conditions pending.
