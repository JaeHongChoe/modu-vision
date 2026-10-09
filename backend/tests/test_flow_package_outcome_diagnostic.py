"""The original isolated-flow refusal keeps bounded observations, never receipt authority.

These controls use genuine package checksum/graph and original workspace guards.
Only the external worker outcome is controlled; no child, GPU or model is run.
Existing real CPU/model/Linux tests remain separate and unchanged.
"""
import hashlib
import json
from pathlib import Path

import pytest
from PIL import Image

from backend.engine import flow_package_runtime as flow
from backend.engine import runtime_deadline as runtime
from backend.engine.flowchart_engine import get_single_segmentation_flowchart


def verified_package(tmp_path):
    root=tmp_path/'package';root.mkdir()
    graph=get_single_segmentation_flowchart('diagnostic_fixture')
    # This byte fixture is checksum verified and never interpreted as weights.
    checkpoint=root/'models/diagnostic_fixture/best_model.pt';checkpoint.parent.mkdir(parents=True)
    checkpoint.write_bytes(b'controlled diagnostic checkpoint bytes; no model acceptance')
    (root/'pipeline.json').write_text(graph.model_dump_json(),encoding='utf-8')
    (root/'run_flow.py').write_text('# Verified inert runner; worker outcome seam only.\n',encoding='utf-8')
    files=[]
    for relative in ['pipeline.json','run_flow.py','models/diagnostic_fixture/best_model.pt']:
        raw=(root/relative).read_bytes()
        files.append({'path':relative,'size':len(raw),'sha256':hashlib.sha256(raw).hexdigest()})
    (root/'manifest.json').write_text(json.dumps({'schema_version':1,'files':files,
        'models':[{'job_id':'diagnostic_fixture','task':'segmentation','checkpoint':'models/diagnostic_fixture/best_model.pt'}]}),encoding='utf-8')
    image=tmp_path/'input.png';Image.new('RGB',(8,8),'gray').save(image)
    # The same original graph/checksum verifier runs again inside the caller.
    _,checkpoints=flow.verify_flow_package(root)
    assert checkpoints=={'diagnostic_fixture':checkpoint}
    return root,image


def snapshot(root):
    return {p.relative_to(root).as_posix():p.read_bytes()for p in root.rglob('*')if p.is_file()}


def observe_original_caller(tmp_path,monkeypatch,outcome):
    root,image=verified_package(tmp_path);before=snapshot(root);state={'calls':[],'finished':[],'output_reads':[]}
    finish=runtime._ProcessWorkspace.finished;read_text=Path.read_text
    def finished(workspace,value):
        state['finished'].append(value)
        return finish(workspace,value)
    def execute(command,**kwargs):
        state['calls'].append((command,kwargs));state['workspace']=Path(kwargs['cwd']);state['output']=Path(command[-1])
        # Even a parseable candidate result cannot turn a refused outcome into a receipt.
        state['output'].write_text(json.dumps({'final_verdict':'CONTROLLED_UNCONFIRMED'}),encoding='utf-8')
        return outcome
    def observed_read(path,*args,**kwargs):
        if path==state.get('output'):
            state['output_reads'].append(str(path))
        return read_text(path,*args,**kwargs)
    monkeypatch.setattr(runtime._ProcessWorkspace,'finished',finished)
    monkeypatch.setattr(runtime,'execute_owned_process',execute)
    monkeypatch.setattr(Path,'read_text',observed_read)
    options={'device':'cpu','deadline_ms':30000,'cpu_threads':1}
    return root,image,before,state,options


@pytest.mark.parametrize('outcome,expected_status,expected_code',[
    ({'status':'completed','returncode':17,'stdout':'','stderr':'controlled worker refusal'},'completed',17),
    ({'status':'uncertain','returncode':1,'leader_returncode':0,'rejection_reason':'OWNED_PROCESS_GROUP_UNRECONCILED',
      'stdout':'','stderr':'Owned process group remains unreconciled',
      'ownership':{'scope':'observed_original_process_group','leader_exit_confirmed':True,'registered_members':[{'pid':321}],
       'remaining_members':[321],'unknown_members':[654],'observation_failed':True,'termination_attempted':False,'termination_failed':False}},'uncertain',1),
    ({'status':'uncertain','returncode':1,'leader_returncode':None,'rejection_reason':'OWNED_PROCESS_GROUP_UNRECONCILED',
      'stdout':'','stderr':'Owned process group remains unreconciled','ownership':{'leader_exit_confirmed':False}},'uncertain',1),
    ({'status':'completed','returncode':-9,'stdout':'','stderr':'controlled signal refusal'},'completed',-9),
],ids=['nonzero-completed','uncertain-exited-leader','uncertain-live-leader','nonzero-signal'])
def test_original_nonzero_flow_outcome_refuses_unconfirmed_result_before_bounded_diagnostic(tmp_path,monkeypatch,outcome,expected_status,expected_code):
    root,image,before,state,options=observe_original_caller(tmp_path,monkeypatch,outcome)
    with pytest.raises(RuntimeError)as error:
        flow._run_isolated_admitted(root,image,'controlled-image',options,None,())
    # All original refusal/custody assertions precede the new causal missing-diagnostic assertion.
    assert len(state['calls'])==1 and state['finished']==[outcome]and state['finished'][0]is outcome
    command,kwargs=state['calls'][0]
    assert kwargs['deadline_ms']==30000 and kwargs['cancel_event']is None
    assert command[1:3]==['-I','-B']and command[-3]==str(root.resolve())
    assert state['output_reads']==[]and snapshot(root)==before
    assert str(error.value).startswith('Owned inference failed: '+outcome['stderr'])
    if expected_status=='uncertain':
        retention=json.loads((state['workspace']/'workspace-retention.json').read_text())
        assert retention['execution_attempt_started']is True and retention['automatic_cleanup_performed']is False
        assert retention['complete_process_tree_verified']is False and state['output'].is_file()
    assert '; diagnostic='in str(error.value),'Original isolated-flow caller discarded the refused owned outcome'
    encoded=str(error.value).split('; diagnostic=',1)[1];diagnostic=json.loads(encoded)
    assert len(encoded)<=1024 and diagnostic['status']==expected_status and diagnostic['returncode']==expected_code
    assert diagnostic['returncode_present']is True and diagnostic['diagnostic_only']is True
    assert diagnostic['release_qualified']is False and diagnostic['process_tree_exit_verified']is False
    assert diagnostic['kind']=='owned_flow_runtime_outcome_diagnostic'
    if expected_status=='uncertain':
        assert diagnostic['leader_returncode_present']is True and diagnostic['leader_returncode']==outcome['leader_returncode']
        assert diagnostic['ownership']['leader_exit_confirmed']is outcome['ownership']['leader_exit_confirmed']


@pytest.mark.parametrize('status',['timeout','cancelled'])
def test_original_timeout_cancel_preserves_exact_outcome_exception_without_diagnostic(tmp_path,monkeypatch,status):
    outcome={'status':status,'final_verdict':'REVIEW','deadline':{'terminated':True,'leader_exit_confirmed':True,'process_tree_exit_verified':False}}
    root,image,before,state,options=observe_original_caller(tmp_path,monkeypatch,outcome)
    def forbidden(value):raise AssertionError('Original timeout/cancel path may not enter nonzero failure formatter')
    monkeypatch.setattr(flow,'_owned_outcome_diagnostic',forbidden,raising=False)
    with pytest.raises(flow._UnconfirmedFlowOutcome)as error:
        flow._run_isolated_admitted(root,image,'controlled-image',options,None,())
    assert error.value.outcome is outcome and outcome['image_id']=='controlled-image'
    assert len(state['calls'])==1 and state['finished']==[outcome]and state['output_reads']==[]and snapshot(root)==before
    assert state['workspace'].is_dir()and not list(state['workspace'].iterdir())


def test_original_completed_flow_reads_only_original_result_without_failure_diagnostic(tmp_path,monkeypatch):
    outcome={'status':'completed','returncode':0,'stdout':'','stderr':'','pid':123,'elapsed_ms':4.0}
    root,image,before,state,options=observe_original_caller(tmp_path,monkeypatch,outcome)
    def forbidden(value):raise AssertionError('Successful original path may not enter failure formatter')
    monkeypatch.setattr(flow,'_owned_outcome_diagnostic',forbidden,raising=False)
    result=flow._run_isolated_admitted(root,image,'controlled-image',options,None,())
    assert len(state['calls'])==1 and state['finished']==[outcome]and state['output_reads']==[str(state['output'])]
    assert result['final_verdict']=='CONTROLLED_UNCONFIRMED'and result['runtime_execution']=={**options,'isolated_process':True,'pid':123,'elapsed_ms':4.0}
    assert 'diagnostic'not in result and snapshot(root)==before
    assert state['workspace'].is_dir()and not list(state['workspace'].iterdir())


def test_original_checksum_refusal_precedes_worker_and_diagnostic(tmp_path,monkeypatch):
    root,image=verified_package(tmp_path);(root/'run_flow.py').write_text('changed verified runner')
    def forbidden(*args,**kwargs):raise AssertionError('Changed package must refuse before execution or diagnostic')
    monkeypatch.setattr(runtime,'execute_owned_process',forbidden)
    monkeypatch.setattr(flow,'_owned_outcome_diagnostic',forbidden,raising=False)
    with pytest.raises(ValueError,match='checksum mismatch'):
        flow._run_isolated_admitted(root,image,None,{'device':'cpu','deadline_ms':30000,'cpu_threads':1},None,())


@pytest.mark.parametrize('invalid',[True,False,'1',1.0,float('nan'),float('inf'),2**100,-2**100,None])
def test_flow_diagnostic_never_coerces_invalid_codes_or_flags(invalid):
    encoded=flow._owned_outcome_diagnostic({'status':'uncertain','returncode':invalid,'leader_returncode':invalid,
        'ownership':{'leader_exit_confirmed':invalid,'observation_failed':invalid,'registered_members':'SECRET'},
        'deadline':{'terminated':invalid,'leader_exit_confirmed':invalid}})
    value=json.loads(encoded)
    assert value['returncode_present']is True and value['returncode']is None
    assert value['leader_returncode_present']is True and value['leader_returncode']is None
    assert value['ownership']['registered_member_count']is None and len(encoded)<=1024
    if type(invalid)is not bool:assert value['ownership']['leader_exit_confirmed']is None and value['deadline']['terminated']is None
    assert value['diagnostic_only']is True and value['process_tree_exit_verified']is False and value['release_qualified']is False


@pytest.mark.parametrize('code',[-2**31,2**32-1])
def test_flow_diagnostic_preserves_exact_bounded_integer_codes(code):
    value=json.loads(flow._owned_outcome_diagnostic({'status':'completed','returncode':code,'leader_returncode':code}))
    assert value['returncode']==value['leader_returncode']==code


@pytest.mark.parametrize('size',[0,65536,65537])
def test_flow_diagnostic_only_counts_exact_bounded_lists_without_inspecting_contents(size):
    class Hostile:
        def __str__(self):raise AssertionError('Private list contents must never be formatted')
        def __repr__(self):raise AssertionError('Private list contents must never be formatted')
    members=[Hostile()]*size
    encoded=flow._owned_outcome_diagnostic({'status':'uncertain','ownership':{'registered_members':members,'remaining_members':members,'unknown_members':members}})
    value=json.loads(encoded);expected=size if size<=65536 else None
    assert value['ownership']['registered_member_count']==value['ownership']['remaining_member_count']==value['ownership']['unknown_member_count']==expected
    assert len(encoded)<=1024 and value['process_tree_exit_verified']is False


def test_flow_diagnostic_ignores_foreign_hostile_private_values_and_authority_claims():
    class Hostile:
        def __str__(self):raise AssertionError('Unknown values must never be stringified')
        def __repr__(self):raise AssertionError('Unknown values must never be repr-ed')
    class ForeignDict(dict):
        def get(self,*args):raise AssertionError('Foreign mapping methods must never run')
    values=[Hostile(),ForeignDict(),{'status':Hostile(),'returncode':Hostile(),'ownership':ForeignDict(),'deadline':ForeignDict()},
        {'status':'SECRET /private/path','rejection_reason':'SECRET','ownership':{'scope':'SECRET','unknown_members':[Hostile()]},
         'stdout':'SECRET'*100000,'stderr':Hostile(),'pid':123,'private_diagnostics':'/private/SECRET','env':{'token':'SECRET'},
         'process_tree_exit_verified':True,'release_qualified':True}]
    for value in values:
        encoded=flow._owned_outcome_diagnostic(value);decoded=json.loads(encoded)
        assert len(encoded)<=1024 and 'SECRET'not in encoded and '/private'not in encoded
        assert not any(key in encoded for key in ['stdout','stderr','private_diagnostics','"pid"','"env"','"token"'])
        assert decoded['process_tree_exit_verified']is False and decoded['release_qualified']is False and decoded['diagnostic_only']is True
    assert json.loads(flow._owned_outcome_diagnostic({'status':'unexpected'}))['status']=='unrecognized'
