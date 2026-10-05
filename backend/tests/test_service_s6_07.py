"""S6-07: the contribution documents describe what this repository actually does.

Every command and path a contributor is told to use exists and matches what CI runs; the CPU demo trains and
evaluates a real model on the CPU of a fresh checkout; the security guidance keeps real images and secrets out of
public reports; and the documents name the model families the engine really has.
"""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
DOCUMENTS = ['CONTRIBUTING.md', 'SECURITY.md', 'CODE_OF_CONDUCT.md', '.github/PULL_REQUEST_TEMPLATE.md',
             '.github/ISSUE_TEMPLATE/bug_report.yml', '.github/ISSUE_TEMPLATE/feature_request.yml',
             '.github/ISSUE_TEMPLATE/private_contact.yml', '.github/ISSUE_TEMPLATE/config.yml', 'README.md']


def read(relative):
    return (ROOT / relative).read_text(encoding='utf-8')


def test_every_contribution_document_exists():
    assert [name for name in DOCUMENTS if not (ROOT / name).is_file()] == []


def test_every_command_and_path_in_the_guide_is_real():
    guide = read('CONTRIBUTING.md')
    scripts = json.loads(read('package.json'))['scripts']
    named = set(re.findall(r'npm run ([\w:-]+)', guide))
    assert named and named <= set(scripts), named - set(scripts)
    # A path ends at a boundary; a template such as test_service_s<단계>_<번호>.py is not a path.
    paths = set(re.findall(r'(?<![\w/.-])((?:build|scripts|backend|docs|\.github|src)/[\w./-]*\w)(?![<\w])', guide))
    assert {'scripts/cpu_demo.py', 'build/ci/requirements-windows-py313-cpu.lock', 'docs/release-platform-matrix.md'} <= paths
    missing = sorted(path for path in paths if not (ROOT / path).exists())
    assert missing == [], missing


def test_the_locked_install_commands_and_versions_are_the_ones_ci_runs():
    guide, readme = read('CONTRIBUTING.md'), read('README.md')
    for workflow, lock in (('windows-native.yml', 'requirements-windows-py313-cpu.lock'), ('ci.yml', 'requirements-ubuntu-py313-cpu.lock')):
        text = ' '.join(read(f'.github/workflows/{workflow}').split())
        installs = re.findall(r'python -m pip install ([^&;|\n]*?build/ci/[\w.-]+\.lock)', text)
        assert installs == [f'--require-hashes --only-binary=:all: -r build/ci/{lock}'], (workflow, installs)
        assert f'-m pip install {installs[0]}' in guide, lock  # the same arguments, whichever Python the shell names
        assert 'npm ci' in text, workflow
        steps = yaml.safe_load(read(f'.github/workflows/{workflow}'))
        versions = {key: str(step['with'][key]) for job in steps['jobs'].values() for step in job['steps']
                    for key in ('python-version', 'node-version') if key in step.get('with', {})}
        assert f"Python {versions['python-version']}" in guide and f"Node.js {versions['node-version']}" in guide, versions
        assert f"**Python**: {versions['python-version']}" in readme and f"**Node.js**: {versions['node-version']}" in readme, versions
    electron = json.loads(read('package.json'))['devDependencies']['electron'].split('.')[0]
    assert f'/ {electron} |' in guide and f'Electron {electron}' in readme, electron
    for document in (guide, readme):
        assert 'npm ci' in document and not re.search(r'npm(?:\.cmd)? install', document), 'the lock file is installed as is'
        assert not re.search(r'(?m)^\s*pip install', document), 'pip runs through the chosen Python'
    assert '-m pip install --require-hashes --only-binary=:all: -r build/ci/requirements-ubuntu-py313-cpu.lock' in readme
    assert f'badge/Electron-{electron}-' in readme and 'badge/Windows-11%20x64' in readme, 'badges state the pinned versions'


def test_the_guide_names_the_model_families_the_engine_has():
    from backend.engine.training_engine import TASKS
    guide = read('CONTRIBUTING.md')
    missing = [task for task in TASKS if not re.search(rf'(?<![\w]){task}(?![\w])', guide)]  # 'detection' is not 'rotated_detection'
    assert not missing, missing
    assert f'모델군은 {len(TASKS)}개' in guide


def test_the_cpu_demo_trains_and_evaluates_a_real_model_on_the_cpu(tmp_path):
    # Only the child receives these paths. Changing the parent process's environment also redirects unrelated
    # job-ledger heartbeat threads from earlier tests, which can create this folder while the demo is running.
    environment = dict(os.environ, VISION_AI_STUDIO_USER_DATA_DIR=str(tmp_path / '사용자 데이터'),
                       VISION_RESOURCE_LEASE_DB=str(tmp_path / 'leases' / 'resource_leases.sqlite3'),
                       PYTHONIOENCODING='utf-8')
    workdir = tmp_path / '데모 작업'
    completed = subprocess.run([sys.executable, str(ROOT / 'scripts' / 'cpu_demo.py'), '--workdir', str(workdir)],
                               cwd=ROOT, env=environment, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=600)
    assert completed.returncode == 0, completed.stdout[-2000:] + completed.stderr[-2000:]
    result = json.loads(completed.stdout[completed.stdout.index('{'):])
    assert result['cpu_demo'] == 'completed' and result['test_images'] == 16
    assert {'model.pt', 'model_meta.json', 'configuration.json', 'evaluation.json'} <= set(result['delivered_files'])
    # Chance level for uniform angles is about 90 degrees; the demo must have learned, not just run.
    assert result['evaluated_split'] == 'test', result
    assert 0 <= result['angular_mae_deg'] < 30, result
    assert (workdir / 'model output').is_dir() and len(result['checkpoint_sha256']) == 64
    # the job record the engine writes stays in the demo's own folder, never in the app data folder of the environment
    assert (workdir / 'app data' / 'jobs' / 'ledger.sqlite3').is_file()
    assert not (tmp_path / '사용자 데이터').exists() and not (tmp_path / 'leases').exists()


def test_security_reports_keep_real_images_and_secrets_out():
    policy = read('SECURITY.md')
    assert 'Report a vulnerability' in policy and '공개 이슈' in policy
    for kept_out in ('검사 이미지', '고객 데이터', '비밀번호', '토큰', 'API 키', '개인정보'):
        assert kept_out in policy, kept_out
    assert 'scripts/cpu_demo.py' in policy, 'a synthetic reproduction is offered'


def test_issue_forms_require_the_hygiene_confirmation_and_route_security_privately():
    for name in ('bug_report.yml', 'feature_request.yml'):
        form = yaml.safe_load(read(f'.github/ISSUE_TEMPLATE/{name}'))
        boxes = [item for item in form['body'] if item['type'] == 'checkboxes']
        assert boxes and all(option.get('required') for box in boxes for option in box['attributes']['options']), name
        assert any('첨부하지 않았습니다' in option['label'] for box in boxes for option in box['attributes']['options'])
    config = yaml.safe_load(read('.github/ISSUE_TEMPLATE/config.yml'))
    assert config['blank_issues_enabled'] is False
    assert any(link['url'].endswith('/security/policy') for link in config['contact_links'])
    template = read('.github/PULL_REQUEST_TEMPLATE.md')
    for evidence in ('실제 학습·검사 실행', 'native Windows 실행', '실제 장비', '모델 품질', '라이선스·서명·배포 승인'):
        assert evidence in template, evidence


@pytest.mark.parametrize('name', DOCUMENTS)
def test_public_documents_carry_no_personal_paths_addresses_or_secrets(name):
    text = read(name)
    assert not re.search(r'[\w.+-]+@[\w-]+\.[\w.]+', text), 'no e-mail addresses'
    assert not re.search(r'/Users/|[A-Za-z]:\\Users\\|/home/\w', text), 'no personal paths'
    assert not re.search(r'\b(ghp_|github_pat_|sk-[A-Za-z0-9]{8}|AKIA[0-9A-Z]{8})', text), 'no tokens'


def test_the_module_map_names_real_folders_files_and_routes():
    """A module-map row names a folder; the files it mentions are under that folder, and API routes exist."""
    guide = read('CONTRIBUTING.md')
    section = guide[guide.index('## 5. '):guide.index('## 6. ')]
    rows = re.findall(r'^\| `([^`]+/)`[^|]*\| (.*) \|$', section, flags=re.M)
    assert len(rows) >= 8, rows
    for folder, text in rows:
        assert (ROOT / folder).is_dir(), folder
        for mentioned in re.findall(r'`([\w./-]+\.(?:ts|tsx|py))`', text):
            assert (ROOT / folder / mentioned).is_file(), (folder, mentioned)
        for route in re.findall(r'`(/api/[\w/-]+)`', text):
            assert any(f'"{route}"' in path.read_text(encoding='utf-8') or f"'{route}'" in path.read_text(encoding='utf-8')
                       for path in (ROOT / 'backend' / 'api').glob('routes_*.py')), route


def test_reports_have_a_private_channel_that_asks_for_no_details():
    """Security and conduct reports reach a private channel even where the hosting's private reporting is off: a contact
    form with no free-text field asks the maintainers to open one."""
    form = yaml.safe_load(read('.github/ISSUE_TEMPLATE/private_contact.yml'))
    kinds = [item['type'] for item in form['body']]
    assert 'textarea' not in kinds and 'input' not in kinds, kinds
    # a public request must not say what it is about (nor who reports a conduct problem): one neutral choice
    choices = next(item for item in form['body'] if item['type'] == 'dropdown')['attributes']['options']
    assert choices == ['비공개 연락이 필요합니다'], choices
    boxes = [option for item in form['body'] if item['type'] == 'checkboxes' for option in item['attributes']['options']]
    assert boxes and all(option.get('required') for option in boxes)
    for name in ('SECURITY.md', 'CODE_OF_CONDUCT.md', 'CONTRIBUTING.md'):
        assert '비공개 연락 요청' in read(name), name
    assert 'Report a vulnerability' in read('SECURITY.md')


def test_the_pull_request_template_asks_for_the_hygiene_confirmation_and_ci_runs_this_test():
    template = read('.github/PULL_REQUEST_TEMPLATE.md')
    assert re.search(r'- \[ \] 실제 검사 이미지, 고객 데이터, 개인정보, 비밀번호·토큰', template)
    for workflow in ('ci.yml', 'windows-native.yml'):
        assert 'backend/tests/test_service_s6_07.py' in read(f'.github/workflows/{workflow}'), workflow
