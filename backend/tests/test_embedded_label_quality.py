"""Saved common-cohort evidence -> immutable advisory -> resumable review queue."""
import copy
import hashlib
import importlib
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image


def quality():
    module=importlib.import_module('backend.engine.classification_label_quality')
    return module


@pytest.fixture
def saved(tmp_path,monkeypatch):
    from backend.tests.test_model_comparisons import _client
    from backend.engine.evaluation_history import EvaluationHistory
    from backend.remote.evaluation_cohort import freeze_cohort,digest
    from backend.engine.dataset_metadata import metadata_for_path
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('VISION_AI_STUDIO_USER_DATA_DIR',str(tmp_path/'user'))
    source=tmp_path/'source'
    for split,offset in [('train',10),('val',30),('test',70)]:
        for label,color in [('OK',20),('NG',120)]:
            p=source/split/label/'sample.png';p.parent.mkdir(parents=True,exist_ok=True)
            Image.new('RGB',(8,8),(color,offset,13)).save(p)
    client=_client(tmp_path)
    project=client.post('/api/project/create',json={'name':'Quality advisory','task':'classification'}).json()
    client.put('/api/project/update',json={'source_dataset_dir':str(source)})
    project={**project,'source_dataset_dir':str(source)}
    from backend.engine.annotation_storage import set_request_annotation_root,set_request_project_root,reset_request_annotation_root,reset_request_project_root
    a=set_request_annotation_root(Path(project['annotations_dir']));p=set_request_project_root(Path(project['project_dir']))
    version=client.post('/api/dataset/versions',json={'name':'heldout'}).json()
    cohort=freeze_cohort(project,version['id'],source,'classification',{'task':'classification','classes':['NG','OK']},SimpleNamespace(selection_sha256={hashlib.sha256((source/'train/NG/sample.png').read_bytes()).hexdigest()}))
    rows=[]
    for index,sample in enumerate(cohort['descriptor']['ordered_samples']):
        image=source/sample['source_relative_path'];metadata=metadata_for_path(Path(project['project_dir']),source,image,Path(project['annotations_dir']))
        scores={'NG':.51,'OK':.49} if index==0 else {'NG':.01,'OK':.99}
        rows.append({**{k:metadata[k] for k in ('image_uuid','revision','content_hash')},'sample_id':sample['sample_id'],'file_path':str(image),'input_sha256':sample['sha256'],'truth_sha256':digest(sample['truth']),'ground_truth':sample['truth'],'predicted_class':max(scores,key=scores.get),'confidence':max(scores.values()),'class_scores':scores,'is_correct':sample['truth']==max(scores,key=scores.get)})
    common={'cohort_sha256':cohort['descriptor']['cohort_sha256'],'dataset_version_id':version['id'],'image_count':len(rows),'split':'test'}
    receipt={k:cohort[k] for k in ('archive_sha256','snapshot_manifest_sha256','version_manifest_sha256')}
    receipt.update(checkpoint_sha256='a'*64,training_snapshot_sha256='b'*64,metadata_sha256='c'*64)
    result={'task':'classification','job_id':'job_owned','test_predictions':rows,'common_cohort':common,'confusion_matrix':{'classes':['NG','OK']},'metrics':{'evaluated_split':'test','selection_overlap':False},'evaluation_binding_sha256':'d'*64,'input_receipt':receipt}
    binding={'source_dataset_path':str(source),'task':'classification','labelset_id':'default','common_cohort':common,'checkpoint_sha256':'a'*64,'input_receipt':receipt,'evaluation_binding_sha256':'d'*64}
    history=EvaluationHistory(Path(project['reports_dir'])/'evaluations')
    record=history.append(result,binding)
    yield client,project,source,record,history
    reset_request_annotation_root(a);reset_request_project_root(p)


def test_upstream_normalized_margin_known_values_and_no_input_mutation():
    mod=quality();p=np.array([[.51,.49],[.9,.1],[0.,1.]])
    original=p.copy()
    assert mod.normalized_margin(np.array([0,1,1]),p).tolist()==pytest.approx([.51,.1,1.])
    assert np.array_equal(p,original)


def test_archived_common_cohort_to_immutable_analysis_to_ranked_queue(saved):
    client,project,source,record,_=saved
    response=client.post('/api/data-workbench/label-quality-analyses',json={'evaluation_id':record['evaluation_id']})
    assert response.status_code==200,response.text
    analysis=response.json()
    assert analysis['advisory_only'] and analysis['group_independence']=='unverified'
    assert analysis['algorithm']['upstream_version']=='2.9.0'
    before=quality().analysis_path(project,source,analysis['id']).read_bytes()
    response=client.post('/api/data-workbench/review-queues',json={'analysis_id':analysis['id'],'mode':'label_quality','top_n':1})
    assert response.status_code==200,response.text
    queue=response.json();assert queue['mode']=='label_quality' and len(queue['items'])==1
    row=queue['items'][0]
    assert row['given_class']=='NG' and row['predicted_class']=='NG' and row['quality_score']==pytest.approx(.51)
    assert row['reasons']==['label_quality'] and queue['origin']['analysis_id']==analysis['id']
    assert client.get('/api/data-workbench/review-queues/'+queue['id']).json()['items']==queue['items']
    done=client.post('/api/data-workbench/review-queues/'+queue['id']+'/advance',json={'expected_revision':1,'relative_path':row['relative_path'],'state':'reviewed','actor':'Kim'})
    assert done.status_code==200 and done.json()['cursor']==1
    assert quality().analysis_path(project,source,analysis['id']).read_bytes()==before


@pytest.mark.parametrize('case',['legacy','scores','order','top1','confidence','nan','sum','duplicate','binding','truth','source','revision'])
def test_invalid_probability_and_saved_identity_evidence_refused(saved,case):
    client,project,source,record,history=saved
    result=copy.deepcopy(record['result']);binding=copy.deepcopy(record['binding']);row=result['test_predictions'][0]
    if case=='legacy':result.pop('common_cohort')
    elif case=='scores':row.pop('class_scores')
    elif case=='order':result['confusion_matrix']['classes']=['OK','NG']
    elif case=='top1':row['predicted_class']='OK'
    elif case=='confidence':row['confidence']=.7
    elif case=='nan':row['class_scores']['NG']=float('nan')
    elif case=='sum':row['class_scores']['NG']=.8
    elif case=='duplicate':result['test_predictions'][1]=copy.deepcopy(row)
    elif case=='binding':binding['checkpoint_sha256']='f'*64
    elif case=='truth':row['ground_truth']='OK'
    elif case=='source':row['file_path']=str(source/'train/NG/sample.png')
    elif case=='revision':row['revision']+=1
    changed=history.append(result,binding)
    response=client.post('/api/data-workbench/label-quality-analyses',json={'evaluation_id':changed['evaluation_id']})
    assert response.status_code in (409,422),response.text


def test_label_edit_allows_historical_progress_but_refuses_new_queue_and_bytes_change(saved):
    client,project,source,record,_=saved
    created=client.post('/api/data-workbench/label-quality-analyses',json={'evaluation_id':record['evaluation_id']})
    assert created.status_code==200,created.text
    analysis=created.json()
    created=client.post('/api/data-workbench/review-queues',json={'analysis_id':analysis['id'],'mode':'label_quality'})
    assert created.status_code==200,created.text
    queue=created.json();row=queue['items'][0]
    changed=client.put('/api/dataset/metadata/class-override',json={'image_path':row['file_path'],'class_name':'OK','actor':'Kim'})
    assert changed.status_code==200,changed.text
    assert changed.json()['metadata']['revision']>row['revision']
    reopened=client.get('/api/data-workbench/review-queues/'+queue['id'])
    assert reopened.status_code==200,reopened.text
    assert reopened.json()['basis_changed'] and reopened.json()['items'][0]['basis_changed']
    assert client.post('/api/data-workbench/review-queues',json={'analysis_id':analysis['id'],'mode':'label_quality'}).status_code==409
    advanced=client.post('/api/data-workbench/review-queues/'+queue['id']+'/advance',json={'expected_revision':1,'relative_path':row['relative_path'],'state':'reviewed','actor':'Kim'})
    assert advanced.status_code==200 and advanced.json()['cursor']==1
    Image.new('RGB',(8,8),'pink').save(row['file_path'])
    assert client.get('/api/data-workbench/review-queues/'+queue['id']).status_code==409


def test_probability_matrix_bounds_and_stable_ties(saved):
    _,project,source,record,_=saved
    mod=quality();descriptor=mod._descriptor(project,record)
    record=copy.deepcopy(record)
    for row in record['result']['test_predictions']:
        row.update(class_scores={'NG':.5,'OK':.5},predicted_class='NG',confidence=.5,is_correct=row['ground_truth']=='NG')
    _,items=mod._rank(record,descriptor)
    assert [r['relative_path'] for r in items]==sorted(r['relative_path'] for r in items)
    assert [r['quality_score'] for r in items]==[.5,.5]
    descriptor['classes']=['only']
    with pytest.raises(ValueError,match='class order'):mod._rank(record,descriptor)


def test_analysis_tampering_wrong_task_and_unknown_scope_are_rejected(saved):
    client,project,source,record,_=saved
    response=client.post('/api/data-workbench/label-quality-analyses',json={'evaluation_id':record['evaluation_id']})
    assert response.status_code==200,response.text
    analysis=response.json();path=quality().analysis_path(project,source,analysis['id'])
    wrong={**project,'task':'detection'}
    with pytest.raises(ValueError,match='scope changed'):quality().read_analysis(wrong,source,analysis['id'])
    payload=json.loads(path.read_text());payload['items'][0]['quality_score']=0;path.write_text(json.dumps(payload))
    assert client.get('/api/data-workbench/label-quality-analyses/'+analysis['id']).status_code==409
    assert client.post('/api/data-workbench/review-queues',json={'mode':'label_quality','analysis_id':analysis['id'],'top_n':101}).status_code==422


def test_analysis_and_quality_queue_enforce_project_review_role(saved,monkeypatch):
    client,project,source,record,_=saved
    from backend.api import routes_image_truth
    from fastapi import HTTPException
    def refuse(*args):raise HTTPException(403,'review role required')
    monkeypatch.setattr(routes_image_truth,'require_role',refuse)
    assert client.post('/api/data-workbench/label-quality-analyses',json={'evaluation_id':record['evaluation_id']}).status_code==403
    assert client.post('/api/data-workbench/review-queues',json={'mode':'label_quality','analysis_id':'labelquality_'+'1'*32}).status_code==403


def test_port_provenance_contains_original_license_and_pinned_source():
    root=Path(__file__).resolve().parents[2]
    provenance=json.loads((root/'third_party/cleanlab/provenance.json').read_text())
    assert provenance['version']=='2.9.0' and provenance['source_sha256']=='3a76b231bd942a9922ee1fa7f92b57540fcea41f07c99211b2bae6979f2e793c'
    assert 'Apache License' in (root/'third_party/cleanlab/LICENSE').read_text()
    assert 'Modified' in (root/'third_party/cleanlab/NOTICE').read_text()
    assert (root/'third_party/cleanlab/LICENSE').read_bytes()==(root/'public/third-party/cleanlab/LICENSE').read_bytes()


def test_assistant_action_accepts_saved_evaluation_only_and_never_truth_approval():
    from backend.engine.assistant_tasks import validate_plan,TaskError
    value={'goal':'라벨 재확인 순위','binding':'scope','steps':[{'action':'analyze_label_quality','args':{'evaluation_id':'evaluation_'+'a'*32,'top_n':7},'label':'순위 저장'}]}
    assert validate_plan(value)==value
    for args in ({'evaluation_id':'/source/image.png'},{'evaluation_id':'evaluation_'+'a'*32,'top_n':101},{'evaluation_id':'evaluation_'+'a'*32,'approve':True}):
        invalid=copy.deepcopy(value);invalid['steps'][0]['args']=args
        with pytest.raises(TaskError):validate_plan(invalid)


def test_historical_queue_refuses_new_source_pixels_and_foreign_binding_task(saved):
    client,project,source,record,history=saved
    response=client.post('/api/data-workbench/label-quality-analyses',json={'evaluation_id':record['evaluation_id']})
    assert response.status_code==200,response.text
    analysis=response.json()
    Image.new('RGB',(8,8),'pink').save(source/'test/NG/new.png')
    response=client.get('/api/data-workbench/label-quality-analyses/'+analysis['id'])
    assert response.status_code==409,response.text
    (source/'test/NG/new.png').unlink()
    binding=copy.deepcopy(record['binding']);binding['task']='detection'
    changed=history.append(record['result'],binding)
    assert client.post('/api/data-workbench/label-quality-analyses',json={'evaluation_id':changed['evaluation_id']}).status_code==422


def test_quality_queue_evidence_cannot_be_reordered_or_modified_and_advance_requires_review_role(saved,monkeypatch):
    client,project,source,record,_=saved
    response=client.post('/api/data-workbench/label-quality-analyses',json={'evaluation_id':record['evaluation_id']});assert response.status_code==200
    analysis=response.json()
    response=client.post('/api/data-workbench/review-queues',json={'analysis_id':analysis['id'],'mode':'label_quality'});assert response.status_code==200
    queue=response.json();row=queue['items'][0]
    from backend.api import routes_image_truth
    from fastapi import HTTPException
    def refuse(*args):raise HTTPException(403,'review role required')
    monkeypatch.setattr(routes_image_truth,'require_role',refuse)
    response=client.post('/api/data-workbench/review-queues/'+queue['id']+'/advance',json={'expected_revision':1,'relative_path':row['relative_path'],'state':'reviewed','actor':'Kim'})
    assert response.status_code==403,response.text
    from backend.engine import data_workbench as dw
    path=dw._queue_path(project['project_dir'],source,queue['id'])
    raw=json.loads(path.read_text());raw['items'][0]['quality_score']=0;path.write_text(json.dumps(raw))
    response=client.get('/api/data-workbench/review-queues/'+queue['id'])
    assert response.status_code==409,response.text


def test_persisted_probability_objects_use_descriptor_class_order_not_sorted_json_keys(saved):
    _,project,_,record,_=saved
    mod=quality();descriptor=mod._descriptor(project,record)
    descriptor['classes']=['OK','NG']
    record=copy.deepcopy(record);record['result']['confusion_matrix']['classes']=['OK','NG']
    assert list(record['result']['test_predictions'][0]['class_scores'])==['NG','OK']
    classes,items=mod._rank(record,descriptor)
    assert classes==['OK','NG'] and items[0]['quality_score']==pytest.approx(.51)
