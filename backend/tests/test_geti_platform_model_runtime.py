"""Variant routing and source-bound actual prediction payloads, with real pixels."""
import hashlib
import io
import zipfile
import uuid
import threading
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout

from PIL import Image
from fastapi import HTTPException
import pytest

from tests.test_geti_platform_dataset_transfer import transfer_workspace


def test_parallel_first_exports_pin_one_consistent_archive(tmp_path, monkeypatch):
    from backend.platforms.geti import model_runtime as module
    first_ready, second_downloaded, release_first = threading.Event(), threading.Event(), threading.Event()
    mid, vid = str(uuid.uuid4()), str(uuid.uuid4())
    receipt = {'transfer_root': str(tmp_path), 'geti_project_id': str(uuid.uuid4()), 'dataset_version_id': 'same-dataset'}
    monkeypatch.setattr(module, '_resolve', lambda *args: ({'dataset_revision_id':'same-revision'}, {'format':'openvino'}))
    class Client:
        def request(self, *args, **kwargs):
            name = threading.current_thread().name
            archive = io.BytesIO()
            with zipfile.ZipFile(archive, 'w') as package:
                package.writestr('model.xml', 'same graph')
                package.writestr('model.bin', 'same weights')
                package.writestr('demo.py', name)
            if name.endswith('_1'): second_downloaded.set()
            return archive.getvalue()
    atomic = module._atomic_json
    def ordered_publication(path, value):
        if threading.current_thread().name.endswith('_0'):
            first_ready.set()
            assert release_first.wait(5)
        return atomic(path, value)
    monkeypatch.setattr(module, '_atomic_json', ordered_publication)
    client = Client()
    with ThreadPoolExecutor(max_workers=2, thread_name_prefix='archive') as pool:
        first = pool.submit(module.export_platform_model, client, receipt, mid, vid)
        try:
            assert first_ready.wait(5)
            second = pool.submit(module.export_platform_model, client, receipt, mid, vid)
            assert second_downloaded.wait(5)
            try: second.result(timeout=.25)
            except FutureTimeout: pass
        finally: release_first.set()
        one, two = first.result(timeout=5), second.result(timeout=5)
    monkeypatch.setattr(module, '_atomic_json', atomic)
    again = module.export_platform_model(client, receipt, mid, vid)
    assert one['data'] == two['data'] == again['data']
    assert hashlib.sha256(again['data']).hexdigest() == again['export_sha256']


def model_fixture(client, receipt):
    mid = str(uuid.uuid4()); rid = str(uuid.uuid4())
    label_schema = [{'id': row['geti_label_id'], 'name': row['name']} for row in receipt['class_mapping']]
    client.models = [{'id':mid,'name':'Real boundary model','architecture':'image-classification-efficientnet-b0',
        'parent_revision':None,'files_deleted':False,'size':60,
        'training_info':{'status':'successful','dataset_revision_id':rid,'label_schema_revision':{'labels':label_schema}},
        'variants':[{'id':str(uuid.uuid4()),'format':fmt,'precision':'fp16' if fmt!='pytorch' else 'fp32',
                     'weights_size':20,'evaluations':[{'dataset_revision_id':rid,'subset':'testing',
                         'metrics':[{'name':'accuracy','value':0.75}]}],'files_deleted':False,
                     'quantization_info':None,'optimal_confidence_threshold':None} for fmt in ['pytorch','onnx','openvino']]}]
    client.revisions[rid] = [{'id':r['media_id'],'subset':r['subset'],'width':r['geometry']['prepared_size'][0],
        'height':r['geometry']['prepared_size'][1],'format':'png'} for r in receipt['source_mapping']]
    return client.models[0]


def test_catalog_keeps_formats_evaluation_revision_and_no_torch_reconstruction(transfer_workspace):
    from backend.platforms.geti.dataset_transfer import prepare_dataset
    from backend.platforms.geti.model_runtime import list_platform_models
    project, _, client = transfer_workspace
    receipt = prepare_dataset(project, {'actor':'Reviewer'}, client)
    raw = model_fixture(client,receipt)
    models = list_platform_models(client,receipt)
    assert models[0]['dataset_revision_id'] == raw['training_info']['dataset_revision_id']
    assert [v['format'] for v in models[0]['variants']] == ['pytorch','onnx','openvino']
    assert [v['prediction_supported'] for v in models[0]['variants']] == [False,True,True]
    assert models[0]['variants'][2]['evaluations'][0]['metrics'] == [{'name':'accuracy','value':0.75}]
    assert not any(path.endswith('/binary') for _,path,_ in client.calls)  # listing does not fetch weights


def test_prediction_reuses_exact_recipe_copies_and_preserves_actual_identity(transfer_workspace):
    from backend.platforms.geti.dataset_transfer import prepare_dataset
    from backend.platforms.geti.model_runtime import predict_platform_model
    project, source, client = transfer_workspace
    recipe={'version':1,'operations':[{'op':'brightness','factor':2}]}
    receipt = prepare_dataset(project, {'actor':'Reviewer','input_recipe':recipe}, client)
    raw = model_fixture(client,receipt); variant=raw['variants'][2]
    row = next(r for r in receipt['source_mapping'] if r['relative_path']=='test/NG/image-3.png')
    ng = next(c['geti_label_id'] for c in receipt['class_mapping'] if c['name']=='NG')
    client.prediction={'predictions':[{'media':{'id':row['media_id'],'frame_index':None},
        'prediction':[{'labels':[{'id':ng}],'shape':{'type':'full_image'},'confidences':[0.8125]}]}]}
    result = predict_platform_model(client,receipt,raw['id'],variant['id'],source/row['relative_path'])
    assert result['predictions'] == [{'label':'NG','confidence':0.8125,'shape':{'type':'full_image'}}]
    assert result['model_id']==raw['id'] and result['variant_id']==variant['id']
    assert result['dataset_revision_id']==raw['training_info']['dataset_revision_id']
    assert len(result['export_sha256'])==64 and result['recipe_sha256']==receipt['recipe_sha256']
    prediction_calls=[body for method,path,body in client.calls if path.endswith('/dataset/media:predict')]
    assert prediction_calls==[{'model_id':raw['id'],'model_variant_id':variant['id'],
        'media':[{'media_id':row['media_id'],'range':None}],'device':'cpu'}]
    assert len(client.projects[receipt['geti_project_id']]['media'])==4  # no inference sample writes into training data


@pytest.mark.parametrize('kind',['pytorch','foreign_model','foreign_variant','stale_revision','wrong_media','foreign_image','mutated_image'])
def test_prediction_rejects_wrong_format_scope_source_or_revision(transfer_workspace,kind):
    from backend.platforms.geti.dataset_transfer import prepare_dataset
    from backend.platforms.geti.model_runtime import predict_platform_model
    project, source, client=transfer_workspace
    receipt=prepare_dataset(project,{'actor':'Reviewer'},client); model=model_fixture(client,receipt)
    mid=model['id']; vid=model['variants'][2]['id']; image=source/receipt['source_mapping'][0]['relative_path']
    if kind=='pytorch': vid=model['variants'][0]['id']
    elif kind=='foreign_model': mid=str(uuid.uuid4())
    elif kind=='foreign_variant': vid=str(uuid.uuid4())
    elif kind=='stale_revision': client.revisions[model['training_info']['dataset_revision_id']][0]['subset']='testing'
    elif kind=='wrong_media': client.prediction={'predictions':[{'media':{'id':str(uuid.uuid4())},'prediction':[]}]}
    elif kind=='foreign_image': image=source.parent/'other.png'; Image.new('RGB',(32,24),'red').save(image)
    else: Image.new('RGB',(32,24),'red').save(image)
    with pytest.raises((ValueError,HTTPException)): predict_platform_model(client,receipt,mid,vid,image)


def test_new_admitted_inspection_image_uses_same_recipe_without_mutating_training_cohort(transfer_workspace):
    from backend.platforms.geti.dataset_transfer import prepare_dataset,validate_transfer
    from backend.platforms.geti.model_runtime import predict_inspection_image
    project,source,client=transfer_workspace
    receipt=prepare_dataset(project,{'actor':'Reviewer','input_recipe':{'version':1,'operations':[{'op':'brightness','factor':2}]}},client)
    model=model_fixture(client,receipt); variant=model['variants'][2]
    image=source/'incoming'/'new.png'; image.parent.mkdir(); Image.new('RGB',(32,24),(10,20,30)).save(image)
    original=image.read_bytes()
    label=next(c['geti_label_id'] for c in receipt['class_mapping'] if c['name']=='NG')
    client.prediction={'predictions':[{'media':{'id':str(uuid.uuid4()),'frame_index':None},
        'prediction':[{'labels':[{'id':label}],'shape':{'type':'full_image'},'confidences':[0.9]}]}]}
    result=predict_inspection_image(project,client,receipt,model['id'],variant['id'],image)
    assert result['predictions'][0]['label']=='NG' and result['image_sha256']==hashlib.sha256(original).hexdigest()
    assert len(client.projects[receipt['geti_project_id']]['media'])==4 and image.read_bytes()==original
    with pytest.raises((ValueError,HTTPException)): validate_transfer(project,receipt)
    uploaded=client.inspection_upload
    with Image.open(io.BytesIO(uploaded)) as transformed: assert transformed.getpixel((0,0))==(20,40,60)


def test_new_inspection_input_does_not_hide_edits_to_frozen_training_labels(transfer_workspace):
    from backend.platforms.geti.dataset_transfer import prepare_dataset
    from backend.platforms.geti.model_runtime import predict_inspection_image
    from backend.engine.class_overrides import set_class_override
    from pathlib import Path
    project,source,client=transfer_workspace
    receipt=prepare_dataset(project,{'actor':'Reviewer'},client); model=model_fixture(client,receipt)
    set_class_override(Path(project['project_dir']),source,source/'train/OK/image-0.png','NG','Reviewer','OK')
    image=source/'incoming.png'; Image.new('RGB',(32,24),'red').save(image)
    with pytest.raises(ValueError,match='label|eligib|frozen|training'):
        predict_inspection_image(project,client,receipt,model['id'],model['variants'][2]['id'],image)


def test_export_identity_remains_stable_when_generated_zip_demo_bytes_change(transfer_workspace):
    from backend.platforms.geti.dataset_transfer import prepare_dataset
    from backend.platforms.geti.model_runtime import export_platform_model
    project,source,client=transfer_workspace
    receipt=prepare_dataset(project,{'actor':'Reviewer'},client); model=model_fixture(client,receipt)
    first=export_platform_model(client,receipt,model['id'],model['variants'][2]['id'])
    client.demo_bytes=b'new generated export demo'
    second=export_platform_model(client,receipt,model['id'],model['variants'][2]['id'])
    assert second['export_sha256']==first['export_sha256'] and second['data']==first['data']
