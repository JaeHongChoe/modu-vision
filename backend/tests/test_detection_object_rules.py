"""Required-object rules use completed detector evidence, never invented ROIs.

The deterministic network is a rule boundary control, not model quality proof.
"""
import numpy as np
import pytest
import torch

from backend.engine.flowchart_engine import (
    FlowchartEngine, get_single_detection_flowchart, ordered_linear_nodes,
)
from backend.engine.flow_class_validation import validate_recorded_flow_classes


def control(monkeypatch, labels=(), trained=True):
    graph = get_single_detection_flowchart("job_count_control")
    node = next(n for n in graph.nodes if n.data.node_type == "detection_crop")
    node.data.params["object_requirements"] = [{"class_name": "bolt", "min_count": 1, "max_count": 2}]
    instance = FlowchartEngine(device="cpu", checkpoint_resolver=lambda *_: None)

    class Network(torch.nn.Module):
        def forward(self, image):
            return [{"boxes": torch.tensor([[2., 2., 18., 18.]] * len(labels)).reshape(-1, 4),
                     "scores": torch.ones(len(labels)) * .99,
                     "labels": torch.tensor(labels, dtype=torch.int64)}]

    monkeypatch.setattr(instance, "_get_detection_model", lambda **_: (Network(), trained))
    instance._model_classes[("detection", "job_count_control", "fast")] = ["bolt", "washer"]
    return instance, graph, node


@pytest.mark.parametrize("labels,verdict,count", [((), "NG", 0), ((1,), "OK", 1), ((2,), "NG", 0), ((1, 1, 1), "NG", 3)])
def test_completed_counts_drive_missing_and_excess_verdict(monkeypatch, labels, verdict, count):
    instance, graph, node = control(monkeypatch, labels)
    result = instance.execute(pipeline=graph, image=np.zeros((32, 32, 3), np.uint8))
    assert result["final_verdict"] == verdict
    step = next(s for s in result["execution_steps"] if s["node_id"] == node.id)
    assert step["count_rule_results"] == [{"class_name": "bolt", "count": count, "min_count": 1, "max_count": 2, "verdict": verdict}]
    assert len(result["crops"]) == len(labels), "absence has no fabricated crop"
    assert all(c["verdict"] == verdict for c in result["crops"])


def test_untrained_objects_never_satisfy_required_count(monkeypatch):
    instance, graph, _ = control(monkeypatch, (1,), trained=False)
    result = instance.execute(pipeline=graph, image=np.zeros((32, 32, 3), np.uint8))
    assert result["final_verdict"] == "REVIEW"
    assert all(not s["count_rule_results"] for s in result["execution_steps"])


@pytest.mark.parametrize("rules", [[], [{"class_name": "bolt", "min_count": True}],
    [{"class_name": "bolt", "min_count": -1}], [{"class_name": "bolt", "min_count": 2, "max_count": 1}],
    [{"class_name": "bolt", "min_count": 1, "extra": 2}],
    [{"class_name": "bolt", "min_count": 1}] * 2])
def test_malformed_count_contract_is_refused(monkeypatch, rules):
    _, graph, node = control(monkeypatch)
    node.data.params["object_requirements"] = rules
    with pytest.raises(ValueError, match="object_requirements"):
        ordered_linear_nodes(graph)


def test_unknown_class_refused_at_save_and_execution(monkeypatch):
    instance, graph, node = control(monkeypatch)
    node.data.params["object_requirements"][0]["class_name"] = "unknown"
    with pytest.raises(ValueError, match="recorded"):
        validate_recorded_flow_classes(graph, lambda _: {"task": "detection", "classes": ["bolt", "washer"]})
    with pytest.raises(ValueError, match="recorded"):
        instance.execute(pipeline=graph, image=np.zeros((32, 32, 3), np.uint8))


def test_unavailable_vocabulary_refuses_save(monkeypatch):
    _, graph, _ = control(monkeypatch)
    with pytest.raises(ValueError, match="recorded"):
        validate_recorded_flow_classes(graph, lambda _: {"task": "detection"})


def test_zero_allowed_count_is_explicit_ok(monkeypatch):
    instance, graph, node = control(monkeypatch)
    node.data.params["object_requirements"] = [{"class_name": "bolt", "min_count": 0, "max_count": 0}]
    assert instance.execute(pipeline=graph, image=np.zeros((32, 32, 3), np.uint8))["final_verdict"] == "OK"


@pytest.mark.parametrize("label",[0,99])
def test_unmapped_prediction_labels_cannot_claim_missing_or_present_objects(monkeypatch,label):
    instance,graph,_=control(monkeypatch,(label,))
    with pytest.raises(RuntimeError,match='foreground class IDs'):
        instance.execute(pipeline=graph,image=np.zeros((32,32,3),np.uint8))
