"""Bounded read-only display context; this never qualifies or ranks a parent."""
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import stat

_LIMIT = 256 * 1024
_METRICS = ('val_loss', 'val_accuracy', 'accuracy', 'angular_mae_deg', 'val_cer', 'cer', 'map50', 'mAP', 'psnr')


def _read(path: Path):
    fd = None
    try:
        if path.is_symlink():
            return {}
        fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return {}
        with os.fdopen(fd, 'rb') as stream:
            fd = None
            raw = stream.read(_LIMIT + 1)
        if len(raw) > _LIMIT:
            return {}
        value = json.loads(raw.decode('utf-8'))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError, UnicodeError, RecursionError):
        return {}
    finally:
        if fd is not None:
            os.close(fd)


def _date(value):
    try:
        if type(value) in (int, float) and math.isfinite(value) and value > 0:
            result = datetime.fromtimestamp(value, timezone.utc)
        elif isinstance(value, str) and len(value) <= 80:
            result = datetime.fromisoformat(value.replace('Z', '+00:00'))
            if result.tzinfo is None:
                return None
        else:
            return None
        return result.astimezone(timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')
    except (ValueError, OSError, OverflowError):
        return None


def parent_summary(directory):
    directory = Path(directory)
    metadata = _read(directory / 'model_meta.json')
    receipt = _read(directory / 'job_receipt.json')
    recorded = receipt.get('metrics')
    recorded = recorded if isinstance(recorded, dict) else {}
    metrics = {}
    def record(name, value):
        try:
            if type(value) in (int, float) and math.isfinite(value):
                metrics[name] = value
        except OverflowError:
            pass
    # Checkpoint-selected values and last-epoch observations are separate keys.
    for name in ('best_metric', 'last_generator_loss', 'last_discriminator_loss'):
        record(name, metadata.get(name))
    validation = metadata.get('validation')
    validation = validation if isinstance(validation, dict) else {}
    record('saved_val_loss', metadata.get('best_validation_loss', validation.get('loss')))
    for name in ('angular_mae_deg', 'mean_oriented_iou', 'mean_angle_error_deg',
                 'mean_direction_error_deg', 'mAP_50', 'mAP_50_95', 'psnr', 'output_mse'):
        record('saved_' + name, validation.get(name))
    for name in _METRICS:
        record(name, recorded.get(name))
    return {'model_recorded_at': _date(metadata.get('created_at')),
            'completed_at': _date(receipt.get('completed_at')),
            'training_metrics': metrics}
