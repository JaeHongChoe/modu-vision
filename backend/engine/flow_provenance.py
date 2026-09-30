"""Authoritative graph identity shared by saved flow metadata and new runs."""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any, Dict

from backend.engine.flowchart_engine import FlowchartPipeline


def _normalized_graph_numbers(value: Any) -> Any:
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("Inspection graph numbers must be finite.")
        return int(value) if value.is_integer() else value
    if isinstance(value, dict):
        return {key: _normalized_graph_numbers(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_normalized_graph_numbers(item) for item in value]
    return value


def canonical_pipeline_json(graph: FlowchartPipeline | Dict[str, Any]) -> str:
    """Validate defaults and numbers for a new graph identity.

    Existing inspection JSON and raw hashes remain unchanged. The server
    publishes this identity so browser number formatting cannot change it.
    """
    pipeline = FlowchartPipeline.model_validate(graph)
    return json.dumps(
        _normalized_graph_numbers(pipeline.model_dump()),
        ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    )


def pipeline_sha256(graph: FlowchartPipeline | Dict[str, Any]) -> str:
    return hashlib.sha256(canonical_pipeline_json(graph).encode("utf-8")).hexdigest()
