"""Behavior checks for shared guidance, edit ownership and two-person review."""
import importlib
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from PIL import Image

from backend.engine import dataset_metadata as dm
from backend.engine.annotation_storage import (
    set_request_annotation_root, reset_request_annotation_root,
    set_request_project_root, reset_request_project_root,
)


def team():
    assert importlib.util.find_spec('backend.engine.team_data') is not None, 'Team-data contract is not implemented'
    return importlib.import_module('backend.engine.team_data')


@pytest.fixture
def workspace(tmp_path):
    source = tmp_path / 'source'; source.mkdir()
    Image.new('RGB', (40, 30), 'white').save(source / 'a.png')
    Image.new('RGB', (40, 30), 'black').save(source / 'b.png')
    root = tmp_path / 'project'; root.mkdir()
    project = {'id': 'project-test', 'task': 'classification', 'project_dir': str(root),
               'source_dataset_dir': str(source), 'active_labelset_id': 'default',
               'annotations_dir': str(root / 'annotations'), 'dataset_dir': str(root / 'dataset'),
               'models_dir': str(root / 'models'), 'reports_dir': str(root / 'reports')}
    (root / 'project.json').write_text(json.dumps(project))
    a = set_request_annotation_root(root / 'annotations'); p = set_request_project_root(root)
    try:
        yield project, source
    finally:
        reset_request_project_root(p); reset_request_annotation_root(a)


def categories(caption='Reference scratch'):
    return [{'id': 0, 'name': 'OK', 'color': '#10b981', 'definition': 'No defect',
             'inclusion': '', 'exclusion': '', 'annotation_guidance': '', 'examples': []},
            {'id': 1, 'name': 'Scratch', 'color': '#f59e0b', 'definition': 'Surface scratch',
             'inclusion': 'Visible scratch', 'exclusion': 'Reflection',
             'annotation_guidance': 'Outline the visible region',
             'examples': [{'relative_path': 'a.png', 'caption': caption}]}]


def configure(project, source, **changes):
    td = team(); state = td.workspace(project, source)
    return td.update_settings(project, source, state['settings']['revision'], 'owner', changes)


def image(project, source, name='a.png'):
    return team().image_state(project, source, source / name)


def save(project, source, actor='labeler', lease_token=None, revision=None):
    from backend.api.routes_annotation import AnnotationSaveRequest, save_annotations
    row = image(project, source)
    return save_annotations(AnnotationSaveRequest(image_id='a', image_path=str(source / 'a.png'),
        image_width=40, image_height=30, actor=actor,
        expected_revision=revision if revision is not None else row['revision'], lease_token=lease_token,
        annotations=[{'id':'scratch', 'type':'bbox', 'label':'Scratch','category_id':1,'bbox':[2,3,10,12]}]))


def test_book_versions_preserve_previous_guidance_and_source_bytes(workspace):
    project, source = workspace; td = team(); original = (source / 'a.png').read_bytes()
    assert td.workspace(project, source)['book'] is None
    first = td.publish_book(project, source, 0, 'owner', 'Inspection guidance', categories())
    second = td.publish_book(project, source, 1, 'owner', 'Updated guidance', categories('Updated example'))
    reopened = td.workspace(project, source)
    assert second['version'] == 2 and reopened['book']['sha256'] == second['sha256']
    assert reopened['book_history'][0] == first
    assert first['categories'][1]['examples'][0]['content_hash'] == dm._hash(source / 'a.png')
    assert first['categories'][1]['examples'][0]['caption'] == 'Reference scratch'
    assert (source / 'a.png').read_bytes() == original and not (source / 'a.json').exists()
    with pytest.raises(dm.RevisionConflict): td.publish_book(project, source, 1, 'owner', 'Stale', categories())


def test_book_rejects_escape_links_duplicate_classes_and_id_reuse(workspace, tmp_path):
    project, source = workspace; td = team(); bad = categories()
    bad[1]['examples'][0]['relative_path'] = '../outside.png'
    with pytest.raises(ValueError): td.publish_book(project, source, 0, 'owner', 'Bad', bad)
    Image.new('RGB', (4,4)).save(tmp_path / 'outside.png'); (source / 'linked.png').symlink_to(tmp_path / 'outside.png')
    bad = categories(); bad[1]['examples'][0]['relative_path'] = 'linked.png'
    with pytest.raises(ValueError): td.publish_book(project, source, 0, 'owner', 'Bad', bad)
    bad = categories(); bad[1]['id'] = 0
    with pytest.raises(ValueError): td.publish_book(project, source, 0, 'owner', 'Bad', bad)
    assert td.workspace(project, source)['book'] is None
    td.publish_book(project,source,0,'owner','Stable',categories())
    renamed=categories();renamed[1]['name']='Different defect'
    with pytest.raises(ValueError):td.publish_book(project,source,1,'owner','Changed',renamed)


def test_first_rejection_can_receive_second_review_and_policy_changes_clear_votes(workspace):
    project,source=workspace;td=team();configure(project,source,review_enabled=True,required_reviews=2)
    row=save(project,source)['metadata']
    rejected=td.review_image(project,source,row['image_uuid'],row['revision'],'Kim','reject','Missing region')['image']
    disputed=td.review_image(project,source,row['image_uuid'],rejected['revision'],'Park','approve','')['image']
    assert disputed['team']['review_status']=='disputed'
    configure(project,source,editing_enabled=True)
    assert image(project,source)['team']['reviews']==[]


def test_two_person_review_cannot_adjudicate_after_only_one_vote(workspace):
    project,source=workspace;td=team();configure(project,source,review_enabled=True,required_reviews=2)
    row=save(project,source)['metadata']
    rejected=td.review_image(project,source,row['image_uuid'],row['revision'],'Kim','reject','Missing region')['image']
    with pytest.raises(ValueError):td.adjudicate_image(project,source,row['image_uuid'],rejected['revision'],'Kim','approve','Changed my decision')


def test_two_clients_cannot_claim_the_same_image(workspace):
    project, source = workspace; td = team(); configure(project, source, editing_enabled=True)
    row = image(project, source)
    def acquire(actor):
        try: return td.acquire_lease(project, source, row['image_uuid'], row['revision'], actor, 120)
        except dm.RevisionConflict: return None
    with ThreadPoolExecutor(max_workers=2) as pool: results = list(pool.map(acquire, ['Lee','Kim']))
    assert sum(result is not None for result in results) == 1
    winner = next(result for result in results if result)
    assert winner['lease_token'] and 'token' not in winner['image']['team']['edit_lease']
    assert 'token' not in td.workspace(project, source).__repr__()


def test_foreign_expired_and_reacquired_tokens_cannot_save_or_release(workspace, monkeypatch):
    project, source = workspace; td = team(); clock = [1000.0]; monkeypatch.setattr(td.time, 'time', lambda: clock[0])
    configure(project, source, editing_enabled=True)
    row = image(project, source); owned = td.acquire_lease(project, source, row['image_uuid'], row['revision'], 'Lee', 120)
    from fastapi import HTTPException
    with pytest.raises(HTTPException): save(project, source, 'Kim', owned['lease_token'])
    renewed = td.renew_lease(project, source, row['image_uuid'], owned['image']['revision'], 'Lee', owned['lease_token'], 120)
    assert renewed['lease_token'] == owned['lease_token'] and renewed['image']['revision'] == owned['image']['revision']
    assert save(project, source, 'Lee', renewed['lease_token'])['status'] == 'saved'
    clock[0] += 121
    with pytest.raises(HTTPException): save(project, source, 'Lee', renewed['lease_token'])
    fresh = image(project, source)
    next_lease = td.acquire_lease(project, source, row['image_uuid'], fresh['revision'], 'Kim', 120)
    assert next_lease['lease_token'] != renewed['lease_token']
    with pytest.raises(HTTPException): save(project, source, 'Lee', renewed['lease_token'])
    with pytest.raises(ValueError): td.release_lease(project,source,row['image_uuid'],next_lease['image']['revision'],'Lee',renewed['lease_token'])


def test_assignment_rejects_unknown_shared_members_and_foreign_edit(workspace):
    project, source = workspace; td = team(); configure(project, source, editing_enabled=True)
    row = image(project, source)
    with pytest.raises(ValueError): td.assign_image(project, source, row['image_uuid'], row['revision'], 'owner', 'stranger', 50, members={'Lee'})
    assigned = td.assign_image(project, source, row['image_uuid'], row['revision'], 'owner', 'Lee', 80, members={'Lee'})['image']
    assert assigned['team']['assignment']['assignee'] == 'Lee'
    with pytest.raises(ValueError): td.acquire_lease(project, source, row['image_uuid'], assigned['revision'], 'Kim', 120)
    assert td.work_queue(project, source, assignee='Lee')['total'] == 1


def test_two_person_review_requires_distinct_people_and_blocks_direct_approval(workspace):
    project, source = workspace; td = team(); configure(project, source, review_enabled=True, required_reviews=2)
    saved = save(project, source, 'Lee')['metadata']
    with pytest.raises(ValueError): td.review_image(project, source, saved['image_uuid'], saved['revision'], 'Lee', 'approve', '')
    with pytest.raises(ValueError): dm.update_metadata(Path(project['project_dir']), source, saved['image_uuid'], saved['revision'], 'Kim', {'workflow_state':'approved'})
    first = td.review_image(project, source, saved['image_uuid'], saved['revision'], 'Kim', 'approve', '')['image']
    assert first['workflow_state'] == 'needs_review'
    with pytest.raises(ValueError): td.review_image(project, source, saved['image_uuid'], first['revision'], 'kim', 'approve', '')
    second = td.review_image(project, source, saved['image_uuid'], first['revision'], 'Park', 'approve', '')['image']
    assert second['workflow_state'] == 'approved' and len(second['team']['reviews']) == 2
    assert all(vote['binding_sha256'] for vote in second['team']['reviews'])


def test_disagreement_requires_reason_and_explicit_adjudication(workspace):
    project, source = workspace; td = team(); configure(project, source, review_enabled=True, required_reviews=2)
    row = save(project, source)['metadata']
    first = td.review_image(project, source, row['image_uuid'], row['revision'], 'Kim', 'approve', '')['image']
    with pytest.raises(ValueError): td.review_image(project, source, row['image_uuid'], first['revision'], 'Park', 'reject', '')
    rejected = td.review_image(project, source, row['image_uuid'], first['revision'], 'Park', 'reject', 'Region misses a scratch')['image']
    assert rejected['team']['review_status'] == 'disputed' and rejected['workflow_state'] == 'needs_review'
    with pytest.raises(ValueError): td.adjudicate_image(project, source, row['image_uuid'], rejected['revision'], 'owner', 'approve', '')
    final = td.adjudicate_image(project, source, row['image_uuid'], rejected['revision'], 'owner', 'approve', 'Checked the region against the reference')['image']
    assert final['workflow_state'] == 'approved'
    assert final['team']['history'][-1]['action'] == 'adjudicated'


def test_guidance_content_and_label_changes_invalidate_votes(workspace):
    project, source = workspace; td = team(); configure(project, source, review_enabled=True)
    td.publish_book(project, source, 0, 'owner', 'Guidance', categories())
    row = save(project, source)['metadata']
    approved = td.review_image(project, source, row['image_uuid'], row['revision'], 'Kim', 'approve', '')['image']
    td.publish_book(project, source, 1, 'owner', 'Changed guidance', categories('New'))
    changed = image(project, source)
    assert changed['workflow_state'] == 'needs_review' and changed['team']['reviews'] == []
    approved = td.review_image(project, source, row['image_uuid'], changed['revision'], 'Kim', 'approve', '')['image']
    Image.new('RGB', (40,30), 'red').save(source / 'a.png')
    changed = image(project, source)
    assert changed['workflow_state'] == 'needs_review' and changed['team']['reviews'] == []
    assert changed['revision'] > approved['revision']


def test_enabled_training_policy_filters_unapproved_without_changing_splits(workspace):
    project, source = workspace; td = team()
    from backend.engine.grouped_dataset_views import source_image_paths
    assert {p.name for p in source_image_paths(source,'classification')} == {'a.png','b.png'}
    row = image(project, source)
    dm.update_metadata(Path(project['project_dir']),source,row['image_uuid'],row['revision'],'Kim',{'workflow_state':'approved'})
    configure(project, source, approved_only_training=True)
    # Enabling eligibility alone keeps legacy approval; enabling a review policy invalidates it.
    assert [p.name for p in source_image_paths(source,'classification')] == ['a.png']
    assert {p.name for p in source_image_paths(source,'classification',include_unused=True)} == {'a.png','b.png'}
    readiness = td.training_readiness(project, source)
    assert readiness['counts']['eligible'] == 1 and readiness['counts']['pending'] == 1
    assert readiness['book_version'] == 0 and len(readiness['policy_sha256']) == 64


def test_eligibility_binding_tracks_guidance_policy_and_exact_approved_images(workspace):
    project, source = workspace; td = team()
    original = td.training_binding(project, source)
    td.publish_book(project,source,0,'owner','Guidance',categories())
    current = td.training_binding(project,source)
    assert original['book_version'] == 0 and current['book_version'] == 1
    assert current['book_sha256'] and current['policy_sha256'] and current['eligibility_sha256']
    assert current['scope']['labelset_id'] == 'default'


def test_work_queue_pagination_and_revision_survive_reopen(workspace):
    project, source = workspace; td = team(); row = image(project,source)
    td.assign_image(project,source,row['image_uuid'],row['revision'],'owner','Lee',90)
    first = td.work_queue(project,source,limit=1)
    assert first['total'] == 2 and len(first['items']) == 1
    assert first['items'][0]['team']['assignment']['assignee'] == 'Lee'
    assert td.work_queue(project,source,offset=1,limit=1)['items'][0]['relative_path'] == 'b.png'
    assert image(project,source)['team']['assignment']['assignee'] == 'Lee'


def test_book_and_review_are_labelset_scoped(workspace):
    project,source = workspace; td=team(); td.publish_book(project,source,0,'owner','Main',categories())
    other={**project,'active_labelset_id':'ls_123456789abc','annotations_dir':str(Path(project['project_dir'])/'labelsets'/'ls_123456789abc'/'annotations')}
    assert td.workspace(other,source)['book'] is None
    assert td.workspace(project,source)['book']['version'] == 1


@pytest.mark.parametrize('family',['patch_classification','rotation','ocr','rotated_detection','defect_gan','enhancement'])
def test_every_specialist_source_map_rejects_unapproved_then_binds_exact_ids(workspace,family):
    import shutil
    from backend.engine.training_provenance import bind_family_training
    project,source=workspace;td=team();Image.new('RGB',(40,30),'red').save(source/'c.png')
    output=Path(project['dataset_dir'])/family/'prepared'
    splits=('train','val','test');names=['a.png','b.png','c.png']
    if family=='patch_classification':
        output.mkdir(parents=True)
        mapping={}
        for name in names:
            shutil.copyfile(source/name,output/name)
            mapping[name]={'source_relative_path':name,'source_sha256':dm._hash(source/name)}
        raw={'version':1,'classes':['OK','NG'],'normal_class':'OK','patch_size':20,'stride':20,
             'patches':[{'image':name,'box':[0,0,20,20],'label':'OK' if i%2==0 else 'NG','split':splits[i]} for i,name in enumerate(names)],
             'source_dataset_path':str(source),'source_map':mapping}
        (output/'patches.json').write_text(json.dumps(raw))
    elif family=='rotation':
        from backend.engine.rotation import prepare_rotation_dataset
        prepare_rotation_dataset(source,output,[{'image':name,'correction_deg':0,'split':splits[i]} for i,name in enumerate(names)])
    elif family in {'ocr','rotated_detection'}:
        from backend.engine.prepared_family_datasets import prepare_family_dataset
        rows=[{'image':name,'split':splits[i],**({'text':'A'} if family=='ocr' else {'label':'Scratch','box':{'cx':20,'cy':15,'width':10,'height':8,'angle_deg':0}})} for i,name in enumerate(names)]
        prepare_family_dataset(family,source,output,rows)
    elif family=='defect_gan':
        from backend.engine.defect_gan import prepare_defect_gan_dataset
        prepare_defect_gan_dataset(source,output,[{'image':name,'bbox':[0,0,40,30],'split':('train','train','val')[i]} for i,name in enumerate(names)])
    else:
        from backend.engine.enhancement import prepare_enhancement
        prepare_enhancement(source,output)
    configure(project,source,approved_only_training=True)
    with pytest.raises(ValueError,match='approved'):bind_family_training(project,output,family)
    for name in names:
        row=image(project,source,name)
        dm.update_metadata(Path(project['project_dir']),source,row['image_uuid'],row['revision'],'reviewer',{'workflow_state':'approved'})
    binding=bind_family_training(project,output,family)
    assert len(binding['family_source_image_uuids'])==3
    assert set(binding['family_source_image_uuids'])=={row['image_uuid'] for row in binding['team_data']['eligibility']}
