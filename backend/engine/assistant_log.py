"""The built-in assistant's project-local run log: ``<project_dir>/assistant/runs.jsonl`` (§4.6).

One JSON object a line: the time, the person who ran the assistant, the tool and its tier, a short argument summary and
the sha256 of the full arguments, whether the person confirmed it, the result (ok, refused, error, cancelled) with a
short error code, the run id that the backend writes of a confirmed tool also carry (``via: "assistant"``), and an
optional link to the record the write produced (an audit action, a job or batch id).

Keys, image pixels and full paths never belong here. The renderer only sends file names and counts; this module refuses
a summary that still looks like an absolute path, a URL, an e-mail address or a credential. Clearing the log moves the
file into ``assistant/archive/`` (and so does a log that grew past its size bound); nothing is ever deleted.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import secrets
import time

from backend.engine.dataset_metadata import _file_lock, _now

SCHEMA_VERSION = 1
LOG_DIRECTORY = 'assistant'
LOG_FILE = 'runs.jsonl'
ARCHIVE_DIRECTORY = 'archive'
MAX_LOG_BYTES = 4 * 1024 * 1024
MAX_LINE_BYTES = 4096
TIERS = ('read', 'navigate', 'write', 'forbidden', 'conversation')
RESULTS = ('ok', 'refused', 'error', 'cancelled')
PROVIDERS = ('openai-compatible', 'anthropic')

_RUN_ID = re.compile(r'run_[a-f0-9]{24}')
_TOOL = re.compile(r'[a-z][a-z0-9_]{1,63}')
_CODE = re.compile(r'[a-z0-9_.-]{1,64}')
_LINK = re.compile(r'[A-Za-z0-9_.:-]{1,120}')
_SHA256 = re.compile(r'[a-f0-9]{64}')
# An absolute POSIX, home, Windows drive or UNC path; a URL of any scheme; an e-mail address; a bearer token, a
# provider key or any long unbroken token. A summary carries names and counts, so none of these is ever needed.
_UNSAFE = (
    ('path', re.compile(r'(^|[\s"\'(=:,])(/[^\s/]+/|~[/\\]|[A-Za-z]:[\\/]|\\\\[^\s\\]+\\)')),
    ('url', re.compile(r'\b[a-z][a-z0-9+.-]*://', re.IGNORECASE)),
    ('email', re.compile(r'[^\s@]+@[^\s@]+\.[A-Za-z]{2,}')),
    ('credential', re.compile(r'(\bsk-[A-Za-z0-9_-]{8,}|\bBearer\s+\S+|[A-Za-z0-9+/=_-]{40,})')),
)


class UnsafeRunEntry(ValueError):
    pass


def _check_text(name: str, value, limit: int) -> str:
    if not isinstance(value, str) or len(value) > limit or any(ord(c) < 32 for c in value):
        raise UnsafeRunEntry(f'{name}: 길이 {limit}자 이하의 한 줄 문자열이어야 합니다.')
    for kind, pattern in _UNSAFE:
        if pattern.search(value):
            raise UnsafeRunEntry(f'{name}: 실행 기록에는 {kind}를 남기지 않습니다. 파일 이름과 수만 적으세요.')
    return value


def validated_entry(entry: dict) -> dict:
    """The fields a caller may set, checked one by one; anything else is refused rather than stored."""
    allowed = {'run_id', 'tool', 'tier', 'summary', 'args_sha256', 'confirmed', 'result', 'error_code', 'provider', 'link'}
    if not isinstance(entry, dict) or set(entry) - allowed:
        raise UnsafeRunEntry('실행 기록 항목에 알 수 없는 필드가 있습니다.')
    if not isinstance(entry.get('run_id'), str) or not _RUN_ID.fullmatch(entry['run_id']):
        raise UnsafeRunEntry('run_id 형식이 올바르지 않습니다.')
    if not isinstance(entry.get('tool'), str) or not _TOOL.fullmatch(entry['tool']):
        raise UnsafeRunEntry('도구 이름 형식이 올바르지 않습니다.')
    if entry.get('tier') not in TIERS or entry.get('result') not in RESULTS:
        raise UnsafeRunEntry('도구 등급이나 결과 값이 올바르지 않습니다.')
    if not isinstance(entry.get('args_sha256'), str) or not _SHA256.fullmatch(entry['args_sha256']):
        raise UnsafeRunEntry('인자 해시는 sha256 16진수여야 합니다.')
    if type(entry.get('confirmed')) is not bool:
        raise UnsafeRunEntry('confirmed는 참/거짓이어야 합니다.')
    clean = {key: entry[key] for key in ('run_id', 'tool', 'tier', 'args_sha256', 'confirmed', 'result')}
    clean['summary'] = _check_text('summary', entry.get('summary', ''), 300)
    for key, pattern in (('error_code', _CODE), ('link', _LINK)):
        value = entry.get(key)
        if value is not None:
            if not isinstance(value, str) or not pattern.fullmatch(value):
                raise UnsafeRunEntry(f'{key} 형식이 올바르지 않습니다.')
            clean[key] = value
    if entry.get('provider') is not None:
        if entry['provider'] not in PROVIDERS:
            raise UnsafeRunEntry('공급자 값이 올바르지 않습니다.')
        clean['provider'] = entry['provider']
    return clean


def _directory(project_dir: Path | str) -> Path:
    root = Path(project_dir).resolve()
    directory = root / LOG_DIRECTORY
    for path in (directory, directory / LOG_FILE, directory / ARCHIVE_DIRECTORY):
        if path.is_symlink():
            raise UnsafeRunEntry('도우미 실행 기록 위치가 링크입니다. 프로젝트 폴더를 확인하세요.')
    return directory


def _archive_locked(directory: Path) -> str | None:
    """Move the current log into the archive folder under a fresh name; the caller holds the log lock."""
    source = directory / LOG_FILE
    if not source.is_file():
        return None
    archive = directory / ARCHIVE_DIRECTORY
    archive.mkdir(exist_ok=True)
    name = f"runs-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}-{secrets.token_hex(3)}.jsonl"
    os.replace(source, archive / name)
    return f'{ARCHIVE_DIRECTORY}/{name}'


def append_run(project_dir: Path | str, entry: dict, *, actor: str) -> dict:
    record = {'schema_version': SCHEMA_VERSION, 'at': _now(), 'actor': str(actor)[:100], **validated_entry(entry)}
    line = (json.dumps(record, ensure_ascii=False, sort_keys=True) + '\n').encode('utf-8')
    if len(line) > MAX_LINE_BYTES:
        raise UnsafeRunEntry('실행 기록 한 줄이 너무 깁니다.')
    directory = _directory(project_dir)
    directory.mkdir(parents=True, exist_ok=True)
    with _file_lock(directory / '.runs.lock'):
        path = directory / LOG_FILE
        if path.is_file() and path.stat().st_size + len(line) > MAX_LOG_BYTES:
            _archive_locked(directory)
        descriptor = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            os.write(descriptor, line)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    return record


def list_runs(project_dir: Path | str, limit: int = 200) -> dict:
    directory = _directory(project_dir)
    path = directory / LOG_FILE
    runs: list[dict] = []
    if path.is_file():
        with path.open('rb') as handle:
            content = handle.read(MAX_LOG_BYTES + MAX_LINE_BYTES)
        for raw in content.splitlines():
            try:
                value = json.loads(raw)
            except (ValueError, UnicodeDecodeError):
                continue  # a torn last line from a crash is skipped, not repaired
            if isinstance(value, dict) and value.get('schema_version') == SCHEMA_VERSION:
                runs.append(value)
    archive = directory / ARCHIVE_DIRECTORY
    archives = sum(1 for item in archive.glob('runs-*.jsonl') if item.is_file()) if archive.is_dir() else 0
    return {'runs': runs[-max(1, min(int(limit), 1000)):], 'total': len(runs), 'archives': archives}


def archive_runs(project_dir: Path | str) -> dict:
    """The person's "clear": the log moves into the archive folder and a new one starts on the next run."""
    directory = _directory(project_dir)
    if not directory.is_dir():
        return {'archived': None, 'count': 0}
    with _file_lock(directory / '.runs.lock'):
        count = list_runs(project_dir, 1)['total'] if (directory / LOG_FILE).is_file() else 0
        return {'archived': _archive_locked(directory), 'count': count}
