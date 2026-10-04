"""Spatial calibration artifacts (E03): how many millimetres one source pixel covers on one plane, bound to the camera,
its setup and the image size it was measured with.

A calibration is an artifact with an identity: the sha256 of its canonical content (``spatial-cal:sha256:<hex>``), so
a flow, a package or a stored result that names it names exactly these numbers. Two methods are explicit:

- ``known_length_planar``: segments of known length drawn on an image of a planar fixture; X and Y scales (or one scale)
  are fitted by least squares and every segment must come back within the declared tolerance, or nothing is made;
- ``manual_planar_scale``: a scale the user typed (the earlier inline method), kept explicit and unverified (no residual).

A calibration applies only to images of its size, and, when a run knows its camera and setup, only to that camera and
setup: the same image size from another camera or with changed settings never keeps millimetres silently. Lens
distortion and perspective are not modelled (one scale per axis on one plane).

``valid_plane.region`` is a source-pixel rectangle ``[x1, y1, x2, y2]``: raster support is half-open, as in
``image[y1:y2, x1:x2]``. Continuous paths and polygon edges may touch either boundary of those pixel cells; every
vertex/control point and every nonzero mask pixel must remain in the declared plane for a calibrated measurement.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator, Optional

import numpy as np

METHODS = ('manual_planar_scale', 'known_length_planar')
REF_PREFIX = 'spatial-cal:sha256:'
_REF = re.compile(r'^spatial-cal:sha256:[0-9a-f]{64}$')
_HASH = re.compile(r'^sha256:[0-9a-f]{64}$')
MIN_SEGMENT_PX = 10.0


class CalibrationRejected(ValueError):
    """The known lengths do not agree within the declared tolerance; ``errors`` lists each segment's error (mm)."""

    def __init__(self, message: str, errors: list[dict]):
        super().__init__(message)
        self.errors = errors


def is_calibration_ref(value: Any) -> bool:
    return isinstance(value, str) and bool(_REF.match(value))


def acquisition_config_hash(config: dict) -> str:
    """The identity of a camera setup (resolution, lens, focus, working distance, exposure, binning ...): any change of a
    value gives another hash."""
    if not isinstance(config, dict) or not config:
        raise ValueError('the acquisition config must be a non-empty object of camera settings')
    canonical = json.dumps(config, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)
    return 'sha256:' + hashlib.sha256(canonical.encode('utf-8')).hexdigest()


def image_artifact_hash(image: np.ndarray) -> str:
    """The identity of the calibration image's pixels (shape included), whatever file format held them."""
    pixels = np.ascontiguousarray(image)
    digest = hashlib.sha256(json.dumps([list(pixels.shape), str(pixels.dtype)]).encode())
    digest.update(pixels.tobytes())
    return 'sha256:' + digest.hexdigest()


def _positive(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        raise ValueError(f'{name} must be a finite positive number')
    return float(value)


def _size(value) -> tuple[int, int]:
    if not isinstance(value, (list, tuple)) or len(value) != 2 or any(type(v) is not int or v < 1 for v in value):
        raise ValueError('source_size must be the native image [width, height] in pixels')
    return int(value[0]), int(value[1])


def _region(value, size) -> list[int]:
    if value is None:
        return [0, 0, size[0], size[1]]
    if not isinstance(value, (list, tuple)) or len(value) != 4 or any(type(v) is not int for v in value):
        raise ValueError('the valid plane region must be four whole pixel numbers x1, y1, x2, y2')
    x1, y1, x2, y2 = value
    if not (0 <= x1 < x2 <= size[0] and 0 <= y1 < y2 <= size[1]):
        raise ValueError('the valid plane region must lie inside the source image')
    return [x1, y1, x2, y2]


@dataclass(frozen=True)
class SpatialCalibration:
    method: str
    camera_id: str
    acquisition_config_hash: str
    source_size: tuple[int, int]
    scales_or_mapping: dict                   # {'mm_per_pixel_x': float, 'mm_per_pixel_y': float}
    residual: Optional[float]                 # mm RMS over the known lengths; None for a manual scale
    valid_plane: dict                         # {'region': [x1, y1, x2, y2] source px, 'description': str}
    reference_artifact_hash: Optional[str]    # the calibration image's pixels
    approved_at: str
    approved_by: str
    units: str = 'mm'
    evidence: dict = field(default_factory=dict)  # known lengths, their errors and the tolerance

    def __post_init__(self):
        if self.method not in METHODS:
            raise ValueError(f"method must be one of {', '.join(METHODS)}")
        if not isinstance(self.camera_id, str) or not self.camera_id.strip():
            raise ValueError('camera_id is required')
        if not isinstance(self.acquisition_config_hash, str) or not _HASH.match(self.acquisition_config_hash):
            raise ValueError('acquisition_config_hash must be sha256:<hex>')
        size = _size(self.source_size)
        object.__setattr__(self, 'source_size', size)
        if self.units != 'mm':
            raise ValueError('units must be mm')
        scales = self.scales_or_mapping
        if not isinstance(scales, dict) or set(scales) != {'mm_per_pixel_x', 'mm_per_pixel_y'}:
            raise ValueError('scales_or_mapping must hold mm_per_pixel_x and mm_per_pixel_y')
        object.__setattr__(self, 'scales_or_mapping', {key: _positive(scales[key], key) for key in sorted(scales)})
        if self.method == 'manual_planar_scale' and self.residual is not None:
            raise ValueError('a manual scale has no residual')
        if self.method == 'known_length_planar':
            if isinstance(self.residual, bool) or not isinstance(self.residual, (int, float)) or not math.isfinite(self.residual) or self.residual < 0:
                raise ValueError('a known-length calibration needs its residual')
        plane = self.valid_plane if isinstance(self.valid_plane, dict) else {}
        object.__setattr__(self, 'valid_plane', {'region': _region(plane.get('region'), size),
                                                 'description': str(plane.get('description') or '')})
        if self.reference_artifact_hash is not None and not _HASH.match(str(self.reference_artifact_hash)):
            raise ValueError('reference_artifact_hash must be sha256:<hex>')
        if not isinstance(self.approved_by, str) or not self.approved_by.strip():
            raise ValueError('approved_by is required')
        try:
            datetime.fromisoformat(self.approved_at)
        except (TypeError, ValueError) as exc:
            raise ValueError('approved_at must be an ISO time') from exc

    def body(self) -> dict:
        return {'method': self.method, 'camera_id': self.camera_id, 'acquisition_config_hash': self.acquisition_config_hash,
                'source_size': list(self.source_size), 'units': self.units, 'scales_or_mapping': dict(self.scales_or_mapping),
                'residual': self.residual, 'valid_plane': dict(self.valid_plane), 'reference_artifact_hash': self.reference_artifact_hash,
                'approved_at': self.approved_at, 'approved_by': self.approved_by, 'evidence': self.evidence}

    @property
    def ref(self) -> str:
        canonical = json.dumps(self.body(), sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)
        return REF_PREFIX + hashlib.sha256(canonical.encode('utf-8')).hexdigest()

    def to_json(self) -> dict:
        return {'ref': self.ref, **self.body()}

    @classmethod
    def from_json(cls, data: dict) -> 'SpatialCalibration':
        if not isinstance(data, dict):
            raise ValueError('a calibration record must be an object')
        values = {key: data.get(key) for key in ('method', 'camera_id', 'acquisition_config_hash', 'source_size',
                                                 'scales_or_mapping', 'residual', 'valid_plane', 'reference_artifact_hash',
                                                 'approved_at', 'approved_by')}
        calibration = cls(**values, units=data.get('units', 'mm'), evidence=data.get('evidence') or {})
        if data.get('ref') != calibration.ref:
            raise ValueError('the calibration record does not match its identity (changed after it was made)')
        return calibration


def _now(now: Optional[datetime]) -> str:
    return (now or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat(timespec='seconds')


def calibrate_known_lengths(segments: list[dict], *, camera_id: str, acquisition_config: dict, source_size,
                            tolerance_mm: float, approved_by: str, isotropic: bool = False, valid_plane: Optional[dict] = None,
                            reference_image: Optional[np.ndarray] = None, now: Optional[datetime] = None) -> SpatialCalibration:
    """Fit the scale from segments of known length on a planar fixture: ``[{'points': [[x1, y1], [x2, y2]],
    'length_mm': L}]`` in source pixels. Every segment must come back within ``tolerance_mm``; one more segment than
    scales is required, so the fit is checked by at least one length it did not have to match exactly."""
    size = _size(source_size)
    tolerance = _positive(tolerance_mm, 'tolerance_mm')
    if reference_image is not None and tuple(np.asarray(reference_image).shape[:2]) != (size[1], size[0]):
        raise ValueError('the calibration image must have the source size')
    if not isinstance(segments, list):
        raise ValueError('segments must be a list')
    deltas, lengths = [], []
    for index, segment in enumerate(segments):
        points = segment.get('points') if isinstance(segment, dict) else None
        if (not isinstance(points, list) or len(points) != 2
                or any(not isinstance(p, (list, tuple)) or len(p) != 2 or any(isinstance(v, bool) or not isinstance(v, (int, float)) for v in p) for p in points)):
            raise ValueError(f'segment {index + 1} needs two points [x, y]')
        start, end = np.asarray(points, float)
        if not np.isfinite([start, end]).all() or (np.r_[start, end] < 0).any() or max(start[0], end[0]) > size[0] or max(start[1], end[1]) > size[1]:
            raise ValueError(f'segment {index + 1} must lie inside the source image')
        delta = np.abs(end - start)
        if float(np.hypot(*delta)) < MIN_SEGMENT_PX:
            raise ValueError(f'segment {index + 1} is shorter than {MIN_SEGMENT_PX:g} px')
        deltas.append(delta)
        lengths.append(_positive(segment.get('length_mm'), f'segment {index + 1} length_mm'))
    unknowns = 1 if isotropic else 2
    if len(deltas) < unknowns + 1:
        raise ValueError(f'at least {unknowns + 1} known lengths are needed ({unknowns} scale{"s" if unknowns > 1 else ""} and one to check them)')
    d = np.asarray(deltas)
    known = np.asarray(lengths)
    if isotropic:
        pixel = np.hypot(d[:, 0], d[:, 1])
        sx = sy = float(np.dot(known, pixel) / np.dot(pixel, pixel))
    else:
        # L^2 = sx^2 dx^2 + sy^2 dy^2 is linear in sx^2 and sy^2; both directions must be present to separate them.
        design = d ** 2
        normalized = design / np.linalg.norm(design, axis=1, keepdims=True)
        if np.linalg.cond(normalized) > 20:
            raise ValueError('the known lengths need both directions (some mostly horizontal and some mostly vertical) '
                             'to fit separate X and Y scales; add segments or calibrate one scale')
        squares, *_ = np.linalg.lstsq(design, known ** 2, rcond=None)
        if (squares <= 0).any():
            raise ValueError('the known lengths are inconsistent: no positive X and Y scales fit them')
        sx, sy = (float(value) for value in np.sqrt(squares))
    predicted = np.hypot(d[:, 0] * sx, d[:, 1] * sy)
    errors = predicted - known
    rows = [{'segment': index + 1, 'length_mm': float(known[index]), 'measured_mm': round(float(predicted[index]), 6),
             'error_mm': round(float(errors[index]), 6)} for index in range(len(known))]
    worst = float(np.abs(errors).max())
    if worst > tolerance:
        raise CalibrationRejected(f'a known length is off by {worst:.4f} mm, more than the {tolerance:g} mm tolerance', rows)
    return SpatialCalibration(
        method='known_length_planar', camera_id=camera_id, acquisition_config_hash=acquisition_config_hash(acquisition_config),
        source_size=size, scales_or_mapping={'mm_per_pixel_x': sx, 'mm_per_pixel_y': sy},
        residual=round(float(np.sqrt(np.mean(errors ** 2))), 6), valid_plane=valid_plane or {},
        reference_artifact_hash=image_artifact_hash(reference_image) if reference_image is not None else None,
        approved_at=_now(now), approved_by=approved_by,
        evidence={'tolerance_mm': tolerance, 'isotropic': bool(isotropic), 'segments': rows})


def manual_calibration(mm_per_pixel_x: float, mm_per_pixel_y: float, *, camera_id: str, acquisition_config: dict, source_size,
                       approved_by: str, valid_plane: Optional[dict] = None, now: Optional[datetime] = None) -> SpatialCalibration:
    """A typed scale, kept explicit as the manual planar method (not verified against known lengths)."""
    return SpatialCalibration(
        method='manual_planar_scale', camera_id=camera_id, acquisition_config_hash=acquisition_config_hash(acquisition_config),
        source_size=_size(source_size), scales_or_mapping={'mm_per_pixel_x': mm_per_pixel_x, 'mm_per_pixel_y': mm_per_pixel_y},
        residual=None, valid_plane=valid_plane or {}, reference_artifact_hash=None, approved_at=_now(now), approved_by=approved_by,
        evidence={'verified': False})


def refusal(calibration: SpatialCalibration, *, source_size, acquisition: Optional[dict] = None) -> Optional[str]:
    """Why the calibration does not apply to an image of ``source_size`` taken with ``acquisition`` ({'camera_id',
    'acquisition_config_hash'}), or None when it applies. Without an acquisition the camera and setup are not checked
    (the caller records that the unit claim was not verified against them)."""
    width, height = _size(list(source_size))
    if (width, height) != calibration.source_size:
        return f'the calibration was measured on {calibration.source_size[0]}x{calibration.source_size[1]} images, not {width}x{height}'
    if acquisition is not None:
        if acquisition.get('camera_id') != calibration.camera_id:
            return f"the calibration belongs to camera {calibration.camera_id}, not {acquisition.get('camera_id')}"
        if acquisition.get('acquisition_config_hash') != calibration.acquisition_config_hash:
            return "the camera's settings changed since the calibration"
    return None


class CalibrationStore:
    """Calibration artifacts as write-once JSON files named by their identity."""

    def __init__(self, root: Path | str):
        self.root = Path(root)

    def _path(self, ref: str) -> Path:
        if not is_calibration_ref(ref):
            raise ValueError('not a spatial calibration reference')
        return self.root / f'{ref[len(REF_PREFIX):]}.json'

    def save(self, calibration: SpatialCalibration) -> str:
        from backend.engine.runtime_process_control import atomic_private_json
        path = self._path(calibration.ref)
        if path.exists():
            if self.load(calibration.ref) is None:
                raise ValueError(f'{path.name} exists but does not hold this calibration')
            return calibration.ref
        self.root.mkdir(parents=True, exist_ok=True)
        atomic_private_json(path, calibration.to_json())
        return calibration.ref

    def load(self, ref: str) -> Optional[SpatialCalibration]:
        """The calibration, or None when it is missing or does not match its identity."""
        path = self._path(ref)
        try:
            calibration = SpatialCalibration.from_json(json.loads(path.read_text(encoding='utf-8')))
        except (OSError, ValueError, TypeError):
            return None
        return calibration if calibration.ref == ref else None

    def list(self) -> list[dict]:
        rows = []
        for path in sorted(self.root.glob('*.json')) if self.root.is_dir() else []:
            calibration = self.load(REF_PREFIX + path.stem) if re.fullmatch(r'[0-9a-f]{64}', path.stem) else None
            if calibration is not None:
                rows.append(calibration.to_json())
        return sorted(rows, key=lambda row: row['approved_at'], reverse=True)


def calibration_refs(pipeline) -> set[str]:
    """The calibration artifacts a flow's measurement nodes name."""
    return {node.data.params['calibration_ref'] for node in pipeline.nodes
            if node.data.node_type == 'measurement' and isinstance(node.data.params, dict) and node.data.params.get('calibration_ref')}


def package_calibrations(root: Path) -> 'CalibrationStore':
    """A flow package keeps the calibrations its flow names next to its pipeline."""
    return CalibrationStore(Path(root) / 'calibrations')


def project_calibration_store(project: dict) -> CalibrationStore:
    """A project's calibrations live in its folder, so backups, restores and team copies keep them."""
    return CalibrationStore(Path(project['project_dir']) / 'calibrations')


@dataclass(frozen=True)
class CalibrationScope:
    """What one run may use: how to find a calibration by its reference, and the camera and setup of its images when
    the run knows them."""
    resolve: Callable[[str], Optional[SpatialCalibration]]
    acquisition: Optional[dict] = None


_SCOPE: ContextVar[Optional[CalibrationScope]] = ContextVar('spatial_calibration_scope', default=None)


@contextmanager
def calibration_scope(resolve: Callable[[str], Optional[SpatialCalibration]], acquisition: Optional[dict] = None) -> Iterator[None]:
    token = _SCOPE.set(CalibrationScope(resolve, acquisition))
    try:
        yield
    finally:
        _SCOPE.reset(token)


def current_scope() -> Optional[CalibrationScope]:
    return _SCOPE.get()
