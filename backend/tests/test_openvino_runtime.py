"""Real IR conversion, calibrated INT8 and full graph deployment."""
import json
from pathlib import Path

import numpy as np
import pytest
import torch
from PIL import Image

from backend.tests.test_runtime_deadline_sdk import real_package


def test_openvino_target_never_falls_back_to_cpu():
    pytest.importorskip('openvino')
    from backend.engine.openvino_runtime import available_openvino_devices,require_openvino_device
    devices=available_openvino_devices()
    assert 'CPU' in devices['devices']
    with pytest.raises(ValueError,match='unavailable|Unavailable'):
        require_openvino_device('NPU.999')


def test_real_ir_conversion_keeps_full_flow_and_saved_metrics(real_package,tmp_path):
    pytest.importorskip('openvino')
    from backend.engine.openvino_runtime import optimize_flow_package
    from backend.engine.flow_package_runtime import Predictor,compare_flow_results,verify_flow_package
    package,image=real_package
    result=optimize_flow_package(package,output_dir=tmp_path/'openvino',precision='fp32',calibration_images=[image],
                                 validation_images=[image],device='CPU')
    optimized=Path(result['package_path'])
    verify_flow_package(optimized)
    assert result['models'][0]['metrics']['max_absolute_error']<.0001
    assert result['models'][0]['metrics']['validation_image_count']==1
    assert result['models'][0]['precision']=='fp32'
    actual=Predictor(optimized,device='openvino:CPU',deadline_ms=30000).predict(image)
    assert compare_flow_results(Predictor(package).predict(image),actual)['status']=='passed'
    assert actual['runtime_execution']['device']=='openvino:CPU'
    assert actual['model_runtime']['backend']=='openvino'
    assert actual['model_runtime']['compiled_models']==1
    assert (optimized/'models/job_real_runtime/openvino/model.xml').is_file()
    heldout=json.loads((optimized/'heldout_flow_results.json').read_text())
    assert heldout[0]['comparison']['status']=='passed'
    assert heldout[0]['reference']['final_verdict']==heldout[0]['candidate']['final_verdict']=='NG'
    assert result['heldout_flow_count']==1


def test_int8_requires_real_distinct_calibration_and_validation_and_records_drift(tmp_path):
    pytest.importorskip('openvino');pytest.importorskip('nncf')
    from backend.engine.openvino_runtime import convert_model_artifact
    model=torch.nn.Sequential(torch.nn.Conv2d(3,4,3,padding=1),torch.nn.ReLU(),torch.nn.AdaptiveAvgPool2d(1),torch.nn.Flatten(),torch.nn.Linear(4,2)).eval()
    samples=[np.random.default_rng(i).random((1,3,16,16),dtype=np.float32) for i in range(4)]
    with pytest.raises(ValueError,match='calibration'):
        convert_model_artifact(model,samples[0],tmp_path/'invalid',precision='int8',calibration=[],validation=samples[2:])
    result=convert_model_artifact(model,samples[0],tmp_path/'int8',precision='int8',calibration=samples[:2],validation=samples[2:])
    assert result['precision']=='int8' and result['quantized_operation_count']>0
    assert result['metrics']['calibration_count']==2 and result['metrics']['validation_count']==2
    assert np.isfinite(result['metrics']['max_absolute_error'])
    assert result['quality_approved'] is False
    reopened=json.loads((tmp_path/'int8'/'conversion.json').read_text())
    assert reopened['metrics']==result['metrics']


def test_real_ocr_ir_uses_saved_height_width_and_inverted_letterbox(tmp_path):
    pytest.importorskip('openvino')
    from backend.tests.test_ocr import _labeled_images
    from backend.engine.ocr import train_ocr,write_ocr_manifest
    from backend.tests.test_flowchart_typed_completion import graph
    from backend.engine.flow_package import build_flow_package
    from backend.engine.openvino_runtime import optimize_flow_package
    from backend.engine.flow_package_runtime import Predictor,compare_flow_results
    torch.set_num_threads(1)
    source=tmp_path/'ocr';write_ocr_manifest(source,_labeled_images(source))
    trained=train_ocr(source,tmp_path/'model',epochs=1,batch_size=2,image_size=(32,64))
    image=source/'images/test_A.png'
    package=Path(build_flow_package(pipeline=graph(task='ocr'),checkpoints={'job_test':tmp_path/'model/best_model.pt'},output_base_dir=tmp_path/'exports',package_name='ocr')['package_path'])
    result=optimize_flow_package(package,output_dir=tmp_path/'optimized',validation_images=[image])
    assert result['models'][0]['input_shape']==[1,1,32,64]
    reference=Predictor(package).predict(image);actual=Predictor(result['package_path']).predict(image)
    assert compare_flow_results(reference,actual)['status']=='passed'


def test_int8_saved_whole_flow_runs_with_actual_distinct_calibration_pixels(real_package,tmp_path):
    pytest.importorskip('openvino');pytest.importorskip('nncf')
    from backend.engine.openvino_runtime import optimize_flow_package
    from backend.engine.flow_package_runtime import Predictor
    package,image=real_package
    calibration=tmp_path/'calibration.png';Image.new('RGB',(48,32),(160,30,80)).save(calibration)
    result=optimize_flow_package(package,output_dir=tmp_path/'int8_flow',precision='int8',calibration_images=[calibration],validation_images=[image])
    assert result['models'][0]['quantized_operation_count']>0
    assert result['heldout_flow_count']==1 and result['heldout_flow_passed_count']==1
    assert Predictor(result['package_path']).predict(image)['final_verdict']=='NG'
    assert result['quality_approved'] is False
