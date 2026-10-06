"""Read-only DICOM decoding and reproducible project-owned display images."""
from __future__ import annotations
import hashlib
import io
import json
import math
import os
import stat
import tempfile
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
    # The validated dataset hierarchy can contain a linked source image.
    # Retain that visible path for subsequent namespace checks; its resolved
    # target never grants direct access outside the imported hierarchy.
    path=Path(os.path.abspath(Path(path).expanduser()))
    source_bytes=path.read_bytes();digest=hashlib.sha256(source_bytes).hexdigest()
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
    windowing='color' if channels==3 else 'minmax' if width is None or center is None else 'dicom_window'
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
        'rescale_slope':slope,'rescale_intercept':intercept,'window_center':center,'window_width':width,'windowing_mode':windowing,
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


def _unlinked_view(path):
    if any(p.is_symlink() for p in (path,*path.parents)):
        raise ValueError('DICOM view storage cannot follow a symbolic link')


def _cached_view_bytes(path,limit):
    _unlinked_view(path)
    with os.fdopen(os.open(path,os.O_RDONLY|getattr(os,'O_NOFOLLOW',0)),'rb') as handle:
        before=os.fstat(handle.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_size>limit:
            raise ValueError('DICOM cached view integrity differs')
        value=handle.read(limit+1)
        _unlinked_view(path);after=os.fstat(handle.fileno());current=path.stat()
        identity=lambda s:(s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns)
        if len(value)>limit or len(value)!=before.st_size or identity(before)!=identity(after) or identity(before)!=identity(current):
            raise ValueError('DICOM cached view integrity changed while reading')
        return value


def _encode_display(path,root,options):
    image,metadata=read_dicom(path,**options)
    # Complete encoding before publication: a failed encoder cannot leave a
    # partially written final PNG that a subsequent request treats as valid.
    encoded=io.BytesIO()
    try:image.save(encoded,format='PNG')
    finally:image.close()
    expected=encoded.getvalue();digest=hashlib.sha256(expected).hexdigest()
    key=hashlib.sha256(json.dumps(metadata,sort_keys=True).encode()).hexdigest()
    target=root/f'{key}.png'
    receipt={**metadata,'view_id':key,'view_path':str(target),'view_sha256':digest,
        'view_transform':[[1,0,0],[0,1,0],[0,0,1]]}
    return expected,receipt


def read_cached_view(view_id,owned_root,*,resolve_source):
    """Revalidate source/display identity before returning fixed response bytes.

    The API supplies the project namespace resolver; cache metadata never grants
    permission to open an arbitrary source path. This read performs no repair.
    """
    root=Path(owned_root).expanduser().absolute();_unlinked_view(root)
    record=root/f'{view_id}.json';target=root/f'{view_id}.png'
    try:
        saved=json.loads(_cached_view_bytes(record,65536))
        path=resolve_source(saved['source_path'])
        options={k:saved[k] for k in ('window_center','window_width','frame_index')}
        if saved['windowing_mode']=='minmax':options.update(window_center=None,window_width=None)
        expected,receipt=_encode_display(path,root,options)
        if receipt!=saved:raise ValueError('DICOM cached receipt integrity differs from current source display')
        actual=_cached_view_bytes(target,len(expected))
        if actual!=expected:raise ValueError('DICOM cached PNG integrity differs from current source display')
        return actual
    except (KeyError,TypeError,UnicodeError) as exc:raise ValueError('DICOM cached receipt integrity is invalid') from exc


def normalized_view(path, owned_root, **options):
    from backend.engine.runtime_process_control import atomic_private_json,runtime_state_lock
    from backend.remote.file_replace import replace_file
    root=Path(owned_root).expanduser().absolute();_unlinked_view(root)
    if root==Path(path).resolve().parent:raise ValueError('DICOM views must be stored in a project-owned directory')
    expected,receipt=_encode_display(path,root,options)
    root.mkdir(parents=True,exist_ok=True);_unlinked_view(root)
    key=receipt['view_id'];target=root/f'{key}.png';record=root/f'{key}.json'
    with runtime_state_lock(root):
        _unlinked_view(target);_unlinked_view(record)
        if target.exists() and _cached_view_bytes(target,len(expected))!=expected:
            raise ValueError('DICOM cached PNG integrity differs from reproducible source display')
        if record.exists():
            try:prior=json.loads(_cached_view_bytes(record,65536))
            except (ValueError,UnicodeError) as exc:raise ValueError('DICOM cached receipt integrity differs') from exc
            if prior!=receipt or not target.exists():raise ValueError('DICOM cached receipt integrity differs from source display')
        if not target.exists():
            fd,name=tempfile.mkstemp(prefix='.'+key+'-',suffix='.tmp',dir=root)
            temporary=Path(name)
            try:
                with os.fdopen(fd,'wb') as handle:handle.write(expected);handle.flush();os.fsync(handle.fileno())
                _unlinked_view(target);replace_file(temporary,target)
            finally:temporary.unlink(missing_ok=True)
        # An interrupted receipt publication may reuse only an exact, complete
        # PNG. A corrupted PNG or rebound receipt is retained and refused.
        if not record.exists():atomic_private_json(record,receipt)
        if os.name!='nt':
            fd=os.open(root,os.O_RDONLY)
            try:os.fsync(fd)
            finally:os.close(fd)
    return receipt
