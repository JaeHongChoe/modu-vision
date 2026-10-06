"""Execute the exact anomaly candidate in a fresh offline process."""
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

repo = Path(__file__).resolve().parents[3]; sys.path.insert(0, str(repo))
from backend.engine.flow_package import build_flow_package
from backend.engine.flowchart_engine import FlowchartPipeline
from backend.engine.flow_package_runtime import compare_flow_results, verify_flow_package

req = json.load(sys.stdin); project = Path(req['project_dir']).resolve(); job = req['job_id']
assert job.startswith('job_') and '/' not in job and '\\' not in job
checkpoint = project / 'models' / job / 'best_model.pt'
package = build_flow_package(pipeline=FlowchartPipeline.model_validate(req['pipeline']), checkpoints={job: checkpoint},
                             output_base_dir=project.parent / 'anomaly-packages', package_name=req['method'] + '-' + job)
root = Path(package['package_path']); output = root.parent / (root.name + '-result.json')
assert not output.exists()
sha = lambda file: hashlib.sha256(file.read_bytes()).hexdigest()
clones = []
if sys.platform == 'darwin':
    # Preserve every byte and path while avoiding duplicate physical extents.
    # The product builder has already completed its normal copy/hash checks.
    for target in (root / 'models').rglob('*.pt'):
        if sha(target) != sha(checkpoint): continue
        temporary = target.with_suffix('.cow-temp')
        assert not target.is_symlink() and not temporary.exists()
        before = sha(target)
        subprocess.run(['/bin/cp', '-cp', str(checkpoint), str(temporary)], check=True)
        assert sha(temporary) == before and temporary.stat().st_ino != checkpoint.stat().st_ino
        temporary.replace(target); clones.append({'path': str(target), 'sha256': before, 'independent_inode': True})
verify_flow_package(root)
run = subprocess.run([sys.executable, str(root / 'run_flow.py'), '--image', req['source'], '--device', 'cpu', '--output', str(output)],
                     cwd=root, env={**os.environ, 'PYTHONPATH': '', 'HF_HUB_OFFLINE': '1'},
                     capture_output=True, text=True, timeout=55)
assert run.returncode == 0, run.stderr
result = json.loads(output.read_text()); comparison = compare_flow_results(req['flow_result'], result)
assert comparison['status'] == 'passed', comparison
verify_flow_package(root); assert 'release_policy' not in package
print(json.dumps({'package': package, 'manifest_sha256': sha(root / 'manifest.json'), 'checkpoint_sha256': sha(checkpoint),
                  'output': str(output), 'result_sha256': sha(output), 'comparison': comparison,
                  'content_preserving_clones': clones, 'fresh_package_process': True, 'candidate_only': True}))
