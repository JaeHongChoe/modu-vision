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
