"""Cold control commands must not initialize the model or dataset runtime."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
EXPORTS = {
    'device': ['DeviceInfo', 'DeviceMemoryInfo', 'DynamicAMPContext', 'HostTelemetry', 'MemoryStats',
        'autocast_context', 'clear_device_cache', 'detect_device', 'get_device', 'get_device_info',
        'get_host_telemetry', 'get_memory_info', 'get_memory_stats', 'to_device'],
    'synthetic_generator': ['Modality', 'SyntheticSample', 'VisionTask', 'generate_metal_sample',
        'generate_pcb_sample', 'generate_synthetic_dataset', 'generate_wafer_sample', 'render_sample'],
    'dataset_loaders': ['AnomalyDataset', 'BoundingBox', 'ClassificationDataset', 'CocoJsonParser',
        'DetectionDataset', 'PascalVocParser', 'SegmentationDataset', 'ValidationResult',
        'create_dataloader', 'detection_collate_fn', 'inspect_dataset', 'split_dataset', 'validate_image_file'],
}


def cold(tmp_path, code):
    home = tmp_path / 'home'; home.mkdir()
    environment = {'PATH': os.defpath, 'HOME': str(home), 'TMPDIR': str(tmp_path),
        'PYTHONDONTWRITEBYTECODE': '1', 'CUDA_VISIBLE_DEVICES': '', 'NVIDIA_VISIBLE_DEVICES': 'none',
        'HF_HUB_OFFLINE': '1', 'TRANSFORMERS_OFFLINE': '1', 'HF_DATASETS_OFFLINE': '1',
        'OMP_NUM_THREADS': '1', 'MKL_NUM_THREADS': '1', 'OPENBLAS_NUM_THREADS': '1'}
    prefix = 'import sys,json,importlib;sys.path.insert(0,' + repr(str(ROOT)) + ');'
    result = subprocess.run([sys.executable, '-I', '-B', '-c', prefix + code],
        cwd=ROOT, env=environment, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout.splitlines()[-1])


def no_tensor_modules():
    return "[n for n in sys.modules if n=='torch' or n.startswith('torch.') or n=='torchvision' or n.startswith('torchvision.') or n in ('backend.engine.device','backend.engine.dataset_loaders')]"


@pytest.mark.parametrize('module', ['backend.engine', 'backend.engine.global_migration', 'backend.engine.runtime_update'])
def test_cold_control_import_does_not_load_tensor_or_dataset_runtime(tmp_path, module):
    # An eager package initializer makes these actual imports load torch even
    # though no control command has asked for a model or device capability.
    value = cold(tmp_path, 'importlib.import_module(' + repr(module) + ');print(json.dumps(' + no_tensor_modules() + '))')
    assert value == [], value


def test_actual_global_migration_help_is_a_cold_control_command(tmp_path):
    value = cold(tmp_path, "from backend.engine.global_migration import main\n"
        "try: main(['--help'])\n"
        "except SystemExit as error: assert error.code==0\n"
        "print(json.dumps(" + no_tensor_modules() + "))")
    assert value == [], value


def test_single_synthetic_export_does_not_load_other_runtime_groups(tmp_path):
    value = cold(tmp_path, "import backend.engine as engine;from backend.engine.synthetic_generator import Modality;"
        "assert engine.Modality is Modality;print(json.dumps(" + no_tensor_modules() + "))")
    assert value == [], value


def test_named_and_star_exports_keep_the_original_public_objects(tmp_path):
    code = "import backend.engine as engine\nexports=" + repr(EXPORTS) + "\n"
    code += "expected=[name for names in exports.values() for name in names]\nassert engine.__all__==expected\n"
    code += "for module,names in exports.items():\n owner=importlib.import_module('backend.engine.'+module)\n for name in names:\n  assert getattr(engine,name) is getattr(owner,name),name\n"
    code += "scope={}\nexec('from backend.engine import *',scope)\n"
    code += "for name in expected: assert scope[name] is getattr(engine,name),name\n"
    code += "print(json.dumps({'resolved':len(expected),'star_objects_identical':True}))"
    assert cold(tmp_path, code) == {'resolved': 35, 'star_objects_identical': True}


def test_unknown_attribute_stays_absent_and_submodule_from_import_still_works(tmp_path):
    value = cold(tmp_path, "import backend.engine as engine\n"
        "try: getattr(engine,'not_a_public_engine_export')\n"
        "except AttributeError: pass\n"
        "else: raise AssertionError('Unknown export resolved')\n"
        "from backend.engine import global_migration\n"
        "assert global_migration is importlib.import_module('backend.engine.global_migration')\n"
        "print(json.dumps(" + no_tensor_modules() + "))")
    assert value == [], value
