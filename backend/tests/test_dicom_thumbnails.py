"""Real DICOM thumbnail routes: the first source frame is a read-only representative."""
import hashlib
import io
from pathlib import Path

import numpy as np
import pytest
from PIL import Image
from backend.tests.test_dicom_input import dicom
from backend.tests.test_service_s3_01 import dataset_client, thumbnail


def expected_first_frame_jpeg():
    # Hand-derived MONOCHROME1 window 200/400 after slope 2 and intercept -100.
    values=np.arange(512,dtype=np.float64).reshape(16,32)*2-100
    gray=255-np.rint(np.clip((values-199.5)/399+.5,0,1)*255).astype(np.uint8)
    pixels=np.repeat(gray[:,:,None],3,axis=2)
    buffer=io.BytesIO()
    with Image.fromarray(pixels) as image:image.save(buffer,format='JPEG',quality=85)
    return buffer.getvalue()


@pytest.mark.parametrize('compressed',[False,True],ids=['uncompressed-multiframe','rle-multiframe'])
def test_multiframe_thumbnail_is_first_frame_without_changing_source_or_view_contract(dataset_client,dicom,compressed):
    import pydicom
    from pydicom.uid import RLELossless
    from backend.engine.dicom_input import read_dicom
    ds=pydicom.dcmread(dicom);ds.NumberOfFrames=2
    ds.PixelData+=np.zeros((16,32),dtype=np.int16).tobytes()
    if compressed:ds.compress(RLELossless)
    ds.save_as(dicom,enforce_file_format=True)
    before=dicom.read_bytes();identity=dicom.stat()
    response=thumbnail(dataset_client,dicom)
    assert response.status_code==200,response.text
    assert response.headers['content-type']=='image/jpeg'
    assert response.content==expected_first_frame_jpeg()
    with Image.open(io.BytesIO(response.content)) as image:
        assert image.size==(32,16)
        assert np.array_equal(np.asarray(image.convert('RGB')),np.asarray(Image.open(io.BytesIO(expected_first_frame_jpeg())).convert('RGB')))
    assert response.headers['cache-control']=='private, no-cache'
    assert thumbnail(dataset_client,dicom,**{'If-None-Match':response.headers['etag']}).status_code==304
    assert thumbnail(dataset_client,dicom).content==response.content
    # Only thumbnails choose a representative: callers opening an actual view
    # still must select a source frame, and frame one remains all white.
    with pytest.raises(ValueError,match='explicit frame_index'):read_dicom(dicom)
    frame_one,metadata=read_dicom(dicom,frame_index=1)
    try:assert metadata['frame_index']==1 and np.all(np.asarray(frame_one)==255)
    finally:frame_one.close()
    after=dicom.stat()
    assert dicom.read_bytes()==before
    assert (after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns,after.st_ctime_ns)==(identity.st_dev,identity.st_ino,identity.st_size,identity.st_mtime_ns,identity.st_ctime_ns)


def test_single_frame_thumbnail_retains_header_window_pixels(dataset_client,dicom):
    before=dicom.read_bytes()
    response=thumbnail(dataset_client,dicom)
    assert response.status_code==200,response.text
    assert response.content==expected_first_frame_jpeg()
    assert dicom.read_bytes()==before


def test_corrupt_multiframe_thumbnail_refuses_without_publishing_cache(dataset_client,dicom,monkeypatch):
    import pydicom
    from backend.api import routes_dataset
    ds=pydicom.dcmread(dicom);ds.NumberOfFrames=2;ds.PixelData=b'\x00\x00'
    ds.save_as(dicom,enforce_file_format=True);before=dicom.read_bytes()
    response=thumbnail(dataset_client,dicom)
    assert response.status_code==400
    assert 'Cannot decode DICOM pixel data' in response.text
    assert list(routes_dataset.THUMBNAIL_CACHE_DIR.iterdir())==[]
    assert dicom.read_bytes()==before
