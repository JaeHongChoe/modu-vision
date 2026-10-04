"""Run every selected pytest file in a fresh, bounded child and preserve partial evidence.

A timed-out, unstarted, or missing-result selection is an error, never a skip or
pass. Windows children use retained process handles and a kill-on-close Job
Object, including descendants; this does not register or alter an SCM service.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
# Application tests deliberately replace os.replace to exercise Windows readers.
# Receipt publication must not consume that application failure injection.
_RECEIPT_REPLACE = os.replace


def atomic_json(path, value):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=True), encoding='utf-8')
    _RECEIPT_REPLACE(temporary, path)


def merged_junit(rows, output, destination):
    tree = ET.Element('testsuites')
    for row in rows:
        path = output / row['junit']
        if row['status'] in ('passed', 'failed') and path.is_file():
            try:
                source = ET.parse(path).getroot()
                suites = [source] if source.tag == 'testsuite' else list(source.findall('testsuite'))
                if not suites:
                    raise ValueError('No test suite was recorded')
                for suite in suites:
                    tree.append(suite)
                continue
            except (ET.ParseError, ValueError):
                pass
        suite = ET.SubElement(tree, 'testsuite', name=row['selection'], tests='1', failures='0', errors='1', skipped='0')
        case = ET.SubElement(suite, 'testcase', classname='ci.selection', name=row['selection'])
        error = ET.SubElement(case, 'error', type='IncompleteSelection', message=row['status'])
        error.text = json.dumps({'status': row['status'], 'last_event': row.get('last_event'), 'log': row['log']})
    temporary = destination.with_suffix('.tmp')
    ET.ElementTree(tree).write(temporary, encoding='utf-8', xml_declaration=True)
    _RECEIPT_REPLACE(temporary, destination)


def preserve_preflight_logs(temporary_root, diagnostics):
    """Copy bounded text diagnostics; synthetic models/images/DBs stay private."""
    diagnostics.mkdir(exist_ok=True)
    index = []
    for source in sorted(temporary_root.rglob('preflight.log')):
        if source.is_symlink():
            continue
        row = {'source': source.relative_to(temporary_root).as_posix()}
        try:
            size = source.stat().st_size
            name = f'preflight-{len(index):03d}.log'
            with source.open('rb') as reader:
                reader.seek(max(0, size - 1024 * 1024))
                (diagnostics / name).write_bytes(reader.read(1024 * 1024))
            row.update(log=name, source_bytes=size, tail_only=size > 1024 * 1024)
        except OSError as exc:
            row['error'] = str(exc)
        index.append(row)
    atomic_json(diagnostics / 'preflight-log-index.json', index)


class ProgressJournal:
    def __init__(self, path):
        self.path = path

    def pytest_runtest_logstart(self, nodeid, location):
        atomic_json(self.path, {'nodeid': nodeid, 'phase': 'started', 'observed_at': time.time()})
        print(f'CI_NODE_START {nodeid}', flush=True)

    def pytest_runtest_logreport(self, report):
        atomic_json(self.path, {'nodeid': report.nodeid, 'phase': report.when, 'outcome': report.outcome,
                                'duration': report.duration, 'observed_at': time.time()})
        print(f'CI_NODE_REPORT {report.nodeid} {report.when} {report.outcome}', flush=True)
        if report.failed:
            # pytest's usual summary is produced only after the entire file.
            # Preserve earlier failures even if a later node stalls forever.
            print(report.longreprtext, flush=True)


def wait_windows_gate():
    value = os.environ.pop('VISION_SCM_START_HANDLE', None)
    if not value:
        return
    import ctypes
    from ctypes import wintypes
    api = ctypes.WinDLL('kernel32', use_last_error=True)
    api.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    api.WaitForSingleObject.restype = wintypes.DWORD
    api.CloseHandle.argtypes = [wintypes.HANDLE]
    api.CloseHandle.restype = wintypes.BOOL
    handle = int(value)
    try:
        if api.WaitForSingleObject(handle, 30000) != 0:
            raise RuntimeError('CI child was not assigned to its owned Windows job')
    finally:
        api.CloseHandle(handle)


def child_main(args):
    # Wait before importing pytest or application modules so no descendant can
    # be created between Popen and assignment to the Windows ownership job.
    wait_windows_gate()
    sys.path.insert(0, str(ROOT))
    import pytest
    return pytest.main([args.selections[0], '-vv', '-rA', '--tb=short', '--durations=20',
                       f'--basetemp={args.basetemp}',
                       '-o', f'faulthandler_timeout={args.diagnostic_timeout}',
                       f'--junitxml={args.junit}'], plugins=[ProgressJournal(args.event_file)])


def spawn_child(command, env, log):
    if os.name == 'nt':
        sys.path.insert(0, str(ROOT))
        from backend.engine.windows_inspection_service import OwnedWindowsChild
        return OwnedWindowsChild(command, cwd=ROOT, env=env, log_path=log)
    with log.open('ab') as writer:
        return subprocess.Popen(command, cwd=ROOT, env=env, stdin=subprocess.DEVNULL,
                                stdout=writer, stderr=subprocess.STDOUT, start_new_session=True)


def close_child(child):
    if os.name == 'nt':
        if child.poll() is None:
            child.terminate_owned()
        child.close()
    else:
        # This process group was created for this retained child. No executable
        # name or unrelated numeric PID is ever used as a Windows stop target.
        try:
            os.killpg(child.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        child.wait(timeout=10)


def run(args):
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    args.junit = args.junit.resolve()
    args.junit.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    for index, selection in enumerate(args.selections):
        folder = output / f'{index:02d}-{Path(selection.split("::", 1)[0]).stem}'
        folder.mkdir(exist_ok=False)
        rows.append({'selection': selection, 'status': 'pending', 'store': str(folder / 'stores'),
                     'log': str((folder / 'console.log').relative_to(output)),
                     'junit': str((folder / 'junit.xml').relative_to(output)),
                     'event_file': str((folder / 'last-event.json').relative_to(output)),
                     'pytest_temp': str((folder / 'pytest-temp').relative_to(output)),
                     'diagnostics': str((folder / 'diagnostics').relative_to(output))})
    summary = {'schema_version': 1, 'status': 'running', 'file_timeout_seconds': args.file_timeout,
               'diagnostic_timeout_seconds': args.diagnostic_timeout, 'selections': rows,
               'scope': 'isolated selected pytest files; hosted platform and skip reasons require review'}

    def persist():
        atomic_json(output / 'summary.json', summary)
        merged_junit(rows, output, args.junit)

    persist()
    active = None
    try:
        for row in rows:
            row['status'] = 'running'
            row['started_at'] = datetime.now(timezone.utc).isoformat()
            persist()
            print(f'CI_SELECTION_START {row["selection"]}', flush=True)
            env = {**os.environ, 'PYTHONUNBUFFERED': '1', 'PYTHONIOENCODING': 'utf-8',
                   'MODU_TEST_DATA_DIR': row['store'], 'PYTHONPATH': str(ROOT),
                   'MODU_PREFLIGHT_TRACE_SECONDS': str(args.diagnostic_timeout)}
            for name in ('VISION_AI_STUDIO_USER_DATA_DIR', 'MODU_FLOW_TEMPLATE_DIR', 'MODU_SPLIT_MANIFEST_DIR',
                         'MODU_THUMBNAIL_CACHE_DIR', 'VISION_RESOURCE_LEASE_DB', 'VISION_SCM_START_HANDLE'):
                env.pop(name, None)
            command = [sys.executable, str(Path(__file__).resolve()), '--child', '--junit', str(output / row['junit']),
                       '--event-file', str(output / row['event_file']), '--diagnostic-timeout', str(args.diagnostic_timeout),
                       '--basetemp', str(output / row['pytest_temp']),
                       row['selection']]
            started = time.monotonic()
            log = output / row['log']
            try:
                active = spawn_child(command, env, log)
                while active.poll() is None and time.monotonic() - started < args.file_timeout:
                    time.sleep(.1)
                code = active.poll()
                row['status'] = 'timed_out' if code is None else 'passed' if code == 0 else 'failed'
                row['returncode'] = code
            except OSError as exc:
                row['status'] = 'spawn_error'
                row['error'] = str(exc)
            finally:
                if active is not None:
                    close_child(active)
                    active = None
                preserve_preflight_logs(output / row['pytest_temp'], output / row['diagnostics'])
                row['seconds'] = round(time.monotonic() - started, 3)
                event = output / row['event_file']
                if event.is_file():
                    row['last_event'] = json.loads(event.read_text(encoding='utf-8'))
                result = output / row['junit']
                if row['status'] in ('passed', 'failed'):
                    try:
                        suites = list(ET.parse(result).getroot().iter('testsuite'))
                        if not suites:
                            raise ValueError('No pytest suites recorded')
                        row['counts'] = {key: sum(int(suite.get(key, '0')) for suite in suites)
                                         for key in ('tests', 'failures', 'errors', 'skipped')}
                        if row['counts']['failures'] or row['counts']['errors']:
                            row['status'] = 'failed'
                    except (OSError, ET.ParseError, ValueError):
                        if row['status'] == 'passed':
                            row['status'] = 'missing_result'
                persist()
            # Each child logs to a file, so inherited grandchild pipes cannot
            # hold this runner open. Publish the complete traceback after each
            # selection, even when another selection has already failed.
            if log.is_file():
                print(log.read_text(encoding='utf-8', errors='replace'), flush=True)
            print(f'CI_SELECTION_END {row["selection"]} {row["status"]} {row["seconds"]}s', flush=True)
        summary['status'] = 'passed' if all(row['status'] == 'passed' for row in rows) else 'failed'
        persist()
        return 0 if summary['status'] == 'passed' else 1
    finally:
        if active is not None:
            close_child(active)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path)
    parser.add_argument('--junit', type=Path, required=True)
    parser.add_argument('--file-timeout', type=float, default=600)
    parser.add_argument('--diagnostic-timeout', type=float, default=120)
    parser.add_argument('--child', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--event-file', type=Path, help=argparse.SUPPRESS)
    parser.add_argument('--basetemp', type=Path, help=argparse.SUPPRESS)
    parser.add_argument('selections', nargs='+')
    args = parser.parse_args()
    if args.file_timeout <= 0 or args.diagnostic_timeout <= 0:
        parser.error('timeouts must be positive')
    if args.child:
        return child_main(args)
    if args.output_dir is None:
        parser.error('--output-dir is required')
    return run(args)


if __name__ == '__main__':
    raise SystemExit(main())
