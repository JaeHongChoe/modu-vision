"""Frozen dispatcher executes verified exported code in its own namespace."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[2]


def _package(tmp_path):
    root=tmp_path/'package';(root/'backend'/'engine').mkdir(parents=True)
    sources={'backend/__init__.py':'', 'backend/engine/__init__.py':'',
             'pipeline.json':'{}', 'backend/engine/flow_package_runtime.py':'''import argparse,json
from pathlib import Path
def main():
    p=argparse.ArgumentParser();p.add_argument('--image');p.add_argument('--device');p.add_argument('--output');a=p.parse_args()
    Path(a.output).write_text(json.dumps({'runtime_origin':__file__,'device':a.device,'image':a.image}))
    return 0
def worker_main():
    import sys
    root,request,output=sys.argv[1:4]
    Path(output).write_text(json.dumps({'runtime_origin':__file__,'request':json.loads(Path(request).read_text())}))
''', 'run_flow.py':"from backend.engine.flow_package_runtime import main\nraise SystemExit(main())\n"}
    rows=[]
    for relative,text in sources.items():
        file=root/relative;file.write_text(text);rows.append({'path':relative,'sha256':hashlib.sha256(file.read_bytes()).hexdigest(),'size':file.stat().st_size})
    (root/'manifest.json').write_text(json.dumps({'schema_version':1,'files':rows,'models':[]}))
    digest=hashlib.sha256((root/'manifest.json').read_bytes()).hexdigest()
    return root,digest


def _run(root,digest,output):
    return subprocess.run([sys.executable,str(ROOT/'scripts'/'frozen_backend_entry.py'),'--flow-package-runner',
        '--package',str(root),'--manifest-sha256',digest,'--image',str(root/'image.png'),'--device','cpu','--output',str(output)],
        cwd=ROOT,env={**os.environ,'PYTHONPATH':str(ROOT)},capture_output=True,text=True,timeout=30)


def test_dispatch_executes_exported_runtime_instead_of_bundled_backend(tmp_path):
    package,digest=_package(tmp_path);output=tmp_path/'result.json'
    result=_run(package,digest,output)
    assert result.returncode==0,result.stderr
    assert Path(json.loads(output.read_text())['runtime_origin'])==package/'backend'/'engine'/'flow_package_runtime.py'
    assert 'VISION_AI_STUDIO_PORT=' not in result.stdout


def test_dispatch_rejects_changed_manifest_and_code_before_any_exported_execution(tmp_path):
    package,digest=_package(tmp_path);output=tmp_path/'result.json'
    runner=package/'run_flow.py';runner.write_text("raise RuntimeError('untrusted code executed')")
    result=_run(package,digest,output)
    assert result.returncode!=0
    assert 'checksum' in result.stderr.lower()
    assert 'untrusted code executed' not in result.stderr
    assert not output.exists()


def test_dispatch_deadline_worker_executes_the_verified_exported_worker(tmp_path):
    package,digest=_package(tmp_path);output=tmp_path/'worker-result.json';request=tmp_path/'request.json'
    request.write_text(json.dumps({'image_path':'owned-input','options':{'device':'cpu'}}))
    result=subprocess.run([sys.executable,str(ROOT/'scripts'/'frozen_backend_entry.py'),'--flow-package-worker',
        '--package',str(package),'--manifest-sha256',digest,'--request',str(request),'--output',str(output)],
        cwd=ROOT,env={**os.environ,'PYTHONPATH':str(ROOT)},capture_output=True,text=True,timeout=30)
    assert result.returncode==0,result.stderr
    actual=json.loads(output.read_text())
    assert Path(actual['runtime_origin'])==package/'backend'/'engine'/'flow_package_runtime.py'
    assert actual['request']['image_path']=='owned-input'
