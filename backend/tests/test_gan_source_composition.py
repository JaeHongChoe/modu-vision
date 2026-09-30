"""Real GAN pixels composited only inside explicitly selected original ROIs."""
import json
from hashlib import sha256
from pathlib import Path

import numpy as np
from PIL import Image
import pytest

from backend.engine.defect_gan import train_defect_gan, write_defect_gan_manifest
from backend.tests.test_defect_gan import _source


@pytest.fixture
def generator(tmp_path):
    source, rows = _source(tmp_path)
    write_defect_gan_manifest(source, rows)
    result = train_defect_gan(source, tmp_path / 'model', epochs=1, batch_size=2, seed=9, base_channels=8)
    return Path(result['checkpoint_path'])


def test_real_generator_composes_two_native_regions_reproducibly(generator, tmp_path):
    from backend.engine.defect_gan import generate_composited_candidates
    source = tmp_path / 'original.png'
    pixels = np.full((96, 128, 3), 220, np.uint8); Image.fromarray(pixels).save(source)
    original_hash = sha256(source.read_bytes()).hexdigest()
    regions = [{'id': 'left', 'bbox': [8, 12, 40, 44], 'opacity': .8, 'feather_px': 3},
               {'id': 'right', 'bbox': [80, 52, 116, 84], 'mask_polygon': [[80, 52], [116, 52], [98, 84]]}]
    review = generate_composited_candidates(generator, source, tmp_path / 'review', regions=regions, count=2, seed=41)
    repeated = generate_composited_candidates(generator, source, tmp_path / 'repeat', regions=regions, count=2, seed=41)
    assert review['source_image_sha256'] == original_hash
    assert review['seed'] == 41
    assert review['quality_status'] == 'unvalidated'
    for candidate, other in zip(review['candidates'], repeated['candidates']):
        output = np.asarray(Image.open(candidate['path']))
        assert output.shape == pixels.shape
        assert np.array_equal(output[:10], pixels[:10])
        assert np.array_equal(output[:, 45:75], pixels[:, 45:75])
        assert not np.array_equal(output[16:40, 12:36], pixels[16:40, 12:36])
        assert candidate['sha256'] == other['sha256']
        assert candidate['status'] == 'synthetic_unreviewed'
        assert len(candidate['composition_regions']) == 2
        assert all(row['generated_patch_sha256'] and row['blend_mask_sha256'] for row in candidate['composition_regions'])
    assert sha256(source.read_bytes()).hexdigest() == original_hash
    assert json.loads((tmp_path / 'review/review_manifest.json').read_text())['source_image_sha256'] == original_hash


def test_changed_source_or_invalid_region_is_rejected_before_generation(generator, tmp_path):
    from backend.engine.defect_gan import generate_composited_candidates
    source = tmp_path / 'original.png'; Image.new('RGB', (32, 32), 'gray').save(source)
    digest = sha256(source.read_bytes()).hexdigest()
    Image.new('RGB', (32, 32), 'white').save(source)
    with pytest.raises(ValueError, match='source.*changed|hash'):
        generate_composited_candidates(generator, source, tmp_path / 'review', regions=[{'id': 'a', 'bbox': [0, 0, 16, 16]}], source_sha256=digest)
    with pytest.raises(ValueError, match='bounds|outside'):
        generate_composited_candidates(generator, source, tmp_path / 'other', regions=[{'id': 'a', 'bbox': [0, 0, 50, 16]}])
    assert not (tmp_path / 'review').exists()


def test_portable_generator_composition_cli_matches_backend(generator, tmp_path):
    import os
    import subprocess
    import sys
    from backend.engine.defect_gan import generate_composited_candidates
    from backend.engine.gan_package_runtime import build_generator_package
    source = tmp_path / 'source.png'; Image.new('RGB', (80, 64), 'white').save(source)
    regions = [{'id': 'part', 'bbox': [10, 10, 50, 50], 'opacity': .7, 'feather_px': 4}]
    region_file = tmp_path / 'regions.json'; region_file.write_text(json.dumps(regions))
    expected = generate_composited_candidates(generator, source, tmp_path / 'reference', regions=regions, count=1, seed=31)
    package = build_generator_package(generator, tmp_path / 'package')
    output = tmp_path / 'standalone'
    process = subprocess.run([sys.executable, str(package / 'generate.py'), '--source-image', str(source),
        '--regions', str(region_file), '--output', str(output), '--count', '1', '--seed', '31'],
        env={**os.environ, 'PYTHONPATH': ''}, cwd=tmp_path, capture_output=True, text=True, timeout=60)
    assert process.returncode == 0, process.stderr
    result = json.loads(process.stdout)
    assert result['candidates'][0]['sha256'] == expected['candidates'][0]['sha256']
    assert result['candidates'][0]['composition_regions'] == expected['candidates'][0]['composition_regions']
    assert result['source_image_sha256'] == expected['source_image_sha256']


def test_composition_api_reopens_regions_and_rejects_changed_source(generator, tmp_path):
    import shutil
    from backend.main import create_app
    from fastapi.testclient import TestClient
    app = create_app(project_dir=str(tmp_path / 'projects'))
    api = TestClient(app, headers={'X-Vision-Token': app.state.api_token})
    project = api.post('/api/project/create', json={'name': 'Composition', 'task': 'classification'}).json()
    job = 'a' * 32
    model = Path(project['models_dir']) / 'defect_gan' / job
    shutil.copytree(generator.parent, model)
    source = tmp_path / 'original.png'; Image.new('RGB', (80, 64), 'gray').save(source)
    request = {'job_id': job, 'source_image_path': str(source), 'source_sha256': sha256(source.read_bytes()).hexdigest(),
               'regions': [{'id': 'part', 'bbox': [8, 8, 40, 40]}], 'count': 1, 'seed': 3}
    generated = api.post('/api/defect-gan/generate', json=request)
    assert generated.status_code == 200, generated.text
    review_id = Path(generated.json()['review_dir']).name
    reopened = api.get(f'/api/defect-gan/reviews/{job}/{review_id}')
    assert reopened.status_code == 200, reopened.text
    assert reopened.json()['regions'][0]['bbox'] == [8, 8, 40, 40]
    assert reopened.json()['candidates'][0]['sha256'] == generated.json()['candidates'][0]['sha256']
    Image.new('RGB', (80, 64), 'white').save(source)
    assert api.get(f'/api/defect-gan/reviews/{job}/{review_id}').status_code == 422


def test_owned_gan_preparation_train_and_evaluate_bind_original_source(tmp_path):
    from backend.main import create_app
    from fastapi.testclient import TestClient
    app=create_app(project_dir=str(tmp_path/'projects'))
    api=TestClient(app,headers={'X-Vision-Token':app.state.api_token})
    project=api.post('/api/project/create',json={'name':'Owned GAN','task':'classification'}).json()
    source,rows=_source(tmp_path)
    rows[2]['split']='val';rows[3]['split']='test'
    rows=[{**row,'label':'scratch'} for row in rows]
    before={path.name:sha256(path.read_bytes()).hexdigest() for path in source.iterdir()}
    api.put('/api/project/update',json={'source_dataset_dir':str(source)})
    prepared=api.post('/api/defect-gan/prepare',json={'source_dataset_path':str(source),'samples':rows})
    assert prepared.status_code==200,prepared.text
    dataset=Path(prepared.json()['dataset_path'])
    assert dataset.is_relative_to(Path(project['dataset_dir']))
    assert not (source/'defect_gan.json').exists()
    discovered = api.get('/api/defect-gan/datasets')
    assert discovered.status_code == 200, discovered.text
    assert discovered.json()['datasets'][0]['dataset_path'] == str(dataset)
    preview = api.get('/api/defect-gan/source-preview', params={'image_path': str(source / rows[0]['image'])})
    assert preview.status_code == 200, preview.text
    assert preview.json()['source_size'] == [64, 64]
    assert preview.json()['source_sha256'] == before[rows[0]['image']]
    trained=api.post('/api/defect-gan/train',json={'dataset_path':str(dataset),'epochs':1,'batch_size':2,'base_channels':8})
    assert trained.status_code==200,trained.text
    job=trained.json()['job_id']
    status=api.get(f'/api/defect-gan/jobs/{job}').json()
    assert status['source_dataset_path']==str(source)
    assert status['dataset_path']==str(dataset)
    parents=api.get('/api/defect-gan/warm-start-parents',params={'dataset_path':str(dataset),'base_channels':8})
    assert parents.status_code==200,parents.text
    assert parents.json()['total']==1
    evaluated=api.post('/api/defect-gan/evaluate',json={'job_id':job,'dataset_path':str(dataset),'count':2})
    assert evaluated.status_code==200,evaluated.text
    assert evaluated.json()['binding']['source_dataset_path']==str(source)
    assert {path.name:sha256(path.read_bytes()).hexdigest() for path in source.iterdir()}==before
