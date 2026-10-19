"""Read saved split keys in either app format without changing evidence bytes."""
from pathlib import Path


def normalized_assignments(source, assignments):
    """Return safe source-relative keys; reject ambiguous or escaping records."""
    source = Path(source).resolve()
    if not isinstance(assignments, dict):
        raise ValueError('Saved split assignments must be an object')
    normalized = {}
    for name, partition in assignments.items():
        if not isinstance(name, str) or not name.strip() or '\\' in name:
            raise ValueError('Invalid saved split image path')
        path = Path(name)
        if '..' in path.parts or not isinstance(partition, str) or partition not in {'train', 'val', 'test'}:
            raise ValueError('Invalid saved split path or partition')
        image = path if path.is_absolute() else source / path
        resolved = image.resolve()
        if resolved == source or not resolved.is_relative_to(source):
            raise ValueError('Saved split image is outside its source')
        for candidate in (image, *image.parents):
            if candidate == source:
                break
            if candidate.is_symlink():
                raise ValueError('Saved split image path cannot contain symbolic links')
        relative = resolved.relative_to(source).as_posix()
        if relative in normalized and normalized[relative] != partition:
            raise ValueError('Saved split has conflicting partitions for the same image')
        normalized[relative] = partition
    return normalized


# The first layout a beginner has: train/, val/ and test/ folders under the source. It is an explicit held-out split
# the user made by hand, so a flow evaluation can freeze its test folder without a saved split file.
SPLIT_FOLDERS = ('train', 'val', 'test')
_IMAGE_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.bmp', '.tif', '.tiff', '.webp'}


def folder_split_assignments(source):
    """{source-relative image path: partition} for every image under the source's train/val/test folders (empty when
    the source has none). Linked files and macOS resource forks are left out, like the loaders leave them out."""
    source = Path(source)
    assignments = {}
    for part in SPLIT_FOLDERS:
        folder = source / part
        if folder.is_symlink() or not folder.is_dir():
            continue
        for path in sorted(folder.rglob('*')):
            if (path.is_symlink() or not path.is_file() or path.name.startswith('._')
                    or path.suffix.lower() not in _IMAGE_EXTENSIONS):
                continue
            assignments[path.relative_to(source).as_posix()] = part
    return assignments


def folder_split_fingerprint(assignments):
    """A digest of which image belongs to which folder, so a changed test folder is noticed later."""
    import hashlib
    lines = '\n'.join(f'{name}\t{part}' for name, part in sorted(assignments.items()))
    return hashlib.sha256(lines.encode('utf-8')).hexdigest()
