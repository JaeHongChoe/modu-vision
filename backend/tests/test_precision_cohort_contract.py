"""Input checks exercise real images; no converted-runtime receipt is fabricated."""
import hashlib
from pathlib import Path
import pytest
from PIL import Image


def inputs(tmp_path):
    source=tmp_path/'source';(source/'val').mkdir(parents=True)
    rows=[]
    for number,color in enumerate(('white','black')):
        image=source/'val'/f'image_{number}.png';Image.new('RGB',(32,32),color).save(image)
        rows.append({'relative_path':str(image.relative_to(source)),'sha256':hashlib.sha256(image.read_bytes()).hexdigest(),'split':'val'})
    return {'source_dataset_path':str(source),'validation_images':rows,'calibration_images':[]}


def test_precision_requires_two_to_sixty_four_distinct_heldout_paths(tmp_path):
    from backend.engine.runtime_release_evidence import precision_cohort_inputs
    receipt=inputs(tmp_path)
    with pytest.raises(ValueError,match='2 to 64'):precision_cohort_inputs({**receipt,'validation_images':receipt['validation_images'][:1]})
    with pytest.raises(ValueError,match='distinct'):precision_cohort_inputs({**receipt,'validation_images':[receipt['validation_images'][0]]*2})
    assert precision_cohort_inputs(receipt,check_source=True)['image_count']==2


def test_precision_original_images_cannot_change_during_approval(tmp_path):
    from backend.engine.runtime_release_evidence import precision_cohort_inputs
    receipt=inputs(tmp_path);row=receipt['validation_images'][0]
    (Path(receipt['source_dataset_path'])/row['relative_path']).write_bytes(b'changed input')
    with pytest.raises(ValueError,match='image.*changed'):precision_cohort_inputs(receipt,check_source=True)


def test_precision_training_inputs_and_unsafe_relative_paths_cannot_qualify(tmp_path):
    from backend.engine.runtime_release_evidence import precision_cohort_inputs
    receipt=inputs(tmp_path);receipt['validation_images'][0]['split']='train'
    with pytest.raises(ValueError,match='heldout'):precision_cohort_inputs(receipt)
    receipt['validation_images'][0]['split']='val';receipt['validation_images'][0]['relative_path']='../escape.png'
    with pytest.raises(ValueError,match='path'):precision_cohort_inputs(receipt)


def test_precision_candidate_must_record_actual_native_cpu_execution():
    from backend.engine.runtime_release_evidence import _verify_precision_candidate_runtime
    # These are rejected provenance inputs, never manufactured acceptance receipts.
    for result in ({}, {'model_runtime':{'backend':'torch','device':'CPU','compiled_models':1}},
                   {'model_runtime':{'backend':'openvino','device':'GPU','compiled_models':1}},
                   {'model_runtime':{'backend':'openvino','device':'CPU','compiled_models':0}}):
        with pytest.raises(ValueError,match='actual OpenVINO CPU'):
            _verify_precision_candidate_runtime(result)


def test_precision_accepts_the_real_versioned_dataset_fingerprint(tmp_path):
    from backend.engine.runtime_release_evidence import _verify_precision_source_identity
    from backend.engine.dataset_fingerprint import fingerprint_dataset
    receipt=inputs(tmp_path)
    receipt['source_fingerprint']=fingerprint_dataset(Path(receipt['source_dataset_path']))
    assert receipt['source_fingerprint'].startswith('v1:')
    _verify_precision_source_identity(receipt,'a'*64)
    for invalid in ('a'*64,'v2:'+'a'*64,'v1:bad'):
        with pytest.raises(ValueError,match='identities'):
            _verify_precision_source_identity({**receipt,'source_fingerprint':invalid},'a'*64)
