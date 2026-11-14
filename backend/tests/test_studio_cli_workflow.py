"""The additive public CLI lane must run without changing full qualification."""
import json
from pathlib import Path
import shlex

import yaml


ROOT = Path(__file__).resolve().parents[2]
CLI_SUITES = (
    'backend/tests/test_studio_cli.py',
    'backend/tests/test_studio_cli_integration.py',
    'backend/tests/test_automation_catalog.py',
)


def workflow():
    path = ROOT / '.github/workflows/ci-studio-cli.yml'
    assert path.is_file(), 'CLI regressions require their own always-selected workflow'
    return yaml.safe_load(path.read_text(encoding='utf-8'))


def test_cli_workflow_runs_for_every_pr_main_push_and_manual_request():
    value = workflow()
    assert value['on'] == {
        'pull_request': None,
        'push': {'branches': ['main']},
        'workflow_dispatch': None,
    }, 'Path filters and related-test selection must never omit the CLI lane'
    assert set(value['jobs']) == {'cli'}
    job = value['jobs']['cli']
    assert not any(key in job for key in ('if', 'needs', 'strategy', 'continue-on-error'))
    assert all('if' not in step and 'continue-on-error' not in step for step in job['steps'])


def test_cli_workflow_checks_out_without_credentials_secrets_or_cancellation():
    value = workflow()
    assert value['permissions'] == {'contents': 'read'}
    assert value['concurrency'] == {
        'group': 'studio-cli-${{ github.workflow }}-${{ github.ref }}',
        'queue': 'max',
        'cancel-in-progress': False,
    }
    assert 'secrets.' not in json.dumps(value)
    job = value['jobs']['cli']
    assert not any(key in job for key in ('permissions', 'environment', 'secrets'))
    assert job['steps'][0] == {
        'uses': 'actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1',
        'with': {'persist-credentials': False},
    }
    assert all('env' not in step for step in job['steps'])


def test_cli_workflow_uses_the_existing_locked_ubuntu_cpu_environment():
    job = workflow()['jobs']['cli']
    assert job['runs-on'] == 'ubuntu-24.04'
    assert job['timeout-minutes'] == 20
    assert job['env'] == {'OMP_NUM_THREADS': '2', 'MKL_NUM_THREADS': '2'}
    assert job['steps'][1] == {
        'uses': 'actions/setup-node@820762786026740c76f36085b0efc47a31fe5020',
        'with': {'node-version': '24'},
    }
    assert job['steps'][2] == {
        'uses': 'actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97',
        'with': {'python-version': '3.13'},
    }
    assert job['steps'][3] == {
        'name': 'Install locked CPU environment',
        'run': 'python -m pip install --require-hashes --only-binary=:all: -r build/ci/requirements-ubuntu-py313-cpu.lock',
    }


def test_cli_workflow_executes_each_complete_suite_once_and_checks_itself():
    steps = workflow()['jobs']['cli']['steps']
    assert len(steps) == 5, 'The independent lane must have a single unconditional regression command'
    command = shlex.split(steps[4]['run'])
    assert command == [
        'python', '-m', 'pytest', '-q', *CLI_SUITES,
        'backend/tests/test_studio_cli_workflow.py',
    ], 'Run every complete CLI suite exactly once; do not mask or select failures away'
    for suite in CLI_SUITES:
        assert (ROOT / suite).is_file()
        assert command.count(suite) == 1
