"""Keep images that cannot be decoded out of the inventory, splits and training without touching the source files.

The import validation decodes every image; this records the ones that fail in the project's usage ledger as
``not_used`` with ``usage_reason: decode_error`` (the same ledger the loaders, saved split readers and remote preparation
already honour through ``unused_image_paths``). The image file itself is never moved, rewritten or deleted. When a file
that was excluded for this reason decodes again (the user replaced it), the next import makes it active again. An image
the user set to ``not_used`` by hand is never touched here.
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterable

REASON = 'decode_error'


def exclude_undecodable(project_root: Path, dataset_root: Path, corrupted: Iterable[dict]) -> dict:
    """Mark undecodable images ``not_used`` and restore earlier automatic exclusions that decode now.

    ``corrupted`` is the import validation's list (``file_path``, ``error_code``, ``details``). Returns the relative paths
    excluded by this call or already excluded for this reason, and those restored to active.
    """
    from backend.engine.dataset_loaders import decode_image_file
    from backend.engine.dataset_metadata import metadata_transaction, _ensure, _event

    source = Path(dataset_root).resolve()
    failed = {}
    for row in corrupted:
        path = Path(row['file_path']).resolve()
        if path.is_relative_to(source) and path.is_file():
            failed[path] = row
    excluded, restored = [], []
    if not failed and not _has_automatic_exclusions(project_root, source):
        return {'excluded': [], 'restored': []}  # nothing to record or restore: never create or rewrite the ledger
    with metadata_transaction(project_root, source) as ledger:
        for path, problem in sorted(failed.items()):
            entry = _ensure(ledger, project_root, source, path)
            if entry.get('usage_state') == 'not_used' and entry.get('usage_reason') != REASON:
                excluded.append(entry['relative_path'])  # the user's own exclusion already keeps it out
                continue
            if entry.get('usage_state') != 'not_used':
                entry['usage_state'] = 'not_used'
                entry['usage_reason'] = REASON
                _event(entry, 'system', 'excluded_undecodable',
                       {'usage_state': 'not_used', 'usage_reason': REASON, 'error_code': problem.get('error_code')})
            excluded.append(entry['relative_path'])
        for relative, entry in list(ledger['images'].items()):
            if entry.get('usage_reason') != REASON or entry.get('usage_state') != 'not_used':
                continue
            path = (source / relative).resolve()
            if path in failed or not path.is_file():
                continue
            if decode_image_file(path).valid:
                entry['usage_state'] = 'active'
                entry.pop('usage_reason', None)
                _event(entry, 'system', 'restored_decodable', {'usage_state': 'active'})
                restored.append(relative)
    return {'excluded': sorted(set(excluded)), 'restored': sorted(restored)}


def _has_automatic_exclusions(project_root: Path, source: Path) -> bool:
    """Whether the ledger already holds an exclusion made for this reason (read only)."""
    import json
    from backend.engine.dataset_metadata import ledger_path

    path = ledger_path(project_root, source)
    if not path.is_file() or path.is_symlink():
        return False
    try:
        rows = json.loads(path.read_text(encoding='utf-8')).get('images', {})
    except (OSError, ValueError):
        return False
    return isinstance(rows, dict) and any(isinstance(row, dict) and row.get('usage_reason') == REASON for row in rows.values())
