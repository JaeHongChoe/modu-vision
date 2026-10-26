"""Inspection graph contracts shared by execution and provenance validation.

Keep graph normalization independent of model runtimes so receipt validation
can verify the same schema without loading inference engines.
"""
from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_serializer


class FlowNodeData(BaseModel):
    model_config = ConfigDict(extra="ignore")
    label: str
    node_type: Literal["input", "fixed_roi", "patch_split", "preprocess", "detection_crop", "inspection", "blob_measure", "measurement", "aggregate", "decision", "output"]
    task: Optional[str] = "anomaly"  # 'detection', 'anomaly', 'segmentation', 'classification'
    model_job_id: Optional[str] = None
    threshold: Optional[float] = 0.5
    score_spec: Optional[Dict[str, Any]] = None
    crop_padding: Optional[int] = 10
    rule: Optional[str] = "any_defect_is_ng"  # 'any_defect_is_ng', 'score_gt_threshold', 'max_flaws_allowed'
    params: Dict[str, Any] = Field(default_factory=dict)


class FlowNode(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    type: str = "custom"
    position: Dict[str, float]  # {"x": 100, "y": 150}
    data: FlowNodeData


class FlowEdge(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    source: str
    target: str
    label: Optional[str] = None
    isBranch: Optional[Literal["pass", "fail", "review", "default"]] = None
    payload_type: Optional[Literal["image", "roi", "result"]] = None
    predicate: Optional[Dict[str, Any]] = None


class FlowExecutionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    max_workers: int = Field(default=1, ge=1, le=8, strict=True)
    device_slots: int = Field(default=1, ge=1, le=8, strict=True)


class FlowchartPipeline(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str = "default_pipeline"
    name: str = "PCB Component Multi-Model Inspection"
    description: Optional[str] = "Stage 1: Detect IC Chips -> Stage 2: Solder Void & Anomaly -> Stage 3: OK/NG"
    nodes: List[FlowNode] = Field(default_factory=list)
    edges: List[FlowEdge] = Field(default_factory=list)
    execution_config: FlowExecutionConfig = Field(default_factory=FlowExecutionConfig)
    capture_group_policy: Optional[Dict[str,Any]] = None

    @field_validator('capture_group_policy')
    @classmethod
    def validate_capture_group_policy(cls,value):
        if value is None:return None
        from backend.engine.service_capture_groups import validate_join_policy
        return validate_join_policy(value)

    @model_serializer(mode='wrap')
    def legacy_capture_serialization(self,handler):
        result=handler(self)
        if self.capture_group_policy is None:result.pop('capture_group_policy',None)
        return result
