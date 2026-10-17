"""Derive one changed controlled origin; never write a report or run inference."""
import base64
import copy
import hashlib
import json
import os
from pathlib import Path
import stat
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from backend.engine.evaluation_history import canonical


def derive(root, report, expected_sha256):
    root, report = Path(root), Path(report)
    if (not root.is_absolute() or root.resolve() != root or report.resolve() != report
            or not report.is_relative_to(root) or report.parent.name != 'evaluations'
            or report.parent.parent.name != 'reports' or report.suffix != '.json'):
        raise ValueError('Changed origin requires its exact owned fixture report')
    def identity(value):
        return (value.st_dev, value.st_ino, value.st_mode, value.st_nlink,
                value.st_size, value.st_mtime_ns, value.st_ctime_ns, value.st_uid, value.st_gid)
    before = report.lstat()
    if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > 1024 * 1024:
        raise ValueError('Controlled origin file identity differs')
    fd = os.open(report, os.O_RDONLY | os.O_NOFOLLOW)
    first = None
    raw = None
    try:
        if identity(os.fstat(fd)) != identity(before):
            raise ValueError('Controlled origin opened file differs')
        with os.fdopen(fd, 'rb', closefd=False) as reader:
            raw = reader.read(1024 * 1024 + 1)
        if identity(os.fstat(fd)) != identity(before) or identity(report.lstat()) != identity(before):
            raise ValueError('Controlled origin changed while reading')
    except BaseException as error:
        first = error
    finally:
        try:
            os.close(fd)
        except BaseException as error:
            if first is None:
                first = error
            else:
                first.add_note('Original fixture descriptor close failed: ' + str(error))
    if first is not None:
        raise first
    if raw is None:
        raise ValueError('Original controlled origin bytes are unavailable')
    if hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise ValueError('Original controlled origin hash differs')
    original = json.loads(raw)
    evidence = {key: value for key, value in original.items() if key != 'evidence_sha256'}
    if (original['evaluation_id'] != report.stem
            or original['binding'].get('fixture_kind') != 'controlled_reports_no_model_inference'
            or original['result'].get('fixture_kind') != 'controlled_reports_no_model_inference'
            or original['evidence_sha256'] != hashlib.sha256(canonical(evidence)).hexdigest()
            or 'fixture_origin_revision' in original['binding']):
        raise ValueError('Only the exact valid controlled saved origin can change')
    changed = copy.deepcopy(original)
    changed['binding']['fixture_origin_revision'] = 2
    evidence = {key: value for key, value in changed.items() if key != 'evidence_sha256'}
    changed['evidence_sha256'] = hashlib.sha256(canonical(evidence)).hexdigest()
    body = canonical(changed)
    restored = copy.deepcopy(changed)
    del restored['binding']['fixture_origin_revision']
    restored['evidence_sha256'] = original['evidence_sha256']
    if restored != original or body == raw:
        raise ValueError('Controlled origin derivative changed unrelated evidence')
    return {'body_base64': base64.b64encode(body).decode('ascii'),
            'original_file_sha256': expected_sha256,
            'changed_file_sha256': hashlib.sha256(body).hexdigest(),
            'original_evidence_sha256': original['evidence_sha256'],
            'changed_evidence_sha256': changed['evidence_sha256'],
            'controlled_origin_only_not_model_inference': True,
            'files_written': 0}


if __name__ == '__main__':
    print(json.dumps(derive(*sys.argv[1:])))
