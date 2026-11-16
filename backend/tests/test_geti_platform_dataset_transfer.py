"""Real Modu storage and synthetic image/label conversion at the HTTP boundary."""
import copy
import hashlib
import io
import json
from pathlib import Path
from urllib.parse import urlsplit, parse_qs
import uuid
import zipfile

import numpy as np
from PIL import Image
import pytest
from fastapi import HTTPException


class GetiMemoryHTTP:
    """Strict pinned API boundary; image, label and split readback are independent."""
    def __init__(self):
        self.projects = {}
        self.models = []
        self.revisions = {}
        self.prediction = {'predictions': []}
        self.calls = []
        self.bad_readback = False

    def request(self, method, path, *, json=None, files=None, timeout=60):
        self.calls.append((method, path, copy.deepcopy(json)))
        parsed = urlsplit(path)
        parts = parsed.path.strip('/').split('/')
        if parts == ['api', 'projects'] and method == 'POST':
            pid = str(uuid.uuid4())
            value = copy.deepcopy(json)
            value['id'] = pid
            for label in value['task']['labels']:
                label['id'] = str(uuid.uuid4())
            self.projects[pid] = {'project': value, 'media': {}}
            return copy.deepcopy(value)
        if parts[0]=='modu' and parts[-1]=='predict' and method=='POST':
            assert files and set(files)=={'file'} and json is None
            self.inspection_upload=files['file'][1]
            return copy.deepcopy(self.prediction)
        project = self.projects[parts[2]]
        prefix = parts[3:]
        if not prefix and method == 'GET': return copy.deepcopy(project['project'])
        if prefix == ['models'] and method == 'GET': return copy.deepcopy(self.models)
        if prefix[:1] == ['dataset_revisions'] and prefix[-1] == 'items':
            return self._page(self.revisions[parts[4]], parsed.query)
        if prefix == ['dataset', 'media'] and method == 'POST':
            assert files and set(files) == {'file'} and json is None
            filename, data, content_type = files['file']
            with Image.open(io.BytesIO(data)) as image: width, height = image.size
            mid = str(uuid.uuid4())
            value = {'id': mid, 'type': 'image', 'name': Path(filename).stem, 'format': Path(filename).suffix[1:],
                     'width': width, 'height': height, 'size': len(data), 'source_id': None}
            project['media'][mid] = {'media': value, 'binary': data}
            return value.copy()
        if prefix == ['dataset', 'items']:
            return self._page([{'id': k, 'subset': v.get('annotation', {}).get('subset', 'unassigned'),
                                'user_reviewed': v.get('annotation', {}).get('user_reviewed', False)}
                               for k, v in project['media'].items()], parsed.query)
        if prefix[:2] == ['dataset', 'media']:
            value = project['media'][parts[5]]
            if prefix[-1] == 'binary':
                return value['binary'] + (b'corrupted' if self.bad_readback else b'')
            if prefix[-1] == 'annotations':
                if method == 'POST':
                    value['annotation'] = {**copy.deepcopy(json), 'user_reviewed': True, 'prediction_model_id': None}
                    for item in value['annotation']['annotations']: item.setdefault('confidences', None)
                return copy.deepcopy(value['annotation'])
        if prefix == ['dataset', 'media:predict']:
            return copy.deepcopy(self.prediction)
        if prefix[:1] == ['models'] and prefix[-1] == 'binary':
            variant = next(v for model in self.models for v in model['variants'] if v['id'] == parts[6])
            output = io.BytesIO()
            with zipfile.ZipFile(output, 'w') as archive:
                if variant['format'] == 'openvino':
                    archive.writestr('model.xml', '<net/>'); archive.writestr('model.bin', b'synthetic exported weights')
                elif variant['format'] == 'onnx': archive.writestr('model.onnx', b'synthetic onnx boundary')
                else: archive.writestr('model.pt', b'synthetic torch boundary')
                if hasattr(self,'demo_bytes'): archive.writestr('demo.py',self.demo_bytes)
            return output.getvalue()
        raise AssertionError((method, path, json))

    @staticmethod
    def _page(rows, query):
        q = parse_qs(query)
        offset = int(q.get('offset', ['0'])[0]); limit = int(q.get('limit', ['10'])[0])
        page = rows[offset:offset+limit]
        return {'items': copy.deepcopy(page), 'pagination': {'total': len(rows), 'offset': offset, 'limit': limit, 'count': len(page)}}


@pytest.fixture
def transfer_workspace(tmp_path):
    from backend.engine.annotation_storage import (set_request_annotation_root, set_request_project_root,
        reset_request_annotation_root, reset_request_project_root)
    source = tmp_path/'source'; root = tmp_path/'project'; root.mkdir(); source.mkdir()
    project = {'id': 'owned-project', 'name': 'Synthetic', 'task': 'classification',
               'source_dataset_dir': str(source), 'project_dir': str(root),
               'annotations_dir': str(root/'annotations'), 'dataset_dir': str(root/'dataset'), 'active_labelset_id': 'default'}
    (root/'project.json').write_text(json.dumps(project))
    for i, (split, label) in enumerate([('train','OK'), ('train','NG'), ('val','OK'), ('test','NG')]):
        path = source/split/label/f'image-{i}.png'; path.parent.mkdir(parents=True, exist_ok=True)
        pixels = np.zeros((24,32,3), dtype=np.uint8); pixels[:] = [20+i*10,40,100]
        Image.fromarray(pixels).save(path)
    a = set_request_annotation_root(root/'annotations'); p = set_request_project_root(root)
    try: yield project, source, GetiMemoryHTTP()
    finally: reset_request_annotation_root(a); reset_request_project_root(p)


def test_copy_freezes_real_versions_overrides_split_and_byte_readback(transfer_workspace):
    from backend.platforms.geti.dataset_transfer import prepare_dataset, validate_transfer
    from backend.engine.class_overrides import set_class_override
    project, source, client = transfer_workspace
    set_class_override(Path(project['project_dir']), source, source/'train/OK/image-0.png', 'NG', 'Reviewer', 'OK')
    before = {p.relative_to(source).as_posix():p.read_bytes() for p in source.rglob('*.png')}
    receipt = prepare_dataset(project, {'actor': 'Reviewer'}, client)
    assert receipt['counts'] == {'training': 2, 'validation': 1, 'testing': 1, 'total': 4}
    assert len(receipt['manifest_sha256']) == 64 and receipt['labelset_id'] == 'default'
    rows = {r['relative_path']:r for r in receipt['source_mapping']}
    assert rows['train/OK/image-0.png']['label'] == 'NG'
    assert rows['test/NG/image-3.png']['subset'] == 'testing'
    assert rows['train/OK/image-0.png']['sha256'] == hashlib.sha256(before['train/OK/image-0.png']).hexdigest()
    assert {p.relative_to(source).as_posix():p.read_bytes() for p in source.rglob('*.png')} == before
    validate_transfer(project, receipt)
    second = prepare_dataset(project, {'actor': 'Reviewer'}, client)
    assert second['geti_project_id'] != receipt['geti_project_id'] and len(client.projects) == 2
    assert not any(method == 'DELETE' for method, _, _ in client.calls)


def test_recipe_changes_only_copies_and_numeric_fft_is_recorded(transfer_workspace):
    from backend.platforms.geti.dataset_transfer import prepare_dataset
    project, source, client = transfer_workspace
    recipe = {'version':1,'operations':[{'op':'code','language':'modu-numeric-v1','code':'result = real(ifft2(fft2(image)))'},
        {'op':'brightness','factor':2},{'op':'resize','width':16,'height':16}]}
    original = (source/'train/OK/image-0.png').read_bytes()
    receipt = prepare_dataset(project, {'actor':'Reviewer', 'input_recipe':recipe}, client)
    row = next(r for r in receipt['source_mapping'] if r['relative_path'] == 'train/OK/image-0.png')
    assert len(receipt['recipe_sha256']) == 64
    uploaded = client.projects[receipt['geti_project_id']]['media'][row['media_id']]['binary']
    with Image.open(io.BytesIO(uploaded)) as image:
        assert image.size == (16,16) and image.getpixel((0,0)) == (40,80,200)
    assert row['uploaded_sha256'] == hashlib.sha256(uploaded).hexdigest()
    assert (source/'train/OK/image-0.png').read_bytes() == original


@pytest.mark.parametrize('change', ['source','split','class','labelset','project'])
def test_stale_source_or_labels_or_split_rejects_training(transfer_workspace, change):
    from backend.platforms.geti.dataset_transfer import prepare_dataset, validate_transfer
    from backend.api import routes_dataset
    from backend.engine.class_overrides import set_class_override
    project, source, client = transfer_workspace
    receipt = prepare_dataset(project, {'actor':'Reviewer'}, client)
    if change == 'source': Image.new('RGB',(32,24),'red').save(source/'train/OK/image-0.png')
    elif change == 'split': routes_dataset._write_split_manifest(source, {'train/OK/image-0.png':'test'}, 1)
    elif change == 'class': set_class_override(Path(project['project_dir']),source,source/'train/OK/image-0.png','NG','Reviewer','OK')
    elif change == 'labelset': project = {**project,'active_labelset_id':'ls_123456abcdef'}
    else: project = {**project,'id':'someone-else'}
    with pytest.raises((ValueError, HTTPException)): validate_transfer(project, receipt)


def test_bad_byte_readback_never_publishes_success(transfer_workspace):
    from backend.platforms.geti.dataset_transfer import prepare_dataset
    project, source, client = transfer_workspace; client.bad_readback = True
    with pytest.raises(ValueError, match='byte|hash'): prepare_dataset(project, {'actor':'Reviewer'}, client)


@pytest.mark.parametrize('task', ['segmentation','anomaly','patch_classification','rotation','ocr','rotated_detection','enhancement','defect_gan'])
def test_native_families_are_explicitly_unsupported_and_unchanged(transfer_workspace, task):
    from backend.platforms.geti.dataset_transfer import prepare_dataset
    project, _, client = transfer_workspace
    with pytest.raises(ValueError, match='native|supported'): prepare_dataset({**project,'task':task}, {}, client)
    assert client.projects == {}


def test_detection_boxes_and_instance_polygons_roundtrip_without_semantic_cast(transfer_workspace):
    from backend.platforms.geti.dataset_transfer import prepare_dataset
    project, source, client = transfer_workspace
    for task, kind, geometry in [('detection','rectangle',[[2,3],[12,13]]),
                                 ('instance_segmentation','polygon',[[2,3],[12,3],[10,13]])]:
        project['task'] = task
        (Path(project['project_dir'])/'project.json').write_text(json.dumps(project))
        for image in source.rglob('*.png'):
            image.with_suffix('.json').write_text(json.dumps({'imagePath':image.name,'imageWidth':32,'imageHeight':24,
                'shapes':[{'label':'scratch','shape_type':kind,'points':geometry}]}))
        receipt = prepare_dataset(project, {'actor':'Reviewer'}, client)
        row = receipt['source_mapping'][0]
        labels = client.projects[receipt['geti_project_id']]['media'][row['media_id']]['annotation']['annotations']
        shape = labels[0]['shape']
        if task == 'detection': assert shape == {'type':'rectangle','x':2,'y':3,'width':10,'height':10}
        else: assert shape == {'type':'polygon','points':[{'x':2.0,'y':3.0},{'x':12.0,'y':3.0},{'x':10.0,'y':13.0}]}


def test_adoption_creates_independent_labelset_and_version_with_cas(transfer_workspace):
    from backend.platforms.geti.dataset_transfer import prepare_dataset, adopt_labels
    from backend.engine.annotation_storage import dataset_annotation_dir
    from backend.engine.class_overrides import read_class_overrides
    project, source, client = transfer_workspace
    receipt = prepare_dataset(project, {'actor':'Reviewer'}, client)
    row = next(r for r in receipt['source_mapping'] if r['relative_path']=='train/OK/image-0.png')
    media = client.projects[receipt['geti_project_id']]['media'][row['media_id']]
    ng = next(c['geti_label_id'] for c in receipt['class_mapping'] if c['name']=='NG')
    media['annotation']['annotations'][0]['labels'] = [{'id':ng}]
    original = {p.relative_to(source).as_posix():p.read_bytes() for p in source.rglob('*') if p.is_file()}
    result = adopt_labels(project, {'actor':'Reviewer'}, client, receipt, expected_revision=receipt['revision'])
    assert result['labelset_id'] != 'default' and result['dataset_version_id'] != receipt['dataset_version_id']
    assert (Path(project['project_dir'])/'versions'/result['dataset_version_id']/'manifest.json').is_file()
    assert {p.relative_to(source).as_posix():p.read_bytes() for p in source.rglob('*') if p.is_file()} == original
    assert read_class_overrides(source) == {}  # original active labels stay intact
    new = Path(project['project_dir'])/'labelsets'/result['labelset_id']/'annotations'
    document = json.loads((dataset_annotation_dir(source,new,use_scope=False)/'classification/class_overrides.json').read_text())
    assert document['overrides']['train/OK/image-0.png'] == 'NG'
    with pytest.raises((ValueError,HTTPException)): adopt_labels(project, {'actor':'Reviewer'}, client, receipt, expected_revision='stale')


def test_unreviewed_machine_prediction_cannot_be_adopted(transfer_workspace):
    from backend.platforms.geti.dataset_transfer import prepare_dataset, adopt_labels
    project, _, client = transfer_workspace
    receipt = prepare_dataset(project, {'actor':'Reviewer'}, client)
    row = receipt['source_mapping'][0]
    client.projects[receipt['geti_project_id']]['media'][row['media_id']]['annotation'].update(user_reviewed=False,prediction_model_id=str(uuid.uuid4()))
    with pytest.raises(ValueError, match='review'): adopt_labels(project, {'actor':'Reviewer'}, client, receipt, expected_revision=receipt['revision'])


@pytest.mark.parametrize('task', ['detection','instance_segmentation'])
def test_reviewed_object_adoption_reprepares_as_new_label_revision(transfer_workspace, task):
    from backend.platforms.geti.dataset_transfer import prepare_dataset, adopt_labels
    from backend.engine.project_labelsets import labelset_root
    project, source, client = transfer_workspace
    project['task']=task; (Path(project['project_dir'])/'project.json').write_text(json.dumps(project))
    for image in source.rglob('*.png'):
        image.with_suffix('.json').write_text(json.dumps({'imagePath':image.name,'imageWidth':32,'imageHeight':24,
            'shapes':[{'label':'scratch','shape_type':'rectangle' if task=='detection' else 'polygon',
                       'points':[[2,3],[12,13]] if task=='detection' else [[2,3],[12,3],[10,13]]}]}))
    receipt=prepare_dataset(project, {'actor':'Reviewer'}, client)
    mid=receipt['source_mapping'][0]['media_id']
    shape=client.projects[receipt['geti_project_id']]['media'][mid]['annotation']['annotations'][0]['shape']
    if task=='detection': shape['x']=4; shape['width']=8
    else: shape['points'][0]['x']=4
    adopted=adopt_labels(project,{'actor':'Reviewer'},client,receipt,expected_revision=receipt['revision'])
    new_project={**project,'active_labelset_id':adopted['labelset_id'],
                 'annotations_dir':str(labelset_root(Path(project['project_dir']),adopted['labelset_id']))}
    (Path(project['project_dir'])/'project.json').write_text(json.dumps(new_project))
    second=prepare_dataset(new_project,{'actor':'Reviewer'},client)
    next_shape=second['source_mapping'][0]['platform_annotations'][0]['shape']
    assert next_shape==shape
    assert second['dataset_version_id'] != receipt['dataset_version_id']


def test_missing_independent_testing_subset_is_rejected_before_remote_mutation(transfer_workspace):
    from backend.platforms.geti.dataset_transfer import prepare_dataset
    project, source, client=transfer_workspace
    (source/'test/NG/image-3.png').unlink()
    with pytest.raises(ValueError,match='testing|test|heldout'):
        prepare_dataset(project,{'actor':'Reviewer'},client)
    assert client.projects == {}


def test_saved_split_exact_memberships_and_common_original_group_guard(transfer_workspace):
    from backend.platforms.geti.dataset_transfer import prepare_dataset
    from backend.api.routes_dataset import _write_split_manifest
    from backend.engine import dataset_metadata as dm
    project,source,client=transfer_workspace
    assignments={'train/OK/image-0.png':'train','train/NG/image-1.png':'val','val/OK/image-2.png':'test','test/NG/image-3.png':'train'}
    _write_split_manifest(source,assignments,42)
    receipt=prepare_dataset(project,{'actor':'Reviewer'},client)
    assert {r['relative_path']:r['subset'] for r in receipt['source_mapping']} == {
        'train/OK/image-0.png':'training','train/NG/image-1.png':'validation','val/OK/image-2.png':'testing','test/NG/image-3.png':'training'}
    for name in ('train/OK/image-0.png','train/NG/image-1.png'):
        row=dm.metadata_for_path(Path(project['project_dir']),source,source/name,Path(project['annotations_dir']))
        dm.update_metadata(Path(project['project_dir']),source,row['image_uuid'],row['revision'],'Reviewer',{'group':'same-original'},Path(project['annotations_dir']))
    with pytest.raises(ValueError,match='group|subsets'): prepare_dataset(project,{'actor':'Reviewer'},client)
    assert len(client.projects)==1


def test_adopted_class_ids_retain_existing_active_labelbook_identity(transfer_workspace):
    from backend.platforms.geti.dataset_transfer import prepare_dataset,adopt_labels
    from backend.engine.annotation_storage import dataset_annotation_dir
    project,source,client=transfer_workspace; project['task']='detection'
    (Path(project['project_dir'])/'project.json').write_text(json.dumps(project))
    for image in source.rglob('*.png'):
        overlay=dataset_annotation_dir(image.parent)/f'{image.stem}.json'; overlay.parent.mkdir(parents=True,exist_ok=True)
        overlay.write_text(json.dumps({'annotations':[{'type':'bbox','label':'scratch','category_id':7,'bbox':[2,3,12,13]}]}))
    receipt=prepare_dataset(project,{'actor':'Reviewer'},client)
    assert receipt['class_mapping'][0]['source_class_ids']==[7]
    result=adopt_labels(project,{'actor':'Reviewer'},client,receipt,expected_revision=receipt['revision'])
    row=receipt['source_mapping'][0]; image=source/row['relative_path']
    new_root=Path(project['project_dir'])/'labelsets'/result['labelset_id']/'annotations'
    saved=json.loads((dataset_annotation_dir(image.parent,new_root,use_scope=False)/f'{image.stem}.json').read_text())
    assert saved['annotations'][0]['category_id']==7


def test_label_adoption_obeys_existing_project_lifecycle_fence(transfer_workspace):
    from concurrent.futures import ThreadPoolExecutor
    from backend.platforms.geti.dataset_transfer import prepare_dataset,adopt_labels
    from backend.engine.runtime_process_control import runtime_state_lock
    project,source,client=transfer_workspace
    receipt=prepare_dataset(project,{'actor':'Reviewer'},client)
    with runtime_state_lock(Path(project['project_dir'])):
        with ThreadPoolExecutor(max_workers=1) as pool:
            future=pool.submit(adopt_labels,project,{'actor':'Reviewer'},client,receipt,expected_revision=receipt['revision'])
            with pytest.raises(ValueError,match='lifecycle|operation'):
                future.result(timeout=5)


def test_native_segmentation_requires_explicit_instance_target_and_preserves_polygons(transfer_workspace):
    from backend.platforms.geti.dataset_transfer import prepare_dataset,validate_transfer
    project,source,client=transfer_workspace; project['task']='segmentation'
    (Path(project['project_dir'])/'project.json').write_text(json.dumps(project))
    for image in source.rglob('*.png'):
        image.with_suffix('.json').write_text(json.dumps({'imagePath':image.name,'imageWidth':32,'imageHeight':24,
            'shapes':[{'label':'scratch','shape_type':'polygon','points':[[2,3],[12,3],[10,13]]}]}))
    with pytest.raises(ValueError,match='native|supported'):
        prepare_dataset(project,{'actor':'Reviewer'},client)
    receipt=prepare_dataset(project,{'actor':'Reviewer','target_task':'instance_segmentation'},client)
    assert receipt['task']=='instance_segmentation' and receipt['source_task']=='segmentation'
    assert receipt['source_mapping'][0]['platform_annotations'][0]['shape']['type']=='polygon'
    validate_transfer(project,receipt)
    assert json.loads((Path(receipt['training_binding']['version_dir'])/'manifest.json').read_text())['task']=='segmentation'


def test_explicit_instance_target_rejects_mask_only_semantic_truth(transfer_workspace):
    from backend.platforms.geti.dataset_transfer import prepare_dataset
    project,source,client=transfer_workspace; project['task']='segmentation'
    (Path(project['project_dir'])/'project.json').write_text(json.dumps(project))
    for image in source.rglob('*.png'):
        masks=source/'masks'; masks.mkdir(exist_ok=True)
        Image.new('L',(32,24),1).save(masks/image.name)
    with pytest.raises(ValueError,match='explicit|annotation|Semantic|instance'):
        prepare_dataset(project,{'actor':'Reviewer','target_task':'instance_segmentation'},client)
    assert client.projects=={}


def test_adoption_revokes_copied_approval_for_new_classification_revision(transfer_workspace):
    from backend.platforms.geti.dataset_transfer import prepare_dataset,adopt_labels
    from backend.engine import dataset_metadata as dm
    from backend.engine.project_labelsets import labelset_root
    project,source,client=transfer_workspace
    image=source/'train/OK/image-0.png'; root=Path(project['project_dir'])
    row=dm.metadata_for_path(root,source,image,Path(project['annotations_dir']))
    dm.update_metadata(root,source,row['image_uuid'],row['revision'],'Reviewer',{'workflow_state':'approved'},Path(project['annotations_dir']))
    receipt=prepare_dataset(project,{'actor':'Reviewer'},client)
    result=adopt_labels(project,{'actor':'Reviewer'},client,receipt,expected_revision=receipt['revision'])
    adopted=dm.metadata_for_path(root,source,image,labelset_root(root,result['labelset_id']))
    assert adopted['workflow_state']=='needs_review'
    assert dm.metadata_for_path(root,source,image,Path(project['annotations_dir']))['workflow_state']=='approved'
