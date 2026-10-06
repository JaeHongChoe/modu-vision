"""GUI reference integrity does not supply action truth or reviewer authority."""
import hashlib,importlib.util,json
from pathlib import Path
import pytest
ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('gui_source_gate',ROOT/'scripts/check_service_plan.py')
gate=importlib.util.module_from_spec(spec);spec.loader.exec_module(gate)
SHA='a'*40
TEST='scripts/e2e/owned.spec.ts::actual save and reopen'

def fixture(tmp_path):
    file=tmp_path/'scripts/e2e/owned.spec.ts';file.parent.mkdir(parents=True);file.write_text("test('actual save and reopen',async()=>{});\n")
    record={'schema':'modu-vision.gui-execution/v1','source_sha':SHA,'source_dirty':False,
        'test':TEST,'test_source_sha256':hashlib.sha256(file.read_bytes()).hexdigest(),
        'mode':'electron','platform':{'os':'darwin','arch':'arm64'},'status':'passed',
        'expected_status':'passed','result':'expected','attempts':1,
        'report_sha256':'b'*64,'harness_sha256':'c'*64,'artifacts':[{'id':'actual-screen','sha256':'d'*64}],
        'quality_approved':False,'independent_review_accepted':False}
    path=tmp_path/'docs/verification/receipts/gui.json';path.parent.mkdir(parents=True)
    entry={'kind':'click','source_sha':SHA,'test':TEST,'receipt':path.name}
    def write():path.write_text(json.dumps(record));entry['receipt_sha256']=hashlib.sha256(path.read_bytes()).hexdigest()
    write();return entry,record,file,write

def test_an_existing_gui_file_alone_cannot_verify_execution(tmp_path):
    entry,_,_,_=fixture(tmp_path);entry.pop('receipt');entry.pop('receipt_sha256')
    assert any('GUI execution receipt' in x for x in gate._reference_errors('F001.gui',entry,'click',tmp_path))

@pytest.mark.parametrize('damage',['failed','skipped','flaky','dirty','case','test_bytes','wrong_mode','unsigned_hash','no_screen','expected_failure'])
def test_claimed_pass_refuses_changed_or_nonexecuted_case(tmp_path,damage):
    entry,record,file,write=fixture(tmp_path)
    if damage=='failed':record['status']='failed'
    elif damage=='skipped':record['status']='skipped'
    elif damage=='flaky':record['attempts']=2
    elif damage=='dirty':record['source_dirty']=True
    elif damage=='case':record['test']='scripts/e2e/owned.spec.ts::other save'
    elif damage=='test_bytes':file.write_text("test('changed',async()=>{});\n")
    elif damage=='wrong_mode':record['mode']='unit'
    elif damage=='unsigned_hash':record['report_sha256']='missing'
    elif damage=='no_screen':record['artifacts']=[]
    else:record['expected_status']='failed'
    write();assert gate._reference_errors('F001.gui',entry,'click',tmp_path)

def test_same_source_exact_case_and_real_artifact_references_are_format_valid(tmp_path):
    entry,_,_,_=fixture(tmp_path)
    assert not gate._reference_errors('F001.gui',entry,'click',tmp_path)
