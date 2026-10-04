"""Production owned restore lifecycle; all projects and ledger are disposable fixtures."""
import json
from pathlib import Path
import pytest
from backend.contracts.context import ProjectContext
from backend.engine.job_store import JobStore
from backend.engine.data_backup_job import DataBackupJobs
from backend.engine.dataset_import_job import ImportNotAcceptable


def fixture(tmp_path):
    root = tmp_path / 'original'; root.mkdir()
    source = tmp_path / 'images'; source.mkdir(); (source / 'part.png').write_bytes(b'original image')
    project = {'id': 'p', 'name': 'Restore', 'schema_version': 1, 'project_dir': str(root),
               'source_dataset_dir': str(source)}
    (root / 'project.json').write_text(json.dumps(project), encoding='utf-8')
    (root / 'models').mkdir(); (root / 'models' / 'weights.pt').write_bytes(b'checkpoint')
    from backend.engine.project_labelsets import load_labelsets
    load_labelsets(root)
    store = JobStore(tmp_path / 'ledger.sqlite3')
    ctx = ProjectContext(workspace_id='w', project_id='p', actor_id='a', mode='local')
    backups = DataBackupJobs(store); ref = backups.submit(ctx, 'ns', project, 'backup')
    assert backups.run(ref.id).state == 'completed'
    return store, ctx, project, ref.id, backups.view(ref.id, 'ns', 'a')


def restore_jobs(store):
    from backend.engine import data_restore_job
    return data_restore_job.DataRestoreJobs(store)


def test_owned_restore_replay_receipt_and_original_preserved(tmp_path):
    store, ctx, project, backup, view = fixture(tmp_path)
    jobs = restore_jobs(store); target = tmp_path / 'restored'
    original = Path(project['project_dir'], 'project.json').read_bytes()
    ref = jobs.submit(ctx, 'ns', backup, str(target), view['result_ref']['sha256'], 'restore')
    assert jobs.run(ref.id).state == 'completed'
    receipt = jobs.view(ref.id, 'ns', 'a')
    assert receipt['result_ref']['count'] >= 3 and len(receipt['result_ref']['sha256']) == 64
    assert receipt['target_dir'] == str(target) and receipt['result_available']
    restored = json.loads((target / 'project.json').read_text(encoding='utf-8'))
    assert restored['project_dir'] == str(target)
    assert Path(restored['source_dataset_dir'], 'part.png').read_bytes() == b'original image'
    assert Path(project['project_dir'], 'project.json').read_bytes() == original
    assert jobs.submit(ctx, 'ns', backup, str(target), view['result_ref']['sha256'], 'restore').id == ref.id
    (target / 'models' / 'weights.pt').write_bytes(b'changed after restore')
    assert not jobs.view(ref.id, 'ns', 'a')['result_available']


def test_restore_refuses_expired_hash_foreign_actor_and_shared_target(tmp_path):
    store, ctx, project, backup, view = fixture(tmp_path)
    jobs = restore_jobs(store); sha = view['result_ref']['sha256']
    with pytest.raises(KeyError): jobs.submit(ctx.model_copy(update={'actor_id': 'foreign'}), 'ns', backup, str(tmp_path/'x'), sha)
    with pytest.raises(ImportNotAcceptable, match='local'): jobs.submit(ctx.model_copy(update={'mode':'team'}), 'ns', backup, str(tmp_path/'x'), sha)
    with pytest.raises(ImportNotAcceptable, match='hash'): jobs.submit(ctx, 'ns', backup, str(tmp_path/'x'), '0'*64)
    operation = store.checkpoint_value(backup); operation['expires_at'] = 1; store.checkpoint(backup, operation)
    with pytest.raises(ImportNotAcceptable, match='expired'): jobs.submit(ctx, 'ns', backup, str(tmp_path/'x'), sha)
    assert not (tmp_path/'x').exists()


def test_archive_prepublication_hook_observes_fully_rebound_stage(tmp_path):
    store, ctx, project, backup, view = fixture(tmp_path)
    from backend.engine.project_archive import restore_archive
    target = tmp_path / 'restored'; observations = []
    def before(stage, final):
        assert not final.exists()
        config = json.loads((stage / 'project.json').read_text(encoding='utf-8'))
        assert config['project_dir'] == str(final)
        observations.append(config)
    restore_archive(Path(view['result_ref']['path']), target, before_publish=before)
    assert len(observations) == 1


def test_cancel_during_final_verification_leaves_no_target(tmp_path,monkeypatch):
    from backend.engine import data_restore_job
    store,ctx,project,backup,view=fixture(tmp_path);jobs=restore_jobs(store);target=tmp_path/'restored'
    ref=jobs.submit(ctx,'ns',backup,str(target),view['result_ref']['sha256'])
    original=data_restore_job.inventory
    def cancel_inventory(root):
        result=original(root);store.request_cancel(ref.id,'a','cancel during hash');return result
    monkeypatch.setattr(data_restore_job,'inventory',cancel_inventory)
    assert jobs.run(ref.id).state=='aborted'
    assert not target.exists()
    assert not Path(store.checkpoint_value(ref.id)['staged_output']).exists()
    assert Path(project['source_dataset_dir'],'part.png').read_bytes()==b'original image'


def test_cancel_after_verified_checkpoint_before_publish_is_atomic(tmp_path,monkeypatch):
    store,ctx,project,backup,view=fixture(tmp_path);jobs=restore_jobs(store);target=tmp_path/'restored'
    ref=jobs.submit(ctx,'ns',backup,str(target),view['result_ref']['sha256'])
    original=store.finish
    def cancel_finish(identifier,event,*args,**kwargs):
        if event=='complete':store.request_cancel(identifier,'a','before publication')
        return original(identifier,event,*args,**kwargs)
    monkeypatch.setattr(store,'finish',cancel_finish)
    assert jobs.run(ref.id).state=='aborted'
    assert not target.exists() and not jobs.view(ref.id,'ns','a')['result_available']


def test_crash_after_rename_reconciles_only_exact_owned_target(tmp_path,monkeypatch):
    from backend.engine import data_restore_job
    store,ctx,project,backup,view=fixture(tmp_path);jobs=restore_jobs(store);target=tmp_path/'restored'
    ref=jobs.submit(ctx,'ns',backup,str(target),view['result_ref']['sha256'])
    publish=data_restore_job._publish_fresh_directory
    def crash_publish(stage,target):publish(stage,target);raise OSError('crash after rename before ledger commit')
    monkeypatch.setattr(data_restore_job,'_publish_fresh_directory',crash_publish)
    assert jobs.run(ref.id).state=='running' and target.exists()
    assert jobs.recover_orphans()['completed']==[ref.id]
    assert jobs.view(ref.id,'ns','a')['result_available']


def test_crash_before_publication_explicit_resume_and_attempt_fence(tmp_path,monkeypatch):
    store,ctx,project,backup,view=fixture(tmp_path);jobs=restore_jobs(store);target=tmp_path/'restored'
    ref=jobs.submit(ctx,'ns',backup,str(target),view['result_ref']['sha256'])
    finish=store.finish
    def crash_finish(identifier,event,*args,**kwargs):
        if event=='complete':raise OSError('crash before publication')
        return finish(identifier,event,*args,**kwargs)
    monkeypatch.setattr(store,'finish',crash_finish)
    assert jobs.run(ref.id).state=='running' and not target.exists()
    oldstage=Path(store.checkpoint_value(ref.id)['staged_output']);assert oldstage.is_dir()
    assert jobs.recover_orphans()['interrupted']==[ref.id]
    assert jobs.resume(ref.id,'ns','a').id==ref.id
    monkeypatch.setattr(store,'finish',finish)
    assert jobs.run(ref.id).state=='completed'
    assert jobs.view(ref.id,'ns','a')['attempt']==2
    assert oldstage.is_dir(),'interrupted owned staging evidence retained'


def test_foreign_target_and_lost_reservation_never_deleted_or_adopted(tmp_path,monkeypatch):
    store,ctx,project,backup,view=fixture(tmp_path);jobs=restore_jobs(store);target=tmp_path/'restored'
    ref=jobs.submit(ctx,'ns',backup,str(target),view['result_ref']['sha256'])
    target.mkdir();(target/'foreign.txt').write_bytes(b'other owner')
    assert jobs.run(ref.id).state=='failed'
    assert (target/'foreign.txt').read_bytes()==b'other owner'
    assert not jobs.view(ref.id,'ns','a')['result_available']


def test_fresh_publication_never_overwrites_an_empty_foreign_directory(tmp_path):
    from backend.engine.project_archive import restore_archive,ArchiveError,_publish_fresh_directory
    store,ctx,project,backup,view=fixture(tmp_path);target=tmp_path/'restored'
    def race(stage,final):final.mkdir()
    with pytest.raises(ArchiveError,match='publication refused'):
        restore_archive(Path(view['result_ref']['path']),target,before_publish=race,publish=_publish_fresh_directory)
    assert target.is_dir() and list(target.iterdir())==[]


def test_actual_restore_api_same_ledger_scope_and_no_activation(tmp_path,monkeypatch):
    import time
    from fastapi.testclient import TestClient
    from backend.main import create_app
    monkeypatch.setenv('VISION_AI_STUDIO_USER_DATA_DIR',str(tmp_path/'user-data'))
    app=create_app(project_dir=str(tmp_path/'projects'))
    with TestClient(app,headers={'X-Vision-Token':app.state.api_token}) as client:
        project=client.post('/api/project/create',json={'name':'Durable restore','task':'classification'}).json()
        submitted=client.post('/api/dataset/operations/backups').json();identifier=submitted['job_id']
        for _ in range(100):
            backup=client.get(f'/api/dataset/operations/backups/{identifier}').json()
            if backup['state']=='completed':break
            time.sleep(.02)
        target=tmp_path/'restored-api';body={'target_dir':str(target),'expected_archive_sha256':backup['result_ref']['sha256']}
        response=client.post(f'/api/dataset/operations/backups/{identifier}/restore',json=body,headers={'Idempotency-Key':'same-restore'})
        assert response.status_code==200,response.text
        restore=response.json();job_id=restore['job_id']
        for _ in range(100):
            restore=client.get(f'/api/dataset/operations/restores/{job_id}').json()
            if restore['state'] in ('completed','failed'):break
            time.sleep(.02)
        assert restore['state']=='completed',restore
        assert restore['autoactivated'] is False and restore['result_available']
        assert client.get('/api/project/current').json()['project_dir']==project['project_dir']
        assert client.post(f'/api/dataset/operations/backups/{identifier}/restore',json=body,headers={'Idempotency-Key':'same-restore'}).json()['job_id']==job_id
        tasks=client.get('/api/training-workspace/tasks').json()['tasks']
        assert any(row['job_id']==job_id and row['kind']=='project_restore' for row in tasks)
        client.post('/api/project/create',json={'name':'Other','task':'classification'})
        assert client.get(f'/api/dataset/operations/restores/{job_id}').status_code==404


def test_staged_multiple_labelsets_split_and_training_alias_match_final_fingerprint(tmp_path):
    from PIL import Image
    from backend.tests.test_project_archive import _client
    from backend.engine.project_archive import create_archive,restore_archive,_dataset_fingerprint,_content_digest
    from backend.engine.project_labelsets import load_labelsets
    client=_client(tmp_path/'projects');project=client.post('/api/project/create',json={'name':'Labels','task':'detection'}).json()
    source=tmp_path/'images';source.mkdir();image=source/'part.png';Image.new('RGB',(24,24),'white').save(image)
    client.put('/api/project/update',json={'source_dataset_dir':str(source)})
    def label(name):
        response=client.post('/api/annotations/save',json={'image_id':'part','image_path':str(image),'image_width':24,'image_height':24,'annotations':[{'type':'bbox','label':name,'bbox':[1,1,12,12]}]})
        assert response.status_code==200,response.text
    label('Default')
    second=client.post('/api/project/labelsets',json={'name':'Second'}).json()['id'];client.put(f'/api/project/labelsets/{second}/activate');label('Second')
    # A real snapshot supplies immutable versions and a training provenance alias.
    version=client.post('/api/dataset/versions',json={'name':'Reviewed'}).json();root=Path(project['project_dir'])
    version_dir=root/'versions'/version['id'];manifest=json.loads((version_dir/'manifest.json').read_text(encoding='utf-8'))
    model=root/'models'/'alias';model.mkdir();(model/'meta.json').write_text(json.dumps({'training_provenance':{'version_dir':str(version_dir),'manifest_sha256':manifest['content_digest']}}),encoding='utf-8')
    from backend.engine.project_archive import _split_key
    split=root/'dataset'/'splits'/f'{_split_key(source)}.json';split.parent.mkdir(parents=True,exist_ok=True)
    split.write_text(json.dumps({'source_dataset_path':str(source),'train':['part.png'],'validation':[],'test':[]}),encoding='utf-8')
    project=client.get('/api/project/current').json();backed=create_archive(project,tmp_path/'backups');target=tmp_path/'restored'
    sealed={}
    def before(stage,final):
        config=json.loads((stage/'project.json').read_text(encoding='utf-8'));logical=Path(config['source_dataset_dir']);physical=stage/logical.relative_to(final)
        assert not final.exists()
        actual_split=stage/'dataset'/'splits'/f'{_split_key(logical)}.json'
        assert actual_split.is_file() and json.loads(actual_split.read_text(encoding='utf-8'))['train']==['part.png']
        for row in load_labelsets(stage)['labelsets']:
            sealed[row['id']]=_dataset_fingerprint(stage,physical,row['id'],source_identity=logical)
        alias=json.loads((stage/'models'/'alias'/'meta.json').read_text(encoding='utf-8'))['training_provenance']
        relocated=json.loads((stage/'versions'/version['id']/'manifest.json').read_text(encoding='utf-8'))
        assert alias['manifest_sha256']==relocated['content_digest']==_content_digest(relocated)
        assert alias['version_dir']==str(final/'versions'/version['id'])
    restore_archive(Path(backed['archive_path']),target,before_publish=before)
    config=json.loads((target/'project.json').read_text(encoding='utf-8'))
    for set_id,expected in sealed.items():assert _dataset_fingerprint(target,Path(config['source_dataset_dir']),set_id)==expected
    assert sealed['default']!=sealed[second]
    assert image.read_bytes()==(Path(config['source_dataset_dir'])/'part.png').read_bytes()


def test_nested_owner_named_artifact_is_hashed(tmp_path):
    store,ctx,project,backup,view=fixture(tmp_path);jobs=restore_jobs(store);target=tmp_path/'restored'
    ref=jobs.submit(ctx,'ns',backup,str(target),view['result_ref']['sha256'])
    assert jobs.run(ref.id).state=='completed'
    from backend.engine.data_restore_job import inventory,MARKER
    nested=target/'models'/MARKER;nested.write_bytes(b'owned artifact A');before=inventory(target)
    nested.write_bytes(b'owned artifact B');assert inventory(target)!=before


def test_backup_hash_mutation_and_reservation_loss_refuse_resume(tmp_path):
    store,ctx,project,backup,view=fixture(tmp_path);jobs=restore_jobs(store);target=tmp_path/'restored'
    ref=jobs.submit(ctx,'ns',backup,str(target),view['result_ref']['sha256']);jobs.recover_orphans()
    reservation=target.parent/f'.{target.name}.restore-reservation.json';original=reservation.read_bytes()
    reservation.write_bytes(b'other owner')
    with pytest.raises(ImportNotAcceptable,match='reservation'):jobs.resume(ref.id,'ns','a')
    assert reservation.read_bytes()==b'other owner'
    reservation.write_bytes(original)
    Path(view['result_ref']['path']).write_bytes(b'changed archive')
    with pytest.raises(ImportNotAcceptable,match='changed'):jobs.resume(ref.id,'ns','a')
    assert not target.exists()


def test_preexisting_symlink_missing_parent_and_original_target_refused(tmp_path):
    store,ctx,project,backup,view=fixture(tmp_path);jobs=restore_jobs(store);sha=view['result_ref']['sha256']
    for target,reason in ((tmp_path/'missing'/'x','existing'),(Path(project['project_dir'])/'new','outside'),(Path(project['source_dataset_dir'])/'new','outside')):
        with pytest.raises(ImportNotAcceptable,match=reason):jobs.submit(ctx,'ns',backup,str(target),sha)
    link=tmp_path/'linked';link.symlink_to(tmp_path/'images',target_is_directory=True)
    with pytest.raises(ImportNotAcceptable,match='unlinked'):jobs.submit(ctx,'ns',backup,str(link/'new'),sha)


def test_old_attempt_cannot_publish_after_new_fence(tmp_path,monkeypatch):
    store,ctx,project,backup,view=fixture(tmp_path);jobs=restore_jobs(store);target=tmp_path/'restored'
    ref=jobs.submit(ctx,'ns',backup,str(target),view['result_ref']['sha256']);checkpoint=store.checkpoint
    def replace_fence(identifier,operation,*args,**kwargs):
        checkpoint(identifier,operation,*args,**kwargs)
        if kwargs.get('require_uncancelled'):
            current=store.get(identifier);store.begin_attempt(identifier,current.revision,'replacement',None,999999)
    monkeypatch.setattr(store,'checkpoint',replace_fence)
    assert jobs.run(ref.id).state=='running' and not target.exists()
    assert jobs.recover_orphans()['interrupted']==[ref.id]
    assert not jobs.view(ref.id,'ns','a')['result_available']


def test_staging_replaced_during_verification_is_not_overwritten_or_cleaned(tmp_path,monkeypatch):
    from backend.engine import data_restore_job
    store,ctx,project,backup,view=fixture(tmp_path);jobs=restore_jobs(store);target=tmp_path/'restored'
    ref=jobs.submit(ctx,'ns',backup,str(target),view['result_ref']['sha256']);read=data_restore_job.inventory;foreign=[]
    def swap(stage):
        result=read(stage);stage.rename(stage.with_name(stage.name+'-held'));stage.mkdir();(stage/'foreign.txt').write_bytes(b'other writer');foreign.append(stage)
        store.request_cancel(ref.id,'a','cancel after stage replacement');return result
    monkeypatch.setattr(data_restore_job,'inventory',swap)
    jobs.run(ref.id)
    assert foreign[0].is_dir() and (foreign[0]/'foreign.txt').read_bytes()==b'other writer'
    assert not (foreign[0]/data_restore_job.MARKER).exists(),'do not mark an unrelated directory owned'
    assert not target.exists()


def test_parent_directory_replacement_refuses_resume(tmp_path):
    store,ctx,project,backup,view=fixture(tmp_path);jobs=restore_jobs(store)
    parent=tmp_path/'outputs';parent.mkdir();target=parent/'restored'
    ref=jobs.submit(ctx,'ns',backup,str(target),view['result_ref']['sha256']);jobs.recover_orphans()
    original=(parent/f'.{target.name}.restore-reservation.json').read_bytes()
    parent.rename(tmp_path/'previous-output');parent.mkdir()
    (parent/f'.{target.name}.restore-reservation.json').write_bytes(original)
    with pytest.raises(ImportNotAcceptable,match='identity changed'):jobs.resume(ref.id,'ns','a')
    assert not target.exists()


def test_shared_api_refuses_even_privileged_account_and_local_token_required(tmp_path,monkeypatch):
    from fastapi.testclient import TestClient
    from backend.main import create_app
    from backend.api import routes_dataset_imports
    monkeypatch.setenv('VISION_AI_STUDIO_USER_DATA_DIR',str(tmp_path/'user-data'))
    app=create_app(project_dir=str(tmp_path/'projects'));client=TestClient(app)
    body={'target_dir':str(tmp_path/'restored'),'expected_archive_sha256':'a'*64}
    assert client.post('/api/dataset/operations/backups/unknown/restore',json=body).status_code==401
    client.headers['X-Vision-Token']=app.state.api_token
    ctx=ProjectContext(workspace_id='w',project_id='p',actor_id='administrator',mode='team')
    monkeypatch.setattr(routes_dataset_imports,'_scope',lambda request:(ctx,'ns',{'project_dir':str(tmp_path/'project')}))
    assert client.post('/api/dataset/operations/backups/unknown/restore',json=body).status_code==403
    assert not (tmp_path/'restored').exists()


def test_invalid_archive_retains_owned_stage_and_originals(tmp_path):
    from zipfile import ZipFile,ZIP_STORED
    from backend.engine.project_archive import _digest_file
    store,ctx,project,backup,view=fixture(tmp_path);archive=Path(view['result_ref']['path'])
    with ZipFile(archive) as source:contents={info.filename:source.read(info) for info in source.infolist()}
    contents['project/models/weights.pt']=b'X'*len(contents['project/models/weights.pt'])
    with ZipFile(archive,'w',compression=ZIP_STORED) as output:
        for name,value in contents.items():output.writestr(name,value)
    operation=store.checkpoint_value(backup);operation['result_ref']['sha256']=_digest_file(archive);store.checkpoint(backup,operation)
    jobs=restore_jobs(store);target=tmp_path/'restored';ref=jobs.submit(ctx,'ns',backup,str(target),operation['result_ref']['sha256'])
    assert jobs.run(ref.id).state=='failed'
    assert not target.exists() and Path(store.checkpoint_value(ref.id)['staged_output']).is_dir()
    assert 'checksum' in jobs.view(ref.id,'ns','a')['error']['message']
    assert Path(project['project_dir'],'models','weights.pt').read_bytes()==b'checkpoint'


def test_restore_cancelled_before_staging_releases_only_owned_reservation(tmp_path):
    store,ctx,project,backup,view=fixture(tmp_path);jobs=restore_jobs(store);target=tmp_path/'restored'
    ref=jobs.submit(ctx,'ns',backup,str(target),view['result_ref']['sha256']);store.request_cancel(ref.id,'a','cancel accepted')
    assert jobs.run(ref.id).state=='aborted'
    assert not target.exists() and not (target.parent/f'.{target.name}.restore-reservation.json').exists()


def test_crash_target_replaced_with_same_bytes_is_not_adopted(tmp_path,monkeypatch):
    import shutil
    from backend.engine import data_restore_job
    store,ctx,project,backup,view=fixture(tmp_path);jobs=restore_jobs(store);target=tmp_path/'restored'
    ref=jobs.submit(ctx,'ns',backup,str(target),view['result_ref']['sha256']);rename=data_restore_job._publish_fresh_directory
    def crash(stage,final):rename(stage,final);raise OSError('crash')
    monkeypatch.setattr(data_restore_job,'_publish_fresh_directory',crash);jobs.run(ref.id)
    original=tmp_path/'held';target.rename(original);shutil.copytree(original,target)
    assert jobs.recover_orphans()['completed']==[]
    assert store.get(ref.id).state=='interrupted'
    assert target.is_dir(),'unrelated exact-byte copy remains untouched'


def test_completed_replay_survives_removed_archive_but_conflicting_target_refused(tmp_path):
    from backend.engine.job_store import JobConflict
    store,ctx,project,backup,view=fixture(tmp_path);jobs=restore_jobs(store);target=tmp_path/'restored';sha=view['result_ref']['sha256']
    ref=jobs.submit(ctx,'ns',backup,str(target),sha,'same');assert jobs.run(ref.id).state=='completed'
    Path(view['result_ref']['path']).unlink()
    assert jobs.submit(ctx,'ns',backup,str(target),sha,'same').id==ref.id
    with pytest.raises(JobConflict):jobs.submit(ctx,'ns',backup,str(tmp_path/'other'),sha,'same')
    operation=store.checkpoint_value(ref.id);operation['expires_at']=1;store.checkpoint(ref.id,operation)
    assert not jobs.view(ref.id,'ns','a')['result_available']


def test_cancel_between_ref_read_and_start_closes_with_current_revision(tmp_path,monkeypatch):
    store,ctx,project,backup,view=fixture(tmp_path);jobs=restore_jobs(store);target=tmp_path/'restored'
    ref=jobs.submit(ctx,'ns',backup,str(target),view['result_ref']['sha256']);read=store.cancel_intent;injected=[]
    def raced(identifier):
        if not injected:injected.append(True);store.request_cancel(identifier,'a','cancel after ref read')
        return read(identifier)
    monkeypatch.setattr(store,'cancel_intent',raced)
    assert jobs.run(ref.id).state=='aborted'
    assert jobs.recover_orphans()=={'interrupted':[],'completed':[]}
    assert not target.exists()


def test_cancelled_accepted_with_lost_reservation_recovers_without_foreign_cleanup(tmp_path):
    store,ctx,project,backup,view=fixture(tmp_path);jobs=restore_jobs(store);target=tmp_path/'restored'
    ref=jobs.submit(ctx,'ns',backup,str(target),view['result_ref']['sha256']);store.request_cancel(ref.id,'a','cancelled before restart')
    reservation=target.parent/f'.{target.name}.restore-reservation.json';reservation.write_bytes(b'foreign replacement')
    result=jobs.recover_orphans()
    assert store.get(ref.id).state=='aborted'
    assert reservation.read_bytes()==b'foreign replacement'
    assert result['completed']==[]


def test_completed_linked_output_stays_readable_but_unavailable(tmp_path):
    store,ctx,project,backup,view=fixture(tmp_path);jobs=restore_jobs(store);target=tmp_path/'restored'
    ref=jobs.submit(ctx,'ns',backup,str(target),view['result_ref']['sha256']);assert jobs.run(ref.id).state=='completed'
    (target/'linked').symlink_to(Path(project['project_dir'])/'project.json')
    answer=jobs.view(ref.id,'ns','a')
    assert answer['state']=='completed' and not answer['result_available']


def test_cancel_between_start_and_attempt_is_fenced_and_closed(tmp_path,monkeypatch):
    store,ctx,project,backup,view=fixture(tmp_path);jobs=restore_jobs(store);target=tmp_path/'restored'
    ref=jobs.submit(ctx,'ns',backup,str(target),view['result_ref']['sha256']);begin=store.begin_attempt;injected=[]
    def raced(identifier,*args,**kwargs):
        if not injected:injected.append(True);store.request_cancel(identifier,'a','cancel before attempt')
        return begin(identifier,*args,**kwargs)
    monkeypatch.setattr(store,'begin_attempt',raced)
    assert jobs.run(ref.id).state=='aborted'
    assert not target.exists()
