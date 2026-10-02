"""Whether a path still names the file behind an open descriptor.

On Windows ``os.stat(path)`` and ``os.fstat(fd)`` fill ``st_dev`` and ``st_ino`` from different system calls (since
Python 3.12 the path form reports the 64-bit volume serial and the 128-bit file id, the descriptor form the 32-bit
serial and the 64-bit file index), so a path stat compared with a descriptor stat can name a different file for the
very same one. ``stat_by_handle`` opens the path again only to ``fstat`` it, so both sides of a comparison come from
the same call on every platform; nothing is read through that handle.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

# O_NONBLOCK: a FIFO or device swapped in at the path must not block the check (it then simply names another file).
_FLAGS = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NONBLOCK', 0)


def stat_by_handle(path: Path | str) -> Optional[os.stat_result]:
    """``fstat`` of a fresh handle to ``path`` (links are not followed where the platform allows), or None."""
    try:
        descriptor = os.open(path, _FLAGS)
    except OSError:
        return None
    try:
        return os.fstat(descriptor)
    finally:
        os.close(descriptor)


def names_descriptor(path: Path | str, descriptor: int) -> bool:
    """Whether ``path`` currently names the file open as ``descriptor`` (device and file id, compared like for like)."""
    current = stat_by_handle(path)
    opened = os.fstat(descriptor)
    return current is not None and (current.st_dev, current.st_ino) == (opened.st_dev, opened.st_ino)
