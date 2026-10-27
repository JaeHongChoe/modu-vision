"""Copy selected import inputs into an owned immutable snapshot; never move originals."""
import hashlib
import json
import os
from pathlib import Path
import stat
import uuid

from .grants import identity, literal_path


def stage_selection(source, project, purpose, *, protected_roots=(), max_bytes=32 * 1024**3, max_files=100_000):
    source, project = literal_path(source), literal_path(project)
    if not project.is_dir(): raise ValueError('Select a project before importing files')
    for protected in protected_roots:
        protected = Path(protected).resolve()
        if source == protected or source.is_relative_to(protected) or source.is_dir() and protected.is_relative_to(source):
            raise ValueError('Private host state cannot be imported')
    if source.is_dir() and project.is_relative_to(source):
        raise ValueError('Select the input folder, not an ancestor of the project')
    if source.is_relative_to(project / '.local-host-imports') or '.local-host-linked-sources' in source.parts:
        raise ValueError('Select original input rather than an existing import snapshot')
    entries = [source] if source.is_file() else sorted(source.rglob('*'))
    files = []
    total = 0
    for entry in entries:
        canonical = literal_path(entry)
        if canonical != entry.resolve() or entry.is_symlink(): raise ValueError('Linked input is not allowed')
        info = entry.lstat()
        if stat.S_ISDIR(info.st_mode): continue
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1: raise ValueError('Input must contain regular files')
        total += info.st_size
        if total > max_bytes or len(files) >= max_files: raise ValueError('Selected input exceeds the import snapshot limit')
        relative = Path(entry.name) if source.is_file() else entry.relative_to(source)
        files.append((entry, relative, identity(entry)))
    parent = project / '.local-host-imports'
    literal_path(parent)
    parent.mkdir(mode=0o700, exist_ok=True)
    destination = parent / uuid.uuid4().hex
    destination.mkdir(mode=0o700)
    payload = destination / 'payload'; payload.mkdir(mode=0o700)
    receipt = destination / 'snapshot.json'
    manifest = []
    copied = 0
    try:
        for entry, relative, expected in files:
            target = payload / relative; target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            descriptor = os.open(entry, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
            try:
                info = os.fstat(descriptor)
                observed = (info.st_dev, info.st_ino, stat.S_IFMT(info.st_mode), (info.st_size, info.st_mtime_ns))
                if observed != expected or not stat.S_ISREG(info.st_mode): raise ValueError('Selected input changed during import')
                digest = hashlib.sha256()
                with os.fdopen(descriptor, 'rb', closefd=False) as reader, target.open('xb') as writer:
                    while block := reader.read(1024 * 1024):
                        copied += len(block)
                        if copied > max_bytes: raise ValueError('Selected input grew beyond the snapshot limit')
                        writer.write(block); digest.update(block)
                    writer.flush(); os.fsync(writer.fileno())
                if identity(entry) != expected: raise ValueError('Selected input changed during import')
                target.chmod(0o400)
                manifest.append({'path': str(relative), 'bytes': info.st_size, 'sha256': digest.hexdigest()})
            finally: os.close(descriptor)
        receipt.write_text(json.dumps({'schema_version': 1, 'state': 'complete', 'purpose': purpose, 'files': manifest}, indent=2))
        receipt.chmod(0o600)
        for directory in sorted((x for x in payload.rglob('*') if x.is_dir()), key=lambda x: len(x.parts), reverse=True): directory.chmod(0o500)
        payload.chmod(0o500)
        return payload / source.name if source.is_file() else payload
    except BaseException:
        # Retain a failed owned snapshot for inspection/recovery, not an opaque
        # best-effort deletion that might erase the only failure evidence.
        receipt.write_text(json.dumps({'schema_version': 1, 'state': 'failed', 'purpose': purpose, 'files': manifest}))
        receipt.chmod(0o600)
        raise
