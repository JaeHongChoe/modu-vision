"""
backend/engine package
Vision AI Studio - AutoML Deep Learning Engine
"""

from backend.engine.device import (
    DeviceInfo,
    DeviceMemoryInfo,
    DynamicAMPContext,
    HostTelemetry,
    MemoryStats,
    autocast_context,
    clear_device_cache,
    detect_device,
    get_device,
    get_device_info,
    get_host_telemetry,
    get_memory_info,
    get_memory_stats,
    to_device,
)
from backend.engine.synthetic_generator import (
    Modality,
    SyntheticSample,
    VisionTask,
    generate_metal_sample,
    generate_pcb_sample,
    generate_synthetic_dataset,
    generate_wafer_sample,
    render_sample,
)
from backend.engine.dataset_loaders import (
    AnomalyDataset,
    BoundingBox,
    ClassificationDataset,
    CocoJsonParser,
    DetectionDataset,
    PascalVocParser,
    SegmentationDataset,
    ValidationResult,
    create_dataloader,
    detection_collate_fn,
    inspect_dataset,
    split_dataset,
    validate_image_file,
)

__all__ = [
    # Device
    "DeviceInfo",
    "DeviceMemoryInfo",
    "DynamicAMPContext",
    "HostTelemetry",
    "MemoryStats",
    "autocast_context",
    "clear_device_cache",
    "detect_device",
    "get_device",
    "get_device_info",
    "get_host_telemetry",
    "get_memory_info",
    "get_memory_stats",
    "to_device",
    # Synthetic Generator
    "Modality",
    "SyntheticSample",
    "VisionTask",
    "generate_metal_sample",
    "generate_pcb_sample",
    "generate_synthetic_dataset",
    "generate_wafer_sample",
    "render_sample",
    # Dataset Loaders
    "AnomalyDataset",
    "BoundingBox",
    "ClassificationDataset",
    "CocoJsonParser",
    "DetectionDataset",
    "PascalVocParser",
    "SegmentationDataset",
    "ValidationResult",
    "create_dataloader",
    "detection_collate_fn",
    "inspect_dataset",
    "split_dataset",
    "validate_image_file",
]
