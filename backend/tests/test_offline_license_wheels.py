"""Hash-pinned offline supplier archives provide exact-version raw license bytes."""
import hashlib
import json
import zipfile
import io
import tarfile
import pytest
from backend.tests.test_packaged_license_texts import frozen


def supplier(tmp_path,name='example',version='1.2'):
    root=tmp_path/'supplier';root.mkdir()
    wheel=root/'vendor.whl'
    with zipfile.ZipFile(wheel,'w') as archive:
        archive.writestr(name+'-'+version+'.dist-info/METADATA',f'Name: {name}\nVersion: {version}\n')
        archive.writestr(name+'-'+version+'.dist-info/licenses/LICENSE',b'Original vendor copyright and complete license bytes\n')
    manifest={'schema_version':1,'files':[{'path':wheel.name,'name':name,'version':version,
        'sha256':hashlib.sha256(wheel.read_bytes()).hexdigest(),'source_url':'https://files.pythonhosted.org/packages/vendor.whl'}]}
    path=root/'manifest.json';path.write_text(json.dumps(manifest))
    return path,wheel,manifest


def test_exact_vendor_bytes_fill_missing_frozen_text_without_a_release_grant(tmp_path):
    from scripts.package_license_texts import collect_frozen_licenses,verify_license_bundle
    build,original=frozen(tmp_path);original.unlink();toc=tmp_path/'PYZ-00.toc';toc.write_text('[]')
    manifest,wheel,_=supplier(tmp_path)
    result=collect_frozen_licenses(build,toc,tmp_path/'licenses',supplier_manifest=manifest)
    assert result['status']=='collected' and result['missing']==[]
    assert result['public_distribution_approved'] is False
    assert len(result['supplier_archives'])==1
    row=result['files'][0]
    assert (tmp_path/'licenses'/row['path']).read_bytes()==b'Original vendor copyright and complete license bytes\n'
    assert str(tmp_path) not in json.dumps(result)
    assert result['supplier_archives'][0]['sha256']==hashlib.sha256(wheel.read_bytes()).hexdigest()
    verify_license_bundle(tmp_path/'licenses')


@pytest.mark.parametrize('damage',['hash','version','linked','traversal','duplicate','empty','unsafe_url'])
def test_invalid_offline_supplier_never_replaces_missing_license_truth(tmp_path,damage):
    from scripts.package_license_texts import collect_frozen_licenses
    build,original=frozen(tmp_path);original.unlink();toc=tmp_path/'PYZ-00.toc';toc.write_text('[]')
    manifest,wheel,value=supplier(tmp_path)
    if damage=='hash':wheel.write_bytes(b'changed wheel')
    elif damage=='version':value['files'][0]['version']='2.0'
    elif damage=='linked':
        other=wheel.with_name('original');wheel.rename(other);wheel.symlink_to(other)
    elif damage=='duplicate':value['files'].append(dict(value['files'][0]))
    elif damage=='unsafe_url':value['files'][0]['source_url']='https://example.invalid/private.whl'
    else:
        with zipfile.ZipFile(wheel,'a') as archive:
            if damage=='traversal':archive.writestr('../LICENSE',b'outside')
            else:archive.writestr('LICENSE',b'')
        value['files'][0]['sha256']=hashlib.sha256(wheel.read_bytes()).hexdigest()
    manifest.write_text(json.dumps(value))
    with pytest.raises(ValueError):collect_frozen_licenses(build,toc,tmp_path/'licenses',supplier_manifest=manifest)
    assert not (tmp_path/'licenses/manifest.json').exists()


def test_other_version_supplier_does_not_fill_current_missing_text(tmp_path):
    from scripts.package_license_texts import collect_frozen_licenses
    build,original=frozen(tmp_path);original.unlink();toc=tmp_path/'PYZ-00.toc';toc.write_text('[]')
    manifest,_,_=supplier(tmp_path,version='0.9')
    result=collect_frozen_licenses(build,toc,tmp_path/'licenses',supplier_manifest=manifest)
    assert result['missing']==['license_text:example@1.2'] and result['files']==[]


@pytest.mark.parametrize('damage',[None,'link','foreign_metadata','duplicate','empty'])
def test_source_archive_reads_actual_bytes_and_refuses_unsafe_members(tmp_path,damage):
    from scripts.package_license_texts import collect_frozen_licenses
    build,original=frozen(tmp_path);original.unlink();toc=tmp_path/'PYZ-00.toc';toc.write_text('[]')
    manifest,wheel,value=supplier(tmp_path);source=wheel.with_suffix('.tar.gz')
    with tarfile.open(source,'w:gz') as archive:
        def add(name,data):
            item=tarfile.TarInfo(name);item.size=len(data);archive.addfile(item,io.BytesIO(data))
        add('example-1.2/PKG-INFO',b'Name: example\nVersion: 1.2\n')
        add('example-1.2/LICENSE',b'' if damage=='empty' else b'Original source license bytes\n')
        if damage=='foreign_metadata':add('example-1.2/pkg/PKG-INFO',b'Name: other\nVersion: 1.2\n')
        if damage=='duplicate':add('example-1.2/LICENSE',b'conflicting license')
        if damage=='link':
            item=tarfile.TarInfo('example-1.2/linked');item.type=tarfile.SYMTYPE;item.linkname='LICENSE';archive.addfile(item)
    value['files'][0].update(path=source.name,sha256=hashlib.sha256(source.read_bytes()).hexdigest())
    manifest.write_text(json.dumps(value))
    if damage:
        with pytest.raises(ValueError):collect_frozen_licenses(build,toc,tmp_path/'licenses',supplier_manifest=manifest)
    else:
        receipt=collect_frozen_licenses(build,toc,tmp_path/'licenses',supplier_manifest=manifest)
        assert receipt['missing']==[] and (tmp_path/'licenses'/receipt['files'][0]['path']).read_bytes()==b'Original source license bytes\n'


def upstream(tmp_path):
    manifest,wheel,value=supplier(tmp_path)
    with zipfile.ZipFile(wheel,'a') as archive:
        archive.writestr('example-1.2.dist-info/UPSTREAM',b'ignored')
    # No code is executed: exact wheel metadata names the upstream repository.
    with zipfile.ZipFile(wheel,'w') as archive:
        archive.writestr('example-1.2.dist-info/METADATA',b'Name: example\nVersion: 1.2\nProject-URL: Source, https://github.com/vendor/example\n')
    commit='a'*40;root=manifest.parent
    license_file=root/'LICENSE.upstream';license_file.write_bytes(b'Version-bound original upstream license\n')
    ref=root/'release-ref.json';ref.write_text(json.dumps({'ref':'refs/tags/v1.2','url':'https://api.github.com/repos/vendor/example/git/refs/tags/v1.2','object':{'type':'commit','sha':commit,'url':'https://api.github.com/repos/vendor/example/git/commits/'+commit}}))
    def record(path):return {'path':path.name,'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
    value['files']=[{'kind':'upstream_license','name':'example','version':'1.2',**record(license_file),
        'source_url':'https://raw.githubusercontent.com/vendor/example/'+commit+'/LICENSE',
        'package_archive':{**record(wheel),'source_url':'https://files.pythonhosted.org/packages/vendor.whl'},
        'release_ref':record(ref)}]
    manifest.write_text(json.dumps(value));return manifest,value


def test_exact_upstream_release_license_keeps_wheel_and_commit_provenance(tmp_path):
    from scripts.package_license_texts import collect_frozen_licenses
    build,original=frozen(tmp_path);original.unlink();toc=tmp_path/'PYZ-00.toc';toc.write_text('[]')
    manifest,value=upstream(tmp_path)
    receipt=collect_frozen_licenses(build,toc,tmp_path/'licenses',supplier_manifest=manifest)
    assert receipt['missing']==[] and receipt['public_distribution_approved'] is False
    assert (tmp_path/'licenses'/receipt['files'][0]['path']).read_bytes()==b'Version-bound original upstream license\n'
    assert receipt['supplier_archives'][0]['upstream_commit']=='a'*40
    assert receipt['supplier_archives'][0]['package_sha256']==value['files'][0]['package_archive']['sha256']


@pytest.mark.parametrize('damage',['repository','commit','tag_version','license_hash','package_version','ref_hash'])
def test_upstream_license_cannot_replace_version_repository_or_original_bytes(tmp_path,damage):
    from scripts.package_license_texts import collect_frozen_licenses
    build,original=frozen(tmp_path);original.unlink();toc=tmp_path/'PYZ-00.toc';toc.write_text('[]')
    manifest,value=upstream(tmp_path);row=value['files'][0]
    if damage=='repository':row['source_url']=row['source_url'].replace('vendor/example','other/example')
    elif damage=='commit':row['source_url']=row['source_url'].replace('a'*40,'b'*40)
    elif damage=='license_hash':row['sha256']='0'*64
    elif damage=='package_version':row['version']='2.0'
    elif damage=='ref_hash':row['release_ref']['sha256']='0'*64
    else:
        ref=manifest.parent/row['release_ref']['path'];proof=json.loads(ref.read_text());proof['ref']='refs/tags/v0.9';ref.write_text(json.dumps(proof));row['release_ref']['sha256']=hashlib.sha256(ref.read_bytes()).hexdigest()
    manifest.write_text(json.dumps(value))
    with pytest.raises(ValueError):collect_frozen_licenses(build,toc,tmp_path/'licenses',supplier_manifest=manifest)
    assert not (tmp_path/'licenses').exists()


def test_annotated_tag_and_monorepo_component_version_are_bound_to_same_commit(tmp_path):
    from scripts.package_license_texts import _supplier_licenses
    manifest,value=upstream(tmp_path);row=value['files'][0];root=manifest.parent
    ref=root/row['release_ref']['path'];proof=json.loads(ref.read_text());commit=dict(proof['object'])
    tag_sha='b'*40;tag='suite@4.0';tag_url='https://api.github.com/repos/vendor/example/git/tags/'+tag_sha
    proof.update(ref='refs/tags/'+tag,url='https://api.github.com/repos/vendor/example/git/refs/tags/'+tag)
    proof['object']={'type':'tag','sha':tag_sha,'url':tag_url};ref.write_text(json.dumps(proof))
    tag_file=root/'annotated.json';tag_file.write_text(json.dumps({'sha':tag_sha,'url':tag_url,'tag':tag,'object':commit}))
    version_file=root/'package.json';version_file.write_text(json.dumps({'name':'example','version':'1.2'}))
    def record(path):return {'path':path.name,'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
    row['release_ref']=record(ref);row['release_tag']=record(tag_file)
    row['version_file']={**record(version_file),'source_url':'https://raw.githubusercontent.com/vendor/example/'+'a'*40+'/component/package.json'}
    manifest.write_text(json.dumps(value))
    record_out=_supplier_licenses(manifest)['example@1.2'][1]
    assert record_out['release_tag_sha256']==row['release_tag']['sha256']
    version_file.write_text(json.dumps({'name':'example','version':'1.3'}));row['version_file'].update(record(version_file));manifest.write_text(json.dumps(value))
    with pytest.raises(ValueError,match='source version'):_supplier_licenses(manifest)


def test_supplier_provenance_is_part_of_build_identity_and_compiler_is_snapshotted(tmp_path,monkeypatch):
    from scripts import build_backend_binary as builder
    manifest,_=upstream(tmp_path)
    monkeypatch.setattr(builder,'DEPENDENCIES',());monkeypatch.setattr(builder,'check_pyinstaller',lambda:False)
    root=__import__('pathlib').Path(__file__).resolve().parents[2]
    bare=builder.dependency_inventory(root);supplied=builder.dependency_inventory(root,supplier_manifest=manifest)
    assert bare['build_identity_sha256']!=supplied['build_identity_sha256']
    assert supplied['license_supplier']['manifest_sha256']==hashlib.sha256(manifest.read_bytes()).hexdigest()
    assert 'scripts/build_backend_binary.py' in {r['path'] for r in supplied['resources']}
    assert '--exclude-module=sitecustomize' in builder.pyinstaller_command(root,tmp_path/'output','Darwin')


def test_direct_script_execution_resolves_offline_suppliers_without_repository_pythonpath(tmp_path):
    import os,subprocess,sys
    from pathlib import Path
    manifest,_=upstream(tmp_path);root=Path(__file__).resolve().parents[2]
    script="""
import runpy,sys
values=runpy.run_path(sys.argv[1]);function=values['dependency_inventory']
function.__globals__['DEPENDENCIES']=()
function.__globals__['check_pyinstaller']=lambda:False
result=function(values['ROOT_DIR'],supplier_manifest=sys.argv[2])
assert len(result['license_supplier']['archives'])==1
"""
    result=subprocess.run([sys.executable,'-c',script,str(root/'scripts/build_backend_binary.py'),str(manifest)],
        cwd=tmp_path,env={**os.environ,'PYTHONPATH':''},capture_output=True,text=True,timeout=30)
    assert result.returncode==0,result.stderr
