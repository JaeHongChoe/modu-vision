"""Native portable flows do not require the authorized desktop Geti adapter."""
import json
import os
import subprocess
import sys
from types import SimpleNamespace

import numpy as np
import pytest

from backend.engine.flowchart_engine import FlowchartEngine, get_single_detection_flowchart, get_single_segmentation_flowchart
from backend.tests.test_portable_capture_policy_runtime import build_package


NATIVE_PROBE = r'''
import importlib.abc, json, sys
from pathlib import Path
sys.dont_write_bytecode = True
package = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(package))
class NativeOnly(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'backend.platforms' or fullname.startswith('backend.platforms.'):
            raise AssertionError('Native flow tried importing desktop platform adapter: ' + fullname)
sys.meta_path.insert(0, NativeOnly())
import backend
assert Path(backend.__file__).resolve().is_relative_to(package)
import numpy as np
from backend.engine.flowchart_engine import FlowchartEngine, get_single_detection_flowchart, get_single_segmentation_flowchart
engine = FlowchartEngine(device='cpu')
pixels = np.zeros((32, 32, 3), dtype=np.uint8)
regions = [{'id':'full_image','label':'Full image','bbox':[0,0,32,32]}]
class NativeBranchReached(Exception): pass
def native_branch(*args, **kwargs): raise NativeBranchReached()
engine._get_inspection_model = native_branch
inspection = get_single_segmentation_flowchart('job_native').nodes[1]
inspection.data.task = 'classification'
try: engine._inspect_crops(pixels, regions, inspection)
except NativeBranchReached: pass
else: raise AssertionError('Native inspection loader was bypassed')
engine._extract_candidate_rois = native_branch
pipeline = get_single_detection_flowchart('job_native')
try: engine._detect_in_regions(pixels, regions, pipeline.nodes[1])
except NativeBranchReached: pass
else: raise AssertionError('Native detector was bypassed')
# Exercise real graph orchestration/artifact collection without executing a model.
engine._extract_candidate_rois = lambda *args: ([], 0.0, 'passed')
result = engine.execute(pipeline=pipeline, image=pixels)
assert next(step for step in result['execution_steps'] if step['node_id'] == pipeline.nodes[1].id)['status'] == 'passed'
assert not any(artifact.get('kind') == 'geti_model_prediction'
    for step in result['execution_steps'] for artifact in step['artifacts'])
assert not any(name == 'backend.platforms' or name.startswith('backend.platforms.') for name in sys.modules)
print(json.dumps({'native_inspection':True,'native_detection':True,'native_graph_artifacts':True,'platform_imports':False}))
'''


def test_native_compact_package_import_inspect_detect_and_artifacts_never_load_geti(tmp_path):
    package = build_package(tmp_path)
    assert not (package/'backend/platforms').exists()
    result = subprocess.run([sys.executable,'-I','-c',NATIVE_PROBE,str(package)],cwd=tmp_path,
        env={**os.environ,'PYTHONPATH':''},capture_output=True,text=True,timeout=60)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {'native_inspection':True,'native_detection':True,'native_graph_artifacts':True,'platform_imports':False}


@pytest.mark.parametrize('kind',['inspection','detection'])
def test_explicit_geti_reference_keeps_authorized_adapter_dispatch(kind,monkeypatch):
    from backend.platforms.geti import inspection
    token='geti:'+'a'*32+':00000000-0000-4000-8000-000000000001:00000000-0000-4000-8000-000000000002'
    node=(get_single_segmentation_flowchart(token) if kind=='inspection' else get_single_detection_flowchart(token)).nodes[1]
    engine=FlowchartEngine(device='cpu');pixels=np.zeros((32,32,3),dtype=np.uint8);regions=[{'id':'full_image','bbox':[0,0,32,32]}]
    calls=[];expected=([{'owned':'adapter'}],1.5,'passed')
    def dispatch(*args):calls.append(args);return expected
    session=SimpleNamespace(inspect=dispatch,detect=dispatch)
    def active(job):assert job==token;return session
    monkeypatch.setattr(inspection,'active_session',active)
    actual=engine._inspect_crops(pixels,regions,node) if kind=='inspection' else engine._detect_in_regions(pixels,regions,node)
    assert actual is expected and calls==[(pixels,regions,node)]


def test_explicit_geti_without_authorized_scope_never_falls_back_to_native(monkeypatch):
    from backend.engine.flowchart_engine import FlowchartInspectionConfigurationError
    engine=FlowchartEngine(device='cpu');pixels=np.zeros((32,32,3),dtype=np.uint8)
    node=get_single_segmentation_flowchart('geti:unbound').nodes[1]
    monkeypatch.setattr(engine,'_get_inspection_model',lambda **kwargs:pytest.fail('Geti reference reached native checkpoint loader'))
    with pytest.raises(FlowchartInspectionConfigurationError,match='authorized inspection binding'):
        engine._inspect_crops(pixels,[{'id':'full_image','bbox':[0,0,32,32]}],node)


def test_unavailable_explicit_geti_adapter_never_falls_back_to_native(monkeypatch):
    import builtins
    original=builtins.__import__
    def unavailable(name,*args,**kwargs):
        if name=='backend.platforms.geti.inspection':raise ModuleNotFoundError('Owned Geti adapter unavailable')
        return original(name,*args,**kwargs)
    monkeypatch.setattr(builtins,'__import__',unavailable)
    engine=FlowchartEngine(device='cpu');pixels=np.zeros((32,32,3),dtype=np.uint8)
    node=get_single_segmentation_flowchart('geti:unbound').nodes[1]
    monkeypatch.setattr(engine,'_get_inspection_model',lambda **kwargs:pytest.fail('Unavailable Geti adapter reached native checkpoint loader'))
    with pytest.raises(ModuleNotFoundError,match='Owned Geti adapter unavailable'):
        engine._inspect_crops(pixels,[{'id':'full_image','bbox':[0,0,32,32]}],node)
