"""Release gates consume real multi-image app/exported-runtime parity evidence."""
import copy
import hashlib
import json
from pathlib import Path
import pytest
from PIL import Image
from backend.engine.flow_package import build_flow_package
from backend.engine.flowchart_engine import get_single_segmentation_flowchart
from backend.tests.runtime_release_fixture import real_classification_checkpoints,cohort_receipt


@pytest.fixture(scope='module')
def genuine(tmp_path_factory):
    root=tmp_path_factory.mktemp('release-evidence')
    checkpoint=root/'job_evidence'/'best_model.pt';checkpoint.parent.mkdir()
    real_classification_checkpoints({'job_evidence':checkpoint})
    graph=get_single_segmentation_flowchart(job_id='job_evidence')
    for node in graph.nodes:
        if node.data.node_type=='inspection':node.data.task='classification'
    package=Path(build_flow_package(pipeline=graph,checkpoints={'job_evidence':checkpoint},output_base_dir=root/'exports',package_name='evidence')['package_path'])
    images=[]
    for index,color in enumerate(('white','black')):
        image=root/f'image_{index}.png';Image.new('RGB',(32,32),color).save(image);images.append(image)
    report=cohort_receipt(package,graph,{'job_evidence':checkpoint},images)
    return package,report


def test_real_matching_cohort_is_bound_to_manifest_graph_models_and_device(genuine):
    from backend.engine.runtime_release_evidence import verify_release_evidence
    package,report=genuine
    actual=verify_release_evidence(package,'cpu')
    assert actual['cohort_sha256']==report['cohort_sha256']
    assert actual['image_count']==2
    assert actual['receipt_sha256']==hashlib.sha256((package/'parity_receipt.json').read_bytes()).hexdigest()


@pytest.mark.parametrize('field,value,reason',[('scope','single_image','cohort'),('device','mps','device'),
 ('manifest_sha256','0'*64,'manifest'),('cohort_sha256','0'*64,'cohort'),('completed_count',1,'complete')])
def test_real_receipt_mutation_cannot_qualify_a_release(genuine,field,value,reason):
    from backend.engine.runtime_release_evidence import verify_release_evidence
    package,report=genuine;receipt=package/'parity_receipt.json';original=receipt.read_bytes()
    changed=copy.deepcopy(report);changed[field]=value;receipt.write_text(json.dumps({'schema_version':1,**changed}))
    try:
        with pytest.raises(ValueError,match=reason):verify_release_evidence(package,'cpu')
    finally:receipt.write_bytes(original)


def test_receipt_bytes_cannot_change_after_policy_binding(genuine):
    from backend.engine.runtime_release_evidence import verify_release_evidence
    package,_=genuine;receipt=package/'parity_receipt.json';original=receipt.read_bytes()
    expected=hashlib.sha256(original).hexdigest();receipt.write_bytes(original+b' ')
    try:
        with pytest.raises(ValueError,match='receipt checksum'):verify_release_evidence(package,'cpu',expected_receipt_sha256=expected)
    finally:receipt.write_bytes(original)


def test_actual_service_readback_reports_its_own_runtime_build(genuine,tmp_path):
    import backend.main  # Existing TestClient compatibility for the legacy test interpreter.
    from backend.engine.inspection_service import create_service_app
    from backend.engine.service_bootstrap import trusted_runtime_identity
    from fastapi.testclient import TestClient
    package,_=genuine
    expected=trusted_runtime_identity()
    app=create_service_app(package,tmp_path/'state',token='test-owned-token',auto_worker=False)
    with TestClient(app) as client:
        actual=client.get('/v1/runtime',headers={'X-Vision-Token':'test-owned-token'}).json()
        assert actual['runtime_build']==expected
        assert actual['manifest_sha256']==hashlib.sha256((package/'manifest.json').read_bytes()).hexdigest()
