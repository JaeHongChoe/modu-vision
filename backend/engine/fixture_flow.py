"""Immutable project fixture references and opt-in fixed-ROI flow integration (E02).

Pose estimation remains fixture_pose's reviewed ORB provider. A flow names one
exact copied reference; current template revisions and file hashes fail closed.
"""
from __future__ import annotations
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import re
import uuid

import cv2
import numpy as np
from backend.engine.fixture_pose import FixtureReference, FixtureLimits, locate_fixture, roi_in_observed
from backend.engine.flow_workspace import atomic_json
from backend.engine.dataset_metadata import _file_lock

_REF = re.compile(r'^fixture-ref:[0-9a-f]{64}$')
_ID = re.compile(r'^[0-9a-f]{32}$')


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode()).hexdigest()


@dataclass
class FixtureArtifact:
    reference: FixtureReference
    body: dict
    png: bytes

    @property
    def ref(self):
        return 'fixture-ref:' + _digest(self.body)

    def to_json(self):
        return {**self.body, 'ref': self.ref}


class FixtureReferenceStore:
    def __init__(self, root):
        self.root = Path(root)

    def _safe(self):
        if any(path.is_symlink() for path in (self.root, *self.root.parents)):
            raise ValueError('Fixture reference storage cannot contain symbolic links')

    def _registry(self):
        self._safe()
        path = self.root / 'active.json'
        if path.is_symlink():
            raise ValueError('Fixture registry cannot be a symbolic link')
        active = json.loads(path.read_text(encoding='utf-8')) if path.is_file() else {}
        if not isinstance(active, dict) or any(not isinstance(key, str) or not _ID.fullmatch(key) or not isinstance(ref, str) or not _REF.fullmatch(ref) for key, ref in active.items()):
            raise ValueError('Invalid fixture revision registry')
        return active

    def save(self, reference: FixtureReference, *, source_sha256: str, name: str, fixture_id=None, expected_ref=None):
        self._safe()
        if not isinstance(source_sha256, str) or not re.fullmatch('[0-9a-f]{64}', source_sha256):
            raise ValueError('Fixture source SHA-256 is required')
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 200:
            raise ValueError('Fixture reference name is required (1–200 characters)')
        self.root.mkdir(parents=True, exist_ok=True)
        with _file_lock(self.root / 'reference.lock'):
            active = self._registry()
            if fixture_id is None:
                if reference.revision != 1 or expected_ref is not None:
                    raise ValueError('A new fixture reference starts at revision 1')
                fixture_id = uuid.uuid4().hex
            elif not _ID.fullmatch(fixture_id) or active.get(fixture_id) != expected_ref:
                raise ValueError('Fixture reference revision changed; reopen before saving')
            else:
                old = self.load(expected_ref)
                if old is None or reference.revision != old.reference.revision + 1:
                    raise ValueError('Fixture revision must increase by exactly one')
            ok, encoded = cv2.imencode('.png', reference.grey)
            if not ok:
                raise ValueError('Fixture reference image could not be encoded')
            png = encoded.tobytes()
            body = {'schema_version': 1, 'fixture_id': fixture_id, 'name': name.strip(), 'revision': reference.revision,
                    'valid_region': list(reference.valid_region), 'source_sha256': source_sha256,
                    'image_sha256': hashlib.sha256(png).hexdigest(), 'provider_reference_ref': reference.artifact_ref,
                    'image_size': [reference.grey.shape[1], reference.grey.shape[0]]}
            artifact = FixtureArtifact(reference, body, png)
            self._write(artifact)
            active[fixture_id] = artifact.ref
            atomic_json(self.root / 'active.json', active)
            return artifact

    def _write(self, artifact):
        stem = artifact.ref.split(':')[-1]
        image = self.root / f'{stem}.png'
        metadata = self.root / f'{stem}.json'
        if image.is_symlink() or metadata.is_symlink():
            raise ValueError('Fixture artifacts cannot be symbolic links')
        if image.exists() and image.read_bytes() != artifact.png:
            raise ValueError('Immutable fixture reference image changed')
        if not image.exists():
            temporary = self.root / f'.{stem}.{uuid.uuid4().hex}.partial'
            try:
                temporary.write_bytes(artifact.png)
                os.replace(temporary, image)
            finally:
                temporary.unlink(missing_ok=True)
        metadata = self.root / f'{stem}.json'
        if metadata.exists() and json.loads(metadata.read_text(encoding='utf-8')) != artifact.to_json():
            raise ValueError('Immutable fixture reference metadata changed')
        if not metadata.exists():
            atomic_json(metadata, artifact.to_json())

    def load(self, ref):
        self._safe()
        if not isinstance(ref, str) or not _REF.fullmatch(ref):
            raise ValueError('Fixture reference must be fixture-ref:<sha256>')
        stem = ref.split(':')[-1]
        try:
            if any((self.root / f'{stem}.{extension}').is_symlink() for extension in ('json', 'png')):
                return None
            metadata = json.loads((self.root / f'{stem}.json').read_text(encoding='utf-8'))
            body = {key: value for key, value in metadata.items() if key != 'ref'}
            png = (self.root / f'{stem}.png').read_bytes()
            if metadata.get('ref') != ref or 'fixture-ref:' + _digest(body) != ref or hashlib.sha256(png).hexdigest() != body['image_sha256']:
                return None
            if self._registry().get(body['fixture_id']) != ref:
                return None  # changed active template revision invalidates dependent graph/evidence
            grey = cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_GRAYSCALE)
            reference = FixtureReference(grey, body['valid_region'], body['revision'])
            if reference.artifact_ref != body['provider_reference_ref']:
                return None
            return FixtureArtifact(reference, body, png)
        except (OSError, ValueError, TypeError, KeyError):
            return None

    def list(self):
        return [artifact.to_json() for ref in self._registry().values() if (artifact := self.load(ref)) is not None]

    def copy_artifact(self, artifact):
        self._safe()
        self.root.mkdir(parents=True, exist_ok=True)
        self._write(artifact)
        active = self._registry(); active[artifact.body['fixture_id']] = artifact.ref
        atomic_json(self.root / 'active.json', active)


def project_fixtures(project):
    return FixtureReferenceStore(Path(project['project_dir']) / 'fixture_references')


def package_fixtures(root):
    return FixtureReferenceStore(Path(root) / 'fixture_references')


_SCOPE = ContextVar('fixture_reference_scope', default=None)


@contextmanager
def fixture_scope(resolve):
    token = _SCOPE.set(resolve)
    try:
        yield
    finally:
        _SCOPE.reset(token)


def fixture_refs(pipeline):
    return {node.data.params['fixture']['reference_ref'] for node in pipeline.nodes
            if node.data.node_type == 'fixed_roi' and node.data.params.get('fixture') is not None}


def validate_fixture_params(params):
    if not isinstance(params, dict) or set(params) - {'reference_ref', 'reference_revision', 'scope', 'limits', 'min_support', 'search_region'}:
        raise ValueError('Unsupported fixture parameters')
    if not isinstance(params.get('reference_ref'), str) or not _REF.fullmatch(params['reference_ref']):
        raise ValueError('Fixture needs its exact immutable reference_ref')
    if type(params.get('reference_revision')) is not int or params['reference_revision'] < 1:
        raise ValueError('Fixture reference_revision must be a positive integer')
    if params.get('scope') not in ('rigid', 'similarity'):
        raise ValueError('Fixture scope must be rigid or similarity')
    support = params.get('min_support', .8)
    if isinstance(support, bool) or not isinstance(support, (float, int)) or not math.isfinite(support) or not 0 <= support <= 1:
        raise ValueError('Fixture min_support must be from 0 to 1')
    region = params.get('search_region')
    if region is not None and (not isinstance(region, (list, tuple)) or len(region) != 4 or any(type(value) is not int for value in region) or min(region[:2]) < 0 or region[2] - region[0] < 32 or region[3] - region[1] < 32):
        raise ValueError('Fixture search_region requires a nonnegative integer rectangle of at least 32x32 pixels')
    try:
        FixtureLimits(**params.get('limits', {}))
    except TypeError as exc:
        raise ValueError('Unsupported fixture quality limits') from exc


class FixtureReview(ValueError):
    def __init__(self, reason, pose):
        super().__init__(reason)
        self.pose = pose


def anchored_roi(image_rgb, roi_bbox, params, node_id, label):
    validate_fixture_params(params)
    resolve = _SCOPE.get()
    try:
        artifact = resolve(params['reference_ref']) if resolve else None
    except (ValueError, OSError):
        artifact = None
    if artifact is None or artifact.reference.revision != params['reference_revision']:
        reason = 'Fixture reference is missing, stale, mutated or outside this project'
        raise FixtureReview(reason, {'status': 'review', 'reason': reason, 'reference_artifact_ref': params['reference_ref']})
    try:
        pose = locate_fixture(artifact.reference, cv2.cvtColor(image_rgb, cv2.COLOR_RGB2BGR), scope=params['scope'],
                              limits=FixtureLimits(**params.get('limits', {})), search_region=params.get('search_region'))
    except ValueError as exc:
        reason = f'Fixture pose cannot be evaluated on this observation: {exc}'
        raise FixtureReview(reason, {'status': 'review', 'reason': reason, 'reference_artifact_ref': artifact.ref,
                                    'reference_revision': artifact.reference.revision}) from exc
    evidence = {**pose.to_json(), 'reference_artifact_ref': artifact.ref,
                'provider_reference_ref': pose.reference_artifact_ref, 'reference_revision': artifact.reference.revision}
    if pose.status != 'located':
        raise FixtureReview(pose.reason, evidence)
    try:
        placed = roi_in_observed(pose, roi_bbox, min_support=params.get('min_support', .8))
        if not placed['inside_frame']:
            raise ValueError('Fixture ROI is not fully inside the observed source frame')
    except ValueError as exc:
        evidence.update(status='review', reason=str(exc))
        raise FixtureReview(str(exc), evidence) from exc
    x1, y1, x2, y2 = roi_bbox
    transform = np.vstack([pose.reference_to_observed_transform, [0, 0, 1]]) @ np.array([[1, 0, x1], [0, 1, y1], [0, 0, 1]])
    width, height = x2-x1, y2-y1
    if width*height > 100_000_000:
        raise FixtureReview('Fixture ROI exceeds native image work budget', evidence)
    pixels = cv2.warpPerspective(image_rgb, np.linalg.inv(transform), (width, height), flags=cv2.INTER_LINEAR)
    from backend.engine.flow_operators import source_bbox
    bbox = source_bbox(transform, width, height)
    bbox = [max(0,bbox[0]), max(0,bbox[1]), min(image_rgb.shape[1],bbox[2]), min(image_rgb.shape[0],bbox[3])]
    evidence['roi'] = placed
    return {'id': f'fixed_roi:{node_id}', 'label': label, 'bbox': bbox, 'polygon': placed['polygon'],
            'image': pixels, 'source_transform': transform.tolist(), 'crop_padding': 0, 'fixture_pose': evidence}, evidence


def current_fixture_resolver():
    return _SCOPE.get()
