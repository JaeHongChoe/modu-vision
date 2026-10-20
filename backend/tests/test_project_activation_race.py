"""A first GET /api/project/current that is still loading the saved project must not put that project back as current
after a concurrent create/open activated another one (issue #9: a spec created a project right after the app's first
current-project read, and the next request saw the older project)."""
import threading
import time
from types import SimpleNamespace

from backend.api import routes_project as rp


def _request(state):
    return SimpleNamespace(state=SimpleNamespace(), app=SimpleNamespace(state=state))


def test_a_concurrent_activation_wins_over_the_lazy_first_activation(monkeypatch):
    state = SimpleNamespace(current_project=None)
    order = []
    loading, release = threading.Event(), threading.Event()

    def record(request, project):
        order.append(project['id'])
        request.app.state.current_project = project
        return project

    def slow_saved_project(request):
        loading.set()
        assert release.wait(5)
        return rp._activate_project(request, {'id': 'saved-pointer-project'})

    monkeypatch.setattr(rp, '_activate_project_locked', record)
    monkeypatch.setattr(rp, '_activate_saved_or_default_project', slow_saved_project)

    lazy_result = []
    lazy = threading.Thread(target=lambda: lazy_result.append(rp.get_current_project(_request(state))))
    lazy.start()
    assert loading.wait(5)
    create = threading.Thread(target=lambda: rp._activate_project(_request(state), {'id': 'created-project'}))
    create.start()
    time.sleep(0.1)
    assert state.current_project is None, 'the create waits until the lazy activation is done'
    release.set()
    lazy.join(5)
    create.join(5)
    assert order == ['saved-pointer-project', 'created-project']
    assert state.current_project['id'] == 'created-project', 'the later explicit activation stays current'
    assert lazy_result[0]['id'] == 'saved-pointer-project'


def test_the_lazy_activation_returns_a_project_activated_while_it_waited(monkeypatch):
    state = SimpleNamespace(current_project=None)
    monkeypatch.setattr(rp, '_activate_saved_or_default_project',
                        lambda request: (_ for _ in ()).throw(AssertionError('must not reactivate the saved project')))
    with rp._ACTIVATION_LOCK:
        waiting = []
        reader = threading.Thread(target=lambda: waiting.append(rp.get_current_project(_request(state))))
        reader.start()
        time.sleep(0.1)
        state.current_project = {'id': 'opened-meanwhile'}
    reader.join(5)
    assert waiting[0]['id'] == 'opened-meanwhile'
