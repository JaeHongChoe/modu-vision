"""Deterministic generic procedural defects; masks describe only changed pixels."""
from __future__ import annotations

import cv2
import numpy as np

SYNTHETIC_FAMILIES = ('spot', 'scratch', 'pollution', 'chipping')


def synthesize_defect(rgb: np.ndarray, rng: np.random.Generator,
                      family: str | None = None, placement_mask: np.ndarray | None = None
                      ) -> tuple[np.ndarray, np.ndarray, str]:
    """Return a new RGB image, a 0/255 modification mask and the selected family.

    Placement restricts generation only. It is never used to suppress inference.
    These shapes are procedural training examples, not real defect annotations.
    """
    if not isinstance(rgb, np.ndarray) or rgb.dtype != np.uint8 or rgb.ndim != 3 or rgb.shape[2] != 3:
        raise ValueError('Synthetic input must be a nonempty HxWx3 RGB uint8 image')
    height, width = rgb.shape[:2]
    if not height or not width:
        raise ValueError('Synthetic input must be a nonempty HxWx3 RGB uint8 image')
    allowed = np.ones((height, width), dtype=bool)
    if placement_mask is not None:
        if np.shape(placement_mask) != (height, width):
            raise ValueError('Synthetic placement mask must match the image dimensions')
        allowed = np.asarray(placement_mask, dtype=bool)
    positions = np.flatnonzero(allowed)
    if not len(positions):
        raise ValueError('Synthetic placement mask is empty')
    if family is None:
        family = SYNTHETIC_FAMILIES[int(rng.integers(len(SYNTHETIC_FAMILIES)))]
    if family not in SYNTHETIC_FAMILIES:
        raise ValueError(f'Unsupported synthetic family: {family}')
    center = int(positions[int(rng.integers(len(positions)))])
    cy, cx = divmod(center, width)
    scale = max(1, min(height, width))
    radius = int(rng.integers(1, max(2, scale // 8 + 1)))
    raw_mask = np.zeros((height, width), dtype=np.uint8)
    if family == 'spot':
        cv2.ellipse(raw_mask, (cx, cy), (radius, max(1, radius // 2)),
                    float(rng.uniform(0, 180)), 0, 360, 255, -1)
    elif family == 'scratch':
        angle = float(rng.uniform(0, 2 * np.pi))
        length = int(rng.integers(max(2, scale // 8), max(3, scale // 2)))
        dx, dy = int(np.cos(angle) * length), int(np.sin(angle) * length)
        cv2.line(raw_mask, (cx - dx // 2, cy - dy // 2), (cx + dx // 2, cy + dy // 2),
                 255, max(1, radius // 3))
    elif family == 'pollution':
        for _ in range(int(rng.integers(3, 7))):
            dx, dy = rng.integers(-radius, radius + 1, size=2)
            cv2.circle(raw_mask, (cx + int(dx), cy + int(dy)), max(1, radius), 255, -1)
    else:
        offsets = np.array([[0, 0], [-radius * 2, -radius], [-radius, radius],
                            [radius, radius // 2], [radius * 2, -radius]], dtype=np.int32)
        angle = float(rng.uniform(0, 2 * np.pi))
        rotation = np.array([[np.cos(angle), -np.sin(angle)], [np.sin(angle), np.cos(angle)]])
        polygon = (offsets @ rotation.T + [cx, cy]).astype(np.int32)
        cv2.fillPoly(raw_mask, [polygon], 255)
    region = (raw_mask > 0) & allowed
    region[cy, cx] = True
    result = rgb.copy()
    pixels = rgb[region].astype(np.float32)
    sign = -1 if float(pixels.mean()) > 127 else 1
    if family == 'pollution':
        color = rng.uniform(0, 90, size=3) if sign < 0 else rng.uniform(165, 255, size=3)
        changed = pixels * 0.35 + color * 0.65 + rng.normal(0, 8, pixels.shape)
    elif family == 'chipping':
        color = rng.uniform(0, 55, size=3) if sign < 0 else rng.uniform(200, 255, size=3)
        changed = pixels * 0.15 + color * 0.85
    else:
        changed = pixels + sign * float(rng.uniform(55, 130))
    result[region] = np.clip(changed, 0, 255).astype(np.uint8)
    if np.array_equal(result[cy, cx], rgb[cy, cx]):
        result[cy, cx, 0] = np.uint8(int(rgb[cy, cx, 0]) ^ 127)
    mask = np.any(result != rgb, axis=2).astype(np.uint8) * 255
    return result, mask, family
