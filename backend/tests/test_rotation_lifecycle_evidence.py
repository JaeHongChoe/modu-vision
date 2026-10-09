"""Rotation durable-record adapter controls, never image/model execution."""
import copy
import json

import pytest

from backend.tests import test_model_lifecycle_evidence as controls

canonical = controls.canonical
sha = controls.sha


def _bind_rotation(case, binding):
    case._replace_family_binding(binding, 'rotation')
    case.change('model/model_meta.json', lambda value: value.update(dataset_path=binding['family_dataset_path']))


@pytest.fixture
def rotation_control():
    case = controls.LifecycleEvidenceTests()
    case.setUp()
    try:
        case._prepare_family('ocr')
        old = json.loads((case.root / 'model/job_receipt.json').read_bytes())['training_provenance']
        binding = copy.deepcopy(old)
        dataset = '/original/project/dataset/rotation'
        version = binding['version_dir']
        samples = []
        for index, name in enumerate(('a.bin', 'b.bin')):
            raw = (case.root / ('source/' + name)).read_bytes()
            samples.append({'image': name, 'correction_deg': -180.0 if index == 0 else -90.0,
                            'split': 'train' if index == 0 else 'val', 'source_sha256': sha(raw)})
        body = {'version': 1, 'samples': samples, 'source_dataset_path': '/original/source'}
        raw = (json.dumps(body, ensure_ascii=False, indent=2) + '\n').encode()
        snapshot = version + '/labels/family/rotation/rotation.json'
        rows = sorted([{'relative_path': 'rotation.json', 'source_path': dataset + '/rotation.json',
                        'sha256': sha(raw), 'snapshot_path': snapshot},
                       *({'relative_path': row['image'], 'source_path': dataset + '/' + row['image'],
                          'sha256': row['source_sha256'], 'snapshot_path': None} for row in samples)],
                      key=lambda row: row['source_path'])
        for path in case.receipt['train']['family_files'].values():
            del case.files[path]
        copies = {row['source_path']: 'rotation/current/' + row['relative_path'] for row in rows}
        copies[snapshot] = 'rotation/frozen/rotation.json'
        case.put(copies[dataset + '/rotation.json'], raw)
        case.put(copies[snapshot], raw)
        for row in samples:
            case.put(copies[dataset + '/' + row['image']], (case.root / ('source/' + row['image'])).read_bytes())
        binding.update(family_task='rotation', family_dataset_path=dataset, family_inputs=rows,
            family_inputs_sha256=sha(json.dumps(rows, sort_keys=True, separators=(',', ':')).encode()),
            family_provenance={'dataset_sha256': sha(json.dumps(samples, sort_keys=True, separators=(',', ':')).encode()),
                'source_sha256': {row['image']: row['source_sha256'] for row in samples},
                'split_counts': {'train': 1, 'val': 1, 'test': 0},
                'angle_semantics': 'counterclockwise_upright_correction_degrees_360',
                'source_dataset_path': '/original/source'})
        binding.pop('family_dataset_sha256', None)
        case.receipt['family'] = 'rotation'
        case.receipt['train']['family_files'] = copies
        _bind_rotation(case, binding)
        graph = json.loads((case.root / 'saved/pipeline.json').read_bytes())
        graph['nodes'][0]['data'] = {'node_type': 'preprocess', 'params': {'operation': 'learned_rotation'},
                                   'model_job_id': 'job_original'}
        case.put('saved/pipeline.json', graph)
        case.put('package/pipeline.json', canonical(graph))
        package = json.loads((case.root / 'package/manifest.json').read_bytes())
        package['models'][0]['task'] = 'rotation'
        for row in package['files']:
            row.update(case.files['package/' + row['path']])
        case.put('package/manifest.json', package)
        case.change('package/parity_receipt.json', lambda value: value.update(
            manifest_sha256=case.files['package/manifest.json']['sha256'],
            graph_sha256=case.files['package/pipeline.json']['sha256']))
        yield case
    finally:
        case.doCleanups()


def test_rotation_record_joins_original_angle_split_source_and_frozen_bytes(rotation_control):
    result = rotation_control.check()
    assert result['record_chain_verified'], result
    for key in ('runtime_execution_reproduced', 'human_truth_approved', 'model_quality_approved',
                'target_execution_approved', 'parent_accepted'):
        assert result[key] is False


@pytest.mark.parametrize('corruption', ['angle', 'split', 'source', 'uuid', 'frozen', 'copied', 'extra_copy'])
def test_rotation_stale_or_foreign_record_cannot_qualify(rotation_control, corruption):
    case = rotation_control
    assert case.check()['record_chain_verified'], case.check()
    binding = json.loads((case.root / 'model/job_receipt.json').read_bytes())['training_provenance']
    if corruption in ('angle', 'split', 'source'):
        def change(value):
            row = value['samples'][0]
            row.update({'angle': {'correction_deg': 181}, 'split': {'split': 'test'},
                        'source': {'source_sha256': '0' * 64}}[corruption])
        case.change('rotation/current/rotation.json', change)
    elif corruption == 'uuid':
        binding['family_source_image_uuids'] = ['foreign-image-uuid']
        _bind_rotation(case, binding)
    elif corruption in ('frozen', 'copied'):
        path = 'rotation/frozen/rotation.json' if corruption == 'frozen' else 'rotation/current/a.bin'
        case.put(path, (case.root / path).read_bytes() + b'changed')
    else:
        case.receipt['train']['family_files']['/foreign/extra'] = 'source/a.bin'
    result = case.check()
    assert not result['record_chain_verified'], result
    assert result['stages']['train']['state'] != 'verified', result
    if corruption == 'uuid':
        assert result['stages']['train']['reason'] == 'family source UUID inventory differs', result


def test_rotation_correct_raw_pins_cannot_hide_changed_producer_angle_digest(rotation_control):
    case = rotation_control
    assert case.check()['record_chain_verified'], case.check()
    for path in ('rotation/current/rotation.json', 'rotation/frozen/rotation.json'):
        case.change(path, lambda value: value['samples'][0].update(correction_deg=90))
    binding = json.loads((case.root / 'model/job_receipt.json').read_bytes())['training_provenance']
    row = next(row for row in binding['family_inputs'] if row['relative_path'] == 'rotation.json')
    row['sha256'] = case.files['rotation/current/rotation.json']['sha256']
    binding['family_inputs_sha256'] = sha(json.dumps(binding['family_inputs'], sort_keys=True, separators=(',', ':')).encode())
    _bind_rotation(case, binding)
    result = case.check()
    assert not result['record_chain_verified'] and result['stages']['train']['state'] != 'verified', result
    assert result['stages']['train']['reason'] == 'family provenance differs from original manifest and member bytes', result
