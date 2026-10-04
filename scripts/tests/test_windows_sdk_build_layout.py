"""The documented SDK commands must find actual CMake multi-config outputs."""
import contextlib
import io
from pathlib import Path
import runpy
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[2] / 'native_runtime' / 'build_native.py'
ARTIFACTS = ('modu_vision_runtime.dll', 'modu_vision_runtime.lib', 'vision_predict.exe', 'vision_execute.exe')


class WindowsBuildLayout(unittest.TestCase):
    def run_build(self, output, missing=None):
        def cmake(argv, **kwargs):
            if '--build' in argv:
                release = output / 'Release'
                release.mkdir()
                for name in ARTIFACTS:
                    if name != missing:
                        (release / name).write_bytes(('compiled:' + name).encode())
            return subprocess.CompletedProcess(argv, 0)

        with patch.object(sys, 'argv', [str(SCRIPT), '--output', str(output)]), \
                patch('platform.system', return_value='Windows'), \
                patch('subprocess.run', side_effect=cmake), \
                contextlib.redirect_stdout(io.StringIO()):
            runpy.run_path(str(SCRIPT), run_name='__main__')

    def test_multi_config_outputs_are_available_at_documented_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'native'
            self.run_build(output)
            for name in ARTIFACTS:
                self.assertEqual((output / name).read_bytes(), (output / 'Release' / name).read_bytes())

    def test_missing_native_output_cannot_be_reported_built(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex((FileNotFoundError, RuntimeError), 'vision_execute'):
                self.run_build(Path(directory) / 'native', missing='vision_execute.exe')


if __name__ == '__main__':
    unittest.main()
