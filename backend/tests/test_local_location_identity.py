"""Durable location identity never infers legacy volume continuity."""
import copy
import importlib
import os
from pathlib import Path
import struct
import sys

import pytest


def module():
    # Keep the first RED an ordinary assertion about the missing provider.
    try:
        return importlib.import_module('backend.local_host.location_identity')
    except ModuleNotFoundError:
        pytest.fail('the durable location identity provider is not implemented')


def common_buffer(*, mask=0x8200020c, fsid=(71, 92), kind=2,
                  created=(1700000000, 123456789), file_id=18446744073709551614):
    return struct.pack('=I5I2iIqqQ', 60, mask, 0, 0, 0, 0,
                       *fsid, kind, *created, file_id)


def volume_buffer(*, returned=0x40060000, capability=0x20001,
                  capability_valid=0x20001, uuid_bytes=bytes(range(1, 17)),
                  supported_common=0x0200020c, supported_volume=0x40060000):
    return (struct.pack('=I5I8I', 112, 0x80000000, returned, 0, 0, 0,
                        capability, 0, 0, 0, capability_valid, 0, 0, 0)
            + uuid_bytes + struct.pack('=10I', supported_common, supported_volume,
                                      0, 0, 0, supported_common, supported_volume, 0, 0, 0))


def darwin_metadata(monkeypatch, tmp_path, *, common=None, volume=None):
    value = module()
    mount = tmp_path.resolve()
    monkeypatch.setattr(value, '_platform', 'darwin')
    monkeypatch.setattr(value, '_filesystem', lambda fd: {'fsid': (71, 92), 'mount': str(mount)})
    monkeypatch.setattr(value, '_get_attributes',
                        lambda fd, common_mask, volume_mask=0:
                        volume if volume_mask else common)
    return value


def test_legacy_dev_only_change_is_refused_without_upgrading_or_writing(tmp_path):
    value = module()
    project = tmp_path / 'project'; project.mkdir()
    manifest = project / 'project.json'; manifest.write_bytes(b'{"id":"preserved"}')
    info = project.stat()
    legacy = [info.st_dev + 1, info.st_ino]
    assert value.matches(legacy, project) is False
    assert legacy == [info.st_dev + 1, info.st_ino]
    assert manifest.read_bytes() == b'{"id":"preserved"}'
    assert value.matches([info.st_dev, info.st_ino], project) is True


@pytest.mark.parametrize('platform', ['linux', 'win32'])
def test_other_platforms_keep_legacy_pair(monkeypatch, tmp_path, platform):
    value = module(); monkeypatch.setattr(value, '_platform', platform)
    info = tmp_path.stat()
    assert value.read(tmp_path) == [info.st_dev, info.st_ino]


def test_v2_retains_full_file_id_and_ignores_only_device_renumbering(monkeypatch, tmp_path):
    value = darwin_metadata(monkeypatch, tmp_path,
                            common=common_buffer(), volume=volume_buffer())
    expected = {'version': 2, 'provider': 'darwin-getattrlist-v2',
                'volume_uuid': '01020304-0506-0708-090a-0b0c0d0e0f10',
                'file_id': '18446744073709551614', 'kind': 'directory',
                'created': [1700000000, 123456789]}
    assert value.read(tmp_path) == expected
    original_fstat = os.fstat
    def renumbered(fd):
        info = original_fstat(fd)
        class ChangedDevice:
            st_dev = info.st_dev + 1
            st_ino = info.st_ino
            st_mode = info.st_mode
        return ChangedDevice()
    monkeypatch.setattr(os, 'fstat', renumbered)
    assert value.matches(expected, tmp_path) is True


@pytest.mark.parametrize('field,replacement', [
    ('volume_uuid', '11111111-1111-1111-1111-111111111111'),
    ('file_id', '18446744073709551613'), ('kind', 'regular'),
    ('created', [1700000000, 123456788]),
])
def test_v2_rejects_other_volume_object_kind_or_creation(monkeypatch, tmp_path, field, replacement):
    value = darwin_metadata(monkeypatch, tmp_path,
                            common=common_buffer(), volume=volume_buffer())
    stored = value.read(tmp_path); stored[field] = replacement
    assert value.matches(stored, tmp_path) is False


@pytest.mark.parametrize('bad', [
    [True, 2], [1, False], [-1, 2], [1], [1, 2, 3], (1, 2), None,
    {'version': 3}, {'version': 2, 'provider': 'unknown'},
])
def test_invalid_legacy_or_schema_cannot_authorize_a_path(tmp_path, bad):
    assert module().matches(bad, tmp_path) is False


@pytest.mark.parametrize('field,replacement', [
    ('version', True), ('version', 2.0), ('provider', 'darwin-getattrlist-v1'),
    ('volume_uuid', '00000000-0000-0000-0000-000000000000'),
    ('volume_uuid', '0102030405060708090a0b0c0d0e0f10'),
    ('file_id', 18446744073709551614), ('file_id', '0'),
    ('file_id', '018446744073709551614'), ('file_id', '18446744073709551616'),
    ('created', [True, 0]), ('created', [1700000000, 1000000000]),
    ('created', [1700000000]), ('created', None), ('extra', 'unrecognized'),
])
def test_strict_v2_parser_refuses_missing_or_malformed_proof(monkeypatch, tmp_path, field, replacement):
    value = darwin_metadata(monkeypatch, tmp_path,
                            common=common_buffer(), volume=volume_buffer())
    stored = value.read(tmp_path); stored[field] = replacement
    assert value.matches(stored, tmp_path) is False


@pytest.mark.parametrize('common,volume', [
    (common_buffer()[:-1], volume_buffer()),
    (common_buffer(mask=0x8000020c), volume_buffer()),
    (common_buffer(kind=1), volume_buffer()),
    (common_buffer(file_id=0), volume_buffer()),
    (common_buffer(created=(1700000000, 1000000000)), volume_buffer()),
    (common_buffer(fsid=(72, 92)), volume_buffer()),
    (common_buffer(), volume_buffer()[:-1]),
    (common_buffer(), volume_buffer(returned=0x40020000)),
    (common_buffer(), volume_buffer(capability=0x20000)),
    (common_buffer(), volume_buffer(capability_valid=0x20000)),
    (common_buffer(), volume_buffer(uuid_bytes=bytes(16))),
    (common_buffer(), volume_buffer(supported_common=0x20c)),
    (common_buffer(), volume_buffer(supported_volume=0x40020000)),
])
def test_capture_refuses_truncated_unsupported_or_unbound_metadata(monkeypatch, tmp_path, common, volume):
    value = darwin_metadata(monkeypatch, tmp_path, common=common, volume=volume)
    with pytest.raises((ValueError, OSError)):
        value.read(tmp_path)


@pytest.mark.parametrize('linked', ['leaf', 'ancestor'])
def test_directory_open_refuses_every_symlink_component(tmp_path, linked):
    value = module()
    root = tmp_path.resolve()
    target = root / 'target'; target.mkdir()
    child = target / 'child'; child.mkdir()
    alias = root / 'alias'; alias.symlink_to(target, target_is_directory=True)
    selected = alias if linked == 'leaf' else alias / 'child'
    with pytest.raises((ValueError, OSError)):
        with value.open_directory(selected):
            pytest.fail('a linked directory was opened')


def test_read_refuses_path_replacement_while_descriptor_is_held(monkeypatch, tmp_path):
    root = tmp_path.resolve(); project = root / 'project'; project.mkdir()
    value = darwin_metadata(monkeypatch, root,
                            common=common_buffer(), volume=volume_buffer())
    original = value._get_attributes
    replaced = False
    def replace_during_capture(fd, common_mask, volume_mask=0):
        nonlocal replaced
        if not replaced:
            project.rename(root / 'old-project'); project.mkdir(); replaced = True
        return original(fd, common_mask, volume_mask)
    monkeypatch.setattr(value, '_get_attributes', replace_during_capture)
    with pytest.raises((ValueError, OSError)):
        value.read(project)
    assert replaced


def test_directory_context_closes_its_descriptor(tmp_path):
    value = module()
    with value.open_directory(tmp_path.resolve()) as descriptor:
        assert os.fstat(descriptor).st_ino == tmp_path.stat().st_ino
    with pytest.raises(OSError):
        os.fstat(descriptor)


@pytest.mark.skipif(sys.platform != 'darwin', reason='Darwin metadata integration')
def test_real_darwin_metadata_on_disposable_directory(tmp_path):
    value = module()
    project = tmp_path.resolve() / 'project'; project.mkdir()
    manifest = project / 'project.json'; manifest.write_bytes(b'{"id":"disposable"}')
    first = value.read(project)
    assert first['version'] == 2
    assert first['provider'] == 'darwin-getattrlist-v2'
    assert int(first['file_id']) == project.stat().st_ino
    assert first['kind'] == 'directory'
    assert value.matches(first, project) is True
    replacement = copy.deepcopy(first); replacement['file_id'] = str(int(first['file_id']) + 1)
    assert value.matches(replacement, project) is False
    assert manifest.read_bytes() == b'{"id":"disposable"}'
