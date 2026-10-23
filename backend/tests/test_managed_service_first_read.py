"""A new project's first screens read the inspection service and its capture groups at the same time. The request
that finds another one writing the service configuration waits for it instead of failing with a 500 (seen as
console errors in runtime-optimization-exact-cancel-controls)."""
import threading
import time

from backend.engine.managed_service import ManagedService
from backend.engine.runtime_process_control import atomic_private_json, runtime_state_lock


def test_a_concurrent_first_read_waits_for_the_configuration_being_written(tmp_path):
    root = tmp_path / 'runtime_service'
    root.mkdir()
    entered, release = threading.Event(), threading.Event()

    def writer():
        with runtime_state_lock(root):
            entered.set()
            assert release.wait(5)
            atomic_private_json(root / 'service.json', {'port': 4321, 'token': 'fixture-token', 'pid': None})

    first = threading.Thread(target=writer)
    first.start()
    assert entered.wait(5)
    services, errors = [], []
    second = threading.Thread(target=lambda: _construct(tmp_path, services, errors))
    second.start()
    time.sleep(0.1)
    release.set()
    first.join(5)
    second.join(5)
    assert errors == []
    assert services[0].config['port'] == 4321, 'the waiting request reads the configuration the other one wrote'


def test_parallel_first_reads_of_a_fresh_project_all_succeed_with_one_configuration(tmp_path):
    services, errors = [], []
    threads = [threading.Thread(target=lambda: _construct(tmp_path, services, errors)) for _ in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)
    assert errors == []
    assert len({service.config['token'] for service in services}) == 1


def _construct(project_dir, services, errors):
    try:
        services.append(ManagedService(project_dir))
    except Exception as exc:  # recorded for the assertion
        errors.append(exc)
