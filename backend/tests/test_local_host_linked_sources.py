import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ORIGIN = 'https://localhost:8443'


def implementation():
    assert importlib.util.find_spec('backend.local_host.linked_sources') is not None, 'missing reviewed local linked-source snapshots'
    from backend.local_host import linked_sources
    return linked_sources


def fixture(tmp_path):
    module = implementation()
    from backend.engine.shared_accounts import AccountStore
    from backend.local_host.controller import LocalHostController
    from backend.local_host.routes import local_host_router
    app = FastAPI(); app.state.accounts = AccountStore(tmp_path/'auth/accounts.sqlite')
    user = app.state.accounts.bootstrap('owner', 'isolated-test-password')
    session = app.state.accounts.login('owner', 'isolated-test-password', transport='cookie')
    project = tmp_path/'project'; project.mkdir(); source = tmp_path/'source'; source.mkdir()
    target = tmp_path/'external'; target.mkdir(); (target/'one.png').write_bytes(b'fixture-image')
    (source/'linked-class').symlink_to(target, target_is_directory=True); (source/'plain.txt').write_bytes(b'plain')
    config = {'id':'project-one','name':'fixture','project_dir':str(project),'source_dataset_dir':str(source),
              'task':'classification','annotations_dir':str(project/'annotations'),'active_labelset_id':'default'}
    (project/'project.json').write_text(json.dumps(config))
    app.state.accounts.register_project(config['id'], project, user['id'])
    class Registry:
        def resolve_request(self, request, account):
            current=json.loads((project/'project.json').read_text())
            return {'project':current,'role':app.state.accounts.project_role_exact(account['id'],current['id']),
                    'membership_key':current['id'],'context':{'workspace_id':'workspace-one','project_id':current['id']}}
    app.state.local_projects=Registry(); app.state.context_registry=SimpleNamespace(workspace_id='workspace-one')
    class NativeBoundary:
        ready={'protocol':1,'platform':'darwin'};closed=False;approved=True;on_confirm=None
        def __init__(self):self.calls=[]
        async def call(self, authority, method, args, **kwargs):
            self.calls.append((authority,method,args))
            if method=='host.approve':return {'approved':True}
            assert method=='host.approveLinkedSource'
            if self.on_confirm:self.on_confirm()
            return {'approved':True,'plan_sha256':args['plan_sha256']} if self.approved else {'approved':False}
    helper=NativeBoundary(); controller=LocalHostController(app,helper,origin=ORIGIN,state_dir=tmp_path/'host')
    app.state.local_host=controller; app.include_router(local_host_router(controller)); app.include_router(module.install_linked_sources(controller))
    client=TestClient(app,base_url=ORIGIN,headers={'Origin':ORIGIN});client.cookies.set('vision_session',session['token'])
    headers={'X-Vision-CSRF':session['csrf_token']};tab=client.post('/api/local-host/session',headers=headers).json()['tab_id']
    headers={**headers,'X-Vision-Local-Tab':tab,'X-Vision-Project':config['id']};assert client.post('/api/local-host/approve',headers=headers).status_code==200
    expected={key:config[key] for key in ('source_dataset_dir','task','annotations_dir','active_labelset_id')};expected['project_id']=config['id']
    def preview():return client.post('/api/local-host/linked-sources/preview',headers=headers,json={'expected_source':expected})
    def materialize(plan):return client.post('/api/local-host/linked-sources/materialize',headers=headers,json={'plan_id':plan['plan_id'],'plan_sha256':plan['plan_sha256']})
    return SimpleNamespace(**locals())


def test_explicit_target_review_copies_only_reviewed_content_without_selecting_source_or_job(tmp_path):
    f=fixture(tmp_path);before=(f.project/'project.json').read_bytes();plan=f.preview().json()
    assert plan['targets']==[{'link':'linked-class','path':str(f.target),'kind':'directory'}]
    assert plan['file_count']==2 and plan['total_bytes']==18
    assert [method for _,method,_ in f.helper.calls]==['host.approve']
    result=f.materialize(plan);assert result.status_code==200,result.text;copy=result.json();root=Path(copy['source'])
    assert root.is_relative_to(f.project/'.local-host-linked-sources')
    assert (root/'linked-class/one.png').read_bytes()==b'fixture-image'
    assert not (root/'linked-class').is_symlink()
    assert (root/'plain.txt').read_bytes()==b'plain'
    assert (f.source/'linked-class').is_symlink() and (f.target/'one.png').read_bytes()==b'fixture-image'
    assert (f.project/'project.json').read_bytes()==before and not f.controller.grants.rows
    check=f.client.post('/api/local-host/linked-sources/use-check',headers=f.headers,json={'snapshot_id':copy['snapshot_id'],'manifest_sha256':copy['manifest_sha256']})
    assert check.status_code==200 and check.json()['source']==copy['source']
    assert f.materialize(plan).status_code==409
    manifest=json.loads((root.parent/'snapshot.json').read_text());assert manifest['state']=='complete'
    assert manifest['files'][0]['sha256']==hashlib.sha256(b'fixture-image').hexdigest()
    from backend.local_host.staging import stage_selection
    with pytest.raises(ValueError):stage_selection(f.source,f.project,'dataset-source')
    with pytest.raises(ValueError):stage_selection(root,f.project,'dataset-source')


@pytest.mark.parametrize('kind',['cancel','logout','source-change','target-change','project-change','tab-change','role-change'])
def test_confirmation_cancel_or_changed_authority_never_returns_a_copy_or_grant(tmp_path,kind):
    f=fixture(tmp_path);plan=f.preview().json()
    if kind=='cancel':f.helper.approved=False
    elif kind=='logout':f.helper.on_confirm=lambda:f.app.state.accounts.logout(f.session['token'])
    elif kind=='source-change':
        def change():
            value=dict(f.config);value['active_labelset_id']='new';(f.project/'project.json').write_text(json.dumps(value))
        f.helper.on_confirm=change
    elif kind=='project-change':
        def change_project():
            f.project.rename(tmp_path/'previous-project');f.project.mkdir();(f.project/'project.json').write_text(json.dumps(f.config))
        f.helper.on_confirm=change_project
    elif kind=='target-change':f.helper.on_confirm=lambda:(f.target/'one.png').write_bytes(b'replaced image')
    elif kind=='tab-change':f.headers['X-Vision-Local-Tab']='wrong-tab'
    else:
        other=f.app.state.accounts.create_user('other-owner','isolated-other-password')
        f.app.state.accounts.set_membership(f.config['id'],other['id'],'owner',f.user['id'])
        f.helper.on_confirm=lambda:f.app.state.accounts.set_membership(f.config['id'],f.user['id'],'reviewer',other['id'])
    result=f.materialize(plan)
    assert result.status_code==200 and result.json()=={'cancelled':True} if kind=='cancel' else result.status_code in {401,403,409}
    assert not (f.project/'.local-host-linked-sources').exists() and not f.controller.grants.rows


@pytest.mark.parametrize('kind',['private-file','private-parent','private-key','private-dir','codex-dir','claude-dir','private-case','hardlink','cycle','limit'])
def test_unreviewable_targets_fail_before_any_native_confirmation(tmp_path,kind):
    f=fixture(tmp_path);link=f.source/'linked-class';link.unlink()
    if kind=='private-file':
        private=f.controller.state_dir/'secret.txt';private.parent.mkdir();private.write_text('private fixture');link.symlink_to(private)
    elif kind=='private-parent':link.symlink_to(tmp_path,target_is_directory=True)
    elif kind=='private-key':
        (f.target/'private.key').write_text('fixture private key');link.symlink_to(f.target,target_is_directory=True)
    elif kind=='private-dir':
        (f.target/'.ssh').mkdir();(f.target/'.ssh/id_rsa').write_text('fixture private key');link.symlink_to(f.target,target_is_directory=True)
    elif kind in {'codex-dir','claude-dir'}:
        folder=f.target/('.codex' if kind=='codex-dir' else '.claude');folder.mkdir();(folder/'auth.json').write_text('fixture credentials');link.symlink_to(f.target,target_is_directory=True)
    elif kind=='private-case':
        folder=f.target/'.CoDeX';folder.mkdir();(folder/'settings.toml').write_text('fixture profile');link.symlink_to(f.target,target_is_directory=True)
    elif kind=='hardlink':
        (f.target/'second.png').hardlink_to(f.target/'one.png');link.symlink_to(f.target,target_is_directory=True)
    elif kind=='cycle':link.symlink_to(f.source,target_is_directory=True)
    else:
        link.symlink_to(f.target,target_is_directory=True);f.controller.linked_sources.max_files=1
    result=f.preview();assert result.status_code==422
    assert len(f.helper.calls)==1 and not (f.project/'.local-host-linked-sources').exists()


def test_old_tab_or_expired_plan_cannot_materialize_and_snapshot_tampering_blocks_use(tmp_path):
    f=fixture(tmp_path);plan=f.preview().json();other=f.client.post('/api/local-host/session',headers=f.headers).json()['tab_id']
    response=f.client.post('/api/local-host/linked-sources/materialize',headers={**f.headers,'X-Vision-Local-Tab':other},json={'plan_id':plan['plan_id'],'plan_sha256':plan['plan_sha256']})
    assert response.status_code==403
    f.controller.linked_sources.now=lambda:10**15
    assert f.materialize(plan).status_code==409
    f.controller.linked_sources.now=__import__('time').monotonic
    copy=f.materialize(f.preview().json()).json();path=Path(copy['source'])/'plain.txt';path.chmod(0o600);path.write_text('tampered')
    response=f.client.post('/api/local-host/linked-sources/use-check',headers=f.headers,json={'snapshot_id':copy['snapshot_id'],'manifest_sha256':copy['manifest_sha256']})
    assert response.status_code==409


def test_copy_race_preserves_failed_receipt_and_never_returns_source(tmp_path,monkeypatch):
    f=fixture(tmp_path);plan=f.preview().json();module=implementation();original=module.copy_reviewed_file;changed=False
    def racing(*args,**kwargs):
        nonlocal changed
        original(*args,**kwargs)
        if not changed:changed=True;(f.target/'one.png').write_bytes(b'raced fixture')
    monkeypatch.setattr(module,'copy_reviewed_file',racing)
    result=f.materialize(plan);assert result.status_code==409
    receipts=list((f.project/'.local-host-linked-sources').glob('*/snapshot.json'));assert len(receipts)==1
    assert json.loads(receipts[0].read_text())['state']=='failed'
    assert (f.source/'linked-class').is_symlink() and not f.controller.grants.rows


def test_snapshot_directory_handles_are_bounded_for_many_classes(tmp_path):
    import resource
    f=fixture(tmp_path)
    for index in range(100):
        directory=f.source/f'class-{index}';directory.mkdir();(directory/'image.png').write_bytes(b'fixture')
    plan=f.preview().json();previous=resource.getrlimit(resource.RLIMIT_NOFILE)
    try:
        resource.setrlimit(resource.RLIMIT_NOFILE,(min(64,previous[0]),previous[1]))
        result=f.materialize(plan)
    finally:resource.setrlimit(resource.RLIMIT_NOFILE,previous)
    assert result.status_code==200,result.text
    assert result.json()['file_count']==102


def test_preview_and_cancel_never_read_link_target_content_before_native_consent(tmp_path,monkeypatch):
    f=fixture(tmp_path);module=implementation();original=module._read_file;reads=[]
    def observe(path,*args,**kwargs):reads.append(str(path));return original(path,*args,**kwargs)
    monkeypatch.setattr(module,'_read_file',observe)
    plan=f.preview().json();assert reads==[], 'preview may inspect bounded metadata but never open target contents'
    f.helper.approved=False
    assert f.materialize(plan).json()=={'cancelled':True};assert reads==[], 'cancelled native consent cannot authorize hashing'
    f.helper.approved=True
    def native_boundary():assert reads==[], 'target content cannot be read while consent is still pending'
    f.helper.on_confirm=native_boundary
    result=f.materialize(f.preview().json());assert result.status_code==200,result.text
    assert str(f.target/'one.png') in reads
    assert (Path(result.json()['source'])/'linked-class/one.png').read_bytes()==b'fixture-image'


def test_private_target_never_reaches_the_content_reader(tmp_path,monkeypatch):
    f=fixture(tmp_path);module=implementation();reads=[]
    monkeypatch.setattr(module,'_read_file',lambda path,*args,**kwargs:reads.append(path))
    secret=f.controller.state_dir/'private.key';secret.parent.mkdir();secret.write_text('fixture private key')
    (f.source/'linked-class').unlink();(f.source/'linked-class').symlink_to(secret)
    assert f.preview().status_code==422 and reads==[] and len(f.helper.calls)==1


@pytest.mark.parametrize('link_kind',['hardlink','symlink'])
@pytest.mark.parametrize('copy_fails',[False,True])
def test_receipt_publication_never_truncates_an_external_original(tmp_path,monkeypatch,link_kind,copy_fails):
    import os
    f=fixture(tmp_path);module=implementation();outside=tmp_path/'outside-original.txt'
    before=b'owned original must survive external filesystem writer';outside.write_bytes(before)
    original=module.SnapshotWriter.receipt;inserted=False
    def race_receipt(self,value):
        nonlocal inserted
        if not inserted:
            inserted=True
            if link_kind=='hardlink':os.link(outside,'snapshot.json',dst_dir_fd=self.destination_fd)
            else:os.symlink(outside,'snapshot.json',dir_fd=self.destination_fd)
        return original(self,value)
    monkeypatch.setattr(module.SnapshotWriter,'receipt',race_receipt)
    if copy_fails:
        def fail_copy(*args,**kwargs):raise ValueError('isolated copy failure')
        monkeypatch.setattr(module,'copy_reviewed_file',fail_copy)
    result=f.materialize(f.preview().json())
    assert outside.read_bytes()==before, 'publishing a receipt must never follow or truncate an existing entry'
    assert result.status_code==(409 if copy_fails else 200),result.text
    receipts=list((f.project/'.local-host-linked-sources').glob('*/snapshot.json'))
    assert len(receipts)==1 and not receipts[0].is_symlink()
    assert receipts[0].stat().st_ino!=outside.stat().st_ino
    assert json.loads(receipts[0].read_text())['state']==('failed' if copy_fails else 'complete')
    assert not f.controller.grants.rows


def test_preview_bounds_empty_directory_metadata_without_counting_directories_as_files(tmp_path,monkeypatch):
    import contextlib
    f=fixture(tmp_path);store=f.controller.linked_sources
    store.max_files=2;store.max_bytes=64;store.max_nodes=12
    (f.source/'legitimate-empty').mkdir()
    baseline=f.preview();assert baseline.status_code==200,baseline.text
    assert baseline.json()['file_count']==2 and baseline.json()['total_bytes']==18
    for index in range(200):(f.source/f'empty-{index}').mkdir()
    module=implementation();scandir=module.os.scandir;enumerated=[]
    @contextlib.contextmanager
    def observe(path):
        with scandir(path) as entries:
            def counted():
                for entry in entries:
                    enumerated.append(entry.name);yield entry
            yield counted() if path==f.source else entries
    monkeypatch.setattr(module.os,'scandir',observe)
    result=f.preview();assert result.status_code==422,result.text
    assert '12' in result.json()['detail'] and 'node' in result.json()['detail'].lower()
    assert 0<len(enumerated)<=13, 'enumeration must stop at the configured budget before sorting the directory'
    assert len(store.plans)==1 and len(f.helper.calls)==1


def test_preview_bounds_stored_metadata_bytes_before_retaining_a_plan(tmp_path):
    f=fixture(tmp_path);store=f.controller.linked_sources;store.max_plan_bytes=512
    result=f.preview();assert result.status_code==422,result.text
    assert 'metadata' in result.json()['detail'].lower() and '512' in result.json()['detail']
    assert not store.plans and len(f.helper.calls)==1


def test_uncertain_receipt_publication_retains_complete_attempt_and_failed_evidence(tmp_path,monkeypatch):
    f=fixture(tmp_path);module=implementation();replace=module.os.replace;failed=False
    def fail_first_publication(source,destination,**kwargs):
        nonlocal failed
        if destination=='snapshot.json' and not failed:
            failed=True;raise OSError('isolated publication failure')
        return replace(source,destination,**kwargs)
    monkeypatch.setattr(module.os,'replace',fail_first_publication)
    result=f.materialize(f.preview().json());assert result.status_code==409,result.text
    attempts=list((f.project/'.local-host-linked-sources').iterdir());assert len(attempts)==1
    assert json.loads((attempts[0]/'snapshot.json').read_text())['state']=='failed'
    uncertain=list(attempts[0].glob('.snapshot-*.json'));assert len(uncertain)==1
    assert json.loads(uncertain[0].read_text())['state']=='complete'
    assert (f.target/'one.png').read_bytes()==b'fixture-image' and (f.source/'plain.txt').read_bytes()==b'plain'
    assert not f.controller.linked_sources.snapshots and not f.controller.grants.rows


def test_shared_writer_retention_refuses_after_bounded_parent_enumeration(tmp_path,monkeypatch):
    import os
    module=implementation();project=tmp_path/'project';project.mkdir();parent=project/'.local-host-linked-sources';parent.mkdir()
    for index in range(200):(parent/f'owned-{index}').mkdir()
    parent_inode=parent.stat().st_ino;scandir=os.scandir;listdir=os.listdir;observed=[]
    def watched(fd):return isinstance(fd,int) and os.fstat(fd).st_ino==parent_inode
    def observe_listdir(fd):
        values=listdir(fd)
        if watched(fd):observed.extend(values)
        return values
    class ObserveScan:
        def __init__(self,fd):self.entries=scandir(fd);self.observe=watched(fd)
        def __enter__(self):return self
        def __exit__(self,*args):self.entries.close()
        def __iter__(self):return self
        def __next__(self):
            entry=next(self.entries)
            if self.observe:observed.append(entry.name)
            return entry
    monkeypatch.setattr(os,'listdir',observe_listdir);monkeypatch.setattr(os,'scandir',ObserveScan)
    with pytest.raises(ValueError,match='retention'):
        module.SnapshotWriter(project,3,module._identity(project)[:3])
    assert 0<len(observed)<=3, 'retention must refuse without enumerating the entire parent'
    assert len(listdir(parent))==200
