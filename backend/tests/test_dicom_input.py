import hashlib
import sys
from pathlib import Path
import numpy as np
import pytest
from PIL import Image
from backend.tests.test_dataset_metadata_api import client_workspace


@pytest.fixture
def dicom(tmp_path):
    pydicom=pytest.importorskip('pydicom')
    from pydicom.dataset import FileDataset,FileMetaDataset
    from pydicom.uid import ExplicitVRLittleEndian,generate_uid
    meta=FileMetaDataset();meta.TransferSyntaxUID=ExplicitVRLittleEndian
    meta.MediaStorageSOPClassUID=generate_uid();meta.MediaStorageSOPInstanceUID=generate_uid()
    path=tmp_path/'xray.dcm';ds=FileDataset(str(path),{},file_meta=meta,preamble=b'\0'*128)
    ds.Rows=16;ds.Columns=32;ds.SamplesPerPixel=1;ds.PhotometricInterpretation='MONOCHROME1'
    ds.BitsAllocated=16;ds.BitsStored=16;ds.HighBit=15;ds.PixelRepresentation=1
    ds.PixelSpacing=[.2,.3];ds.RescaleSlope=2;ds.RescaleIntercept=-100
    ds.WindowCenter=200;ds.WindowWidth=400
    ds.PixelData=np.arange(512,dtype=np.int16).reshape(16,32).tobytes();ds.save_as(path,enforce_file_format=True)
    return path


def test_dicom_windowing_owned_view_and_source_hash(dicom,tmp_path):
    from backend.engine.dicom_input import read_dicom,normalized_view
    original=dicom.read_bytes();image,metadata=read_dicom(dicom,window_center=200,window_width=400)
    pixels=np.asarray(image)
    assert image.size==(32,16) and pixels.shape==(16,32,3)
    assert pixels[0,0,0]==255 and pixels[-1,-1,0]==0
    assert metadata['pixel_spacing_mm']==[.2,.3]
    assert metadata['source_sha256']==hashlib.sha256(original).hexdigest()
    receipt=normalized_view(dicom,tmp_path/'owned',window_center=200,window_width=400)
    assert Path(receipt['view_path']).is_file() and receipt['width']==32 and receipt['height']==16
    assert receipt['view_sha256']==hashlib.sha256(Path(receipt['view_path']).read_bytes()).hexdigest()
    assert dicom.read_bytes()==original
    second=normalized_view(dicom,tmp_path/'owned',window_center=100,window_width=200)
    assert second['view_path']!=receipt['view_path']


def test_dicom_is_in_source_loader_without_replacing_existing_formats(dicom):
    from backend.engine.dataset_loaders import SUPPORTED_IMAGE_EXTENSIONS,_read_image_rgb,validate_image_file
    assert {'.dcm','.dicom','.jpg','.jpeg','.png','.bmp','.tif','.tiff','.webp'}<=SUPPORTED_IMAGE_EXTENSIONS
    assert _read_image_rgb(dicom).shape==(16,32,3)
    assert validate_image_file(dicom).valid


def test_missing_pydicom_has_explicit_error(monkeypatch,tmp_path):
    from backend.engine.dicom_input import read_dicom
    monkeypatch.setitem(sys.modules,'pydicom',None)
    with pytest.raises(ValueError,match='pydicom'):read_dicom(tmp_path/'x.dcm')


def test_dicom_rejects_invalid_window_and_multiframe(dicom):
    from backend.engine.dicom_input import read_dicom
    with pytest.raises(ValueError,match='width'):read_dicom(dicom,window_width=0)
    import pydicom
    ds=pydicom.dcmread(dicom);ds.NumberOfFrames=2;ds.PixelData*=2;ds.save_as(dicom,enforce_file_format=True)
    with pytest.raises(ValueError,match='frame'):read_dicom(dicom)


@pytest.mark.parametrize('header,options,reason', [
    ({'Rows':65535,'Columns':65535}, {}, '128 megapixel'),
    ({'Rows':0}, {}, 'positive'),
    ({'NumberOfFrames':2}, {}, 'explicit frame_index'),
    ({'NumberOfFrames':2}, {'frame_index':2}, 'outside available frames'),
    ({'NumberOfFrames':2}, {'frame_index':True}, 'integer'),
])
def test_dicom_header_limits_refuse_before_pixel_decoding(dicom,header,options,reason):
    import pydicom
    from backend.engine.dicom_input import read_dicom
    ds=pydicom.dcmread(dicom)
    for key,value in header.items():setattr(ds,key,value)
    # Invalid pixel bytes expose ordering: a decoder error must not hide the
    # header refusal or allocate the dimensions declared by an untrusted file.
    ds.PixelData=b'\x00\x00';ds.save_as(dicom,enforce_file_format=True)
    original=dicom.read_bytes()
    with pytest.raises(ValueError,match=reason):read_dicom(dicom,**options)
    assert dicom.read_bytes()==original


def test_dicom_explicit_frame_preserves_pixels_and_original(dicom):
    import pydicom
    from backend.engine.dicom_input import read_dicom
    ds=pydicom.dcmread(dicom);ds.NumberOfFrames=2
    ds.PixelData+=np.zeros((16,32),dtype=np.int16).tobytes()
    ds.save_as(dicom,enforce_file_format=True);original=dicom.read_bytes()
    first,first_metadata=read_dicom(dicom,frame_index=0)
    second,second_metadata=read_dicom(dicom,frame_index=1)
    assert np.asarray(first).min()==0 and np.asarray(first).max()==255
    assert np.all(np.asarray(second)==255)
    assert [first_metadata['frame_index'],second_metadata['frame_index']]==[0,1]
    assert dicom.read_bytes()==original


def test_dicom_api_source_metadata_owned_view_and_default_raw(client_workspace,dicom):
    from backend.api import routes_dicom,routes_dataset
    from backend.engine.annotation_storage import set_request_project_root,reset_request_project_root
    client,project,source=client_workspace;target=source/dicom.name;target.write_bytes(dicom.read_bytes());original=target.read_bytes()
    client.app.include_router(routes_dicom.router);client.app.include_router(routes_dataset.router)
    rows=client.get('/api/dataset/metadata').json()['items'];row=next(r for r in rows if r['file_path']==str(target))
    assert row['width']==32 and row['height']==16
    response=client.post('/api/dataset/dicom/view',json={'image_path':str(target),'window_center':200,'window_width':400})
    assert response.status_code==200,response.text
    receipt=response.json();assert client.get(receipt['display_url']).status_code==200
    token=set_request_project_root(Path(project['project_dir']))
    try:raw=client.get('/api/dataset/raw/xray.dcm',params={'file_path':str(target)})
    finally:reset_request_project_root(token)
    assert raw.status_code==200,raw.text
    assert Image.open(__import__('io').BytesIO(raw.content)).size==(32,16)
    assert target.read_bytes()==original


@pytest.mark.parametrize('frame',[False,0.0,'0'])
def test_dicom_api_requires_an_explicit_integer_frame(client_workspace,dicom,frame):
    from backend.api import routes_dicom
    client,project,source=client_workspace;target=source/dicom.name
    target.write_bytes(dicom.read_bytes());original=target.read_bytes()
    client.app.include_router(routes_dicom.router)
    response=client.post('/api/dataset/dicom/view',json={'image_path':str(target),'frame_index':frame})
    assert response.status_code==422,response.text
    assert target.read_bytes()==original
    assert not (Path(project['project_dir'])/'dicom_views').exists()


def test_dicom_cached_png_must_match_the_reproducible_display(dicom,tmp_path):
    from backend.engine.dicom_input import normalized_view
    receipt=normalized_view(dicom,tmp_path/'views');target=Path(receipt['view_path'])
    altered=b'corrupt owned display cache';target.write_bytes(altered)
    original=dicom.read_bytes()
    with pytest.raises(ValueError,match='integrity'):
        normalized_view(dicom,tmp_path/'views')
    assert target.read_bytes()==altered and dicom.read_bytes()==original


def test_dicom_cached_receipt_cannot_rebind_original_identity(dicom,tmp_path):
    import json
    from backend.engine.dicom_input import normalized_view
    receipt=normalized_view(dicom,tmp_path/'views');path=Path(receipt['view_path']).with_suffix('.json')
    edited={**receipt,'source_sha256':'f'*64};path.write_text(json.dumps(edited))
    with pytest.raises(ValueError,match='integrity'):
        normalized_view(dicom,tmp_path/'views')
    assert json.loads(path.read_text())==edited


def test_dicom_cache_never_follows_a_linked_parent(dicom,tmp_path):
    from backend.engine.dicom_input import normalized_view
    outside=tmp_path/'outside';outside.mkdir();link=tmp_path/'linked';link.symlink_to(outside,target_is_directory=True)
    with pytest.raises(ValueError,match='link'):
        normalized_view(dicom,link/'views')
    assert list(outside.iterdir())==[]


def test_dicom_partial_png_is_never_published_after_write_failure(dicom,tmp_path,monkeypatch):
    from backend.engine.dicom_input import normalized_view
    def interrupted(image,stream,*args,**kwargs):
        if hasattr(stream,'write'):stream.write(b'partial-image')
        else:Path(stream).write_bytes(b'partial-image')
        raise OSError('controlled display encoding interruption')
    monkeypatch.setattr(Image.Image,'save',interrupted)
    root=tmp_path/'views'
    with pytest.raises(OSError,match='interruption'):normalized_view(dicom,root)
    assert not root.exists() or list(root.glob('*.png'))==[]


def test_dicom_receipt_interruption_recovers_exact_completed_png(dicom,tmp_path,monkeypatch):
    from backend.engine import runtime_process_control
    from backend.engine.dicom_input import normalized_view
    original=runtime_process_control.atomic_private_json
    def fail(*args,**kwargs):raise OSError('controlled receipt interruption')
    monkeypatch.setattr(runtime_process_control,'atomic_private_json',fail)
    root=tmp_path/'views'
    with pytest.raises(OSError,match='receipt interruption'):normalized_view(dicom,root)
    files=list(root.glob('*.png'));assert len(files)==1
    before=files[0].read_bytes()
    monkeypatch.setattr(runtime_process_control,'atomic_private_json',original)
    receipt=normalized_view(dicom,root)
    assert files[0].read_bytes()==before
    assert receipt['view_sha256']==hashlib.sha256(before).hexdigest()
    assert len(list(root.glob('*.json')))==1


@pytest.mark.parametrize('changed',['png','source','receipt'])
def test_dicom_display_refuses_stale_or_tampered_cached_identity(client_workspace,dicom,changed):
    import json
    from backend.api import routes_dicom
    client,project,source=client_workspace;target=source/dicom.name
    target.write_bytes(dicom.read_bytes());client.app.include_router(routes_dicom.router)
    prepared=client.post('/api/dataset/dicom/view',json={'image_path':str(target)})
    assert prepared.status_code==200,prepared.text
    receipt=prepared.json();cache=Path(receipt['view_path']);metadata=cache.with_suffix('.json')
    if changed=='png':cache.write_bytes(b'corrupt display bytes')
    elif changed=='source':target.write_bytes(target.read_bytes()+b'changed original')
    else:metadata.write_text(json.dumps({**receipt,'source_sha256':'f'*64}))
    before={p:p.read_bytes() for p in (target,cache,metadata)}
    response=client.get(receipt['display_url'])
    assert response.status_code==422,response.text
    assert 'integrity' in response.json()['detail']
    assert all(p.read_bytes()==content for p,content in before.items())


def test_dicom_automatic_window_display_reopens_without_changed_pixels(client_workspace,dicom):
    import pydicom
    from backend.api import routes_dicom
    client,project,source=client_workspace;target=source/dicom.name
    ds=pydicom.dcmread(dicom);del ds.WindowCenter;del ds.WindowWidth
    ds.PixelData=(np.arange(512,dtype=np.int16)%3).tobytes()
    ds.save_as(target,enforce_file_format=True);client.app.include_router(routes_dicom.router)
    prepared=client.post('/api/dataset/dicom/view',json={'image_path':str(target)})
    assert prepared.status_code==200,prepared.text
    receipt=prepared.json();response=client.get(receipt['display_url'])
    assert response.status_code==200,response.text
    assert hashlib.sha256(response.content).hexdigest()==receipt['view_sha256']
    assert response.headers['cache-control']=='no-store'


@pytest.mark.parametrize('mode',['monochrome','rgb','multi'])
def test_actual_builtin_rle_decoder_preserves_selected_source_pixels(dicom,tmp_path,mode):
    import pydicom
    from pydicom.uid import RLELossless
    from backend.engine.dicom_input import read_dicom,normalized_view,read_cached_view
    ds=pydicom.dcmread(dicom);options={}
    if mode=='rgb':
        ds.SamplesPerPixel=3;ds.PhotometricInterpretation='RGB';ds.PlanarConfiguration=0
        ds.BitsAllocated=8;ds.BitsStored=8;ds.HighBit=7;ds.PixelRepresentation=0
        ds.PixelData=np.arange(16*32*3,dtype=np.uint8).reshape(16,32,3).tobytes()
    elif mode=='multi':
        ds.NumberOfFrames=2;ds.PixelData+=np.zeros((16,32),dtype=np.int16).tobytes();options={'frame_index':1}
    ds.save_as(dicom,enforce_file_format=True)
    reference,reference_meta=read_dicom(dicom,**options)
    ds.compress(RLELossless)
    compressed=tmp_path/'compressed.dcm';ds.save_as(compressed,enforce_file_format=True)
    original=compressed.read_bytes();actual,metadata=read_dicom(compressed,**options)
    assert np.array_equal(np.asarray(reference),np.asarray(actual))
    assert metadata['transfer_syntax_uid']==str(RLELossless)
    assert metadata['frame_index']==reference_meta['frame_index']
    receipt=normalized_view(compressed,tmp_path/'rle-views',**options)
    viewed=read_cached_view(receipt['view_id'],tmp_path/'rle-views',resolve_source=lambda _:compressed)
    assert hashlib.sha256(viewed).hexdigest()==receipt['view_sha256']
    assert compressed.read_bytes()==original
