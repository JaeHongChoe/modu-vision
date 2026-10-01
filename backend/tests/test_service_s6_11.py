"""S6-11: public distribution license decision and the fail-closed public release gate.

The gate reads the S6-01 inventory and the decision record. Every public artifact scope stays held until the owner
records an approved decision with a named authority that pins each unresolved item and whose notices list what ships.
Nothing here decides a license or changes LICENSE.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
DECISIONS = ROOT / 'docs' / 'distribution-license-decision.md'
GATE = ROOT / 'scripts' / 'distribution_release_gate.py'
FIRST_PARTY = {'category': 'first_party', 'name': 'modu-vision', 'version': None, 'license': 'MIT',
               'distribution': 'source_and_installer', 'status': 'resolved', 'notices_required': False}


@pytest.fixture(scope='module')
def receipt():
    from scripts.license_inventory import build_inventory
    return build_inventory(ROOT)


def _sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _tree(tmp_path, notices='', matrix=''):
    """A small source tree the gate can check notices and receipt freshness against."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / 'LICENSE').write_text('MIT License\n')
    (tmp_path / 'THIRD_PARTY_NOTICES.md').write_text(notices)
    (tmp_path / 'docs').mkdir()
    (tmp_path / 'docs' / 'model-license-matrix.md').write_text(matrix)
    (tmp_path / 'requirements.txt').write_text('')
    (tmp_path / 'package.json').write_text('{"name": "mini", "version": "1.0.0", "license": "MIT", "dependencies": {}}')
    (tmp_path / 'package-lock.json').write_text('{"name": "mini", "lockfileVersion": 3, "packages": {"": {"name": "mini"}}}')
    return tmp_path


def _component(category, name, distribution, status='needs_decision', license='AGPL-3.0', version='1.0', notices=True):
    return {'category': category, 'name': name, 'version': version, 'license': license, 'distribution': distribution,
            'status': status, 'notices_required': notices}


def _receipt(root, *components, conditions=()):
    from scripts import license_inventory as inventory
    names = ['package-lock.json', inventory.PYTHON_RUNTIME, inventory.REMOTE_WORKER_LOCK, inventory.REMOTE_WORKER_DOCKERFILE,
             inventory.CACHED_WEIGHTS, *inventory.PYTHON_OPTIONAL.values()]
    shipped = [FIRST_PARTY, *components]
    unresolved = [{'id': f"{item['category']}:{item['name']}", 'status': item['status'], 'license': item['license'],
                   'distribution': item['distribution']} for item in shipped if item['status'] != 'resolved']
    unresolved += [{'id': item_id, 'status': 'needs_review', 'license': None, 'distribution': distribution}
                   for item_id, distribution in conditions]
    return {'schema': inventory.SCHEMA, 'receipt': 'InventoryReceipt',
            'inputs': {name: inventory._sha256(Path(root) / name) for name in names},
            'components': shipped, 'unresolved_items': unresolved}


def _notice_rows(*components):
    """Notice table rows the way the S6-01 generator writes them."""
    from scripts import license_inventory as inventory
    return '\n'.join(inventory._table(list(components))) + '\n'


def _pin(item):
    return {'id': f"{item['category']}:{item['name']}", 'license': item['license'], 'version': item['version']}


def _approved(scope, *accepted, notices='THIRD_PARTY_NOTICES.md', **overrides):
    row = {'artifact_scope': scope, 'license': 'recorded', 'notices': notices, 'authority': 'release owner',
           'approval_status': 'approved', 'decision': 'reviewed', 'accepted_items': list(accepted), 'excluded_packages': []}
    row.update(overrides)
    return row


def _source(root, **overrides):
    return _approved('source', notices='LICENSE', license_file_sha256=_sha256(Path(root) / 'LICENSE'), **overrides)


def _decision_file(path, rows):
    path.write_text('# decisions\n\n<!-- distribution-decisions -->\n```json\n' + json.dumps(rows) + '\n```\n')
    return path


def _gate(*args):
    return subprocess.run([sys.executable, str(GATE), *map(str, args)], cwd=ROOT, capture_output=True, text=True, timeout=300)


# The real inventory and decision record.
def test_every_public_scope_in_the_inventory_has_a_decision_row(receipt):
    from scripts.distribution_release_gate import artifact_scopes, load_decisions
    decisions = load_decisions(DECISIONS)
    recorded = {row['artifact_scope'] for row in decisions}
    needed = set()
    for item in receipt['components'] + receipt['unresolved_items']:
        needed |= artifact_scopes(item['distribution'])
    assert needed - recorded == set(), 'every public artifact scope needs a decision row'
    for row in decisions:
        assert set(row) >= {'artifact_scope', 'license', 'notices', 'authority', 'approval_status'}
        assert row['approval_status'] in ('pending', 'approved', 'blocked')
        for notice in ([row['notices']] if isinstance(row['notices'], str) else row['notices']):
            assert (ROOT / notice).is_file(), notice


def test_the_gate_holds_the_current_public_release_with_its_reasons(receipt, tmp_path):
    from scripts.distribution_release_gate import evaluate, load_decisions
    report = evaluate(receipt, load_decisions(DECISIONS), _sha256(ROOT / 'LICENSE'))
    assert report['allowed'] is False and 'inventory' not in report['scopes'], 'a fresh valid receipt, held by decisions'
    assert 'python_runtime:ultralytics' in report['scopes']['installer']['unresolved'], 'the bundled AGPL package holds'
    assert report['scopes']['trained_export']['allowed'] is False
    assert 'model_weights:sam2_user_configured' in report['not_public_unresolved'], 'non-public items stay visible'
    path = tmp_path / 'receipt.json'
    path.write_text(json.dumps(receipt))
    result = _gate('--inventory', path, '--decisions', DECISIONS)
    assert result.returncode == 1, result.stderr
    assert json.loads(result.stdout)['scopes']['installer']['unresolved'] == report['scopes']['installer']['unresolved']


def test_the_default_command_builds_the_inventory_and_holds():
    result = _gate()
    assert result.returncode == 1, result.stderr
    assert json.loads(result.stdout)['allowed'] is False


def test_the_decision_record_matches_the_current_license_and_decides_nothing_on_the_owners_behalf():
    from scripts.distribution_release_gate import load_decisions
    rows = {row['artifact_scope']: row for row in load_decisions(DECISIONS)}
    assert rows['source']['license_file_sha256'] == _sha256(ROOT / 'LICENSE'), 'LICENSE is unchanged in this round'
    assert (ROOT / 'LICENSE').read_text().startswith('MIT License')
    assert all(row['approval_status'] == 'pending' and not row['authority'] for row in rows.values())


# The gate's rules, on small valid receipts.
def test_a_small_fully_decided_release_passes(tmp_path):
    from scripts.distribution_release_gate import evaluate, load_decisions
    lib = _component('npm_runtime', 'left-pad', 'bundled_in_renderer', status='resolved', license='MIT')
    root = _tree(tmp_path / 'tree', notices=_notice_rows(lib))
    decisions = load_decisions(_decision_file(tmp_path / 'decisions.md', [_source(root), _approved('installer')]))
    assert evaluate(_receipt(root, lib), decisions, _sha256(root / 'LICENSE'), root)['allowed'] is True


def test_a_release_built_and_decided_end_to_end_exits_zero_and_a_doctored_receipt_does_not(tmp_path):
    """A small real tree and frozen build; the owner pins every unresolved item and regenerates the notices."""
    from scripts import license_inventory as inventory
    from scripts.distribution_release_gate import artifact_scopes
    root = _tree(tmp_path / 'tree')
    build = tmp_path / 'dist' / 'vision_ai_backend'
    info = build / '_internal' / 'fake_pkg-1.0.dist-info'
    info.mkdir(parents=True)
    (info / 'METADATA').write_text('Metadata-Version: 2.1\nName: fake-pkg\nVersion: 1.0\nLicense: MIT\n')
    toc = tmp_path / 'PYZ-00.toc'
    toc.write_text('[]')
    receipt = inventory.build_inventory(root, frozen_build=build, pyz_toc=toc)
    scopes, pins = {}, {}
    for item in receipt['unresolved_items']:
        components = [c for c in receipt['components'] if f"{c['category']}:{c['name']}" == item['id']]
        for scope in artifact_scopes(item['distribution']):
            pins.setdefault(scope, []).extend(
                [_pin(c) for c in components] or [{'id': item['id'], 'license': item['license']}])
    for item in receipt['components']:
        scopes.update({scope: True for scope in artifact_scopes(item['distribution'])})
    noticed = [c for c in receipt['components'] if c['notices_required']
               and c['category'] not in ('model_weights', 'trained_model', 'first_party', 'first_party_asset')]
    (root / 'THIRD_PARTY_NOTICES.md').write_text(_notice_rows(*noticed))
    (root / 'docs' / 'model-license-matrix.md').write_text(inventory.render_matrix(receipt))
    notices = {'source': 'LICENSE', 'runtime_download': 'docs/model-license-matrix.md',
               'trained_export': 'docs/model-license-matrix.md',
               'remote_worker_image': ['THIRD_PARTY_NOTICES.md', 'docs/model-license-matrix.md']}
    rows = [_approved(scope, *pins.get(scope, []), notices=notices.get(scope, 'THIRD_PARTY_NOTICES.md'))
            for scope in sorted(set(scopes) | set(pins))]
    rows = [{**row, 'license_file_sha256': _sha256(root / 'LICENSE')} if row['artifact_scope'] == 'source' else row
            for row in rows]
    decisions = _decision_file(tmp_path / 'decisions.md', rows)
    built = ('--root', root, '--frozen-build', build, '--pyz-toc', toc, '--decisions', decisions)
    result = _gate(*built)
    assert result.returncode == 0, result.stdout + result.stderr
    (tmp_path / 'receipt.json').write_text(json.dumps(receipt))
    assert _gate(*built, '--inventory', tmp_path / 'receipt.json').returncode == 0
    doctored = {**receipt, 'components': [c for c in receipt['components'] if c['category'] != 'model_weights']}
    doctored['unresolved_items'] = [i for i in receipt['unresolved_items'] if not i['id'].startswith('model_weights:')]
    (tmp_path / 'doctored.json').write_text(json.dumps(doctored))
    held = _gate(*built, '--inventory', tmp_path / 'doctored.json')
    assert held.returncode == 1 and 'differ from a fresh inventory' in held.stdout
    # Drop the frozen package's notice row: honestly held, and an edited receipt cannot switch the notice off.
    (root / 'THIRD_PARTY_NOTICES.md').write_text(_notice_rows(*[c for c in noticed if c['name'] != 'fake-pkg']))
    assert _gate(*built).returncode == 1
    edited = {**receipt, 'components': [{**c, 'notices_required': False} for c in receipt['components']]}
    (tmp_path / 'edited.json').write_text(json.dumps(edited))
    assert _gate(*built, '--inventory', tmp_path / 'edited.json').returncode == 1


def test_approval_needs_an_authority_a_decision_and_items_pinned_to_license_and_version(tmp_path):
    from scripts.distribution_release_gate import evaluate
    agpl = _component('python_runtime', 'ultralytics', 'frozen_backend', version='8.4.41')
    root = _tree(tmp_path, notices=_notice_rows(agpl))
    receipt = _receipt(root, agpl)

    def installer(row):
        return evaluate(receipt, [_source(root), row], _sha256(root / 'LICENSE'), root)['scopes']['installer']

    assert installer(_approved('installer', _pin(agpl)))['allowed'] is True
    assert installer(_approved('installer', _pin(agpl), authority='  '))['allowed'] is False
    assert installer(_approved('installer', _pin(agpl), decision=' '))['allowed'] is False
    assert installer(_approved('installer', _pin(agpl), decision=True))['allowed'] is False
    assert installer(_approved('installer', _pin(agpl), approval_status='pending'))['allowed'] is False
    assert installer(_approved('installer', 'python_runtime:ultralytics'))['allowed'] is False, 'a bare id pins nothing'
    assert installer(_approved('installer', {**_pin(agpl), 'license': 'MIT'}))['allowed'] is False, 'license changed'
    assert installer(_approved('installer', {**_pin(agpl), 'version': '8.4.40'}))['allowed'] is False, 'version changed'
    assert evaluate(receipt, [_source(root)], None, root)['scopes']['installer']['allowed'] is False, 'no row holds'


def test_a_new_unresolved_item_reblocks_an_approved_scope_and_stale_acceptances_are_reported(tmp_path):
    from scripts.distribution_release_gate import evaluate
    a, b = (_component('python_runtime', name, 'frozen_backend') for name in ('a', 'b'))
    root = _tree(tmp_path, notices=_notice_rows(a, b))
    gone = {'id': 'python_runtime:gone', 'license': 'GPL-3.0', 'version': '1'}
    rows = [_source(root), _approved('installer', _pin(a), gone)]
    assert evaluate(_receipt(root, a), rows, None, root)['scopes']['installer']['allowed'] is True
    report = evaluate(_receipt(root, a, b), rows, None, root)['scopes']['installer']
    assert report['allowed'] is False and report['unresolved'] == ['python_runtime:b']
    assert report['stale_accepted'] == ['python_runtime:gone']


def test_unknown_or_empty_distributions_fail_closed(tmp_path):
    from scripts.distribution_release_gate import artifact_scopes, evaluate
    for value in (None, '', '+', ' + ', '(frozen_backend)', 'carrier_pigeon', 3):
        assert artifact_scopes(value) == {'unmapped'}, value
    assert artifact_scopes('bundled_in_renderer (module preload helper in the built JS)') == {'installer'}
    assert artifact_scopes('runtime_download_from_provider + user_import') == {'runtime_download'}
    assert artifact_scopes('not_distributed') == set()
    root = _tree(tmp_path)
    report = evaluate(_receipt(root, _component('python_runtime', 'x', '')), [_source(root)], None, root)
    assert report['allowed'] is False and report['scopes']['unmapped']['unresolved'] == ['python_runtime:x']


def test_an_excluded_package_holds_every_scope_that_ships_it_under_any_spelling(tmp_path):
    from scripts.distribution_release_gate import evaluate
    worker = {**_component('python_remote_worker', 'Ultralytics', 'dockerfile_built_by_server_owner'), 'bundled': False}
    thop = _component('frozen_backend_package', 'ultralytics_thop', 'frozen_backend')
    codec = _component('frozen_backend_native', 'libx264.so.164', 'frozen_backend', version=None)
    mac = _component('frozen_backend_native', 'libx265.199.dylib', 'frozen_backend', version=None)
    win = _component('frozen_backend_native', 'avcodec-61.dll', 'frozen_backend', version=None)
    root = _tree(tmp_path, notices=_notice_rows(worker, thop, codec, mac, win))
    receipt = _receipt(root, worker, thop, codec, mac, win)
    rows = [_source(root), _approved('remote_worker_image', _pin(worker), excluded_packages=['ultralytics']),
            _approved('installer', _pin(thop), _pin(codec), _pin(mac), _pin(win),
                      excluded_packages=['python_runtime:ultralytics-thop', 'libx264', 'libx265', 'avcodec'])]
    scopes = evaluate(receipt, rows, None, root)['scopes']
    for scope in ('remote_worker_image', 'installer'):
        assert scopes[scope]['allowed'] is False
        assert any('excluded' in reason for reason in scopes[scope]['reasons']), scopes[scope]
    for library in ('libx264.so.164', 'libx265.199.dylib', 'avcodec-61.dll'):
        assert any(library in reason for reason in scopes['installer']['reasons']), library


def test_the_notices_must_list_what_each_scope_ships(tmp_path):
    from scripts.distribution_release_gate import evaluate
    root = _tree(tmp_path, notices=_notice_rows(_component('npm_runtime', 'other', 'bundled_in_renderer')),
                 matrix='| dino | id | backbone | provider terms | runtime_download_from_provider | resolved | src |\n')
    lib = _component('npm_runtime', 'brand-new-dep', 'bundled_in_renderer', status='resolved', license='MIT')
    weights = _component('model_weights', 'dino', 'runtime_download_from_provider', status='resolved',
                         license='provider terms', version='id')
    receipt = _receipt(root, lib, weights)
    rows = [_source(root), _approved('installer'), _approved('runtime_download', notices='docs/model-license-matrix.md')]
    scopes = evaluate(receipt, rows, None, root)['scopes']
    assert scopes['installer']['allowed'] is False
    assert any('brand-new-dep' in reason for reason in scopes['installer']['reasons'])
    assert scopes['runtime_download']['allowed'] is True, 'the matrix row carries the license and status'
    changed = _receipt(root, lib, {**weights, 'license': 'new terms'})
    rows[2]['accepted_items'] = []
    assert evaluate(changed, rows, None, root)['scopes']['runtime_download']['allowed'] is False, 'matrix out of date'
    both = [_source(root), _approved('installer', notices=['THIRD_PARTY_NOTICES.md', 'docs/model-license-matrix.md'])]
    assert evaluate(_receipt(root), both, None, root)['scopes']['installer']['allowed'] is True, 'several notice files'
    missing = [_source(root), _approved('installer', notices='NOTICE-THAT-DOES-NOT-EXIST.md')]
    held = evaluate(_receipt(root), missing, None, root)['scopes']['installer']
    assert held['allowed'] is False and any('does not exist' in reason for reason in held['reasons'])
    for outside in ('/etc/hosts', '../THIRD_PARTY_NOTICES.md', ' /etc/hosts', ' ../THIRD_PARTY_NOTICES.md'):
        held = evaluate(_receipt(root), [_source(root), _approved('installer', notices=outside)], None, root)
        assert any('outside the source tree' in reason for reason in held['scopes']['installer']['reasons']), outside


def test_a_notice_counts_only_as_a_table_row_with_the_exact_version_license_and_status(tmp_path):
    from scripts.distribution_release_gate import evaluate
    numpy = _component('python_runtime', 'numpy', 'frozen_backend', status='resolved', license='BSD-3-Clause',
                       version='99.0.0')
    exact = _notice_rows(numpy).splitlines()[-1]
    near_misses = ['We bundle | numpy | in prose.', '<!-- | numpy | 99.0.0 | BSD-3-Clause | resolved | -->',
                   '| numpy |', _notice_rows({**numpy, 'version': '1.26.4'}), _notice_rows({**numpy, 'license': 'MIT'}),
                   f'<!--\n{exact}\n-->', f'```\n{exact}\n```', f'<!-- unterminated\n{exact}',
                   f'~~~\n{exact}\n~~~', f'  ```\n{exact}\n  ```', f'```\n{exact}',
                   '| numpy | 99.0.0 | resolved | BSD-3-Clause | src |']
    for text in near_misses:
        root = _tree(tmp_path / str(abs(hash(text))), notices=text + '\n')
        scope = evaluate(_receipt(root, numpy), [_source(root), _approved('installer')], None, root)['scopes']['installer']
        assert scope['allowed'] is False, text
    root = _tree(tmp_path / 'exact', notices=_notice_rows(numpy))
    assert evaluate(_receipt(root, numpy), [_source(root), _approved('installer')], None, root)['allowed'] is True


def test_a_matrix_license_containing_a_bar_matches_the_generated_matrix(tmp_path):
    from scripts import license_inventory as inventory
    from scripts.distribution_release_gate import evaluate
    weights = {**_component('model_weights', 'dual', 'runtime_download_from_provider', status='resolved',
                            license='A | B', version='dual.v1'), 'use': 'test', 'source': 'src'}
    root = _tree(tmp_path)
    receipt = _receipt(root, weights)
    (root / 'docs' / 'model-license-matrix.md').write_text(inventory.render_matrix(receipt))
    rows = [_source(root), _approved('runtime_download', notices='docs/model-license-matrix.md')]
    assert evaluate(receipt, rows, None, root)['scopes']['runtime_download']['allowed'] is True


def test_every_shipped_version_of_a_duplicated_id_must_be_pinned(tmp_path):
    from scripts.distribution_release_gate import evaluate
    old, new = (_component('npm_runtime', 'dupe', 'bundled_in_renderer', license=terms, version=version)
                for terms, version in (('MPL-2.0', '1.0.0'), ('GPL-3.0', '2.0.0')))
    root = _tree(tmp_path, notices=_notice_rows(old, new))
    receipt = _receipt(root, old, new)
    receipt['unresolved_items'] = [item for index, item in enumerate(receipt['unresolved_items'])
                                   if item['id'] != 'npm_runtime:dupe' or index == 0]  # the generator lists an id once
    assert evaluate(receipt, [_source(root), _approved('installer', _pin(old))], None, root)['allowed'] is False
    assert evaluate(receipt, [_source(root), _approved('installer', _pin(old), _pin(new))], None, root)['allowed'] is True


def test_release_build_conditions_cannot_be_accepted(tmp_path):
    from scripts.distribution_release_gate import evaluate
    root = _tree(tmp_path)
    receipt = _receipt(root, conditions=[('frozen_backend:inventory_required', 'frozen_backend')])
    pin = {'id': 'frozen_backend:inventory_required', 'license': None}
    scope = evaluate(receipt, [_source(root), _approved('installer', pin)], None, root)['scopes']['installer']
    assert scope['allowed'] is False and scope['unresolved'] == ['frozen_backend:inventory_required']
    assert any('locked release build' in reason for reason in scope['reasons'])


def test_a_receipt_from_another_commit_or_generator_is_not_evidence(tmp_path):
    from scripts.distribution_release_gate import evaluate
    root = _tree(tmp_path)
    fresh = {**_receipt(root), 'source': {'commit': 'a' * 40}}
    rows = [_source(root), _approved('installer')]
    assert evaluate(fresh, rows, None, root, fresh=fresh)['allowed'] is True
    other_commit = {**fresh, 'source': {'commit': 'b' * 40}}
    report = evaluate(other_commit, rows, None, root, fresh=fresh)
    assert report['allowed'] is False and any('commit' in r for r in report['scopes']['inventory']['reasons'])
    older = {**fresh, 'components': [*fresh['components'], _component('model_weights', 'gone', 'runtime_download_from_provider')]}
    assert evaluate(older, rows, None, root, fresh=fresh)['scopes']['inventory']['allowed'] is False


def test_a_malformed_or_stale_receipt_holds_the_release(tmp_path):
    from scripts.distribution_release_gate import evaluate
    root = _tree(tmp_path / 'tree')
    rows = [_source(root), _approved('installer')]
    empty = evaluate({}, rows, None, root)
    assert empty['allowed'] is False and empty['scopes']['inventory']['allowed'] is False
    fresh = _receipt(root)
    assert evaluate(fresh, rows, None, root)['allowed'] is True
    (root / 'requirements.txt').write_text('torch\n')  # inputs changed after the receipt was built
    stale = evaluate(fresh, rows, None, root)
    assert stale['allowed'] is False and any('stale' in reason for reason in stale['scopes']['inventory']['reasons'])
    (tmp_path / 'empty.json').write_text('{}')
    decisions = _decision_file(tmp_path / 'decisions.md', rows)
    assert _gate('--root', root, '--inventory', tmp_path / 'empty.json', '--decisions', decisions).returncode == 1


def test_a_license_change_holds_the_source_and_the_installer(tmp_path):
    from scripts.distribution_release_gate import evaluate
    root = _tree(tmp_path)
    rows = [_source(root), _approved('installer')]
    assert evaluate(_receipt(root), rows, _sha256(root / 'LICENSE'), root)['allowed'] is True
    scopes = evaluate(_receipt(root), rows, 'b' * 64, root)['scopes']
    for scope in ('source', 'installer'):
        assert scopes[scope]['allowed'] is False and any('LICENSE' in reason for reason in scopes[scope]['reasons'])


def test_an_unreadable_decision_record_is_an_error_not_a_release(tmp_path):
    broken = tmp_path / 'decisions.md'
    broken.write_text('# no decisions block\n')
    result = _gate('--decisions', broken, '--inventory', tmp_path / 'missing.json')
    assert result.returncode == 2 and json.loads(result.stderr)['allowed'] is False
    from scripts.distribution_release_gate import GateError, load_decisions
    for bad in ({'excluded_packages': 'ultralytics'}, {'accepted_items': 'x'}, {'notices': 3},
                {'approval_status': None}, {'excluded_packages': [1]}):
        with pytest.raises(GateError):
            load_decisions(_decision_file(tmp_path / 'bad.md', [{**_approved('installer'), **bad}]))
