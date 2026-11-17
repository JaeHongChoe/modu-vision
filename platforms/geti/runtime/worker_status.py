"""A terminal event and an observed OS process exit are separate evidence."""
import threading


class WorkerRegistry:
    def __init__(self):
        self._workers = {}
        self._lock = threading.Lock()

    def register(self, job_id, process):
        with self._lock: self._workers[str(job_id)] = process

    def exited(self, job_id):
        with self._lock:
            process = self._workers.get(str(job_id))
            return process is not None and process.exitcode is not None
