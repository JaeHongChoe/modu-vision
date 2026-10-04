"""Read-only descendant/artifact observer for one CI-owned packaged app.

Never exports argv, environment, headers or file contents. Stops via owned stdin.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import threading
import time
import psutil


def digest(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as source:
        for block in iter(lambda: source.read(1024 * 1024), b''):
            value.update(block)
    return value.hexdigest()


def classify(argv, executable):
    if Path(executable).name.lower() != 'vision_ai_backend.exe':
        return None
    return 'preflight' if '-m' in argv and 'backend.engine.worker_preflight' in argv else 'backend'


def observe(root_pid, user_data, output):
    stop = threading.Event()
    threading.Thread(target=lambda: (os.read(0, 1), stop.set()), daemon=True).start()
    processes, artifacts, cache = {}, {}, {}
    root = psutil.Process(root_pid)
    runs = Path(user_data)
    while not stop.is_set():
        try:
            children = root.children(recursive=True)
        except psutil.Error:
            children = []
        for child in children:
            try:
                executable = child.exe()
                kind = classify(child.cmdline(), executable)
                if not kind:
                    continue
                key = (child.pid, child.create_time())
                if executable not in cache:
                    cache[executable] = digest(executable)
                row = processes.setdefault(key, {'pid': key[0], 'create_time': key[1], 'kind': kind,
                                                 'executable': executable, 'sha256': cache[executable],
                                                 'first_seen': time.time()})
                row['last_seen'] = time.time()
            except (psutil.Error, OSError):
                continue
        # The production preflight removes its own temporary run after completion.
        # Observe only its known input/model/manifest identities while they exist.
        for path in runs.rglob('*'):
            if not path.is_file() or path.is_symlink() or 'worker_preflight_runs' not in path.parts:
                continue
            if path.name not in {'best_model.pt', 'NG_test_0.png', 'manifest.json'}:
                continue
            try:
                before = path.stat()
                sha = digest(path)
                after = path.stat()
                if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                    continue
                relative = path.relative_to(runs).as_posix()
                artifacts[relative] = {'path': relative, 'bytes': after.st_size, 'sha256': sha,
                                       'observed_at': time.time()}
            except OSError:
                continue
        Path(output).write_text(json.dumps({'processes': list(processes.values()),
                                          'artifacts': list(artifacts.values())}), encoding='utf-8')
        stop.wait(.1)
    # Read-only identity check, never kill by PID. Owned app/child handles do cleanup.
    remaining = []
    for (pid, created), row in processes.items():
        try:
            process = psutil.Process(pid)
            if process.create_time() == created and process.is_running():
                remaining.append({'pid': pid, 'kind': row['kind']})
        except psutil.Error:
            pass
    Path(output).write_text(json.dumps({'processes': list(processes.values()),
                                      'artifacts': list(artifacts.values()),
                                      'remaining_processes': remaining}), encoding='utf-8')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--pid', type=int, required=True)
    parser.add_argument('--user-data', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    observe(args.pid, args.user_data, args.output)
