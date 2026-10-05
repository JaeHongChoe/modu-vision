"""Fresh offline candidate executes both exact trained detector and dense model."""
import hashlib, json, os, subprocess, sys
from pathlib import Path
repo = Path(__file__).resolve().parents[3]; sys.path.insert(0, str(repo))
from backend.engine.flow_package import build_flow_package
from backend.engine.flowchart_engine import FlowchartPipeline
from backend.engine.flow_package_runtime import compare_flow_results, verify_flow_package
req = json.load(sys.stdin); project = Path(req['project_dir']).resolve()
jobs = req['job_ids']; assert len(jobs) == 2 and len(set(jobs)) == 2
assert all(job.startswith('job_') and '/' not in job and '\\' not in job for job in jobs)
checkpoints = {job: project / 'models' / job / 'best_model.pt' for job in jobs}
assert all(file.is_file() and file.resolve().is_relative_to(project) for file in checkpoints.values())
pipeline = FlowchartPipeline.model_validate(req['pipeline'])
assert {node.data.model_job_id for node in pipeline.nodes if node.data.node_type in ('detection_crop', 'inspection')} == set(jobs)
package = build_flow_package(pipeline=pipeline, checkpoints=checkpoints,
    output_base_dir=project.parent / 'yolo-recipe-packages', package_name='yolo-' + jobs[0])
root = Path(package['package_path']); output = root.parent / (root.name + '-result.json')
assert not output.exists()
run = subprocess.run([sys.executable, str(root / 'run_flow.py'), '--image', req['source'],
    '--device', 'cpu', '--output', str(output)], cwd=root,
    env={**os.environ, 'PYTHONPATH': '', 'HF_HUB_OFFLINE': '1'}, capture_output=True, text=True, timeout=60)
assert run.returncode == 0, run.stderr
result = json.loads(output.read_text()); comparison = compare_flow_results(req['flow_result'], result)
assert comparison['status'] == 'passed', comparison
verify_flow_package(root); assert 'release_policy' not in package
sha = lambda file: hashlib.sha256(file.read_bytes()).hexdigest()
print(json.dumps({'package': package, 'manifest_sha256': sha(root / 'manifest.json'),
    'checkpoint_sha256': {job: sha(file) for job, file in checkpoints.items()}, 'output': str(output),
    'result_sha256': sha(output), 'comparison': comparison, 'fresh_package_process': True, 'candidate_only': True}))
