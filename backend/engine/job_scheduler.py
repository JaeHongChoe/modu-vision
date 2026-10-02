"""Job scheduler and ownership (S1-03): queue, priority, fairness, quota, device reservations and leases.

The ledger (JobStore) keeps the queue, attempts and fences; the shared reservation database (ResourceLeases)
stays the single device-reservation authority for every app version, so an older app that is still running
sees the scheduler's reservations and the scheduler honours the older app's. Nothing is migrated.

A claim is ordered so every crash point is safe: reserve devices (unfenced, short TTL) -> ledger claim
(attempt + fence, queued -> running) -> stamp the fence on the reservation. Only then may a worker launch.
A fenced reservation never expires into free capacity: when its attempt lease runs out it becomes uncertain
and stays reserved until exit evidence arrives. The scheduler never signals a process; a spent runtime
budget becomes a cancel intent for the cancellation path to carry out.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
import json
import sqlite3
import time
from typing import Any, Callable, Iterable, Optional

from backend.engine.job_state import IllegalTransition
from backend.engine.job_state import TERMINAL
from backend.engine.job_store import JobRef, JobStore, QuotaExceeded, StaleFencingToken, StaleRevision, UnknownJob
from backend.engine.shared_scheduler import ResourceLeases

_END_EVENTS = {'completed': 'complete', 'failed': 'fail', 'aborted': 'abort', 'interrupted': 'interrupt'}


@dataclass(frozen=True)
class AttemptLease:
    job_id: str
    attempt: int
    fence: int
    expires_at: float
    worker_id: str
    reservation: Optional[dict] = None


class JobScheduler:
    def __init__(self, store: JobStore, leases: ResourceLeases, *, lease_seconds: Optional[float] = None,
                 reservation_timeout: float = 2.0):
        self.store = store
        self.leases = leases
        self.lease_seconds = lease_seconds if lease_seconds is not None else leases.lease_seconds
        self.reservation_timeout = reservation_timeout

    # --- Queue -------------------------------------------------------------------------------------------
    def enqueue(self, job_id: str, expected_revision: int, priority: int = 0, resources: Optional[dict] = None,
                budget: Optional[dict] = None) -> JobRef:
        if resources is not None:
            if not isinstance(resources.get('host'), str) or not resources['host']:
                raise ValueError('A device reservation names its host')
            resources = {'host': resources['host'], 'selector': resources.get('selector', 'all'),
                         'memory_budget_mb': int(resources.get('memory_budget_mb', 0)),
                         'allow_sharing': bool(resources.get('allow_sharing', False))}
        return self.store.enqueue(job_id, expected_revision, priority, resources, budget)

    def set_quota(self, project_key: str, max_running: int) -> None:
        self.store.set_quota(project_key, max_running)

    def set_budget(self, job_id: str, max_runtime_s: Optional[float] = None, max_attempts: int = 1) -> None:
        self.store.set_budget(job_id, {'max_runtime_s': max_runtime_s, 'max_attempts': int(max_attempts)})

    def _ordered(self, rows: Iterable[dict]) -> list[dict]:
        """Priority first, then the project and the actor holding fewer attempts, then queue age."""
        by_project, by_actor = self.store.holding_counts()
        return sorted(rows, key=lambda row: (-row['priority'], by_project.get(row['project_key'], 0),
                                             by_actor.get(row['actor_id'], 0), row['queued_ns'] or 0, row['id']))

    def queue_view(self, project_key: Optional[str] = None) -> list[dict]:
        """One project's waiting jobs in claim order; positions count within that project only."""
        ordered = [row for row in self._ordered(self.store.queued()) if project_key is None or row['project_key'] == project_key]
        rows = []
        for position, row in enumerate(ordered, start=1):
            rows.append({'job_id': row['id'], 'priority': row['priority'], 'position': position,
                         'wait_reason': row['wait_reason'] or 'priority',
                         'budget': json.loads(row['budget_json']) if row['budget_json'] else None})
        return rows

    # --- Claim -------------------------------------------------------------------------------------------
    def _blocked_reason(self, blocking: list[dict]) -> str:
        if any(row.get('uncertain') for row in blocking):
            return 'uncertain_reservation'
        if any(row.get('ledger_job') for row in blocking):
            return 'device_reserved'
        if any(row.get('app_schema') is None and not row.get('remote') for row in blocking):
            return 'legacy_app_active'
        return 'external_reservation'

    def claim_job(self, worker_id: str, capabilities: dict) -> Optional[AttemptLease]:
        """Claim the best eligible queued job for this worker, or None; every job left waiting records why."""
        rows = self.store.queued()
        allowed = capabilities.get('job_ids')
        if allowed is not None:
            rows = [row for row in rows if row['id'] in set(allowed)]
        hosts = set(capabilities.get('hosts') or ())
        by_project, _ = self.store.holding_counts()
        reasons: dict[str, str] = {}
        claimed: Optional[AttemptLease] = None
        for row in self._ordered(rows):
            job_id = row['id']
            if claimed is not None:
                reasons[job_id] = 'priority'
                continue
            limit = self.store.quota(row['project_key'])
            if limit is not None and by_project.get(row['project_key'], 0) >= limit:
                reasons[job_id] = 'project_quota'
                continue
            resources = json.loads(row['resources_json']) if row['resources_json'] else None
            if resources is not None and resources['host'] not in hosts:
                reasons[job_id] = 'capability_mismatch'
                continue
            if resources is not None:
                try:
                    if self.leases.older_app_active(resources['host'], timeout=self.reservation_timeout):
                        reasons[job_id] = 'legacy_app_active'  # drain: never claim beside an older app version
                        continue
                    acquired, blocking = self.leases.acquire_for_job(
                        job_id, resources['host'], resources.get('selector', 'all'),
                        memory_budget_mb=int(resources.get('memory_budget_mb', 0)),
                        allow_sharing=bool(resources.get('allow_sharing', False)), project_id=row['project_id'],
                        account_id=row['actor_id'], timeout=self.reservation_timeout)
                except sqlite3.Error:
                    reasons[job_id] = 'reservation_store_unavailable'  # fail closed: no attempt without a reservation
                    continue
                if not acquired:
                    reasons[job_id] = self._blocked_reason(blocking)
                    continue
            try:
                attempt = self.store.claim(job_id, worker_id, self.lease_seconds)
            except (QuotaExceeded, IllegalTransition, StaleRevision, UnknownJob) as exc:
                if resources is not None:
                    self.leases.release_unfenced(job_id)
                if isinstance(exc, QuotaExceeded):
                    reasons[job_id] = 'project_quota'
                continue
            try:
                stamped = resources is None or self.leases.stamp_fence(job_id, attempt.fencing_token)
            except sqlite3.Error:
                stamped = False
            if not stamped:
                # The reservation lapsed (or was taken) before the fence landed: never hand out a lease without
                # devices. The claim goes back to the queue under its fence and nothing launches.
                self._return_to_queue(job_id, attempt.fencing_token, 'reservation lost before launch')
                self.leases.release_unfenced(job_id)
                reasons[job_id] = 'reservation_lost'
                continue
            claimed = AttemptLease(job_id, attempt.number, attempt.fencing_token, time.time() + self.lease_seconds,
                                   worker_id, resources)
        self.store.set_wait_reasons(reasons)
        return claimed

    def _return_to_queue(self, job_id: str, fence: int, reason: str) -> None:
        ref = self.store.get(job_id)
        if ref.state != 'disconnected':
            ref = self.store.transition(job_id, ref.revision, 'disconnect', {'reason': reason}, fencing_token=fence)
        self.store.transition(job_id, ref.revision, 'requeue', {'reason': reason}, fencing_token=fence)

    # --- Ownership ---------------------------------------------------------------------------------------
    def heartbeat(self, lease: AttemptLease) -> AttemptLease:
        expires_ns = self.store.heartbeat(lease.job_id, lease.fence, self.lease_seconds)
        if lease.reservation is not None:
            self.leases.heartbeat_fenced(lease.job_id, lease.fence)
        return replace(lease, expires_at=expires_ns / 1e9)

    def publish_result(self, lease: AttemptLease, outcome: str, payload: Any = None,
                       publish: Optional[Callable[[], Any]] = None) -> JobRef:
        """End the job and publish under the lease's fence; a stale lease ends and publishes nothing."""
        ref = self.store.finish(lease.job_id, _END_EVENTS[outcome], payload, fencing_token=lease.fence, publish=publish)
        if lease.reservation is not None:
            self.leases.release_fenced(lease.job_id, lease.fence)
        return ref

    def reattach(self, job_id: str, worker_id: str) -> AttemptLease:
        """A restarted owner takes over the running attempt under a new fence (the old owner is fenced off)."""
        fence = self.store.reattach(job_id, worker_id=worker_id, lease_seconds=self.lease_seconds)
        record = self.store.record(job_id)
        resources = json.loads(record['resources_json']) if record.get('resources_json') else None
        if resources is not None:
            self.leases.adopt_fenced(job_id, fence)
        attempt = self.store.attempts(job_id)[-1]['number']
        return AttemptLease(job_id, attempt, fence, time.time() + self.lease_seconds, worker_id, resources)

    def expire_leases(self, observe: Callable[[AttemptLease], str]) -> list[str]:
        """Expired attempt leases: exit evidence ends (or requeues) the job and frees its devices; without
        evidence ('unknown' or 'alive') the job is disconnected and its reservation stays held as uncertain."""
        changed = []
        for row in self.store.expired_attempts():
            resources = json.loads(row['resources_json']) if row['resources_json'] else None
            lease = AttemptLease(row['job_id'], row['number'], row['fencing_token'], row['lease_expires_ns'] / 1e9,
                                 row['worker_id'] or '', resources)
            try:
                evidence = observe(lease)
                if evidence == 'exited':
                    budget = json.loads(row['budget_json']) if row['budget_json'] else {}
                    stopping = row['state'] == 'stopping' or self.store.cancel_intent(lease.job_id) is not None
                    if stopping:
                        self.store.finish(lease.job_id, 'abort', {'reason': 'worker exit confirmed after a stop request'},
                                          fencing_token=lease.fence)
                    elif len(self.store.attempts(lease.job_id)) < int(budget.get('max_attempts') or 1):
                        self._return_to_queue(lease.job_id, lease.fence, 'worker exit confirmed')
                    else:
                        self.store.finish(lease.job_id, 'interrupt', {'reason': 'worker exit confirmed after its lease expired'},
                                          fencing_token=lease.fence)
                    if resources is not None:
                        self.leases.release_fenced(lease.job_id, lease.fence)
                    changed.append(lease.job_id)
                elif row['state'] in ('running', 'stopping'):
                    ref = self.store.get(lease.job_id)
                    self.store.transition(lease.job_id, ref.revision, 'disconnect',
                                          {'reason': 'attempt lease expired without exit evidence'}, fencing_token=lease.fence)
                    if resources is not None:
                        self.leases.mark_uncertain_fenced(lease.job_id, lease.fence)
                    changed.append(lease.job_id)
            except (StaleRevision, IllegalTransition, StaleFencingToken, UnknownJob, sqlite3.Error):
                continue  # another owner moved the job (or the store was busy); the next pass re-reads it
        return changed

    def sweep_orphaned_reservations(self) -> list[str]:
        """Free fenced local reservations whose job this ledger records as ended (interrupted at a restart, aborted
        while being claimed, ...) once nothing refreshes them: a worker that is still alive keeps its row fresh, and
        the row is kept until its refresh stops. A job this ledger does not know may belong to another ledger sharing
        the reservation DB, and an uncertain or remote reservation waits for exit evidence: all are always kept."""
        freed = []
        now = time.time()
        for row in self.leases.list():
            if row.get('fence') is None or row.get('uncertain') or row.get('remote') or (row.get('expires') or 0) > now:
                continue
            try:
                state = self.store.get(row['job_id']).state
            except UnknownJob:
                continue
            if state in TERMINAL:
                self.leases.release_fenced(row['job_id'], row['fence'])
                freed.append(row['job_id'])
        return freed

    def enforce_budgets(self) -> list[str]:
        """A spent runtime budget becomes a durable cancel intent; the scheduler itself signals nothing."""
        now = time.time_ns()
        cancelled = []
        for row in self.store.open_attempts():
            budget = json.loads(row['budget_json']) if row['budget_json'] else {}
            limit = budget.get('max_runtime_s')
            if limit is not None and now - row['started_ns'] > float(limit) * 1e9 and not self.store.cancel_intent(row['job_id']):
                self.store.request_cancel(row['job_id'], 'scheduler', 'runtime budget exceeded')
                cancelled.append(row['job_id'])
        return cancelled
