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


def test_owned_package_worker_skips_unlisted_startup_and_parent_environment(real_package, tmp_path, monkeypatch):
    """Observe real CPU inference and interpreter startup, not a stub worker."""
    import hashlib
    from backend.engine import runtime_deadline
    from backend.engine.flow_package_runtime import Predictor
    package, image = real_package
    marker, probe = tmp_path/'unlisted-startup-executed', tmp_path/'owned-worker-probe.json'
    (package/'sitecustomize.py').write_text(f'from pathlib import Path\nPath({str(marker)!r}).write_text("unverified startup")\n')
    inherited = {
        'OPENAI_API_KEY':'controlled-parent-secret', 'AWS_SECRET_ACCESS_KEY':'controlled-parent-secret',
        'VISION_AI_STUDIO_API_TOKEN':'controlled-parent-secret', 'VISION_RESOURCE_LEASE_DB':str(tmp_path/'foreign.sqlite3'),
        'PYTHONPATH':str(tmp_path/'parent-plugin'), 'PYTHONUSERBASE':str(tmp_path/'parent-user-site'),
        'PYTHONSTARTUP':str(tmp_path/'parent-startup.py'), 'QT_PLUGIN_PATH':str(tmp_path/'parent-plugin'),
        'LD_PRELOAD':'', 'DYLD_INSERT_LIBRARIES':'',
    }
    for key,value in inherited.items():monkeypatch.setenv(key,value)
    monkeypatch.setenv('HOME',str(tmp_path/'parent-home'))
    monkeypatch.setenv('VISION_PACKAGE_PARITY_IDENTITY','1')
    monkeypatch.setenv('CUDA_VISIBLE_DEVICES','')
    monkeypatch.setenv('OPENBLAS_NUM_THREADS','17')
    offline = ('HF_HUB_OFFLINE', 'TRANSFORMERS_OFFLINE', 'HF_DATASETS_OFFLINE')
    for key in offline:monkeypatch.setenv(key,'1')
    observed = {}
    execute = runtime_deadline.execute_owned_process
    def inspect_real_child(command, *, deadline_ms, env=None, cwd=None, cancel_event=None):
        observed.update(command=list(command), environment=dict(env), cwd=str(cwd))
        command=list(command)
        # Test-only bootstrap telemetry runs after interpreter startup. The
        # production command, environment and full packaged graph stay real.
        inspection=('import json,os,sys;from pathlib import Path;'
                    f'Path({str(probe)!r}).write_text(json.dumps({{"isolated":sys.flags.isolated,'
                    '"no_user_site":sys.flags.no_user_site,"dont_write_bytecode":sys.dont_write_bytecode,'
                    '"pycache_prefix":sys.pycache_prefix,"cwd":os.getcwd(),'
                    f'"inherited_present":[key for key in {list(inherited)!r} if key in os.environ],'
                    f'"offline":{{key:os.environ.get(key) for key in {list(offline)!r}}},'
                    '"home":os.environ.get("HOME"),"omp":os.environ.get("OMP_NUM_THREADS"),'
                    '"mkl":os.environ.get("MKL_NUM_THREADS"),"openblas":os.environ.get("OPENBLAS_NUM_THREADS")}));')
        command[command.index('-c')+1]=inspection+command[command.index('-c')+1]
        return execute(command,deadline_ms=deadline_ms,env=env,cwd=cwd,cancel_event=cancel_event)
    monkeypatch.setattr(runtime_deadline,'execute_owned_process',inspect_real_child)
    before={p.relative_to(package).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in package.rglob('*') if p.is_file()}
    result=Predictor(package,device='cpu',deadline_ms=30000,cpu_threads=1).predict(image,'owned-startup-proof')
    (tmp_path/'owned-worker-result.json').write_text(json.dumps(result,ensure_ascii=False),encoding='utf-8')
    (tmp_path/'package-before.json').write_text(json.dumps(before,sort_keys=True),encoding='utf-8')
    assert result['final_verdict']=='NG' and result['runtime_device_identity']['device']=='cpu'
    assert result['runtime_device_identity']['cuda_visible_devices']==''
    assert not marker.exists(),'Unlisted package-root sitecustomize ran before verified runtime imports'
    child=json.loads(probe.read_text())
    assert child['isolated']==child['no_user_site']==1 and child['dont_write_bytecode'] is True
    assert child['inherited_present']==[]
    assert child['offline']=={key:'1' for key in offline}
    assert child['omp']==child['mkl']==child['openblas']=='1'
    owned=Path(observed['cwd'])
    assert owned!=package and owned.name.startswith('vision-inference-')
    assert Path(child['pycache_prefix'])==owned/'bytecode' and Path(child['home']).is_relative_to(owned)
    assert observed['command'][1:3]==['-I','-B']
    assert observed['command'][3:5]==['-X','pycache_prefix='+str(owned/'bytecode')]
    assert observed['command'][-3:]==[str(package),str(owned/'request.json'),str(owned/'result.json')]
    assert not owned.exists(),'Owned inference temporary state must be removed after exit'
    assert before=={p.relative_to(package).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in package.rglob('*') if p.is_file()}


def test_runtime_ignores_unlisted_bytecode_and_preserves_all_package_bytes(real_package,tmp_path):
    import hashlib,importlib.util,py_compile,struct
    from backend.engine.flow_package_runtime import Predictor
    package,image=real_package
    verified=package/'backend/engine/runtime_configuration.py'
    marker=tmp_path/'unlisted-bytecode-executed'
    poison=tmp_path/'controlled-poison.py'
    poison.write_text(f'from pathlib import Path\nPath({str(marker)!r}).write_text("unverified")\nraise RuntimeError("unlisted cached code")\n')
    cache=Path(importlib.util.cache_from_source(str(verified)));cache.parent.mkdir(exist_ok=True)
    py_compile.compile(str(poison),cfile=str(cache),doraise=True)
    raw=cache.read_bytes();stat=verified.stat()
    # Timestamp header names the checksum-verified source; the unlisted payload differs.
    cache.write_bytes(raw[:8]+struct.pack('<II',int(stat.st_mtime)&0xffffffff,stat.st_size)+raw[16:])
    before={p.relative_to(package).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in package.rglob('*') if p.is_file()}
    result=Predictor(package,deadline_ms=30000,cpu_threads=1).predict(image)
    assert result['final_verdict']=='NG' and not marker.exists()
    direct=subprocess.run([sys.executable,str(package/'run_flow.py'),'--image',str(image),
        '--deadline-ms','30000'],capture_output=True,text=True,timeout=40)
    assert direct.returncode==0,direct.stderr
    assert json.loads(direct.stdout)['final_verdict']=='NG' and not marker.exists()
    assert before=={p.relative_to(package).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in package.rglob('*') if p.is_file()}


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
    # Native startup must ignore the same source-looking unlisted bytecode as
    # Python SDK startup, before the runtime bridge or configuration is imported.
    import hashlib,importlib.util,py_compile,struct
    cached_marker=tmp_path/'unlisted-native-bytecode-executed'
    controlled=tmp_path/'controlled-native-cache.py'
    controlled.write_text(f'from pathlib import Path\nPath({str(cached_marker)!r}).write_text("unverified")\nraise RuntimeError("unlisted native cached code")\n')
    for name in ('native_runtime_bridge.py','runtime_configuration.py'):
        source=package/'backend/engine'/name
        cache=Path(importlib.util.cache_from_source(str(source)));cache.parent.mkdir(exist_ok=True)
        py_compile.compile(str(controlled),cfile=str(cache),doraise=True)
        raw=cache.read_bytes();stat=source.stat()
        cache.write_bytes(raw[:8]+struct.pack('<II',int(stat.st_mtime)&0xffffffff,stat.st_size)+raw[16:])
    snapshot={p.relative_to(package).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in package.rglob('*') if p.is_file()}
    cached=subprocess.run(command,capture_output=True,text=True,encoding='utf-8',timeout=45)
    assert cached.returncode==0,cached.stderr
    assert json.loads(cached.stdout)['final_verdict']=='NG' and not cached_marker.exists()
    assert snapshot=={p.relative_to(package).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in package.rglob('*') if p.is_file()}
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


@pytest.mark.skipif(sys.platform != 'linux', reason='Linux shared-library loader contract')
def test_local_dlopen_host_can_import_python_extensions(real_package, tmp_path):
    """A P/Invoke-style local loader must preserve verified full-graph execution."""
    import shutil
    from backend.engine.flow_package_runtime import Predictor, compare_flow_results
    package, image = real_package
    native = tmp_path / 'native'
    built = subprocess.run([sys.executable, str(package / 'native_runtime/build_native.py'), '--output', str(native)],
                           capture_output=True, text=True, encoding='utf-8', timeout=60)
    assert built.returncode == 0, built.stdout + built.stderr
    host = tmp_path / 'local-loader.cpp'
    host.write_text(r'''
#include <dlfcn.h>
#include <cstdio>
int main(int argc, char** argv) {
    if (argc != 4) return 1;
    void* library = dlopen(argv[1], RTLD_NOW | RTLD_LOCAL);
    if (!library) { std::fprintf(stderr, "%s\n", dlerror()); return 1; }
    auto create = reinterpret_cast<void*(*)(const char*, const char*, char**)>(dlsym(library, "mv_create"));
    auto predict = reinterpret_cast<int(*)(void*, const char*, const char*, char**)>(dlsym(library, "mv_predict"));
    auto release = reinterpret_cast<void(*)(void*)>(dlsym(library, "mv_release"));
    auto free_result = reinterpret_cast<void(*)(char*)>(dlsym(library, "mv_free"));
    if (!create || !predict || !release || !free_result) return 1;
    char* output = nullptr;
    void* handle = create(argv[2], "{\"deadline_ms\":30000}", &output);
    if (!handle) { std::fprintf(stderr, "%s\n", output ? output : "No handle"); free_result(output); return 1; }
    int status = predict(handle, argv[3], nullptr, &output);
    std::printf("%s\n", output ? output : "null");
    free_result(output); release(handle);
    // CPython retains the runtime for the lifetime of the embedding process.
    return status;
}
''', encoding='utf-8')
    compiled = subprocess.run([shutil.which('g++'), '-std=c++17', str(host), '-ldl', '-o', str(tmp_path / 'local-loader')],
                              capture_output=True, text=True, encoding='utf-8', timeout=60)
    assert compiled.returncode == 0, compiled.stderr
    command = [str(tmp_path / 'local-loader'), str(native / 'libmodu_vision_runtime.so'), str(package), str(image)]
    result = subprocess.run(command, capture_output=True, text=True, encoding='utf-8', timeout=45)
    assert result.returncode == 0, result.stdout + result.stderr
    assert compare_flow_results(Predictor(package).predict(image), json.loads(result.stdout))['status'] == 'passed'
    marker = tmp_path / 'unverified-code-executed'
    bridge = package / 'backend/engine/native_runtime_bridge.py'
    bridge.write_text(bridge.read_text(encoding='utf-8') + f'\nfrom pathlib import Path\nPath({str(marker)!r}).write_text("escaped")\n', encoding='utf-8')
    refused = subprocess.run(command, capture_output=True, text=True, encoding='utf-8', timeout=20)
    assert refused.returncode != 0 and 'checksum' in refused.stderr.lower()
    assert not marker.exists()
