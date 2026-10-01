"""Score units and model-bound calibration used by saved graphs and releases."""
from __future__ import annotations

import hashlib
import json
import math
from functools import lru_cache
from pathlib import Path


def validate_score_spec(value, *, threshold=None):
    if not isinstance(value, dict):
        raise ValueError('Score specification is required')
    domain, unit = value.get('domain'), value.get('unit')
    if domain not in ('distance', 'probability') or unit not in (
            'mahalanobis_distance', 'euclidean_distance', 'probability'):
        raise ValueError('Unsupported score domain or unit')
    if (domain == 'probability') != (unit == 'probability'):
        raise ValueError('Score domain and unit differ')
    if value.get('direction') != 'higher_is_defect':
        raise ValueError('Unsupported score direction')
    if not isinstance(value.get('calibration_id'), str) or not value['calibration_id'].strip():
        raise ValueError('Score calibration identity is required')
    selected = value.get('threshold')
    if isinstance(selected, bool) or not isinstance(selected, (float, int)) or not math.isfinite(selected):
        raise ValueError('Score threshold must be finite')
    if selected < 0 or (domain == 'probability' and selected > 1):
        raise ValueError('Score threshold is outside its domain')
    if threshold is not None and selected != threshold:
        raise ValueError('Node threshold differs from score specification threshold')
    return {key: value[key] for key in ('domain', 'unit', 'direction', 'calibration_id', 'threshold')}


def compatible_scores(first, second):
    return all(first[key] == second[key] for key in ('domain', 'unit', 'direction', 'calibration_id'))


def calibrated_score_spec(state, unit):
    """Hash fitted tensors once at fit/load, independently of path or device."""
    import torch
    digest = hashlib.sha256()
    def feed(value):
        if isinstance(value, torch.Tensor):
            tensor = value.detach().cpu().contiguous()
            digest.update(str((str(tensor.dtype), tuple(tensor.shape))).encode())
            digest.update(tensor.reshape(-1).view(torch.uint8).numpy().tobytes())
        elif isinstance(value, dict):
            digest.update(b'{')
            for key in sorted(value):
                if key == 'score_spec': continue
                digest.update(json.dumps(key).encode()); feed(value[key]); digest.update(b',')
            digest.update(b'}')
        elif isinstance(value, (list, tuple)):
            digest.update(b'[')
            for item in value: feed(item); digest.update(b',')
            digest.update(b']')
        else:
            digest.update(json.dumps(value, sort_keys=True, allow_nan=False).encode())
    digest.update(unit.encode()); feed(state)
    return validate_score_spec({'domain': 'probability' if unit == 'probability' else 'distance',
        'unit': unit, 'direction': 'higher_is_defect',
        'calibration_id': 'sha256:' + digest.hexdigest(), 'threshold': state['threshold']})


def restore_score_spec(state, unit):
    actual = calibrated_score_spec(state, unit)
    if state.get('score_spec') is not None and validate_score_spec(state['score_spec']) != actual:
        raise ValueError('Saved score calibration differs from fitted model state')
    return actual


def resolve_model_score(model, node_spec, threshold):
    """Legacy distance nodes inherit the detector's saved calibrated threshold."""
    from backend.engine.anomaly.padim import PaDiMDetector
    from backend.engine.anomaly.patchcore import PatchCoreDetector
    unit = ('mahalanobis_distance' if isinstance(model, PaDiMDetector) else
            'euclidean_distance' if isinstance(model, PatchCoreDetector) else 'probability')
    actual = getattr(model, 'score_spec', None)
    if actual is None:
        # Fitted distance detectors populate this at load/fit. Probability
        # anomaly heads use the same deterministic state binding lazily once.
        state = model.state_dict()
        state = {**state, 'threshold': float(model.threshold)}
        actual = calibrated_score_spec(state, unit)
        model.score_spec = actual
    actual = validate_score_spec(actual)
    if node_spec is None:
        if actual['domain'] == 'distance':
            return actual, 'saved_model_calibration_legacy_flow'
        return validate_score_spec({**actual, 'threshold': threshold}), 'legacy_probability_threshold'
    selected = validate_score_spec(node_spec, threshold=threshold)
    if not compatible_scores(actual, selected):
        raise ValueError('Flow score domain, unit or calibration differs from selected model')
    return selected, 'explicit_flow_score_spec'


def state_score_spec(state):
    """Bind the exact state loaded for prediction, independent of file caches."""
    if 'cov_inv' in state and 'mean' in state: unit = 'mahalanobis_distance'
    elif 'coreset' in state: unit = 'euclidean_distance'
    else: unit = 'probability'
    return restore_score_spec(state, unit)


@lru_cache(maxsize=128)
def _checkpoint_score_spec(path, size, modified, changed, inode):
    import torch
    payload = torch.load(path, map_location='cpu', weights_only=True)
    return state_score_spec(payload.get('model_state_dict', payload))


def checkpoint_score_spec(path):
    path = Path(path)
    stat = path.stat()
    return dict(_checkpoint_score_spec(str(path), stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns, stat.st_ino))


def resolve_inference_score(path, task, threshold=None, score_spec=None):
    """Resolve API thresholds without treating raw distances as probabilities."""
    if task not in ('anomaly', 'anomaly_detection'):
        value = .5 if threshold is None else threshold
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 1:
            raise ValueError('Probability threshold must be finite and between 0 and 1')
        if score_spec is not None:
            validate_score_spec(score_spec, threshold=value)
            if score_spec['domain'] != 'probability':
                raise ValueError('Score domain differs from probability model')
        return value, None
    actual = checkpoint_score_spec(path)
    if threshold is None:
        threshold = actual['threshold']
    if score_spec is None:
        if actual['domain'] == 'distance' and threshold != actual['threshold']:
            raise ValueError('Distance threshold override requires the model score calibration binding')
        selected = validate_score_spec({**actual, 'threshold': threshold})
    else:
        selected = validate_score_spec(score_spec, threshold=threshold)
        if not compatible_scores(actual, selected):
            raise ValueError('Score domain, unit or calibration differs from selected model')
    return threshold, selected


def validate_score_rule(specs, decision_spec, threshold):
    if decision_spec is None:
        if any(spec is not None for spec in specs):
            raise ValueError('Global score rule requires an explicit compatible score calibration')
        if threshold is None or not math.isfinite(threshold) or not 0 <= threshold <= 1:
            raise ValueError('Decision probability score threshold must be between 0 and 1')
        return
    selected = validate_score_spec(decision_spec, threshold=threshold)
    for spec in specs:
        if spec is None or not compatible_scores(validate_score_spec(spec), selected):
            raise ValueError('Global score rule mixes incompatible score units or calibrations')
