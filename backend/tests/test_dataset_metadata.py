"""Operator metadata persists with optimistic review and source identity."""
import json
from pathlib import Path
import pytest
from PIL import Image
from backend.engine import dataset_metadata as dm

@pytest.fixture
def workspace(tmp_path):
    source = tmp_path / 'source'; source.mkdir()
    Image.new('RGB', (100, 80), 'white').save(source / 'a.png')
    Image.new('RGB', (100, 80), 'black').save(source / 'b.png')
    project = tmp_path / 'project'; project.mkdir()
    return project, source

def test_identity_survives_reload_and_is_project_scoped(workspace, tmp_path):
    project, source = workspace
    first = dm.metadata_for_path(project, source, source/'a.png')
    assert first['image_uuid'] == dm.metadata_for_path(project, source, source/'a.png')['image_uuid']
    assert first['image_uuid'] != dm.metadata_for_path(tmp_path/'other', source, source/'a.png')['image_uuid']
    Image.new('RGB', (100,80), 'red').save(source/'a.png')
    changed = dm.metadata_for_path(project, source, source/'a.png')
    assert changed['image_uuid'] == first['image_uuid']
    assert changed['content_version'] == 2
    assert changed['content_hash'] != first['content_hash']

def test_stale_edit_and_annotation_changes_invalidate_approval(workspace):
    project, source = workspace
    first = dm.metadata_for_path(project, source, source/'a.png')
    approved = dm.update_metadata(project, source, first['image_uuid'], first['revision'], 'reviewer Kim',
                                  {'tags':['scratch','scratch'], 'product':'P1', 'workflow_state':'approved'})
    assert approved['reviewer'] == 'reviewer Kim'
    assert approved['tags'] == ['scratch']
    assert approved['review_history'][-1]['state'] == 'approved'
    with pytest.raises(dm.RevisionConflict):
        dm.update_metadata(project, source, first['image_uuid'], first['revision'], 'Lee', {'lot':'L2'})
    changed = dm.annotation_changed(project, source, source/'a.png', 'operator Lee')
    assert changed['workflow_state'] == 'needs_review'
    assert changed['reviewer'] is None
    assert changed['audit'][-1]['actor'] == 'operator Lee'
    assert changed['audit'][-1]['action'] == 'annotation_changed'

def test_external_annotation_edit_detected_before_approval(workspace):
    project, source = workspace
    first = dm.metadata_for_path(project, source, source/'a.png')
    dm.update_metadata(project, source, first['image_uuid'], first['revision'], 'Kim', {'workflow_state':'approved'})
    (source/'a.json').write_text(json.dumps({'shapes':[{'label':'NG'}]}))
    changed = dm.metadata_for_path(project, source, source/'a.png')
    assert changed['workflow_state'] == 'needs_review'
    assert changed['audit'][-1]['action'] == 'external_annotation_changed'

def test_group_split_keeps_groups_and_duplicate_content_together(workspace):
    project, source = workspace
    (source/'copy.png').write_bytes((source/'a.png').read_bytes())
    rows = dm.list_metadata(project, source)
    for row in rows:
        dm.update_metadata(project, source, row['image_uuid'], row['revision'], 'Kim', {'lot':'L1' if row['relative_path'] == 'b.png' else 'L2'})
    preview = dm.preview_split(dm.list_metadata(project, source), ['lot'], .5, .5, 0, 7)
    assignments = preview['assignments']
    assert assignments['a.png'] == assignments['copy.png']
    assert set(assignments.values()) == {'train', 'val'}
    duplicates = dm.duplicate_leakage(dm.list_metadata(project, source), {'a.png':'train','copy.png':'val','b.png':'train'})
    assert duplicates[0]['cross_split'] is True
    assert len(duplicates[0]['images']) == 2

def test_safe_path_and_empty_actor_are_rejected(workspace, tmp_path):
    project, source = workspace
    Image.new('RGB',(2,2)).save(tmp_path/'outside.png')
    with pytest.raises(ValueError): dm.metadata_for_path(project, source, tmp_path/'outside.png')
    first = dm.metadata_for_path(project, source, source/'a.png')
    with pytest.raises(ValueError): dm.update_metadata(project, source, first['image_uuid'], first['revision'], ' ', {'product':'P'})
