"""Run an exported inspection flow with the exact bundled graph engine."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import sys
import tempfile
from typing import Any

from backend.engine.flowchart_engine import FlowchartEngine, FlowchartPipeline, ordered_linear_nodes
from backend.engine.industrial_adapters import read_image_safely_rgb
from backend.engine.specialized_models import flow_model_task


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _package_file(root: Path, relative: str) -> Path:
    if not isinstance(relative, str) or not relative or "\\" in relative:
        raise ValueError("Invalid package file path")
    part = PurePosixPath(relative)
    if part.is_absolute() or any(segment in ("", ".", "..") for segment in relative.split("/")):
        raise ValueError("Invalid package file path")
    path = root.joinpath(*part.parts)
    if path.is_symlink() or any(parent.is_symlink() for parent in path.parents if parent != root and root in parent.parents):
        raise ValueError(f"Package file is a symbolic link: {relative}")
    if not path.is_file() or not path.resolve().is_relative_to(root.resolve()):
        raise ValueError(f"Package file is missing or outside the package: {relative}")
    return path


def verify_flow_package(package_dir: Path) -> tuple[FlowchartPipeline, dict[str, Path]]:
    """Reject changed files and model links before loading a PyTorch checkpoint."""
    root = Path(package_dir).resolve()
    manifest_path = _package_file(root, "manifest.json")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict) or manifest.get("schema_version") != 1:
        raise ValueError("Unsupported flow package manifest")
    files = manifest.get("files")
    models = manifest.get("models")
    if not isinstance(files, list) or not isinstance(models, list) or not files or not models:
        raise ValueError("Incomplete flow package manifest")
    seen: set[str] = set()
    for row in files:
        if not isinstance(row, dict) or not isinstance(row.get("path"), str) or row["path"] in seen:
            raise ValueError("Duplicate or invalid package file entry")
        seen.add(row["path"])
        path = _package_file(root, row["path"])
        if path.stat().st_size != row.get("size") or _sha256(path) != row.get("sha256"):
            raise ValueError(f"Package checksum mismatch: {row['path']}")
    if "pipeline.json" not in seen or "run_flow.py" not in seen:
        raise ValueError("Flow package has no graph or runner")
    if any(path.relative_to(root).as_posix() not in seen for path in (root/'backend').rglob('*.py')):
        raise ValueError('Flow package contains unlisted runtime code')
    if 'runtime' in manifest:
        from backend.engine.runtime_configuration import runtime_options
        if 'runtime_config.json' not in seen or runtime_options(json.loads((root/'runtime_config.json').read_text(encoding='utf-8')))!=manifest['runtime']:
            raise ValueError('Runtime configuration differs from the verified manifest')
    pipeline = FlowchartPipeline.model_validate(json.loads((root / "pipeline.json").read_text(encoding="utf-8")))
    ordered_linear_nodes(pipeline)
    from backend.engine.spatial_calibration import calibration_refs, package_calibrations
    needed = calibration_refs(pipeline)
    if needed - set(manifest.get('calibrations') or []):
        raise ValueError('The flow package does not list every calibration its flow measures with')
    for ref in needed:
        if f"calibrations/{ref.rsplit(':', 1)[-1]}.json" not in seen or package_calibrations(root).load(ref) is None:
            raise ValueError(f'The flow package lacks calibration {ref}')
    from backend.engine.fixture_flow import fixture_refs, package_fixtures
    for ref in fixture_refs(pipeline):
        digest = ref.split(":", 1)[1]
        required = {f"fixture_references/{digest}.json", f"fixture_references/{digest}.png", "fixture_references/active.json"}
        if ref not in (manifest.get("fixtures") or []) or not required <= seen or package_fixtures(root).load(ref) is None:
            raise ValueError(f"Missing or stale packaged fixture {ref}")
    expected = {
        (node.data.model_job_id, flow_model_task(node))
        for node in pipeline.nodes if flow_model_task(node) is not None
    }
    checkpoints: dict[str, Path] = {}
    found: set[tuple[str, str]] = set()
    for row in models:
        if not isinstance(row, dict):
            raise ValueError("Invalid model entry")
        job_id, task, relative = row.get("job_id"), row.get("task"), row.get("checkpoint")
        if not isinstance(job_id, str) or not isinstance(task, str) or not isinstance(relative, str):
            raise ValueError("Invalid model entry")
        if relative != f"models/{job_id}/best_model.pt" or relative not in seen:
            raise ValueError("Model checkpoint is missing from the package manifest")
        if job_id in checkpoints or (job_id, task) in found:
            raise ValueError("Duplicate model job in the package manifest")
        checkpoints[job_id] = _package_file(root, relative)
        found.add((job_id, task))
    if found != expected:
        raise ValueError("Packaged model jobs do not match the saved graph")
    if 'openvino_models.json' in seen:
        converted=json.loads((root/'openvino_models.json').read_text(encoding='utf-8'))
        records=converted.get('models',[]) if isinstance(converted,dict) else []
        if len(records)!=len(models) or {(row.get('job_id'),row.get('task')) for row in records}!=found:
            raise ValueError('OpenVINO model artifacts do not cover the complete saved graph')
        for row in records:
            directory=row.get('directory')
            if directory!=f"models/{row['job_id']}/openvino" or row.get('checkpoint')!=f"models/{row['job_id']}/best_model.pt":
                raise ValueError('OpenVINO artifact leaves its owned model directory')
            if any(directory+'/'+name not in seen for name in ('model.xml','model.bin','conversion.json')):
                raise ValueError('OpenVINO artifact is absent from the package manifest')
        if 'release' in manifest:
            if 'runtime_acceptance.json' not in seen or _sha256(root/'runtime_acceptance.json')!=manifest.get('runtime_acceptance_sha256'):
                raise ValueError('Approved OpenVINO release requires explicit verified precision acceptance')
            acceptance=json.loads((root/'runtime_acceptance.json').read_text(encoding='utf-8'))
            if (acceptance.get('holdout_reviewed') is not True or acceptance.get('models')!=records
                or acceptance.get('input_receipt')!=converted.get('input_receipt')
                or acceptance.get('heldout_flow_results_sha256')!=converted.get('heldout_flow_results_sha256')
                or 'heldout_flow_results.json' not in seen or _sha256(root/'heldout_flow_results.json')!=acceptance.get('heldout_flow_results_sha256')
                or acceptance.get('runtime_configuration')!=manifest['runtime']
                or acceptance.get('approval_revisions')!=manifest['release']['approval_revisions']):
                raise ValueError('Precision acceptance differs from this exact runtime release')
    return pipeline, checkpoints


def run_flow_package(package_dir: Path, image_path: Path, image_id: str | None = None, *, device: str | None = None, deadline_ms: int | None = None, cpu_threads: int | None = None, _owned_worker: bool = False) -> dict[str, Any]:
    from backend.engine.runtime_configuration import runtime_options
    root=Path(package_dir).expanduser().resolve()
    config=root/'runtime_config.json'
    saved=json.loads(config.read_text(encoding='utf-8')) if config.is_file() else {}
    manifest=json.loads((root/'manifest.json').read_text(encoding='utf-8'))
    if manifest.get('runtime_acceptance_sha256') and device is not None and device!=saved.get('device'):
        raise ValueError('Reviewed precision runtime requires its explicitly accepted device')
    for key,value in [('device',device),('deadline_ms',deadline_ms),('cpu_threads',cpu_threads)]:
        if value is not None: saved[key]=value
    options=runtime_options(saved)
    device=options['device']
    if device.startswith('openvino:') and not any(row['path']=='openvino_models.json' for row in manifest['files']):
        raise ValueError('OpenVINO requires a verified converted package')
    if options['deadline_ms'] is not None and not _owned_worker:
        return _run_isolated(package_dir,image_path,image_id,options)
    from backend.engine.edge_runtime import enforce_edge_device
    pipeline, checkpoints = verify_flow_package(package_dir)
    enforce_edge_device(package_dir, device)
    from backend.engine.runtime_device import resolve_runtime_device
    openvino_device=device.split(':',1)[1] if device.startswith('openvino:') else None
    if openvino_device is None:device = str(resolve_runtime_device(device))
    parity_identity = None
    if os.environ.get('VISION_PACKAGE_PARITY_IDENTITY') == '1':
        from backend.engine.runtime_device_identity import runtime_device_identity
        parity_identity = runtime_device_identity(device)
    image = Path(image_path).expanduser().resolve()
    if not image.is_file():
        raise FileNotFoundError(f"Inspection image not found: {image}")
    try:
        probe = read_image_safely_rgb(image, max_dim=32)
        if probe.size == 0:
            raise ValueError("Empty image")
    except (OSError, ValueError) as exc:
        raise ValueError(f"Not a readable image: {image}") from exc

    def resolve(job_id: str, task: str) -> Path | None:
        return checkpoints.get(job_id)

    engine = FlowchartEngine(device='cpu' if openvino_device else device, checkpoint_resolver=resolve)
    from backend.engine.spatial_calibration import calibration_scope, package_calibrations
    from backend.engine.fixture_flow import fixture_scope, package_fixtures
    with calibration_scope(package_calibrations(root).load), fixture_scope(package_fixtures(root).load):
        if openvino_device:
            from backend.engine.openvino_runtime import OpenVINOSession
            with OpenVINOSession(root,openvino_device,options['cpu_threads']) as session:
                result=engine.execute(pipeline=pipeline,image_path=image,image_id=image_id)
                result['model_runtime']=session.receipt()
        else:result = engine.execute(pipeline=pipeline, image_path=image, image_id=image_id)
    result = result.model_dump() if hasattr(result, "model_dump") else result
    if parity_identity is not None:
        result['runtime_device_identity'] = parity_identity
    return result


def _run_isolated(package_dir, image_path, image_id, options, cancel_event=None):
    from backend.engine.runtime_deadline import execute_owned_process
    root=Path(package_dir).expanduser().resolve()
    # Validate graph and content before executing a packaged Python entry point.
    verify_flow_package(root)
    manifest=json.loads((root/'manifest.json').read_text(encoding='utf-8'))
    if options['device'].startswith('openvino:') and not any(row['path']=='openvino_models.json' for row in manifest['files']):
        raise ValueError('OpenVINO requires a verified converted package')
    if manifest.get('runtime_acceptance_sha256') and options['device']!=manifest['runtime']['device']:
        raise ValueError('Reviewed precision runtime requires its explicitly accepted device')
    with tempfile.TemporaryDirectory(prefix='vision-inference-') as temporary:
        request=Path(temporary)/'request.json';output=Path(temporary)/'result.json'
        request.write_text(json.dumps({'image_path':str(Path(image_path).expanduser().resolve()),'image_id':image_id,
                                       'options':options}),encoding='utf-8')
        bootstrap="import sys;sys.modules['pyarrow']=None;from backend.engine.flow_package_runtime import worker_main;worker_main()"
        explicit_openvino=options['device'].startswith('openvino:') and bool(os.environ.get('VISION_OPENVINO_PYTHON'))
        if getattr(sys,'frozen',False) and not explicit_openvino:
            # A frozen app cannot run -c; its worker re-verifies and runs this package's own runtime.
            command=[sys.executable,'--flow-package-worker','--package',str(root),'--manifest-sha256',_sha256(root/'manifest.json'),
                     '--request',str(request),'--output',str(output)]
        else:
            python=os.environ['VISION_OPENVINO_PYTHON'] if explicit_openvino else sys.executable
            command=[python,'-c',bootstrap,str(root),str(request),str(output)]
        env={**os.environ,'PYTHONPATH':str(root),'PYTHONNOUSERSITE':'1',
             'OMP_NUM_THREADS':str(options['cpu_threads']),'MKL_NUM_THREADS':str(options['cpu_threads'])}
        outcome=execute_owned_process(command,deadline_ms=options['deadline_ms'],env=env,cwd=root,cancel_event=cancel_event)
        if outcome['status'] in ('timeout','cancelled'):
            outcome['image_id']=image_id
            return outcome
        if outcome['returncode']!=0:
            raise RuntimeError(f"Owned inference failed: {outcome['stderr'] or outcome['stdout']}")
        if not output.is_file() or output.stat().st_size>128*1024*1024:
            raise RuntimeError('Owned inference returned no bounded result')
        result=json.loads(output.read_text(encoding='utf-8'))
        result['runtime_execution']={**options,'isolated_process':True,'pid':outcome['pid'],'elapsed_ms':outcome['elapsed_ms']}
        return result


def worker_main():
    root,request,output=map(Path,sys.argv[1:4])
    raw=json.loads(request.read_text(encoding='utf-8'))
    from backend.engine.runtime_configuration import runtime_options
    options=runtime_options(raw['options'])
    import torch
    torch.set_num_threads(options['cpu_threads'])
    result=run_flow_package(root,Path(raw['image_path']),raw.get('image_id'),device=options['device'],cpu_threads=options['cpu_threads'],_owned_worker=True)
    output.write_text(json.dumps(result,ensure_ascii=False),encoding='utf-8')


class Predictor:
    """File input, complete graph result; initialization is part of the budget."""
    def __init__(self,package_dir,*,device=None,deadline_ms=None,cpu_threads=None):
        from backend.engine.runtime_configuration import runtime_options
        self.package_dir=Path(package_dir).expanduser().resolve()
        verify_flow_package(self.package_dir)
        config=self.package_dir/'runtime_config.json'
        saved=json.loads(config.read_text(encoding='utf-8')) if config.is_file() else {}
        for key,value in [('device',device),('deadline_ms',deadline_ms),('cpu_threads',cpu_threads)]:
            if value is not None:saved[key]=value
        if saved.get('deadline_ms') is None:saved['deadline_ms']=300000
        self.options=runtime_options(saved)
        from backend.engine.runtime_deadline import CancellableExecution
        self._execution = CancellableExecution()

    def predict(self,image_path,image_id=None):
        if image_id is not None and (not isinstance(image_id,str) or len(image_id)>512):
            raise ValueError('image_id must be a string of at most 512 characters')
        with self._execution.running() as event:
            return _run_isolated(self.package_dir,image_path,image_id,self.options,cancel_event=event)

    def cancel(self):
        """Request cancellation of the active call; its returned status confirms the outcome."""
        return self._execution.cancel()


class Executor(Predictor):
    """Predictor contract with an explicit JSON request for native interop."""
    def execute(self,request):
        if not isinstance(request,dict) or set(request)-{'image_path','image_id'}:
            raise ValueError('Unknown executor input fields')
        if not isinstance(request.get('image_path'),str) or not request['image_path']:
            raise ValueError('Executor requires image_path')
        return self.predict(request['image_path'],request.get('image_id'))


def native_create(package_dir,options_json):
    options=json.loads(options_json or '{}')
    from backend.engine.runtime_configuration import runtime_options
    runtime_options(options)
    return Executor(package_dir,**options)


def native_execute(executor,input_json):
    return json.dumps(executor.execute(json.loads(input_json)),ensure_ascii=False)


def _semantic_equal(left,right):
    if isinstance(left,dict) and isinstance(right,dict):
        if left.get('dtype')==right.get('dtype')=='float32' and left.get('encoding')==right.get('encoding')=='zlib_base64':
            # Compression magnifies tiny CPU accumulation differences into unrelated
            # strings. Compare finite raster values; uint8 masks remain exact.
            if left.keys()!=right.keys() or set(left)!={'dtype','encoding','shape','data'} or left['shape']!=right['shape']:
                return False
            import numpy as np
            import zlib
            from backend.engine.segmentation_evidence import decoded_array
            try:
                a,b=decoded_array(left),decoded_array(right)
                return bool(np.isfinite(a).all() and np.isfinite(b).all() and np.max(np.abs(a.astype(np.float64)-b.astype(np.float64)))<=1e-6)
            except (ValueError,TypeError,KeyError,zlib.error):
                return False
        return left.keys()==right.keys() and all(_semantic_equal(left[key],right[key]) for key in left)
    if isinstance(left,list) and isinstance(right,list):
        return len(left)==len(right) and all(_semantic_equal(a,b) for a,b in zip(left,right))
    if type(left) in (int,float) and type(right) in (int,float) and (type(left)is float or type(right)is float):
        import math
        return math.isfinite(left) and math.isfinite(right) and abs(left-right)<=1e-4
    return left==right


def compare_flow_results(reference: dict[str, Any], packaged: dict[str, Any]) -> dict[str, Any]:
    """Compare decisions and spatial evidence, excluding machine-dependent timing."""
    fields = (
        "final_verdict", "roi_count", "defective_roi_count", "routed_output_node_id",
        "rejection_reason", "inspected_image_size", "execution_resources",
    )
    mismatches = [field for field in fields if reference.get(field) != packaged.get(field)]
    ref_steps, pkg_steps = reference.get("execution_steps", []), packaged.get("execution_steps", [])
    if len(ref_steps) != len(pkg_steps):
        mismatches.append("execution_steps.length")
    else:
        step_fields = (
            "node_id", "status", "input_payload_type", "output_payload_type",
            "input_count", "output_count", "branch_verdict",
            "selected_edge_ids", "skip_reason", "artifacts",
        )
        for index, (left, right) in enumerate(zip(ref_steps, pkg_steps)):
            for field in step_fields:
                if not _semantic_equal(left.get(field),right.get(field)):
                    mismatches.append(f"execution_steps[{index}].{field}")

    ref_crops, pkg_crops = reference.get("crops", []), packaged.get("crops", [])
    if len(ref_crops) != len(pkg_crops):
        mismatches.append("crops.length")
    else:
        for index, (left, right) in enumerate(zip(ref_crops, pkg_crops)):
            for field in (
                "roi_id", "source_node_id", "label", "bbox", "verdict",
                "flaw_type", "defect_area_px", "blob_count",
                "largest_blob_area_px", "tiles_processed", "recognized_text", "predicted_class", "polygon", "anomaly_map", "anomaly_values", "map_semantics", "mask", "source_transform",
                "segmentation_classes", "blob_measurements", "original_text", "corrected_text", "correction_applied", "rule_violations", "measurements", "execution_resources",
                "ocr_regions", "ocr_recipe", "ocr_rule_result",
            ):
                if not _semantic_equal(left.get(field),right.get(field)):
                    mismatches.append(f"crops[{index}].{field}")
            try:
                score_gap = abs(float(left.get("defect_score")) - float(right.get("defect_score")))
            except (TypeError, ValueError):
                score_gap = float("inf")
            if score_gap > 1e-4:
                mismatches.append(f"crops[{index}].defect_score")
    return {
        "status": "passed" if not mismatches else "mismatch",
        "mismatched_fields": mismatches,
        "compared_fields": [*fields, "execution_steps", "crops"],
        "final_verdict": packaged.get("final_verdict"),
        "roi_count": packaged.get("roi_count"),
    }


def run_flow_batch(package_dir: Path, input_manifest: Path, *, device=None,
                   deadline_ms=None, cpu_threads=None) -> dict[str, Any]:
    """Run explicit image identities sequentially through the verified executor."""
    input_manifest = Path(input_manifest).resolve()
    with input_manifest.open('rb') as source:
        raw = source.read(1024 * 1024 + 1)
    if len(raw) > 1024 * 1024:
        raise ValueError('Batch manifest exceeds one MiB')
    def unique_keys(pairs):
        record = {}
        for key, value in pairs:
            if key in record:
                raise ValueError('Duplicate batch manifest key')
            record[key] = value
        return record
    manifest = json.loads(raw, object_pairs_hook=unique_keys)
    if (not isinstance(manifest, dict) or set(manifest) != {'schema', 'images'}
            or manifest['schema'] != 'FlowBatchInput/v1'
            or not isinstance(manifest['images'], list)
            or not 1 <= len(manifest['images']) <= 1000):
        raise ValueError('Batch requires FlowBatchInput/v1 and one to 1000 images')
    inputs, identities = [], set()
    for row in manifest['images']:
        if not isinstance(row, dict) or set(row) - {'image_path', 'image_id', 'sha256'}:
            raise ValueError('Unknown or invalid batch image fields')
        image_id, image_path = row.get('image_id'), row.get('image_path')
        if not isinstance(image_id, str) or not image_id.strip() or len(image_id) > 256:
            raise ValueError('Batch image identity must be a nonempty string of at most 256 characters')
        if image_id in identities:
            raise ValueError('Duplicate image identity in batch')
        identities.add(image_id)
        if not isinstance(image_path, str) or not image_path.strip():
            raise ValueError('Batch image path must be nonempty')
        expected = row.get('sha256')
        if expected is not None and (not isinstance(expected, str) or len(expected) != 64
                or any(character not in '0123456789abcdef' for character in expected)):
            raise ValueError('Batch expected sha256 must be 64 lowercase hexadecimal characters')
        path = Path(image_path)
        if not path.is_absolute():
            path = input_manifest.parent / path
        inputs.append((image_id, path.resolve(), expected))
    # Validate the entire input manifest before initializing or executing models.
    executor = Executor(package_dir, device=device, deadline_ms=deadline_ms, cpu_threads=cpu_threads)
    package_sha = _sha256(Path(package_dir) / 'manifest.json')
    results = []
    for image_id, image, expected in inputs:
        observed = None
        reason = 'INPUT_ERROR'
        try:
            observed = _sha256(image)
            if expected is not None and observed != expected:
                raise ValueError('Batch input checksum mismatch')
            reason = 'EXECUTION_ERROR'
            result = executor.predict(image, image_id)
            reason = 'INPUT_CHANGED'
            if _sha256(image) != observed:
                raise ValueError('Batch input changed during execution')
        except (ValueError, OSError, RuntimeError) as error:
            result = {'status': 'error', 'image_id': image_id, 'final_verdict': 'REVIEW',
                      'rejection_reason': reason, 'error': str(error)}
        results.append({'image_id': image_id, 'image_path': str(image), 'input_sha256': observed,
                        'status': result['status'] if result.get('status') in ('error', 'timeout', 'cancelled')
                                  else 'completed', 'result': result})
    summary = {'total': len(results),
               'completed': sum(row['status'] == 'completed' for row in results),
               'errors': sum(row['status'] == 'error' for row in results),
               'timeouts': sum(row['status'] == 'timeout' for row in results),
               'cancelled': sum(row['status'] == 'cancelled' for row in results),
               'review': sum(row['result'].get('final_verdict') == 'REVIEW' for row in results)}
    return {'schema': 'FlowBatchResult/v1',
            'status': 'completed_with_review' if summary['review'] else 'completed',
            'input_manifest_sha256': hashlib.sha256(raw).hexdigest(),
            'package_manifest_sha256': package_sha, 'summary': summary, 'results': results}


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a saved Modu Vision inspection flow offline")
    parser.add_argument("--verify-only", action="store_true", help="Verify graph, code, and model checksums")
    parser.add_argument("--preflight", action="store_true",
                        help="Check every node's model, calibration, runtime and device on this computer and keep the report")
    parser.add_argument("--show-preflight", action="store_true", help="Show the last kept preflight report and whether it is current")
    parser.add_argument("--image", type=Path, help="Image to inspect")
    parser.add_argument("--batch", type=Path, help="FlowBatchInput/v1 image manifest; sequential per-image execution")
    parser.add_argument("--device", default=None, help="Explicit execution device; unavailable devices fail")
    parser.add_argument("--image-id", help="Optional source image ID")
    parser.add_argument("--deadline-ms",type=int,help="Hard wall time budget including model initialization")
    parser.add_argument("--cpu-threads",type=int)
    parser.add_argument("--output", type=Path, help="Write the complete JSON result here")
    args = parser.parse_args()
    if args.batch and (args.image or args.image_id or args.verify_only or args.preflight or args.show_preflight):
        parser.error('--batch cannot be combined with single-image or verification commands')
    root = Path(__file__).resolve().parents[2]
    try:
        if args.preflight or args.show_preflight:
            from backend.engine.flow_preflight import latest_package_preflight, package_preflight
            result = (package_preflight(root, device=args.device) if args.preflight
                      else latest_package_preflight(root, device=args.device) or {"status": "not_run"})
            payload = json.dumps(result, ensure_ascii=False, indent=2)
            if args.output:
                args.output.write_text(payload + "\n", encoding="utf-8")
            else:
                print(payload)
            return 4 if result.get("status") == "blocked" or result.get("stale") else 0
        if args.batch:
            result = run_flow_batch(root, args.batch, device=args.device, deadline_ms=args.deadline_ms,
                                    cpu_threads=args.cpu_threads)
        elif args.verify_only:
            pipeline, checkpoints = verify_flow_package(root)
            result = {"status": "verified", "pipeline_id": pipeline.id, "model_job_ids": sorted(checkpoints)}
        else:
            if args.image is None:
                parser.error("--image is required unless --verify-only is set")
            result = run_flow_package(root, args.image, args.image_id, device=args.device,deadline_ms=args.deadline_ms,cpu_threads=args.cpu_threads)
        payload = json.dumps(result, ensure_ascii=False, indent=2)
        if args.output:
            args.output.write_text(payload + "\n", encoding="utf-8")
        else:
            print(payload)
        if args.batch:
            return 2 if result['summary']['errors'] else 3 if result['summary']['timeouts'] or result['summary']['cancelled'] else 0
        return 3 if result.get('status')=='timeout' else 0
    except (ValueError, OSError, RuntimeError) as exc:
        parser.exit(2, f"Flow package error: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
