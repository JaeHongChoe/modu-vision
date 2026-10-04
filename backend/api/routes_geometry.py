"""Native-image calibrated curve and polygon measurements."""
from hashlib import sha256
from pathlib import Path
from typing import Any
import base64
import io

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from backend.engine.geometry_measurement import measure_geometry
from backend.engine.industrial_adapters import read_image_safely_rgb
from backend.engine.annotation_storage import request_shared_scope
from PIL import Image

router = APIRouter(prefix='/api/geometry', tags=['geometry'])


class GeometryMeasureRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    image_path: str = Field(min_length=1)
    params: dict[str, Any] = Field(default_factory=dict)
    polygons: list[dict[str, Any]] = Field(default_factory=list, max_length=64)


def _calibration_store(request: Request):
    from backend.api.routes_project import get_current_project
    from backend.engine.spatial_calibration import project_calibration_store
    return project_calibration_store(get_current_project(request))


@router.post('/measure')
def measure(req: GeometryMeasureRequest, request: Request):
    from backend.engine.spatial_calibration import CalibrationScope
    try:
        path = _native_source_path(req.image_path, request)
        pixels = read_image_safely_rgb(path, max_dim=None)
        scope = CalibrationScope(_calibration_store(request).load) if req.params.get('calibration_ref') else None
        rows = measure_geometry(req.params, source_size=[pixels.shape[1], pixels.shape[0]], polygons=req.polygons, scope=scope)
        return {'measurements': rows, 'source_size': [pixels.shape[1], pixels.shape[0]],
                'source_image_path': str(path.resolve()), 'source_sha256': sha256(path.read_bytes()).hexdigest()}
    except (ValueError, OSError, KeyError, TypeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _native_source_path(image_path, request):
    path = Path(image_path).expanduser()
    if path.is_symlink() or not path.is_file(): raise ValueError('Measurement source must be an existing native image')
    path = path.resolve()
    if request_shared_scope():
        from backend.api.routes_project import get_current_project
        source = get_current_project(request).get('source_dataset_dir')
        if not source or not path.is_relative_to(Path(source).resolve()): raise ValueError('Measurement source escaped active project source')
    return path


@router.get('/source-preview')
def source_preview(image_path: str, request: Request):
    try:
        path = _native_source_path(image_path, request)
        pixels = read_image_safely_rgb(path, max_dim=768)
        with Image.open(path) as source: size = list(source.size)
        output = io.BytesIO(); Image.fromarray(pixels).save(output, format='PNG')
        return {'source_size': size, 'source_sha256': sha256(path.read_bytes()).hexdigest(),
            'preview_data_url': 'data:image/png;base64,'+base64.b64encode(output.getvalue()).decode('ascii')}
    except (ValueError, OSError) as exc:
        raise HTTPException(422, str(exc)) from exc


class KnownLengthCalibrationRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    camera_id: str = Field(min_length=1, max_length=200)
    acquisition_config: dict[str, Any]
    source_size: list[int] = Field(min_length=2, max_length=2)
    segments: list[dict[str, Any]] = Field(min_length=2, max_length=200)
    tolerance_mm: float = Field(gt=0)
    isotropic: bool = False
    valid_plane: dict[str, Any] | None = None
    reference_image_path: str | None = None


class ManualCalibrationRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    camera_id: str = Field(min_length=1, max_length=200)
    acquisition_config: dict[str, Any]
    source_size: list[int] = Field(min_length=2, max_length=2)
    mm_per_pixel_x: float = Field(gt=0)
    mm_per_pixel_y: float = Field(gt=0)
    valid_plane: dict[str, Any] | None = None


def _actor(request: Request) -> str:
    account = getattr(request.state, 'account_user', None)
    return str(account.get('username') or account.get('id')) if account else 'this computer'


@router.get('/calibrations')
def list_calibrations(request: Request):
    """The project's spatial calibrations (E03), newest first."""
    return {'calibrations': _calibration_store(request).list()}


@router.get('/calibrations/{ref}')
def get_calibration(ref: str, request: Request):
    try:
        calibration = _calibration_store(request).load(ref)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    if calibration is None:
        raise HTTPException(404, 'No such calibration in this project, or it changed after it was made.')
    return calibration.to_json()


@router.post('/calibrations/known-lengths')
def create_known_length_calibration(req: KnownLengthCalibrationRequest, request: Request):
    """A calibration fitted from known lengths on a planar fixture; refused (422, with each segment's error) when a
    length does not come back within the tolerance."""
    from backend.engine.spatial_calibration import CalibrationRejected, calibrate_known_lengths
    try:
        reference = None
        if req.reference_image_path:
            reference = read_image_safely_rgb(_native_source_path(req.reference_image_path, request), max_dim=None)
        calibration = calibrate_known_lengths(
            req.segments, camera_id=req.camera_id, acquisition_config=req.acquisition_config, source_size=req.source_size,
            tolerance_mm=req.tolerance_mm, approved_by=_actor(request), isotropic=req.isotropic, valid_plane=req.valid_plane,
            reference_image=reference)
    except CalibrationRejected as exc:
        raise HTTPException(422, {'message': str(exc), 'segments': exc.errors}) from exc
    except (ValueError, OSError) as exc:
        raise HTTPException(422, str(exc)) from exc
    _calibration_store(request).save(calibration)
    return calibration.to_json()


@router.post('/calibrations/manual')
def create_manual_calibration(req: ManualCalibrationRequest, request: Request):
    """A typed scale kept as an explicit, unverified manual planar calibration."""
    from backend.engine.spatial_calibration import manual_calibration
    try:
        calibration = manual_calibration(req.mm_per_pixel_x, req.mm_per_pixel_y, camera_id=req.camera_id,
                                         acquisition_config=req.acquisition_config, source_size=req.source_size,
                                         approved_by=_actor(request), valid_plane=req.valid_plane)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    _calibration_store(request).save(calibration)
    return calibration.to_json()
