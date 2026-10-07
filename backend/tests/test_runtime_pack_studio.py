"""Studio uses reviewed inventory pins, isolated storage and fresh byte checks."""
import hashlib
import json
from pathlib import Path

import pytest

from backend.tests.test_product_delivery import ApiClient, project
from backend.tests.test_runtime_pack_inventory import module as packs


def fixture(tmp_path, *, target=None):
    from backend.engine import runtime_pack_store as studio
    p = project(tmp_path)
    source = Path(p['project_dir']) / 'source-pack'; source.mkdir()
    # A payload would change disk state if accidentally imported or executed.
    (source / 'never_execute.py').write_text('raise AssertionError("Pack executed")\n')
    host = target or studio.observed_target()
    manifest = packs.inventory_pack(source, {'id':'studio-fixture', 'version':'1.0.0',
        'kind':'ocr', 'platform':host['platform'], 'arch':host['arch'],
        'source':'https://example.invalid/reviewed-pack', 'license':'MIT',
        'driver_minimum':None, 'compatibility':host['compatibility'], 'files':['never_execute.py']})
    raw = packs.canonical_bytes(manifest)
    inventory = Path(p['project_dir']) / 'inventory.json'; inventory.write_bytes(raw)
    return studio, p, source, inventory, hashlib.sha256(raw).hexdigest()


def install(values):
    module,p,source,inventory,pin = values
    return module.install(p, source_dir=str(source), inventory_path=str(inventory), expected_sha256=pin)


def test_app_installs_reopens_and_rehashes_without_executing_payload(tmp_path):
    values=fixture(tmp_path); module,p,source,_,pin=values
    before=(source/'never_execute.py').read_bytes()
    row=install(values)
    assert row['integrity']=='verified' and row['target_compatible'] is True
    assert row['inventory_sha256']==pin and row['installed'] is True
    assert row['activated'] is False and row['signature_verified'] is False and row['execution_verified'] is False
    assert row['python_abi']['state']=='not_declared'
    assert row==install(values)
    report=module.inventory(p)
    assert report['packs']==[row] and report['project_id']==p['id']
    assert 'installation_path' not in row
    target=Path(p['project_dir'])/row['installation_relative_path']
    assert (target/'payload/never_execute.py').read_bytes()==before
    assert (source/'never_execute.py').read_bytes()==before
    assert not (Path(p['project_dir'])/'runtime_service').exists()
    assert module.inventory({**p,'id':'other-project'})['packs']==[]


@pytest.mark.parametrize('damage',['payload','receipt','inventory','extra','link'])
def test_fresh_readback_marks_damage_failed_and_never_repairs_or_activates(tmp_path,damage):
    values=fixture(tmp_path);module,p,source,_,_=values;row=install(values)
    target=Path(p['project_dir'])/row['installation_relative_path']
    if damage=='payload': (target/'payload/never_execute.py').write_bytes(b'tampered')
    elif damage=='receipt':
        receipt=json.loads((target/'receipt.json').read_bytes());receipt['activated']=True
        (target/'receipt.json').write_text(json.dumps(receipt))
    elif damage=='inventory': (target/'inventory.json').write_bytes(b'{}')
    elif damage=='extra': (target/'extra.py').write_bytes(b'not admitted')
    else:
        (target/'payload/never_execute.py').unlink()
        (target/'payload/never_execute.py').symlink_to(source/'never_execute.py')
    read=module.inventory(p)['packs'][0]
    assert read['integrity']=='failed' and read['activated'] is False and read['target_compatible'] is False
    with pytest.raises(ValueError):install(values)
    assert module.inventory(p)['packs'][0]['integrity']=='failed'


def test_target_change_keeps_byte_integrity_separate_from_compatibility(tmp_path,monkeypatch):
    values=fixture(tmp_path);module,p,*_=values;install(values)
    original=module.observed_target()
    monkeypatch.setattr(module,'observed_target',lambda:{**original,'platform':'windows' if original['platform']!='windows' else 'linux'})
    read=module.inventory(p)['packs'][0]
    assert read['integrity']=='verified' and read['target_compatible'] is False
    assert read['compatibility_error'] and read['activated'] is False


@pytest.mark.parametrize('damage',['wrong_pin','linked_inventory','linked_source','foreign_target','inside_source','linked_store'])
def test_install_refuses_unreviewed_or_linked_inputs(tmp_path,damage):
    values=list(fixture(tmp_path));module,p,source,inventory,pin=values
    if damage=='wrong_pin':values[4]='0'*64
    elif damage=='linked_inventory':
        alias=tmp_path/'linked.json';alias.symlink_to(inventory);values[3]=alias
    elif damage=='linked_source':
        alias=tmp_path/'linked-source';alias.symlink_to(source);values[2]=alias
    elif damage=='foreign_target':
        data=json.loads(inventory.read_bytes());data['compatibility']['worker']+=1
        raw=packs.canonical_bytes(data);inventory.write_bytes(raw);values[4]=hashlib.sha256(raw).hexdigest()
    elif damage=='inside_source':values[1]={**p,'project_dir':str(source)}
    else:
        directory=Path(p['project_dir'])/'delivery';directory.mkdir()
        (directory/'runtime-packs').symlink_to(source)
    with pytest.raises(ValueError):install(values)
    assert (source/'never_execute.py').read_text().startswith('raise AssertionError')


def test_registry_pin_and_scope_cannot_be_overridden_by_installation_receipt(tmp_path):
    values=fixture(tmp_path);module,p,*_=values;install(values)
    registry=next((Path(p['project_dir'])/'delivery/runtime-packs').glob('*/admissions.json'))
    record=json.loads(registry.read_bytes());record['project_id']='foreign-project'
    registry.write_text(json.dumps(record))
    with pytest.raises(ValueError,match='scope'):module.inventory(p)


def test_api_requires_project_owner_and_server_admin_and_redacts_diagnostics(tmp_path):
    from fastapi import FastAPI
    from backend.api.routes_product_delivery import router
    values=fixture(tmp_path);module,p,source,inventory,pin=values
    app=FastAPI();app.state.current_project=p;app.include_router(router)
    current={'id':'admin','administrator':True};role={'value':'owner'}
    class Accounts:
        def project_role(self,*_):return role['value']
    app.state.accounts=Accounts()
    @app.middleware('http')
    async def account(request,call_next):
        request.state.account_user=current
        request.state.scoped_project=p
        return await call_next(request)
    client=ApiClient(app);body={'source_dir':str(source),'inventory_path':str(inventory),'expected_sha256':pin}
    for admin,project_role in [(False,'owner'),(True,'viewer'),(True,'trainer')]:
        current['administrator']=admin;role['value']=project_role
        assert client.post('/api/product-delivery/runtime-packs/install',json=body).status_code==403
    current['administrator']=True;role['value']='owner'
    response=client.post('/api/product-delivery/runtime-packs/install',json=body)
    assert response.status_code==200,response.text
    assert response.json()['integrity']=='verified'
    report=client.get('/api/product-delivery/runtime-packs').json()
    assert report['packs'][0]['inventory_sha256']==pin
    diagnostic=client.post('/api/product-delivery/diagnostics',json={'sections':['installation']})
    assert diagnostic.status_code==200,diagnostic.text
    assert diagnostic.json()['bundle']['installation']['runtime_packs']['packs'][0]['inventory_sha256']==pin
    encoded=json.dumps(diagnostic.json()['bundle'])
    assert str(tmp_path) not in encoded and 'reviewed-pack' not in encoded


@pytest.mark.parametrize('record',['source_dir','inventory_path'])
def test_engine_repeats_current_project_path_fence_for_desktop(tmp_path,record):
    values=fixture(tmp_path);module,p,source,inventory,pin=values
    body={'source_dir':str(source),'inventory_path':str(inventory),'expected_sha256':pin}
    outside=tmp_path/'outside';outside.mkdir()
    if record=='source_dir':
        (outside/'never_execute.py').write_bytes((source/'never_execute.py').read_bytes());body[record]=str(outside)
    else:
        file=outside/'inventory.json';file.write_bytes(inventory.read_bytes());body[record]=str(file)
    with pytest.raises(ValueError,match='active project'):module.install(p,**body)


def test_disk_capacity_is_separate_from_existing_installed_integrity(tmp_path,monkeypatch):
    values=fixture(tmp_path);module,p,*_=values;install(values)
    monkeypatch.setattr(module.shutil,'disk_usage',lambda *_:type('Disk',(),{'free':0})())
    assert module.inventory(p)['packs'][0]['integrity']=='verified'


def test_coordinated_inventory_payload_receipt_tampering_cannot_replace_admitted_pin(tmp_path):
    values=fixture(tmp_path);module,p,*_=values;row=install(values)
    target=Path(p['project_dir'])/row['installation_relative_path']
    payload=target/'payload/never_execute.py';payload.write_bytes(b'changed payload')
    inventory=target/'inventory.json';data=json.loads(inventory.read_bytes())
    data['files'][0].update(size=payload.stat().st_size,sha256=hashlib.sha256(payload.read_bytes()).hexdigest())
    data['total_bytes']=payload.stat().st_size;raw=packs.canonical_bytes(data);inventory.write_bytes(raw)
    receipt=target/'receipt.json';r=json.loads(receipt.read_bytes())
    r.update(files=data['files'],total_bytes=data['total_bytes'],estimated_staging_bytes=data['total_bytes']*3,inventory_sha256=hashlib.sha256(raw).hexdigest())
    receipt.write_bytes(packs.canonical_bytes(r))
    read=module.inventory(p)['packs'][0]
    assert read['integrity']=='failed' and read['inventory_sha256']==values[4]


@pytest.mark.parametrize('filename,compatible',[('pydicom-3.0.1-py3-none-any.whl',True),('provider-1.0.0-cp27-cp27m-win32.whl',False),('runtime.whl',False)])
def test_wheel_abi_observation_never_imports_payload(tmp_path,filename,compatible):
    from backend.engine import runtime_pack_store as module
    assert module._abi([{'path':filename}])['compatible'] is compatible


@pytest.mark.parametrize('kind',['oversize','directory','fifo'])
def test_bounded_record_read_refuses_special_or_unbounded_inputs(tmp_path,kind):
    from backend.engine import runtime_pack
    file=tmp_path/'record.json'
    if kind=='oversize':
        with file.open('wb') as f:f.truncate(1024*1024+1)
    elif kind=='directory':file.mkdir()
    else:
        import os
        if os.name=='nt':pytest.skip('POSIX FIFO admission boundary')
        os.mkfifo(file)
    with pytest.raises((ValueError,OSError)):runtime_pack.read_record(file)


@pytest.mark.parametrize('operation',['hash','copy'])
def test_payload_replaced_with_fifo_before_open_cannot_block_inspection(tmp_path,monkeypatch,operation):
    import os
    from backend.engine import runtime_pack
    if os.name=='nt':pytest.skip('POSIX FIFO admission race')
    source=tmp_path/'source';source.mkdir();file=source/'payload.bin';file.write_bytes(b'owned payload')
    record={'path':'payload.bin','size':file.stat().st_size,'sha256':hashlib.sha256(file.read_bytes()).hexdigest()}
    destination=tmp_path/'destination';destination.mkdir();original=os.open
    def replace(path,flags,*args,**kwargs):
        if Path(path)==file:
            assert flags&os.O_NONBLOCK,'Payload validation must not block on a raced FIFO'
            file.unlink();os.mkfifo(file)
        return original(path,flags,*args,**kwargs)
    monkeypatch.setattr(runtime_pack.os,'open',replace)
    with pytest.raises(ValueError):
        if operation=='hash':runtime_pack._hash(source,'payload.bin')
        else:runtime_pack._copy_payload(source,destination,[record])
