import hashlib
import pytest


def test_import_snapshot_preserves_original_and_uses_project_owned_copy(tmp_path):
    import json
    import stat
    from backend.local_host.staging import stage_selection
    project = tmp_path / 'project'; project.mkdir()
    source = tmp_path / 'labels'; source.mkdir()
    (source / 'label.json').write_bytes(b'{"label":"scratch"}')
    (source / 'label.json').chmod(0o640)
    before = hashlib.sha256((source / 'label.json').read_bytes()).hexdigest()
    staged = stage_selection(source, project, 'label-import')
    assert staged.is_relative_to(project)
    assert staged != source and (staged / 'label.json').read_bytes() == b'{"label":"scratch"}'
    assert hashlib.sha256((source / 'label.json').read_bytes()).hexdigest() == before
    assert stat.S_IMODE((source / 'label.json').stat().st_mode)==0o640
    assert stat.S_IMODE((staged / 'label.json').stat().st_mode)==0o400
    assert stat.S_IMODE(staged.stat().st_mode)==0o500
    assert staged.parent.parent==project/'.local-host-imports'
    assert json.loads((staged.parent/'snapshot.json').read_text())=={
        'schema_version':1,'state':'complete','purpose':'label-import',
        'files':[{'path':'label.json','bytes':19,'sha256':before}]}
    (source / 'label.json').write_text('changed')
    assert (staged / 'label.json').read_bytes() == b'{"label":"scratch"}'


def test_linked_or_oversized_snapshot_is_rejected_without_deleting_source(tmp_path):
    from backend.local_host.staging import stage_selection
    project = tmp_path / 'project'; project.mkdir()
    source = tmp_path / 'source'; source.mkdir()
    (source / 'data.json').write_text('longer than limit')
    with pytest.raises(ValueError): stage_selection(source, project, 'label-import', max_bytes=2)
    assert (source / 'data.json').read_text() == 'longer than limit'
    (source / 'link.json').symlink_to(source / 'data.json')
    with pytest.raises(ValueError): stage_selection(source, project, 'label-import')


def test_input_inside_project_is_still_snapshotted_and_secret_ancestor_denied(tmp_path):
    from backend.local_host.staging import stage_selection
    project = tmp_path / 'project'; project.mkdir()
    source = project / 'input.txt'; source.write_text('original')
    assert stage_selection(source, project, 'pretrained-weights').read_text() == 'original'
    private = tmp_path / 'auth'; private.mkdir()
    with pytest.raises(ValueError): stage_selection(tmp_path, project, 'label-import', protected_roots=[private])


def test_directory_nodes_exhaust_the_snapshot_bound_even_with_few_small_files(tmp_path):
    from backend.local_host.staging import stage_selection
    project=tmp_path/'project';project.mkdir()
    source=tmp_path/'source';source.mkdir()
    for index in range(200):(source/f'empty-{index:03d}').mkdir()
    (source/'one.txt').write_bytes(b'one');(source/'two.txt').write_bytes(b'two')
    with pytest.raises(ValueError,match='path'):
        stage_selection(source,project,'label-import',max_files=2,max_bytes=64,max_nodes=16)
    assert not (project/'.local-host-imports').exists()
    assert (source/'one.txt').read_bytes()==b'one'
    assert len(list(source.iterdir()))==202


def test_snapshot_stops_directory_enumeration_at_the_remaining_node_budget(tmp_path,monkeypatch):
    import os
    from backend.local_host.staging import stage_selection
    project=tmp_path/'project';project.mkdir()
    source=tmp_path/'source';source.mkdir()
    for index in range(200):(source/f'empty-{index:03d}').mkdir()
    real_scandir=os.scandir;observed=[]
    class BoundedObservation:
        def __init__(self,path):self.entries=real_scandir(path)
        def __enter__(self):return self
        def __exit__(self,*args):self.entries.close()
        def __iter__(self):return self
        def __next__(self):
            entry=next(self.entries);observed.append(entry.name)
            assert len(observed)<=16, 'snapshot eagerly enumerated beyond the bounded refusal'
            return entry
    monkeypatch.setattr(os,'scandir',BoundedObservation)
    with pytest.raises(ValueError,match='path'):
        stage_selection(source,project,'label-import',max_nodes=16)
    assert len(observed)==16
    assert not (project/'.local-host-imports').exists()


def test_snapshot_small_tree_and_single_file_keep_original_bytes_at_exact_node_bound(tmp_path):
    from backend.local_host.staging import stage_selection
    project=tmp_path/'project';project.mkdir()
    source=tmp_path/'source';source.mkdir()
    (source/'class-a').mkdir();(source/'class-b').mkdir()
    first=source/'class-a'/'one.txt';first.write_bytes(b'one')
    second=source/'class-b'/'two.txt';second.write_bytes(b'two')
    staged=stage_selection(source,project,'label-import',max_files=2,max_bytes=6,max_nodes=5)
    assert (staged/'class-a'/'one.txt').read_bytes()==first.read_bytes()==b'one'
    assert (staged/'class-b'/'two.txt').read_bytes()==second.read_bytes()==b'two'
    assert stage_selection(first,project,'pretrained-weights',max_nodes=1).read_bytes()==b'one'
    with pytest.raises(ValueError,match='path'):
        stage_selection(source,project,'label-import',max_nodes=4)


@pytest.mark.parametrize('kind',['directory-link','hardlink','fifo'])
def test_bounded_enumeration_still_refuses_links_and_special_entries_before_copy(tmp_path,kind):
    import os
    from backend.local_host.staging import stage_selection
    project=tmp_path/'project';project.mkdir()
    source=tmp_path/'source';source.mkdir()
    outside=tmp_path/'outside';outside.mkdir()
    original=outside/'original.txt';original.write_bytes(b'preserved original')
    if kind=='directory-link':(source/'entry').symlink_to(outside,target_is_directory=True)
    elif kind=='hardlink':os.link(original,source/'entry')
    else:
        if not hasattr(os,'mkfifo'):pytest.skip('Named pipe fixture requires POSIX')
        os.mkfifo(source/'entry')
    with pytest.raises(ValueError):stage_selection(source,project,'label-import',max_nodes=5)
    assert original.read_bytes()==b'preserved original'
    assert not (project/'.local-host-imports').exists()


@pytest.mark.parametrize('link_kind',['hardlink','symlink'])
def test_normal_snapshot_receipt_cannot_overwrite_a_foreign_original(tmp_path,monkeypatch,link_kind):
    import json
    import os
    import stat
    from backend.local_host.staging import stage_selection
    project=tmp_path/'project';project.mkdir();source=tmp_path/'source.txt';source.write_bytes(b'input fixture')
    outside=tmp_path/'foreign-original.txt';before=b'outside original must survive receipt publication';outside.write_bytes(before);outside.chmod(0o640)
    mkdir=os.mkdir;inserted=[]
    def install_foreign_receipt(path,*args,**kwargs):
        result=mkdir(path,*args,**kwargs)
        if str(path).split('/')[-1]=='payload' and not inserted:
            destination=next((project/'.local-host-imports').iterdir());receipt=destination/'snapshot.json'
            if link_kind=='hardlink':os.link(outside,receipt)
            else:receipt.symlink_to(outside)
            inserted.append(receipt)
        return result
    monkeypatch.setattr(os,'mkdir',install_foreign_receipt)
    staged=stage_selection(source,project,'label-import')
    assert inserted and outside.read_bytes()==before
    assert stat.S_IMODE(outside.stat().st_mode)==0o640
    assert staged.read_bytes()==b'input fixture' and source.read_bytes()==b'input fixture'
    receipt=inserted[0];assert not receipt.is_symlink() and receipt.stat().st_ino!=outside.stat().st_ino
    assert json.loads(receipt.read_text())['state']=='complete'


@pytest.mark.parametrize('replacement',['file','directory'])
def test_normal_snapshot_chmod_never_follows_a_substituted_output_link(tmp_path,monkeypatch,replacement):
    import json
    import os
    import stat
    from backend.local_host.staging import stage_selection
    project=tmp_path/'project';project.mkdir();source=tmp_path/'source';(source/'class').mkdir(parents=True)
    (source/'class/image.txt').write_bytes(b'input fixture')
    outside=tmp_path/'foreign';outside.mkdir();original=outside/'image.txt';original.write_bytes(b'outside fixture')
    outside.chmod(0o750);original.chmod(0o640);fsync=os.fsync;replaced=[]
    def swap_before_path_permissions(fd):
        candidates=list((project/'.local-host-imports').glob('*/payload/class/image.txt'))
        if candidates and not replaced and os.fstat(fd).st_ino==candidates[0].lstat().st_ino:
            target=candidates[0]
            if replacement=='file':target.unlink();target.symlink_to(original)
            else:
                target.parent.rename(target.parent.with_name('owned-class'));target.parent.symlink_to(outside,target_is_directory=True)
            replaced.append(True)
        return fsync(fd)
    monkeypatch.setattr(os,'fsync',swap_before_path_permissions)
    try:stage_selection(source,project,'label-import')
    except (ValueError,OSError):pass
    assert replaced, 'the real output was substituted at the filesystem sync boundary'
    assert original.read_bytes()==b'outside fixture' and stat.S_IMODE(original.stat().st_mode)==0o640
    assert stat.S_IMODE(outside.stat().st_mode)==0o750
    assert (source/'class/image.txt').read_bytes()==b'input fixture'
    receipts=list((project/'.local-host-imports').glob('*/snapshot.json'));assert len(receipts)==1
    assert json.loads(receipts[0].read_text())['state']=='failed'
