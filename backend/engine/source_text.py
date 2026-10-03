"""Reading label text that users or other tools wrote (LabelMe/COCO JSON, YOLO class lists and labels, data.yaml).

The app writes its own files as UTF-8. Label files arrive from elsewhere: UTF-8 with or without a byte order mark, or
the ANSI code page of the Windows machine that saved them (cp949 on Korean Windows). Before every read named UTF-8,
such a file loaded on that machine through the platform default; reading strictly as UTF-8 would break it there. So a
source text is decoded as UTF-8 (a BOM is dropped), then, only if that fails, as this machine's locale encoding when it
is not UTF-8 (exactly what the platform default did), and otherwise refused with a message that says what to do.
Nothing is guessed: no code page is tried that the platform would not have used.
"""
from __future__ import annotations

import codecs
import locale
from pathlib import Path


class SourceTextError(ValueError):
    """A label file is neither UTF-8 nor this machine's text encoding."""


def decode_source_text(data: bytes, name: str = 'file') -> str:
    if data.startswith(codecs.BOM_UTF8):
        data = data[len(codecs.BOM_UTF8):]
    try:
        return data.decode('utf-8')
    except UnicodeDecodeError as exc:
        fallback = locale.getpreferredencoding(False)
        alternative = ''
        if codecs.lookup(fallback).name != 'utf-8':
            alternative = f' (nor {fallback})'
            try:
                return data.decode(fallback)
            except UnicodeDecodeError:
                pass
        raise SourceTextError(f'{name} is not UTF-8 text{alternative}; save it as UTF-8 and try again') from exc


def read_source_text(path: Path | str) -> str:
    """The text of a user-supplied label file, decoded as described above."""
    path = Path(path)
    return decode_source_text(path.read_bytes(), path.name)
