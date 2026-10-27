"""Copy selected import inputs into an owned immutable snapshot; never move originals."""
import hashlib
import os
from pathlib import Path
import stat

from .grants import identity, literal_path
from .linked_sources import SnapshotWriter


def _input_identity(info):
    if not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
        raise ValueError('Input must contain regular files')
    if stat.S_ISREG(info.st_mode) and info.st_nlink != 1:
        raise ValueError('Input must contain regular files')
    return (info.st_dev, info.st_ino, stat.S_IFMT(info.st_mode),
            None if stat.S_ISDIR(info.st_mode) else (info.st_size, info.st_mtime_ns))


def _open_absolute(path):
    """Open every absolute-path component without following directory links."""
    descriptor = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for index, part in enumerate(path.parts[1:]):
            flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, 'O_NONBLOCK', 0)
            if index < len(path.parts) - 2:
                flags |= os.O_DIRECTORY
            child = os.open(part, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


class SourceSelection:
    """Pin selected input before consuming its grant and retain it through copying."""
    def __init__(self, path):
        self.path = literal_path(path)
        self.descriptor = None
        if os.name != 'posix' or not getattr(os, 'O_NOFOLLOW', 0) or not getattr(os, 'O_DIRECTORY', 0) or os.open not in os.supports_dir_fd:
            raise ValueError('Import snapshots require qualified POSIX no-follow filesystem operations')
        try:
            self.descriptor = _open_absolute(self.path)
            self.identity = _input_identity(os.fstat(self.descriptor))
            self.is_file = self.identity[2] == stat.S_IFREG
            self.check_current()
        except BaseException:
            self.close()
            raise

    def check_current(self):
        descriptor = _open_absolute(self.path)
        try:
            if _input_identity(os.fstat(descriptor)) != self.identity:
                raise ValueError('Selected input changed during import')
        finally:
            os.close(descriptor)

    def open(self, relative, expected, directories):
        """Resolve children from the pinned root, checking each ancestor identity."""
        self.check_current()
        descriptor = os.dup(self.descriptor)
        try:
            prefix = Path('.')
            for index, part in enumerate(relative.parts):
                prefix /= part
                node = expected if index == len(relative.parts) - 1 else directories[prefix]
                flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, 'O_NONBLOCK', 0)
                if node[2] == stat.S_IFDIR:
                    flags |= os.O_DIRECTORY
                child = os.open(part, flags, dir_fd=descriptor)
                os.close(descriptor)
                descriptor = child
                if _input_identity(os.fstat(descriptor)) != node:
                    raise ValueError('Selected input changed during import')
            if _input_identity(os.fstat(descriptor)) != expected:
                raise ValueError('Selected input changed during import')
            return descriptor
        except BaseException:
            os.close(descriptor)
            raise

    def close(self):
        if self.descriptor is not None:
            os.close(self.descriptor)
            self.descriptor = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


def stage_selection(source, project, purpose, *, protected_roots=(), max_bytes=32 * 1024**3, max_files=100_000, max_nodes=200_000, expected_source_identity=None):
    source, project = literal_path(source), literal_path(project)
    if not project.is_dir(): raise ValueError('Select a project before importing files')
    project_identity = list(identity(project)[:3])
    for protected in protected_roots:
        protected = Path(protected).resolve()
        if source == protected or source.is_relative_to(protected) or source.is_dir() and protected.is_relative_to(source):
            raise ValueError('Private host state cannot be imported')
    if source.is_dir() and project.is_relative_to(source):
        raise ValueError('Select the input folder, not an ancestor of the project')
    if source.is_relative_to(project / '.local-host-imports') or any(part.casefold() == '.local-host-linked-sources' for part in source.parts):
        raise ValueError('Select original input rather than an existing import snapshot')
    if type(max_nodes) is not int or max_nodes < 1:
        raise ValueError('Selected input exceeds the import snapshot path limit')
    with SourceSelection(source) as selected:
        if expected_source_identity is not None and selected.identity != expected_source_identity:
            raise ValueError('Selected input changed during import')
        return _stage_selected(selected, project, purpose, project_identity, max_bytes, max_files, max_nodes)


def _stage_selected(selected, project, purpose, project_identity, max_bytes, max_files, max_nodes):
    # Reserve every queued entry, including empty directories, before sorting
    # or traversing it. File/byte limits alone cannot bound directory metadata.
    pending = [(Path('.'), selected.identity)]
    directories = {Path('.'): selected.identity}
    nodes = 1
    files = []
    total = 0
    while pending:
        relative, expected = pending.pop()
        descriptor = selected.open(relative, expected, directories)
        try:
            if expected[2] == stat.S_IFDIR:
                children = []
                with os.scandir(descriptor) as entries:
                    for child in entries:
                        if nodes >= max_nodes:
                            raise ValueError('Selected input exceeds the import snapshot path limit')
                        nodes += 1
                        child_relative = relative / child.name
                        child_identity = _input_identity(os.stat(child.name, dir_fd=descriptor, follow_symlinks=False))
                        children.append((child_relative, child_identity))
                        if child_identity[2] == stat.S_IFDIR:
                            directories[child_relative] = child_identity
                pending.extend(sorted(children, key=lambda entry: entry[0], reverse=True))
            else:
                total += expected[3][0]
                if total > max_bytes or len(files) >= max_files:
                    raise ValueError('Selected input exceeds the import snapshot limit')
                files.append((relative, expected))
            # A directory may have been replaced after opening its descriptor;
            # verify its live location too before retaining any enumerated tree.
            os.close(selected.open(relative, expected, directories))
        finally:
            os.close(descriptor)
    selected.check_current()
    writer = SnapshotWriter(project, None, project_identity, container='.local-host-imports')
    manifest = []
    copied = 0
    try:
        for relative, expected in files:
            descriptor = selected.open(relative, expected, directories)
            output = None
            try:
                digest = hashlib.sha256()
                destination = Path(selected.path.name) if selected.is_file else relative
                output = writer.output(str(destination))
                while block := os.read(descriptor, 1024 * 1024):
                    copied += len(block)
                    if copied > max_bytes: raise ValueError('Selected input grew beyond the snapshot limit')
                    offset = 0
                    while offset < len(block): offset += os.write(output, block[offset:])
                    digest.update(block)
                if _input_identity(os.fstat(descriptor)) != expected:
                    raise ValueError('Selected input changed during import')
                os.close(selected.open(relative, expected, directories))
                os.fchmod(output, 0o400); os.fsync(output)
                manifest.append({'path': str(destination), 'bytes': expected[3][0], 'sha256': digest.hexdigest()})
            finally:
                if output is not None: os.close(output)
                os.close(descriptor)
        selected.check_current()
        writer.freeze()
        writer.receipt({'schema_version': 1, 'state': 'complete', 'purpose': purpose, 'files': manifest})
        return writer.payload / selected.path.name if selected.is_file else writer.payload
    except BaseException:
        # Retain a failed owned snapshot for inspection/recovery, not an opaque
        # best-effort deletion that might erase the only failure evidence.
        writer.receipt({'schema_version': 1, 'state': 'failed', 'purpose': purpose, 'files': manifest})
        raise
    finally:
        writer.close()
