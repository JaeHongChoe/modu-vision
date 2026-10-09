"""Synthetic copied record controls; no pixel decoder, training or adoption."""
import copy
import hashlib
import json

import pytest

from backend.tests import test_model_lifecycle_evidence as controls

sha = controls.sha
canonical = controls.canonical


def _write_binding(case, binding):
    family = case.receipt['family']
    case._replace_family_binding(binding, family)
    extra = {'dataset_path': binding['family_dataset_path'], 'source_dataset_path': '/original/source'}
    if family == 'enhancement':
        extra.update(version=1, architecture='rgb_residual_cnn', mode='explicit_pairs',
                     provenance=binding['family_provenance'], dataset_provenance=binding['family_provenance'])
        case.change('model/job_receipt.json', lambda value: value.pop('dataset_fingerprint', None))
    else:
        extra.update(model_kind='dcgan_defect_crop', image_size=64,
                     source_manifest_sha256=binding['family_dataset_sha256'], quality_status='unvalidated')
        case.change('model/model_meta.json', lambda value: value.pop('version', None))
    case.change('model/model_meta.json', lambda value: value.update(extra))


def _rewrite(case, mutate, *, rebind_gan_raw=False):
    family = case.receipt['family']; name = 'pairs.json' if family == 'enhancement' else 'defect_gan.json'
    path = 'prepared/current/' + name
    body = json.loads((case.root / path).read_bytes()); mutate(body)
    raw = (json.dumps(body, ensure_ascii=False, indent=2) + '\n').encode()
    case.put(path, raw); case.put('prepared/frozen/' + name, raw)
    binding = json.loads((case.root / 'model/job_receipt.json').read_bytes())['training_provenance']
    next(row for row in binding['family_inputs'] if row['relative_path'] == name)['sha256'] = sha(raw)
    binding['family_inputs_sha256'] = sha(json.dumps(binding['family_inputs'], sort_keys=True, separators=(',', ':')).encode())
    if rebind_gan_raw:
        binding['family_dataset_sha256'] = sha(raw)
        binding['family_provenance'] = {**body['provenance'], 'manifest_sha256': sha(raw)}
    _write_binding(case, binding)


@pytest.fixture
def prepared_control(request):
    family = request.param
    case = controls.LifecycleEvidenceTests(); case.setUp()
    try:
        case._prepare_family('ocr')
        for path in case.receipt['train']['family_files'].values():
            del case.files[path]
        case.receipt.update(family=family, flow_or_adoption=None, export=None, target=None)
        manifest = json.loads((case.root / 'version/manifest.json').read_bytes())
        labels = json.loads((case.root / 'version/team-data.json').read_bytes())
        additions = [('c.bin', b'actual synthetic distinct test bytes')]
        if family == 'defect_gan':
            additions.append(('d.bin', b'original normal control not selected for defect generation'))
        for name, value in additions:
            case.put('source/' + name, value)
            manifest['files'].append({'origin': 'source', 'kind': 'image', 'relative_path': name,
                'source_path': '/original/source/' + name, 'sha256': sha(value), 'size_bytes': len(value), 'snapshot_path': None})
            labels['eligibility'].append({'relative_path': name, 'image_uuid': name[0] * 64})
            case.receipt['dataset']['files']['source:' + name] = 'source/' + name
        labels['settings']['approved_only_training'] = family == 'enhancement'
        manifest['task'] = family
        manifest['content_digest'] = sha(canonical({k: v for k, v in manifest.items() if k != 'content_digest'}))
        case.put('version/manifest.json', manifest); case.put('version/team-data.json', labels)
        binding = json.loads((case.root / 'model/job_receipt.json').read_bytes())['training_provenance']
        dataset = '/original/project/dataset/' + family
        name = 'pairs.json' if family == 'enhancement' else 'defect_gan.json'
        snapshot = binding['version_dir'] + '/labels/family/' + family + '/' + name
        member_bytes = {}; records = []; mapping = []
        for index, (source, split) in enumerate([('a.bin', 'train'), ('b.bin', 'val'), ('c.bin', 'test')]):
            value = (case.root / ('source/' + source)).read_bytes(); digest = sha(value)
            if family == 'enhancement':
                image = 'inputs/' + str(index) + '.bin'; target = 'targets/' + str(index) + '.bin'
                target_value = b'explicit different paired target ' + str(index).encode()
                member_bytes.update({image: value, target: target_value})
                records.append({'input': image, 'target': target, 'split': split, 'source_relative_path': source,
                    'source_sha256': digest, 'input_sha256': digest, 'target_sha256': sha(target_value),
                    'target_source_relative_path': '참조/' + str(index) + '.bin'})
            else:
                image = 'images/' + str(index) + '.bin'; box = [0, 0, 16, 16]
                member_bytes[image] = value
                records.append({'image': image, 'bbox': box, 'split': split, 'source_sha256': digest, 'label': 'scratch'})
                mapping.append({'image': image, 'source_image': source, 'source_sha256': digest,
                                'source_bbox': box, 'split': split, 'label': 'scratch'})
        if family == 'enhancement':
            body = {'version': 1, 'task': family, 'mode': 'explicit_pairs', 'source_dataset_path': '/original/source',
                'target_source_path': '/original/reference_targets',
                'alignment': 'exact pixel dimensions; operator-supplied registration, not automatic alignment', 'records': records}
            portable = {k: v for k, v in body.items() if k not in {'source_dataset_path', 'target_source_path', 'dataset_path', 'provenance'}}
            provenance = {'dataset_sha256': sha(json.dumps(portable, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()),
                          'sample_count': 3, 'source_dataset_path': '/original/source'}
        else:
            provenance = {'source_dataset_path': '/original/source',
                          'source_map': {row['image']: {'source_relative_path': row['source_image'], 'source_sha256': row['source_sha256']} for row in mapping}}
            body = {'version': 1, 'image_size': 64, 'samples': records, 'source_dataset_path': '/original/source',
                    'source_map': mapping, 'provenance': provenance}
        raw = (json.dumps(body, ensure_ascii=False, indent=2) + '\n').encode()
        rows = sorted([{'relative_path': name, 'source_path': dataset + '/' + name, 'sha256': sha(raw), 'snapshot_path': snapshot},
                       *({'relative_path': member, 'source_path': dataset + '/' + member, 'sha256': sha(value), 'snapshot_path': None}
                         for member, value in member_bytes.items())], key=lambda row: row['source_path'])
        copies = {row['source_path']: 'prepared/current/' + row['relative_path'] for row in rows}
        copies[snapshot] = 'prepared/frozen/' + name
        case.put(copies[dataset + '/' + name], raw); case.put(copies[snapshot], raw)
        for member, value in member_bytes.items():
            case.put(copies[dataset + '/' + member], value)
        if family == 'defect_gan':
            provenance = {**provenance, 'manifest_sha256': sha(raw)}
        binding.update(family_task=family, family_dataset_path=dataset, family_inputs=rows,
            family_inputs_sha256=sha(json.dumps(rows, sort_keys=True, separators=(',', ':')).encode()),
            family_provenance=provenance, family_source_image_uuids=['a' * 64, 'b' * 64, 'c' * 64],
            team_data=labels, team_data_sha256=sha(canonical(labels)), manifest_sha256=manifest['content_digest'],
            split_sha256=sha(json.dumps([{key: row[key] for key in ('origin', 'relative_path', 'sha256')} for row in manifest['files']], sort_keys=True, separators=(',', ':')).encode()))
        binding.pop('family_dataset_sha256', None)
        if family == 'defect_gan':
            binding['family_dataset_sha256'] = sha(raw)
        case.receipt['train']['family_files'] = copies
        _write_binding(case, binding)
        if family == 'enhancement':
            case.change('saved/pipeline.json', lambda value: value['nodes'][0]['data'].update(
                node_type='preprocess', model_job_id='job_original', params={'operation': 'enhancement'}))
            case.receipt['flow_or_adoption'] = {'kind': 'flow', 'graph': 'saved/pipeline.json'}
        yield case
    finally:
        case.doCleanups()


@pytest.mark.parametrize('prepared_control', ['enhancement', 'defect_gan'], indirect=True)
def test_original_pair_generator_records_verify_without_model_or_adoption(prepared_control):
    case = prepared_control; result = case.check()
    assert result['stages']['dataset']['state'] == 'verified', result
    assert result['stages']['labels']['state'] == 'verified', result
    assert result['stages']['train']['state'] == 'verified', result
    assert result['stages']['eval']['state'] == 'verified', result
    for name in ('export', 'target'):
        assert result['stages'][name]['state'] == 'pending', result
    job = json.loads((case.root / 'model/job_receipt.json').read_bytes())
    if case.receipt['family'] == 'enhancement':
        assert 'dataset_fingerprint' not in job
        assert 'family_dataset_sha256' not in job['training_provenance']
        assert len(case.receipt['train']['family_files']) == 8  # six pixels + two manifest copies
        assert result['stages']['flow_or_adoption']['state'] == 'verified', result
        body = json.loads((case.root / 'prepared/current/pairs.json').read_bytes())
        portable = {k: v for k, v in body.items() if k not in {'source_dataset_path', 'target_source_path', 'dataset_path', 'provenance'}}
        assert job['training_provenance']['family_provenance']['dataset_sha256'] != sha(json.dumps(portable, sort_keys=True, separators=(',', ':')).encode())
    else:
        assert 'version' not in json.loads((case.root / 'model/model_meta.json').read_bytes())
        assert len(job['training_provenance']['family_source_image_uuids']) == 3
        assert len(job['training_provenance']['team_data']['eligibility']) == 4
        assert len(case.receipt['train']['family_files']) == 5  # three selected pixels + two manifests
        assert result['stages']['flow_or_adoption']['reason'] == 'GAN generation/adoption receipt format'
    for key in ('record_chain_verified', 'runtime_execution_reproduced', 'human_truth_approved', 'model_quality_approved', 'target_execution_approved', 'parent_accepted'):
        assert result[key] is False


@pytest.mark.parametrize('prepared_control', ['enhancement', 'defect_gan'], indirect=True)
@pytest.mark.parametrize('corruption', ['copy_missing', 'copy_bytes', 'snapshot', 'extra_copy', 'alias_copy', 'row_order',
    'row_digest', 'source_uuid', 'approved_only', 'missing_setting', 'dataset_job', 'dataset_meta', 'meta_source',
    'meta_producer', 'provenance', 'original_source', 'split', 'missing_member'])
def test_pair_generator_byte_scope_and_producer_changes_are_not_adopted(prepared_control, corruption):
    case = prepared_control; family = case.receipt['family']
    assert case.check()['stages']['train']['state'] == 'verified', case.check()
    binding = json.loads((case.root / 'model/job_receipt.json').read_bytes())['training_provenance']
    copies = case.receipt['train']['family_files']; name = 'pairs.json' if family == 'enhancement' else 'defect_gan.json'
    member = 'inputs/0.bin' if family == 'enhancement' else 'images/0.bin'
    if corruption == 'copy_missing':
        del copies[binding['family_dataset_path'] + '/' + member]
    elif corruption == 'copy_bytes':
        case.put('prepared/current/' + member, b'changed raw prepared pixels')
    elif corruption == 'snapshot':
        case.put('prepared/frozen/' + name, (case.root / ('prepared/frozen/' + name)).read_bytes() + b' ')
    elif corruption == 'extra_copy':
        copies['/foreign/extra'] = 'source/a.bin'
    elif corruption == 'alias_copy':
        copies[binding['family_dataset_path'] + '/' + member] = copies[binding['family_dataset_path'] + '/' + name]
    elif corruption == 'row_order':
        binding['family_inputs'].reverse()
        binding['family_inputs_sha256'] = sha(json.dumps(binding['family_inputs'], sort_keys=True, separators=(',', ':')).encode())
        _write_binding(case, binding)
    elif corruption in ('row_digest', 'source_uuid', 'provenance'):
        if corruption == 'row_digest': binding['family_inputs_sha256'] = '0' * 64
        elif corruption == 'source_uuid': binding['family_source_image_uuids'].append('d' * 64)
        else: binding['family_provenance']['source_dataset_path'] = '/foreign/source'
        _write_binding(case, binding)
    elif corruption in ('approved_only', 'missing_setting'):
        if corruption == 'approved_only':
            binding['team_data']['settings']['approved_only_training'] = True
            binding['team_data']['eligibility'] = binding['team_data']['eligibility'][1:]
        else:
            del binding['team_data']['settings']['approved_only_training']
        case.put('version/team-data.json', binding['team_data'])
        binding['team_data_sha256'] = sha(canonical(binding['team_data']))
        _write_binding(case, binding)
    elif corruption == 'dataset_job':
        case.change('model/job_receipt.json', lambda value: value.update(dataset_path='/foreign/dataset'))
    elif corruption in ('dataset_meta', 'meta_source', 'meta_producer'):
        key = {'dataset_meta': 'dataset_path', 'meta_source': 'source_dataset_path', 'meta_producer': 'architecture' if family == 'enhancement' else 'model_kind'}[corruption]
        case.change('model/model_meta.json', lambda value: value.update({key: 'foreign'}))
    else:
        def mutate(body):
            rows = body['records' if family == 'enhancement' else 'samples']
            if corruption == 'original_source': body['source_dataset_path'] = '/foreign/source'
            elif corruption == 'split': rows[0]['split'] = 'foreign'
            elif family == 'enhancement': rows[0]['target'] = 'targets/missing.bin'
            else: rows[0]['image'] = 'images/missing.bin'
        _rewrite(case, mutate, rebind_gan_raw=family == 'defect_gan')
    result = case.check()
    assert result['stages']['train']['state'] != 'verified', result
    assert result['record_chain_verified'] is False


@pytest.mark.parametrize('prepared_control', ['enhancement'], indirect=True)
@pytest.mark.parametrize('corruption', ['top_fp', 'nested_fp', 'missing_dataset', 'unsupported_extra', 'target_bytes',
    'target_cross_split', 'input_repeat', 'source_digest', 'target_origin', 'alignment', 'synthetic_mode'])
def test_enhancement_legacy_omission_is_narrow_and_pairs_remain_bound(prepared_control, corruption):
    case = prepared_control; assert case.check()['stages']['train']['state'] == 'verified', case.check()
    if corruption in ('top_fp', 'missing_dataset', 'unsupported_extra'):
        def mutate(value):
            if corruption == 'top_fp': value['dataset_fingerprint'] = 'v1:' + '0' * 64
            elif corruption == 'missing_dataset': del value['dataset_path']
            else: value['undeclared_legacy_scope'] = True
        case.change('model/job_receipt.json', mutate)
    elif corruption == 'nested_fp':
        binding = json.loads((case.root / 'model/job_receipt.json').read_bytes())['training_provenance']
        binding['dataset_fingerprint'] = 'v1:' + '0' * 64; _write_binding(case, binding)
    elif corruption == 'target_bytes':
        case.put('prepared/current/targets/0.bin', b'changed target bytes')
    else:
        def mutate(body):
            if corruption == 'target_cross_split':
                body['records'][1]['target'] = body['records'][0]['target']
                body['records'][1]['target_sha256'] = body['records'][0]['target_sha256']
            elif corruption == 'input_repeat': body['records'][1]['input'] = body['records'][0]['input']
            elif corruption == 'source_digest': body['records'][0]['source_sha256'] = '0' * 64
            elif corruption == 'target_origin': body['records'][0]['target_source_relative_path'] = '../foreign'
            elif corruption == 'alignment': body['alignment'] = 'automatically aligned'
            else: body['mode'] = 'synthetic_gaussian'
        _rewrite(case, mutate)
    assert case.check()['stages']['train']['state'] != 'verified', case.check()


@pytest.mark.parametrize('prepared_control', ['defect_gan'], indirect=True)
@pytest.mark.parametrize('corruption', ['bbox_bool', 'bbox_small', 'mapping_box', 'mapping_split', 'mapping_label',
    'source_bytes', 'raw_digest', 'missing_digest', 'crop_label', 'normal_uuid', 'missing_top_fp'])
def test_gan_raw_crop_label_partition_and_selected_uuid_scope_is_strict(prepared_control, corruption):
    case = prepared_control; assert case.check()['stages']['train']['state'] == 'verified', case.check()
    if corruption in ('raw_digest', 'missing_digest', 'normal_uuid'):
        binding = json.loads((case.root / 'model/job_receipt.json').read_bytes())['training_provenance']
        if corruption == 'raw_digest': binding['family_dataset_sha256'] = '0' * 64
        elif corruption == 'missing_digest': del binding['family_dataset_sha256']
        else: binding['family_source_image_uuids'].append('d' * 64)
        if corruption == 'missing_digest':
            # Preserve original metadata for a truly missing completed binding.
            case._replace_family_binding(binding, 'defect_gan')
            case.change('model/model_meta.json', lambda value: value.update(dataset_path=binding['family_dataset_path'],
                source_dataset_path='/original/source', model_kind='dcgan_defect_crop', image_size=64,
                source_manifest_sha256='0' * 64))
        else: _write_binding(case, binding)
    elif corruption == 'source_bytes':
        case.put('prepared/current/images/0.bin', b'changed prepared source bytes')
    elif corruption == 'missing_top_fp':
        case.change('model/job_receipt.json', lambda value: value.pop('dataset_fingerprint'))
    else:
        def mutate(body):
            entry = body['samples'][0]; mapping = body['source_map'][0]
            if corruption in ('bbox_bool', 'bbox_small'):
                entry['bbox'] = [False, 0, 16, 16] if corruption == 'bbox_bool' else [0, 0, 15, 16]
                mapping['source_bbox'] = entry['bbox']
            elif corruption == 'mapping_box': mapping['source_bbox'] = [1, 0, 17, 16]
            elif corruption == 'mapping_split': mapping['split'] = 'val'
            elif corruption == 'mapping_label': mapping['label'] = 'foreign'
            else: entry['label'] = mapping['label'] = ''
        _rewrite(case, mutate, rebind_gan_raw=True)
    assert case.check()['stages']['train']['state'] != 'verified', case.check()


@pytest.mark.parametrize('prepared_control', ['defect_gan'], indirect=True)
def test_gan_generation_and_adoption_claims_do_not_become_inspection_flow(prepared_control):
    case = prepared_control; assert case.check()['stages']['train']['state'] == 'verified', case.check()
    case.receipt['flow_or_adoption'] = {'kind': 'adoption', 'generation_manifest': 'prepared/current/defect_gan.json'}
    case.receipt['export'] = {'manifest': 'prepared/current/defect_gan.json', 'parity': 'evaluation.json'}
    result = case.check()
    assert result['stages']['flow_or_adoption'] == {'state': 'pending', 'reason': 'GAN generation/adoption receipt format'}
    assert result['stages']['export']['state'] == result['stages']['target']['state'] == 'pending'
    assert result['record_chain_verified'] is False


@pytest.mark.parametrize('prepared_control', ['defect_gan'], indirect=True)
def test_gan_retained_adoption_comparison_is_not_completed_job_evaluation(prepared_control):
    case = prepared_control; assert case.check()['stages']['train']['state'] == 'verified', case.check()
    case.put('adoption-comparison.json', {'baseline': 'original held-out classifier', 'candidate': 'synthetic adopted train-only cohort',
        'generator_job_id': 'job_original', 'status': 'passed', 'quality_approval': False})
    case.receipt['eval'] = 'adoption-comparison.json'
    result = case.check()
    assert result['stages']['train']['state'] == 'verified', result
    assert result['stages']['eval']['state'] != 'verified', result
    assert result['stages']['flow_or_adoption']['reason'] == 'GAN generation/adoption receipt format'
    assert result['record_chain_verified'] is False


@pytest.mark.parametrize('prepared_control', ['enhancement'], indirect=True)
def test_enhancement_missing_top_fingerprint_does_not_adopt_generic_unprepared_binding(prepared_control):
    case = prepared_control; assert case.check()['stages']['train']['state'] == 'verified', case.check()
    binding = json.loads((case.root / 'model/job_receipt.json').read_bytes())['training_provenance']
    for key in ('family_inputs', 'family_task', 'family_dataset_path', 'family_provenance'):
        del binding[key]
    case.change('model/job_receipt.json', lambda value: value.update(training_provenance=binding))
    case.change('model/model_meta.json', lambda value: value.update(training_provenance=binding))
    del case.receipt['train']['family_files']
    result = case.check()
    assert result['stages']['dataset']['state'] == result['stages']['labels']['state'] == 'verified', result
    assert result['stages']['train']['state'] == 'refused', result
    assert result['stages']['train']['reason'] == 'legacy enhancement receipt has no original prepared pair binding'
