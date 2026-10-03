"""How every owned child process is started apart from this one (standard library only).

Packaged runtimes copy backend/engine without backend/remote, so this module imports nothing from the app.
"""
from __future__ import annotations

import os
import subprocess


def session_isolation():
    """Popen options that keep an owned child out of this process's session, as start_new_session does on POSIX.

    Windows ignores start_new_session: there the child gets its own process group and a console without a window, so
    Ctrl+C in, or closing, the console this backend was started from never reaches it, and no window opens in the
    desktop app.
    """
    if os.name == 'nt':
        return {'creationflags': subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW}
    return {'start_new_session': True}
