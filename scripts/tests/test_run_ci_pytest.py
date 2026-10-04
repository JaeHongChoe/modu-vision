"""Exercise the CI runner against real passing, failing, and stalled pytest children."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / 'scripts' / 'run_ci_pytest.py'


class CiPytestRunnerTests(unittest.TestCase):
    def setUp(self):
        # The hosted Windows checkout and default OS temp folder can be on
        # different drives. Exercise the gate's actual in-checkout selectors.
        self.temporary = tempfile.TemporaryDirectory(prefix='.ci-pytest-runner-test-', dir=ROOT)
        self.addCleanup(self.temporary.cleanup)
        self.folder = Path(self.temporary.name)

    def write_test(self, name, content):
        path = self.folder / name
        path.write_text(content, encoding='utf-8')
        return path

    def run_gate(self, selections, timeout=20):
        output = self.folder / 'receipts'
        combined = self.folder / 'combined.xml'
        result = subprocess.run(
            [sys.executable, str(RUNNER), '--output-dir', str(output), '--junit', str(combined),
             '--file-timeout', str(timeout), '--diagnostic-timeout', '1', *map(str, selections)],
            cwd=ROOT, text=True, encoding='utf-8', capture_output=True, timeout=60,
            env={**os.environ, 'PYTEST_DISABLE_PLUGIN_AUTOLOAD': '1', 'MODU_TEST_DATA_DIR': 'inherited-store'},
        )
        self.diagnostics = (f'fixture={self.folder}\ncheckout={ROOT}\npython={sys.executable}\n'
                            f'stdout:\n{result.stdout}\nstderr:\n{result.stderr}')
        return result, output, combined

    def test_failed_selection_keeps_its_failure_and_runs_the_next_selection(self):
        failed = self.write_test('test_failed.py', "def test_broken():\n    assert False, 'preserved failure'\n")
        passed = self.write_test('test_passed.py', "def test_good():\n    assert True\n")
        result, output, combined = self.run_gate([failed, passed])
        self.assertEqual(result.returncode, 1, self.diagnostics)
        summary = json.loads((output / 'summary.json').read_text())
        self.assertEqual([row['status'] for row in summary['selections']], ['failed', 'passed'], self.diagnostics)
        tree = ET.parse(combined)
        self.assertEqual(len(tree.findall('.//testcase')), 2, self.diagnostics)
        self.assertEqual(len(tree.findall('.//failure')), 1, self.diagnostics)
        self.assertIn('preserved failure', tree.find('.//failure').text, self.diagnostics)
        self.assertTrue(all((output / row['log']).is_file() for row in summary['selections']), self.diagnostics)

    def test_timeout_is_an_error_with_last_node_and_following_selection_still_runs(self):
        stalled = self.write_test('test_stalled.py', "import time\ndef test_stalled():\n    time.sleep(30)\n")
        passed = self.write_test('test_passed.py', "def test_good():\n    assert True\n")
        # Windows startup counts against this file budget. Leave time for the
        # one-second diagnostic timer to fire after pytest starts the node;
        # the deliberately stalled call still lasts 30 seconds and must fail.
        result, output, combined = self.run_gate([stalled, passed], timeout=10)
        self.assertEqual(result.returncode, 1, self.diagnostics)
        summary = json.loads((output / 'summary.json').read_text())
        self.assertEqual([row['status'] for row in summary['selections']], ['timed_out', 'passed'], self.diagnostics)
        self.assertIn('test_stalled', summary['selections'][0]['last_event']['nodeid'], self.diagnostics)
        tree = ET.parse(combined)
        self.assertEqual(len(tree.findall('.//error')), 1, self.diagnostics)
        self.assertEqual(len(tree.findall('.//testcase')), 2, self.diagnostics)
        self.assertIn('test_stalled', tree.find('.//error').text, self.diagnostics)
        self.assertIn('Timeout', (output / summary['selections'][0]['log']).read_text(), self.diagnostics)

    def test_each_selection_has_a_fresh_store(self):
        test = self.write_test('test_store.py', "import os\nfrom pathlib import Path\ndef test_fresh_store():\n    store=Path(os.environ['MODU_TEST_DATA_DIR'])\n    store.mkdir(exist_ok=True)\n    marker=store/'marker'\n    assert not marker.exists()\n    marker.write_text('owned')\n")
        result, output, combined = self.run_gate([test, test])
        self.assertEqual(result.returncode, 0, self.diagnostics)
        summary = json.loads((output / 'summary.json').read_text())
        self.assertEqual(summary['status'], 'passed', self.diagnostics)
        self.assertEqual(len({row['store'] for row in summary['selections']}), 2, self.diagnostics)
        self.assertEqual(len(ET.parse(combined).findall('.//testcase')), 2, self.diagnostics)

    def test_a_zero_exit_without_pytest_results_is_an_error(self):
        vanished = self.write_test('test_vanished.py', "import os\nos._exit(0)\n")
        result, output, combined = self.run_gate([vanished])
        self.assertEqual(result.returncode, 1, self.diagnostics)
        summary = json.loads((output / 'summary.json').read_text())
        self.assertEqual(summary['selections'][0]['status'], 'missing_result', self.diagnostics)
        self.assertEqual(len(ET.parse(combined).findall('.//error')), 1, self.diagnostics)

    def test_application_replace_failure_does_not_intercept_the_progress_journal(self):
        test = self.write_test('test_application_replace.py',
            "import os\nimport pytest\n"
            "def test_application_replace_failure(monkeypatch):\n"
            "    def denied(*args, **kwargs):\n"
            "        raise PermissionError(13, 'application replace denied')\n"
            "    monkeypatch.setattr(os, 'replace', denied)\n"
            "    with pytest.raises(PermissionError, match='application replace denied'):\n"
            "        os.replace('application-source', 'application-target')\n")
        result, output, combined = self.run_gate([test])
        self.assertEqual(result.returncode, 0, self.diagnostics)
        summary = json.loads((output / 'summary.json').read_text())
        self.assertEqual(summary['selections'][0]['status'], 'passed', self.diagnostics)
        self.assertEqual(summary['selections'][0]['last_event']['phase'], 'teardown', self.diagnostics)
        self.assertEqual(len(ET.parse(combined).findall('.//testcase')), 1, self.diagnostics)

    def test_timeout_preserves_only_thin_preflight_logs_as_diagnostics(self):
        test = self.write_test('test_preflight_trace.py',
            "import time\n"
            "def test_preflight_trace(tmp_path):\n"
            "    run = tmp_path / 'worker_preflight_runs' / 'classification-owned'\n"
            "    run.mkdir(parents=True)\n"
            "    (run / 'preflight.log').write_text('PREFLIGHT_STAGE_START train\\nowned child trace')\n"
            "    (run / 'best_model.pt').write_bytes(b'private synthetic model')\n"
            "    time.sleep(30)\n")
        result, output, _ = self.run_gate([test], timeout=10)
        self.assertEqual(result.returncode, 1, self.diagnostics)
        summary = json.loads((output / 'summary.json').read_text())
        row = summary['selections'][0]
        self.assertEqual(row['status'], 'timed_out', self.diagnostics)
        self.assertIn('diagnostics', row, self.diagnostics)
        diagnostics = output / row['diagnostics']
        logs = list(diagnostics.glob('preflight-*.log'))
        self.assertEqual(len(logs), 1, self.diagnostics)
        self.assertIn('owned child trace', logs[0].read_text(), self.diagnostics)
        self.assertFalse(list(diagnostics.glob('*.pt')), self.diagnostics)
        index = json.loads((diagnostics / 'preflight-log-index.json').read_text())
        self.assertEqual(len(index), 1, self.diagnostics)


if __name__ == '__main__':
    unittest.main()
