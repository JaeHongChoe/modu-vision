"""Settled file admission uses the same bounded durable store as HTTP."""
from pathlib import Path
import os
import time
from PIL import Image


class FolderInputAdapter:
    def __init__(self, root: Path, *, settle_seconds: float = 1.0,
                 max_scan_entries: int = 256):
        if type(max_scan_entries) is not int or not 1 <= max_scan_entries <= 65536:
            raise ValueError('Folder scan budget must be 1..65536 entries')
        if settle_seconds < 0: raise ValueError('Folder settlement cannot be negative')
        self.root = Path(root).resolve()
        self.settle_seconds = settle_seconds
        self.max_scan_entries = max_scan_entries
        self.observed = {}
        self._walk = None

    def _entries(self):
        # Keep only one native directory iterator per depth. Path.rglob can
        # materialize every entry of a wide directory before yielding files.
        stack = [os.scandir(self.root)]
        try:
            while stack:
                try: entry = next(stack[-1])
                except StopIteration:
                    stack.pop().close()
                    continue
                if entry.is_dir(follow_symlinks=False):
                    yield None  # Directory traversal consumes the same budget.
                    stack.append(os.scandir(entry.path))
                else: yield Path(entry.path)
        finally:
            for directory in stack: directory.close()

    def scan(self, store):
        from backend.engine.inspection_service import INBOX_EXTENSIONS, InboxFull
        if not self.root.is_dir():
            if self._walk is not None: self._walk.close()
            self._walk = None
            self.observed.clear()
            return 'disconnected'
        now = time.monotonic()
        checking = False
        budget = self.max_scan_entries
        if self._walk is None and not self.observed: self._walk = self._entries()
        try:
            for path, prior in list(self.observed.items()):
                budget -= 1
                if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(self.root):
                    self.observed.pop(path, None)
                    continue
                stat = path.stat()
                signature = (stat.st_size, stat.st_mtime_ns)
                if prior[0] != signature:
                    # Defer a continuously changing window to the next traversal
                    # after a bounded hold; stable files farther down still run.
                    if now - prior[2] >= max(1.0, 4*self.settle_seconds):
                        self.observed.pop(path, None)
                    else: self.observed[path] = (signature, now, prior[2])
                    checking = True
                    continue
                if now - prior[1] < self.settle_seconds:
                    checking = True
                    continue
                try:
                    # A settled corrupt input is committed once as REVIEW evidence.
                    error = None
                    try:
                        with Image.open(path) as image: image.verify()
                    except (OSError,ValueError) as exc: error = f'Invalid settled image: {exc}'
                    job = store.enqueue(path,'inbox')
                    if error: store.reject_queued(job,error,'CORRUPT_INPUT')
                    self.observed.pop(path, None)
                except InboxFull: return 'backpressure'
                except (OSError,ValueError): checking = True
            while self._walk is not None and budget > 0 and len(self.observed) < self.max_scan_entries:
                budget -= 1
                try: path = next(self._walk)
                except StopIteration:
                    self._walk = None
                    break
                if path is None or path.suffix.lower() not in INBOX_EXTENSIONS or path.is_symlink() or not path.is_file(): continue
                if not path.resolve().is_relative_to(self.root): continue
                stat = path.stat()
                self.observed[path] = ((stat.st_size, stat.st_mtime_ns), now, now)
                checking = True
        except OSError:
            if self._walk is not None: self._walk.close()
            self._walk = None
            return 'disconnected'
        return 'checking' if checking or self.observed or self._walk is not None else 'connected'
