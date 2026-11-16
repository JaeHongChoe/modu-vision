"""Geti background work remains visible after the HTTP/UI lifetime ends."""
import time


def test_busy_tracker_is_project_scoped_and_waits_for_worker_exit(tmp_path):
    from backend.platforms.geti.activity import PlatformActivity
    from backend.platforms.geti.bindings import PlatformStore
    store = PlatformStore(tmp_path / 'ledger')
    class Runtime:
        exited = False
        def client_if_running(self, key): return self
        def request(self, method, path, **kwargs): return {'job_id':'child-a','status':'DONE','progress':100,'worker_exited':self.exited}
        def exit_evidence(self, key, generation): return None
    runtime = Runtime()
    tracker = PlatformActivity(runtime, store, poll_interval=0.02)
    root = tmp_path / 'project-a'
    job = {'id':'owned-job','operation_id':'child-op','status':'running','worker_exited':False,'generation':'gen-a','project_root':str(root)}
    try:
        tracker.track('scope-a','actor-a',job)
        tracker.track('scope-a','actor-a',job)
        assert len(tracker.active_work(root)) == 1
        assert tracker.active_work(tmp_path / 'project-b') == []
        # DONE is an artifact event; the OS worker can still be alive.
        time.sleep(0.08)
        assert len(tracker.active_work(root)) == 1
        runtime.exited = True
        deadline = time.monotonic() + 2
        while tracker.active_work(root) and time.monotonic() < deadline: time.sleep(0.02)
        assert tracker.active_work(root) == []
        assert store.get_job('scope-a','owned-job')['worker_exited'] is True
    finally: tracker.shutdown()


def test_generation_exit_proof_settles_only_its_ambiguous_job(tmp_path):
    from backend.platforms.geti.activity import PlatformActivity
    from backend.platforms.geti.bindings import PlatformStore
    store = PlatformStore(tmp_path / 'ledger')
    class Runtime:
        def client_if_running(self, key): return None
        def exit_evidence(self, key, generation):
            return {'generation':'gen-a','worker_exited':True,'finished_at':123,'proof':'owned OS exit'} if generation=='gen-a' else None
    tracker = PlatformActivity(Runtime(), store, poll_interval=0.02)
    try:
        for generation in ('gen-a','gen-b'):
            tracker.track('scope-a','actor-a',{'id':generation,'operation_id':generation,'platform_job_id':'child-'+generation,
                'status':'submission_unconfirmed','worker_exited':False,'generation':generation,'project_root':str(tmp_path)})
        deadline = time.monotonic() + 2
        while store.get_job('scope-a','gen-a')['status']!='interrupted' and time.monotonic()<deadline: time.sleep(0.02)
        job = store.get_job('scope-a','gen-a')
        assert job['status']=='interrupted' and job['worker_exited'] is True
        assert job['submission_evidence']['status']=='submission_unconfirmed'
        assert job['platform_job_id']=='child-gen-a'
        assert [row['id'] for row in tracker.active_work(tmp_path)] == ['gen-b']
    finally: tracker.shutdown()
