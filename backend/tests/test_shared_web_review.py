"""Shared-server regressions from the web-version review: file reads, GET work, report markup and server surface."""
import hashlib
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import quote

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image

PASSWORD = 'long password 123'


def _login(app, username):
    client = TestClient(app)
    response = client.post('/api/accounts/login', json={'username': username, 'password': PASSWORD})
    assert response.status_code == 200, response.text
    client.headers['Authorization'] = 'Bearer ' + response.json()['token']
    return client


def _team(tmp_path, *roles):
    """A shared server whose administrator created one project and granted each role to its own account."""
    from backend.main import create_app
    app = create_app(project_dir=str(tmp_path/'projects'), shared_auth_dir=str(tmp_path/'accounts'))
    desktop = TestClient(app, headers={'X-Vision-Token': app.state.api_token})
    assert desktop.post('/api/accounts/bootstrap', json={'username': 'admin', 'password': PASSWORD}).status_code == 200
    admin = _login(app, 'admin')
    project = admin.post('/api/project/create', json={'name': 'Web review'}).json()
    clients = {}
    for role in roles:
        member = admin.post('/api/accounts/users', json={'username': role, 'password': PASSWORD, 'administrator': False}).json()
        assert admin.put(f"/api/accounts/projects/{project['id']}/members", json={'user_id': member['id'], 'role': role}).status_code == 200
        clients[role] = _login(app, role)
        assert clients[role].post('/api/accounts/select-project', json={'project_id': project['id']}).status_code == 200
    return app, admin, project, clients


def _image(path, color='white'):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new('RGB', (8, 8), color).save(path)
    return path


def test_shared_viewer_image_reads_never_leave_the_project(tmp_path, monkeypatch):
    _, _, project, clients = _team(tmp_path, 'viewer')
    viewer = clients['viewer']
    root = Path(project['project_dir'])
    inside = _image(root/'inside.png')
    secret = _image(tmp_path/'server-secret.png', 'red')
    accounts = tmp_path/'accounts'/'accounts.sqlite'
    for route in ('raw', 'thumbnail'):
        assert viewer.get(f'/api/dataset/{route}/inside.png', params={'file_path': str(inside)}).status_code == 200
        # The authorized selector names the project folder itself; the path segment names a server file.
        for target in (secret, accounts):
            response = viewer.get(f'/api/dataset/{route}/' + quote(str(target), safe=''), params={'file_path': str(root)})
            assert response.status_code in (403, 404), (route, target.name, response.status_code)
    # A missing in-project selector must not fall back to a name search of the server's working folder.
    monkeypatch.chdir(tmp_path)
    for route in ('raw', 'thumbnail'):
        response = viewer.get(f'/api/dataset/{route}/accounts.sqlite', params={'file_path': str(root/'missing.png')})
        assert response.status_code in (403, 404), (route, response.status_code)
        response = viewer.get(f'/api/dataset/{route}/server-secret.png', params={'file_path': str(root/'missing.png')})
        assert response.status_code in (403, 404), (route, response.status_code)


def test_shared_raw_image_selector_cannot_follow_a_link_out_of_the_project(tmp_path):
    _, _, project, clients = _team(tmp_path, 'viewer')
    root = Path(project['project_dir'])
    secret = _image(tmp_path/'server-secret.png', 'red')
    link = root/'linked.png'
    try:
        link.symlink_to(secret)
    except OSError:
        pytest.skip('this account cannot create symbolic links')
    response = clients['viewer'].get('/api/dataset/raw/linked.png', params={'file_path': str(link)})
    assert response.status_code == 403


def test_local_desktop_raw_image_lookup_is_unchanged(tmp_path):
    from backend.main import create_app
    app = create_app(project_dir=str(tmp_path/'projects'))
    desktop = TestClient(app, headers={'X-Vision-Token': app.state.api_token})
    image = _image(tmp_path/'elsewhere'/'picked.png')
    # The single-user app keeps reading any file its own user picks, by selector or by path segment.
    assert desktop.get('/api/dataset/raw/picked.png', params={'file_path': str(image)}).status_code == 200
    assert desktop.get('/api/dataset/raw/' + quote(str(image), safe='')).status_code == 200
    assert desktop.get('/api/dataset/thumbnail/' + quote(str(image), safe='')).status_code == 200


def test_shared_viewer_heatmap_infers_only_project_images(tmp_path, monkeypatch):
    from backend.api import routes_evaluation
    from backend.engine import score_contract
    from backend.remote import operations
    _, _, project, clients = _team(tmp_path, 'viewer')
    viewer = clients['viewer']
    root = Path(project['project_dir'])
    model = root/'models'/'job_review'/'best_model.pt'
    model.parent.mkdir(parents=True)
    model.write_bytes(b'model')
    monkeypatch.setattr(routes_evaluation, '_find_model_file', lambda job_id: model)
    monkeypatch.setattr(score_contract, 'resolve_inference_score', lambda *args: (0.5, None))
    monkeypatch.setattr(operations, 'remote_job_context', lambda *args: None)
    seen = []

    def infer(**options):
        seen.append(Path(options['image_input']))
        return SimpleNamespace(visual_overlay=np.zeros((4, 4, 3), np.uint8), confidence_score=.5, predictions=[], latency_ms=1.0)
    monkeypatch.setattr(routes_evaluation, 'infer', infer)
    inside = _image(root/'inside.png')
    secret = _image(tmp_path/'server-secret.png', 'red')
    assert viewer.get('/api/evaluation/heatmap/inside', params={'job_id': 'job_review', 'file_path': str(inside)}).status_code == 200
    response = viewer.get('/api/evaluation/heatmap/' + quote(str(secret), safe=''), params={'job_id': 'job_review', 'file_path': str(root)})
    assert response.status_code in (403, 404)
    monkeypatch.chdir(tmp_path)
    assert viewer.get('/api/evaluation/heatmap/server-secret', params={'job_id': 'job_review'}).status_code in (403, 404)
    assert seen == [inside]


def test_shared_viewer_get_cannot_run_evaluation(tmp_path, monkeypatch):
    from backend.api import routes_evaluation
    _, _, _, clients = _team(tmp_path, 'viewer', 'trainer')
    calls = []

    def evaluate(**options):
        calls.append((options.get('force_recompute', False), options.get('compute_allowed', True)))
        return {'job_id': 'job_review', 'metrics': {}}
    monkeypatch.setattr(routes_evaluation, 'run_or_load_evaluation', evaluate)
    forced = {'job_id': 'job_review', 'force_recompute': 'true'}
    assert clients['viewer'].get('/api/evaluation/results', params=forced).status_code == 403
    common = {'job_id': 'job_review', 'evaluation_dataset_version_id': 'v1', 'compute_profile_id': 'server', 'device': 'cpu'}
    assert clients['viewer'].get('/api/evaluation/results', params=common).status_code == 403
    assert calls == []
    # Reading stored results stays open to the viewer, but without the authority to compute missing ones.
    assert clients['viewer'].get('/api/evaluation/results', params={'job_id': 'job_review'}).status_code == 200
    assert calls == [(False, False)]
    clients['viewer'].get('/api/evaluation/overkill-underkill', params={'job_id': 'job_review'})
    assert calls == [(False, False), (False, False)]
    # The GET calibration rewrites the stored threshold that export reads.
    assert clients['viewer'].get('/api/evaluation/zero-escape-calibrate', params={'job_id': 'job_review'}).status_code == 403
    assert len(calls) == 2
    assert clients['trainer'].get('/api/evaluation/results', params=forced).status_code == 200
    assert calls[-1] == (True, True)


def test_shared_evaluation_folder_job_stays_inside_the_project(tmp_path, monkeypatch):
    import json
    from backend.engine import calibration_evidence
    _, admin, _, clients = _team(tmp_path, 'viewer', 'trainer')
    other = admin.post('/api/project/create', json={'name': 'Other team'}).json()
    foreign = Path(other['project_dir'])/'models'/'job_foreign'
    foreign.mkdir(parents=True)
    (foreign/'eval_results.json').write_text(json.dumps({'marker': 'other-project'}), encoding='utf-8')
    seen = []
    original = calibration_evidence.validate_calibration_evidence

    def spy(payload, version):
        seen.append(payload.get('marker'))
        return original(payload, version)
    monkeypatch.setattr(calibration_evidence, 'validate_calibration_evidence', spy)
    # The folder is another project's model output: its evaluation file must not even be opened.
    response = clients['viewer'].get('/api/evaluation/overkill-underkill', params={'job_id': str(foreign)})
    assert (seen, response.status_code) == ([], 403)
    response = clients['trainer'].post('/api/evaluation/zero-escape-calibrate', json={'job_id': str(foreign)})
    assert (seen, response.status_code) == ([], 403)


def test_load_only_evaluation_refuses_to_compute_missing_results(tmp_path, monkeypatch):
    from fastapi import HTTPException
    from backend.api import routes_evaluation
    output = tmp_path/'models'/'job_review'
    output.mkdir(parents=True)
    (output/'best_model.pt').write_bytes(b'model')
    monkeypatch.setattr(routes_evaluation, '_resolve_job_artifacts',
                        lambda **options: (output, output/'best_model.pt', {'task': 'classification'}, 'classification', 'job_review', tmp_path/'data'))
    monkeypatch.setattr(routes_evaluation, '_evaluation_class_semantics', lambda meta, task: {'roles': {}})
    from backend.remote import operations
    monkeypatch.setattr(operations, 'remote_job_context', lambda *args: None)
    from backend.api import routes_model_comparisons
    monkeypatch.setattr(routes_model_comparisons, '_fingerprint', lambda path: 'v1:data')
    monkeypatch.setattr(routes_model_comparisons, '_sha256', lambda path: 'model-digest')
    monkeypatch.setattr(routes_evaluation, '_evaluate_classification', lambda *args: pytest.fail('a load-only read evaluated the model'))
    with pytest.raises(HTTPException) as refused:
        routes_evaluation.run_or_load_evaluation(job_id='job_review', compute_allowed=False)
    assert refused.value.status_code == 403
    assert not (output/'eval_results.json').exists()


def test_report_markup_escapes_names_from_labels_and_results():
    from backend.api import routes_report
    hostile = '<img src=x onerror=alert(1)>'
    data = {'job_id': 'job_review', 'task': 'classification', 'metrics': {'accuracy': 1.0, '<b>_key': 2},
            'confusion_matrix': {'classes': [hostile, 'OK'], 'matrix': [[1, 0], [0, 1]]}, 'test_predictions': []}
    html = routes_report._render_standalone_html('job<review>', data, 'now', include_images=False)
    assert hostile not in html and '<B> Key' not in html and 'job<review>' not in html
    assert '&lt;img src=x onerror=alert(1)&gt; (Pred)' in html and '&lt;img src=x onerror=alert(1)&gt; (True)' in html


def test_report_markup_escapes_sample_captions(tmp_path, monkeypatch):
    from backend.api import routes_report
    image = _image(tmp_path/'sample.png')
    monkeypatch.setattr(routes_report, '_find_model_file', lambda job_id: None)
    hostile = '"><script>alert(1)</script>'
    data = {'task': 'classification', 'metrics': {}, 'confusion_matrix': {'classes': ['OK'], 'matrix': [[1]]},
            'test_predictions': [{'file_path': str(image), 'predicted_class': hostile, 'ground_truth': hostile, 'confidence': .5, 'is_correct': False}]}
    html = routes_report._render_standalone_html('job_review', data, 'now', include_images=True)
    assert '<script>' not in html and 'alt="Inspection Sample: &quot;&gt;&lt;script&gt;' in html


def test_report_view_cannot_run_scripts_stored_in_old_reports(tmp_path):
    _, _, project, clients = _team(tmp_path, 'viewer')
    reports = Path(project['reports_dir'])
    reports.mkdir(parents=True, exist_ok=True)
    (reports/'report_old.html').write_text('<html><body><script>alert(1)</script></body></html>', encoding='utf-8')
    response = clients['viewer'].get('/api/report/view/report_old')
    assert response.status_code == 200
    policy = response.headers.get('content-security-policy', '')
    assert 'sandbox' in policy and "default-src 'none'" in policy
    assert response.headers.get('x-content-type-options') == 'nosniff'


def test_shared_raw_image_is_never_stored_by_a_shared_cache(tmp_path):
    _, _, project, clients = _team(tmp_path, 'viewer')
    inside = _image(Path(project['project_dir'])/'inside.png')
    response = clients['viewer'].get('/api/dataset/raw/inside.png', params={'file_path': str(inside)})
    assert response.status_code == 200
    cache = response.headers['cache-control']
    assert 'public' not in cache and 'private' in cache


def test_shared_host_startup_registration_requires_the_administrator_owner(tmp_path, monkeypatch):
    from backend.engine.managed_service import ManagedService
    app, admin, project, clients = _team(tmp_path, 'reviewer', 'owner')
    calls = []
    for name in ('activate_install', 'uninstall_native', 'activate_scm'):
        monkeypatch.setattr(ManagedService, name, lambda self, name=name: calls.append(name) or {'status': name})
    monkeypatch.setattr(ManagedService, 'prepare_scm', lambda self, configuration: calls.append('prepare_scm') or {'status': 'prepared'})
    requests = [('post', '/api/runtime-services/install/activate', None), ('post', '/api/runtime-services/install/remove', None),
                ('post', '/api/runtime-services/scm/prepare', {'service_account': 'NT SERVICE\\vision'}),
                ('post', '/api/runtime-services/scm/activate', None)]
    for role in ('reviewer', 'owner'):
        for method, path, body in requests:
            response = getattr(clients[role], method)(path, json=body)
            assert response.status_code == 403, (role, path, response.status_code)
    assert calls == []
    assert admin.post('/api/accounts/select-project', json={'project_id': project['id']}).status_code == 200
    for method, path, body in requests:
        assert getattr(admin, method)(path, json=body).status_code == 200, path
    assert calls == ['activate_install', 'uninstall_native', 'prepare_scm', 'activate_scm']


def test_login_hashes_passwords_outside_the_account_write_lock(tmp_path, monkeypatch):
    from backend.engine.shared_accounts import AccountStore
    store = AccountStore(tmp_path/'accounts.sqlite')
    store.bootstrap('admin', PASSWORD)
    original = hashlib.scrypt
    locked = []

    def observed(*args, **kwargs):
        probe = sqlite3.connect(store.path, timeout=0)
        try:
            probe.execute('BEGIN IMMEDIATE')
            probe.rollback()
            locked.append(False)
        except sqlite3.OperationalError:
            locked.append(True)
        finally:
            probe.close()
        return original(*args, **kwargs)
    monkeypatch.setattr(hashlib, 'scrypt', observed)
    assert store.login('admin', PASSWORD)['user']['username'] == 'admin'
    with pytest.raises(ValueError):
        store.login('admin', 'wrong password 000')
    with pytest.raises(ValueError):
        store.login('nobody', 'wrong password 000')
    assert locked == [False, False, False]


def test_login_keeps_its_failure_limit_and_rechecks_the_account_after_hashing(tmp_path, monkeypatch):
    from backend.engine.shared_accounts import AccountStore
    store = AccountStore(tmp_path/'accounts.sqlite')
    admin = store.bootstrap('admin', PASSWORD)
    user = store.create_user('member', PASSWORD, administrator=False)
    for _ in range(10):
        with pytest.raises(ValueError):
            store.login('member', 'wrong password 000')
    with pytest.raises(ValueError):
        store.login('member', PASSWORD)  # blocked for the window, correct password or not
    db = sqlite3.connect(store.path)
    db.execute('DELETE FROM failures')
    db.commit()
    db.close()
    assert store.login('member', PASSWORD)['user']['id'] == user['id']
    original = hashlib.scrypt

    def disable_while_hashing(*args, **kwargs):
        monkeypatch.setattr(hashlib, 'scrypt', original)
        store.set_disabled(user['id'], True, admin['id'])
        return original(*args, **kwargs)
    monkeypatch.setattr(hashlib, 'scrypt', disable_while_hashing)
    with pytest.raises(ValueError):
        store.login('member', PASSWORD)


def test_shared_responses_carry_browser_security_headers(tmp_path):
    from backend.main import served_app
    app, admin, project, clients = _team(tmp_path, 'viewer')
    served = served_app(app)
    member = TestClient(served, headers={'Authorization': clients['viewer'].headers['Authorization']})
    reports = Path(project['reports_dir'])
    reports.mkdir(parents=True, exist_ok=True)
    (reports/'report_plain.html').write_text('<html></html>', encoding='utf-8')
    for response in (TestClient(served).get('/api/accounts/config'), member.get('/api/project/current'),
                     TestClient(served).get('/api/project/current'), member.get('/api/report/view/report_plain')):
        assert response.headers.get('x-content-type-options') == 'nosniff'
        assert response.headers.get('x-frame-options') == 'DENY'
        assert response.headers.get('referrer-policy') == 'no-referrer'
    assert "frame-ancestors 'none'" in TestClient(served).get('/api/project/current').headers['content-security-policy']
    # A route's own policy stays as it is.
    assert member.get('/api/report/view/report_plain').headers['content-security-policy'].startswith('sandbox;')


def test_local_desktop_server_runs_the_application_unchanged(tmp_path):
    from backend.main import create_app, served_app
    app = create_app(project_dir=str(tmp_path/'projects'))
    assert served_app(app) is app
    import ast
    import inspect
    from backend import main
    config = next(node for node in ast.walk(ast.parse(inspect.getsource(main.run_server)))
                  if isinstance(node, ast.Call) and ast.unparse(node.func) == 'uvicorn.Config')
    assert ast.unparse(next(keyword.value for keyword in config.keywords if keyword.arg == 'app')) == 'served_app(app)'
