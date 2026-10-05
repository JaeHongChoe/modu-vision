"""Bounded previews of bytes matching a recorded run/image identity."""
import base64
import hashlib
import io
import os
from pathlib import Path
from PIL import Image


def verified_preview(image_path: str, source: Path, expected: str):
    try:
        image = Path(image_path).expanduser().resolve()
        if str(image) != image_path or not image.is_relative_to(source.resolve()) or not image.is_file():
            raise ValueError("Recorded image path changed or is unavailable.")
        fd = os.open(image, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0))
        with os.fdopen(fd, "rb") as handle:
            content = handle.read(64 * 1024 * 1024 + 1)
        if len(content) > 64 * 1024 * 1024:
            raise ValueError("Source image exceeds the 64 MiB viewer limit.")
        if hashlib.sha256(content).hexdigest() != expected:
            raise ValueError("Source image changed since this execution; saved prediction remains read-only.")
        with Image.open(io.BytesIO(content)) as decoded:
            size = list(decoded.size)
            if decoded.width * decoded.height > 20_000_000:
                raise ValueError("Source image exceeds the 20 million pixel viewer limit.")
            decoded.thumbnail((1536, 1536))
            output = io.BytesIO()
            decoded.convert("RGB").save(output, format="PNG")
    except (OSError, ValueError, RuntimeError, Image.DecompressionBombError) as exc:
        raise ValueError(str(exc)) from exc
    return {"image_path": image_path, "image_sha256": expected, "original_size": size,
            "read_only": True, "image": "data:image/png;base64," + base64.b64encode(output.getvalue()).decode("ascii")}
