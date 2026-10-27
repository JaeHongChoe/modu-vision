"""Copy selected import inputs into an owned immutable snapshot; never move originals."""
import hashlib
import os
from pathlib import Path
import stat

from .grants import identity, literal_path
from .linked_sources import SnapshotWriter


def stage_selection(source, project, purpose, *, protected_roots=(), max_bytes=32 * 1024**3, max_files=100_000, max_nodes=200_000):
    source, project = literal_path(source), literal_path(project)
    if not project.is_dir(): raise ValueError('Select a project before importing files')
    project_identity=list(identity(project)[:3])
    for protected in protected_roots:
        protected = Path(protected).resolve()
        if source == protected or source.is_relative_to(protected) or source.is_dir() and protected.is_relative_to(source):
            raise ValueError('Private host state cannot be imported')
    if source.is_dir() and project.is_relative_to(source):
        raise ValueError('Select the input folder, not an ancestor of the project')
    if source.is_relative_to(project / '.local-host-imports') or any(part.casefold()=='.local-host-linked-sources' for part in source.parts):
        raise ValueError('Select original input rather than an existing import snapshot')
    if type(max_nodes) is not int or max_nodes < 1:
        raise ValueError('Selected input exceeds the import snapshot path limit')
    # Reserve every queued entry, including empty directories, before sorting
    # or traversing it. File/byte limits alone cannot bound directory metadata.
    pending = [source]
    nodes = 1
    source_is_file = source.is_file()
    files = []
    total = 0
    while pending:
        entry = pending.pop()
        canonical = literal_path(entry)
        if canonical != entry.resolve() or entry.is_symlink(): raise ValueError('Linked input is not allowed')
        info = entry.lstat()
        if stat.S_ISDIR(info.st_mode):
            children = []
            with os.scandir(entry) as entries:
                for child in entries:
                    if nodes >= max_nodes:
                        raise ValueError('Selected input exceeds the import snapshot path limit')
                    nodes += 1
                    children.append(Path(child.path))
            pending.extend(sorted(children, reverse=True))
            continue
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1: raise ValueError('Input must contain regular files')
        total += info.st_size
        if total > max_bytes or len(files) >= max_files: raise ValueError('Selected input exceeds the import snapshot limit')
        relative = Path(entry.name) if source_is_file else entry.relative_to(source)
        files.append((entry, relative, identity(entry)))
    writer = SnapshotWriter(project, None, project_identity, container='.local-host-imports')
    manifest = []
    copied = 0
    try:
        for entry, relative, expected in files:
            descriptor = os.open(entry, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
            output = None
            try:
                info = os.fstat(descriptor)
                observed = (info.st_dev, info.st_ino, stat.S_IFMT(info.st_mode), (info.st_size, info.st_mtime_ns))
                if observed != expected or not stat.S_ISREG(info.st_mode) or info.st_nlink!=1: raise ValueError('Selected input changed during import')
                digest = hashlib.sha256()
                output=writer.output(str(relative))
                while block := os.read(descriptor, 1024 * 1024):
                    copied += len(block)
                    if copied > max_bytes: raise ValueError('Selected input grew beyond the snapshot limit')
                    offset=0
                    while offset<len(block):offset+=os.write(output,block[offset:])
                    digest.update(block)
                if identity(entry) != expected: raise ValueError('Selected input changed during import')
                os.fchmod(output,0o400);os.fsync(output)
                manifest.append({'path': str(relative), 'bytes': info.st_size, 'sha256': digest.hexdigest()})
            finally:
                if output is not None:os.close(output)
                os.close(descriptor)
        writer.freeze()
        writer.receipt({'schema_version': 1, 'state': 'complete', 'purpose': purpose, 'files': manifest})
        return writer.payload / source.name if source_is_file else writer.payload
    except BaseException:
        # Retain a failed owned snapshot for inspection/recovery, not an opaque
        # best-effort deletion that might erase the only failure evidence.
        writer.receipt({'schema_version': 1, 'state': 'failed', 'purpose': purpose, 'files': manifest})
        raise
    finally:writer.close()
