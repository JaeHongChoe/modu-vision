"""Synthetic same-source mixed-model fixture. Actual actions require Root's gate.

Source controls use only the pure PNG/graph/result predicates. `seed`, `split`,
`models` and `package` are never invoked by source preparation. No training or
checkpoint metadata is fabricated; two original application jobs must complete.
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


def require(value, message):
    if not value:
        raise ValueError(message)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def document(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, 'Duplicate JSON key')
            result[key] = value
        return result
    return json.loads(raw, object_pairs_hook=pairs,
                      parse_constant=lambda _: require(False, 'Nonfinite JSON'))


def file_bytes(path):
    path = Path(path)
    require(not path.is_symlink(), 'Linked file')
    with os.fdopen(os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0)), 'rb') as stream:
        before = os.fstat(stream.fileno())
        require(stat.S_ISREG(before.st_mode), 'Not a regular file')
        raw = stream.read()
        after = os.fstat(stream.fileno())
        key = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
        require(key(before) == key(after) == key(path.stat()), 'Changed file')
    return raw, after


def tree(root):
    root = Path(root)
    require(root.is_dir() and not root.is_symlink(), 'Missing or linked root')
    files, directories = {}, []
    for folder, names, members in os.walk(root, followlinks=False):
        names.sort(); members.sort()
        for name in names:
            item = Path(folder) / name
            require(not item.is_symlink(), 'Linked directory')
            directories.append(item.relative_to(root).as_posix())
        for name in members:
            item = Path(folder) / name
            raw, info = file_bytes(item)
            files[item.relative_to(root).as_posix()] = {
                'bytes': len(raw), 'sha256': sha(raw),
                'mtime_ns': str(info.st_mtime_ns), 'ctime_ns': str(info.st_ctime_ns)}
    return {'directories': sorted(directories), 'files': dict(sorted(files.items()))}


def binding(file, digest):
    raw, _ = file_bytes(file)
    require(sha(raw) == digest, 'Binding bytes')
    value = document(raw)
    require(value.get('schema') == 'modu-vision.shared-source-seg-patch/v1', 'Binding schema')
    require(value.get('base_architecture') == 'vit_small_patch16_dinov3.lvd1689m', 'Original DINO architecture')
    weight = Path(value['base_weights'])
    require(weight.is_absolute() and weight.resolve(strict=True) == weight, 'Canonical original weight')
    raw, info = file_bytes(weight)
    require(sha(raw) == value['base_sha256'] and info.st_size == value['base_bytes'], 'Original full weight bytes')
    for row in value['original_receipts']:
        source = Path(row['path'])
        raw, _ = file_bytes(source)
        require(sha(raw) == row['sha256'], 'Original receipt changed')
        original = document(raw)
        require(original.get('task') == row['task'], 'Original receipt purpose')
        require(original.get('pretrained_sha256') == value['base_sha256'], 'Original authenticated base receipt')
    require(value.get('epochs') == 2 and value.get('max_runtime_s') == 120,
            'Only two bounded CPU fits')
    return value


def png(index):
    require(type(index) is int and 0 <= index < 6, 'Exact six source images')
    pixels = bytearray()
    for y in range(64):
        pixels.append(0)
        for x in range(64):
            defect = 8 + index <= x < 56 - index and 8 <= y < 40
            shade = 30 + index if defect else 100 + ((x * 7 + y * 11 + index * 13) % 45)
            pixels.extend((shade, shade + (0 if defect else 3), shade + (0 if defect else 7)))
    def chunk(kind, payload):
        return struct.pack('>I', len(payload)) + kind + payload + struct.pack('>I', zlib.crc32(kind + payload) & 0xffffffff)
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', 64, 64, 8, 2, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(bytes(pixels), 9)) + chunk(b'IEND', b''))


def label(index, filename):
    return {'version': '5.0', 'imagePath': filename, 'imageWidth': 64, 'imageHeight': 64,
            'imageData': None, 'flags': {}, 'shapes': [{'label': 'chip', 'shape_type': 'rectangle',
                'points': [[8 + index, 8], [56 - index, 40]], 'flags': {}}]}


def seed(workspace, value):
    root = Path(workspace)
    require(root.resolve(strict=True) == root and not root.is_symlink(), 'Canonical owned workspace')
    source = root / 'shared-seg-patch-source'
    require(not source.exists(), 'Fresh source only')
    source.mkdir()
    images = []
    for index in range(6):
        split = ('train', 'val', 'test')[index // 2]
        image = source / split / ('sample-' + str(index) + '.png')
        image.parent.mkdir(exist_ok=True)
        with image.open('xb') as stream: stream.write(png(index))
        annotation = image.with_suffix('.json')
        with annotation.open('x', encoding='utf-8') as stream:
            json.dump(label(index, image.name), stream, separators=(',', ':'), ensure_ascii=False)
        images.append({'relative_path': image.relative_to(source).as_posix(), 'path': str(image), 'split': split,
                       'sha256': sha(file_bytes(image)[0]), 'annotation_sha256': sha(file_bytes(annotation)[0])})
    # A byte-exact owned copy retains the safetensors extension expected by timm.
    # The original regular blob and both original metadata receipts stay untouched.
    weights = root / 'shared-original-dinov3.safetensors'
    with weights.open('xb') as stream: stream.write(file_bytes(value['base_weights'])[0])
    require(sha(file_bytes(weights)[0]) == value['base_sha256'], 'Owned base copy bytes')
    return {'source': str(source), 'source_tree': tree(source), 'images': images, 'weights': str(weights),
            'weight_tree': {'sha256': value['base_sha256'], 'bytes': value['base_bytes']},
            'cohort': images[-2:], 'synthetic_only': True, 'human_truth': False}


def split(workspace, project, fixture):
    root, project_dir = Path(workspace), Path(project['project_dir'])
    require(project_dir.resolve(strict=True).is_relative_to(root.resolve(strict=True))
            and project['task'] == 'segmentation' and project['source_dataset_dir'] == fixture['source'], 'Owned project/source')
    target = Path(project['dataset_dir']) / 'splits' / (sha(fixture['source'].encode()) + '.json')
    require(not target.exists() and not target.is_symlink(), 'Fresh exact split fixture')
    target.parent.mkdir(parents=True, exist_ok=True)
    assignments = {row['relative_path']: row['split'] for row in fixture['images']}
    payload = {'folder_path': fixture['source'], 'assignments': assignments, 'seed': 112}
    with target.open('x', encoding='utf-8') as stream: json.dump(payload, stream, separators=(',', ':'))
    require(tree(Path(fixture['source'])) == fixture['source_tree'], 'Original source changed during split setup')
    return {'path': str(target), 'sha256': sha(file_bytes(target)[0]), 'payload': payload,
            'controlled_fixture_write_before_version': True}


def graph(jobs, edited=False):
    require(set(jobs) == {'segmentation', 'patch_classification'} and len(set(jobs.values())) == 2
            and all(re.fullmatch(r'job_[0-9]+_[a-z0-9]+', job) for job in jobs.values()), 'Two real distinct jobs')
    def node(identity, kind, x, y, **data):
        return {'id': identity, 'type': 'custom', 'position': {'x': x, 'y': y},
                'data': {'label': identity, 'node_type': kind, **data}}
    nodes = [node('node_input', 'input', 40, 160)]
    for side, task, y in [('seg', 'segmentation', 30), ('patch', 'patch_classification', 290)]:
        box = ([0, 0, 32, 64] if side == 'seg' else [32, 0, 64, 64]) if edited else [0, 0, 64, 64]
        nodes.extend([node('roi_' + side, 'fixed_roi', 280, y, params={'roi_bbox': box}),
                      node('inspect_' + side, 'inspection', 520, y, task=task,
                           model_job_id=jobs[task], threshold=0.5, crop_padding=0,
                           params={'min_defect_area_px': 1} if side == 'seg' else {})])
    nodes.extend([node('aggregate', 'aggregate', 780, 160, rule='any_ng'),
                  node('node_decision', 'decision', 1020, 160, rule='aggregate_verdict'),
                  node('node_output', 'output', 1260, 160)])
    links = []
    for side in ('seg', 'patch'):
        links.extend([('input-' + side, 'node_input', 'roi_' + side, 'image'),
                      ('roi-' + side, 'roi_' + side, 'inspect_' + side, 'roi'),
                      ('result-' + side, 'inspect_' + side, 'aggregate', 'result')])
    links.extend([('aggregate-decision', 'aggregate', 'node_decision', 'result'),
                  ('decision-output', 'node_decision', 'node_output', 'result')])
    return {'id': 'shared_source_seg_patch', 'name': '같은 원본 SEG + Patch · 두 ROI · 집계',
            'description': 'Tiny synthetic CPU control; no manufacturing quality or operational approval',
            'nodes': nodes, 'edges': [{'id': i, 'source': s, 'target': t, 'payload_type': p} for i, s, t, p in links],
            'execution_config': {'max_workers': 2 if edited else 1, 'device_slots': 2 if edited else 1}}


def model_contract(project, fixture, version, jobs, training=None):
    require(set(jobs) == {'segmentation', 'patch_classification'}, 'Exact purpose pair')
    records = {}
    accepted = {}
    if training is not None:
        require(type(training) is list and len(training) == 2 and {row['task'] for row in training} == set(jobs), 'Exact two actual submissions')
        accepted = {row['task']: row for row in training}
    for task, identity in jobs.items():
        require(re.fullmatch(r'job_[0-9]+_[a-z0-9]+', identity) is not None, 'Real job identifier')
        root = Path(project['models_dir']) / identity
        receipt = document(file_bytes(root / 'job_receipt.json')[0])
        meta = document(file_bytes(root / 'model_meta.json')[0])
        local = document(file_bytes(root / 'local_job.json')[0])
        spec = document(file_bytes(root / 'local_spec.json')[0])
        require(receipt['job_id'] == identity and receipt['status'] == 'completed' and receipt['task'] == task
                and receipt['source_dataset_path'] == fixture['source'], 'Original completed same-source job')
        require(receipt['dataset_fingerprint'] == version['dataset_fingerprint']
                and receipt['training_provenance']['dataset_fingerprint'] == version['dataset_fingerprint']
                and receipt['training_provenance']['dataset_version_id'] == version['id'], 'Same original version/fingerprint')
        require(receipt['total_epochs'] == receipt['current_epoch'] == 2
                and meta['device'] == spec['device'] == 'cpu' and spec['task'] == task
                and spec['config_overrides']['epochs'] == 2, 'Exactly two original CPU epochs')
        require(local['job_id'] == identity and local['worker_exit_confirmed'] is True
                and type(local['worker_exit_code']) is int and local['worker_exit_code'] == 0, 'Original training worker direct exit')
        require(meta['task'] == task and meta['pretrained_sha256'] == fixture['weight_tree']['sha256']
                and meta['pretrained'] is True and meta['encoder_frozen'] is True
                and meta['train_mode'] == 'head_only', 'Actual typed authentic frozen encoder model')
        require(meta['classes'] == (['background', 'chip'] if task == 'segmentation' else ['OK', 'chip']), 'Purpose class mapping')
        require(meta['encoder_architecture'] == 'vit_small_patch16_dinov3.lvd1689m', 'Authentic encoder')
        if task == 'patch_classification':
            require(receipt['training_provenance']['family_task'] == task
                    and receipt['training_provenance']['family_provenance']['source_dataset_path'] == fixture['source'], 'Original family source binding')
        if training is not None:
            row = accepted[task]
            require(submission_contract(task, row['posted'], row['accepted'], fixture['source'], version) == identity
                    and row['terminal']['job_id'] == identity and row['terminal']['task'] == task
                    and row['terminal']['status'] == 'completed'
                    and row['accepted']['training_provenance'] == receipt['training_provenance'], 'Original submission/terminal/receipt join')
        records[task] = {'receipt': receipt, 'metadata': meta, 'local_job': local, 'local_spec': spec,
                         'tree': tree(root), 'checkpoint': str(root / 'best_model.pt')}
    require(jobs['segmentation'] != jobs['patch_classification'], 'Distinct original fits')
    return records



def submission_contract(task, posted, response, source, version):
    """Route-specific original response; purpose is joined to terminal records."""
    require(type(response) is dict and re.fullmatch(r'job_[0-9]+_[a-z0-9]+', response.get('job_id', '')) is not None,
            'Original submission identity')
    provenance = response['training_provenance']
    require(type(provenance) is dict and provenance['dataset_version_id'] == version['id']
            and provenance['dataset_fingerprint'] == version['dataset_fingerprint'], 'Original submitted version')
    if task == 'patch_classification':
        require(set(response) == {'job_id', 'status', 'dataset_path', 'source_dataset_path', 'training_provenance'},
                'Original Patch submission shape')
        require(response['status'] in ('running', 'queued', 'completed')
                and response['dataset_path'] == posted['dataset_path'] and response['source_dataset_path'] == source,
                'Original Patch submitted dataset/source')
        require(provenance['family_task'] == task
                and provenance['family_provenance']['source_dataset_path'] == source, 'Original Patch family binding')
    else:
        require(task == 'segmentation' and response['task'] == task
                and response['status'] in ('started', 'queued'), 'Original SEG submission purpose')
    return response['job_id']


def transport_contract(request, semantic_delta, unchanged_delta):
    """Full captured write multiset only; incidental GET inventory is unclaimed."""
    from urllib.parse import urlsplit, parse_qsl
    from collections import Counter
    expected = request['expected']
    require(type(expected) is list and len(expected) == 27, 'Exact intended write inventory')
    require(Counter(row['path'] for row in expected) == {
        '/api/flowchart/models/verify': 6, '/api/flowchart/pipeline/diff': 2,
        '/api/flowchart/execution-resources': 2, '/api/flowchart/run': 4,
        '/api/flowchart/draft': 5, '/api/flowchart/pipeline': 1,
        '/api/dataset/library/resolve': 3, '/api/patch-classification/predict': 4}, 'Original handler counts')
    require(request['initial_version_id'] != request['saved_version_id']
            and type(request['save_started']) is int, 'Two distinct original save bases and request clock')
    previews = {
        request['initial_version_id']: {'parent_revision': request['initial_version_id'], 'stale': False,
            'semantic_delta': semantic_delta, 'layout_only': bool(semantic_delta['layout_only'])},
        request['saved_version_id']: {'parent_revision': request['saved_version_id'], 'stale': False,
            'semantic_delta': unchanged_delta, 'layout_only': bool(unchanged_delta['layout_only'])}}
    expected = [{**row, 'response': previews[row['query']['expected_version_id']]}
                if row['path'] == '/api/flowchart/pipeline/diff' else row for row in expected]
    actual = []
    for row in sorted(request['writes'], key=lambda value: value['started']):
        url = urlsplit(row['url']); query = parse_qsl(url.query, keep_blank_values=True)
        require(url.scheme + '://' + url.netloc == request['origin'] and not url.fragment
                and len(query) == len(dict(query)), 'Original transport origin/query')
        require(row['status'] == 200 and row['method'] in ('POST', 'PUT'), 'Original write method/status')
        raw = base64.b64decode(row['raw_base64'], validate=True)
        require(type(row['bytes']) is int and len(raw) == row['bytes'] and sha(raw) == row['sha256']
                and document(raw) == row['response'], 'Original full eager write reply bytes')
        require(all(type(row[key]) is int for key in ('started', 'deadline', 'finished'))
                and row['started'] <= row['finished'] <= row['deadline']
                and row['deadline'] - row['started'] == (60000 if url.path in
                    ('/api/flowchart/run', '/api/patch-classification/predict') else 10000), 'Original write deadline')
        if url.path == '/api/flowchart/pipeline/diff':
            original_base = request['initial_version_id'] if row['started'] < request['save_started'] else request['saved_version_id']
            require(dict(query) == {'expected_version_id': original_base}, 'Original pre/post save confirmation base')
        actual.append({'path': url.path, 'method': row['method'], 'query': dict(query),
                       'body': row['body'], 'response': row['response']})
    require(len(actual) == len(expected), 'Missing or extra original write')
    # Compare in original per-endpoint request-start order, not completion order.
    for endpoint in {row['path'] for row in expected}:
        require([row for row in actual if row['path'] == endpoint]
                == [row for row in expected if row['path'] == endpoint], 'Original full write tuple/reply differs: ' + endpoint)
    return {'write_count': len(actual), 'counts': dict(Counter(row['path'] for row in actual)),
            'full_tuple_and_reply': True, 'incidental_get_inventory_enumerated': False}


def raster(value, dtype, shape):
    require(set(value) == {'dtype', 'encoding', 'shape', 'data'} and value.get('dtype') == dtype
            and value.get('shape') == shape and value.get('encoding') == 'zlib_base64', 'Full raster schema')
    raw = zlib.decompress(base64.b64decode(value['data'], validate=True))
    require(len(raw) == math.prod(shape) * (4 if dtype == 'float32' else 1), 'Full raster length')
    if dtype == 'uint8': require(set(raw) <= {0, 1}, 'Binary class mask')
    else: require(all(math.isfinite(row[0]) and 0 <= row[0] <= 1 for row in struct.iter_unpack('=f', raw)), 'Finite class probabilities')
    return raw


def result_contract(result, image, image_id, graph_sha, capacity):
    require(result['status'] == 'success' and result['final_verdict'] in ('OK', 'NG')
            and result['is_ok'] == (result['final_verdict'] == 'OK'), 'Complete real result')
    require(result['image_path'] == image and result['image_id'] == image_id and result['graph_sha256'] == graph_sha
            and result.get('stop_node_id') is None and result['inspected_image_size'] == [64, 64]
            and result['routed_output_node_id'] == 'node_output', 'Exact whole-flow identity')
    require(result['execution_resources'] == {'requested_workers': 2, 'requested_device_slots': 2,
        'effective_device_slots': capacity, 'device': 'cpu', 'engine_device_capacity': capacity}, 'Original admitted resources')
    steps = {row['node_id']: row for row in result['execution_steps']}
    expected = {'node_input', 'roi_seg', 'inspect_seg', 'roi_patch', 'inspect_patch', 'aggregate', 'node_decision', 'node_output'}
    require(len(result['execution_steps']) == 8 and set(steps) == expected
            and all(row['status'] in ('passed', 'flagged_ng') for row in steps.values()), 'No mocked/untrained/partial/skipped branch')
    require((steps['inspect_seg']['input_count'], steps['inspect_seg']['output_count']) == (1, 1)
            and (steps['inspect_patch']['input_count'], steps['inspect_patch']['output_count']) == (1, 2), 'Original ROI/model crop counts')
    crops = result['crops']
    require(len(crops) == result['roi_count'] == 3, 'One SEG and two native patches')
    seg = [row for row in crops if row['source_node_id'] == 'inspect_seg']
    patches = [row for row in crops if row['source_node_id'] == 'inspect_patch']
    require(len(seg) == 1 and len(patches) == 2 and seg[0]['bbox'] == [0, 0, 32, 64]
            and [row['bbox'] for row in patches] == [[32, 0, 64, 32], [32, 32, 64, 64]], 'Original source coordinates')
    hashes = []
    require([(row['class_id'], row['class_name']) for row in seg[0]['segmentation_classes']] == [(0, 'background'), (1, 'chip')], 'SEG complete classes')
    for row in seg[0]['segmentation_classes']:
        require(row['bbox'] == [0, 0, 32, 64] and row['source_transform'] == [[1, 0, 0], [0, 1, 0], [0, 0, 1]], 'SEG source transform')
        mask = raster(row['mask'], 'uint8', [64, 32]); probability = raster(row['probability'], 'float32', [64, 32])
        require(row['area_px'] == sum(mask), 'All class pixels counted')
        hashes.append({'class_id': row['class_id'], 'mask_sha256': sha(mask), 'probability_sha256': sha(probability)})
    for row in patches:
        require(math.isfinite(row['defect_score']) and 0 <= row['defect_score'] <= 1
                and row['label'] in ('OK', 'chip') and row['segmentation_classes'] == [], 'Actual Patch score purpose')
        require(row['verdict'] == ('NG' if row['defect_score'] >= 0.5 else 'OK'), 'Patch threshold equality')
    verdict = 'NG' if any(row['verdict'] == 'NG' for row in crops) else 'OK'
    require(steps['aggregate']['input_count'] == steps['aggregate']['output_count'] == 3
            and steps['aggregate']['branch_verdict'] == steps['node_decision']['branch_verdict'] == result['final_verdict'] == verdict,
            'Original any_ng aggregation')
    return hashes


def package(request):
    # Root actual only: these imports load real models and start owned runners.
    import subprocess
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
    from backend.engine.flowchart_engine import FlowchartPipeline
    from backend.engine.flow_package import build_flow_package
    from backend.engine.flow_package_runtime import verify_flow_package, compare_flow_results
    from backend.engine.flow_provenance import pipeline_sha256
    project, fixture, models = request['project'], request['fixture'], request['models']
    require(tree(Path(fixture['source'])) == fixture['source_tree'], 'Source before package')
    for task, record in models.items(): require(tree(Path(record['checkpoint']).parent) == record['tree'], 'Original new checkpoint changed')
    pipeline = FlowchartPipeline.model_validate(request['pipeline'])
    graph_sha = pipeline_sha256(pipeline)
    wanted = graph(request['jobs'], True)
    require(pipeline.execution_config.model_dump() == wanted['execution_config'], 'Original graph worker request')
    for actual, expected in zip(pipeline.nodes, wanted['nodes']):
        require(actual.id == expected['id'] and actual.data.node_type == expected['data']['node_type'], 'Saved graph node identity')
        for key in ('task', 'model_job_id', 'threshold', 'crop_padding', 'rule', 'params'):
            if key in expected['data']: require(getattr(actual.data, key) == expected['data'][key], 'Saved node field ' + key)
    require(len(pipeline.nodes) == 8 and [(e.id, e.source, e.target, e.payload_type) for e in pipeline.edges]
            == [(e['id'], e['source'], e['target'], e['payload_type']) for e in wanted['edges']], 'Complete original graph')
    output_root = Path(request['workspace']) / 'shared-seg-patch-package'
    require(not output_root.exists() and not output_root.is_symlink(), 'Fresh owned package')
    built = build_flow_package(pipeline=pipeline, checkpoints={request['jobs'][task]: Path(row['checkpoint']) for task, row in models.items()},
        output_base_dir=output_root, package_name='shared_seg_patch_two_rois',
        runtime_config={'device': 'cpu', 'cpu_threads': 1, 'deadline_ms': None})
    root = Path(built['package_path'])
    verified, checkpoints = verify_flow_package(root)
    require(verified.model_dump() == pipeline.model_dump() and set(checkpoints) == set(request['jobs'].values()), 'Exact package model pair')
    for task, row in models.items():
        require(sha(file_bytes(checkpoints[request['jobs'][task]])[0]) == row['tree']['files']['best_model.pt']['sha256'], 'Both full packaged checkpoint bytes')
    rows = []
    for index, (serial, parallel) in enumerate(zip(request['serial'], request['parallel'])):
        require(serial['image_path'] == parallel['image_path'] and serial['image_id'] == parallel['image_id'], 'Same GUI input identity')
        serial_masks = result_contract(serial, serial['image_path'], serial['image_id'], graph_sha, 1)
        parallel_masks = result_contract(parallel, parallel['image_path'], parallel['image_id'], graph_sha, 2)
        comparison = compare_flow_results(serial, parallel)
        require(comparison['mismatched_fields'] == ['execution_resources'], 'Same genuine serial/parallel semantics')
        require([r['mask_sha256'] for r in serial_masks] == [r['mask_sha256'] for r in parallel_masks], 'Same full SEG class masks')
        output = output_root / ('standalone-' + str(index) + '.json')
        require(not output.exists(), 'Fresh original runner output')
        command = [sys.executable, str(root / 'run_flow.py'), '--image', serial['image_path'], '--image-id', serial['image_id'],
                   '--device', 'cpu', '--cpu-threads', '1', '--output', str(output)]
        run = subprocess.run(command, cwd=root, env={**os.environ, 'PYTHONPATH': '', 'PYTHONDONTWRITEBYTECODE': '1',
            'HF_HUB_OFFLINE': '1', 'TRANSFORMERS_OFFLINE': '1'}, capture_output=True, text=True, timeout=45, check=False)
        require(run.returncode == 0, 'Standalone original runner: ' + run.stderr[-2000:])
        result = document(file_bytes(output)[0])
        packaged_masks = result_contract(result, serial['image_path'], serial['image_id'], graph_sha, 1)
        parity = compare_flow_results(serial, result)
        require(parity['status'] == 'passed' and parity['mismatched_fields'] == [], 'Original full package comparator')
        require([r['mask_sha256'] for r in serial_masks] == [r['mask_sha256'] for r in packaged_masks], 'All original SEG pixels in package')
        rows.append({'command': command, 'returncode': run.returncode, 'stdout': run.stdout, 'stderr': run.stderr,
            'output': str(output), 'result': result, 'comparison': parity, 'parallel_comparison': comparison,
            'serial_rasters': serial_masks, 'parallel_rasters': parallel_masks, 'packaged_rasters': packaged_masks})
    require(len(rows) == len(request['serial']) == len(request['parallel']) == 2, 'Exact two synthetic test images')
    require(tree(Path(fixture['source'])) == fixture['source_tree'], 'Source after package')
    for task, record in models.items(): require(tree(Path(record['checkpoint']).parent) == record['tree'], 'Models after package')
    return {'package': built, 'package_tree': tree(root), 'parity': rows, 'graph_sha256': graph_sha,
            'new_cpu_fits': 2, 'synthetic_only': True, 'human_truth': False, 'quality_approval': False}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['seed', 'split', 'graph', 'models', 'package', 'transport'])
    parser.add_argument('--request', required=True)
    args = parser.parse_args()
    request = document(file_bytes(args.request)[0])
    if args.action == 'seed':
        result = seed(request['workspace'], binding(request['binding'], request['binding_sha256']))
    elif args.action == 'split': result = split(request['workspace'], request['project'], request['fixture'])
    elif args.action == 'graph': result = graph(request['jobs'], request.get('edited', False))
    elif args.action == 'models': result = model_contract(request['project'], request['fixture'], request['version'], request['jobs'], request.get('training'))
    elif args.action == 'transport':
        sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
        from backend.engine.flow_provenance import semantic_delta
        result = transport_contract(request, semantic_delta(request['initial_pipeline'], request['pipeline']),
                                    semantic_delta(request['pipeline'], request['pipeline']))
    else: result = package(request)
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))


if __name__ == '__main__': main()
