import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('inventory', Path(__file__).parents[1] / 'windows_package_inventory.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

class InventoryTests(unittest.TestCase):
    def test_pass_requires_both_unsigned_artifact_kinds_and_never_exports_environment(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'release').mkdir()
            (root / 'packaged-evidence').mkdir()
            (root / 'packaged-evidence' / 'packaged-receipt.json').write_text(json.dumps({'status': 'passed'}), encoding='utf-8')
            (root / 'package-lock.json').write_text(json.dumps({'packages': {'node_modules/' + name: {'version': '1'} for name in ('electron', 'electron-builder', '@playwright/test')}}), encoding='utf-8')
            with patch.object(module.subprocess, 'check_output', side_effect=lambda *a, **kw: '' if kw.get('text') else b''), patch.dict(os.environ, {'X_VISION_TOKEN': 'sentinel-secret'}):
                with self.assertRaisesRegex(ValueError, 'NSIS'):
                    module.inventory(root, root / 'cache')
                (root / 'release' / 'Vision Setup.exe').write_bytes(b'installer')
                (root / 'release' / 'Vision.exe').write_bytes(b'portable')
                result = module.inventory(root, root / 'cache')
                self.assertEqual(result['status'], 'passed')
                self.assertFalse(result['release_ready'])
                self.assertNotIn('sentinel-secret', json.dumps(result))
                self.assertEqual(len(result['artifacts']), 2)
                (root / 'packaged-evidence' / 'packaged-receipt.json').write_text(json.dumps({'status': 'failed', 'cleanup': 'failed', 'cleanup_error': 'EBUSY'}), encoding='utf-8')
                failed = module.inventory(root, root / 'cache')
                self.assertEqual(failed['status'], 'failed')
                self.assertEqual(failed['failure_stage'], 'packaged')

    def test_failed_or_absent_app_proof_cannot_promote_build_artifacts(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'package-lock.json').write_text(json.dumps({'packages': {'node_modules/' + name: {'version': '1'} for name in ('electron', 'electron-builder', '@playwright/test')}}), encoding='utf-8')
            with patch.object(module.subprocess, 'check_output', side_effect=lambda *a, **kw: '' if kw.get('text') else b''):
                self.assertEqual(module.inventory(root, root / 'cache')['status'], 'partial')
