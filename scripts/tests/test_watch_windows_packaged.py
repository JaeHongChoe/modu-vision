import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('watcher', Path(__file__).parents[1] / 'watch_windows_packaged.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

class IdentityTests(unittest.TestCase):
    def test_source_python_never_claims_frozen_child(self):
        self.assertIsNone(module.classify(['-m', 'backend.engine.worker_preflight'], 'python.exe'))

    def test_internal_child_is_bound_to_delivered_backend(self):
        self.assertEqual(module.classify(['vision_ai_backend.exe', '-m', 'backend.engine.worker_preflight'], 'vision_ai_backend.exe'), 'preflight')
        self.assertEqual(module.classify(['vision_ai_backend.exe', '--port', '0'], 'vision_ai_backend.exe'), 'backend')

    def test_token_is_never_returned(self):
        self.assertEqual(module.classify(['--token', 'secret'], 'vision_ai_backend.exe'), 'backend')
