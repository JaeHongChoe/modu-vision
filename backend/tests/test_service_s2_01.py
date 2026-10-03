"""S2-01: the first-run guide's state, the synthetic example dataset, the example marker and its refusal at approval.

The example is drawn on this computer from a seed (nothing downloaded); a folder a user changed is never overwritten;
only a project made from the example data is marked, and an example project never authorizes an approval or release.
"""
import json
from pathlib import Path

import pytest


def test_the_example_dataset_is_drawn_once_reused_and_never_overwritten_when_changed(tmp_path):
    from backend.engine import onboarding
    from backend.engine.demo_dataset import DEMO_CLASSES, DEMO_SPLITS
    first = onboarding.ensure_example_dataset(tmp_path)
    folder = Path(first['folder'])
    assert first['created'] and folder.name == 'surface-scratch-classification-v1'
    images = sorted(path.relative_to(folder).as_posix() for path in folder.rglob('*.png'))
    assert len(images) == sum(DEMO_SPLITS.values()) * len(DEMO_CLASSES) == 64
    assert {name.split('/')[1] for name in images} == set(DEMO_CLASSES) and not list(folder.rglob('*.json')), 'no label file in the data'
    again = onboarding.ensure_example_dataset(tmp_path)
    assert again == {**first, 'created': False}, 'the same pixels are reused'
    # A user changed one image: that folder is left as it is and a fresh one is drawn beside it.
    changed = folder / images[0]
    from PIL import Image
    Image.new('RGB', (64, 64), (0, 0, 0)).save(changed)
    kept = changed.read_bytes()
    fresh = onboarding.ensure_example_dataset(tmp_path)
    assert fresh['created'] and Path(fresh['folder']).name == 'surface-scratch-classification-v1-2'
    assert changed.read_bytes() == kept, 'nothing a user put there is overwritten'
    assert [row['pixel_sha256'] for row in fresh['manifest']['images']] == [row['pixel_sha256'] for row in first['manifest']['images']]


def test_the_guide_state_survives_and_an_unreadable_record_shows_the_guide_again(tmp_path):
    from backend.engine.onboarding import OnboardingStore
    store = OnboardingStore(tmp_path / 'onboarding.json')
    assert store.state() == {'version': 1, 'dismissed': False}
    store.dismiss()
    assert OnboardingStore(tmp_path / 'onboarding.json').state() == {'version': 1, 'dismissed': True}
    (tmp_path / 'onboarding.json').write_text('{"version": 1, "dismissed": "yes"}', encoding='utf-8')
    assert store.state() == {'version': 1, 'dismissed': False, 'record_error': 'unreadable'}


def test_only_the_example_folders_count_as_example_sources(tmp_path):
    from backend.engine.onboarding import is_example_source
    root = tmp_path / 'examples'
    assert is_example_source(str(root / 'surface-scratch-classification-v1'), root)
    assert is_example_source(str(root / 'surface-scratch-classification-v1-2'), root)
    assert not is_example_source(str(tmp_path / 'line3' / 'surface-scratch-classification-v1'), root), 'a look-alike elsewhere'
    assert not is_example_source(str(root / 'other-dataset'), root)
    assert not is_example_source(None, root)


def _app(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    import backend.main as main
    monkeypatch.setenv('VISION_AI_STUDIO_USER_DATA_DIR', str(tmp_path / 'user_data'))
    app = main.create_app(project_dir=str(tmp_path / 'projects'))
    return TestClient(app, headers={'X-Vision-Token': app.state.api_token})


def test_the_api_offers_the_example_and_marks_only_a_project_made_from_it(tmp_path, monkeypatch):
    api = _app(tmp_path, monkeypatch)
    state = api.get('/api/onboarding').json()
    assert state['dismissed'] is False and state['example']['images'] == 64 and state['example']['task'] == 'classification'
    assert api.post('/api/onboarding/dismiss', json={'dismissed': True}).json()['dismissed'] is True
    assert api.get('/api/onboarding').json()['dismissed'] is True
    made = api.post('/api/onboarding/example-dataset').json()
    assert made['images'] == 64 and made['synthetic'] is True
    assert Path(made['folder']).parent == (tmp_path / 'user_data' / 'examples').resolve()
    assert api.post('/api/project/create', json={'name': 'Line 3', 'task': 'classification'}).status_code == 200
    other = tmp_path / 'line3-data'
    (other / 'train' / 'OK').mkdir(parents=True)
    assert api.put('/api/project/update', json={'source_dataset_dir': str(other)}).status_code == 200
    refused = api.post('/api/onboarding/example-project')
    assert refused.status_code == 409 and '예제 데이터로 만든 프로젝트만' in refused.json()['detail']
    assert api.post('/api/project/create', json={'name': '예제', 'task': 'classification'}).status_code == 200
    assert api.put('/api/project/update', json={'source_dataset_dir': made['folder']}).status_code == 200
    marked = api.post('/api/onboarding/example-project')
    assert marked.status_code == 200, marked.text
    project = api.get('/api/project/current').json()
    assert project['example']['id'] == 'surface-scratch-classification' and project['example']['quality_approval'] == 'not_applicable'
    saved = json.loads((Path(project['project_dir']) / 'project.json').read_text(encoding='utf-8'))
    assert saved['example']['synthetic'] is True, 'the marker is in project.json, so backups and restores keep it'
    listed = api.get('/api/onboarding').json()['example_projects']
    assert [row['name'] for row in listed] == ['예제'], 'the guide offers the example made before, not the other project'
    # A later source change keeps the marker: an example project stays an example.
    assert api.put('/api/project/update', json={'description': 'notes'}).json()['example']['id'] == 'surface-scratch-classification'


def test_an_example_project_never_authorizes_an_approval_or_a_release(tmp_path, monkeypatch):
    from backend.engine.release_eligibility import ExampleProjectRefused, release_authority, verify_project_context
    api = _app(tmp_path, monkeypatch)
    made = api.post('/api/onboarding/example-dataset').json()
    assert api.post('/api/project/create', json={'name': '예제', 'task': 'classification'}).status_code == 200
    assert api.put('/api/project/update', json={'source_dataset_dir': made['folder']}).status_code == 200
    project = api.post('/api/onboarding/example-project').json()
    with pytest.raises(ExampleProjectRefused, match='품질 승인·배포 대상이 아닙니다'):
        verify_project_context(project)
    with pytest.raises(ExampleProjectRefused):
        with release_authority(project):
            pass
    approval = api.post('/api/model-deployments/approve', json={
        'comparison_id': 'c1', 'source_dataset_path': made['folder'], 'task': 'classification',
        'reviewer': 'QA lead', 'reason': 'an approval attempt on the example', 'holdout_reviewed': True})
    assert approval.status_code == 409 and '예제 프로젝트(합성 데이터)는 품질 승인·배포 대상이 아닙니다' in approval.json()['detail'], approval.text


def test_the_example_is_the_same_on_every_computer_and_its_defects_are_real(tmp_path):
    import hashlib
    from PIL import Image
    from backend.engine import onboarding
    made = onboarding.ensure_example_dataset(tmp_path)
    rows = made['manifest']['images']
    digest = hashlib.sha256(''.join(row['pixel_sha256'] for row in rows).encode()).hexdigest()
    assert digest == 'a3fe9388e312450116beabd399e772b7bc12854450f7c0a037586f4b80596090', 'the seed draws the same pixels everywhere'
    assert {(split, label): sum(row['split'] == split and row['label'] == label for row in rows)
            for split in ('train', 'val', 'test') for label in ('OK', 'NG')} == {
        ('train', 'OK'): 20, ('train', 'NG'): 20, ('val', 'OK'): 6, ('val', 'NG'): 6, ('test', 'OK'): 6, ('test', 'NG'): 6}
    # Every NG image carries a dark mark the clean surface never has (a local signal, not a brightness shift).
    darkest = {row['label']: [] for row in rows}
    for row in rows:
        with Image.open(Path(made['folder']) / row['path']) as image:
            darkest[row['label']].append(min(image.convert('L').getdata()))
    assert max(darkest['NG']) < 70 < min(darkest['OK'])


def test_an_unmarked_project_made_from_the_example_data_is_still_refused(tmp_path, monkeypatch):
    from backend.engine.release_eligibility import ExampleProjectRefused, verify_project_context
    api = _app(tmp_path, monkeypatch)
    made = api.post('/api/onboarding/example-dataset').json()
    assert api.post('/api/project/create', json={'name': '예제', 'task': 'classification'}).status_code == 200
    project = api.put('/api/project/update', json={'source_dataset_dir': made['folder']}).json()
    assert 'example' not in project or not project['example'], 'the marker request never ran (an import that stopped)'
    with pytest.raises(ExampleProjectRefused):
        verify_project_context(project)
    approval = api.post('/api/model-deployments/approve', json={
        'comparison_id': 'c1', 'source_dataset_path': made['folder'], 'task': 'classification',
        'reviewer': 'QA lead', 'reason': 'an approval attempt on the example', 'holdout_reviewed': True})
    assert approval.status_code == 409, approval.text
    # Marked, then pointed at other data: the marker stays, and either copy carrying it is enough (the caller's cached
    # project or the saved one).
    assert api.post('/api/onboarding/example-project').status_code == 200
    other = tmp_path / 'line3-data'
    (other / 'train' / 'OK').mkdir(parents=True)
    moved = api.put('/api/project/update', json={'source_dataset_dir': str(other)}).json()
    assert moved['example'] and Path(moved['source_dataset_dir']) == other.resolve()
    with pytest.raises(ExampleProjectRefused):
        verify_project_context({**moved, 'example': None})
    project_file = Path(moved['project_dir']) / 'project.json'
    saved = json.loads(project_file.read_text(encoding='utf-8'))
    saved.pop('example')
    project_file.write_text(json.dumps(saved), encoding='utf-8')
    with pytest.raises(ExampleProjectRefused):
        verify_project_context(moved)
    verify_project_context({**moved, 'example': None})  # neither copy marked and real data: allowed


def test_the_example_folder_survives_file_browsers_bad_manifests_and_links(tmp_path):
    from backend.engine import onboarding
    first = onboarding.ensure_example_dataset(tmp_path)
    folder = Path(first['folder'])
    (folder / '.DS_Store').write_bytes(b'finder')
    (folder / 'train' / 'Thumbs.db').write_bytes(b'explorer')
    assert onboarding.ensure_example_dataset(tmp_path)['folder'] == first['folder'], 'a file browser is not a user change'
    (tmp_path / 'surface-scratch-classification-v1.manifest.json').write_text('{"images": 3}', encoding='utf-8')
    second = onboarding.ensure_example_dataset(tmp_path)
    assert Path(second['folder']).name == 'surface-scratch-classification-v1-2', 'a manifest of another shape matches nothing'
    (tmp_path / 'surface-scratch-classification-v1-3').symlink_to(tmp_path / 'nowhere', target_is_directory=True)
    (tmp_path / 'surface-scratch-classification-v1-2.manifest.json').write_text('[]', encoding='utf-8')
    third = onboarding.ensure_example_dataset(tmp_path)
    assert Path(third['folder']).name == 'surface-scratch-classification-v1-4', 'a link is never followed or replaced'
    assert (tmp_path / 'surface-scratch-classification-v1-3').is_symlink()


def test_only_the_apps_example_folder_names_count(tmp_path):
    import os
    from backend.engine.onboarding import is_example_source
    root = tmp_path / 'examples'
    for name in ('surface-scratch-classification-v1-mine', 'surface-scratch-classification-v10', 'surface-scratch-classification-v1-0',
                 'surface-scratch-classification-v1-100'):
        assert not is_example_source(str(root / name), root), name
    assert is_example_source(str(root / 'surface-scratch-classification-v1-99'), root)
    if os.name == 'nt':  # Windows compares paths without letter case
        assert is_example_source(str(root / 'SURFACE-SCRATCH-CLASSIFICATION-V1').upper(), root)


def test_the_guide_record_keeps_what_was_asked_and_a_broken_record_is_not_dismissed(tmp_path):
    from backend.engine.onboarding import OnboardingStore
    store = OnboardingStore(tmp_path / 'onboarding.json')
    store.dismiss(True)
    assert store.dismiss(False) == {'version': 1, 'dismissed': False} and store.state()['dismissed'] is False
    for broken in ('{', '[]', '{"version": 2, "dismissed": true}', '{"version": 1, "dismissed": "true"}'):
        (tmp_path / 'onboarding.json').write_text(broken, encoding='utf-8')
        assert store.state()['dismissed'] is False, broken


def test_a_team_server_keeps_no_guide_record_and_makes_no_example(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from fastapi import HTTPException
    from backend.api import routes_onboarding
    monkeypatch.setenv('VISION_AI_STUDIO_USER_DATA_DIR', str(tmp_path / 'user_data'))
    team = SimpleNamespace(state=SimpleNamespace(account_user={'id': 'u1'}))
    state = routes_onboarding.onboarding_state(team)
    assert state['team'] is True and state['dismissed'] is True and state['example_projects'] == []
    assert routes_onboarding.dismiss(routes_onboarding.DismissRequest(dismissed=True), team) == {'version': 1, 'dismissed': True}
    assert not (tmp_path / 'user_data' / 'onboarding.json').exists(), 'nothing written on the shared server'
    for call in (lambda: routes_onboarding.example_dataset(team), lambda: routes_onboarding.mark_example_project(team)):
        with pytest.raises(HTTPException) as refused:
            call()
        assert refused.value.status_code == 409 and '개인 모드' in refused.value.detail
    assert not (tmp_path / 'user_data' / 'examples').exists()


def test_a_broken_project_history_lists_no_example_and_hides_no_guide(monkeypatch):
    from types import SimpleNamespace
    from backend.api import routes_onboarding, routes_project

    def broken(request):
        raise OSError('history unreadable')
    monkeypatch.setattr(routes_project, 'list_projects', broken)
    assert routes_onboarding._example_projects(SimpleNamespace(state=SimpleNamespace())) == []


def test_files_the_importer_reads_and_added_folders_are_changes_and_a_huge_image_is_never_decoded(tmp_path):
    """Review of freeze 2: an AppleDouble '._' image and an empty class folder are read by the importer, so the folder
    is no longer the example; an image declaring a huge size is refused from its header (no 500 on every call)."""
    from PIL import Image
    from backend.engine import onboarding
    first = Path(onboarding.ensure_example_dataset(tmp_path)['folder'])
    (first / 'train' / 'OK' / '._ok_train_00.png').write_bytes(b'\x00\x05\x16\x07 resource fork')
    second = Path(onboarding.ensure_example_dataset(tmp_path)['folder'])
    assert second.name.endswith('-v1-2') and (first / 'train' / 'OK' / '._ok_train_00.png').exists(), 'left as it is'
    (second / 'train' / 'EXTRA').mkdir()
    third = Path(onboarding.ensure_example_dataset(tmp_path)['folder'])
    assert third.name.endswith('-v1-3'), 'an empty folder would become an empty class'
    bomb = third / 'test' / 'NG' / 'ng_test_00.png'
    Image.new('1', (14_000, 14_000)).save(bomb)  # 196 M pixels declared in a small file
    fourth = onboarding.ensure_example_dataset(tmp_path)
    assert Path(fourth['folder']).name.endswith('-v1-4') and fourth['created'] is True
    assert onboarding._pixel_sha256(bomb) is None


def test_an_example_folder_reached_under_another_letter_case_is_still_the_example(tmp_path):
    from backend.engine import onboarding
    root = tmp_path / 'examples'
    folder = Path(onboarding.ensure_example_dataset(root)['folder'])
    other = folder.parent / folder.name.upper()
    if not other.exists():
        pytest.skip('this file system tells letter cases apart: the other spelling is another folder')
    assert onboarding.is_example_source(str(other), root)
    assert not onboarding.is_example_source(str(tmp_path / 'elsewhere' / folder.name.upper()), root)


def test_a_record_place_that_is_not_a_file_is_refused_with_its_reason(tmp_path, monkeypatch):
    # s201s3 review P3: a folder where the guide's record or an example's manifest belongs gave a 500.
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from backend.api import routes_onboarding
    from backend.engine import onboarding
    (tmp_path / 'onboarding.json').mkdir()
    store = onboarding.OnboardingStore(tmp_path / 'onboarding.json')
    with pytest.raises(onboarding.OnboardingRecordError, match='파일이 아니어서'):
        store.dismiss(True)
    assert store.state()['record_error'] == 'unreadable'
    monkeypatch.setattr(routes_onboarding, '_store', lambda: store)
    app = FastAPI()
    app.include_router(routes_onboarding.router)
    answer = TestClient(app).post('/api/onboarding/dismiss', json={'dismissed': True})
    assert answer.status_code == 409 and '파일이 아니어서' in answer.json()['detail']
    root = tmp_path / 'examples'
    first = onboarding._example_folder_names()[0]
    (root / f'{first}.manifest.json').mkdir(parents=True)
    made = onboarding.ensure_example_dataset(root)
    assert Path(made['folder']).name != first and made['created'] is True
    assert not (root / first).exists(), 'nothing is drawn under a name whose record cannot be written'


def test_a_link_placed_under_an_example_name_is_not_the_example(tmp_path):
    # s201s3 review P3: the letter-case fallback compared files and followed a hand-placed link.
    import os
    from backend.engine import onboarding
    root = tmp_path / 'examples'
    real = tmp_path / 'my-data'
    real.mkdir()
    root.mkdir()
    name = onboarding._example_folder_names()[0]
    try:
        os.symlink(real, root / name, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip('this system cannot make a folder link here')
    assert not onboarding.is_example_source(str(real), root)
    assert not onboarding.is_example_source(str(real).upper(), root)
