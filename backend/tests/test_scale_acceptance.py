"""Acceptance measurements use isolated fixtures and do not invent target support."""
from pathlib import Path
import pytest
from scripts.acceptance.scale_and_soak import measure_scale, queue_control


def test_search_measurement_covers_every_metadata_row_and_decodes_separate_image(tmp_path):
    result = measure_scale(tmp_path / 'scale', rows=250, width=512, height=384, repeats=3)
    assert result['metadata']['fixture_kind'] == 'metadata_only_not_image_inventory'
    assert result['metadata']['rows'] == result['metadata']['paged_rows'] == 250
    assert result['metadata']['unique_ordered_rows']
    assert result['metadata']['searches']['missing']['returned'] == 0
    assert result['metadata']['searches']['ng']['returned'] > 0
    assert result['large_image']['decoded']
    assert result['large_image']['dimensions'] == [512, 384]
    assert result['large_image']['source_unchanged']
    assert not result['target_support_approved']
    assert not result['soak_72h_completed']


def test_owned_measurement_directory_cannot_replace_existing_evidence(tmp_path):
    root = tmp_path / 'existing'; root.mkdir()
    (root / 'preserved').write_text('receipt', encoding='utf-8')
    with pytest.raises(FileExistsError): measure_scale(root, rows=10, width=16, height=16, repeats=1)
    assert (root / 'preserved').read_text(encoding='utf-8') == 'receipt'


def test_queue_control_exercises_duplicate_backpressure_and_restart_without_model_claim(tmp_path):
    result = queue_control(tmp_path / 'queue', cycles=3, capacity=4)
    assert result['completed'] == 12
    assert result['duplicate_requests'] == 12
    assert result['backpressure_refusals'] == 3
    assert result['reopened_completed'] == 12
    assert result['source_unchanged']
    assert not result['actual_model_inference']
    assert not result['soak_72h_completed']


# Fixed optional manifest-measurement controls. Root executes after design review.
# All images below are tiny GENERATED UNIT FIXTURES, never real-photo scale proof.
import copy
import hashlib
import json
import math
import os
from PIL import Image
from scripts.acceptance import scale_and_soak as measurement


def manifest_measurement():
    function = getattr(measurement, 'measure_photo_manifest', None)
    assert callable(function), 'Optional pinned real-photo manifest measurement is not implemented'
    return function


def file_snapshot(path):
    row = path.lstat()
    return (hashlib.sha256(path.read_bytes()).hexdigest(), row.st_dev, row.st_ino,
        row.st_mode, row.st_nlink, row.st_size, row.st_mtime_ns, row.st_ctime_ns,
        row.st_uid, row.st_gid)


def write_photo_manifest(path, source, rows):
    value = {'schema_version': 1, 'kind': 'photo_decode_measurement_manifest',
             'source_root': str(source), 'files': rows}
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')), encoding='utf-8')
    return value


def tiny_manifest(tmp_path):
    source = tmp_path / 'original-photos'
    source.mkdir()
    files = [('가/첫 검사.png', (32, 24), 'PNG'),
             ('나/둘 검사.jpg', (48, 32), 'JPEG'),
             ('다/셋 검사.bmp', (24, 40), 'BMP')]
    rows = []
    for index, (name, dimensions, format_name) in enumerate(files):
        path = source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        image = Image.new('RGB', dimensions, (40 + index, 80, 120))
        try:
            image.save(path, format=format_name)
        finally:
            image.close()
        body = path.read_bytes()
        rows.append({'relative_path': name, 'size': len(body), 'sha256': hashlib.sha256(body).hexdigest()})
    manifest = tmp_path / 'photos-manifest.json'
    write_photo_manifest(manifest, source, rows)
    return source, manifest, rows, files


def test_manifest_measurement_decodes_exact_order_counts_and_retains_originals(tmp_path, monkeypatch):
    function = manifest_measurement()
    source, manifest, rows, files = tiny_manifest(tmp_path)
    before = {name: file_snapshot(source / name) for name, _, _ in files}
    manifest_before = file_snapshot(manifest)
    original_decode = measurement.decode_image_file
    seen = []
    def record_decode(path):
        seen.append(Path(path).relative_to(source).as_posix())
        return original_decode(path)
    monkeypatch.setattr(measurement, 'decode_image_file', record_decode)
    result = function(tmp_path / 'measurement', manifest, repeats=2)
    names = [row['relative_path'] for row in rows]
    assert seen == [name for name in names for _ in range(2)]
    assert result['schema_version'] == 1 and result['kind'] == 'manifest_photo_decode_measurement'
    assert result['manifest']['file_count'] == 3
    assert result['manifest']['source_bytes'] == sum(row['size'] for row in rows)
    assert result['manifest']['sha256'] == manifest_before[0]
    assert result['manifest']['ordered_paths_sha256'] == hashlib.sha256(json.dumps(names, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()
    assert [row['relative_path'] for row in result['files']] == names
    assert [row['dimensions'] for row in result['files']] == [[32, 24], [48, 32], [24, 40]]
    assert all(row['decoded'] is True and row['decode_failures'] == 0 for row in result['files'])
    assert result['extension_counts'] == {'.png': 1, '.jpg': 1, '.bmp': 1}
    assert result['decode']['attempted'] == result['decode']['succeeded'] == 6
    assert result['decode']['failed'] == 0 and result['decode']['all_files_decoded'] is True
    timing = result['decode']['timing']
    assert timing['samples'] == 6
    assert all(math.isfinite(timing[key]) and timing[key] >= 0 for key in ('min_ms', 'max_ms', 'p95_ms'))
    assert timing['min_ms'] <= timing['p95_ms'] <= timing['max_ms']
    assert result['manifest_unchanged'] is True and result['source_unchanged'] is True
    assert file_snapshot(manifest) == manifest_before
    assert {name: file_snapshot(source / name) for name, _, _ in files} == before
    assert not list(source.rglob('*.sqlite3')) and not list(source.rglob('receipt.json'))
    for flag in ('actual_model_inference', 'model_quality_approved', 'performance_approved',
                 'scale_10k_100k_qualified', 'target_support_approved', 'soak_72h_completed'):
        assert result[flag] is False


def test_manifest_measurement_reports_resource_observations_without_cold_cache_claim(tmp_path):
    function = manifest_measurement()
    _, manifest, _, _ = tiny_manifest(tmp_path)
    result = function(tmp_path / 'resources', manifest)
    assert result['host_before']['platform'] and result['host_after']['architecture']
    for key in ('disk_free_before_bytes', 'disk_free_after_bytes'):
        assert type(result[key]) is int and result[key] >= 0
    assert result['disk_free_change_bytes'] == result['disk_free_before_bytes'] - result['disk_free_after_bytes']
    assert result['process_peak_rss_bytes'] is None or (type(result['process_peak_rss_bytes']) is int and result['process_peak_rss_bytes'] > 0)
    assert result['decode']['cache_state'] == 'uncontrolled_os_cache'
    assert result['performance_approved'] is False


def test_manifest_percentile_uses_existing_nearest_rank_ms_definition():
    assert measurement.summary([4.0, 1.0, 9.0, 2.0]) == {'samples': 4, 'p95_ms': 9.0, 'max_ms': 9.0, 'min_ms': 1.0}


@pytest.mark.parametrize('relative', ['../outside.png', '/tmp/foreign.png', './photo.png', 'a//photo.png', 'a\\photo.png'])
def test_manifest_refuses_noncanonical_or_escaping_relative_path_before_decode(tmp_path, monkeypatch, relative):
    function = manifest_measurement()
    source, manifest, rows, _ = tiny_manifest(tmp_path)
    rows[0]['relative_path'] = relative
    write_photo_manifest(manifest, source, rows)
    def forbid_decode(_):
        pytest.fail('No photo may decode before all manifest paths are admitted')
    monkeypatch.setattr(measurement, 'decode_image_file', forbid_decode)
    output = tmp_path / 'refused-path'
    with pytest.raises(ValueError):
        function(output, manifest)
    assert not output.exists()


@pytest.mark.parametrize('changed', ['size', 'sha256'])
def test_manifest_refuses_changed_pinned_source_before_decode(tmp_path, monkeypatch, changed):
    function = manifest_measurement()
    source, manifest, rows, _ = tiny_manifest(tmp_path)
    rows[0][changed] = rows[0]['size'] + 1 if changed == 'size' else 'f' * 64
    write_photo_manifest(manifest, source, rows)
    def forbid_decode(_):
        pytest.fail('No photo may decode before all original pins are checked')
    monkeypatch.setattr(measurement, 'decode_image_file', forbid_decode)
    output = tmp_path / 'refused-pin'
    with pytest.raises(ValueError):
        function(output, manifest)
    assert not output.exists()


@pytest.mark.parametrize('changed', ['duplicate-path', 'unsorted', 'empty', 'duplicate-json-key'])
def test_manifest_refuses_ambiguous_order_or_empty_input(tmp_path, changed):
    function = manifest_measurement()
    source, manifest, rows, _ = tiny_manifest(tmp_path)
    if changed == 'duplicate-path':
        rows.append(copy.deepcopy(rows[-1]))
    elif changed == 'unsorted':
        rows = list(reversed(rows))
    elif changed == 'empty':
        rows = []
    write_photo_manifest(manifest, source, rows)
    if changed == 'duplicate-json-key':
        body = manifest.read_text(encoding='utf-8')
        manifest.write_text(body[:-1] + ',"files":[]}', encoding='utf-8')
    output = tmp_path / 'refused-manifest'
    with pytest.raises(ValueError):
        function(output, manifest)
    assert not output.exists()


@pytest.mark.parametrize('changed', ['linked-photo', 'linked-source-root', 'linked-manifest', 'hardlinked-photo'])
def test_manifest_refuses_input_aliases_instead_of_following_them(tmp_path, changed):
    function = manifest_measurement()
    source, manifest, rows, _ = tiny_manifest(tmp_path)
    if changed == 'linked-photo':
        alias = source / '가/별칭.png'
        alias.symlink_to(source / rows[0]['relative_path'])
        rows[0]['relative_path'] = '가/별칭.png'
        rows.sort(key=lambda row: row['relative_path'])
        write_photo_manifest(manifest, source, rows)
    elif changed == 'linked-source-root':
        alias = tmp_path / 'linked-root'
        alias.symlink_to(source, target_is_directory=True)
        write_photo_manifest(manifest, alias, rows)
    elif changed == 'linked-manifest':
        alias = tmp_path / 'linked-manifest.json'
        alias.symlink_to(manifest)
        manifest = alias
    else:
        os.link(source / rows[0]['relative_path'], source / 'hardlink.png')
    output = tmp_path / 'refused-alias'
    with pytest.raises(ValueError):
        function(output, manifest)
    assert not output.exists()


@pytest.mark.parametrize('bound', ['file-count', 'source-total', 'single-file'])
def test_manifest_explicit_read_budgets_refuse_before_decode(tmp_path, monkeypatch, bound):
    function = manifest_measurement()
    _, manifest, rows, _ = tiny_manifest(tmp_path)
    options = {'max_files': 2} if bound == 'file-count' else {'max_total_bytes': sum(row['size'] for row in rows) - 1} if bound == 'source-total' else {'max_file_bytes': max(row['size'] for row in rows) - 1}
    def forbid_decode(_):
        pytest.fail('Measurement budget must be checked before photo decode')
    monkeypatch.setattr(measurement, 'decode_image_file', forbid_decode)
    output = tmp_path / 'refused-budget'
    with pytest.raises(ValueError):
        function(output, manifest, **options)
    assert not output.exists()


@pytest.mark.parametrize('repeats', [0, 4, True])
def test_manifest_requires_bounded_positive_repeat_count(tmp_path, repeats):
    function = manifest_measurement()
    _, manifest, _, _ = tiny_manifest(tmp_path)
    output = tmp_path / 'refused-repeats'
    with pytest.raises(ValueError):
        function(output, manifest, repeats=repeats)
    assert not output.exists()


def test_manifest_measurement_preserves_existing_output_and_rejects_output_in_inputs(tmp_path):
    function = manifest_measurement()
    source, manifest, _, _ = tiny_manifest(tmp_path)
    existing = tmp_path / 'existing'
    existing.mkdir()
    sentinel = existing / 'receipt.json'
    sentinel.write_text('existing evidence', encoding='utf-8')
    with pytest.raises(FileExistsError):
        function(existing, manifest)
    assert sentinel.read_text() == 'existing evidence'
    with pytest.raises(ValueError):
        function(source / 'measurement-output', manifest)
    assert not (source / 'measurement-output').exists()


def test_manifest_keeps_decode_failure_in_counts_and_does_not_modify_bad_input(tmp_path):
    function = manifest_measurement()
    source = tmp_path / 'bad-original'
    source.mkdir()
    photo = source / 'bad.png'
    photo.write_bytes(b'not a decodable photo but a pinned source control')
    body = photo.read_bytes()
    manifest = tmp_path / 'bad-manifest.json'
    write_photo_manifest(manifest, source, [{'relative_path': 'bad.png', 'size': len(body), 'sha256': hashlib.sha256(body).hexdigest()}])
    before = file_snapshot(photo)
    result = function(tmp_path / 'failed-decode-evidence', manifest)
    assert result['manifest']['file_count'] == 1
    assert result['decode']['attempted'] == result['decode']['failed'] == 1
    assert result['decode']['succeeded'] == 0 and result['decode']['all_files_decoded'] is False
    assert result['files'][0]['decoded'] is False and result['files'][0]['decode_failures'] == 1
    assert result['files'][0]['error_codes'] == ['CORRUPT_HEADER']
    assert file_snapshot(photo) == before
    assert result['source_unchanged'] is True and result['performance_approved'] is False


@pytest.mark.parametrize('changed', ['photo', 'manifest'])
def test_manifest_detects_source_or_manifest_change_during_real_decode(tmp_path, monkeypatch, changed):
    function = manifest_measurement()
    source, manifest, rows, _ = tiny_manifest(tmp_path)
    original_decode = measurement.decode_image_file
    once = []
    def mutate_after_original_decode(path):
        result = original_decode(path)
        if not once:
            once.append(True)
            victim = Path(path) if changed == 'photo' else manifest
            victim.write_bytes(victim.read_bytes() + b'\n')
        return result
    monkeypatch.setattr(measurement, 'decode_image_file', mutate_after_original_decode)
    output = tmp_path / 'changed-input-evidence'
    with pytest.raises(ValueError):
        function(output, manifest)
    # Retain the failed owned output, never reset/delete it to pretend success.
    assert output.is_dir()
    assert not (output / 'receipt.json').exists()


def test_cli_explicit_manifest_mode_does_not_run_synthetic_scale_or_queue(tmp_path, monkeypatch, capsys):
    manifest_measurement()
    _, manifest, _, _ = tiny_manifest(tmp_path)
    output = tmp_path / 'cli-manifest-mode'
    calls = []
    def manifest_only(root, actual, **kwargs):
        calls.append((root, actual))
        return {'unit_cli_boundary_only': True, 'target_support_approved': False, 'soak_72h_completed': False}
    def forbid_synthetic(*args, **kwargs):
        pytest.fail('Explicit photo mode must not launch the unrelated synthetic 100k/80MP/queue controls')
    monkeypatch.setattr(measurement, 'measure_photo_manifest', manifest_only)
    monkeypatch.setattr(measurement, 'measure_scale', forbid_synthetic)
    monkeypatch.setattr(measurement, 'queue_control', forbid_synthetic)
    monkeypatch.setattr(measurement.sys, 'argv', ['scale_and_soak.py', '--output', str(output), '--photo-manifest', str(manifest)])
    measurement.main()
    assert calls == [(output / 'real-photos', manifest)]
    saved = json.loads((output / 'receipt.json').read_text())
    assert set(saved) == {'real_photos'} and saved['real_photos']['unit_cli_boundary_only'] is True
    capsys.readouterr()



def test_cli_default_synthetic_mode_retains_original_two_control_branches(tmp_path, monkeypatch, capsys):
    output = tmp_path / 'cli-existing-synthetic-mode'
    calls = []
    def scale(root, **kwargs):
        calls.append(('scale', root, kwargs))
        return {'unit_cli_boundary_only': True, 'metadata': {'fixture_kind': 'metadata_only_not_image_inventory'}, 'target_support_approved': False, 'soak_72h_completed': False}
    def queue(root, **kwargs):
        calls.append(('queue', root, kwargs))
        return {'fixture_kind': 'queue_control_without_model_or_target', 'actual_model_inference': False, 'soak_72h_completed': False}
    def forbid_photo(*args, **kwargs):
        pytest.fail('Existing synthetic CLI must not read a real-photo manifest')
    monkeypatch.setattr(measurement, 'measure_scale', scale)
    monkeypatch.setattr(measurement, 'queue_control', queue)
    if hasattr(measurement, 'measure_photo_manifest'):
        monkeypatch.setattr(measurement, 'measure_photo_manifest', forbid_photo)
    monkeypatch.setattr(measurement.sys, 'argv', ['scale_and_soak.py', '--output', str(output), '--rows', '10000', '--queue-cycles', '2'])
    measurement.main()
    assert calls == [('scale', output / 'scale', {'rows': 10000}), ('queue', output / 'queue', {'cycles': 2})]
    saved = json.loads((output / 'receipt.json').read_text())
    assert set(saved) == {'scale', 'queue'}
    assert saved['scale']['metadata']['fixture_kind'] == 'metadata_only_not_image_inventory'
    assert saved['queue']['actual_model_inference'] is False
    capsys.readouterr()



def test_manifest_cli_refuses_output_inside_originals_without_leaving_directory(tmp_path, monkeypatch):
    manifest_measurement()
    source, manifest, rows, _ = tiny_manifest(tmp_path)
    original_files = {row['relative_path']: file_snapshot(source / row['relative_path']) for row in rows}
    original_manifest = file_snapshot(manifest)
    original_members = sorted(path.relative_to(source).as_posix() for path in source.rglob('*'))
    output = source / 'forbidden-cli-output'
    monkeypatch.setattr(measurement.sys, 'argv', ['scale_and_soak.py', '--output', str(output), '--photo-manifest', str(manifest)])
    with pytest.raises(ValueError):
        measurement.main()
    assert not output.exists(), 'CLI must validate photo/input separation before outer output mkdir'
    assert {row['relative_path']: file_snapshot(source / row['relative_path']) for row in rows} == original_files
    assert file_snapshot(manifest) == original_manifest
    assert sorted(path.relative_to(source).as_posix() for path in source.rglob('*')) == original_members


def test_manifest_cli_refuses_wrong_source_pin_before_outer_output_claim(tmp_path, monkeypatch):
    manifest_measurement()
    source, manifest, rows, _ = tiny_manifest(tmp_path)
    rows[0]['sha256'] = 'f' * 64
    write_photo_manifest(manifest, source, rows)
    original_files = {row['relative_path']: file_snapshot(source / row['relative_path']) for row in rows}
    original_manifest = file_snapshot(manifest)
    output = tmp_path / 'refused-cli-wrong-pin'
    monkeypatch.setattr(measurement.sys, 'argv', ['scale_and_soak.py', '--output', str(output), '--photo-manifest', str(manifest)])
    with pytest.raises(ValueError):
        measurement.main()
    assert not output.exists(), 'Failed CLI source admission must not claim a successful output namespace'
    assert {row['relative_path']: file_snapshot(source / row['relative_path']) for row in rows} == original_files
    assert file_snapshot(manifest) == original_manifest
