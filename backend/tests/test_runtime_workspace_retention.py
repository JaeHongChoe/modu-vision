"""Retain helper-owned files while observed work is uncertain; no tree claim."""
import json
import os
from pathlib import Path

import pytest

from backend.engine import runtime_deadline as runtime
from backend.tests.test_runtime_deadline_sdk import real_package
from backend.tests.test_gan_source_composition import generator
from backend.tests.test_runtime_deadline_tree import owned_fixture, assert_live

pytestmark=pytest.mark.skipif(os.name!='posix',reason='Retained directory handle and actual group controls qualify POSIX only')

@pytest.mark.skipif(os.name!='posix',reason='Actual owned group fixture qualifies POSIX only')
@pytest.mark.parametrize('consumer',['flow','generator','conversion'])
def test_uncertain_consumer_keeps_private_request_and_candidate_with_live_member(
        consumer,real_package,generator,tmp_path,monkeypatch):
    package,image=real_package;observed={};owned_execute=runtime.execute_owned_process
    from backend.engine.flow_package_runtime import Predictor
    from backend.engine.gan_package_runtime import build_generator_package,GeneratorExecutor
    from backend.engine.openvino_runtime import optimize_flow_package
    output=tmp_path/'unpublished'
    executor=GeneratorExecutor(build_generator_package(generator,tmp_path/'gan-package'),deadline_ms=2000) if consumer=='generator' else None
    with owned_fixture(tmp_path/'survivor') as fixture:
        def transport(command,**kwargs):
            request=Path(command[-2]);result=Path(command[-1]);observed['workspace']=request.parent
            result.write_text(json.dumps({'final_verdict':'CONTROLLED','package_path':'controlled-report'}))
            payload=json.loads(request.read_text());staged=Path(payload['output_dir']) if consumer!='flow' else request.parent/'candidate'
            staged.mkdir();(staged/'controlled.txt').write_text('Owned unconfirmed candidate')
            observed['candidate']=staged/'controlled.txt'
            kwargs.update(cwd=fixture['directory'],deadline_ms=2000)
            observed['outcome']=owned_execute(fixture['command'],**kwargs)
            return observed['outcome']
        monkeypatch.setattr(runtime,'execute_owned_process',transport)
        with pytest.raises((RuntimeError,ValueError),match='failed'):
            if consumer=='flow':Predictor(package,deadline_ms=2000,cpu_threads=1).predict(image,'controlled')
            elif consumer=='generator':executor.execute({'output_dir':str(output),'count':1})
            else:optimize_flow_package(package,output_dir=output,validation_images=[image],cpu_threads=1)
        assert observed['workspace'].is_dir(),'Uncertain consumer deleted request/cache/candidate while original member remained'
        assert (observed['workspace']/'request.json').is_file() and observed['candidate'].is_file()
        _,activity=assert_live(fixture)
        assert observed['outcome']['status']=='uncertain' and observed['outcome']['returncode']==1
        assert activity and not output.exists()
        marker=json.loads((observed['workspace']/'workspace-retention.json').read_text())
        assert marker['status']=='recovery_required' and marker['complete_process_tree_verified'] is False
        fixture['stop']()


def test_private_workspace_cleans_after_confirmed_result_and_before_spawn_errors(tmp_path):
    from backend.engine.runtime_deadline import owned_process_workspace
    with owned_process_workspace(prefix='owned-positive-',directory=tmp_path) as workspace:
        path=workspace.path;workspace.started();workspace.finished({'status':'completed','returncode':0})
        (path/'result.json').write_text('{}')
    assert path.is_dir() and not list(path.iterdir())
    with pytest.raises(ValueError):
        with owned_process_workspace(prefix='owned-preflight-',directory=tmp_path) as workspace:
            path=workspace.path;raise ValueError('Controlled pre-spawn validation')
    assert path.is_dir() and not list(path.iterdir())


def test_started_interruption_preserves_original_error_and_private_files(tmp_path):
    from backend.engine.runtime_deadline import owned_process_workspace
    with pytest.raises(KeyboardInterrupt,match='Controlled interruption'):
        with owned_process_workspace(prefix='owned-interrupt-',directory=tmp_path) as workspace:
            path=workspace.path;(path/'request.json').write_text('{}');workspace.started()
            raise KeyboardInterrupt('Controlled interruption')
    assert (path/'request.json').read_text()=='{}'
    assert json.loads((path/'workspace-retention.json').read_text())['execution_attempt_started'] is True


def test_changed_workspace_identity_is_never_read_or_cleaned_as_original(tmp_path):
    from backend.engine.runtime_deadline import owned_process_workspace
    with pytest.raises(RuntimeError,match='identity'):
        with owned_process_workspace(prefix='owned-identity-',directory=tmp_path) as workspace:
            original=workspace.path;retained=tmp_path/'original-retained';workspace.started()
            original.rename(retained);original.mkdir();(original/'foreign.txt').write_text('Preserve replacement')
            workspace.finished({'status':'completed','returncode':0})
    assert retained.is_dir() and (original/'foreign.txt').read_text()=='Preserve replacement'
    assert not (original/'workspace-retention.json').exists()


@pytest.mark.parametrize('deadline',[
    {},{'terminated':True},{'leader_exit_confirmed':True},
    {'terminated':True,'leader_exit_confirmed':1},
])
def test_unconfirmed_timeout_retains_private_files(tmp_path,deadline):
    from backend.engine.runtime_deadline import owned_process_workspace
    with owned_process_workspace(prefix='owned-unconfirmed-',directory=tmp_path) as workspace:
        path=workspace.path;(path/'request.json').write_text('{}');workspace.started()
        workspace.finished({'status':'timeout','deadline':deadline})
    assert (path/'request.json').read_text()=='{}'
    assert json.loads((path/'workspace-retention.json').read_text())['automatic_cleanup_performed'] is False


def test_confirmed_timeout_cleans_only_its_private_workspace(tmp_path):
    from backend.engine.runtime_deadline import owned_process_workspace
    with owned_process_workspace(prefix='owned-confirmed-',directory=tmp_path) as workspace:
        path=workspace.path;workspace.started()
        workspace.finished({'status':'timeout','deadline':{'terminated':True,'leader_exit_confirmed':True}})
    assert path.is_dir() and not list(path.iterdir())


@pytest.mark.parametrize('symlink',[False,True])
def test_existing_recovery_marker_is_never_overwritten(tmp_path,symlink):
    from backend.engine.runtime_deadline import owned_process_workspace
    destination=tmp_path/'prior-recovery.json';destination.write_text('Prior recovery evidence')
    with owned_process_workspace(prefix='owned-existing-',directory=tmp_path) as workspace:
        path=workspace.path;marker=path/'workspace-retention.json'
        if symlink:marker.symlink_to(destination)
        else:marker.write_text('Prior recovery evidence')
        workspace.started()
    assert path.is_dir() and marker.read_text()==destination.read_text()=='Prior recovery evidence'
    assert marker.is_symlink() is symlink


def test_unavailable_retained_directory_handle_authorizes_no_cleanup_or_marker(tmp_path):
    """Controlled no-handle policy, not a native Windows filesystem test."""
    from backend.engine.runtime_deadline import owned_process_workspace
    with owned_process_workspace(prefix='owned-no-handle-',directory=tmp_path) as workspace:
        path=workspace.path;workspace.started()
        os.close(workspace._fd);workspace._fd=None
        workspace.finished({'status':'completed','returncode':0})
    assert path.is_dir() and not (path/'workspace-retention.json').exists()


@pytest.mark.parametrize('completed',[False,True])
def test_check_to_use_replacement_never_writes_or_cleans_foreign_tree(tmp_path,monkeypatch,completed):
    from backend.engine.runtime_deadline import owned_process_workspace
    foreign=tmp_path/'foreign';foreign.mkdir();(foreign/'foreign.txt').write_text('Foreign bytes')
    retained=tmp_path/'original-retained'
    with owned_process_workspace(prefix='owned-race-',directory=tmp_path) as workspace:
        original=workspace.path;(original/'request.json').write_text('Original bytes');workspace.started()
        if completed:workspace.finished({'status':'completed','returncode':0})
        matches=workspace._matches
        def replace_after_check():
            result=matches();original.rename(retained)
            if completed:
                original.mkdir();(original/'foreign.txt').write_text('Foreign replacement bytes')
            else:original.symlink_to(foreign,target_is_directory=True)
            return result
        monkeypatch.setattr(workspace,'_matches',replace_after_check)
    assert (foreign/'foreign.txt').read_text()=='Foreign bytes'
    assert not (foreign/'workspace-retention.json').exists()
    if completed:
        assert (original/'foreign.txt').read_text()=='Foreign replacement bytes'
        assert retained.is_dir() and not list(retained.iterdir())
    else:
        assert (retained/'request.json').read_text()=='Original bytes'
        assert json.loads((retained/'workspace-retention.json').read_text())['status']=='recovery_required'
