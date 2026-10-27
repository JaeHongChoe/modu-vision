"""Display views of immutable whole-flow receipts, without inline pixel blobs.

The authoritative receipt and its digest stay on disk unchanged. This projection
is only for HTTP display: rules, decisions, metrics, truth and validity are not
recomputed. Large values name their position and canonical JSON digest in that
receipt instead of overflowing Chromium's bounded JSON string reader.
"""
from __future__ import annotations

import hashlib
import json


MAX_INLINE_BYTES = 64 * 1024
_DENSE_FIELDS = frozenset({'anomaly_values', 'probability', 'probability_map'})
_PIXEL_FIELDS = frozenset({'image', 'crop_thumbnail', 'mask', 'anomaly_map',
                           'probability_map', 'data'})


def evaluation_response(record):
    referenced = 0
    encoder = json.JSONEncoder(ensure_ascii=False, sort_keys=True, separators=(',', ':'))

    def reference(value, pointer):
        nonlocal referenced
        digest = hashlib.sha256()
        size = 0
        for chunk in encoder.iterencode(value):
            raw = chunk.encode('utf-8')
            size += len(raw)
            digest.update(raw)
        if size <= MAX_INLINE_BYTES:
            return None
        referenced += 1
        return {'kind': 'stored_evaluation_value', 'json_pointer': pointer,
                'json_bytes': size, 'json_sha256': digest.hexdigest()}

    def visit(value, pointer='', field=''):
        if ((field in _PIXEL_FIELDS and isinstance(value, str) and len(value) > MAX_INLINE_BYTES // 4)
                or (field in _DENSE_FIELDS and isinstance(value, list))
                or (isinstance(value, list) and len(value) > 1024
                    and all(isinstance(item, (int, float)) for item in value))):
            replacement = reference(value, pointer)
            if replacement is not None:
                return replacement
        if isinstance(value, dict):
            return {key: visit(child, pointer + '/' + key.replace('~', '~0').replace('/', '~1'), key)
                    for key, child in value.items()}
        if isinstance(value, list):
            return [visit(child, pointer + '/' + str(index)) for index, child in enumerate(value)]
        return value

    view = visit(record)
    if referenced:
        view['response_projection'] = {
            'schema_version': 1, 'kind': 'bounded_evaluation_view',
            'stored_record_sha256': record['record_sha256'], 'referenced_values': referenced,
        }
    return view
