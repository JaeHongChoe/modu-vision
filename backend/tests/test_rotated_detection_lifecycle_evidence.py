"""Copied durable OBB record controls; never decode an image or load a model."""
import copy
import hashlib
import json

import pytest

from backend.tests import test_model_lifecycle_evidence as controls

sha = controls.sha
canonical = controls.canonical


def _write_binding(case, binding):
    case._replace_family_binding(binding, 'rotated_detection')
    case.change('model/model_meta.json', lambda value: value.update(dataset_path=binding['family_dataset_path']))


def _rewrite_manifest(case, mutate):
    body = json.loads((case.root / 'obb/current/rotated_boxes.json').read_bytes())
    mutate(body)
    raw = (json.dumps(body, ensure_ascii=False, indent=2) + '\n').encode()
    case.put('obb/current/rotated_boxes.json', raw)
    case.put('obb/frozen/rotated_boxes.json', raw)
    binding = json.loads((case.root / 'model/job_receipt.json').read_bytes())['training_provenance']
    next(row for row in binding['family_inputs'] if row['relative_path'] == 'rotated_boxes.json')['sha256'] = sha(raw)
    binding['family_inputs_sha256'] = sha(json.dumps(binding['family_inputs'], sort_keys=True, separators=(',', ':')).encode())
    _write_binding(case, binding)


@pytest.fixture
def obb_control():
    case = controls.LifecycleEvidenceTests()
    case.setUp()
    try:
        case._prepare_family('ocr')
        case.receipt['family'] = 'rotated_detection'
        case.receipt['export'] = None
        case.receipt['target'] = None
        for path in case.receipt['train']['family_files'].values():
            del case.files[path]
        manifest = json.loads((case.root / 'version/manifest.json').read_bytes())
        labels = json.loads((case.root / 'version/team-data.json').read_bytes())
        for name, marker in [('c.bin', b'negative train pixels'), ('d.bin', b'negative val pixels')]:
            case.put('source/' + name, marker)
            manifest['files'].append({'origin': 'source', 'kind': 'image', 'relative_path': name,
                'source_path': '/original/source/' + name, 'sha256': sha(marker), 'size_bytes': len(marker), 'snapshot_path': None})
            labels['eligibility'].append({'relative_path': name, 'image_uuid': name[0] * 64})
            case.receipt['dataset']['files']['source:' + name] = 'source/' + name
        manifest['content_digest'] = sha(canonical({k: v for k, v in manifest.items() if k != 'content_digest'}))
        case.put('version/manifest.json', manifest)
        case.put('version/team-data.json', labels)
        binding = json.loads((case.root / 'model/job_receipt.json').read_bytes())['training_provenance']
        dataset = '/original/project/dataset/rotated_detection'
        snapshot = binding['version_dir'] + '/labels/family/rotated_detection/rotated_boxes.json'
        samples = []
        for name, split, count in [('a.bin', 'train', 2), ('c.bin', 'train', 0), ('b.bin', 'val', 1), ('d.bin', 'val', 0)]:
            value = sha((case.root / ('source/' + name)).read_bytes())
            samples.append({'image': name, 'split': split, 'source_sha256': value,
                'objects': [{'label': 'part', 'box': {'cx': 4, 'cy': 4, 'width': 2, 'height': 1, 'angle_deg': -45}} for _ in range(count)]})
        mapping = {row['image']: {'source_relative_path': row['image'], 'source_sha256': row['source_sha256']} for row in samples}
        body = {'version': 2, 'samples': samples, 'source_dataset_path': '/original/source', 'source_map': mapping}
        raw = (json.dumps(body, ensure_ascii=False, indent=2) + '\n').encode()
        manifest_sha = sha(raw)
        rows = sorted([{'relative_path': 'rotated_boxes.json', 'source_path': dataset + '/rotated_boxes.json',
                        'sha256': manifest_sha, 'snapshot_path': snapshot},
                       *({'relative_path': row['image'], 'source_path': dataset + '/' + row['image'],
                          'sha256': row['source_sha256'], 'snapshot_path': None} for row in samples if row['objects'])],
                      key=lambda row: row['source_path'])
        copies = {dataset + '/rotated_boxes.json': 'obb/current/rotated_boxes.json', snapshot: 'obb/frozen/rotated_boxes.json',
                  **{dataset + '/' + row['image']: 'obb/current/' + row['image'] for row in samples}}
        case.put(copies[dataset + '/rotated_boxes.json'], raw)
        case.put(copies[snapshot], raw)
        for row in samples:
            case.put(copies[dataset + '/' + row['image']], (case.root / ('source/' + row['image'])).read_bytes())
        digest = hashlib.sha256(b'rotated-detection-single-object-v1\0' + manifest_sha.encode('ascii'))
        # Exact producer: object image once per object, explicit empty image once.
        digest_rows = [(row['image'], row['source_sha256']) for row in samples for _ in range(max(1, len(row['objects'])))]
        for image, value in sorted(digest_rows):
            digest.update(b'\0' + image.encode('utf-8') + b'\0' + value.encode('ascii'))
        provenance = {'dataset_sha256': 'sha256:' + digest.hexdigest(), 'manifest_sha256': manifest_sha,
            'source_sha256': {row['image']: row['source_sha256'] for row in samples},
            'split_counts': {'train': 2, 'val': 2, 'test': 0}, 'source_image_count': 4,
            'object_count': 3, 'direction_enabled': False, 'source_dataset_path': '/original/source', 'source_map': mapping}
        binding.update(family_task='rotated_detection', family_dataset_path=dataset, family_inputs=rows,
            family_inputs_sha256=sha(json.dumps(rows, sort_keys=True, separators=(',', ':')).encode()),
            family_provenance=provenance, family_dataset_sha256=provenance['dataset_sha256'],
            family_source_image_uuids=sorted(row['image_uuid'] for row in labels['eligibility']),
            team_data=labels, team_data_sha256=sha(canonical(labels)), manifest_sha256=manifest['content_digest'],
            split_sha256=sha(json.dumps([{key: row[key] for key in ('origin', 'relative_path', 'sha256')} for row in manifest['files']], sort_keys=True, separators=(',', ':')).encode()))
        case.receipt['train']['family_files'] = copies
        _write_binding(case, binding)
        case.change('saved/pipeline.json', lambda value: value['nodes'][0]['data'].update(task='rotated_detection'))
        yield case
    finally:
        case.doCleanups()


def test_obb_prepared_original_object_inventory_keeps_full_background_copy_and_first_five(obb_control):
    case = obb_control
    result = case.check()
    assert result['stages']['dataset']['state'] == 'verified', result
    assert result['stages']['labels']['state'] == 'verified', result
    assert result['stages']['train']['state'] == 'verified', result
    assert result['stages']['eval']['state'] == 'verified', result
    assert result['stages']['flow_or_adoption']['state'] == 'verified', result
    binding = json.loads((case.root / 'model/job_receipt.json').read_bytes())['training_provenance']
    assert len(binding['family_inputs']) == 3  # manifest + two object-bearing images
    assert len(case.receipt['train']['family_files']) == 6  # all four images + current/frozen manifest
    assert len(binding['family_source_image_uuids']) == 4
    assert result['stages']['export']['state'] == result['stages']['target']['state'] == 'pending'
    for key in ('runtime_execution_reproduced', 'human_truth_approved', 'model_quality_approved', 'target_execution_approved', 'parent_accepted'):
        assert result[key] is False


@pytest.mark.parametrize('corruption', ['negative_missing', 'negative_bytes', 'negative_uuid', 'extra_copy', 'alias_copy',
    'input_order', 'invented_negative_row', 'snapshot_bytes', 'source_map', 'source_digest', 'duplicate_image', 'split',
    'object_count', 'image_count', 'direction', 'box', 'label', 'dataset_job', 'dataset_meta', 'approval', 'manifest_digest'])
def test_obb_changed_or_incomplete_original_records_cannot_qualify(obb_control, corruption):
    case = obb_control
    assert case.check()['stages']['train']['state'] == 'verified', case.check()
    binding = json.loads((case.root / 'model/job_receipt.json').read_bytes())['training_provenance']
    copies = case.receipt['train']['family_files']
    if corruption == 'negative_missing':
        del copies[binding['family_dataset_path'] + '/c.bin']
    elif corruption == 'negative_bytes':
        case.put('obb/current/c.bin', b'changed negative bytes')
    elif corruption == 'negative_uuid':
        binding['family_source_image_uuids'].remove('c' * 64)
        _write_binding(case, binding)
    elif corruption == 'extra_copy':
        copies['/foreign/extra'] = 'source/a.bin'
    elif corruption == 'alias_copy':
        copies[binding['family_dataset_path'] + '/c.bin'] = 'obb/current/a.bin'
    elif corruption in ('input_order', 'invented_negative_row'):
        if corruption == 'input_order':
            binding['family_inputs'].reverse()
        else:
            binding['family_inputs'].append({'relative_path': 'c.bin', 'source_path': binding['family_dataset_path'] + '/c.bin',
                'sha256': case.files['obb/current/c.bin']['sha256'], 'snapshot_path': None})
            binding['family_inputs'].sort(key=lambda row: row['source_path'])
        binding['family_inputs_sha256'] = sha(json.dumps(binding['family_inputs'], sort_keys=True, separators=(',', ':')).encode())
        _write_binding(case, binding)
    elif corruption == 'snapshot_bytes':
        case.put('obb/frozen/rotated_boxes.json', (case.root / 'obb/frozen/rotated_boxes.json').read_bytes() + b' ')
    elif corruption in ('source_map', 'source_digest', 'duplicate_image', 'split', 'direction', 'box', 'label'):
        def mutate(value):
            if corruption == 'source_map':
                del value['source_map']['c.bin']
            elif corruption == 'source_digest':
                value['samples'][1]['source_sha256'] = '0' * 64
            elif corruption == 'duplicate_image':
                value['samples'][1]['image'] = 'a.bin'
            elif corruption == 'split':
                value['samples'][0]['split'] = 'foreign'
            elif corruption == 'direction':
                value['samples'][0]['objects'][0]['direction_deg'] = 0
            elif corruption == 'box':
                value['samples'][0]['objects'][0]['box']['angle_deg'] = 90
            else:
                value['samples'][0]['objects'][0]['label'] = '../foreign'
        _rewrite_manifest(case, mutate)
    elif corruption in ('object_count', 'image_count', 'manifest_digest'):
        key = {'object_count': 'object_count', 'image_count': 'source_image_count', 'manifest_digest': 'manifest_sha256'}[corruption]
        binding['family_provenance'][key] = 0 if corruption != 'manifest_digest' else '0' * 64
        _write_binding(case, binding)
    elif corruption == 'dataset_job':
        case.change('model/job_receipt.json', lambda value: value.update(dataset_path='/foreign/dataset'))
    elif corruption == 'dataset_meta':
        case.change('model/model_meta.json', lambda value: value.update(dataset_path='/foreign/dataset'))
    else:
        binding['team_data']['eligibility'] = [row for row in binding['team_data']['eligibility'] if row['relative_path'] != 'c.bin']
        case.put('version/team-data.json', binding['team_data'])
        binding['team_data_sha256'] = sha(canonical(binding['team_data']))
        _write_binding(case, binding)
    result = case.check()
    assert result['stages']['train']['state'] != 'verified', result
    assert result['record_chain_verified'] is False


@pytest.mark.parametrize('missing', ['family_dataset_sha256', 'family_source_image_uuids', 'approved_only_training'])
def test_obb_legacy_missing_original_binding_is_not_adopted(obb_control, missing):
    case = obb_control
    assert case.check()['stages']['train']['state'] == 'verified', case.check()
    binding = json.loads((case.root / 'model/job_receipt.json').read_bytes())['training_provenance']
    if missing == 'approved_only_training':
        del binding['team_data']['settings'][missing]
        case.put('version/team-data.json', binding['team_data'])
        binding['team_data_sha256'] = sha(canonical(binding['team_data']))
    else:
        del binding[missing]
    _write_binding(case, binding)
    result = case.check()
    assert result['stages']['train']['state'] != 'verified', result
    assert result['record_chain_verified'] is False


def test_obb_raw_manifest_changes_cannot_hide_repeated_object_digest(obb_control):
    case = obb_control
    assert case.check()['stages']['train']['state'] == 'verified', case.check()
    _rewrite_manifest(case, lambda value: value['samples'][0]['objects'].pop())
    binding = json.loads((case.root / 'model/job_receipt.json').read_bytes())['training_provenance']
    # Updating raw pins and the declared count does not replace the producer digest.
    binding['family_provenance']['object_count'] -= 1
    _write_binding(case, binding)
    result = case.check()
    assert result['stages']['train']['state'] != 'verified', result
    assert result['record_chain_verified'] is False
