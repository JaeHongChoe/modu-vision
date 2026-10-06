"""Read-only DICOM decoding and reproducible project-owned display images."""
from __future__ import annotations
import hashlib
import json
import math
from pathlib import Path
import numpy as np
from PIL import Image

DICOM_EXTENSIONS = {'.dcm', '.dicom'}


def is_dicom(path):
    return Path(path).suffix.lower() in DICOM_EXTENSIONS


def _first(value, default=None):
    if value is None: return default
    if hasattr(value, '__iter__') and not isinstance(value, (str, bytes)): return float(value[0])
    return float(value)


def read_dicom(path, *, window_center=None, window_width=None, frame_index=None):
    try: import pydicom
    except ImportError as exc: raise ValueError('DICOM input requires optional pydicom in the backend Python environment (backend/requirements-dicom.txt).') from exc
    path=Path(path).resolve();source_bytes=path.read_bytes();digest=hashlib.sha256(source_bytes).hexdigest()
    try:
        import io
        ds=pydicom.dcmread(io.BytesIO(source_bytes))
    except Exception as exc: raise ValueError(f'Cannot decode DICOM pixel data; compressed input may require an optional pydicom decoder: {exc}') from exc
    try:
        h,w=int(ds.Rows),int(ds.Columns)
        frames=int(getattr(ds,'NumberOfFrames',1));channels=int(getattr(ds,'SamplesPerPixel',1))
    except (AttributeError,TypeError,ValueError) as exc:
        raise ValueError('DICOM requires valid rows, columns, frames and samples') from exc
    if min(h,w,frames)<=0:raise ValueError('DICOM dimensions and frame count must be positive')
    if h*w>128_000_000:raise ValueError('DICOM exceeds 128 megapixel resource guard')
    if frame_index is not None and type(frame_index) is not int:raise ValueError('DICOM frame_index must be an integer')
    if frames>1:
        if frame_index is None: raise ValueError('Multi-frame DICOM requires an explicit frame_index; one source frame must be selected.')
        if not 0<=frame_index<frames: raise ValueError('DICOM frame_index outside available frames')
    elif frame_index not in (None,0): raise ValueError('DICOM frame_index outside available frames')
    try:
        from pydicom.pixels import pixel_array
        # pydicom >=3 decodes only the requested frame; never materialize an
        # entire multi-frame pixel array to select one image after allocation.
        pixels=pixel_array(ds,index=frame_index if frames>1 else None)
    except Exception as exc:raise ValueError(f'Cannot decode DICOM pixel data; compressed input may require an optional pydicom decoder: {exc}') from exc
    center=_first(window_center,_first(getattr(ds,'WindowCenter',None)))
    width=_first(window_width,_first(getattr(ds,'WindowWidth',None)))
    if width is not None and (not math.isfinite(width) or width<=0): raise ValueError('DICOM window width must be positive and finite')
    if center is not None and not math.isfinite(center): raise ValueError('DICOM window center must be finite')
    slope=float(getattr(ds,'RescaleSlope',1));intercept=float(getattr(ds,'RescaleIntercept',0))
    photo=str(getattr(ds,'PhotometricInterpretation',''))
    if channels==1:
        if photo not in ('MONOCHROME1','MONOCHROME2'): raise ValueError(f'Unsupported DICOM photometric interpretation: {photo}')
        values=pixels.astype(np.float64)*slope+intercept
        if not np.isfinite(values).all(): raise ValueError('DICOM pixels contain non-finite values')
        if width is None or center is None:
            low,high=float(values.min()),float(values.max());center=(low+high)/2; width=max(1.,high-low)
            scaled=(values-low)/max(1.,high-low)
        elif width<=1: scaled=(values>center-.5).astype(float)
        else: scaled=(values-(center-.5))/(width-1)+.5
        gray=np.rint(np.clip(scaled,0,1)*255).astype(np.uint8)
        if photo=='MONOCHROME1':gray=255-gray
        rgb=np.repeat(gray[:,:,None],3,axis=2)
    elif channels==3 and pixels.shape==(h,w,3):
        # pydicom's default pixel_array converts YBR to RGB before this boundary.
        if pixels.dtype!=np.uint8:raise ValueError('Color DICOM currently requires 8-bit samples')
        rgb=pixels
    else: raise ValueError('Unsupported DICOM sample geometry')
    metadata={'source_path':str(path),'source_sha256':digest,'width':w,'height':h,'frames':frames,'frame_index':frame_index or 0,
        'modality':str(getattr(ds,'Modality','')),'photometric_interpretation':photo,'bits_allocated':int(ds.BitsAllocated),
        'pixel_spacing_mm':[float(v) for v in getattr(ds,'PixelSpacing',[])],
        'rescale_slope':slope,'rescale_intercept':intercept,'window_center':center,'window_width':width,
        'transfer_syntax_uid':str(ds.file_meta.TransferSyntaxUID),'coordinate_space':'native_source_pixels'}
    return Image.fromarray(rgb),metadata


# Everything opening a source image for display or geometry can raise: unreadable or truncated files (OSError, incl.
# UnidentifiedImageError), unsupported DICOM (ValueError), malformed headers (SyntaxError), and images above Pillow's
# pixel limit (DecompressionBombError, which is none of those).
IMAGE_OPEN_ERRORS = (OSError, ValueError, SyntaxError, Image.DecompressionBombError)
# The subset that is a property of the bytes, not of the moment: unrecognised or malformed data, unsupported DICOM, too
# many pixels. PermissionError, a vanished file or a dropped share are OSErrors outside it.
UNDECODABLE_IMAGE_ERRORS = (Image.UnidentifiedImageError, ValueError, SyntaxError, Image.DecompressionBombError)


def open_source_image(path):
    return read_dicom(path)[0] if is_dicom(path) else Image.open(path)


def normalized_view(path, owned_root, **options):
    image,metadata=read_dicom(path,**options)
    root=Path(owned_root).resolve()
    if root==Path(path).resolve().parent:raise ValueError('DICOM views must be stored in a project-owned directory')
    if Path(owned_root).is_symlink():raise ValueError('DICOM view directory cannot be a symbolic link')
    root.mkdir(parents=True,exist_ok=True)
    key=hashlib.sha256(json.dumps(metadata,sort_keys=True).encode()).hexdigest()
    target=root/f'{key}.png'
    if target.is_symlink():raise ValueError('DICOM view cannot be a symbolic link')
    if not target.exists():image.save(target,format='PNG')
    receipt={**metadata,'view_id':key,'view_path':str(target),'view_sha256':hashlib.sha256(target.read_bytes()).hexdigest(),
        'view_transform':[[1,0,0],[0,1,0],[0,0,1]]}
    (root/f'{key}.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2),encoding='utf-8')
    return receipt
