"""Ordinary native selections must authorize bytes before any snapshot read."""
import json
import os
from pathlib import Path

import pytest

from backend.tests.test_local_host_linked_sources import fixture


def ordinary_picker(tmp_path):
    f = fixture(tmp_path)
    chosen = tmp_path / 'ordinary-input'
    chosen.mkdir()
    (chosen / 'selected.txt').write_bytes(b'selected fixture')
    native = f.helper.call
    async def helper(authority, method, args, **kwargs):
        if method == 'dialog.folder':
            return str(chosen)
        return await native(authority, method, args, **kwargs)
    f.helper.call = helper
    return f, chosen


def replace_directory(path):
    path.rename(path.with_name(path.name + '-selected-original'))
    path.mkdir()
    (path / 'unselected.txt').write_bytes(b'unselected replacement fixture')


def test_native_selection_replaced_after_issue_is_rejected_before_snapshot_copy(tmp_path):
    f, chosen = ordinary_picker(tmp_path)
    issue = f.controller.grants.issue
    def swap_after_issue(authority, path, purpose, kind):
        selection = issue(authority, path, purpose, kind)
        if Path(path) == chosen:
            replace_directory(chosen)
        return selection
    f.controller.grants.issue = swap_after_issue
    response = f.client.post('/api/local-host/dialog/folder', headers=f.headers,
                             json={'purpose': 'dataset-source'})
    assert response.status_code == 422
    assert not list(f.project.glob('.local-host-imports/*/payload/unselected.txt'))
    assert not (f.project / '.local-host-imports').exists()
    assert (chosen / 'unselected.txt').read_bytes() == b'unselected replacement fixture'


def test_consumed_selection_replaced_before_worker_is_not_read_or_retried(tmp_path):
    f, chosen = ordinary_picker(tmp_path)
    consume = f.controller.grants.consume
    def swap_after_consume(authority, tokens):
        consume(authority, tokens)
        replace_directory(chosen)
    f.controller.grants.consume = swap_after_consume
    response = f.client.post('/api/local-host/dialog/folder', headers=f.headers,
                             json={'purpose': 'dataset-source'})
    assert response.status_code == 422
    assert not (f.project / '.local-host-imports').exists()
    assert not f.controller.grants.rows
    assert len([method for _, method, _ in f.helper.calls if method == 'host.approve']) == 1
    assert (chosen.with_name(chosen.name + '-selected-original') / 'selected.txt').read_bytes() == b'selected fixture'


@pytest.mark.parametrize('replacement', ['source', 'child'])
def test_replacing_directory_at_enumeration_never_reads_replacement_bytes(tmp_path, monkeypatch, replacement):
    from backend.local_host.staging import stage_selection
    project = tmp_path / 'project'; project.mkdir()
    source = tmp_path / 'source'; source.mkdir()
    child = source / 'class'; child.mkdir()
    (child / 'selected.txt').write_bytes(b'selected fixture')
    target = source if replacement == 'source' else child
    target_identity = (target.stat().st_dev, target.stat().st_ino)
    scandir = os.scandir; read = os.read
    replaced = []; content = []
    def swap_at_enumeration(path):
        info = os.fstat(path) if isinstance(path, int) else Path(path).stat()
        if (info.st_dev, info.st_ino) == target_identity and not replaced:
            replace_directory(target)
            replaced.append(True)
        return scandir(path)
    def observe_read(fd, size):
        block = read(fd, size)
        content.append(block)
        return block
    monkeypatch.setattr(os, 'scandir', swap_at_enumeration)
    monkeypatch.setattr(os, 'read', observe_read)
    with pytest.raises((ValueError, OSError)):
        stage_selection(source, project, 'dataset-source')
    assert replaced
    assert b'unselected replacement fixture' not in content
    assert not list(project.glob('.local-host-imports/*/payload/**/unselected.txt'))
    assert (target / 'unselected.txt').read_bytes() == b'unselected replacement fixture'


def test_successful_native_selection_issues_one_use_snapshot_grant(tmp_path):
    f, chosen = ordinary_picker(tmp_path)
    response = f.client.post('/api/local-host/dialog/folder', headers=f.headers,
                             json={'purpose': 'dataset-source'})
    assert response.status_code == 200, response.text
    result = response.json(); staged = Path(result['path'])
    assert result['staged'] is True
    assert (staged / 'selected.txt').read_bytes() == (chosen / 'selected.txt').read_bytes() == b'selected fixture'
    authority = f.controller.grants.rows[result['grant_id']]['scope']
    assert len(f.controller.grants.rows) == 1 and authority[0] == f.user['id']
    receipt = json.loads((staged.parent / 'snapshot.json').read_text())
    assert receipt['state'] == 'complete'
    assert receipt['files'][0]['sha256'] == '0c6318daaadf3f2f41ed8b86374af9511b60b52bb244ca7d7a91a006e8ce8b99'


@pytest.mark.parametrize('change', ['logout', 'labelset'])
def test_native_authority_changed_after_issue_refuses_before_source_read(tmp_path, change):
    f, chosen = ordinary_picker(tmp_path)
    issue = f.controller.grants.issue
    def change_after_issue(authority, path, purpose, kind):
        selection = issue(authority, path, purpose, kind)
        if Path(path) == chosen:
            if change == 'logout':
                f.app.state.accounts.logout(f.session['token'])
            else:
                config = dict(f.config); config['active_labelset_id'] = 'changed-labelset'
                (f.project / 'project.json').write_text(json.dumps(config))
        return selection
    f.controller.grants.issue = change_after_issue
    response = f.client.post('/api/local-host/dialog/folder', headers=f.headers,
                             json={'purpose': 'dataset-source'})
    assert response.status_code == (401 if change == 'logout' else 409)
    assert not (f.project / '.local-host-imports').exists()
    assert (chosen / 'selected.txt').read_bytes() == b'selected fixture'


def test_selection_replaced_at_worker_boundary_must_match_consumed_identity(tmp_path, monkeypatch):
    import asyncio
    f, chosen = ordinary_picker(tmp_path)
    to_thread = asyncio.to_thread
    async def swap_before_worker(function, *args, **kwargs):
        replace_directory(chosen)
        return await to_thread(function, *args, **kwargs)
    monkeypatch.setattr(asyncio, 'to_thread', swap_before_worker)
    response = f.client.post('/api/local-host/dialog/folder', headers=f.headers,
                             json={'purpose': 'dataset-source'})
    assert response.status_code == 422
    assert not (f.project / '.local-host-imports').exists()
    assert not f.controller.grants.rows
    assert (chosen / 'unselected.txt').read_bytes() == b'unselected replacement fixture'
