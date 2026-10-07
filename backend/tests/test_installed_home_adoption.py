"""Explicit adoption of original quiescent POSIX stores, never copied authority."""
import hashlib
from contextlib import closing
import io
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys

import pytest

from backend.tests.test_global_migration import owned
from backend.engine.global_store_paths import OWNER_FILE, SUPPORTED_SCOPES


def api():
    try:
        from backend.engine import installed_home_adoption
    except ImportError:
        pytest.fail('Installed-home adoption preview/CAS contract is missing')
    return installed_home_adoption


def installed(tmp_path):
    root, scopes, ledger, registry, accounts, actor = owned(tmp_path)
    directory = root / 'projects' / 'original'
    (directory / 'models').mkdir(parents=True)
    project = {'id': 'original', 'workspace_id': registry.workspace_id,
               'project_dir': str(directory), 'models_dir': str(directory / 'models')}
    (directory / 'project.json').write_text(json.dumps(project))
    key = registry.register_project(project)
    with closing(sqlite3.connect(root / scopes['accounts'])) as db, db:
        db.execute('INSERT INTO projects VALUES(?,?)', ('original', str(directory)))
        db.execute('INSERT INTO members VALUES(?,?,?)', ('original', actor['id'], 'owner'))
    with closing(sqlite3.connect(root / scopes['ledger'])) as db, db:
        db.execute("INSERT INTO jobs(id,workspace_id,project_key,project_id,actor_id,mode,kind,spec_sha256,spec_json,state,revision,source,created_ns,updated_ns) VALUES(?,?,?,?,?,'local','training','original-spec','{}','completed',1,'fixture',1,1)",
                   ('ended-original', registry.workspace_id, key, 'original', registry.local_actor_id))
    for name in ('local_journals', 'remote_journals'):
        (root / scopes[name]).mkdir(exist_ok=True)
    (root / OWNER_FILE).unlink()
    return root, scopes, accounts


def original_bytes(root):
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob('*')
            if p.is_file() and '.installed-home-adoption' not in p.relative_to(root).parts
            and p.name not in {OWNER_FILE, 'migration_admission.lock'}}


def attest(preview):
    return {'owned_original': True, 'writers_quiescent': True,
            'root_identity': preview['root_identity']}


def apply(root, scopes, preview, **kwargs):
    return api().adopt_installed_home(root, scopes=scopes,
        expected_preview_sha256=preview['preview_sha256'],
        owned_quiescent_attestation=attest(preview), **kwargs)


def test_readonly_preview_then_adoption_preserves_originals_and_enables_existing_migration(tmp_path):
    root, scopes, accounts = installed(tmp_path)
    before = original_bytes(root)
    full_membership = sorted(p.relative_to(root).as_posix() for p in root.rglob('*'))
    proposal = api().preview_installed_home(root, scopes=scopes)
    assert proposal['can_adopt'] and proposal['blockers'] == []
    assert proposal['root_identity'] == {'path': str(root), 'device': root.stat().st_dev,
                                         'inode': root.stat().st_ino}
    assert original_bytes(root) == before
    assert sorted(p.relative_to(root).as_posix() for p in root.rglob('*')) == full_membership
    result = apply(root, scopes, proposal)
    assert result['status'] == 'adopted'
    assert original_bytes(root) == before
    descriptor = json.loads((root / OWNER_FILE).read_bytes())
    assert descriptor['root_identity'] == proposal['root_identity']
    backup = Path(result['backup_path'])
    for name, content in before.items():
        assert (backup / name).read_bytes() == content
        assert (backup / name).stat().st_mode & 0o777 == 0o600
    assert backup.stat().st_mode & 0o777 == 0o700
    from backend.engine.global_migration import preview, apply as migrate
    plan = preview(root)
    assert plan['can_apply'], plan['blockers']
    migrated = migrate(root, expected_source_sha256=plan['source_sha256'])
    assert migrated['status'] == 'applied'
    assert {name: (root / name).read_bytes() for name in before} == before


@pytest.mark.parametrize('scope', ['profiles', 'ledger'])
def test_logical_validation_never_reopens_source_replaced_by_fifo_after_raw_scan(tmp_path, scope):
    root, scopes, _ = installed(tmp_path)
    script = r'''
import json, os, sys
from pathlib import Path
from backend.engine import installed_home_adoption as adoption
root = Path(sys.argv[1]); scopes = json.loads(sys.argv[2]); name = sys.argv[3]
scan = adoption._raw_inventory
calls = 0
def changed(*args, **kwargs):
    global calls
    result = scan(*args, **kwargs)
    calls += 1
    if calls == 1:
        target = root / scopes[name]
        target.unlink(); os.mkfifo(target, 0o600)
    return result
adoption._raw_inventory = changed
result = adoption.preview_installed_home(root, scopes=scopes)
assert not result['can_adopt'], result
assert not (root / 'global-installation.json').exists()
'''
    result = subprocess.run([sys.executable, '-c', script, str(root), json.dumps(scopes), scope],
                            capture_output=True, text=True, timeout=5)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize('scope', ['profiles', 'ledger'])
def test_unsafe_logical_source_open_boundary_cannot_block_on_fifo(tmp_path, scope):
    root, scopes, _ = installed(tmp_path)
    script = r'''
import json, os, sys
from pathlib import Path
from backend.engine import installed_home_adoption as adoption
from backend.remote.profiles import ProfileStore
root=Path(sys.argv[1]); scopes=json.loads(sys.argv[2]); scope=sys.argv[3]
target=root/scopes[scope]; changed=False
def replace():
    global changed
    changed=True; target.unlink(); os.mkfifo(target,0o600)
if scope=='profiles':
    original=ProfileStore._read
    def read(self):
        if self.path==target and not changed: replace()
        return original(self)
    ProfileStore._read=read
else:
    original=Path.open
    def open(self,*args,**kwargs):
        if self==target and not changed: replace()
        return original(self,*args,**kwargs)
    Path.open=open
result=adoption.preview_installed_home(root,scopes=scopes)
assert result['can_adopt'] is not changed, result
'''
    result = subprocess.run([sys.executable, '-c', script, str(root), json.dumps(scopes), scope],
                            capture_output=True, text=True, timeout=5)
    assert result.returncode == 0, result.stdout + result.stderr


def test_shared_logical_snapshot_rejects_growth_before_copying_extra_bytes(tmp_path, monkeypatch):
    from backend.engine import migration_inventory as reader
    source = tmp_path / 'bounded.json'; source.write_bytes(b'{"original":true}')
    expected_size = source.stat().st_size
    opened = reader.os.read
    altered = False
    def growing(descriptor, count):
        nonlocal altered
        block = opened(descriptor, count)
        if not altered:
            altered = True
            with source.open('ab') as writer: writer.write(b'growth')
        return block
    monkeypatch.setattr(reader.os, 'read', growing)
    with pytest.raises(ValueError, match='grew|changed|bound'):
        with reader.bounded_file_snapshot(source, max_bytes=expected_size) as snapshot:
            pytest.fail('A growing source cannot become a logical snapshot')


@pytest.mark.parametrize('scope', ['profiles', 'ledger'])
def test_logical_source_fifo_at_safe_fd_open_is_refused_without_blocking(tmp_path, scope):
    root, scopes, _ = installed(tmp_path)
    script = r'''
import json, os, sys
from pathlib import Path
from backend.engine import installed_home_adoption as adoption
root=Path(sys.argv[1]); scopes=json.loads(sys.argv[2]); target=root/scopes[sys.argv[3]]
original=os.open; calls=0
def open(path,*args,**kwargs):
    global calls
    if Path(path)==target:
        calls+=1
        if calls==2: target.unlink(); os.mkfifo(target,0o600)
    return original(path,*args,**kwargs)
os.open=open
result=adoption.preview_installed_home(root,scopes=scopes)
assert calls==2 and not result['can_adopt'], result
'''
    result = subprocess.run([sys.executable, '-c', script, str(root), json.dumps(scopes), scope],
                            capture_output=True, text=True, timeout=5)
    assert result.returncode == 0, result.stdout + result.stderr


def test_profile_growth_after_raw_scan_refuses_before_logical_parse(tmp_path, monkeypatch):
    root, scopes, _ = installed(tmp_path)
    scan = api()._raw_inventory
    calls = 0
    def growing(*args, **kwargs):
        nonlocal calls
        value = scan(*args, **kwargs)
        calls += 1
        if calls == 1:
            with (root / scopes['profiles']).open('r+b') as writer:
                writer.truncate(8 * 1024**2 + 1)
        return value
    monkeypatch.setattr(api(), '_raw_inventory', growing)
    proposal = api().preview_installed_home(root, scopes=scopes)
    assert not proposal['can_adopt']
    assert any('unbounded' in blocker for blocker in proposal['blockers'])
    assert not (root / OWNER_FILE).exists()


@pytest.mark.parametrize('value', ['[]', 'null', 'true', '"scalar"', 'duplicate-id'])
def test_malformed_or_duplicate_original_project_manifest_is_structured_readonly_refusal(tmp_path, value):
    root, scopes, _ = installed(tmp_path)
    if value == 'duplicate-id':
        value = '{"id":"foreign",' + (root / 'projects/original/project.json').read_text()[1:]
    (root / 'projects/original/project.json').write_text(value)
    before = original_bytes(root)
    proposal = api().preview_installed_home(root, scopes=scopes)
    assert not proposal['can_adopt'] and proposal['blockers']
    assert original_bytes(root) == before
    assert not (root / OWNER_FILE).exists()


def test_duplicate_profile_keys_are_ambiguous_readonly_authority(tmp_path):
    root, scopes, _ = installed(tmp_path)
    (root / scopes['profiles']).write_text('{"profiles":[],"selected":"foreign","selected":null}')
    before = original_bytes(root)
    proposal = api().preview_installed_home(root, scopes=scopes)
    assert not proposal['can_adopt'] and proposal['blockers']
    assert original_bytes(root) == before


@pytest.mark.parametrize('input_file', ['scopes', 'attestation'])
def test_cli_rejects_duplicate_explicit_authority_keys_without_publishing(tmp_path, input_file):
    root, scopes, _ = installed(tmp_path)
    proposal = api().preview_installed_home(root, scopes=scopes)
    before = original_bytes(root)
    scopes_file = tmp_path / 'scopes.json'; scopes_file.write_text(json.dumps(scopes))
    attestation_file = tmp_path / 'attestation.json'; attestation_file.write_text(json.dumps(attest(proposal)))
    args = ['preview-installed', '--root', str(root), '--scopes-file', str(scopes_file)]
    if input_file == 'scopes': scopes_file.write_text('{"ledger":"foreign",' + json.dumps(scopes)[1:])
    else:
        attestation_file.write_text('{"owned_original":false,' + json.dumps(attest(proposal))[1:])
        args = ['adopt-installed', '--root', str(root), '--scopes-file', str(scopes_file),
                '--expected-preview-sha256', proposal['preview_sha256'], '--attestation-file', str(attestation_file)]
    result = subprocess.run([sys.executable, '-m', 'backend.engine.global_migration', *args],
        cwd=Path(__file__).resolve().parents[2], capture_output=True, text=True, timeout=30)
    assert result.returncode == 1 and json.loads(result.stdout)['status'] == 'refused'
    assert not (root / OWNER_FILE).exists() and original_bytes(root) == before


def test_specialist_relocation_deserializes_only_bounded_private_checkpoint_copies(tmp_path, monkeypatch):
    import torch
    from backend.tests.test_terminal_specialist_history import specialist_history
    from backend.engine.terminal_runtime_history import _validate_relocated_pair
    root, scopes, ledger, registry, key, source, output, journal, artifacts = specialist_history(tmp_path, monkeypatch)
    original = torch.load
    seen = []
    def private_load(path, *args, **kwargs):
        path = Path(path)
        assert not path.is_relative_to(root), 'Logical validation reopened an original checkpoint'
        seen.append(path)
        return original(path, *args, **kwargs)
    monkeypatch.setattr(torch, 'load', private_load)
    manifest = json.loads((output / 'remote_artifacts.json').read_bytes())
    from backend.remote.profiles import ComputeProfile
    _validate_relocated_pair(root, output, journal, manifest, ComputeProfile.model_validate(journal['profile']))
    assert len(seen) == 2


@pytest.mark.parametrize('operation', ['adopt', 'apply', 'advance', 'recover'])
def test_ambiguous_application_launch_owner_blocks_all_admission_mutations(tmp_path, operation):
    from backend.engine import global_migration as migration
    root, scopes, _ = installed(tmp_path)
    proposal = api().preview_installed_home(root, scopes=scopes)
    if operation != 'adopt':
        apply(root, scopes, proposal)
        plan = migration.preview(root)
        if operation in {'advance', 'recover'}:
            applied = migration.apply(root, expected_source_sha256=plan['source_sha256'])
            if operation == 'advance': plan = migration.preview_forward(root)
    (root / 'application-launch-lease.json').write_text('{}')
    before = original_bytes(root)
    with pytest.raises(ValueError, match='[Aa]pplication|[Ll]aunch'):
        if operation == 'adopt': apply(root, scopes, proposal)
        elif operation == 'apply': migration.apply(root, expected_source_sha256=plan['source_sha256'])
        elif operation == 'advance': migration.advance(root, expected_source_sha256=plan['source_sha256'])
        else: migration.recover(root, applied['migration_id'], action='finish')
    assert original_bytes(root) == before
    if operation == 'adopt':
        assert not (root / OWNER_FILE).exists()
        assert not (root / '.installed-home-adoption').exists()


@pytest.mark.parametrize('operation', ['apply_live', 'recover_live'])
def test_live_cutover_api_launch_guard_precedes_source_or_journal_validation(tmp_path, operation):
    from backend.engine import live_control_migration as migration
    root, scopes, *_ = owned(tmp_path)
    (root / 'application-launch-lease.json').write_text('{}')
    (root / 'application-active.json').write_text('{"damaged":true}')
    before = original_bytes(root)
    with pytest.raises(ValueError, match='[Aa]pplication.*[Ll]aunch'):
        if operation == 'apply_live': migration.apply_live(root, expected_source_sha256='0' * 64)
        else: migration.recover_live(root, '0' * 32, action='finish')
    assert original_bytes(root) == before
    assert not (root / '.global-migrations').exists()


@pytest.mark.parametrize('change', ['data', 'replaced_same_bytes', 'new_file', 'scope', 'attestation', 'digest'])
def test_reviewed_source_identity_scope_and_attestation_are_required_before_publication(tmp_path, change):
    root, scopes, _ = installed(tmp_path)
    before = original_bytes(root)
    proposal = api().preview_installed_home(root, scopes=scopes)
    evidence = attest(proposal)
    digest = proposal['preview_sha256']
    if change == 'data': (root / 'projects/labels.json').write_bytes(b'changed')
    elif change == 'replaced_same_bytes':
        path = root / 'projects/labels.json'
        replacement = root / 'replacement'; replacement.write_bytes(path.read_bytes()); replacement.replace(path)
    elif change == 'new_file': (root / 'new-original').write_bytes(b'new')
    elif change == 'scope': scopes = {**scopes, 'profiles': 'other.json'}
    elif change == 'attestation': evidence['writers_quiescent'] = 1
    elif change == 'digest': digest = '0' * 64
    before = original_bytes(root)
    with pytest.raises(ValueError):
        api().adopt_installed_home(root, scopes=scopes, expected_preview_sha256=digest,
                                  owned_quiescent_attestation=evidence)
    assert not (root / OWNER_FILE).exists() and original_bytes(root) == before


@pytest.mark.parametrize('change', ['schema', 'authority', 'external_project', 'active_job', 'lease',
    'open_attempt', 'session', 'oidc', 'journal', 'marker', 'linked_file', 'linked_directory',
    'hardlink', 'special_file', 'missing_scope'])
def test_unsafe_or_uncertain_original_state_refuses_readonly_and_never_publishes(tmp_path, change):
    root, scopes, accounts = installed(tmp_path)
    if change == 'schema':
        with closing(sqlite3.connect(root / scopes['ledger'])) as db, db: db.execute('CREATE TABLE foreign_authority(token TEXT)')
    elif change == 'authority':
        with closing(sqlite3.connect(root / scopes['accounts'])) as db, db: db.execute("UPDATE account_meta SET value='copied' WHERE name='workspace_id'")
    elif change == 'external_project':
        with closing(sqlite3.connect(root / scopes['context'])) as db, db: db.execute('UPDATE projects SET path=?', (str(tmp_path),))
    elif change == 'active_job':
        with closing(sqlite3.connect(root / scopes['ledger'])) as db, db: db.execute("UPDATE jobs SET state='running'")
    elif change == 'lease':
        with closing(sqlite3.connect(root / scopes['leases'])) as db, db:
            db.execute("INSERT INTO leases(job_id,host,selector,owner,expires,remote,uncertain) VALUES('unproven','local','cpu','foreign',1,0,1)")
    elif change == 'open_attempt':
        with closing(sqlite3.connect(root / scopes['ledger'])) as db, db:
            db.execute("INSERT INTO attempts(job_id,number,executor,fencing_token,started_ns) VALUES('ended-original',1,'local',1,1)")
    elif change == 'session': accounts.login('fixture-admin', 'fixture-password-123')
    elif change == 'oidc':
        with closing(sqlite3.connect(root / scopes['accounts'])) as db, db:
            db.execute("INSERT INTO oidc_pending VALUES('h','p','n','v','o',9999999999,'b')")
    elif change == 'journal': (root / scopes['local_journals'] / 'unproven.json').write_text('{"job_id":"unproven","status":"completed"}')
    elif change == 'marker': (root / 'application-active.json').write_text('{}')
    elif change == 'linked_file': (root / 'alias').symlink_to(root / 'projects/labels.json')
    elif change == 'linked_directory': (root / 'alias-directory').symlink_to(root / 'projects', target_is_directory=True)
    elif change == 'hardlink': os.link(root / 'projects/labels.json', root / 'other-labels')
    elif change == 'special_file': os.mkfifo(root / 'pipe')
    elif change == 'missing_scope': (root / scopes['profiles']).unlink()
    before = original_bytes(root)
    proposal = api().preview_installed_home(root, scopes=scopes)
    assert not proposal['can_adopt'] and proposal['blockers']
    with pytest.raises(ValueError): apply(root, scopes, proposal)
    assert not (root / OWNER_FILE).exists() and original_bytes(root) == before


def test_copied_installation_keeps_original_project_paths_and_cannot_be_adopted(tmp_path):
    root, scopes, _ = installed(tmp_path)
    copy = tmp_path / 'copied'; shutil.copytree(root, copy)
    original = original_bytes(root)
    proposal = api().preview_installed_home(copy, scopes=scopes)
    assert not proposal['can_adopt']
    with pytest.raises(ValueError): apply(copy, scopes, proposal)
    assert not (copy / OWNER_FILE).exists() and original_bytes(root) == original


def test_publication_is_no_replace_even_when_foreign_owner_appears_during_backup(tmp_path, monkeypatch):
    root, scopes, _ = installed(tmp_path)
    proposal = api().preview_installed_home(root, scopes=scopes)
    before = original_bytes(root)
    real_link = os.link
    foreign = b'{"foreign":"preserved"}'
    def race(source, target, *args, **kwargs):
        if Path(target) == root / OWNER_FILE:
            (root / OWNER_FILE).write_bytes(foreign)
        return real_link(source, target, *args, **kwargs)
    monkeypatch.setattr(os, 'link', race)
    with pytest.raises(ValueError): apply(root, scopes, proposal)
    assert (root / OWNER_FILE).read_bytes() == foreign and original_bytes(root) == before


def test_actual_process_crash_after_seal_has_explicit_safe_retry(tmp_path):
    root, scopes, _ = installed(tmp_path)
    proposal = api().preview_installed_home(root, scopes=scopes)
    before = original_bytes(root)
    script = """
import json,os,sys
from pathlib import Path
from backend.engine.installed_home_adoption import adopt_installed_home
root=Path(sys.argv[1]);p=json.loads(sys.argv[2]);scopes=json.loads(sys.argv[3]);real=os.link
def crash(source,target,*a,**kw):
 if Path(target)==root/'.global-migration-owner.json':os._exit(73)
 return real(source,target,*a,**kw)
os.link=crash
adopt_installed_home(root,scopes=scopes,expected_preview_sha256=p['preview_sha256'],owned_quiescent_attestation={'owned_original':True,'writers_quiescent':True,'root_identity':p['root_identity']})
"""
    result = subprocess.run([sys.executable, '-c', script, str(root), json.dumps(proposal), json.dumps(scopes)],
                            cwd=Path(__file__).resolve().parents[2], capture_output=True, timeout=30)
    assert result.returncode == 73, result.stderr.decode()
    assert not (root / OWNER_FILE).exists() and original_bytes(root) == before
    adopted = apply(root, scopes, proposal)
    owner = (root / OWNER_FILE).read_bytes()
    repeated = apply(root, scopes, proposal)
    assert adopted['installation_id'] == repeated['installation_id']
    assert (root / OWNER_FILE).read_bytes() == owner and original_bytes(root) == before


def test_exclusive_admission_refuses_other_process_without_owner_or_backup(tmp_path):
    root, scopes, _ = installed(tmp_path)
    proposal = api().preview_installed_home(root, scopes=scopes)
    from backend.engine.migration_guard import maintenance_guard
    script = """
import json,sys
from backend.engine.installed_home_adoption import adopt_installed_home
p=json.loads(sys.argv[2])
try:adopt_installed_home(sys.argv[1],scopes=json.loads(sys.argv[3]),expected_preview_sha256=p['preview_sha256'],owned_quiescent_attestation={'owned_original':True,'writers_quiescent':True,'root_identity':p['root_identity']})
except ValueError:sys.exit(19)
sys.exit(0)
"""
    with maintenance_guard(root, exclusive=True):
        result = subprocess.run([sys.executable, '-c', script, str(root), json.dumps(proposal), json.dumps(scopes)],
                                cwd=Path(__file__).resolve().parents[2], capture_output=True, timeout=30)
    assert result.returncode == 19, result.stderr.decode()
    assert not (root / OWNER_FILE).exists() and not (root / '.installed-home-adoption').exists()


def test_validated_json_replacement_fifo_refuses_without_blocking(tmp_path):
    control = tmp_path / 'intent.json'; control.write_text('{"original":true}')
    script = """
import os,sys
from pathlib import Path
from backend.engine import installed_home_adoption as a
p=Path(sys.argv[1]);real=a._regular
def replace(path,*args,**kw):
 r=real(path,*args,**kw);Path(path).unlink();os.mkfifo(path);return r
a._regular=replace
try:
 value=a._read_control(p)
 assert p.is_fifo() and value=={'original':True}
 sys.exit(20)
except ValueError:sys.exit(19)
"""
    try:
        result = subprocess.run([sys.executable, '-c', script, str(control)],
            cwd=Path(__file__).resolve().parents[2], capture_output=True, timeout=3)
    except subprocess.TimeoutExpired:
        pytest.fail('Validated JSON replacement FIFO blocked the control reader')
    assert result.returncode in {19, 20}, result.stderr.decode()


def test_growing_copy_never_publishes_more_than_reviewed_bytes(tmp_path, monkeypatch):
    root, scopes, _ = installed(tmp_path)
    module = api(); proposal = module.preview_installed_home(root, scopes=scopes)
    source = root / 'projects/labels.json'; original = source.read_bytes(); inode = source.stat().st_ino
    active = False; grown = False
    real_backup, real_open, real_read = module._backup, io.open, os.read
    def grow():
        nonlocal grown
        if active and not grown:
            grown = True
            with real_open(source, 'ab') as writer: writer.write(b'changed-after-review')
    class Reader:
        def __init__(self, reader): self.reader = reader
        def __enter__(self): return self
        def __exit__(self, *args): return self.reader.__exit__(*args)
        def read(self, size=-1): grow(); return self.reader.read(size)
    def open_file(path, mode='r', *args, **kwargs):
        result = real_open(path, mode, *args, **kwargs)
        return Reader(result) if active and mode == 'rb' and isinstance(path, (str, os.PathLike)) and Path(path) == source else result
    def read_fd(fd, size):
        if active and os.fstat(fd).st_ino == inode: grow()
        return real_read(fd, size)
    def backup(*args, **kwargs):
        nonlocal active
        active = True
        try: return real_backup(*args, **kwargs)
        finally: active = False
    monkeypatch.setattr(io, 'open', open_file); monkeypatch.setattr(os, 'read', read_fd)
    monkeypatch.setattr(module, '_backup', backup)
    with pytest.raises(ValueError): apply(root, scopes, proposal)
    assert grown and not (root / OWNER_FILE).exists()
    copied = root / '.installed-home-adoption/backup/projects/labels.json'
    assert not copied.exists() or copied.stat().st_size <= len(original)
    assert source.read_bytes() == original + b'changed-after-review'


def test_backup_copy_process_crash_retries_without_partial_published_files(tmp_path):
    root, scopes, _ = installed(tmp_path)
    proposal = api().preview_installed_home(root, scopes=scopes); before = original_bytes(root)
    script = """
import json,os,sys
from pathlib import Path
from backend.engine.installed_home_adoption import adopt_installed_home
root=Path(sys.argv[1]);p=json.loads(sys.argv[2]);real=os.link
def crash(source,target,*a,**kw):
 if Path(target).is_relative_to(root/'.installed-home-adoption/backup'):os._exit(72)
 return real(source,target,*a,**kw)
os.link=crash
adopt_installed_home(root,scopes=json.loads(sys.argv[3]),expected_preview_sha256=p['preview_sha256'],owned_quiescent_attestation={'owned_original':True,'writers_quiescent':True,'root_identity':p['root_identity']})
"""
    result = subprocess.run([sys.executable, '-c', script, str(root), json.dumps(proposal), json.dumps(scopes)],
        cwd=Path(__file__).resolve().parents[2], capture_output=True, timeout=30)
    assert result.returncode == 72, result.stderr.decode()
    assert not (root / OWNER_FILE).exists() and original_bytes(root) == before
    assert apply(root, scopes, proposal)['status'] == 'adopted'
    assert original_bytes(root) == before


def test_root_replacement_with_identical_content_refuses_reviewed_inode(tmp_path):
    root, scopes, _ = installed(tmp_path)
    proposal = api().preview_installed_home(root, scopes=scopes); before = original_bytes(root)
    moved = tmp_path / 'moved'; root.rename(moved); shutil.copytree(moved, root)
    with pytest.raises(ValueError): apply(root, scopes, proposal)
    assert not (root / OWNER_FILE).exists() and original_bytes(moved) == before


def test_tampered_sealed_backup_cannot_be_replayed_as_published_adoption(tmp_path):
    root, scopes, _ = installed(tmp_path)
    proposal = api().preview_installed_home(root, scopes=scopes)
    result = apply(root, scopes, proposal); owner = (root / OWNER_FILE).read_bytes()
    original = original_bytes(root)
    (Path(result['backup_path']) / 'projects/labels.json').write_bytes(b'tampered-private-backup')
    with pytest.raises(ValueError): apply(root, scopes, proposal)
    assert (root / OWNER_FILE).read_bytes() == owner and original_bytes(root) == original


def test_explicit_cli_preview_and_adoption_require_separate_review_and_attestation_files(tmp_path):
    root, scopes, _ = installed(tmp_path)
    before = original_bytes(root)
    scopes_file = tmp_path / 'scopes.json'; scopes_file.write_text(json.dumps(scopes))
    command = [sys.executable, '-m', 'backend.engine.global_migration']
    cwd = Path(__file__).resolve().parents[2]
    reviewed = subprocess.run(command + ['preview-installed', '--root', str(root),
        '--scopes-file', str(scopes_file)], cwd=cwd, capture_output=True, text=True, timeout=30)
    assert reviewed.returncode == 0, reviewed.stderr
    proposal = json.loads(reviewed.stdout)
    assert proposal['can_adopt'] and not (root / OWNER_FILE).exists() and original_bytes(root) == before
    attestation_file = tmp_path / 'attestation.json'; attestation_file.write_text(json.dumps(attest(proposal)))
    bad = attest(proposal); bad['owned_original'] = False
    attestation_file.write_text(json.dumps(bad))
    arguments = ['adopt-installed', '--root', str(root), '--scopes-file', str(scopes_file),
        '--expected-preview-sha256', proposal['preview_sha256'], '--attestation-file', str(attestation_file)]
    refused = subprocess.run(command + arguments, cwd=cwd, capture_output=True, text=True, timeout=30)
    assert refused.returncode == 1 and json.loads(refused.stdout)['status'] == 'refused'
    assert not (root / OWNER_FILE).exists() and original_bytes(root) == before
    attestation_file.write_text(json.dumps(attest(proposal)))
    adopted = subprocess.run(command + arguments,
        cwd=cwd, capture_output=True, text=True, timeout=30)
    assert adopted.returncode == 0, adopted.stderr + adopted.stdout
    assert json.loads(adopted.stdout)['status'] == 'adopted'
    migration = subprocess.run(command + ['preview', '--root', str(root)], cwd=cwd,
        capture_output=True, text=True, timeout=30)
    assert migration.returncode == 0, migration.stderr + migration.stdout
    plan = json.loads(migration.stdout); assert plan['can_apply'], plan['blockers']
    applied = subprocess.run(command + ['apply', '--root', str(root), '--expected-source-sha256', plan['source_sha256']],
        cwd=cwd, capture_output=True, text=True, timeout=30)
    assert applied.returncode == 0 and json.loads(applied.stdout)['status'] == 'applied'
    assert {name: (root / name).read_bytes() for name in before} == before


def test_oversized_source_refuses_before_hash_read_or_backup(tmp_path):
    root, scopes, _ = installed(tmp_path)
    large = root / 'oversized'
    with large.open('wb') as writer: writer.truncate(512 * 1024**2 + 1)
    proposal = api().preview_installed_home(root, scopes=scopes)
    assert not proposal['can_adopt']
    with pytest.raises(ValueError): apply(root, scopes, proposal)
    assert not (root / OWNER_FILE).exists() and not (root / '.installed-home-adoption').exists()


def test_unqualified_platform_cannot_preview_or_create_adoption_state(tmp_path, monkeypatch):
    root, scopes, _ = installed(tmp_path)
    monkeypatch.setattr(sys, 'platform', 'win32')
    with pytest.raises(ValueError): api().preview_installed_home(root, scopes=scopes)
    assert not (root / OWNER_FILE).exists() and not (root / '.installed-home-adoption').exists()


def test_malformed_foreign_owner_refuses_without_replacement_or_attribute_error(tmp_path):
    root, scopes, _ = installed(tmp_path)
    proposal = api().preview_installed_home(root, scopes=scopes)
    foreign = b'["foreign-owner"]'; (root / OWNER_FILE).write_bytes(foreign)
    with pytest.raises(ValueError): apply(root, scopes, proposal)
    assert (root / OWNER_FILE).read_bytes() == foreign


def test_published_retry_rechecks_owner_changed_during_backup_validation(tmp_path, monkeypatch):
    root, scopes, _ = installed(tmp_path)
    module = api(); proposal = module.preview_installed_home(root, scopes=scopes)
    apply(root, scopes, proposal); before = original_bytes(root)
    original = module._backup; foreign = b'{"foreign":"preserved"}'
    def change(*args, **kwargs):
        result = original(*args, **kwargs)
        # Replace the published name; the retained prepared descriptor remains
        # intact, so the retry must recheck the actual root owner as well.
        temporary = root / 'foreign-owner'; temporary.write_bytes(foreign)
        temporary.replace(root / OWNER_FILE)
        return result
    monkeypatch.setattr(module, '_backup', change)
    with pytest.raises(ValueError): apply(root, scopes, proposal)
    assert (root / OWNER_FILE).read_bytes() == foreign and original_bytes(root) == before


def test_linked_backup_root_cannot_write_foreign_directory_during_retry(tmp_path):
    root, scopes, _ = installed(tmp_path)
    proposal = api().preview_installed_home(root, scopes=scopes)
    result = apply(root, scopes, proposal)
    backup = Path(result['backup_path']); backup.rename(tmp_path / 'retained-original-backup')
    foreign = tmp_path / 'foreign'; foreign.mkdir(mode=0o700)
    (foreign / 'marker').write_bytes(b'foreign-preserved')
    backup.symlink_to(foreign, target_is_directory=True)
    with pytest.raises(ValueError): apply(root, scopes, proposal)
    assert sorted(p.relative_to(foreign).as_posix() for p in foreign.rglob('*')) == ['marker']
    assert (foreign / 'marker').read_bytes() == b'foreign-preserved'
