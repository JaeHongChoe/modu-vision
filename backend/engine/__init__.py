"""
backend/engine package
Vision AI Studio - AutoML Deep Learning Engine
"""

from importlib import import_module as _import_module


_EXPORT_MODULES = {
    **dict.fromkeys((
        'DeviceInfo', 'DeviceMemoryInfo', 'DynamicAMPContext', 'HostTelemetry', 'MemoryStats',
        'autocast_context', 'clear_device_cache', 'detect_device', 'get_device', 'get_device_info',
        'get_host_telemetry', 'get_memory_info', 'get_memory_stats', 'to_device',
    ), 'device'),
    **dict.fromkeys((
        'Modality', 'SyntheticSample', 'VisionTask', 'generate_metal_sample', 'generate_pcb_sample',
        'generate_synthetic_dataset', 'generate_wafer_sample', 'render_sample',
    ), 'synthetic_generator'),
    **dict.fromkeys((
        'AnomalyDataset', 'BoundingBox', 'ClassificationDataset', 'CocoJsonParser',
        'DetectionDataset', 'PascalVocParser', 'SegmentationDataset', 'ValidationResult',
        'create_dataloader', 'detection_collate_fn', 'inspect_dataset', 'split_dataset',
        'validate_image_file',
    ), 'dataset_loaders'),
}


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


def __getattr__(name):
    """Resolve an existing public capability only when it is requested."""
    module = _EXPORT_MODULES.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(_import_module(f'{__name__}.{module}'), name)
    globals()[name] = value
    return value
