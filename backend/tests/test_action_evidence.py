"""Action evidence cannot inherit a whole case's pass without explicit bindings."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import pytest

ROOT=Path(__file__).resolve().parents[2]

def gate():
    file=ROOT/'scripts/check_action_evidence.py'
    assert file.is_file(), 'The action-level evidence validator is missing'
    spec=importlib.util.spec_from_file_location('action_evidence_gate',file)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module

def fixture(root):
    source='src/renderer/components/Owned.tsx';path=root/source
    path.parent.mkdir(parents=True);path.write_text('<button>Save</button>')
    test='scripts/e2e/owned.spec.ts::save then reopen';file=root/test.split('::')[0]
    file.parent.mkdir(parents=True);file.write_text("test('save then reopen',async()=>{});\n")
    record={'schema':'modu-vision.gui-execution/v1','source_sha':'a'*40,'source_dirty':False,'test':test,
        'test_source_sha256':hashlib.sha256(file.read_bytes()).hexdigest(),'mode':'electron',
        'platform':{'os':'darwin','arch':'arm64'},'status':'passed','expected_status':'passed',
        'result':'expected','attempts':1,'report_sha256':'b'*64,'harness_sha256':'c'*64,
        'artifacts':[{'id':'screen','sha256':'d'*64}]}
    receipt=root/'docs/verification/receipts/owned.json';receipt.parent.mkdir(parents=True)
    receipt.write_text(json.dumps(record))
    entry={'task':'S2-08','source_sha':'a'*40,'kind':'click','test':test,'receipt':receipt.name,
        'receipt_sha256':hashlib.sha256(receipt.read_bytes()).hexdigest(),'action':'Click Save',
        'expected':'Saved draft remains after reload','observed':'Native reload restored exact draft hash',
        'reviewer':'Controlled format reviewer'}
    scenarios={k:{'state':'pending'} for k in ('success','empty','invalid','error','cancel','reopen','handoff')}
    scenarios['success']={'state':'verified','evidence':[entry]}
    program={'requirements':[{'id':'S2-08'}],'legacy_coverage':[
        {'legacy_id':'U010','service_requirements':['S2-08']},
        {'legacy_id':'U012','service_requirements':['S2-08']}]}
    value={'schema_version':1,'kind':'curated_action_evidence_not_complete_feature_acceptance',
        'sources':{source:hashlib.sha256(path.read_bytes()).hexdigest()},'records':[
            {'id':'U010','actions':[{'id':'save','label':'Save draft','source':source,'scenarios':scenarios}],
             'remaining':'Other dynamic actions require execution'},
            {'id':'U012','actions':[],'remaining':'Action execution has not been reviewed'}]}
    return program,value,path

def test_exact_case_can_resolve_only_one_scenario(tmp_path):
    program,value,_=fixture(tmp_path);report=gate().check(program,value,tmp_path)
    assert report['ok'],report['errors']
    assert report['verified_scenarios']==1 and report['pending_scenarios']==6
    assert report['accepted_features']==0

@pytest.mark.parametrize('damage',['missing_id','duplicate_action','missing_scenario','unknown_owner','wrong_case','no_receipt','stale_source','unreviewed_waiver','blind_pass'])
def test_incomplete_or_unbound_action_claims_refuse(tmp_path,damage):
    program,value,path=fixture(tmp_path);action=value['records'][0]['actions'][0]
    entry=action['scenarios']['success']['evidence'][0]
    if damage=='missing_id':value['records'].pop()
    elif damage=='duplicate_action':value['records'][0]['actions'].append(copy.deepcopy(action))
    elif damage=='missing_scenario':action['scenarios'].pop('cancel')
    elif damage=='unknown_owner':entry['task']='S7-07'
    elif damage=='wrong_case':entry['test']='scripts/e2e/owned.spec.ts::never executed'
    elif damage=='no_receipt':entry.pop('receipt');entry.pop('receipt_sha256')
    elif damage=='stale_source':path.write_text('<button>Changed behavior</button>')
    elif damage=='unreviewed_waiver':action['scenarios']['empty']={'state':'not_required','reason':'Not applicable'}
    else:action['scenarios']['cancel']={'state':'verified','evidence':[]}
    assert not gate().check(program,value,tmp_path)['ok']
