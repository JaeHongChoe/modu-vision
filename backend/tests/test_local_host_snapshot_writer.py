"""Real filesystem custody regressions for the shared snapshot output writer."""
import os
from pathlib import Path
import stat

import pytest

from backend.local_host.linked_sources import SnapshotWriter


@pytest.fixture
def writer(tmp_path):
    project=tmp_path/'project';project.mkdir();info=project.stat()
    value=SnapshotWriter(project,None,[info.st_dev,info.st_ino,stat.S_IFMT(info.st_mode)])
    try:yield value
    finally:value.close()


def put(writer,name):
    fd=writer.output(name)
    try:os.write(fd,b'owned fixture');os.fchmod(fd,0o400);os.fsync(fd)
    finally:os.close(fd)


def replace_class(writer,tmp_path):
    original=tmp_path/'original-directory';original.mkdir();original.chmod(0o750)
    (original/'original.txt').write_bytes(b'outside original')
    directory=writer.payload/'class'
    directory.rename(writer.payload/'created-class');original.rename(directory)
    return original,directory


def restore_fixture(writer,original,directory):
    # Restore only the test-owned original after observing the product's result.
    directory.chmod(0o750);writer.payload.chmod(0o700);directory.rename(original)


def test_freeze_never_chmods_a_foreign_real_directory_replacing_created_output(writer,tmp_path):
    put(writer,'class/one.txt');original,directory=replace_class(writer,tmp_path)
    before=directory.stat();file_before=(directory/'original.txt').stat();refused=False
    try:
        try:writer.freeze()
        except (ValueError,OSError):refused=True
        after=directory.stat();file_after=(directory/'original.txt').stat()
        assert stat.S_IMODE(after.st_mode)==0o750, 'freeze must not chmod a foreign replacement inode'
        assert (after.st_ino,after.st_mtime_ns,after.st_ctime_ns)==(before.st_ino,before.st_mtime_ns,before.st_ctime_ns)
        assert file_after==file_before and (directory/'original.txt').read_bytes()==b'outside original'
        assert refused, 'an unknown replacement directory must never publish as an owned snapshot'
    finally:restore_fixture(writer,original,directory)


def test_output_refuses_reentering_a_parent_replaced_by_a_foreign_directory(writer,tmp_path):
    put(writer,'class/one.txt');original,directory=replace_class(writer,tmp_path);refused=False
    try:
        try:
            fd=writer.output('class/second.txt');os.close(fd)
        except (ValueError,OSError):refused=True
        assert refused and not (directory/'second.txt').exists(), 'output must not create files inside a replacement inode'
        assert stat.S_IMODE(directory.stat().st_mode)==0o750
    finally:restore_fixture(writer,original,directory)


def test_output_refuses_an_existing_directory_it_did_not_create(writer):
    directory=writer.payload/'foreign';directory.mkdir();directory.chmod(0o750)
    with pytest.raises((ValueError,OSError)):
        fd=writer.output('foreign/new.txt');os.close(fd)
    assert not (directory/'new.txt').exists() and stat.S_IMODE(directory.stat().st_mode)==0o750


def test_freeze_refuses_unknown_directory_inventory_without_changing_it(writer):
    put(writer,'class/one.txt');unknown=writer.payload/'unknown';unknown.mkdir();unknown.chmod(0o750)
    before=unknown.stat();refused=False
    try:
        try:writer.freeze()
        except (ValueError,OSError):refused=True
        after=unknown.stat()
        assert stat.S_IMODE(after.st_mode)==0o750 and after.st_ctime_ns==before.st_ctime_ns
        assert refused
    finally:writer.payload.chmod(0o700);unknown.chmod(0o750)


def test_freeze_bounds_enumeration_to_recorded_inventory_plus_first_unknown(writer,monkeypatch):
    put(writer,'class/one.txt')
    for index in range(200):(writer.payload/f'unknown-{index}').mkdir()
    scandir=os.scandir;observed=[];payload_inode=os.fstat(writer.payload_fd).st_ino
    class Observe:
        def __init__(self,fd):self.entries=scandir(fd);self.track=isinstance(fd,int) and os.fstat(fd).st_ino==payload_inode
        def __enter__(self):return self
        def __exit__(self,*args):self.entries.close()
        def __iter__(self):return self
        def __next__(self):
            entry=next(self.entries)
            if self.track:observed.append(entry.name)
            return entry
    monkeypatch.setattr(os,'scandir',Observe)
    with pytest.raises(ValueError,match='inventory'):writer.freeze()
    assert 0<len(observed)<=2, 'freeze must stop on the first unknown child, without collecting all foreign entries'
