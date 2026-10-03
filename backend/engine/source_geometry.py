"""Known raster-header failures used only while reading missing source-label geometry.

Keep operating-system and unknown read failures visible. The messages here were
reproduced from Pillow on malformed WebP/BMP/PNG/JPEG bytes; an errno or Windows
error code always takes precedence over a coincidentally matching message.
"""
from __future__ import annotations

import re


def is_corrupt_source_header(error: OSError) -> bool:
    if type(error) is not OSError or error.errno is not None or getattr(error, 'winerror', None) is not None:
        return False
    message = str(error)
    return message in {'Truncated File Read', 'could not create decoder object', 'Unsupported BMP bitfields layout'} or bool(
        re.fullmatch(r'Unsupported BMP (?:header type|pixel depth|compression) \(\d+\)', message)
    )
