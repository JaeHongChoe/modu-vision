"""Check class-bearing flow rules against server-resolved model metadata."""

from collections.abc import Callable, Mapping
from typing import Any

from backend.engine.flow_workspace import catalog_class_vocabulary


def _recorded_vocabulary(record: Mapping[str, Any], node_id: str) -> dict[str, list]:
    """A missing class record is unknown; an invalid supplied record is an error."""
    if not isinstance(record, Mapping):
        raise ValueError(f"Model node {node_id} has malformed class metadata.")
    nested = record.get("metadata")
    if nested is not None and not isinstance(nested, Mapping):
        raise ValueError(f"Model node {node_id} has malformed class metadata.")
    value = {**(nested or {}), **record}
    for field in ("class_names", "classes"):
        names = value.get(field)
        if names is not None and (
            not isinstance(names, list)
            or any(not isinstance(name, str) or not name.strip() for name in names)
            or len(set(names)) != len(names)
        ):
            raise ValueError(f"Model node {node_id} has malformed recorded {field}.")
    if value.get("class_names") is not None and value.get("classes") is not None:
        named = catalog_class_vocabulary({"task": value.get("task"), "class_names": value["class_names"]})
        classes = catalog_class_vocabulary({"task": value.get("task"), "classes": value["classes"]})
        if named["class_names"] != classes["class_names"]:
            raise ValueError(f"Model node {node_id} has conflicting recorded class names and classes.")
    name = value.get("class_name")
    if name is not None and (not isinstance(name, str) or not name.strip()):
        raise ValueError(f"Model node {node_id} has malformed recorded class_name.")
    ids = value.get("class_ids")
    if ids is not None and (
        not isinstance(ids, list)
        or any(type(index) is not int or index < 0 for index in ids)
        or len(set(ids)) != len(ids)
    ):
        raise ValueError(f"Model node {node_id} has malformed recorded class_ids.")
    vocabulary = catalog_class_vocabulary(value)
    names = vocabulary.get("class_names")
    if ids is not None and names is not None and len(ids) != len(names):
        raise ValueError(f"Model node {node_id} has inconsistent recorded class names and IDs.")
    if value.get("task") == "segmentation" and ids is not None and names is not None and ids != list(range(len(names))):
        raise ValueError(f"Model node {node_id} recorded segmentation class IDs differ from channel positions.")
    return vocabulary


def validate_recorded_flow_classes(pipeline: Any, resolve_model: Callable[[Any], Mapping[str, Any]]) -> None:
    """Validate known classes without treating draft declarations as model evidence.

    Structural validation runs before this function. A predicate belongs to its
    source model, segmentation settings to their own model, and Blob settings to
    the directly connected inspection model. An unbound model is an unknown
    boundary even when a detector with recorded classes exists farther upstream.
    """
    nodes = {node.id: node for node in pipeline.nodes}
    incoming = {node_id: [] for node_id in nodes}
    for edge in pipeline.edges:
        incoming[edge.target].append(edge.source)
    vocabularies: dict[str, dict[str, list]] = {}

    def vocabulary(node_id: str) -> dict[str, list]:
        node = nodes[node_id]
        if not node.data.model_job_id:
            return {}
        if node_id not in vocabularies:
            vocabularies[node_id] = _recorded_vocabulary(resolve_model(node), node_id)
        return vocabularies[node_id]

    for edge in pipeline.edges:
        if edge.predicate is None:
            continue
        names = vocabulary(edge.source).get("class_names")
        wanted = edge.predicate["class_name"]
        if names and wanted not in names:
            raise ValueError(
                f"Edge {edge.id} class '{wanted}' is not recorded by source model node {edge.source}."
            )

    for node in pipeline.nodes:
        if node.data.node_type == "inspection" and node.data.task == "segmentation":
            model_id = node.id
        elif node.data.node_type == "blob_measure":
            model_id = incoming[node.id][0]
        else:
            continue
        params = node.data.params
        selected = list(params.get("class_ids") or [])
        selected.extend(rule["class_id"] for rule in params.get("class_rules", []))
        declared_names = params.get("class_names") if model_id == node.id else None
        if not selected and not declared_names:
            continue
        recorded = vocabulary(model_id)
        ids = recorded.get("class_ids")
        if ids:
            missing = sorted(set(selected) - {index for index in ids if index > 0})
            if missing:
                raise ValueError(
                    f"Node {node.id} class IDs {missing} are not recorded foreground classes of model node {model_id}."
                )
        names = recorded.get("class_names")
        if declared_names and names and declared_names != names:
            raise ValueError(
                f"Node {node.id} segmentation class_names differs from model node {model_id}'s recorded class order."
            )
