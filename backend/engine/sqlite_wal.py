"""Switching a SQLite store to write-ahead logging when more than one process opens it.

Switching a database file to WAL (a new file, or one created before WAL was used) takes an exclusive lock. When two
openers switch at the same moment, SQLite answers the second with SQLITE_BUSY ('database is locked') at once instead of
calling its busy handler (waiting could deadlock), so a connection timeout does not help. The opener tries again; the
next try finds the file already switched by the first. Standard library only.
"""
from __future__ import annotations

import sqlite3
import time

_SQLITE_BUSY = 5  # the primary result code; extended codes such as SQLITE_BUSY_RECOVERY keep it in their low byte


def _busy(exc: sqlite3.OperationalError) -> bool:
    """SQLITE_BUSY only: 'database table is locked' (SQLITE_LOCKED) and every other error are not retried."""
    code = getattr(exc, 'sqlite_errorcode', None)  # Python 3.11 and later
    if code is not None:
        return code & 0xFF == _SQLITE_BUSY
    return str(exc) == 'database is locked'


def use_wal(connection, timeout: float = 10.0) -> None:
    """Run PRAGMA journal_mode=WAL on ``connection``, retrying a concurrent switch for up to ``timeout`` seconds."""
    deadline = time.monotonic() + timeout
    while True:
        try:
            connection.execute('PRAGMA journal_mode=WAL')
            return
        except sqlite3.OperationalError as exc:
            if not _busy(exc) or time.monotonic() > deadline:
                raise
            time.sleep(0.02)
