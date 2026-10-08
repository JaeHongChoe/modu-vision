"""Explicit retained-model E2E fixture; no framework is imported by prepare/check.

Only the root's separately authorized `package` action loads the genuine model
and starts two owned standalone runners. Original metadata/provenance is copied
byte for byte. This fixture cannot approve a model, release or human quality.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import struct
import sys
import zlib


def require(condition, reason):
    if not condition:
        raise ValueError(reason)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def document(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, 'Duplicate JSON field')
            result[key] = value
        return result
    return json.loads(raw, object_pairs_hook=pairs,
                      parse_constant=lambda value: require(False, 'Nonfinite JSON'))


def file_bytes(file):
    file = Path(file)
    require(not file.is_symlink(), 'Linked file')
    fd = os.open(file, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
    with os.fdopen(fd, 'rb') as handle:
        before = os.fstat(handle.fileno())
        require(stat.S_ISREG(before.st_mode), 'Not a regular file')
        raw = handle.read()
        after = os.fstat(handle.fileno())
        identity = lambda row: (row.st_dev, row.st_ino, row.st_size, row.st_mtime_ns, row.st_ctime_ns)
        require(identity(before) == identity(after) == identity(file.stat()), 'File changed during read')
    return raw, after


def tree(root, timestamps=False):
    root = Path(root)
    require(root.is_dir() and not root.is_symlink(), 'Missing or linked tree root')
    files, directories = {}, []
    for folder, names, members in os.walk(root, followlinks=False):
        names.sort(); members.sort()
        for name in names:
            item = Path(folder) / name
            require(not item.is_symlink(), 'Linked directory')
            directories.append(item.relative_to(root).as_posix())
        for name in members:
            item = Path(folder) / name
            raw, row = file_bytes(item)
            record = {'bytes': len(raw), 'sha256': sha(raw)}
            if timestamps:
                record.update(mtime_ns=row.st_mtime_ns, ctime_ns=row.st_ctime_ns)
            files[item.relative_to(root).as_posix()] = record
    return {'directories': sorted(directories), 'files': dict(sorted(files.items()))}


def binding(file, expected_sha):
    require(isinstance(expected_sha, str) and bool(re.fullmatch('[0-9a-f]{64}', expected_sha)), 'Binding hash required')
    raw, _ = file_bytes(file)
    require(sha(raw) == expected_sha, 'Binding bytes changed')
    value = document(raw)
    require(value.get('schema') == 'modu-vision.retained-unet-source-binding/v1', 'Binding schema')
    require(re.fullmatch(r'job_[0-9]+_[a-z0-9]+', value.get('job_id', '')) is not None, 'Job identity')
    require(value.get('task') == 'segmentation' and value.get('classes') == ['background', 'defect']
            and value.get('model_name') == 'unet', 'Original model semantics')
    require(value.get('copy_members') == ['best_model.pt', 'job_receipt.json', 'model_meta.json', 'eval_results.json'], 'Raw copy members')
    source = Path(value['source_dataset_path']); model = Path(value['original_model_dir'])
    require(source.is_absolute() and source.resolve(strict=True) == source
            and model.is_absolute() and model.resolve(strict=True) == model, 'Canonical retained roots')
    require(tree(source, True) == value['source_tree'] and tree(model, True) == value['original_model_tree'], 'Original retained tree changed')
    require(value['original_model_tree']['files']['best_model.pt']['sha256'] == value['checkpoint_sha256'], 'Checkpoint binding')
    receipt = document(file_bytes(model / 'job_receipt.json')[0])
    metadata = document(file_bytes(model / 'model_meta.json')[0])
    # The old terminal_status preparing snapshot is neither changed nor used as
    # current training authority; only the original completed receipt is used.
    require(receipt.get('status') == 'completed', 'Original completed receipt required')
    provenance = receipt.get('training_provenance') or {}
    require(receipt.get('job_id') == value['job_id'] and receipt.get('task') == value['task']
            and receipt.get('source_dataset_path') == str(source)
            and receipt.get('dataset_fingerprint') == provenance.get('dataset_fingerprint') == value['dataset_fingerprint']
            and receipt.get('checkpoint_sha256') == value['checkpoint_sha256'], 'Original receipt provenance')
    require(metadata.get('training_provenance') == provenance, 'Metadata provenance differs')
    cached = document(file_bytes(model / 'eval_results.json')[0])
    require(cached.get('evaluation_contract_version') == 2 and cached.get('job_id') == value['job_id']
            and cached.get('task') == 'segmentation', 'Original cached evaluation contract')
    require(cached.get('binding') == {'source_dataset_path': str(source), 'dataset_fingerprint': value['dataset_fingerprint'],
            'checkpoint_sha256': value['checkpoint_sha256'], 'labelset_id': 'default', 'training_labelset_id': 'default',
            'parent_job_id': None, 'threshold_settings': {}}, 'Exact original cached binding')
    require(cached.get('class_semantics') == {'version': 1, 'roles': {'background': 'normal', 'defect': 'defect'},
            'basis': {'background': 'segmentation_structure', 'defect': 'segmentation_structure'},
            'normal_classes': ['background'], 'defect_classes': ['defect'], 'unknown_classes': []}, 'Original cached class semantics')
    require(len(cached.get('test_predictions', [])) == 8 and bool(cached.get('confusion_matrix', {}).get('cell_samples')), 'Original cached validation cohort')
    require(all(Path(name).is_file() and Path(name).resolve().is_relative_to(source)
                for names in cached['confusion_matrix']['cell_samples'].values() for name in names), 'Cached original images unavailable')
    require(len(value['cohort']) == 2, 'Exact two original validation images')
    seen = set()
    for item in value['cohort']:
        require(item['relative_path'] not in seen, 'Duplicate input'); seen.add(item['relative_path'])
        require(item['split'] == 'original_validation' and item['width'] == item['height'] == 64, 'Original cohort scope')
        for relative_key, hash_key in [('relative_path', 'image_sha256'), ('mask_relative_path', 'mask_sha256')]:
            relative = Path(item[relative_key])
            require(not relative.is_absolute() and '..' not in relative.parts
                    and value['source_tree']['files'][relative.as_posix()]['sha256'] == item[hash_key], 'Input provenance')
    return value


def graph(job, edited=False):
    def node(identity, label, kind, x, y, **data):
        return {'id': identity, 'type': 'custom', 'position': {'x': x, 'y': y},
                'data': {'label': label, 'node_type': kind, **data}}
    nodes = [node('node_input', '검사 이미지', 'input', 40, 160)]
    for side, y in [('left', 40), ('right', 290)]:
        box = ([0, 0, 32, 64] if side == 'left' else [32, 0, 64, 64]) if edited else [0, 0, 64, 64]
        nodes += [node('roi_' + side, '보관 ROI ' + side, 'fixed_roi', 280, y, params={'roi_bbox': box}),
                  node('inspect_' + side, '보관 UNet ' + side, 'inspection', 520, y, task='segmentation',
                       model_job_id=job, threshold=0.5, crop_padding=0, params={'min_defect_area_px': 8})]
    nodes += [node('aggregate', '두 ROI 결과 집계', 'aggregate', 780, 160, rule='any_ng'),
              node('node_decision', '최종 판정', 'decision', 1020, 160, rule='aggregate_verdict'),
              node('node_output', '검사 결과', 'output', 1260, 160)]
    links = []
    for side in ('left', 'right'):
        links += [('input-' + side, 'node_input', 'roi_' + side, 'image'),
                  ('roi-' + side, 'roi_' + side, 'inspect_' + side, 'roi'),
                  ('result-' + side, 'inspect_' + side, 'aggregate', 'result')]
    links += [('aggregate-decision', 'aggregate', 'node_decision', 'result'),
              ('decision-output', 'node_decision', 'node_output', 'result')]
    return {'id': 'retained_unet_parallel_rois', 'name': '보관 UNet 두 ROI · 병렬 · 집계',
            'description': 'Original synthetic validation overlap; no quality or release approval',
            'nodes': nodes, 'edges': [{'id': identity, 'source': source, 'target': target, 'payload_type': payload}
                                    for identity, source, target, payload in links],
            'execution_config': {'max_workers': 2 if edited else 1, 'device_slots': 2 if edited else 1}}


def graph_contract(pipeline, job):
    expected = graph(job, True)
    require(pipeline.get('id') == expected['id'] and pipeline.get('name') == expected['name'], 'Graph identity')
    require(pipeline.get('execution_config') == expected['execution_config'], 'Original two-worker graph')
    require(len(pipeline['nodes']) == len(expected['nodes']), 'Graph node count')
    for actual, wanted in zip(pipeline['nodes'], expected['nodes']):
        require(actual['id'] == wanted['id'] and actual['position'] == wanted['position'], 'Original node/layout')
        for key, value in wanted['data'].items():
            require(actual['data'].get(key) == value, 'Graph model/ROI/rule changed: ' + key)
    require(len(pipeline['edges']) == len(expected['edges']), 'Graph edge count')
    for actual, wanted in zip(pipeline['edges'], expected['edges']):
        for key, value in wanted.items():
            require(actual.get(key) == value, 'Graph topology changed')
        require(actual.get('isBranch') is None and actual.get('predicate') is None, 'Unexpected branch/condition')


def prepare(value, workspace, project):
    workspace = Path(workspace).resolve(strict=True)
    for original_root in (Path(value['source_dataset_path']), Path(value['original_model_dir'])):
        require(not workspace.is_relative_to(original_root) and not original_root.is_relative_to(workspace), 'Workspace overlaps retained originals')
    project_dir, models = Path(project['project_dir']), Path(project['models_dir'])
    require(project_dir.resolve(strict=True) == project_dir and project_dir.is_relative_to(workspace)
            and project_dir != workspace and models == project_dir / 'models', 'Exact owned project models')
    require(project['task'] == value['task'] and project['source_dataset_dir'] == value['source_dataset_path'], 'Selected original source')
    require(not models.is_symlink(), 'Linked model directory')
    target = models / value['job_id']
    require(not target.exists() and not target.is_symlink(), 'Model target must be new')
    target.mkdir()
    copied = {}
    for name in value['copy_members']:
        raw, _ = file_bytes(Path(value['original_model_dir']) / name)
        with (target / name).open('xb') as destination:
            destination.write(raw)
        copied[name] = {'bytes': len(raw), 'sha256': sha(raw)}
    require(tree(target)['files'] == copied, 'Raw copied files differ')
    return {'project_dir': str(project_dir), 'model_dir': str(target), 'copied': copied,
            'pipeline': graph(value['job_id']), 'original_provenance_preserved': True,
            'app_model_verification_required': True, 'training_started': False}


def raster(value, dtype, shape):
    require(set(value) == {'dtype', 'encoding', 'shape', 'data'} and value['dtype'] == dtype
            and value['encoding'] == 'zlib_base64' and value['shape'] == shape, 'Raster codec/shape')
    raw = zlib.decompress(base64.b64decode(value['data'], validate=True))
    require(len(raw) == math.prod(shape) * (1 if dtype == 'uint8' else 4), 'Full raster length')
    if dtype == 'uint8':
        require(set(raw) <= {0, 1}, 'Mask is not binary')
    else:
        require(all(math.isfinite(item[0]) and 0 <= item[0] <= 1 for item in struct.iter_unpack('=f', raw)), 'Nonfinite probability')
    return raw


def result_contract(result, image, image_id, graph_sha, capacity):
    require(result['status'] == 'success' and result['final_verdict'] in ('OK', 'NG')
            and result['is_ok'] == (result['final_verdict'] == 'OK'), 'Complete genuine decision')
    require(result['image_path'] == image and result['image_id'] == image_id
            and result['graph_sha256'] == graph_sha and result.get('stop_node_id') is None, 'Full execution identity')
    require(result['inspected_image_size'] == [64, 64] and result['routed_output_node_id'] == 'node_output', 'Original source geometry')
    require(result['execution_resources'] == {'requested_workers': 2, 'requested_device_slots': 2,
            'effective_device_slots': capacity, 'device': 'cpu', 'engine_device_capacity': capacity}, 'Exact resource admission')
    steps = result['execution_steps']; require(len(steps) == 8 and len({row['node_id'] for row in steps}) == 8, 'Complete graph steps')
    by_node = {row['node_id']: row for row in steps}
    require(set(by_node) == {node['id'] for node in graph('placeholder')['nodes']}, 'Exact node set')
    require(all(row['status'] in ('passed', 'flagged_ng') for row in steps), 'No partial/untrained/skipped execution')
    for side in ('left', 'right'):
        require(by_node['inspect_' + side]['input_count'] == by_node['inspect_' + side]['output_count'] == 1, 'One original ROI per model')
    require(by_node['aggregate']['input_count'] == by_node['aggregate']['output_count'] == 2, 'Two-parent aggregate')
    require(len(result['crops']) == result['roi_count'] == 2, 'Two retained crop results')
    raster_hashes = []
    for crop, side, box in zip(result['crops'], ('left', 'right'), ([0, 0, 32, 64], [32, 0, 64, 64])):
        require(crop['source_node_id'] == 'inspect_' + side and crop['bbox'] == box, 'Crop source-node coordinates')
        require(crop['verdict'] == by_node['inspect_' + side]['branch_verdict'], 'Node result verdict')
        require([(row['class_id'], row['class_name']) for row in crop['segmentation_classes']]
                == [(0, 'background'), (1, 'defect')], 'Original class order')
        for row in crop['segmentation_classes']:
            require(row['bbox'] == box and row['source_transform'] == [[1, 0, box[0]], [0, 1, 0], [0, 0, 1]], 'Class source transform')
            mask = raster(row['mask'], 'uint8', [64, 32]); probability = raster(row['probability'], 'float32', [64, 32])
            require(row['area_px'] == sum(mask), 'Full class area count')
            raster_hashes.append({'node': crop['source_node_id'], 'class_id': row['class_id'],
                                  'mask_sha256': sha(mask), 'probability_sha256': sha(probability), 'pixels': 2048})
    verdict = 'NG' if any(row['verdict'] == 'NG' for row in result['crops']) else 'OK'
    require(by_node['aggregate']['branch_verdict'] == by_node['node_decision']['branch_verdict'] == result['final_verdict'] == verdict, 'any_ng aggregation')
    return raster_hashes


def package(value, workspace, project, saved, references, parallel_references):
    # Actual-only action. Source gates do not import these framework modules.
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
    import subprocess
    from backend.engine.flowchart_engine import FlowchartPipeline
    from backend.engine.flow_package import build_flow_package
    from backend.engine.flow_package_runtime import verify_flow_package, compare_flow_results
    from backend.engine.flow_provenance import pipeline_sha256
    workspace = Path(workspace).resolve(strict=True)
    project_dir = Path(project['project_dir']); models = Path(project['models_dir'])
    require(project_dir.resolve(strict=True) == project_dir and project_dir.is_relative_to(workspace)
            and project_dir != workspace and models == project_dir / 'models'
            and project['task'] == value['task'] and project['source_dataset_dir'] == value['source_dataset_path'], 'Exact owned original project')
    for original_root in (Path(value['source_dataset_path']), Path(value['original_model_dir'])):
        require(not workspace.is_relative_to(original_root) and not original_root.is_relative_to(workspace), 'Workspace overlaps retained originals')
    model = models / value['job_id']
    require(model.resolve(strict=True) == model and model.is_relative_to(workspace), 'Owned copied checkpoint')
    for name in value['copy_members']:
        require(sha(file_bytes(model / name)[0]) == value['original_model_tree']['files'][name]['sha256'], 'Copied original model changed')
    pipeline = FlowchartPipeline.model_validate(saved)
    graph_contract(pipeline.model_dump(), value['job_id'])
    graph_sha = pipeline_sha256(pipeline)
    require(len(references) == len(parallel_references) == 2, 'Exact reference cohort')
    destination = workspace / 'retained-unet-independent-package'
    require(not destination.exists() and not destination.is_symlink(), 'Package destination must be new')
    built = build_flow_package(pipeline=pipeline, checkpoints={value['job_id']: model / 'best_model.pt'},
                              output_base_dir=destination, package_name='retained_unet_two_rois',
                              runtime_config={'device': 'cpu', 'cpu_threads': 1, 'deadline_ms': None})
    package_dir = Path(built['package_path'])
    verified, checkpoints = verify_flow_package(package_dir)
    require(verified.model_dump() == pipeline.model_dump() and set(checkpoints) == {value['job_id']}
            and sha(file_bytes(checkpoints[value['job_id']])[0]) == value['checkpoint_sha256'], 'Verified package original graph/checkpoint')
    rows = []
    for index, (item, reference, parallel) in enumerate(zip(value['cohort'], references, parallel_references)):
        image = str(Path(value['source_dataset_path']) / item['relative_path'])
        image_id = reference['image_id']
        reference_rasters = result_contract(reference, image, image_id, graph_sha, 1)
        parallel_rasters = result_contract(parallel, image, image_id, graph_sha, 2)
        parallel_comparison = compare_flow_results(reference, parallel)
        require(parallel_comparison['mismatched_fields'] == ['execution_resources'], 'Parallel semantic/pixel result differs')
        require([row['mask_sha256'] for row in reference_rasters] == [row['mask_sha256'] for row in parallel_rasters], 'Parallel class masks differ')
        output = destination / ('standalone-' + str(index) + '.json')
        require(not output.exists(), 'Standalone output must be new')
        command = [sys.executable, str(package_dir / 'run_flow.py'), '--image', image, '--image-id', image_id,
                   '--device', 'cpu', '--cpu-threads', '1', '--output', str(output)]
        run = subprocess.run(command, cwd=package_dir,
                             env={**os.environ, 'PYTHONPATH': '', 'PYTHONDONTWRITEBYTECODE': '1',
                                  'HF_HUB_OFFLINE': '1', 'TRANSFORMERS_OFFLINE': '1'},
                             capture_output=True, text=True, timeout=45, check=False)
        require(run.returncode == 0 and output.is_file(), 'Original standalone runner failed: ' + run.stderr[-2000:])
        packaged = document(file_bytes(output)[0])
        packaged_rasters = result_contract(packaged, image, image_id, graph_sha, 1)
        comparison = compare_flow_results(reference, packaged)
        require(comparison['status'] == 'passed' and comparison['mismatched_fields'] == [], 'Original strict package parity: ' + str(comparison))
        # Masks are independently exact; finite probabilities use the original
        # comparator tolerance (1e-6), rather than compressed byte equality.
        require([row['mask_sha256'] for row in reference_rasters] == [row['mask_sha256'] for row in packaged_rasters], 'Every original class-mask pixel differs')
        from PIL import Image
        truth_file = Path(value['source_dataset_path']) / item['mask_relative_path']
        with Image.open(truth_file) as truth_image:
            require(truth_image.size == (64, 64), 'Original truth dimensions')
            truth_raw = truth_image.convert('L').tobytes()
        require(len(truth_raw) == 4096 and set(truth_raw) <= {0, 255}, 'Original generated mask values')
        truth = [int(pixel == 255) for pixel in truth_raw]
        predicted = [0] * 4096
        for crop in reference['crops']:
            row = crop['segmentation_classes'][1]; pixels = raster(row['mask'], 'uint8', [64, 32]); left = crop['bbox'][0]
            for y in range(64):
                predicted[y * 64 + left:y * 64 + left + 32] = pixels[y * 32:y * 32 + 32]
        counts = {key: 0 for key in ('tp', 'tn', 'fp', 'fn')}
        for actual, wanted in zip(predicted, truth):
            counts['tp' if actual and wanted else 'fp' if actual else 'fn' if wanted else 'tn'] += 1
        tp, fp, fn = counts['tp'], counts['fp'], counts['fn']
        metrics = {**counts, 'pixels': 4096, 'iou': tp / (tp + fp + fn) if tp + fp + fn else None,
                   'dice': 2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else None,
                   'scope': 'original generated validation mask; validation overlap; no human truth or model quality acceptance'}
        rows.append({'index': index, 'image_sha256': item['image_sha256'], 'image_id': image_id,
                     'command': command, 'returncode': run.returncode, 'stdout': run.stdout, 'stderr': run.stderr,
                     'output': str(output), 'reference': reference, 'packaged': packaged, 'comparison': comparison,
                     'reference_rasters': reference_rasters, 'packaged_rasters': packaged_rasters,
                     'parallel_rasters': parallel_rasters, 'parallel_comparison': parallel_comparison,
                     'original_synthetic_validation_pixels': metrics})
    require(tree(Path(value['source_dataset_path']), True) == value['source_tree']
            and tree(Path(value['original_model_dir']), True) == value['original_model_tree'], 'Retained originals changed during package')
    return {'package': built, 'package_tree': tree(package_dir), 'parity': rows, 'graph_sha256': graph_sha,
            'cohort_scope': 'two original generated validation images; validation overlap; no human truth',
            'whole_flow_approved': False, 'release_approved': False, 'model_quality_approved': False,
            'training_started': False, 'compiled_backend_covered': False}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['check', 'prepare', 'package'])
    parser.add_argument('--binding', required=True); parser.add_argument('--binding-sha256', required=True)
    parser.add_argument('--workspace'); parser.add_argument('--project'); parser.add_argument('--pipeline'); parser.add_argument('--references'); parser.add_argument('--parallel-references')
    args = parser.parse_args()
    value = binding(args.binding, args.binding_sha256)
    if args.action == 'check':
        result = {'binding_sha256': args.binding_sha256, 'original_source_tree': value['source_tree'],
                  'original_model_tree': value['original_model_tree'], 'cohort': value['cohort'], 'actual_runtime': False}
    else:
        require(args.workspace and args.project, 'Owned workspace/project required')
        project = document(args.project)
        if args.action == 'prepare':
            result = prepare(value, args.workspace, project)
        else:
            require(args.pipeline and args.references and args.parallel_references, 'Exact saved graph/reference files required')
            result = package(value, args.workspace, project, document(file_bytes(args.pipeline)[0]),
                             document(file_bytes(args.references)[0]), document(file_bytes(args.parallel_references)[0]))
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))


if __name__ == '__main__':
    main()
