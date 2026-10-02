"""Searching a validated dataset revision and resolving saved image selections by identity (S2-07).

``query_images`` pages one immutable revision of the persistent index (S3-01) by keyset on the relative path, with a
case-insensitive file name/path search, the index filters (folder label, split, validity, annotation label) and the
metadata ledger's fields (tags, product, lot, review state, use). Index filters run in SQL; ledger filters run on each
scanned batch, so a page reads at most ``scan_limit`` rows: when a selective filter finds fewer items, the page still
returns a cursor that continues from where the scan stopped (``scanned_to``), never loading the whole list.

``resolve_image_ids`` checks saved selections against a revision. A selection is resolved by its image id and content
digest (its path is never sent or matched): same id and digest is ``found``; same id with other bytes is ``changed``
(the file at that path was replaced); same id but unread in this build is ``unreadable``; an unknown id whose digest is
found elsewhere is ``moved`` with its candidates (a moved source or a renamed file; several candidates are never picked
automatically); otherwise ``missing``.
"""
from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Iterable, Optional
import unicodedata

LEDGER_FILTERS = ('tag', 'product', 'lot', 'workflow_state', 'usage_state')
STATES = ('valid', 'invalid')
_MAX_LIMIT = 200
_MAX_CURSOR = 8192
_COLUMNS = ('relative_path', 'image_uuid', 'sha256', 'size', 'width', 'height', 'label', 'split', 'valid', 'error_code',
            'error_detail', 'via_link')


def _connect(index_file: Path | str) -> sqlite3.Connection:
    connection = sqlite3.connect(f'file:{Path(index_file).as_posix()}?mode=ro', uri=True, timeout=30)
    connection.row_factory = sqlite3.Row
    return connection


def _escape_like(text: str) -> str:
    return text.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')


_LEDGER_CACHE: dict = {}
_LEDGER_CACHE_SIZE = 4


def load_ledger_rows(ledger_file: Path | str | None) -> dict:
    """{relative_path: ledger row} from the metadata ledger (never written here); {} when it does not exist. A parsed
    ledger is reused while its size and modification time are unchanged (the ledger is replaced atomically)."""
    if ledger_file is None:
        return {}
    path = Path(ledger_file)
    try:
        stat = path.stat()
    except FileNotFoundError:
        return {}
    # the inode changes on every atomic replacement, so a same-size rewrite on a coarse-clock volume is still seen
    key, stamp = str(path.resolve()), (stat.st_ino, stat.st_size, stat.st_mtime_ns)
    cached = _LEDGER_CACHE.get(key)
    if cached is not None and cached[0] == stamp:
        return cached[1]
    data = json.loads(path.read_text(encoding='utf-8'))
    images = data.get('images') if isinstance(data, dict) else None
    rows = images if isinstance(images, dict) else {}
    if len(_LEDGER_CACHE) >= _LEDGER_CACHE_SIZE and key not in _LEDGER_CACHE:
        _LEDGER_CACHE.pop(next(iter(_LEDGER_CACHE)))
    _LEDGER_CACHE[key] = (stamp, rows)
    return rows


def _ledger_match(row: Optional[dict], filters: dict) -> bool:
    """Whether a ledger row matches; an image without a row has the ledger's defaults (unworked, active, no tags)."""
    row = row or {}
    for name in LEDGER_FILTERS:
        wanted = filters.get(name)
        if wanted is None:
            continue
        if name == 'tag':
            matched = wanted in (row.get('tags') or [])
        elif name == 'workflow_state':
            matched = row.get(name, 'unworked') == wanted
        elif name == 'usage_state':
            matched = row.get(name, 'active') == wanted
        else:
            matched = row.get(name) == wanted
        if not matched:
            return False
    return True


def _item(row: sqlite3.Row, ledger: Optional[dict]) -> dict:
    item = {column: row[column] for column in _COLUMNS}
    item['valid'] = bool(item['valid'])
    item['file_name'] = Path(item['relative_path']).name
    item['annotation_labels'] = json.loads(row['labels']) if row['labels'] else []
    item['annotation_error'] = row['annotation_error']
    item['tags'] = list((ledger or {}).get('tags') or [])
    for name in ('product', 'lot'):
        item[name] = (ledger or {}).get(name)
    item['workflow_state'] = (ledger or {}).get('workflow_state', 'unworked')
    item['usage_state'] = (ledger or {}).get('usage_state', 'active')
    return item


def query_images(index_file: Path | str, project_key: str, revision_id: str, *, ledger: Optional[dict] = None,
                 query: Optional[str] = None, filters: Optional[dict] = None, cursor: Optional[str] = None,
                 limit: int = 100, scan_limit: int = 5000) -> dict:
    """One page of a revision's images in relative-path order; the cursor is bound to the revision, query and filters."""
    filters = {name: value for name, value in (filters or {}).items() if value not in (None, '')}
    unknown = set(filters) - {'label', 'split', 'state', 'annotation_label', *LEDGER_FILTERS}
    if unknown:
        raise ValueError(f'Unknown filters: {sorted(unknown)}')
    if filters.get('state') not in (None, *STATES):
        raise ValueError(f'state must be one of {STATES}')
    limit = max(1, min(int(limit), _MAX_LIMIT))
    query = (query or '').strip()
    binding = hashlib.sha256(json.dumps([project_key, revision_id, query, filters], sort_keys=True,
                                        ensure_ascii=False).encode()).hexdigest()[:16]
    after = ''
    if cursor:
        if len(cursor) > _MAX_CURSOR:
            raise ValueError('Invalid cursor')
        try:
            bound, after = json.loads(base64.urlsafe_b64decode(cursor.encode()).decode())
        except (ValueError, TypeError) as exc:
            raise ValueError('Invalid cursor') from exc
        if bound != binding or not isinstance(after, str):
            raise ValueError('The cursor belongs to another revision, search or filter')
    clauses, params = ['i.revision_id=?'], [revision_id]
    for column, name in (('i.label', 'label'), ('i.split', 'split')):
        if name in filters:
            clauses.append(f'{column}=?')
            params.append(filters[name])
    if 'state' in filters:
        clauses.append('i.valid=?')
        params.append(int(filters['state'] == 'valid'))
    if 'annotation_label' in filters:
        clauses.append('EXISTS (SELECT 1 FROM json_each(a.labels) WHERE json_each.value=?)')
        params.append(filters['annotation_label'])
    if query:
        # File names created on macOS often store Hangul decomposed (NFD) while people type it composed (NFC).
        forms = sorted({unicodedata.normalize('NFC', query), unicodedata.normalize('NFD', query)})
        clauses.append('(' + ' OR '.join("i.relative_path LIKE ? ESCAPE '\\'" for _ in forms) + ')')
        params.extend(f'%{_escape_like(form)}%' for form in forms)
    sql = ('SELECT i.*, a.labels AS labels, a.error AS annotation_error FROM dataset_index_images i '
           'LEFT JOIN dataset_index_annotations a ON a.revision_id = i.revision_id AND a.relative_path = i.relative_path '
           f'WHERE {" AND ".join(clauses)} AND i.relative_path > ? ORDER BY i.relative_path LIMIT ?')
    ledger = ledger or {}
    items, scanned, position, exhausted = [], 0, after, False
    with _connect(index_file) as db:
        if db.execute('SELECT 1 FROM dataset_revisions WHERE revision_id=? AND project_key=?',
                      (revision_id, project_key)).fetchone() is None:
            raise KeyError(revision_id)
        while len(items) <= limit and scanned < scan_limit:
            batch = db.execute(sql, (*params, position, min(500, scan_limit - scanned))).fetchall()
            if not batch:
                exhausted = True
                break
            for row in batch:
                scanned += 1
                row_ledger = ledger.get(row['relative_path'])
                if _ledger_match(row_ledger, filters):
                    items.append(_item(row, row_ledger))
                    if len(items) > limit:
                        break
                position = row['relative_path']
            if len(items) > limit:
                break
    more = len(items) > limit
    items = items[:limit]
    if more:
        position = items[-1]['relative_path']
    token = None if exhausted and not more else base64.urlsafe_b64encode(json.dumps([binding, position]).encode()).decode()
    return {'revision_id': revision_id, 'items': items, 'next_cursor': token, 'scanned': scanned,
            'scanned_to': position or None, 'complete_page': len(items) == limit or exhausted}


_ROW = 'SELECT relative_path, image_uuid, sha256, valid FROM dataset_index_images WHERE revision_id=?'
_CHUNK = 500  # well below SQLite's bound-parameter limit


def _chunks(values: list) -> Iterable[list]:
    for start in range(0, len(values), _CHUNK):
        yield values[start:start + _CHUNK]


def resolve_image_ids(index_file: Path | str, project_key: str, revision_id: str, selections: Iterable[dict]) -> list:
    """The state of each saved selection ({image_uuid, sha256}) in this revision, in input order: found, changed (other
    bytes under that id), unreadable (the image is there but this build could not read it: kept, not dropped), moved
    (the id is gone but the bytes are elsewhere; candidates listed) or missing. Ids and digests are looked up in
    batches through indexes, so the cost grows with the selections, not with the revision."""
    selections = list(selections)
    with _connect(index_file) as db:
        if db.execute('SELECT 1 FROM dataset_revisions WHERE revision_id=? AND project_key=?',
                      (revision_id, project_key)).fetchone() is None:
            raise KeyError(revision_id)
        by_uuid: dict = {}
        for chunk in _chunks(sorted({s['image_uuid'] for s in selections if s.get('image_uuid')})):
            for row in db.execute(f'{_ROW} AND image_uuid IN ({",".join("?" * len(chunk))})', (revision_id, *chunk)):
                by_uuid[row['image_uuid']] = dict(row)
        wanted = sorted({s['sha256'] for s in selections if s.get('sha256') and s.get('image_uuid') not in by_uuid})
        by_digest: dict = {}
        for chunk in _chunks(wanted):
            for row in db.execute(f'{_ROW} AND sha256 IN ({",".join("?" * len(chunk))}) ORDER BY relative_path', (revision_id, *chunk)):
                found = by_digest.setdefault(row['sha256'], [])
                if len(found) < 20:
                    found.append(dict(row))
    results = []
    for selection in selections:
        image_uuid, digest = selection.get('image_uuid'), selection.get('sha256')
        row = by_uuid.get(image_uuid) if image_uuid else None
        if row is not None:
            status = 'unreadable' if row['sha256'] is None else 'found' if digest is None or row['sha256'] == digest else 'changed'
            results.append({**selection, 'status': status, 'current': row, 'candidates': []})
            continue
        candidates = by_digest.get(digest, []) if digest else []
        results.append({**selection, 'status': 'moved' if candidates else 'missing', 'current': None, 'candidates': candidates})
    return results
