"""Explicit offline binding of ended owned history; no worker/lease adoption.

The existing legacy importer retains unbound provenance. This converter attaches
only its exact terminal rows to an already registered owned local project. The
original actor remains in the audit receipt; the target actor is the installation's
local owner, never a guessed team member. Source journals are never rewritten,
copied into a recovery index or used as launch authorization.
"""
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import time
import uuid

from backend.engine import global_migration as migration
from backend.engine.global_store_paths import active_generation, resolve_store_path, store_admission
from backend.engine.job_state import from_legacy
from backend.engine.job_store import JobStore, spec_digest

_CONTEXT = ('workspace_id', 'project_key', 'project_id', 'actor_id', 'mode')
_PREFIX = 'historical-binding:'


def _owned_path(root, value):
    if not isinstance(value, str):raise ValueError('Historical path is missing')
    path = Path(value)
    resolved = path.resolve()
    if (not path.is_absolute() or not path.exists() or not path.is_relative_to(root)
            or not resolved.is_relative_to(root)
            or any(p.is_symlink() for p in (path, *path.parents))):
        raise ValueError('Historical source/output must remain in the unlinked owned installation')
    return resolved


def _view(root, owner):
    if active_generation(root):raise ValueError('Bind historical records before generation cutover')
    try:
        snapshot = migration._snapshot(root)
    except (ValueError, OSError) as exc:
        record = {'schema_version': 1, 'installation_id': owner['installation_id'], 'rows': [],
            'can_apply': False, 'blockers': [str(exc)], 'source_sha256': None}
        record['preview_sha256'] = migration.digest(record)
        return record
    blockers = list(snapshot['blockers'])
    schemas = migration._known_schemas(owner['scopes'])
    for name, schema in schemas.items():
        if migration._schema(root / owner['scopes'][name]) != schema:
            blockers.append('Unsupported ' + name + ' control schema')
    with migration._read_db(root / owner['scopes']['context']) as db:
        identities = dict(db.execute('SELECT name,value FROM identities'))
        locations = list(db.execute('SELECT scope_key,workspace_id,project_id,path FROM project_locations'))
    with migration._read_db(root / owner['scopes']['ledger']) as db:
        db.row_factory = sqlite3.Row
        history = [dict(row) for row in db.execute("SELECT * FROM jobs WHERE source='legacy_migration' AND workspace_id='legacy' ORDER BY id")]
    if len(history) > 1000:raise ValueError('Bind at most 1000 terminal records in one reviewed conversion')
    rows = []
    for row in history:
        try:
            if (row['state'] not in {'completed', 'failed', 'aborted'} or row['mode'] != 'local' or row['project_id'] != 'legacy'
                    or row['actor_id'] != 'legacy' or row['idempotency_key'] is not None or row['operation_json'] is not None):
                raise ValueError('Only unchanged ended locally owned legacy history can be bound')
            if not re.fullmatch(r'job_[A-Za-z0-9_-]{1,123}', row['id']):raise ValueError('Historical job identity is invalid')
            spec = json.loads(row['spec_json'])
            if set(spec) != {'legacy_source', 'legacy_sha256'} or spec_digest(spec) != row['spec_sha256']:
                raise ValueError('Historical source specification changed')
            source = _owned_path(root, spec['legacy_source'])
            raw = source.read_bytes()
            if hashlib.sha256(raw).hexdigest() != spec['legacy_sha256']:
                raise ValueError('Historical origin source changed')
            journal = json.loads(raw)
            if source.is_relative_to(root / owner['scopes']['local_journals']):
                from backend.engine.terminal_runtime_history import validate_local
                if source.parent != root / owner['scopes']['local_journals']:
                    raise ValueError('Nested local recovery indexes are unsupported')
                validate_local(root, owner['scopes'], source, journal)
            elif source.is_relative_to(root / owner['scopes']['remote_journals']):
                from backend.engine.terminal_runtime_history import validate_remote
                if source.parent != root / owner['scopes']['remote_journals']:
                    raise ValueError('Nested remote recovery indexes are unsupported')
                validate_remote(root, owner['scopes'], source, journal)
            if (journal.get('job_id') != row['id'] or from_legacy(journal.get('status') or journal.get('state')) != row['state']
                    or journal.get('output_dir') != row['output_dir']):
                raise ValueError('Historical source identity/state/output differs from its ledger')
            output = _owned_path(root, row['output_dir'])
            if not output.is_dir():raise ValueError('Historical model output must remain a directory')
            matches = [location for location in locations if output.is_relative_to(Path(location[3]))]
            if len(matches) != 1:raise ValueError('Historical output lacks one registered project')
            key, workspace, project_id, directory = matches[0]
            directory = _owned_path(root, directory)
            manifest = _owned_path(root, str(directory / 'project.json'))
            project = json.loads(manifest.read_bytes())
            models = _owned_path(root, project.get('models_dir'))
            if not models.is_dir():raise ValueError('Historical project models must remain a directory')
            if (workspace != identities.get('workspace_id') or project.get('id') != project_id
                    or project.get('workspace_id', workspace) != workspace or not models.is_relative_to(directory)
                    or output != models / row['id']):
                raise ValueError('Historical output/project manifest differs from its registered local namespace')
            rows.append({'job_id': row['id'], 'revision': row['revision'], 'state': row['state'],
                'spec_sha256': row['spec_sha256'], 'source_sha256': spec['legacy_sha256'],
                'previous_context': {k: row[k] for k in _CONTEXT},
                'target_context': dict(zip(_CONTEXT, (workspace, key, project_id, identities['local_actor_id'], 'local'))),
                'project_dir': str(directory), 'registry_root': str((root / owner['scopes']['context']).parent)})
        except (ValueError, KeyError, TypeError, OSError) as exc:
            blockers.append(row['id'] + ': ' + str(exc))
    blockers += migration._authority_blockers(root, owner['scopes'], history_jobs={r['job_id'] for r in rows})
    from backend.engine.terminal_runtime_history import journal_blockers
    blockers += journal_blockers(root, owner['scopes'])
    record = {'schema_version': 1, 'installation_id': owner['installation_id'],
        'source_sha256': snapshot['source_snapshot']['sha256'], 'rows': rows,
        'blockers': sorted(set(blockers)), 'can_apply': bool(rows) and not blockers,
        'policy': 'ended owned history only; original actor retained; no connection, launch, lease or team authority created'}
    record['preview_sha256'] = migration.digest(record)
    return record


def preview(root):
    root, owner = migration._owner(root)
    with store_admission(root):return _view(root, owner)


def apply(root, *, expected_preview_sha256, reason):
    if not isinstance(reason, str) or not 10 <= len(reason.strip()) <= 2000:
        raise ValueError('Enter an explicit historical binding review reason of 10–2000 characters')
    if not isinstance(expected_preview_sha256, str) or not re.fullmatch('[0-9a-f]{64}', expected_preview_sha256):
        raise ValueError('Expected historical preview digest is invalid')
    root, owner = migration._owner(root)
    with store_admission(root, exclusive=True):
        # Inspect schema/source before any constructor can upgrade an older DB.
        # A prior receipt is a history readback, even after forward cutover.
        path = resolve_store_path(root / owner['scopes']['ledger'])
        with migration._read_db(path) as source:
            saved = source.execute('SELECT value FROM meta WHERE key=?', (_PREFIX + expected_preview_sha256,)).fetchone()
        if saved:return {**json.loads(saved[0]), 'replayed': True}
        before = _view(root, owner)
        if before['preview_sha256'] != expected_preview_sha256:raise ValueError('Historical source or control changed since preview')
        if not before['can_apply']:raise ValueError('; '.join(before['blockers']) or 'No unbound terminal history')
        ledger = JobStore(root / owner['scopes']['ledger'])
        with ledger._tx() as db:
            saved = db.execute('SELECT value FROM meta WHERE key=?', (_PREFIX + expected_preview_sha256,)).fetchone()
            if saved:return {**json.loads(saved[0]), 'replayed': True}
            plan = _view(root, owner)
            if plan['preview_sha256'] != expected_preview_sha256:raise ValueError('Historical source or control changed since preview')
            if not plan['can_apply']:raise ValueError('; '.join(plan['blockers']) or 'No unbound terminal history')
            now = time.time_ns(); identifier = uuid.uuid4().hex
            for row in plan['rows']:
                target = row['target_context']
                changed = db.execute('UPDATE jobs SET workspace_id=?,project_key=?,project_id=?,actor_id=?,mode=?,registry_root=?,project_dir=?,revision=revision+1,updated_ns=? WHERE id=? AND revision=? AND state=? AND spec_sha256=?',
                    (*[target[k] for k in _CONTEXT], row['registry_root'], row['project_dir'], now,
                     row['job_id'], row['revision'], row['state'], row['spec_sha256'])).rowcount
                if changed != 1:raise ValueError('Historical ledger revision changed before binding')
                ledger._event(db, row['job_id'], row['revision'] + 1, 'historical_context_bound', row['state'], row['state'],
                    {'binding_id': identifier, 'source_sha256': row['source_sha256'], 'previous_context': row['previous_context'],
                     'target_context': target, 'reason': reason.strip(), 'worker_authority_created': False}, now)
            receipt = {'schema_version': 1, 'binding_id': identifier, 'preview_sha256': expected_preview_sha256,
                'installation_id': owner['installation_id'], 'reason': reason.strip(), 'rows': plan['rows'],
                'written_ns': now, 'status': 'bound_terminal_history', 'worker_authority_created': False,
                'source_journals_changed': False, 'replayed': False}
            db.execute('INSERT INTO meta(key,value) VALUES(?,?)', (_PREFIX + expected_preview_sha256, json.dumps(receipt, sort_keys=True)))
            return receipt


def read_receipt(root, identifier):
    if not isinstance(identifier, str) or not re.fullmatch('[0-9a-f]{32}', identifier):raise ValueError('Invalid historical binding identity')
    root, owner = migration._owner(root)
    path = resolve_store_path(root / owner['scopes']['ledger'])
    with store_admission(path), migration._read_db(path) as db:
        for (raw,) in db.execute('SELECT value FROM meta WHERE key LIKE ?', (_PREFIX + '%',)):
            record = json.loads(raw)
            if record.get('binding_id') == identifier:return record
    raise ValueError('Historical binding receipt is unavailable')


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('preview', 'apply', 'receipt'));parser.add_argument('--root', required=True)
    parser.add_argument('--expected-preview-sha256');parser.add_argument('--reason');parser.add_argument('--binding-id')
    args = parser.parse_args(argv)
    try:
        result = preview(args.root) if args.command == 'preview' else read_receipt(args.root, args.binding_id) if args.command == 'receipt' else apply(args.root, expected_preview_sha256=args.expected_preview_sha256, reason=args.reason)
    except (ValueError, OSError, KeyError, TypeError, sqlite3.Error) as exc:
        print(json.dumps({'status': 'refused', 'error': str(exc)}));return 1
    print(json.dumps(result, sort_keys=True));return 0


if __name__ == '__main__':raise SystemExit(main())
