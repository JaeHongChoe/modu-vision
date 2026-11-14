"""Historical label edits do not waive immutable saved-version backup hashes."""
import json

import pytest

from backend.tests.test_embedded_label_quality import saved


def _queue(saved):
    client,project,source,record,_=saved
    created=client.post('/api/data-workbench/label-quality-analyses',json={'evaluation_id':record['evaluation_id']})
    assert created.status_code==200,created.text
    analysis=created.json()
    created=client.post('/api/data-workbench/review-queues',json={'analysis_id':analysis['id'],'mode':'label_quality'})
    assert created.status_code==200,created.text
    return client,project,source,analysis,created.json()


@pytest.mark.parametrize('change',['modify','delete'])
def test_historical_queue_hard_stales_after_immutable_label_backup_damage(saved,change):
    client,project,source,analysis,queue=_queue(saved)
    from backend.api.routes_dataset_versions import _read_manifest
    version,manifest=_read_manifest(project,analysis['common_cohort']['dataset_version_id'])
    backup=version/next(row['snapshot_path'] for row in manifest['files'] if row['kind']=='label')
    if change=='modify':backup.write_text('{"damaged_saved_truth":true}')
    else:backup.unlink()
    assert client.get('/api/data-workbench/label-quality-analyses/'+analysis['id']).status_code==409
    assert client.get('/api/data-workbench/review-queues/'+queue['id']).status_code==409
    response=client.post('/api/data-workbench/review-queues/'+queue['id']+'/advance',json={
        'expected_revision':1,'relative_path':queue['items'][0]['relative_path'],'state':'reviewed','actor':'Kim'})
    assert response.status_code==409,response.text
    from backend.engine.data_workbench import _queue_path
    persisted=json.loads(_queue_path(project['project_dir'],source,queue['id']).read_text())
    assert persisted['cursor']==0 and persisted['revision']==1


def test_frozen_split_backup_hash_failure_is_hard_stale(saved,monkeypatch):
    client,project,source,analysis,queue=_queue(saved)
    from backend.api import routes_dataset_versions as versions
    verify=versions._verify
    def failed_frozen_split(*args,**kwargs):
        result=verify(*args,**kwargs)
        return {**result,'status':'changed','changed_files':[*result['changed_files'],'backup/split/manifest.json']}
    monkeypatch.setattr(versions,'_verify',failed_frozen_split)
    response=client.get('/api/data-workbench/review-queues/'+queue['id'])
    assert response.status_code==409,response.text


def test_own_current_label_edit_remains_historical_basis_change(saved):
    client,_,_,analysis,queue=_queue(saved)
    row=queue['items'][0]
    response=client.put('/api/dataset/metadata/class-override',json={'image_path':row['file_path'],'class_name':'OK','actor':'Kim'})
    assert response.status_code==200,response.text
    response=client.get('/api/data-workbench/review-queues/'+queue['id'])
    assert response.status_code==200 and response.json()['basis_changed'],response.text
    response=client.post('/api/data-workbench/review-queues/'+queue['id']+'/advance',json={
        'expected_revision':1,'relative_path':row['relative_path'],'state':'reviewed','actor':'Kim'})
    assert response.status_code==200 and response.json()['cursor']==1,response.text
