"""A model deadline owns and kills execution; SDKs run the verified full graph."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest
import torch
from PIL import Image

from backend.engine.flow_package import build_flow_package
from backend.engine.flowchart_engine import get_single_segmentation_flowchart
from backend.engine.segmentation import build_segmentation_model


@pytest.fixture
def real_package(tmp_path):
    model = build_segmentation_model('unet', num_classes=2, pretrained=False)
    with torch.no_grad():
        for parameter in model.parameters(): parameter.zero_()
        model.head.bias[1] = 8
    checkpoint = tmp_path/'model'/'best_model.pt'; checkpoint.parent.mkdir()
    torch.save({'task':'segmentation','model_name':'unet','classes':['background','defect'],
                'image_size':[32,32],'model_state_dict':model.state_dict()}, checkpoint)
    graph = get_single_segmentation_flowchart('job_real_runtime')
    graph.nodes[1].data.crop_padding=0
    graph.nodes[1].data.params={'min_defect_area_px':1}
    package = Path(build_flow_package(pipeline=graph, checkpoints={'job_real_runtime':checkpoint},
        output_base_dir=tmp_path/'exports',package_name='sdk_graph')['package_path'])
    image=tmp_path/'image.png'; Image.new('RGB',(48,32),'gray').save(image)
    return package,image


def test_deadline_terminates_a_child_inference_and_retains_review_receipt(tmp_path):
    from backend.engine.runtime_deadline import execute_owned_process
    marker=tmp_path/'finished'
    program="import pathlib,time;time.sleep(2);pathlib.Path(__import__('sys').argv[1]).write_text('escaped')"
    started=time.monotonic()
    outcome=execute_owned_process([sys.executable,'-c',program,str(marker)],deadline_ms=120)
    assert time.monotonic()-started<1
    assert outcome['status']=='timeout' and outcome['final_verdict']=='REVIEW'
    assert outcome['deadline']['terminated'] is True
    assert outcome['deadline']['deadline_ms']==120
    time.sleep(.15)
    assert not marker.exists()
    with pytest.raises(ProcessLookupError): os.kill(outcome['deadline']['pid'],0)


def test_python_predictor_executor_use_same_full_dag_and_strict_options(real_package):
    from backend.engine.flow_package_runtime import Predictor, Executor, compare_flow_results
    package,image=real_package
    predictor=Predictor(package,deadline_ms=30000,cpu_threads=1)
    result=predictor.predict(image,image_id='explicit-image')
    reopened=Executor(package,deadline_ms=30000,cpu_threads=1).execute({'image_path':str(image),'image_id':'explicit-image'})
    assert result['final_verdict']=='NG' and result['roi_count']==1
    assert compare_flow_results(result,reopened)['status']=='passed'
    assert result['runtime_execution']['isolated_process'] is True
    assert result['runtime_execution']['cpu_threads']==1
    with pytest.raises(ValueError,match='deadline'): Predictor(package,deadline_ms=True)
    with pytest.raises(ValueError,match='unknown|Unknown'): Executor(package).execute({'image_path':str(image),'fake':1})


def test_runtime_package_deadline_survives_reopen_and_cli(real_package,tmp_path):
    from backend.engine.flow_package_runtime import Predictor
    package,image=real_package
    # A one millisecond budget also includes model initialization, never a late NG.
    result=Predictor(package,deadline_ms=1).predict(image)
    assert result['status']=='timeout' and result['final_verdict']=='REVIEW'
    assert result['deadline']['terminated'] is True
    process=subprocess.run([sys.executable,str(package/'run_flow.py'),'--image',str(image),
                            '--deadline-ms','1'],capture_output=True,text=True,encoding="utf-8",timeout=20)
    assert process.returncode==3,process.stderr
    assert json.loads(process.stdout)['status']=='timeout'


def test_packaged_cpp_native_sdk_embeds_python_and_matches_whole_graph(real_package,tmp_path):
    from backend.engine.flow_package_runtime import Predictor,compare_flow_results
    package,image=real_package
    build=package/'native_runtime'/'build_native.py'
    assert build.is_file(),'A native C ABI and C++ SDK must be bundled'
    compiled=subprocess.run([sys.executable,str(build),'--output',str(tmp_path/'native')],capture_output=True,text=True,encoding="utf-8",timeout=45)
    assert compiled.returncode==0,compiled.stderr
    command=[str(tmp_path/'native'/'vision_predict'),str(package),str(image)]
    process=subprocess.run(command,capture_output=True,text=True,encoding="utf-8",timeout=45)
    assert process.returncode==0,process.stderr
    native=json.loads(process.stdout)
    assert compare_flow_results(Predictor(package,deadline_ms=30000).predict(image),native)['status']=='passed'
    timed=subprocess.run(command+['1'],capture_output=True,text=True,encoding="utf-8",timeout=20)
    assert timed.returncode==3 and json.loads(timed.stdout)['status']=='timeout'
    marker=tmp_path/'unverified-code-executed'
    bridge=package/'backend/engine/native_runtime_bridge.py'
    bridge.write_text(bridge.read_text()+f'\nfrom pathlib import Path\nPath({str(marker)!r}).write_text("escaped")\n')
    rejected=subprocess.run(command,capture_output=True,text=True,encoding="utf-8",timeout=20)
    assert rejected.returncode!=0 and 'checksum' in rejected.stderr.lower()
    assert not marker.exists(),'Native create must verify package code before importing it'


def test_csharp_pinvoke_calls_the_same_native_executor(real_package,tmp_path):
    import shutil
    dotnet=shutil.which('dotnet') or os.environ.get('VISION_TEST_DOTNET')
    if not dotnet: pytest.skip('A .NET 8 SDK is required for the C# compilation gate')
    from backend.engine.flow_package_runtime import Predictor,compare_flow_results
    package,image=real_package;native=tmp_path/'native';csharp=native/'csharp'
    compiled=subprocess.run([sys.executable,str(package/'native_runtime/build_native.py'),'--output',str(native)],capture_output=True,text=True,encoding="utf-8",timeout=45)
    assert compiled.returncode==0,compiled.stderr
    build=subprocess.run([dotnet,'build',str(package/'native_runtime/VisionRuntime.csproj'),'-o',str(csharp)],capture_output=True,text=True,encoding="utf-8",timeout=90)
    assert build.returncode==0,build.stdout+build.stderr
    for artifact in native.glob('*modu_vision_runtime*'):
        if artifact.is_file(): shutil.copyfile(artifact,csharp/artifact.name)
    execute=[dotnet,str(csharp/'VisionRuntime.dll'),str(package),str(image)]
    process=subprocess.run(execute,capture_output=True,text=True,encoding="utf-8",timeout=45)
    assert process.returncode==0,process.stdout+process.stderr
    assert compare_flow_results(Predictor(package).predict(image),json.loads(process.stdout))['status']=='passed'
    timeout=subprocess.run(execute+['1'],capture_output=True,text=True,encoding="utf-8",timeout=25)
    assert timeout.returncode==3 and json.loads(timeout.stdout)['deadline']['terminated'] is True
