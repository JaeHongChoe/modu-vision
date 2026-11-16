"""Project-scoped visibility for owned Geti work beyond an HTTP request.

This is a busy/worker-evidence tracker. It does not qualify Geti's process tree
for the existing managed application update protocol or relax its fail-closed
observer. Ambiguous publication remains busy until a private worker observation
or recorded generation exit proves settlement.
"""
from __future__ import annotations

from pathlib import Path
import threading


class PlatformActivity:
    def __init__(self, runtime, store, *, poll_interval=1.0):
        self.runtime = runtime
        self.store = store
        self.poll_interval = poll_interval
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._thread = None
        self.runtime.activity = self
        if self.store.activity_jobs(): self._start()

    def _start(self):
        with self._lock:
            if self._thread is not None and self._thread.is_alive(): return
            if self._stop.is_set(): return
            self._thread = threading.Thread(target=self._watch, name='geti-owned-job-evidence', daemon=True)
            self._thread.start()

    def track(self, key, actor, record):
        # The same durable job identity cannot create another busy hold or job.
        previous = self.store.get_job(key, record['id'])
        if previous and previous.get('worker_exited'): return
        self.store.save_job(key, actor, record)
        self._start()

    def active_work(self, project_root=None):
        selected = Path(project_root).resolve() if project_root is not None else None
        rows = self.store.activity_jobs()
        result = []
        for row in rows:
            job = row['job']
            root = job.get('project_root')
            if selected is not None and root and Path(root).resolve() != selected: continue
            result.append({**job, 'project_key': row['project_key']})
        return result

    def settle_generation(self, key, proof):
        if not proof.get('worker_exited') or not proof.get('generation'): return
        for row in self.store.activity_jobs():
            job = row['job']
            if row['project_key'] != key or job.get('generation') != proof['generation']: continue
            terminal = job.get('status') in {'done', 'failed', 'cancelled', 'rejected'}
            value = {**job, 'worker_exited': True, 'exit_evidence': proof}
            if not terminal:
                value.update(status='interrupted', finished_at=proof['finished_at'],
                    submission_evidence={name: job.get(name) for name in ('status','operation_id','platform_job_id','message')},
                    message='Owned runtime workers exited; original submission was preserved and was not retried.')
            self.store.save_job(key, row['actor'], value)

    def _watch(self):
        while not self._stop.is_set():
            for row in self.store.activity_jobs():
                if self._stop.is_set(): return
                key, actor, job = row['project_key'], row['actor'], row['job']
                try:
                    generation = job.get('generation')
                    proof = self.runtime.exit_evidence(key, generation) if generation else None
                    if proof:
                        self.settle_generation(key, proof)
                        continue
                    client = self.runtime.client_if_running(key)
                    if client is None or not job.get('operation_id'): continue
                    raw = client.request('GET', '/modu/jobs/' + job['operation_id'], timeout=3)
                    # A terminal artifact event alone cannot settle the worker.
                    if raw.get('worker_exited') is not True: continue
                    current = self.store.get_job(key, job['id']) or job
                    if current.get('status') == 'interrupted': continue
                    model = (raw.get('metadata') or {}).get('model') or {}
                    value = {**current, 'worker_exited': True, 'status': str(raw.get('status',current['status'])).lower(),
                             'progress':raw.get('progress',current.get('progress',0)), 'message':raw.get('message'),
                             'model_id':model.get('id') or current.get('model_id'), 'finished_at':raw.get('finished_at')}
                    self.store.save_job(key, actor, value)
                except Exception:
                    # No automatic restart, cancellation or submission on a
                    # polling failure. Preserve busy/ambiguous evidence.
                    continue
            self._stop.wait(self.poll_interval)

    def shutdown(self):
        # The runtime owner stops/proves its children first. Unproved durable
        # records remain busy on the next parent, even when this watcher exits.
        self._stop.set()
        with self._lock: thread = self._thread
        if thread is not None and thread is not threading.current_thread(): thread.join(timeout=4)
