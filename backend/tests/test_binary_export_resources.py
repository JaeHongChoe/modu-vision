"""Compiled backend resource declarations must preserve standalone flow export."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[2]


def _builder():
    spec=importlib.util.spec_from_file_location('binary_builder_review',ROOT/'scripts/build_backend_binary.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize('platform_name,separator',[('Darwin',':'),('Windows',';')])
def test_declared_frozen_resources_export_and_reopen_flow(tmp_path,monkeypatch,platform_name,separator):
    from backend.engine import flow_package
    from backend.engine.flowchart_engine import get_single_detection_flowchart
    from backend.engine.native_sdk import _REQUIRED
    builder=_builder();commands=[]
    with monkeypatch.context() as capture:
        capture.setattr(builder,'OUTPUT_DIR',tmp_path/'dist')
        capture.setattr(builder,'check_pyinstaller',lambda:True)
        capture.setattr(builder.platform,'system',lambda:platform_name)
        capture.setattr(builder.subprocess,'run',lambda command,**kwargs:commands.append(command) or SimpleNamespace(returncode=0))
        builder.build_binary()
    command=commands[0];frozen=tmp_path/'_MEI';frozen.mkdir()
    for index,argument in enumerate(command):
        if argument!='--add-data':continue
        source,target=command[index+1].rsplit(separator,1);source=Path(source);destination=frozen/target
        if source.is_dir():shutil.copytree(source,destination,dirs_exist_ok=True)
        else:destination.mkdir(parents=True,exist_ok=True);shutil.copyfile(source,destination/source.name)
    model=tmp_path/'job_detector';model.mkdir();checkpoint=model/'best_model.pt';checkpoint.write_bytes(b'detector checkpoint fixture')
    (model/'model_meta.json').write_text(json.dumps({'task':'detection','image_size':[256,256]}))
    monkeypatch.setattr(flow_package,'__file__',str(frozen/'backend/engine/flow_package.py'))
    result=flow_package.build_flow_package(pipeline=get_single_detection_flowchart(job_id='job_detector'),checkpoints={'job_detector':checkpoint},output_base_dir=tmp_path/'exports',package_name='frozen_flow')
    package=Path(result['package_path'])
    for name in _REQUIRED:assert (package/'native_runtime'/name).read_bytes()==(ROOT/'native_runtime'/name).read_bytes()
    for name in ('inspection-service-client.mjs','InspectionServiceClient.cs'):assert (package/'clients'/name).read_bytes()==(ROOT/'examples'/name).read_bytes()
    assert (package/'backend/engine/flow_package_runtime.py').is_file()
    reopened=subprocess.run([sys.executable,str(package/'run_flow.py'),'--verify-only'],cwd=tmp_path,env={**os.environ,'PYTHONPATH':''},capture_output=True,text=True,timeout=30)
    assert reopened.returncode==0,reopened.stderr


def test_export_resources_include_only_required_sources_without_user_data(tmp_path):
    from backend.engine.native_sdk import _REQUIRED
    builder=_builder();native=tmp_path/'native_runtime';native.mkdir()
    for name in _REQUIRED:(native/name).write_text('source fixture')
    (native/'private-token.txt').write_text('excluded private fixture')
    examples=tmp_path/'examples';examples.mkdir()
    for name in ('inspection-service-client.mjs','InspectionServiceClient.cs'):(examples/name).write_text('client fixture')
    engine=tmp_path/'backend/engine';engine.mkdir(parents=True)
    (engine/'runtime.py').write_text('source fixture')
    (engine/'model.pt').write_bytes(b'excluded model')
    (engine/'__pycache__').mkdir();(engine/'__pycache__/cached.py').write_text('excluded cache fixture')
    files=builder.export_resource_files(tmp_path)
    assert all(source.is_file() for source,_ in files)
    assert {source.name for source,_ in files}==set(_REQUIRED)|{'inspection-service-client.mjs','InspectionServiceClient.cs','runtime.py'}
    assert (engine/'runtime.py','backend/engine') in files
