"""Controlled report serialization tests; no actual GUI execution is claimed."""
import hashlib,json,subprocess
from pathlib import Path
import pytest
from scripts.gui_execution_receipt import collect

def fixture(tmp_path):
    repo=tmp_path/'repo';repo.mkdir();test=repo/'scripts/e2e/owned.spec.ts';test.parent.mkdir(parents=True);test.write_text("test('save',async()=>{});\n")
    def git(*args):return subprocess.check_output(['git',*args],cwd=repo,text=True).strip()
    git('init','-q');git('add','.');git('-c','user.name=Fixture','-c','user.email=fixture@example.invalid','commit','-qm','controlled fixture');commit=git('rev-parse','HEAD')
    run=tmp_path/'run';run.mkdir();image=run/'screen.png';image.write_bytes(b'controlled serialization pixels')
    report={'suites':[{'specs':[{'file':'owned.spec.ts','title':'save','tests':[{'projectName':'electron','expectedStatus':'passed','status':'expected','results':[{'status':'passed','retry':0,'errors':[]}]}]}]}]}
    manifest={'schema':'modu-vision.e2e-evidence/v1','receipt':'HarnessReceipt','source':{'commit':commit,'dirty':False},'test':'owned.spec.ts › save','project':'electron','mode':'electron','platform':{'os':'darwin','arch':'arm64'},'status':'passed','expected_status':'passed','result':'pass','screenshots':[str(image)]}
    rp=run/'report.json';mp=run/'harness.json'
    def write():rp.write_text(json.dumps(report));mp.write_text(json.dumps(manifest))
    write();return repo,test,run,image,report,manifest,rp,mp,write

def test_collector_binds_exact_committed_case_and_canonical_own_artifacts(tmp_path):
    repo,test,run,image,report,manifest,rp,mp,write=fixture(tmp_path)
    value=collect(repo,rp,mp)
    assert value['test']=='scripts/e2e/owned.spec.ts::save'
    assert value['source_sha']==manifest['source']['commit']
    assert value['test_source_sha256']==hashlib.sha256(test.read_bytes()).hexdigest()
    assert value['artifacts'][0]['sha256']==hashlib.sha256(image.read_bytes()).hexdigest()
    assert not value['quality_approved'] and not value['independent_review_accepted']

@pytest.mark.parametrize('damage',['fail','skip','retry','expected_fail','duplicate','missing','dirty','changed_test','no_screenshot','outside','linked','mismatch'])
def test_collector_refuses_nonexecuted_changed_or_foreign_references(tmp_path,damage):
    repo,test,run,image,report,manifest,rp,mp,write=fixture(tmp_path);record=report['suites'][0]['specs'][0]['tests'][0]
    if damage=='fail':record['results'][0]['status']='failed'
    elif damage=='skip':record['results'][0]['status']='skipped'
    elif damage=='retry':record['results'].append(dict(record['results'][0]))
    elif damage=='expected_fail':record['expectedStatus']='failed'
    elif damage=='duplicate':report['suites'].append(report['suites'][0])
    elif damage=='missing':report['suites']=[]
    elif damage=='dirty':manifest['source']['dirty']=True
    elif damage=='changed_test':test.write_text('different source')
    elif damage=='no_screenshot':manifest['screenshots']=[]
    elif damage=='outside':outside=tmp_path/'external.png';outside.write_bytes(b'external');manifest['screenshots']=[str(outside)]
    elif damage=='linked':image.unlink();image.symlink_to(test)
    elif damage=='mismatch':manifest['test']='owned.spec.ts › unexecuted'
    write()
    with pytest.raises(ValueError):collect(repo,rp,mp)
