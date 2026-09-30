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
