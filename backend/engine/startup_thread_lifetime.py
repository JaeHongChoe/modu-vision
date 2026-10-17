"""Original cooperative startup Thread/Event lifetimes; no process/tree authority."""
from __future__ import annotations

import logging
import math
import os
import threading
import time


class StartupThreadError(ValueError):
    pass


class OriginalStartupThread:
    """Capture the created thread before start, stop it, and prove its join.

    The returned local receipt covers only this original thread. It never
    repairs a launch epoch, stops another process, or grants release authority.
    """
    def __init__(self, role, target, *, stop_event=None, name=None):
        if role not in {'job_ledger_maintenance', 'preflight_run_sweep'} or not callable(target):
            raise StartupThreadError('Unknown original startup role or target')
        stop = threading.Event() if stop_event is None else stop_event
        if type(stop) is not threading.Event:
            raise StartupThreadError('Original startup stop Event is required')
        self._pid = os.getpid()
        self._role = role
        self._stop = stop
        self._started = False
        self._failure = None
        def original():
            try:
                target(stop)
            except BaseException as error:
                self._failure = error
                logging.getLogger(__name__).exception('Original startup thread failed: %s', role)
        self._thread = threading.Thread(target=original, name=name or role, daemon=True)
        self._binding = (self._pid, self._role, self._thread, self._stop)

    @property
    def thread(self):
        return self._thread

    @property
    def stop_event(self):
        return self._stop

    def start(self):
        if ((self._pid, self._role, self._thread, self._stop) != self._binding
                or os.getpid() != self._pid or self._started):
            raise StartupThreadError('Original startup thread start differs or replayed')
        self._started = True
        self._thread.start()

    def stop_and_join(self, absolute_deadline, *, expected_thread, expected_stop=None):
        if ((self._pid, self._role, self._thread, self._stop) != self._binding
                or os.getpid() != self._pid or not self._started or expected_thread is not self._thread
                or expected_stop is not None and expected_stop is not self._stop
                or type(self._thread) is not threading.Thread or self._thread is threading.current_thread()):
            raise StartupThreadError('Original startup thread handle or stop Event changed')
        if (type(absolute_deadline) not in (int, float) or not math.isfinite(absolute_deadline)
                or time.monotonic() >= absolute_deadline):
            raise StartupThreadError('Original startup join deadline expired')
        thread, stop = self._thread, self._stop
        stop.set()
        thread.join(max(0.0, absolute_deadline-time.monotonic()))
        if ((self._pid, self._role, self._thread, self._stop) != self._binding
                or self._thread is not thread or self._stop is not stop):
            raise StartupThreadError('Original startup thread handle or stop Event changed during join')
        if thread.is_alive() or time.monotonic() >= absolute_deadline:
            raise StartupThreadError('Original startup thread remains alive or join deadline expired')
        if self._failure is not None:
            raise StartupThreadError('Original startup thread failed') from self._failure
        return {'role': self._role, 'original_thread_joined': True,
                'whole_writer_coverage': False, 'process_tree_exit_verified': False,
                'can_release_launch_lease': False}
