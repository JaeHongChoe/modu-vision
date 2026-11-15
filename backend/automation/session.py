"""Explicit CLI-owned bearer files only. Never discovers an app credential store."""
import getpass
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import warnings

from backend.automation.client import CommandError, bytes_body, checked_output, checked_url, clean_header, strict_json


def read_session(filename, url):
    try:
        path = Path(filename).expanduser().absolute()
        before = path.lstat()
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > 16384:
            raise ValueError
        descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
        with os.fdopen(descriptor, 'rb') as handle:
            held = os.fstat(handle.fileno())
            if (before.st_ino, before.st_dev) != (held.st_ino, held.st_dev) or not stat.S_ISREG(held.st_mode): raise ValueError
            if os.name == 'posix' and (held.st_uid != os.geteuid() or held.st_mode & 0o077): raise ValueError
            value = strict_json(handle.read(16385).decode('utf-8'))
        if not isinstance(value, dict) or value.get('schema') != 'StudioCLISession/v1': raise ValueError
        token = clean_header(value.get('token'), 'Session credential')
    except (OSError, RuntimeError, ValueError, UnicodeError):
        raise CommandError('unsafe_session_file', 'Use an explicit private regular CLI session file (owner-only permissions on POSIX), without symlinks') from None
    if checked_url(value.get('url')) != checked_url(url):
        raise CommandError('session_origin_mismatch', 'CLI session belongs to a different server origin; credentials were not sent')
    return token


def password_input(stdin=False):
    if stdin:
        value = sys.stdin.readline(4097)
        if len(value) > 4096: raise CommandError('invalid_arguments', 'Password input exceeds the supported length')
        value = value.removesuffix('\n').removesuffix('\r')
    else:
        try:
            if not sys.stdin.isatty(): raise ValueError
            with warnings.catch_warnings():
                warnings.simplefilter('error', getpass.GetPassWarning)
                value = getpass.getpass('Local account password: ')
        except (getpass.GetPassWarning, ValueError, EOFError):
            raise CommandError('password_input_required', 'Use a hidden interactive terminal, or explicitly pipe a password with --password-stdin') from None
    if not value: raise CommandError('invalid_arguments', 'A nonempty account password is required')
    return value


def login(client, *, username, filename, stdin=False):
    output = checked_output(filename)
    if output is None: raise CommandError('session_file_required', 'Explicit login requires --session-file for the new private bearer file')
    if not username or len(username) > 128: raise CommandError('invalid_arguments', 'Provide a local account username')
    password = password_input(stdin)
    client.secrets = (*client.secrets, password)
    value = client.request('POST', '/api/accounts/login', effect='execute', _login_result=True,
        body=bytes_body(json.dumps({'username': username, 'password': password, 'transport': 'bearer'}, ensure_ascii=False).encode(), 'application/json'))
    if not isinstance(value, dict) or not isinstance(value.get('token'), str):
        raise CommandError('invalid_login_response', 'Account server did not return a bearer session', exit_code=4, outcome='unconfirmed')
    token = clean_header(value['token'], 'Session credential')
    client.secrets = (*client.secrets, token)
    temporary, saved = None, False
    try:
        descriptor, temporary = tempfile.mkstemp(prefix='.modu-cli-session-', dir=output.parent)
        with os.fdopen(descriptor, 'w', encoding='utf-8') as handle:
            json.dump({'schema': 'StudioCLISession/v1', 'url': client.url, 'token': token, 'expires_at': value.get('expires_at')}, handle, ensure_ascii=False)
            handle.flush(); os.fsync(handle.fileno())
        try:
            os.link(temporary, output)
            saved = True
        except FileExistsError:
            raise CommandError('output_exists', 'Session file appeared while logging in; it was preserved', exit_code=4,
                outcome='responded', detail={'account_login_succeeded': True, 'session_saved': False}) from None
    except OSError:
        raise CommandError('session_persistence_failed', 'Account login succeeded but the private CLI session file could not be saved', exit_code=4,
            outcome='responded', detail={'account_login_succeeded': True, 'session_saved': saved}) from None
    finally:
        if temporary is not None:
            try: os.unlink(temporary)
            except OSError:
                raise CommandError('session_persistence_failed', 'Account login succeeded; private temporary-file cleanup could not be confirmed', exit_code=4,
                    outcome='responded', detail={'account_login_succeeded': True, 'session_saved': saved, 'temporary_cleanup_failed': True}) from None
    return {'logged_in': True, 'session_file': str(output), 'expires_at': value.get('expires_at'),
            'native_host_approved': False}
