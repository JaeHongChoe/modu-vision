"""Refuse known credentials before archive publication without echoing their values.

This bounded format check is not a general DLP or arbitrary binary-secret detector.
Only the exact host service record is sanitized; customer/source bytes are never edited.
"""
from __future__ import annotations
import json
import re
import sqlite3
from contextlib import closing
from pathlib import Path

HOST_SERVICE_FIELDS = {'token', 'pid', 'process_created_at', 'process_command_sha256',
                       'native_label', 'native_kind', 'native_registration_path'}
_CREDENTIAL_KEYS = {'password', 'passwd', 'password_hash', 'private_key', 'api_key', 'access_token',
                    'refresh_token', 'secret_key', 'client_secret', 'ssh_key', 'token'}
_CREDENTIAL_NAMES = {'id_rsa', 'id_ed25519', 'id_ecdsa', 'credentials', 'credentials.json'}
_PATTERN = re.compile(rb'-----BEGIN (?:[A-Z]+ )?PRIVATE KEY-----|(?:https?|ssh)://[^\s/:@]+:[^\s/@]+@', re.I)


def _has_credential(value):
    if isinstance(value, dict):
        return any((str(key).lower() in _CREDENTIAL_KEYS and isinstance(item, str) and bool(item.strip()))
                   or _has_credential(item) for key, item in value.items())
    if isinstance(value, list):
        return any(_has_credential(item) for item in value)
    return False


def portable_service(path):
    value = json.loads(Path(path).read_text(encoding='utf-8'))
    if not isinstance(value, dict):
        raise ValueError('Invalid host service record')
    portable = {key: item for key, item in value.items() if key not in HOST_SERVICE_FIELDS}
    portable['pid'] = None
    if _has_credential(portable):
        raise ValueError('Unrecognized credentials remain in host service record')
    return json.dumps(portable, ensure_ascii=False, indent=2).encode('utf-8')


def check_credentials(path, member):
    name = Path(member).name.lower()
    if name in _CREDENTIAL_NAMES or name == '.env' or name.startswith('.env.') or name.endswith(('.key', '.p12', '.pfx')) or '.ssh' in Path(member).parts:
        raise ValueError(f'Credential file cannot be included: {member}')
    # Scan bounded chunks with overlap. The supported patterns have short prefixes;
    # arbitrarily long/encoded credentials are outside this checker, documented explicitly.
    overlap = b''
    with Path(path).open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            combined = overlap + chunk
            if _PATTERN.search(combined):
                raise ValueError(f'Credential content cannot be included: {member}')
            overlap = combined[-4096:]
    if name.endswith('.json'):
        if Path(path).stat().st_size > 32 * 1024 * 1024:
            raise ValueError(f'JSON exceeds credential-check limit: {member}')
        try:
            value = json.loads(Path(path).read_text(encoding='utf-8'))
        except (ValueError, UnicodeDecodeError):
            return  # Existing archive compatibility permits non-JSON customer bytes.
        if _has_credential(value):
            raise ValueError(f'Structured credentials cannot be included: {member}')
    if Path(path).suffix in {'.db', '.sqlite', '.sqlite3'}:
        with Path(path).open('rb') as handle:
            sqlite = handle.read(16) == b'SQLite format 3\x00'
        if sqlite:
            quote = lambda name: '"' + name.replace('"', '""') + '"'
            with closing(sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro', uri=True)) as db:
                for (table,) in db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall():
                    for column in db.execute(f'PRAGMA table_info({quote(table)})').fetchall():
                        if column[1].lower() in _CREDENTIAL_KEYS:
                            identifier = quote(column[1])
                            if db.execute(f"SELECT 1 FROM {quote(table)} WHERE typeof({identifier})='text' AND length(trim({identifier}))>0 LIMIT 1").fetchone():
                                raise ValueError(f'Database credentials cannot be included: {member}')


def sanitize_fleet_database(path):
    """Erase credentials from the owned snapshot, including SQLite free pages."""
    with closing(sqlite3.connect(path)) as db:
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if 'targets' in tables:
            columns = {row[1] for row in db.execute('PRAGMA table_info(targets)')}
            if 'token' not in columns:
                raise ValueError('Invalid fleet credential schema')
            db.execute('PRAGMA secure_delete=ON')
            db.execute("UPDATE targets SET token=''")
            db.commit()
            db.execute('VACUUM')
