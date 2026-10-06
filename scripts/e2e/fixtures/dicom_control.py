"""Synthetic, non-patient DICOM bytes for an owned optional-runtime GUI run."""
import hashlib
import json
from pathlib import Path
import sys
import numpy as np
from pydicom.dataset import FileDataset,FileMetaDataset
from pydicom.uid import ExplicitVRLittleEndian,generate_uid

root=Path(sys.argv[1]);root.mkdir(exist_ok=False)
path=root/'part.dcm';meta=FileMetaDataset();meta.TransferSyntaxUID=ExplicitVRLittleEndian
meta.MediaStorageSOPClassUID=generate_uid();meta.MediaStorageSOPInstanceUID=generate_uid()
ds=FileDataset(str(path),{},file_meta=meta,preamble=b'\0'*128)
ds.Rows=16;ds.Columns=32;ds.SamplesPerPixel=1;ds.PhotometricInterpretation='MONOCHROME1'
ds.BitsAllocated=16;ds.BitsStored=16;ds.HighBit=15;ds.PixelRepresentation=1
ds.PixelSpacing=[.2,.3];ds.RescaleSlope=2;ds.RescaleIntercept=-100
ds.WindowCenter=200;ds.WindowWidth=400;ds.Modality='OT'
ds.PixelData=np.arange(512,dtype=np.int16).reshape(16,32).tobytes()
ds.save_as(path,enforce_file_format=True)
print(json.dumps({'source':str(root),'file':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}))
