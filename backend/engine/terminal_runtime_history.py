"""Validate ended recovery journals without acquiring worker authority.

Only an original owned installation with current control schemas is supported.
Live/uncertain sessions still require their own ownership protocol. Remote
records are archival only: no connection, launch, signal or lease is created.
"""
import hashlib
import json
import math
from pathlib import Path, PurePosixPath
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


def _validate_remote_epoch(root, output, journal):
    """An optional received epoch state is archival only, never launch authority."""
    checkpoint, receipt_path = output / 'latest_training_state.pt', output / 'training_state_receipt.json'
    if not any(path.exists() or path.is_symlink() for path in (checkpoint, receipt_path)):
        return  # Original workers may have no completed epoch or state protocol.
    receipt, _ = _read(root, receipt_path)
    checkpoint = _file(root, checkpoint)
    from backend.remote.training_state_transfer import MAX_STATE_BYTES, STATE_ARTIFACT, identity_sha256
    size = checkpoint.stat().st_size
    if (type(receipt.get('protocol_version')) is not int or receipt['protocol_version'] != 1
            or receipt.get('operation') != 'train' or receipt.get('job_id') != journal['job_id']
            or receipt.get('input_manifest_sha256') != journal['input_manifest_sha256']
            or receipt.get('path') != STATE_ARTIFACT or type(receipt.get('size')) is not int
            or not 0 < size <= MAX_STATE_BYTES or receipt['size'] != size
            or any(not re.fullmatch('[0-9a-f]{64}', str(receipt.get(key, '')))
                   for key in ('sha256', 'identity_sha256'))):
        raise ValueError('Remote training-state receipt differs from its original terminal run')
    from backend.engine.training_resume import read_training_state
    state = read_training_state(checkpoint)
    if (state['_checkpoint_sha256'] != receipt['sha256']
            or state['identity'].get('task') != journal['task']
            or state['identity'].get('preset') != journal['preset']
            or identity_sha256(state['identity']) != receipt['identity_sha256']
            or any(type(receipt.get(key)) is not int or state[key] != receipt[key]
                   for key in ('next_epoch', 'global_step'))):
        raise ValueError('Remote training-state bytes or epoch identity differ from the received receipt')


def _validate_remote_labels(root, output, journal):
    """Keep original pending proposals as archival content, not review authority."""
    labels, _ = _read(root, output / 'label_results.json')
    rows = labels.get('results')
    if (labels.get('job_id') != journal['job_id']
            or labels.get('input_manifest_sha256') != journal['input_manifest_sha256']
            or labels.get('automatically_approved') is not False
            or not isinstance(rows, list) or not 1 <= len(rows) <= 10000):
        raise ValueError('Remote label results differ from their original pending snapshot')
    images = set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError('Remote label result must be a pending image record')
        name = row.get('image_path')
        if not isinstance(name, str) or not name or '\\' in name or '\x00' in name:
            raise ValueError('Remote label image must have its original relative snapshot path')
        path = PurePosixPath(name)
        if (path.is_absolute() or '..' in path.parts or str(path) != name or name == '.'
                or name in images or not isinstance(row.get('image_sha256'), str)
                or not re.fullmatch('[0-9a-f]{64}', row['image_sha256'])
                or not isinstance(row.get('candidates'), list) or row.get('review_state') != 'pending'):
            raise ValueError('Remote label image identity or pending review state is invalid')
        images.add(name)


def validate_remote(root, scopes, path, journal):
    """Original confirmed exit and received bytes, never current SSH authority.

    This adapter supports original current train and pending label manifests,
    not relocated specialist aliases or other remote operation results. Received optimizer epoch
    states are retained only with their original hash-bound receipt; no resume
    execution or live worker adoption is granted. It reads
    the coordinator's existing exit confirmation; it does not check a server's
    current process state or create authority to use that server again.
    """
    from backend.remote.profiles import ComputeProfile
    root = Path(root).absolute()
    identifier, state, operation = journal.get('job_id'), journal.get('state'), journal.get('operation')
    if (not isinstance(identifier, str) or not re.fullmatch(r'job_[A-Za-z0-9_-]{1,123}', identifier)
            or Path(path).name != identifier + '.json' or journal.get('protocol_version') != 1
            or operation not in {'train', 'label'} or state not in TERMINAL
            or journal.get('worker_terminal_state') != state or journal.get('worker_exit_confirmed') is not True):
        raise ValueError('Only confirmed ended current remote train or label journals can migrate')
    profile = ComputeProfile.model_validate(journal.get('profile'))
    handle = journal.get('remote_handle')
    pattern = r'[1-9][0-9]*:[0-9a-f]{32}' if profile.runtime_kind == 'python' else r'[0-9a-fA-F]{12,64}'
    if not isinstance(handle, str) or not re.fullmatch(pattern, handle):
        raise ValueError('Remote history lacks its original launch handle')
    output = Path(journal['output_dir']).absolute()
    _, run_raw = _read(root, output / 'remote_job.json')
    _, index_raw = _read(root, path)
    if index_raw != run_raw:
        raise ValueError('Remote recovery index differs from its original run journal')
    spec_path = _file(root, output / 'remote_spec.json')
    spec, raw = _read(root, spec_path)
    transfers, launch = journal.get('transfers'), journal.get('launch_spec')
    if not isinstance(transfers, list) or not 1 <= len(transfers) <= 32 or not isinstance(launch, dict):
        raise ValueError('Remote history lacks its bounded original transfer/launch specification')
    selected = [r for r in transfers if isinstance(r, dict) and r.get('target') == 'spec.json']
    if (len(selected) != 1 or selected[0].get('source') != str(spec_path)
            or type(selected[0].get('size')) is not int or selected[0]['size'] != len(raw)
            or selected[0].get('sha256') != hashlib.sha256(raw).hexdigest()):
        raise ValueError('Remote original launch specification changed')
    if (spec.get('protocol_version') != 1 or spec.get('job_id') != identifier or spec.get('operation') != operation
            or spec.get('task') != journal.get('task') or spec.get('preset') != journal.get('preset')
            or not re.fullmatch('[0-9a-f]{64}', str(journal.get('input_manifest_sha256', '')))
            or spec.get('input_manifest_sha256') != journal['input_manifest_sha256']
            or launch.get('operation', 'train') != operation):
        raise ValueError('Remote history launch belongs to another run, task or snapshot')
    receipt, _ = _read(root, output / 'job_receipt.json')
    if (receipt.get('job_id') != identifier or receipt.get('status') != state
            or receipt.get('task') != journal['task'] or receipt.get('output_dir') != str(output)
            or receipt.get('compute_profile_id') != profile.id):
        raise ValueError('Remote terminal receipt differs from its original job/profile')
    if operation == 'train':
        _validate_remote_epoch(root, output, journal)
    elif any(p.exists() or p.is_symlink() for p in (
            output / 'latest_training_state.pt', output / 'training_state_receipt.json')):
        raise ValueError('Remote label history cannot carry training-state authority')
    label_artifacts_present = operation == 'label' and any(p.exists() or p.is_symlink() for p in (
        output / 'label_results.json', output / 'remote_artifacts.json'))
    if state != 'completed' and not label_artifacts_present:
        return
    manifest, _ = _read(root, output / 'remote_artifacts.json')
    if (manifest.get('protocol_version') != 1 or manifest.get('job_id') != identifier
            or manifest.get('operation') != operation or manifest.get('input_manifest_sha256') != journal['input_manifest_sha256']
            or 'relocation' in manifest):
        raise ValueError('Remote received artifact manifest belongs to another or relocated run')
    rows = manifest.get('artifacts')
    expected = {'outputs/best_model.pt', 'outputs/model_meta.json'} if operation == 'train' else {'outputs/label_results.json'}
    if not isinstance(rows, list) or len(rows) != len(expected) or any(not isinstance(row, dict) for row in rows):
        raise ValueError('Remote received artifact list is invalid')
    names = [row.get('path') for row in rows]
    if any(not isinstance(name, str) for name in names) or set(names) != expected:
        raise ValueError('Remote received artifact identities differ from the original operation manifest')
    for row in rows:
        file = _file(root, output / Path(row['path']).name)
        if (type(row.get('size')) is not int or row['size'] < 1 or file.stat().st_size != row['size']
                or not re.fullmatch('[0-9a-f]{64}', str(row.get('sha256', '')))):
            raise ValueError('Remote received artifact size or checksum is invalid')
        with file.open('rb') as stream:
            checksum = hashlib.file_digest(stream, 'sha256').hexdigest()
        if checksum != row['sha256'] or (file.name == 'best_model.pt' and receipt.get('checkpoint_sha256') != checksum):
            raise ValueError('Remote received artifact or completed receipt checksum changed')
    if operation == 'label':
        _validate_remote_labels(root, output, journal)
        return
    metadata, _ = _read(root, output / 'model_meta.json')
    if metadata.get('task') != journal['task']:
        raise ValueError('Remote received model task differs')
    binding = launch.get('dataset_binding')
    if binding and (metadata.get('training_provenance') != binding or spec.get('dataset_binding') != binding
                    or receipt.get('training_provenance') != binding):
        raise ValueError('Remote received model provenance differs from its launch or receipt')


def journal_blockers(source_root, scopes, *, installation_root=None):
    """Inspect every declared index file before a drained generation is copied."""
    from backend.engine.global_migration import _read_db
    source_root = Path(source_root).absolute()
    original = Path(installation_root or source_root).absolute()
    errors = []
    entries=[]
    for kind in ('local','remote'):
        index=source_root/scopes[kind+'_journals']
        if not index.exists() and not index.is_symlink():continue
        if index.is_symlink() or not index.is_dir():
            errors.append('Runtime '+kind+' recovery index must be an unlinked directory');continue
        entries.extend((kind,path) for path in sorted(index.iterdir()))
    if len(entries) > 1000:
        return errors + ['Runtime history exceeds the bounded 1000-journal conversion']
    if not entries:return errors
    try:
        with _read_db(source_root / scopes['ledger']) as db:
            db.row_factory = sqlite3.Row
            rows={}
            for _,path in entries:
                row=db.execute('SELECT * FROM jobs WHERE id=?',(path.stem,)).fetchone()
                if row is not None:rows[row['id']]=dict(row)
        with _read_db(source_root / scopes['context']) as db:
            locations=list(db.execute('SELECT scope_key,workspace_id,project_id,path FROM project_locations LIMIT 1001'))
        if len(locations)>1000:return errors+['Runtime history exceeds the bounded 1000-project conversion']
    except (OSError,ValueError,sqlite3.Error) as error:
        return errors+['Runtime history control stores are unavailable: '+str(error)]
    for kind,path in entries:
        try:
            journal, raw = _read(original, path)
            (validate_local if kind=='local' else validate_remote)(original,scopes,path,journal)
            row = rows.get(journal['job_id'])
            if row is None or row['state'] != journal['status' if kind=='local' else 'state'] or row['output_dir'] != journal['output_dir']:
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
