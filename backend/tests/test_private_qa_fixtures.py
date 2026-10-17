"""Portable prerequisites for explicitly requested external qualification tests."""
import importlib
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


def root_resolver():
    try:
        return importlib.import_module('backend.tests.private_qa_fixtures').private_qa_root
    except ModuleNotFoundError as error:
        raise AssertionError('An explicit private qualification root resolver is required') from error


class PrivateQAFixtureRootTests(unittest.TestCase):
    def test_missing_or_empty_root_is_an_explicit_prerequisite_failure(self):
        for value in (None, '', '   '):
            with self.subTest(value=value), patch.dict(os.environ, {}, clear=True):
                if value is not None:
                    os.environ['MODU_PRIVATE_QA_ROOT'] = value
                with self.assertRaisesRegex(RuntimeError, 'MODU_PRIVATE_QA_ROOT'):
                    root_resolver()()

    def test_relative_or_missing_root_cannot_adopt_the_callers_working_directory(self):
        for value in ('relative-fixture-root', str(Path(tempfile.gettempdir()) / 'missing-private-qa-root-qualification')):
            with self.subTest(value=value), patch.dict(os.environ, {'MODU_PRIVATE_QA_ROOT': value}):
                with self.assertRaisesRegex((RuntimeError, FileNotFoundError), 'MODU_PRIVATE_QA_ROOT'):
                    root_resolver()()

    def test_explicit_existing_root_with_spaces_resolves_without_reading_or_copying_inputs(self):
        with tempfile.TemporaryDirectory(prefix='private qa root ') as directory:
            root = Path(directory)
            original = root / 'original-control.txt'
            original.write_bytes(b'explicit fixture custody\n')
            with patch.dict(os.environ, {'MODU_PRIVATE_QA_ROOT': str(root)}):
                self.assertEqual(root_resolver()(), root.resolve())
            self.assertEqual(original.read_bytes(), b'explicit fixture custody\n')
            self.assertEqual(list(root.iterdir()), [original])

    def test_external_modules_fail_before_importing_runtime_dependencies_without_the_root(self):
        repo = Path(__file__).resolve().parents[2]
        environment = {key: value for key, value in os.environ.items()
                       if key not in ('MODU_PRIVATE_QA_ROOT', 'MV_HTTP_ORIGINAL_ROOT')}
        environment['PYTHONPATH'] = str(repo)
        for name in ('current_capture_cohort', 'http_capture', 'native_wire_graph', 'shared_authorized_continuation'):
            with self.subTest(module=name):
                result = subprocess.run([sys.executable, '-c',
                    f'import backend.tests.test_required_flow_{name}'], cwd=tempfile.gettempdir(),
                    env=environment, capture_output=True, text=True, timeout=20)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('MODU_PRIVATE_QA_ROOT', result.stderr)
                self.assertNotIn('ModuleNotFoundError', result.stderr)
