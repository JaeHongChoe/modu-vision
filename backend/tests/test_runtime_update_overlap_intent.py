"""Root-run real signed-file probes for tentative invocation-local overlap.

No application/model process is admitted. The existing fixture retains the real
signed archive, installed files, transition OFD and exact stdlib worker guard;
OS parent/executable identities are its declared models. This author has not
executed this module. Injected errors below identify their modeled boundaries.
"""
import concurrent.futures.thread as pool_module
import threading
import zipfile

import pytest

from backend.engine import application_launch_lease as lease
from backend.engine import runtime_update as update
from backend.tests.test_application_backend_intent_composition import original
from backend.tests.test_service_s6_04 import controlled_preactivation_guard


def admitted(original):
    root, record, _, _, _, _ = original
    return root, record, lease._transition_admission(root, record['nonce'])


def first_row(root, record):
    path = root/update.GENERATIONS/record['binding']['application_generation']/'application'/'portable-application.json'
    return update._json(update._read(path, update.MAX_APPLICATION_MANIFEST))['files'][0]


def test_real_zip_member_can_wait_for_original_installed_file_check(original, monkeypatch):
    root, record, guard = admitted(original)
    row = first_row(root, record)
    installed_started = threading.Event()
    original_read = zipfile.ZipExtFile.read
    original_check = update._check_file
    seen = []
    def read(member, *args, **kwargs):
        if member.name == row['path']:
            assert installed_started.wait(2), 'Original serial ZIP barrier prevents installed work'
            seen.append('zip')
        return original_read(member, *args, **kwargs)
    def check(path, current, *, copy_to=None):
        if '/application/' in str(path) and current['path'] == row['path']:
            seen.append('installed')
            installed_started.set()
        return original_check(path, current, copy_to=copy_to)
    monkeypatch.setattr(zipfile.ZipExtFile, 'read', read)
    monkeypatch.setattr(update, '_check_file', check)
    with guard:
        _, _, manifest = update._validated_intent(root, record['binding']['update_id'])
    assert seen[0] == 'installed' and 'zip' in seen
    assert manifest['files'][0] == row


@pytest.mark.parametrize('kind', ['size', 'checksum'])
def test_real_installed_byte_guards_still_refuse(original, kind):
    root, record, guard = admitted(original)
    row = first_row(root, record)
    path = root/update.GENERATIONS/record['binding']['application_generation']/'application'/row['path']
    path.chmod(0o600)
    raw = path.read_bytes()
    assert raw
    changed = raw + b'changed' if kind == 'size' else raw[:-1] + bytes([raw[-1] ^ 1])
    path.write_bytes(changed)
    path.chmod(0o500 if row['executable'] else 0o400)
    with guard, pytest.raises(update.UpdateError, match='artifact ' + kind + ' differs'):
        update._validated_intent(root, record['binding']['update_id'])


def test_archive_domain_error_object_precedes_installed_domain_error(original, monkeypatch):
    root, record, guard = admitted(original)
    row = first_row(root, record)
    archive_error = OSError('Exact modeled archive-member boundary')
    installed_error = OSError('Exact modeled installed-file boundary')
    original_read = zipfile.ZipExtFile.read
    original_check = update._check_file
    observed = []
    def read(member, *args, **kwargs):
        value = original_read(member, *args, **kwargs)
        if member.name == row['path']:
            observed.append('archive')
            raise archive_error
        return value
    def check(path, current, *, copy_to=None):
        value = original_check(path, current, copy_to=copy_to)
        if '/application/' in str(path) and current['path'] == row['path']:
            observed.append('installed')
            raise installed_error
        return value
    monkeypatch.setattr(zipfile.ZipExtFile, 'read', read)
    monkeypatch.setattr(update, '_check_file', check)
    with guard, pytest.raises(OSError) as caught:
        update._validated_intent(root, record['binding']['update_id'])
    assert caught.value is archive_error
    assert set(observed) == {'archive', 'installed'}


def test_archive_error_still_precedes_installed_pre_scan_error(original, monkeypatch):
    root, record, guard = admitted(original)
    row = first_row(root, record)
    archive_error = OSError('Exact modeled original ZIP member')
    namespace_error = OSError('Exact modeled original pre-scan')
    original_read = zipfile.ZipExtFile.read
    original_namespace = update._intent_parallel_namespace
    original_check = update._check_file
    installed = []
    def read(member, *args, **kwargs):
        value = original_read(member, *args, **kwargs)
        if member.name == row['path']:
            raise archive_error
        return value
    def namespace(executor, application, manifest, retained, **kwargs):
        original_namespace(executor, application, manifest, retained, **kwargs)
        raise namespace_error
    def check(path, current, *, copy_to=None):
        if '/application/' in str(path): installed.append(path)
        return original_check(path, current, copy_to=copy_to)
    monkeypatch.setattr(zipfile.ZipExtFile, 'read', read)
    monkeypatch.setattr(update, '_intent_parallel_namespace', namespace)
    monkeypatch.setattr(update, '_check_file', check)
    with guard, pytest.raises(OSError) as caught:
        update._validated_intent(root, record['binding']['update_id'])
    assert caught.value is archive_error and installed == []


def test_namespace_post_scan_is_fresh_after_all_installed_checks(original, monkeypatch):
    root, record, guard = admitted(original)
    application = root/update.GENERATIONS/record['binding']['application_generation']/'application'
    original_namespace = update._intent_parallel_namespace
    scans = []
    def namespace(executor, path, manifest, retained, **kwargs):
        scans.append(path)
        if len(scans) == 2:
            (application/'unexpected-member').write_bytes(b'foreign current member')
        return original_namespace(executor, path, manifest, retained, **kwargs)
    monkeypatch.setattr(update, '_intent_parallel_namespace', namespace)
    with guard, pytest.raises(update.UpdateError, match='membership differs'):
        update._validated_intent(root, record['binding']['update_id'])
    assert scans == [application, application]


@pytest.mark.parametrize('kind', [KeyboardInterrupt, SystemExit])
@pytest.mark.parametrize('hidden', [False, True])
def test_submit_caller_interruption_keeps_exact_object_and_all_work_done(original, monkeypatch, kind, hidden):
    root, record, guard = admitted(original)
    error = kind('Exact original submit caller interruption')
    submit = pool_module.ThreadPoolExecutor.submit
    captured = []
    calls = []
    def interrupted(executor, function, *args, **kwargs):
        ordinal = len(calls)
        calls.append(function)
        if ordinal == 3 and not hidden:
            raise error
        future = submit(executor, function, *args, **kwargs)
        captured.append(future)
        if ordinal == 3:
            raise error
        return future
    monkeypatch.setattr(pool_module.ThreadPoolExecutor, 'submit', interrupted)
    with guard, pytest.raises(kind) as caught:
        update._validated_intent(root, record['binding']['update_id'])
    assert caught.value is error
    assert captured and all(future.done() for future in captured)
    assert len(calls) == 4 and len(captured) == 3 + int(hidden)



def test_original_bundle_refusal_precedes_bad_archive_open(original, monkeypatch):
    root, record, guard = admitted(original)
    identifier = record['binding']['update_id']
    current, directory = update._intent(root, identifier)
    installer = next(row for row in current['release']['artifacts'] if row['kind'] == 'installer')
    archive = directory/'bundle'/installer['path']
    raw = archive.read_bytes()
    # Same length, wrong signed SHA and actual invalid ZIP header. Both original
    # validators refuse; the exact original raw-bundle exception must win.
    archive.chmod(0o600)
    archive.write_bytes(b'\0' * len(raw))
    archive.chmod(0o400)
    original_bundle = update._bundle
    failures = []
    def checked_bundle(*args, **kwargs):
        try:
            return original_bundle(*args, **kwargs)
        except BaseException as error:
            failures.append(error)
            raise
    monkeypatch.setattr(update, '_bundle', checked_bundle)
    with guard, pytest.raises(update.UpdateError, match='artifact checksum differs') as caught:
        update._validated_intent(root, identifier)
    assert failures == [caught.value]


def test_original_zip_error_precedes_later_ordinary_submit_refusal(original, monkeypatch):
    root, record, guard = admitted(original)
    row = first_row(root, record)
    archive_error = OSError('Exact earlier original ZIP row error')
    submission_error = RuntimeError('Exact later ordinary submission error')
    original_read = zipfile.ZipExtFile.read
    original_submit = pool_module.ThreadPoolExecutor.submit
    calls = []; captured = []
    def read(member, *args, **kwargs):
        value = original_read(member, *args, **kwargs)
        if member.name == row['path']:
            raise archive_error
        return value
    def submit(executor, function, *args, **kwargs):
        ordinal = len(calls); calls.append(function)
        if ordinal == 3:
            raise submission_error
        future = original_submit(executor, function, *args, **kwargs)
        captured.append(future)
        return future
    monkeypatch.setattr(zipfile.ZipExtFile, 'read', read)
    monkeypatch.setattr(pool_module.ThreadPoolExecutor, 'submit', submit)
    with guard, pytest.raises(OSError) as caught:
        update._validated_intent(root, record['binding']['update_id'])
    assert caught.value is archive_error
    assert captured and all(future.done() for future in captured)
    assert len(calls) == 4 and len(captured) == 3
