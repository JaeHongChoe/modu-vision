"""The built-in assistant's project-local run log and its `via: assistant` attribution (§4.6).

The log keeps names, counts and hashes only: a path, URL, e-mail address or key is refused, never stored. Clearing moves
the log into the archive folder. A backend write the person confirmed in the assistant carries X-Modu-Assistant-Run; the
image metadata ledger and the job ledger record `via: "assistant"` with that run id while the actor stays the person.
"""
import json
from pathlib import Path

import pytest
from PIL import Image

RUN = 'run_' + 'a1' * 12


def _setup(tmp_path):
    from backend.main import create_app
    from fastapi.testclient import TestClient
    app = create_app(project_dir=str(tmp_path / 'projects'))
    client = TestClient(app, headers={'X-Vision-Token': app.state.api_token})
    assert client.post('/api/project/create', json={'name': 'Assistant', 'task': 'classification'}).status_code == 200
    source = tmp_path / 'source'
    for cls, shade in (('OK', 0), ('NG', 200)):
        (source / cls).mkdir(parents=True)
        for i in range(3):
            Image.new('RGB', (16, 16), (shade, i * 9, 0)).save(source / cls / f'{cls.lower()}_{i:02d}.png')
    assert client.put('/api/project/update', json={'source_dataset_dir': str(source)}).status_code == 200
    assert client.post('/api/dataset/import', json={'folder_path': str(source), 'task': 'classification',
                                                    'validate_images': True}).status_code == 200
    project = client.get('/api/project/current').json()
    return client, source, Path(project['project_dir'])


def _entry(**changes):
    return {'run_id': RUN, 'tool': 'save_split', 'tier': 'write', 'summary': '학습 70% / 검증 20% / 시험 10%',
            'args_sha256': 'b' * 64, 'confirmed': True, 'result': 'ok', 'provider': 'anthropic', **changes}


def test_the_log_appends_lists_and_archives_without_deleting(tmp_path):
    client, _, project_dir = _setup(tmp_path)
    assert client.get('/api/assistant/runs').json() == {'runs': [], 'total': 0, 'archives': 0}
    first = client.post('/api/assistant/runs', json=_entry())
    assert first.status_code == 200, first.text
    second = client.post('/api/assistant/runs', json=_entry(tool='read_status', tier='read', confirmed=False,
                                                            summary='', result='ok', link='class_changed'))
    assert second.status_code == 200, second.text
    listed = client.get('/api/assistant/runs').json()
    assert [row['tool'] for row in listed['runs']] == ['save_split', 'read_status'] and listed['total'] == 2
    assert listed['runs'][0]['actor'] == 'local' and listed['runs'][0]['schema_version'] == 1
    log = project_dir / 'assistant' / 'runs.jsonl'
    lines = log.read_text(encoding='utf-8').splitlines()
    assert [json.loads(line)['run_id'] for line in lines] == [RUN, RUN]
    if hasattr(log.stat(), 'st_mode') and __import__('os').name == 'posix':
        assert log.stat().st_mode & 0o077 == 0, 'the log is owner-only on POSIX'

    archived = client.post('/api/assistant/runs/archive').json()
    assert archived['count'] == 2 and archived['archived'].startswith('archive/runs-')
    assert not log.exists(), 'clearing moves the log away'
    moved = project_dir / 'assistant' / archived['archived']
    assert moved.read_text(encoding='utf-8').splitlines() == lines, 'the archived copy is the whole log, unchanged'
    assert client.get('/api/assistant/runs').json() == {'runs': [], 'total': 0, 'archives': 1}
    assert client.post('/api/assistant/runs/archive').json() == {'archived': None, 'count': 0}


@pytest.mark.parametrize('summary', ['/Users/someone/data/ok_00.png 분류 변경', 'C:\\Users\\kim\\image.png',
                                     '서버 https://team.example.com 에서', '연락 kim@example.com',
                                     'sk-test-0123456789abcdefghij', 'Bearer abcdefghijklmnop',
                                     'x' * 301, '두 줄\n요약'])
def test_paths_addresses_and_keys_never_reach_the_log(tmp_path, summary):
    client, _, project_dir = _setup(tmp_path)
    response = client.post('/api/assistant/runs', json=_entry(summary=summary))
    assert response.status_code == 422, response.text
    assert not (project_dir / 'assistant' / 'runs.jsonl').exists()


def test_unknown_fields_and_malformed_ids_are_refused(tmp_path):
    client, _, _ = _setup(tmp_path)
    for body in (_entry(api_key='placeholder'), _entry(run_id='run_short'), _entry(tool='Save Split'),
                 _entry(tier='admin'), _entry(confirmed='yes'), _entry(provider='https://api.example.com')):
        assert client.post('/api/assistant/runs', json=body).status_code == 422, body


def test_a_confirmed_assistant_write_is_attributed_in_the_image_ledger(tmp_path):
    client, source, _ = _setup(tmp_path)
    image = source / 'OK' / 'ok_00.png'
    direct = client.put('/api/dataset/metadata/class-override', json={'image_path': str(image), 'class_name': 'NG', 'actor': 'Kim'})
    assert direct.status_code == 200, direct.text
    event = direct.json()['metadata']['audit'][-1]
    assert event['action'] == 'class_changed' and 'via' not in event and 'assistant_run' not in event

    assisted = client.put('/api/dataset/metadata/class-override', headers={'X-Modu-Assistant-Run': RUN},
                          json={'image_path': str(image), 'class_name': 'OK', 'actor': 'Kim'})
    assert assisted.status_code == 200, assisted.text
    event = assisted.json()['metadata']['audit'][-1]
    assert (event['action'], event['actor'], event['via'], event['assistant_run']) == ('class_changed', 'Kim', 'assistant', RUN)

    forged = client.put('/api/dataset/metadata/class-override', headers={'X-Modu-Assistant-Run': 'run_<script>'},
                        json={'image_path': str(image), 'class_name': 'NG', 'actor': 'Kim'})
    assert forged.status_code == 200, 'a malformed run id is ignored, never a failed write'
    assert 'via' not in forged.json()['metadata']['audit'][-1]
    from backend.engine import assistant_run
    assert assistant_run.current_run() is None, 'the binding ends with the request'


def test_the_job_ledger_names_the_run_only_while_it_is_bound(tmp_path):
    from backend.engine import assistant_run
    from backend.engine.job_store import JobStore
    store = JobStore(tmp_path / 'jobs.sqlite')
    context = {'workspace_id': 'workspace', 'project_id': 'project', 'actor_id': 'person', 'mode': 'local'}
    plain = store.submit(context, 'project-key', 'training', {'epochs': 1})
    with assistant_run.bound(RUN):
        assisted = store.submit(context, 'project-key', 'training', {'epochs': 2})
    with assistant_run.bound('not-a-run'):
        ignored = store.submit(context, 'project-key', 'training', {'epochs': 3})
    assert 'via' not in store.events(plain.id)[0]['payload']
    payload = store.events(assisted.id)[0]['payload']
    assert payload['via'] == 'assistant' and payload['assistant_run'] == RUN and payload['parent_verified'] is False
    assert 'via' not in store.events(ignored.id)[0]['payload']
    assert assistant_run.marks() == {}
