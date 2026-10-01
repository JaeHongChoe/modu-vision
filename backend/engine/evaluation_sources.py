"""Bind prepared evaluation evidence to an owned original label-edit image."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path


def _relative(path: Path, root: Path) -> Path | None:
    if '..' in path.parts:
        raise ValueError('Evaluation prepared/source path contains parent traversal')
    try:
        return path.absolute().relative_to(root.absolute())
    except ValueError:
        return None


def _no_symlinks(path: Path, root: Path, kind: str) -> None:
    relative = _relative(path, root)
    if relative is None:
        raise ValueError(f'Evaluation {kind} file is outside its owned root')
    current = root
    if current.is_symlink():
        raise ValueError(f'Evaluation {kind} root is a symlink')
    for part in relative.parts:
        current /= part
        if current.is_symlink():
            raise ValueError(f'Evaluation {kind} path contains a symlink')


def _owned_file(path: Path, root: Path, kind: str) -> Path:
    _no_symlinks(path, root, kind)
    if not path.is_file() or not path.resolve().is_relative_to(root.resolve()):
        raise ValueError(f'Evaluation {kind} must be a file inside its owned root')
    return path.resolve()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def remap_prepared_predictions(predictions, resolved_dataset, bound_source, out_dir):
    """Remap exact prepared paths, keeping evidence and cache IDs unchanged.

    Remote snapshots exclude the private source manifest. Their pixel paths
    match the owned job/dataset manifest by relative path, never by basename.
    """
    dataset = Path(resolved_dataset).absolute()
    source = Path(bound_source).absolute()
    job = Path(out_dir).absolute()
    if _relative(dataset, job) is not None:
        _no_symlinks(dataset, job, 'prepared dataset')
    prepared = job / 'dataset'
    mappings = {}
    roots = [dataset]
    if prepared != dataset:
        roots.append(prepared)
    for root in roots:
        manifest = root / 'source_manifest.json'
        if not manifest.exists() and not manifest.is_symlink():
            continue
        if root == prepared:
            _no_symlinks(root, job, 'prepared dataset')
        manifest = _owned_file(manifest, root, 'prepared manifest')
        rows = json.loads(manifest.read_text(encoding='utf-8'))
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise ValueError('Evaluation prepared source manifest is invalid')
        for row in rows:
            original = row.get('source_image')
            if not original:
                continue
            original = Path(original)
            if not original.is_absolute():
                original = source / original
            original = _owned_file(original, source, 'source')
            for key in ('image', 'prepared_image', 'image_path', 'output_image'):
                if not row.get(key):
                    continue
                image = Path(row[key])
                if not image.is_absolute():
                    image = root / image
                relative = _relative(image, root)
                if relative is None:
                    raise ValueError('Evaluation prepared mapping leaves its dataset')
                image = _owned_file(image, root, 'prepared')
                candidates = mappings.setdefault(relative.as_posix(), [])
                if candidates and any(item[1] != original for item in candidates):
                    raise ValueError('Evaluation prepared source mapping is ambiguous')
                candidates.append((image, original))

    for prediction in predictions:
        prediction['labeling_supported'] = False
        raw = prediction.get('evaluation_file_path') or prediction.get('file_path')
        if not raw:
            continue
        evidence = Path(raw)
        if not evidence.is_absolute():
            evidence = dataset / evidence
        relative = _relative(evidence, dataset)
        if relative is None:
            # Direct evaluation of current source images needs no prepared map.
            if not prediction.get('evaluation_file_path') and _relative(evidence, source) is not None:
                original = _owned_file(evidence, source, 'source')
                prediction.update(file_path=str(original), file_name=original.name,
                                  source_image_id=original.stem, labeling_supported=True)
            continue
        evidence = _owned_file(evidence, dataset, 'prepared evidence')
        candidates = mappings.get(relative.as_posix())
        if not candidates:
            prediction['file_path'] = str(evidence)
            if not prediction.get('evaluation_file_path') and _relative(evidence, source) is not None:
                original = _owned_file(evidence, source, 'source')
                prediction.update(file_name=original.name, source_image_id=original.stem,
                                  labeling_supported=True)
            continue
        evidence_hash = _sha256(evidence)
        if any(_sha256(image) != evidence_hash for image, _ in candidates):
            raise ValueError('Evaluation prepared pixels differ from the evaluated snapshot')
        original = candidates[0][1]
        if prediction.get('evaluation_file_path'):
            cached_original = Path(prediction.get('file_path') or '')
            if not cached_original.is_absolute():
                cached_original = source / cached_original
            if cached_original.absolute() != evidence:
                cached_original = _owned_file(cached_original, source, 'source')
            if cached_original not in (evidence, original):
                raise ValueError('Evaluation cached source mapping is ambiguous')
        prediction.update(evaluation_file_path=str(evidence), file_path=str(original),
                          file_name=original.name, source_image_id=original.stem,
                          labeling_supported=True)
    return predictions
