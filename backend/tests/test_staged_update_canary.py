"""Preactivation app/DB publication controls; owned disposable POSIX fixtures only."""
import json
from pathlib import Path
from dataclasses import replace
import hashlib
import os
import zipfile

import pytest

from backend.tests.test_global_migration import owned
from backend.tests.test_service_s6_04 import fixture


def plan(root,value):
    from backend.engine.runtime_update import plan_update
    return plan_update(root,value['directory'],value['envelope'],value['authority'],
        pinned_authority_sha256=value['pinned_authority_sha256'],target=value['target'])


def pointers(root):
    return {name:(root/name).read_bytes() if (root/name).exists() else None
            for name in ('global-active.json','application-active.json')}


CONTROL_SPEC={'workspace_id':'1'*32,'project_id':'2'*32,'plan_sha256':'3'*64}
CONTROL_CAPABILITY_SHA='0'*64


def control_preflight(root,manifest,spec,capability_sha,*,archive=None):
    from backend.engine.staged_update_canary import FLAGS
    return {'schema_version':1,'protocol':1,'required':True,'policy':'same_reviewed_source_runtime_worker_v1',
        'status':'source_ready','supported':True,'pins':spec,'capability_sha256':capability_sha,
        'candidate_runtime_source_sha256':'7'*64,'reason':None,**{name:False for name in FLAGS}}


def controlled_proof(monkeypatch,*,after=None,damage=None):
    """Test-only pointer/fault provider. No source worker/native acceptance proof."""
    from backend.engine import staged_update_canary as canary,runtime_update as update
    calls=[]
    def provide(root,record,database,manifest,requirement_sha):
        calls.append(database['migration_id']);directory=canary._directory(root,record)
        (directory/'private').mkdir()
        intent={'schema_version':1,'kind':'staged_update_canary_intent','requirement_sha256':requirement_sha,
            'nonce':'4'*32,'epoch':'5'*32,'status':'spawn_started'}
        intent_sha=canary._write_sealed(directory/canary.INTENT,intent)
        result={'requirement_sha256':requirement_sha,'nonce':intent['nonce'],'epoch':intent['epoch'],
            'semantic_output_sha256':'6'*64,'runtime_source_sha256':'7'*64}
        result_sha=canary._write_sealed(directory/'canary-result.json',result)
        receipt={'schema_version':1,'kind':'staged_update_canary_receipt','status':'verified',
            'execution_scope':'staged_source_runtime_worker','requirement_sha256':requirement_sha,
            'nonce':intent['nonce'],'epoch':intent['epoch'],'intent_sha256':intent_sha,
            'result_sha256':result_sha,'semantic_output_sha256':'6'*64,'runtime_source_sha256':'7'*64,
            **{name:False for name in canary.FLAGS}}
        if damage:damage(receipt)
        canary._write_sealed(directory/canary.RECEIPT,receipt)
        if after:after(root,record,database,manifest)
    monkeypatch.setattr(canary,'execute_source_candidate',provide)
    monkeypatch.setattr(canary,'review_spec',lambda root,spec:CONTROL_CAPABILITY_SHA)
    monkeypatch.setattr(canary,'review_candidate',control_preflight)
    monkeypatch.setattr(canary,'validate_source_result',lambda root,record,manifest,receipt,result:None)
    return calls


def controlled_plan(root,value):
    proposal=plan(root,value)
    return replace(proposal,canary=dict(CONTROL_SPEC),canary_capability_sha256=CONTROL_CAPABILITY_SHA,
        canary_preflight=control_preflight(root,None,dict(CONTROL_SPEC),CONTROL_CAPABILITY_SHA))


def test_pre_spawn_dependency_failure_preserves_original_pair_and_can_abort(tmp_path,monkeypatch):
    from backend.engine.runtime_update import install_update,recover_update
    root,*_=owned(tmp_path);value=fixture(tmp_path);before=pointers(root)
    from backend.engine import staged_update_canary as canary
    monkeypatch.setattr(canary,'review_spec',lambda root,spec:CONTROL_CAPABILITY_SHA)
    monkeypatch.setattr(canary,'review_candidate',control_preflight)
    def unavailable(*args):raise canary.CanaryError('requires_target: controlled source dependency unavailable before spawn')
    monkeypatch.setattr(canary,'execute_source_candidate',unavailable)
    with pytest.raises(ValueError,match='requires_target'):
        install_update(root,controlled_plan(root,value))
    pending=json.loads((root/'application-update-pending.json').read_bytes())
    assert pointers(root)==before
    assert recover_update(root,pending['update_id'],action='abort')['status']=='aborted'
    assert pointers(root)==before


def test_exact_controlled_canary_is_required_before_normal_publication_and_retry(tmp_path,monkeypatch):
    from backend.engine.runtime_update import install_update,recover_update
    root,*_=owned(tmp_path);value=fixture(tmp_path);calls=controlled_proof(monkeypatch)
    result=install_update(root,controlled_plan(root,value));before=pointers(root)
    assert result['status']=='committed' and len(calls)==1
    assert recover_update(root,result['update_id'],action='finish')['status']=='committed'
    assert pointers(root)==before and len(calls)==1


@pytest.mark.parametrize('damage',[lambda value:value.update(native_application_verified=True),
                                  lambda value:value.update(unrecognized_authority=True),
                                  lambda value:value.update(requirement_sha256='8'*64)])
def test_foreign_or_overclaiming_receipt_never_publishes(tmp_path,monkeypatch,damage):
    from backend.engine.runtime_update import install_update
    root,*_=owned(tmp_path);value=fixture(tmp_path);before=pointers(root)
    controlled_proof(monkeypatch,damage=damage)
    with pytest.raises(ValueError,match='canary|Canary'):
        install_update(root,controlled_plan(root,value))
    assert pointers(root)==before


@pytest.mark.parametrize('where',['source','target','application'])
def test_original_source_target_or_application_change_after_math_refuses(tmp_path,monkeypatch,where):
    from backend.engine.runtime_update import install_update
    root,*_=owned(tmp_path);value=fixture(tmp_path);before=pointers(root)
    def drift(root,record,database,manifest):
        target=(root/'projects/labels.json' if where=='source' else
            root/'.global-generations'/database['migration_id']/'compute_profiles.json' if where=='target' else
            root/'.application-generations'/record['update_id']/'application'/manifest['entrypoint'])
        target.chmod(0o600);target.write_bytes(b'changed after controlled proof')
    controlled_proof(monkeypatch,after=drift)
    with pytest.raises(ValueError):install_update(root,controlled_plan(root,value))
    assert pointers(root)==before


def test_missing_sealed_proof_on_direct_recovery_cannot_execute_or_publish(tmp_path,monkeypatch):
    from backend.engine import runtime_update as update,global_migration as migration
    root,*_=owned(tmp_path);value=fixture(tmp_path);before=pointers(root)
    def stop(point):
        if point=='database_prepared':raise KeyboardInterrupt()
    monkeypatch.setattr(update,'_checkpoint',stop)
    controlled_proof(monkeypatch)
    with pytest.raises(KeyboardInterrupt):update.install_update(root,controlled_plan(root,value))
    pending=json.loads((root/update.PENDING).read_bytes());record,_=update._intent(root,pending['update_id'])
    with pytest.raises((ValueError,OSError)):
        migration.recover(root,record['migration_id'],action='finish')
    assert pointers(root)==before


def test_retained_attempt_never_guesses_no_spawn_from_missing_child(tmp_path,monkeypatch):
    from backend.engine import runtime_update as update,staged_update_canary as canary
    root,*_=owned(tmp_path);value=fixture(tmp_path);before=pointers(root)
    def interrupted(root,record,database,manifest,requirement_sha):
        canary._write_sealed(canary._directory(root,record)/canary.INTENT,
            {'schema_version':1,'kind':'staged_update_canary_intent','status':'spawn_started',
             'requirement_sha256':requirement_sha,'nonce':'a'*32,'epoch':'b'*32})
        raise OSError('controlled ambiguous spawn boundary')
    monkeypatch.setattr(canary,'execute_source_candidate',interrupted)
    monkeypatch.setattr(canary,'review_spec',lambda root,spec:CONTROL_CAPABILITY_SHA)
    monkeypatch.setattr(canary,'review_candidate',control_preflight)
    with pytest.raises(OSError):update.install_update(root,controlled_plan(root,value))
    pending=(root/update.PENDING).read_bytes();identifier=json.loads(pending)['update_id']
    with pytest.raises(ValueError,match='may have started'):
        update.recover_update(root,identifier,action='abort')
    assert pointers(root)==before and (root/update.PENDING).read_bytes()==pending


def test_prepared_abort_cannot_be_reactivated_by_standalone_migration_recovery(tmp_path,monkeypatch):
    from backend.engine import runtime_update as update,global_migration as migration
    root,*_=owned(tmp_path);value=fixture(tmp_path);before=pointers(root)
    identifier,record=prepare_legacy(root,value,monkeypatch)
    update.recover_update(root,identifier,action='abort')
    with pytest.raises(ValueError,match='canary|Canary'):
        migration.recover(root,record['migration_id'],action='finish')
    assert pointers(root)==before


def test_already_attempted_canary_cannot_reexecute_on_missing_receipt_recovery(tmp_path,monkeypatch):
    from backend.engine import runtime_update as update,staged_update_canary as canary
    root,*_=owned(tmp_path);value=fixture(tmp_path);before=pointers(root);calls=[]
    def interrupted(root,record,database,manifest,requirement_sha):
        calls.append(1)
        if len(calls)==1:
            canary._write_sealed(canary._directory(root,record)/canary.INTENT,
                {'schema_version':1,'kind':'staged_update_canary_intent','status':'spawn_started',
                 'requirement_sha256':requirement_sha,'nonce':'a'*32,'epoch':'b'*32})
        raise OSError('controlled unknown child/writer after spawn intent')
    monkeypatch.setattr(canary,'execute_source_candidate',interrupted)
    monkeypatch.setattr(canary,'review_spec',lambda root,spec:CONTROL_CAPABILITY_SHA)
    monkeypatch.setattr(canary,'review_candidate',control_preflight)
    with pytest.raises(OSError):update.install_update(root,controlled_plan(root,value))
    pending=json.loads((root/update.PENDING).read_bytes())
    with pytest.raises((ValueError,OSError)):update.recover_update(root,pending['update_id'],action='finish')
    assert calls==[1]
    assert pointers(root)==before


def test_retry_does_not_rebind_changed_receipt_bytes_even_if_json_value_is_identical(tmp_path,monkeypatch):
    from backend.engine import runtime_update as update,staged_update_canary as canary
    root,*_=owned(tmp_path);value=fixture(tmp_path);before=pointers(root)
    controlled_proof(monkeypatch)
    def stop(point):
        if point=='canary_verified':raise KeyboardInterrupt()
    monkeypatch.setattr(update,'_checkpoint',stop)
    with pytest.raises(KeyboardInterrupt):update.install_update(root,controlled_plan(root,value))
    pending=json.loads((root/update.PENDING).read_bytes());identifier=pending['update_id']
    record,directory=update._intent(root,identifier);path=canary._directory(root,record)/canary.RECEIPT
    raw=path.read_bytes();path.chmod(0o600);path.write_bytes(raw+b'\n')
    journal_before=(directory/'journal.json').read_bytes()
    monkeypatch.setattr(update,'_checkpoint',lambda _:None)
    with pytest.raises(ValueError,match='receipt|Receipt'):
        update.recover_update(root,identifier,action='finish')
    assert (directory/'journal.json').read_bytes()==journal_before
    assert pointers(root)==before


def test_bare_database_advance_cannot_bypass_canary_for_an_installed_application(tmp_path,monkeypatch):
    from backend.engine import runtime_update as update,global_migration as migration
    root,*_=owned(tmp_path);value=fixture(tmp_path);controlled_proof(monkeypatch)
    update.install_update(root,controlled_plan(root,value));before=pointers(root)
    source=migration.preview_forward(root)
    with pytest.raises(ValueError,match='canary|Canary'):
        migration.advance(root,expected_source_sha256=source['source_sha256'])
    assert pointers(root)==before


def test_explicit_canary_plan_must_be_registered_and_pinned_during_review(tmp_path):
    from backend.engine.runtime_update import plan_update
    root,*_=owned(tmp_path);value=fixture(tmp_path);before=pointers(root)
    with pytest.raises(ValueError,match='canary|Canary|plan|registered|requires_target'):
        plan_update(root,value['directory'],value['envelope'],value['authority'],
            pinned_authority_sha256=value['pinned_authority_sha256'],target=value['target'],canary=CONTROL_SPEC)
    assert pointers(root)==before
    assert not (root/'.application-updates').exists()


def prepare_legacy(root, value, monkeypatch):
    """Actual retained protocol1 bytes from the pre-canary update state machine."""
    from backend.engine import runtime_update as update
    import uuid
    proposal=plan(root,value);identifier=uuid.uuid4().hex
    directory=root/update.UPDATES/identifier;directory.mkdir(parents=True)
    update._bundle(proposal.bundle,proposal.app,destination=directory/'bundle')
    update._write_raw(directory/'envelope.json',update._read(proposal.envelope))
    generation=root/update.GENERATIONS/identifier;generation.mkdir(parents=True)
    installer=next(row for row in proposal.app['artifacts'] if row['kind']=='installer')
    update._portable(directory/'bundle'/installer['path'],proposal.app,destination=generation/'application')
    _,owner=update._root(root)
    record={'schema_version':1,'installation_id':owner['installation_id'],'update_id':identifier,
        'application_generation':identifier,'status':'staged','authority_path':proposal.authority,
        'authority_sha256':proposal.authority_sha256,'envelope_sha256':proposal.envelope_sha256,'target':proposal.target,
        'release':proposal.app,'source_sha256':proposal.source_sha256,'previous_database':None,
        'previous_application':None,'migration_id':None,'database_pointer':None,'recovery_history':[]}
    update._write(directory/'journal.json',record);update._pending(root,record)
    def stop(database):
        record.update(status='database_prepared',migration_id=database['migration_id'])
        update._write(directory/'journal.json',record)
        raise KeyboardInterrupt('controlled legacy preparation before any pointer')
    with pytest.raises(KeyboardInterrupt):
        update.migration.apply(root,expected_source_sha256=proposal.source_sha256,on_prepared=stop)
    return identifier,record


def test_new_app_activation_without_explicit_canary_refuses_before_pointer(tmp_path):
    from backend.engine.runtime_update import install_update
    root,*_=owned(tmp_path);value=fixture(tmp_path);before=pointers(root)
    with pytest.raises(ValueError,match='canary'):
        install_update(root,plan(root,value))
    assert pointers(root)==before
    assert not (root/'.application-updates').exists()


def test_direct_database_recovery_cannot_publish_unverified_application_candidate(tmp_path,monkeypatch):
    from backend.engine.global_migration import recover
    root,*_=owned(tmp_path);value=fixture(tmp_path);before=pointers(root)
    _,record=prepare_legacy(root,value,monkeypatch)
    with pytest.raises(ValueError,match='canary'):
        recover(root,record['migration_id'],action='finish')
    assert pointers(root)==before


def test_legacy_prepared_without_spawn_can_abort_with_original_pair_unchanged(tmp_path,monkeypatch):
    from backend.engine.runtime_update import recover_update
    root,*_=owned(tmp_path);value=fixture(tmp_path);before=pointers(root)
    identifier,_=prepare_legacy(root,value,monkeypatch)
    result=recover_update(root,identifier,action='abort')
    assert result['status']=='aborted'
    assert pointers(root)==before
    assert not (root/'application-update-pending.json').exists()
    assert list((root/'.global-generations').iterdir())


def test_standalone_database_migration_has_no_application_canary_dependency(tmp_path):
    from backend.engine.global_migration import apply,preview,recover
    root,*_=owned(tmp_path)
    result=apply(root,expected_source_sha256=preview(root)['source_sha256'])
    assert result['status']=='applied'
    assert recover(root,result['migration_id'],action='finish')['status']=='applied'
    assert not (root/'application-active.json').exists()


EXPECTED_OUTPUT={'final_verdict':'OK','roi_count':1,'defective_roi_count':0,
                 'routed_output_node_id':'output','recognized_texts':['A']}


def source_candidate(tmp_path,*,semantic=None,deadline_ms=30000,source_damage=None):
    """Pinned real SmallCTCOCR; signed inert main plus the exact source candidate tree."""
    import numpy as np
    import torch
    from PIL import Image
    from backend.engine.ocr import SmallCTCOCR
    from backend.engine.flow_package import build_flow_package
    from backend.engine.flowchart_engine import FlowNode,FlowNodeData,FlowEdge,FlowchartPipeline
    from backend.engine import application_launch_execution as execution,runtime_update as update
    repository=Path(__file__).resolve().parents[2]
    root,_,_,registry,*_=owned(tmp_path);value=fixture(tmp_path)
    project_id='a'*32;project=root/'projects'/'staged-canary';project.mkdir()
    manifest={'id':project_id,'project_dir':str(project)}
    (project/'project.json').write_bytes(update._canonical(manifest));registry.register_project(manifest)
    modeldir=project/'model';modeldir.mkdir();checkpoint=modeldir/'best_model.pt'
    model=SmallCTCOCR(1)
    for parameter in model.parameters():parameter.data.zero_()
    model.head.bias.data[1]=20
    torch.save({'task':'ocr','version':1,'alphabet':'A','image_size':[32,64],
        'model_state_dict':model.state_dict(),'dataset_provenance':{'dataset_sha256':'controlled'},'best_epoch':1},checkpoint)
    nodes=[FlowNode(id=name,position={},data=FlowNodeData(label=name,node_type=kind,
        task='ocr' if name=='ocr' else None,model_job_id='b'*32 if name=='ocr' else None,
        params={'regex':'^A$'} if name=='ocr' else {}))
        for name,kind in [('input','input'),('ocr','inspection'),('decision','decision'),('output','output')]]
    graph=FlowchartPipeline(nodes=nodes,edges=[FlowEdge(id='e'+str(i),source=nodes[i].id,target=nodes[i+1].id) for i in range(3)])
    delivery=project/'delivery'/'launch-known-image';delivery.mkdir(parents=True)
    package=Path(build_flow_package(pipeline=graph,checkpoints={'b'*32:checkpoint},output_base_dir=delivery,package_name='package')['package_path'])
    image=delivery/'input.png';Image.fromarray(np.full((32,64,3),127,np.uint8)).save(image)
    digest=lambda path:hashlib.sha256(path.read_bytes()).hexdigest()
    reviewed={'schema_version':1,'kind':'owned_cpu_ocr_known_image_plan','workspace_id':registry.workspace_id,
        'project_id':project_id,'project_manifest_sha256':digest(project/'project.json'),
        'package_path':execution.PACKAGE,'package_manifest_sha256':digest(package/'manifest.json'),
        'graph_sha256':digest(package/'pipeline.json'),'checkpoints':[{'job_id':'b'*32,'sha256':digest(package/'models'/('b'*32)/'best_model.pt')}],
        'input_path':execution.INPUT,'input_sha256':digest(image),'semantic_output_sha256':update._sha(update._canonical(semantic or EXPECTED_OUTPUT)),
        'device':'cpu','cpu_threads':1,'deadline_ms':deadline_ms,'release_policy':None,
        'runtime_source_sha256':execution.runtime_source_identity()}
    path=project/execution.PLAN;path.write_bytes(update._canonical(reviewed))
    files={'bin/app':b'#!/bin/sh\nexit 0\n'}
    for path in sorted((repository/'backend').rglob('*.py')):
        if 'tests' not in path.relative_to(repository/'backend').parts:
            files[path.relative_to(repository).as_posix()]=path.read_bytes()
    if source_damage:source_damage(files)
    app={'schema_version':1,'version':'1.0.0','platform':value['target']['platform'],'arch':value['target']['arch'],
        'entrypoint':'bin/app','files':[{'path':name,'size':len(raw),'sha256':update._sha(raw),'executable':name=='bin/app'} for name,raw in files.items()]}
    archive=value['directory']/'application.zip'
    with zipfile.ZipFile(archive,'w') as writer:
        writer.writestr('portable-application.json',update._canonical(app))
        for name,raw in files.items():writer.writestr(name,raw)
    value['payload'].update(sha256=digest(archive),size=archive.stat().st_size)
    value['payload']['artifacts'][0].update(sha256=value['payload']['sha256'],size=value['payload']['size']);value['sign'](value['payload'])
    spec={'workspace_id':registry.workspace_id,'project_id':project_id,'plan_sha256':digest(project/execution.PLAN)}
    proposal=update.plan_update(root,value['directory'],value['envelope'],value['authority'],
        pinned_authority_sha256=value['pinned_authority_sha256'],target=value['target'],canary=spec)
    return root,value,proposal,project,reviewed


def test_actual_staged_cpu_math_precedes_both_original_pointers_and_uses_candidate_copy(tmp_path,monkeypatch):
    from backend.engine import runtime_update as update,staged_update_canary as canary
    root,value,proposal,project,reviewed=source_candidate(tmp_path);before=pointers(root)
    original_project=(project/'project.json').read_bytes();observed=[]
    def checkpoint(point):
        if point=='canary_verified':
            assert pointers(root)==before
            pending=json.loads((root/update.PENDING).read_bytes());record,_=update._intent(root,pending['update_id'])
            attempt=canary._directory(root,record);receipt=json.loads((attempt/canary.RECEIPT).read_bytes())
            observed.append(receipt)
    monkeypatch.setattr(update,'_checkpoint',checkpoint)
    result=update.install_update(root,proposal)
    assert result['status']=='committed' and len(observed)==1
    assert all(observed[0][name] is False for name in canary.FLAGS)
    record,_=update._intent(root,result['update_id']);attempt=canary._directory(root,record)
    proof=json.loads((attempt/'canary-result.json').read_bytes())
    assert proof['semantic_output']==EXPECTED_OUTPUT
    assert proof['runtime_source_sha256']==reviewed['runtime_source_sha256']
    assert proof['worker']['runtime_helper_path']==str(attempt/'private'/'candidate-runtime'/'backend/engine/flow_package_runtime.py')
    assert proof['flow_result']['runtime_execution']['device']=='cpu'
    assert proof['flow_result']['runtime_execution']['cpu_threads']==1
    assert proof['worker']['environment']['CUDA_VISIBLE_DEVICES']==''
    assert proof['worker']['environment']['NVIDIA_VISIBLE_DEVICES']=='none'
    assert proof['worker']['environment']['HOME']==str(attempt/'private/home')
    assert (project/'project.json').read_bytes()==original_project
    assert not (project/'delivery/launch-known-image/results').exists()


@pytest.mark.parametrize('failure',['semantic','timeout'])
def test_actual_bad_math_or_deadline_keeps_original_pair_and_never_reexecutes(tmp_path,failure):
    from backend.engine import runtime_update as update,staged_update_canary as canary
    root,_,proposal,_,_=source_candidate(tmp_path,
        semantic={**EXPECTED_OUTPUT,'recognized_texts':['B']} if failure=='semantic' else None,
        deadline_ms=1 if failure=='timeout' else 30000)
    before=pointers(root)
    with pytest.raises(ValueError,match='semantic|timed out'):
        update.install_update(root,proposal)
    pending=(root/update.PENDING).read_bytes();identifier=json.loads(pending)['update_id'];record,_=update._intent(root,identifier)
    attempt=canary._directory(root,record);intent=(attempt/canary.INTENT).read_bytes()
    assert pointers(root)==before and not (attempt/canary.RECEIPT).exists()
    with pytest.raises(ValueError,match='already attempted'):
        update.recover_update(root,identifier,action='finish')
    with pytest.raises(ValueError,match='may have started'):
        update.recover_update(root,identifier,action='abort')
    assert (root/update.PENDING).read_bytes()==pending and (attempt/canary.INTENT).read_bytes()==intent
    assert pointers(root)==before


def test_changed_candidate_source_is_an_actionable_preflight_refusal_without_staging(tmp_path):
    from backend.engine import runtime_update as update,staged_update_canary as canary
    root,_,proposal,_,_=source_candidate(tmp_path,source_damage=lambda files:
        files.update({'backend/engine/flow_package_runtime.py':files['backend/engine/flow_package_runtime.py']+b'\nraise RuntimeError("unreviewed candidate")\n'}))
    state=update.review_update(proposal)['preactivation_canary']
    assert state['status']=='requires_target' and state['supported'] is False
    assert 'changes' in state['reason'] and all(state[name] is False for name in canary.FLAGS)
    with pytest.raises(ValueError,match='requires_target'):update.install_update(root,proposal)
    assert not (root/'.application-updates').exists() and pointers(root)=={'global-active.json':None,'application-active.json':None}


def test_frozen_canary_refuses_before_any_staging_or_process_attempt(tmp_path,monkeypatch):
    from backend.engine import runtime_update as update,staged_update_canary as canary
    root,value,previous,_,_=source_candidate(tmp_path)
    monkeypatch.setattr(canary.sys,'frozen',True,raising=False)
    # A source process claiming frozen without the compiled inventory must be
    # refused during admission, before a staged operation can exist.
    with pytest.raises(ValueError,match='frozen'):
        proposal=update.plan_update(root,value['directory'],value['envelope'],value['authority'],
            pinned_authority_sha256=value['pinned_authority_sha256'],target=value['target'],
            canary=previous.canary)
        update.install_update(root,proposal)
    assert not (root/'.application-updates').exists()
    assert pointers(root)=={'global-active.json':None,'application-active.json':None}


def test_actual_cli_preview_and_install_bind_exact_explicit_canary_pins(tmp_path):
    import subprocess,sys
    from backend.engine import runtime_update as update
    root,value,proposal,_,_=source_candidate(tmp_path);repository=Path(__file__).resolve().parents[2]
    args=['--root',str(root),'--bundle',str(value['directory']),'--envelope',str(value['envelope']),
        '--authority',str(value['authority']),'--pinned-authority-sha256',value['pinned_authority_sha256'],
        '--target-json',json.dumps(value['target']),
        '--canary-workspace-id',proposal.canary['workspace_id'],'--canary-project-id',proposal.canary['project_id'],
        '--canary-plan-sha256',proposal.canary['plan_sha256']]
    preview=subprocess.run([sys.executable,'-m','backend.engine.runtime_update','preview',*args],cwd=repository,
        capture_output=True,text=True,timeout=30)
    assert preview.returncode==0,preview.stdout+preview.stderr
    reviewed=json.loads(preview.stdout);state=reviewed['preactivation_canary']
    assert state['status']=='source_ready' and state['pins']==proposal.canary
    assert state['protocol']==1 and state['policy']=='same_reviewed_source_runtime_worker_v1'
    assert pointers(root)=={'global-active.json':None,'application-active.json':None}
    result=subprocess.run([sys.executable,'-m','backend.engine.runtime_update','install',*args,
        '--expected-plan-sha256',reviewed['plan_sha256']],cwd=repository,capture_output=True,text=True,timeout=50)
    assert result.returncode==0,result.stdout+result.stderr
    assert json.loads(result.stdout)['status']=='committed'


def test_malformed_graph_is_structured_preflight_refusal_with_no_process_attempt(tmp_path):
    from backend.engine import runtime_update as update
    root,value,_,project,reviewed=source_candidate(tmp_path)
    package=project/'delivery/launch-known-image/package';graph=package/'pipeline.json';graph.write_bytes(update._canonical({'nodes':[None,None,None,None],'edges':[{}, {}, {}]}))
    manifest=json.loads((package/'manifest.json').read_bytes())
    for row in manifest['files']:
        if row['path']=='pipeline.json':row.update(size=graph.stat().st_size,sha256=hashlib.sha256(graph.read_bytes()).hexdigest())
    (package/'manifest.json').write_bytes(update._canonical(manifest))
    reviewed.update(graph_sha256=hashlib.sha256(graph.read_bytes()).hexdigest(),package_manifest_sha256=hashlib.sha256((package/'manifest.json').read_bytes()).hexdigest())
    plan_path=project/'delivery/launch-known-image/plan.json';plan_path.write_bytes(update._canonical(reviewed))
    proposal=update.plan_update(root,value['directory'],value['envelope'],value['authority'],
        pinned_authority_sha256=value['pinned_authority_sha256'],target=value['target'],
        canary={'workspace_id':reviewed['workspace_id'],'project_id':reviewed['project_id'],'plan_sha256':hashlib.sha256(plan_path.read_bytes()).hexdigest()})
    assert update.review_update(proposal)['preactivation_canary']['status']=='requires_target'
    with pytest.raises(ValueError):update.install_update(root,proposal)
    assert not (root/'.application-updates').exists()


def test_foreign_capsule_member_blocks_publication_instead_of_hiding_possible_activity(tmp_path,monkeypatch):
    from backend.engine import runtime_update as update,staged_update_canary as canary
    root,*_=owned(tmp_path);value=fixture(tmp_path);before=pointers(root)
    def foreign(root,record,database,manifest):
        (canary._directory(root,record)/'unknown-writer.json').write_bytes(b'{"pid":999999}')
    controlled_proof(monkeypatch,after=foreign)
    with pytest.raises(ValueError,match='member|activity|capsule'):
        update.install_update(root,controlled_plan(root,value))
    assert pointers(root)==before


def test_known_private_preflight_without_spawn_intent_can_abort_without_guessing_pid_exit(tmp_path,monkeypatch):
    from backend.engine import runtime_update as update,staged_update_canary as canary
    root,*_=owned(tmp_path);value=fixture(tmp_path);before=pointers(root)
    controlled_proof(monkeypatch)
    def failed_preflight(root,record,database,manifest,requirement_sha):
        private=canary._directory(root,record)/'private';private.mkdir()
        (private/'retained-incomplete-input').write_bytes(b'private retained bytes; no spawn attempted')
        raise ValueError('controlled preflight copy failure before any spawn intent')
    monkeypatch.setattr(canary,'execute_source_candidate',failed_preflight)
    with pytest.raises(ValueError,match='preflight'):update.install_update(root,controlled_plan(root,value))
    pending=json.loads((root/update.PENDING).read_bytes());identifier=pending['update_id']
    result=update.recover_update(root,identifier,action='abort')
    assert result['status']=='aborted' and pointers(root)==before


def test_direct_canary_worker_requires_original_exclusive_admission_before_source_read(tmp_path,monkeypatch):
    from backend.engine import runtime_update as update,staged_update_canary as canary,application_launch_execution as execution
    root,*_=owned(tmp_path);record={'update_id':'c'*32,'migration_id':'d'*32,'canary':dict(CONTROL_SPEC)}
    seen=[]
    def source_read(*args,**kwargs):
        seen.append(True);raise RuntimeError('original source reached without admission')
    monkeypatch.setattr(execution,'admit_plan',source_read)
    with pytest.raises(ValueError,match='exclusive'):
        canary.execute_source_candidate(root,record,{}, {}, 'f'*64)
    assert seen==[] and pointers(root)=={'global-active.json':None,'application-active.json':None}


def test_fixed_worker_environment_hides_cuda_and_nvidia_devices(tmp_path):
    from backend.engine.staged_update_canary import _worker_environment
    environment=_worker_environment(tmp_path/'home',tmp_path/'cache',tmp_path/'tmp')
    assert environment['CUDA_VISIBLE_DEVICES']==''
    assert environment['NVIDIA_VISIBLE_DEVICES']=='none'
    assert environment['OMP_NUM_THREADS']==environment['MKL_NUM_THREADS']=='1'


def resource_archive(tmp_path,*,count=2,change=None,materialize=True):
    from backend.engine import runtime_update as update
    files={'bin/app':b'#!/bin/sh\nexit 0\n',
        **{'resources/'+('r'*100)+str(i)+'.py':b'' for i in range(count-1)}}
    manifest={'schema_version':1,'version':'1.0.0','platform':'linux','arch':'x64','entrypoint':'bin/app',
        'files':[{'path':name,'size':len(raw),'sha256':update._sha(raw),'executable':name=='bin/app'} for name,raw in files.items()]}
    if change:change(manifest,files)
    archive=tmp_path/'resource-application.zip'
    with zipfile.ZipFile(archive,'w') as writer:
        writer.writestr('portable-application.json',update._canonical(manifest))
        for name,raw in files.items():
            if materialize or name=='bin/app':writer.writestr(name,raw)
    return archive,manifest


def test_hash_bound_empty_resource_materializes_but_empty_control_documents_stay_refused(tmp_path):
    from backend.engine import runtime_update as update
    archive,manifest=resource_archive(tmp_path);destination=tmp_path/'extracted'
    assert update._portable(archive,manifest,destination=destination)[0]==manifest
    row=manifest['files'][1];file=destination/row['path']
    assert file.read_bytes()==b'' and file.stat().st_mode&0o777==0o400
    update._check_file(file,row)
    document=tmp_path/'empty-control.json';document.write_bytes(b'')
    with pytest.raises(ValueError,match='bounded'):update._read(document)


@pytest.mark.parametrize('damage',['wrong_empty_hash','empty_executable','boolean_size','negative_size'])
def test_empty_resource_contract_refuses_unbound_or_executable_bytes_before_extract(tmp_path,damage):
    from backend.engine import runtime_update as update
    def change(manifest,files):
        row=manifest['files'][1]
        if damage=='wrong_empty_hash':row['sha256']='e'*64
        elif damage=='empty_executable':row['executable']=True
        elif damage=='boolean_size':row['size']=False
        else:row['size']=-1
    archive,manifest=resource_archive(tmp_path,change=change);destination=tmp_path/'extracted'
    with pytest.raises(ValueError):update._portable(archive,manifest,destination=destination)
    if damage!='wrong_empty_hash':assert not destination.exists()


def test_portable_reviewed_20000_regular_member_and_large_manifest_bound(tmp_path):
    from backend.engine import runtime_update as update
    archive,manifest=resource_archive(tmp_path,count=20000)
    assert 1024**2<len(update._canonical(manifest))<8*1024**2
    assert update._portable(archive,manifest)[0]==manifest


def test_portable_inventory_above_reviewed_member_cap_refuses_before_membership(tmp_path):
    from backend.engine import runtime_update as update
    archive,manifest=resource_archive(tmp_path,count=20001,materialize=False)
    with pytest.raises(ValueError,match='inventory'):update._portable(archive,manifest)


def test_application_manifest_above_eight_mebibytes_refuses_before_json_read(tmp_path):
    from backend.engine import runtime_update as update
    archive,manifest=resource_archive(tmp_path)
    with zipfile.ZipFile(archive,'w') as writer:
        writer.writestr('portable-application.json',b' '*(8*1024**2+1))
        writer.writestr('bin/app',b'x')
    with pytest.raises(ValueError,match='manifest.*excessive'):update._portable(archive,manifest)


@pytest.mark.parametrize('kind',['symlink','hardlink','fifo'])
def test_empty_resource_reader_retains_no_link_or_special_file_admission(tmp_path,kind):
    from backend.engine import runtime_update as update
    original=tmp_path/'original';original.write_bytes(b'');path=tmp_path/'resource'
    if kind=='symlink':path.symlink_to(original)
    elif kind=='hardlink':os.link(original,path)
    else:os.mkfifo(path)
    with pytest.raises(ValueError):update._check_file(path,{'size':0,'sha256':update._sha(b''),'executable':False})


@pytest.mark.parametrize('point',['after_canary_result','after_canary_receipt'])
def test_actual_canary_partial_publication_never_runs_a_second_worker(tmp_path,monkeypatch,point):
    from backend.engine import runtime_update as update,staged_update_canary as canary
    root,_,proposal,_,_=source_candidate(tmp_path);before=pointers(root);calls=[]
    original=canary.execute_source_candidate
    def execute(*args):calls.append(True);return original(*args)
    def crash(observed):
        if observed==point:raise KeyboardInterrupt('controlled private canary publication crash')
    monkeypatch.setattr(canary,'execute_source_candidate',execute)
    monkeypatch.setattr(canary,'_checkpoint',crash)
    with pytest.raises(KeyboardInterrupt):update.install_update(root,proposal)
    assert pointers(root)==before and calls==[True]
    identifier=json.loads((root/update.PENDING).read_bytes())['update_id']
    monkeypatch.setattr(canary,'_checkpoint',lambda observed:None)
    if point=='after_canary_receipt':
        assert update.recover_update(root,identifier,action='finish')['status']=='committed'
    else:
        with pytest.raises(ValueError,match='already attempted'):update.recover_update(root,identifier,action='finish')
        assert pointers(root)==before
    assert calls==[True]


@pytest.mark.parametrize('name',[
    'Owned CPU.app/Contents/Resources/backend/_internal/PIL/.dylibs/libXau.6.dylib',
    'Owned CPU.app/Contents/Resources/backend/_internal/libgrpc++.1.71.dylib',
    'Owned CPU.app/Contents/Resources/backend/_internal/pytz/zoneinfo/Etc/GMT+0',
    'Owned CPU.app/Contents/Resources/backend/_internal/bokeh/server/static/.eslintrc.js',
    'Owned CPU.app/Contents/Resources/backend/_internal/bokeh/server/static/js/@microsoft/fast.js',
    'Owned CPU.app/Contents/Resources/backend/_internal/bokeh/server/static/js/fast-components@2.30.6.js',
    'Owned CPU.app/Contents/Resources/backend/_internal/bokeh/server/static/js/carto@^9.0.20.js',
    'Owned Native.app/Contents/Frameworks/Electron Helper (GPU).app/Contents/Info.plist',
    'Owned Native.app/Contents/Frameworks/Electron Helper (GPU).app/Contents/MacOS/Electron Helper (GPU)',
    'Owned Native.app/Contents/Frameworks/Electron Helper (GPU).app/Contents/PkgInfo',
    'Owned Native.app/Contents/Frameworks/Electron Helper (Plugin).app/Contents/Info.plist',
    'Owned Native.app/Contents/Frameworks/Electron Helper (Plugin).app/Contents/MacOS/Electron Helper (Plugin)',
    'Owned Native.app/Contents/Frameworks/Electron Helper (Plugin).app/Contents/PkgInfo',
    'Owned Native.app/Contents/Frameworks/Electron Helper (Renderer).app/Contents/Info.plist',
    'Owned Native.app/Contents/Frameworks/Electron Helper (Renderer).app/Contents/MacOS/Electron Helper (Renderer)',
    'Owned Native.app/Contents/Frameworks/Electron Helper (Renderer).app/Contents/PkgInfo'])
def test_actual_compiled_ordinary_dot_and_plus_names_remain_hash_bound_archive_members(tmp_path,name):
    from backend.engine import runtime_update as update
    def change(manifest,files):
        files[name]=b'controlled actual basename fixture'
        manifest['files'].append({'path':name,'size':len(files[name]),'sha256':update._sha(files[name]),'executable':False})
    archive,manifest=resource_archive(tmp_path,change=change)
    assert update._portable(archive,manifest)[0]==manifest


@pytest.mark.parametrize('name',['.','..','','/absolute','a//b','a/./b','a/../b','a\\b','a:b',
    'a/b.','a/b ','a/CON','a/nul.txt','a/COM1','a/LPT9.data','a/'+('x'*161),'x'*241,
    '(leading)/file','a/(leading)','/root(escape)','a/../Electron Helper (GPU)',
    'a/./Electron Helper (GPU)','a/Electron Helper (GPU)\\escape',
    'a/Electron Helper (GPU):escape','a/Electron Helper (GPU).','a/Electron Helper (GPU) '])
def test_portable_ordinary_name_support_keeps_unsafe_path_boundaries(name):
    from backend.engine import runtime_update as update
    with pytest.raises(ValueError):update._safe_path(name)


def test_portable_name_length_limit_retains_exact_160_character_segment():
    from backend.engine import runtime_update as update
    assert update._safe_path('a/'+('x'*160))=='a/'+('x'*160)


# Diagnostic-only external-interface seam: the original source caller runs its
# original pre-proof refusal; no CPU worker, acceptance proof or runtime API runs.
def _source_canary_outcome_failure(tmp_path,monkeypatch,outcome):
    from types import SimpleNamespace
    from backend.engine import staged_update_canary as canary,runtime_deadline as runtime
    calls=[];attempt=tmp_path/'attempt';attempt.mkdir();project=tmp_path/'source-project';project.mkdir()
    canonical=lambda value:json.dumps(value,sort_keys=True,separators=(',',':')).encode()
    digest=lambda raw:hashlib.sha256(raw).hexdigest()
    specification={'workspace_id':'1'*32,'project_id':'2'*32,'plan_sha256':'3'*64}
    capability={'project_path':str(project),'plan':{'runtime_source_sha256':'4'*64,
        'release_policy':None,'project_manifest_sha256':'5'*64,'deadline_ms':30000}}
    def admitted(*args,**kwargs):
        calls.append('admit');destination=kwargs.get('copy_to')
        if destination:destination.mkdir(parents=True)
        return capability
    def write_raw(path,raw):
        path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw)
    def proof_read(*args):
        calls.append('proof_read');raise AssertionError('Failed outcome must not read or seal acceptance proof')
    updater=SimpleNamespace(_unlinked=lambda path:Path(path),_sha=digest,_canonical=canonical,
        _hex=lambda value,length=64:isinstance(value,str) and len(value)==length and all(c in '0123456789abcdef' for c in value),
        _write_raw=write_raw,GENERATIONS='generations',migration=SimpleNamespace(_sync_directories=lambda *args,**kwargs:None))
    execution=SimpleNamespace(admit_plan=admitted,_read=lambda *args,**kwargs:b'controlled input',PLAN='plan.json',MAX_RESULT=1024,_json=proof_read)
    monkeypatch.setattr(canary,'_require_exclusive',lambda root:calls.append('exclusive'))
    monkeypatch.setattr(canary,'_update',lambda:updater);monkeypatch.setattr(canary,'_execution',lambda:execution)
    monkeypatch.setattr(canary,'_directory',lambda *args:attempt)
    monkeypatch.setattr(canary,'_candidate_rows',lambda manifest:([],capability['plan']['runtime_source_sha256']))
    monkeypatch.setattr(canary,'_validate_private_inputs',lambda *args:calls.append('private_checked'))
    monkeypatch.setattr(canary,'_source_and_target',lambda *args:calls.append('source_checked'))
    monkeypatch.setattr(canary,'_checkpoint',lambda point:calls.append(point))
    def observed(command,**kwargs):
        calls.append(('execute',kwargs['deadline_ms']));return outcome
    monkeypatch.setattr(runtime,'execute_owned_process',observed)
    record={'canary':specification,'canary_capability_sha256':digest(canonical(capability)),'application_generation':'a'*32}
    with pytest.raises(canary.CanaryError)as error:
        canary.execute_source_candidate(tmp_path,record,{}, {}, '6'*64)
    assert calls.count(('execute',30000))==1 and calls[0]=='exclusive'
    assert 'before_canary_spawn'in calls and 'after_canary_math'not in calls and 'proof_read'not in calls
    assert (attempt/canary.INTENT).is_file() and not(attempt/canary.RECEIPT).exists()
    assert not(attempt/'canary-result.json').exists() and not(attempt/'private/worker-result.json').exists()
    # The original recovery-intent guard must refuse a second attempt; it cannot
    # use diagnostic text as a sealed receipt or new publication capability.
    requirement={'schema_version':1};requirement_sha=canary._write_sealed(attempt/canary.REQUIREMENT,requirement)
    monkeypatch.setattr(canary,'expected_requirement',lambda *args:requirement)
    monkeypatch.setattr(canary,'_document',lambda *args:(requirement,requirement_sha))
    monkeypatch.setattr(updater,'UPDATES','updates',raising=False)
    monkeypatch.setattr(updater,'_write',lambda *args:None,raising=False)
    record['update_id']='b'*32;record['canary_requirement_sha256']=requirement_sha
    with pytest.raises(canary.CanaryError,match='already attempted without sealed proof'):
        canary.ensure_verified(tmp_path,record,{}, {})
    assert calls.count(('execute',30000))==1 and not(attempt/canary.RECEIPT).exists()
    prefix='Canary CPU failed or timed out; retain recovery ownership, process-tree exit is unverified'
    assert str(error.value).startswith(prefix)
    return str(error.value)


@pytest.mark.parametrize('outcome,expected_status,returncode_present,expected_code',[
    ({'status':'completed','returncode':17,'stdout':'SECRET /private/canary','stderr':'SECRET credential'},'completed',True,17),
    ({'status':'uncertain','returncode':1,'leader_returncode':0,'rejection_reason':'OWNED_PROCESS_GROUP_UNRECONCILED',
        'ownership':{'scope':'observed_original_process_group','leader_exit_confirmed':True,'remaining_members':[123],
            'unknown_members':[456],'observation_failed':True,'termination_attempted':False,'termination_failed':False},
        'private_diagnostics':'/private/SECRET','stdout':'SECRET','stderr':'SECRET'},'uncertain',True,1),
    ({'status':'timeout','rejection_reason':'INFERENCE_DEADLINE_EXCEEDED',
        'deadline':{'terminated':True,'leader_exit_confirmed':True,'pid':123,'process_tree_exit_verified':False}},'timeout',False,None),
    ({'status':'cancelled','rejection_reason':'CANCELLED',
        'deadline':{'terminated':True,'leader_exit_confirmed':True,'pid':123}},'cancelled',False,None),
])
def test_source_canary_refusal_exposes_exact_bounded_outcome_without_reading_proof_or_retry(tmp_path,monkeypatch,outcome,expected_status,returncode_present,expected_code):
    message=_source_canary_outcome_failure(tmp_path,monkeypatch,outcome)
    assert '; diagnostic='in message,'Original source caller discarded the refused execution outcome'
    encoded=message.split('; diagnostic=',1)[1];diagnostic=json.loads(encoded)
    assert len(encoded)<=1024 and diagnostic['status']==expected_status
    assert diagnostic['returncode_present']is returncode_present and diagnostic['returncode']==expected_code
    assert diagnostic['diagnostic_only']is True and diagnostic['release_qualified']is False
    assert diagnostic['process_tree_exit_verified']is False
    assert not any(word in encoded for word in ('SECRET','/private','stdout','stderr','private_diagnostics','"pid"'))
    if expected_status=='uncertain':
        assert diagnostic['leader_returncode_present']is True and diagnostic['leader_returncode']==0
        assert diagnostic['ownership']['remaining_member_count']==diagnostic['ownership']['unknown_member_count']==1
        assert diagnostic['ownership']['observation_failed']is True
    else:
        assert diagnostic['ownership']['remaining_member_count']is None
        assert diagnostic['ownership']['unknown_member_count']is None


@pytest.mark.parametrize('invalid',[True,False,'1',1.0,2**100,-2**100,None])
def test_source_canary_diagnostic_never_coerces_invalid_codes_or_flags(invalid):
    from backend.engine import staged_update_canary as canary
    encoded=canary._source_outcome_diagnostic({'status':'uncertain','returncode':invalid,'leader_returncode':invalid,
        'ownership':{'leader_exit_confirmed':invalid,'observation_failed':invalid,'remaining_members':'SECRET'},
        'deadline':{'terminated':invalid,'leader_exit_confirmed':invalid}})
    diagnostic=json.loads(encoded);assert diagnostic['returncode_present']is True and diagnostic['returncode']is None
    assert diagnostic['leader_returncode_present']is True and diagnostic['leader_returncode']is None
    assert diagnostic['ownership']['remaining_member_count']is None and len(encoded)<=1024
    if type(invalid)is not bool:
        assert diagnostic['ownership']['leader_exit_confirmed']is None and diagnostic['deadline']['terminated']is None
    assert diagnostic['process_tree_exit_verified']is False and diagnostic['release_qualified']is False


def test_source_canary_diagnostic_ignores_hostile_nested_large_or_foreign_values():
    from backend.engine import staged_update_canary as canary
    class Hostile:
        def __str__(self):raise AssertionError('Diagnostic must not stringify unknown values')
        def __repr__(self):raise AssertionError('Diagnostic must not repr unknown values')
    class ForeignDict(dict):
        def get(self,*args):raise AssertionError('Diagnostic must not call foreign nested methods')
    values=[{'status':Hostile(),'returncode':Hostile(),'ownership':ForeignDict(),'deadline':ForeignDict()},
        {'status':'SECRET /private/path','rejection_reason':'SECRET','ownership':{'scope':'SECRET','unknown_members':[Hostile()]*65537}},
        {'status':'uncertain','ownership':Hostile(),'deadline':Hostile(),'stdout':'SECRET'*100000,'stderr':Hostile()},Hostile()]
    for value in values:
        encoded=canary._source_outcome_diagnostic(value);assert len(encoded)<=1024
        assert 'SECRET'not in encoded and '/private'not in encoded
        diagnostic=json.loads(encoded);assert diagnostic['process_tree_exit_verified']is False
        assert diagnostic['release_qualified']is False and diagnostic['diagnostic_only']is True
    assert json.loads(canary._source_outcome_diagnostic({'status':'unexpected'}))['status']=='unrecognized'
