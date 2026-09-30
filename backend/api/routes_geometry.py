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


@router.post('/measure')
def measure(req: GeometryMeasureRequest, request: Request):
    try:
        path = _native_source_path(req.image_path, request)
        pixels = read_image_safely_rgb(path, max_dim=None)
        rows = measure_geometry(req.params, source_size=[pixels.shape[1], pixels.shape[0]], polygons=req.polygons)
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
