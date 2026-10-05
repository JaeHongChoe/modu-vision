"""Cooperative runtime limits for the native, in-process family trainers.

Arm only after compute admission. The watchdog requests cancellation; it never
terminates a thread or releases a lease while its trainer is still using it.
"""
from __future__ import annotations
import math
import threading
import time
from typing import Callable


class RuntimeBudget:
    def __init__(self, limit: float | None, cancel: threading.Event,
                 started: Callable[[float], None], expired: Callable[[], None]):
        if limit is not None and (isinstance(limit, bool) or not isinstance(limit, (float, int))
                                  or not math.isfinite(limit) or not 0 < limit <= 604800):
            raise ValueError('Training runtime limit must be finite, positive and at most 7 days')
        self.limit = limit
        self.cancel = cancel
        self._started = started
        self._expired = expired
        self._done = threading.Event()
        self._lock = threading.Lock()
        self._deadline = None
        self._thread = None
        self._expiry_finished = threading.Event()
        self.spent = False
        self._error = None

    def __enter__(self):
        self._deadline = time.monotonic() + self.limit if self.limit is not None else None
        # Refuse to enter the trainer when its start/budget cannot be journaled.
        self._started(time.time())
        if self._deadline is not None:
            def watch():
                if not self._done.wait(max(0, self._deadline - time.monotonic())):
                    self._expire(only_if_active=True)
            self._thread = threading.Thread(target=watch, daemon=True, name='FamilyRuntimeBudget')
            self._thread.start()
        try:
            self.check()
        except BaseException:
            self.__exit__()
            raise
        return self

    def _expire(self, *, only_if_active=False):
        with self._lock:
            if self.spent or (only_if_active and self._done.is_set()):
                return
            self.spent = True
            self.cancel.set()
        try:
            self._expired()
        except (ValueError, OSError, RuntimeError) as exc:
            # Cancellation still reaches the trainer if its journal is unavailable.
            self._error = exc
        finally:
            self._expiry_finished.set()

    def check(self):
        if self._deadline is not None and time.monotonic() >= self._deadline:
            self._expire()
        # The watchdog sets cancel before its journal callback finishes so the
        # owned trainer can stop promptly. Publish its outcome only after that
        # callback succeeds/fails; otherwise a copy of terminal status can omit
        # its acknowledgement or conceal a journal failure.
        if self.spent and not self._expiry_finished.wait(1):
            raise OSError('Training runtime cancellation journal did not finish')
        if self._error is not None:
            raise OSError('Training runtime cancellation journal failed') from self._error
        if self.spent:
            raise InterruptedError('Training runtime limit exceeded')
        if self.cancel.is_set():
            raise InterruptedError('Training cancelled')

    def seal(self):
        """Fence successful publication against a just-expired deadline."""
        with self._lock:
            self._done.set()
        self.check()

    def __exit__(self, *args):
        self._done.set()
        if self._thread is not None:
            self._thread.join(timeout=1)
