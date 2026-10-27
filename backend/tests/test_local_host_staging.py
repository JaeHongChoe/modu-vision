import hashlib
import pytest


def test_import_snapshot_preserves_original_and_uses_project_owned_copy(tmp_path):
    from backend.local_host.staging import stage_selection
    project = tmp_path / 'project'; project.mkdir()
    source = tmp_path / 'labels'; source.mkdir()
    (source / 'label.json').write_bytes(b'{"label":"scratch"}')
    before = hashlib.sha256((source / 'label.json').read_bytes()).hexdigest()
    staged = stage_selection(source, project, 'label-import')
    assert staged.is_relative_to(project)
    assert staged != source and (staged / 'label.json').read_bytes() == b'{"label":"scratch"}'
    assert hashlib.sha256((source / 'label.json').read_bytes()).hexdigest() == before
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
