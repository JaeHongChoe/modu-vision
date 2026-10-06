"""Extension manifests describe reviewed contracts; they never load code."""
import pytest
from pydantic import ValidationError
from backend.contracts.extensions import ExtensionManifest, admit_extension


def manifest(**changes):
    return ExtensionManifest.model_validate({
        'id':'example.camera', 'version':'1.2.3', 'protocol_version':1, 'kind':'input',
        'adapter_sha256':'a'*64, 'license':'MIT', 'permissions':['source.read'], **changes})


def test_input_adapter_contract_is_admitted_only_with_pinned_source_and_explicit_grants():
    row=manifest()
    result=admit_extension(row,reviewed_sources={'example.camera':'a'*64},granted_permissions={'source.read'},approved_licenses={'MIT'})
    assert result.allowed and result.reasons == ()
    assert result.execution == 'reviewed_static_adapter_only'
    assert row.model_dump()['adapter_sha256']=='a'*64


@pytest.mark.parametrize('changes',[
    {'entrypoint':'untrusted.py'}, {'version':'latest'}, {'protocol_version':2},
    {'adapter_sha256':'wrong'}, {'permissions':['shell.execute']}, {'license':''},
    {'permissions':['source.read','source.read']}, {'id':'../untrusted'},
])
def test_untrusted_or_ambiguous_extension_manifest_is_rejected(changes):
    with pytest.raises(ValidationError):manifest(**changes)


@pytest.mark.parametrize('changes,sources,grants,licenses,reason',[
    ({},{}, {'source.read'}, {'MIT'}, 'source_not_reviewed'),
    ({},{'example.camera':'b'*64}, {'source.read'}, {'MIT'}, 'source_hash_changed'),
    ({},{'example.camera':'a'*64}, set(), {'MIT'}, 'permission_not_granted:source.read'),
    ({},{'example.camera':'a'*64}, {'source.read'}, set(), 'license_not_approved:MIT'),
    ({'permissions':['result.write']},{'example.camera':'a'*64}, {'result.write'}, {'MIT'}, 'permission_incompatible:result.write'),
])
def test_manifest_cannot_assert_its_own_review_permission_or_license(changes,sources,grants,licenses,reason):
    result=admit_extension(manifest(**changes),reviewed_sources=sources,granted_permissions=grants,approved_licenses=licenses)
    assert not result.allowed and reason in result.reasons
    assert result.execution == 'refused'


@pytest.mark.parametrize('kind,permission',[('model','model.infer'),('input','source.read'),('delivery','result.write'),('storage','artifact.store')])
def test_all_four_adapter_kinds_have_separate_capability_boundaries(kind,permission):
    row=manifest(kind=kind,permissions=[permission])
    result=admit_extension(row,reviewed_sources={row.id:row.adapter_sha256},granted_permissions={permission},approved_licenses={'MIT'})
    assert result.allowed


def test_contributor_camera_fixture_uses_the_real_bounded_camera_contract():
    import hashlib
    from pathlib import Path
    from examples.extensions.fixture_camera import open_fixture_camera
    from backend.engine.camera_adapters import CameraAdapterFactory, read_frame
    from backend.contracts.extensions import create_reviewed_camera
    source=Path(__file__).resolve().parents[2]/'examples/extensions/fixture_camera.py'
    digest=hashlib.sha256(source.read_bytes()).hexdigest()
    row=manifest(adapter_sha256=digest)
    factory=CameraAdapterFactory('simulator',open_fixture_camera)
    camera=create_reviewed_camera(row,source,factory,'finite-fixture',reviewed_sources={row.id:digest},
        granted_permissions={'source.read'},approved_licenses={'MIT'})
    opened,frame=read_frame(camera)
    assert opened and frame.shape==(32,48,3) and frame.dtype.name=='uint8'
    frame[:]=255
    assert read_frame(camera)==(False,None)
    camera.release();assert not camera.isOpened()


def test_admission_refuses_unreviewed_camera_before_opening_it(tmp_path):
    from backend.contracts.extensions import create_reviewed_camera
    from backend.engine.camera_adapters import CameraAdapterFactory
    calls=[]
    factory=CameraAdapterFactory('simulator',lambda source:calls.append(source))
    with pytest.raises(ValueError,match='source_not_reviewed'):
        create_reviewed_camera(manifest(),tmp_path/'not-read.py',factory,'source',reviewed_sources={},
            granted_permissions={'source.read'},approved_licenses={'MIT'})
    assert not calls


def test_source_pin_refuses_linked_parent_before_camera_open(tmp_path):
    from pathlib import Path
    from backend.contracts.extensions import _reviewed_source_sha256
    source=Path(__file__).resolve().parents[2]/'examples/extensions/fixture_camera.py'
    alias=tmp_path/'linked';alias.symlink_to(source.parent,target_is_directory=True)
    with pytest.raises(ValueError,match='linked paths'):
        _reviewed_source_sha256(alias/source.name)


def test_source_pin_refuses_growth_during_bounded_read(tmp_path,monkeypatch):
    import os
    from backend.contracts.extensions import _reviewed_source_sha256
    source=tmp_path/'fixture.py';source.write_bytes(b'initial')
    real=os.read;changed=False
    def grow(fd,count):
        nonlocal changed
        if not changed:
            with source.open('ab') as writer:writer.write(b'x'*65536)
            changed=True
        return real(fd,count)
    monkeypatch.setattr(os,'read',grow)
    with pytest.raises(ValueError,match='grew'):
        _reviewed_source_sha256(source)
