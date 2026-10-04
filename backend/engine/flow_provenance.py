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


# What a flow decides, apart from how it is drawn (E04): node positions, display names, the node type used for drawing,
# edge ids and edge labels are layout. Everything else (node kinds, models, thresholds, parameters, rules, connections
# and their branches or conditions, execution limits) is an inspection rule.
_LAYOUT_NODE_DATA = ('label',)


def semantic_projection(graph: FlowchartPipeline | Dict[str, Any]) -> Dict[str, Any]:
    pipeline = FlowchartPipeline.model_validate(graph)
    data = _normalized_graph_numbers(pipeline.model_dump())
    nodes = {node['id']: {key: value for key, value in node['data'].items() if key not in _LAYOUT_NODE_DATA}
             for node in data['nodes']}
    edges = sorted(({key: edge.get(key) for key in ('source', 'target', 'isBranch', 'predicate', 'payload_type')}
                    for edge in data['edges']), key=lambda edge: json.dumps(edge, sort_keys=True))
    return {'nodes': nodes, 'edges': edges, 'execution_config': data.get('execution_config')}


def semantic_sha256(graph: FlowchartPipeline | Dict[str, Any]) -> str:
    canonical = json.dumps(semantic_projection(graph), ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(canonical.encode('utf-8')).hexdigest()


def _changed_fields(before: Any, after: Any, path: str, out: list) -> None:
    if isinstance(before, dict) and isinstance(after, dict):
        for key in sorted(set(before) | set(after)):
            _changed_fields(before.get(key), after.get(key), f'{path}.{key}' if path else key, out)
    elif before != after:
        out.append({'field': path, 'before': before, 'after': after})


def semantic_delta(before: FlowchartPipeline | Dict[str, Any] | None, after: FlowchartPipeline | Dict[str, Any]) -> Dict[str, Any]:
    """The inspection-rule difference between two flows (none when only the layout changed). ``before`` may be None for
    the first saved flow."""
    new = semantic_projection(after)
    old = semantic_projection(before) if before is not None else {'nodes': {}, 'edges': [], 'execution_config': None}
    changes = []
    for node_id in sorted(set(old['nodes']) | set(new['nodes'])):
        if node_id not in old['nodes']:
            changes.append({'kind': 'node_added', 'node_id': node_id, 'node_type': new['nodes'][node_id].get('node_type')})
        elif node_id not in new['nodes']:
            changes.append({'kind': 'node_removed', 'node_id': node_id, 'node_type': old['nodes'][node_id].get('node_type')})
        else:
            fields: list = []
            _changed_fields(old['nodes'][node_id], new['nodes'][node_id], '', fields)
            changes.extend({'kind': 'node_changed', 'node_id': node_id, **field} for field in fields)
    key = lambda edge: json.dumps(edge, sort_keys=True)
    old_edges, new_edges = {key(edge): edge for edge in old['edges']}, {key(edge): edge for edge in new['edges']}
    changes.extend({'kind': 'edge_removed', **old_edges[edge]} for edge in sorted(set(old_edges) - set(new_edges)))
    changes.extend({'kind': 'edge_added', **new_edges[edge]} for edge in sorted(set(new_edges) - set(old_edges)))
    if old['execution_config'] != new['execution_config'] and before is not None:
        changes.append({'kind': 'execution_changed', 'before': old['execution_config'], 'after': new['execution_config']})
    layout_only = before is not None and not changes and pipeline_sha256(before) != pipeline_sha256(after)
    return {'changes': changes, 'layout_only': layout_only, 'semantic_sha256_before': semantic_sha256(before) if before is not None else None,
            'semantic_sha256_after': semantic_sha256(after)}
