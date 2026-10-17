"""Schema inspection in a staged worker must not borrow its installer's lock."""
import json
import os
from pathlib import Path
import subprocess
import sys

from backend.engine import global_migration as migration
from backend.engine.global_store_paths import SUPPORTED_SCOPES, store_admission


def test_schema_scratch_under_owned_worker_tmp_preserves_installer_exclusion(tmp_path):
    expected = migration._known_schemas(SUPPORTED_SCOPES)
    root = tmp_path / 'installation'
    root.mkdir(mode=0o700)
    migration.initialize_owned(root, scopes=SUPPORTED_SCOPES)
    private_tmp = root / '.application-updates' / 'private' / 'tmp'
    private_tmp.mkdir(parents=True, mode=0o700)
    owner = (root / migration.OWNER_FILE).read_bytes()
    child_code = '''
import json, sys, tempfile
from pathlib import Path
from backend.engine import global_migration as migration
from backend.engine.global_store_paths import SUPPORTED_SCOPES, store_admission
root, private_tmp = map(Path, sys.argv[1:])
assert Path(tempfile.gettempdir()) == private_tmp
def writer_stays_excluded():
    try:
        with store_admission(root):
            raise AssertionError('A child acquired the original installer admission')
    except ValueError as error:
        assert 'hold maintenance admission' in str(error), str(error)
writer_stays_excluded()
schemas = migration._known_schemas(SUPPORTED_SCOPES)
writer_stays_excluded()
assert not list(private_tmp.iterdir()), 'Schema scratch escaped cleanup'
print(json.dumps(schemas, sort_keys=True))
'''
    environment = dict(os.environ, TMPDIR=str(private_tmp), TMP=str(private_tmp),
                       TEMP=str(private_tmp), PYTHONDONTWRITEBYTECODE='1')
    with store_admission(root, exclusive=True):
        child = subprocess.run(
            [sys.executable, '-B', '-c', child_code, str(root), str(private_tmp)],
            cwd=Path(__file__).resolve().parents[2], env=environment,
            stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=30,
        )
        assert child.returncode == 0, child.stderr
        assert json.loads(child.stdout) == expected
        assert (root / migration.OWNER_FILE).read_bytes() == owner
        assert not list(private_tmp.iterdir())
        assert not (root / 'global-active.json').exists()
        assert not (root / 'application-active.json').exists()
        assert not (root / 'application-update-pending.json').exists()
