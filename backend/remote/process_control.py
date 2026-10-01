"""Linux process ownership proof used for bounded remote cancellation.

The worker creates a private session. PID reuse and unrelated processes fail
closed; a missing leader is reconciled against marked children in that session.
"""
from __future__ import annotations

import json
import os
import signal
import sys
from pathlib import Path


def _entry(proc_root: Path, pid: int, *, details: bool = True):
    directory = proc_root / str(pid)
    try:
        fields = (directory / 'stat').read_text().rsplit(')', 1)[1].split()
        entry = {'pid': pid, 'state': fields[0], 'group': int(fields[2]),
                 'session': int(fields[3]), 'start_ticks': fields[19]}
        if details:
            entry.update(cmdline=(directory / 'cmdline').read_bytes().split(b'\0'),
                         environment=(directory / 'environ').read_bytes().split(b'\0'))
        return entry
    except FileNotFoundError:
        return None


def owned_members(run_dir: Path, pid: int, proc_root: Path = Path('/proc'), expected_token: str | None = None):
    """Return proven live members, an empty list for exit, None for uncertainty."""
    try:
        identity = json.loads((run_dir / 'worker_identity.json').read_text())
        if expected_token is not None and identity['token'] != expected_token:
            return None
        if identity['pid'] != pid or identity['group'] != pid or identity['session'] != pid:
            return None
        token = ('MODU_VISION_WORKER_TOKEN=' + identity['token']).encode()
        leader = _entry(proc_root, pid)
        if leader and (leader['start_ticks'] != identity['start_ticks'] or
                       (leader['state'] != 'Z' and (b'backend.remote.worker' not in leader['cmdline'] or
                       str(run_dir / 'spec.json').encode() not in leader['cmdline']))):
            return None
        members = []
        for directory in proc_root.iterdir():
            if not directory.name.isdecimal():
                continue
            entry = _entry(proc_root, int(directory.name), details=False)
            if not entry or entry['state'] == 'Z' or entry['group'] != pid or entry['session'] != pid:
                continue
            entry = _entry(proc_root, entry['pid'])
            if entry is None:
                continue
            if entry['pid'] != pid and token not in entry['environment']:
                return None
            members.append(entry)
        return members
    except (OSError, ValueError, KeyError, IndexError, TypeError):
        return None


def control_owned_worker(run_dir: Path, pid: int, action: str, expected_token: str | None = None) -> int:
    if not (run_dir / 'worker_identity.json').is_file():
        return 4
    members = owned_members(run_dir, pid, expected_token=expected_token)
    if members is None:
        return 3
    if action == 'probe':
        return 0 if members else 1
    if action not in {'terminate', 'kill'} or not hasattr(os, 'pidfd_open') or not hasattr(signal, 'pidfd_send_signal'):
        return 3
    descriptors = []
    try:
        # Open all identities before signaling; every send uses a pidfd so an
        # exiting worker cannot cause a signal to hit a reused numeric PID.
        for member in sorted(members, key=lambda row: row['pid'] == pid):
            try:
                descriptor = os.pidfd_open(member['pid'])
            except ProcessLookupError:
                continue
            descriptors.append(descriptor)
            current = _entry(Path('/proc'), member['pid'])
            if current is None:
                continue
            if current['start_ticks'] != member['start_ticks']:
                return 3
        for descriptor in descriptors:
            try:
                signal.pidfd_send_signal(descriptor, signal.SIGKILL if action == 'kill' else signal.SIGTERM)
            except ProcessLookupError:
                pass
        return 0
    except OSError:
        return 3
    finally:
        for descriptor in descriptors:
            os.close(descriptor)


if __name__ == '__main__':
    sys.exit(control_owned_worker(Path(sys.argv[1]), int(sys.argv[2]), sys.argv[3], sys.argv[4] if len(sys.argv) > 4 else None))
