"""Bounded RGB decoding for one-off inference; never writes dataset media."""
import io
import numpy as np
from PIL import Image, UnidentifiedImageError


def decode_prediction_image(payload: bytes, *, max_bytes=32 * 1024 * 1024, max_pixels=25_000_000):
    if not payload or len(payload) > max_bytes: raise ValueError('Prediction image exceeds the upload size limit')
    try:
        with Image.open(io.BytesIO(payload)) as image:
            if image.width * image.height > max_pixels or image.width < 1 or image.height < 1:
                raise ValueError('Prediction image exceeds the pixel limit')
            return np.asarray(image.convert('RGB')).copy()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise ValueError('Prediction upload is not a supported raster image') from exc
