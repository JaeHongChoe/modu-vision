"""Independent fresh generation command exact candidate hashes; no quality approval."""
import json,os,subprocess,sys,hashlib
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[3]))
req=json.loads(sys.argv[1]);root=Path(req['package_path']);owned=Path(req['project_dir']).resolve()
assert root.resolve().is_relative_to(owned) and req['generator_sha256']==hashlib.sha256((root/'best_model.pt').read_bytes()).hexdigest()
output=owned/'generation-package-readback'
run=subprocess.run([sys.executable,str(root/'generate.py'),'--output',str(output),'--count','2','--seed','41'],cwd=root,env={**os.environ,'PYTHONPATH':'','HF_HUB_OFFLINE':'1'},capture_output=True,text=True,timeout=60)
assert run.returncode==0,run.stderr
generated=json.loads(run.stdout);assert [r['sha256'] for r in generated['candidates']]==[r['sha256'] for r in req['expected']]
assert all(r['status']=='synthetic_unreviewed' for r in generated['candidates'])
for r in generated['candidates']:assert hashlib.sha256(Path(r['path']).read_bytes()).hexdigest()==r['sha256']
print(json.dumps({'generation':generated,'fresh_process':True,'exact_pixel_parity':True,'quality_approved':False,'workflow':'generation_review_adoption','generator_sha256':req['generator_sha256']}))
