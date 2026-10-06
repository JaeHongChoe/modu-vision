"""Validate ended local recovery journals without acquiring worker authority.

Only an original owned installation with current control schemas is supported.
Live/uncertain sessions and remote recovery journals still require their own
ownership protocol. No process is signalled, lease released or source rewritten.
"""
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3

TERMINAL = {'completed', 'failed', 'aborted'}
MAX_JSON_BYTES = 1024 * 1024


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate runtime history key')
        result[key] = value
    return result


def _file(root, path):
    path = Path(path).absolute()
    if (not path.is_relative_to(root) or not path.resolve().is_relative_to(root)
            or any(p.is_symlink() for p in (path, *path.parents)) or not path.is_file()):
        raise ValueError('Runtime history must stay in the unlinked original installation')
    return path


def _read(root, path):
    path = _file(root, path)
    with path.open('rb') as handle:
        raw = handle.read(MAX_JSON_BYTES + 1)
    if len(raw) > MAX_JSON_BYTES:
        raise ValueError('Runtime history exceeds the bounded JSON size')
    value = json.loads(raw, object_pairs_hook=_unique)
    if not isinstance(value, dict):
        raise ValueError('Runtime history must be a JSON object')
    return value, raw


def validate_local(root, scopes, path, journal):
    """Read-only, current-schema local exit/spec/copy proof; not live adoption."""
    root = Path(root).absolute()
    identifier = journal.get('job_id')
    if (not isinstance(identifier, str) or not re.fullmatch(r'job_[A-Za-z0-9_-]{1,123}', identifier)
            or Path(path).name != identifier + '.json' or journal.get('protocol_version') != 1
            or journal.get('status') not in TERMINAL or journal.get('worker_exit_confirmed') is not True):
        raise ValueError('Only exact confirmed ended current local runtime journals can migrate')
    token = journal.get('owner_token')
    created = journal.get('owner_created_at')
    if (not isinstance(token, str) or not re.fullmatch('[0-9a-f]{32}', token)
            or type(journal.get('owner_pid')) is not int or journal['owner_pid'] < 1
            or journal.get('owner_session') != journal['owner_pid']
            or type(created) not in (float, int) or not math.isfinite(created) or created <= 0
            or not isinstance(journal.get('owner_username'), str) or not journal['owner_username']):
        raise ValueError('Local runtime history lacks original session ownership proof')
    output = Path(journal['output_dir']).absolute()
    spec_path = _file(root, journal['spec_path'])
    if spec_path != output / 'local_spec.json':
        raise ValueError('Runtime launch specification differs from its original output')
    spec, raw = _read(root, spec_path)
    if hashlib.sha256(raw).hexdigest() != journal.get('spec_sha256'):
        raise ValueError('Runtime history launch specification changed')
    if (spec.get('protocol_version') != 1 or spec.get('job_id') != identifier
            or spec.get('output_dir') != str(output)
            or spec.get('lease_path') != str(root / scopes['leases'])
            or not isinstance(spec.get('lease_owner'), str) or not spec['lease_owner']
            or spec.get('task') != journal.get('task') or spec.get('preset') != journal.get('preset')):
        raise ValueError('Runtime history specification belongs to another job or lease store')
    _, run_raw = _read(root, output / 'local_job.json')
    _, index_raw = _read(root, path)
    if index_raw != run_raw:
        raise ValueError('Runtime recovery index differs from the original run-local journal')
    from backend.engine.local_training_worker import _liveness
    if _liveness(journal) is not False:
        raise ValueError('Runtime session is live or uncertain; migration cannot adopt it as ended')


def journal_blockers(source_root, scopes, *, installation_root=None):
    """Inspect every declared index file before a drained generation is copied."""
    from backend.engine.global_migration import _read_db
    source_root = Path(source_root).absolute()
    original = Path(installation_root or source_root).absolute()
    errors = []
    remote = source_root / scopes['remote_journals']
    if remote.exists() and (not remote.is_dir() or any(remote.iterdir())):
        errors.append('Historical remote_journals requires a separately reviewed remote ownership adapter')
    index = source_root / scopes['local_journals']
    if not index.exists():
        return errors
    if index.is_symlink() or not index.is_dir():
        return errors + ['Runtime local recovery index must be an unlinked directory']
    entries = sorted(index.iterdir())
    if len(entries) > 1000:
        return errors + ['Runtime history exceeds the bounded 1000-journal conversion']
    if not entries:return errors
    try:
        with _read_db(source_root / scopes['ledger']) as db:
            db.row_factory = sqlite3.Row
            rows={}
            for path in entries:
                row=db.execute('SELECT * FROM jobs WHERE id=?',(path.stem,)).fetchone()
                if row is not None:rows[row['id']]=dict(row)
        with _read_db(source_root / scopes['context']) as db:
            locations=list(db.execute('SELECT scope_key,workspace_id,project_id,path FROM project_locations LIMIT 1001'))
        if len(locations)>1000:return errors+['Runtime history exceeds the bounded 1000-project conversion']
    except (OSError,ValueError,sqlite3.Error) as error:
        return errors+['Runtime history control stores are unavailable: '+str(error)]
    for path in entries:
        try:
            journal, raw = _read(original, path)
            validate_local(original, scopes, path, journal)
            row = rows.get(journal['job_id'])
            if row is None or row['state'] != journal['status'] or row['output_dir'] != journal['output_dir']:
                raise ValueError('Runtime history lacks its exact terminal ledger record')
            output = Path(row['output_dir'])
            matches = [r for r in locations if output.is_relative_to(Path(r[3]))]
            if len(matches) != 1:
                raise ValueError('Runtime output lacks one original registered project')
            key, workspace, project_id, directory = matches[0]
            project, _ = _read(original, Path(directory) / 'project.json')
            if (project.get('id') != project_id or output != Path(project.get('models_dir', '')) / row['id']):
                raise ValueError('Runtime output differs from its original registered models')
            if row['source'] == 'legacy_migration':
                spec = json.loads(row['spec_json'], object_pairs_hook=_unique)
                if spec.get('legacy_sha256') != hashlib.sha256(raw).hexdigest():
                    raise ValueError('Runtime historical source changed after ledger import')
            if row['workspace_id'] != 'legacy' and (row['workspace_id'], row['project_key'], row['project_id']) != (workspace, key, project_id):
                raise ValueError('Runtime history namespace differs from its original project')
        except (ValueError, OSError, KeyError, TypeError) as exc:
            errors.append(path.name + ': ' + str(exc))
    return errors
