"""Read-only retained rotation input and an explicit restore fixture.

No framework, model, server, backup API or training is called here. The archive
is synthetic input to the real project restore API, not a backup-creation claim.
Glyph labels describe the original geometric arrow; no recognition accuracy is
assumed after the learned correction changes its orientation.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from zipfile import ZipFile, ZIP_STORED


def require(value, message):
    if not value:
        raise ValueError(message)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def read(file):
    file = Path(file)
    require(file.is_absolute() and file.resolve(strict=True) == file, 'Canonical file required')
    for parent in file.parents:
        require(not parent.is_symlink(), 'Linked ancestor')
    fd = os.open(file, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
    with os.fdopen(fd, 'rb') as handle:
        before = os.fstat(handle.fileno())
        require(stat.S_ISREG(before.st_mode), 'Regular file required')
        raw = handle.read()
        after = os.fstat(handle.fileno())
    identity = lambda value: (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns)
    require(identity(before) == identity(after) == identity(file.stat()), 'Input changed while read')
    return raw


def tree(root):
    root = Path(root)
    require(root.is_absolute() and root.resolve(strict=True) == root and not root.is_symlink(), 'Canonical root required')
    files, directories = {}, []
    for folder, names, members in os.walk(root, followlinks=False):
        names.sort(); members.sort()
        for name in names:
            item = Path(folder) / name
            require(not item.is_symlink(), 'Linked directory')
            directories.append(item.relative_to(root).as_posix())
        for name in members:
            file = Path(folder) / name
            raw = read(file)
            files[file.relative_to(root).as_posix()] = {'bytes': len(raw), 'sha256': sha(raw)}
    return {'directories': sorted(directories), 'files': dict(sorted(files.items()))}


def glyph_rows(source):
    # This is the retained generator's exact geometric transform, not a guessed
    # word absent from the image. Every one of the 16 entire RGB planes is checked.
    from PIL import Image, ImageDraw
    rows, pixel_hashes = [], {}
    glyphs = {0: '↑', 90: '→', 180: '↓', -90: '←'}
    for split, copies in [('train', 2), ('val', 1), ('test', 1)]:
        for angle in (0, 90, 180, -90):
            for copy in range(copies):
                name = f'{split}_{angle}_{copy}.png'
                expected = Image.new('RGB', (32, 32), 'black')
                ImageDraw.Draw(expected).polygon([(16, 2), (5, 25), (16, 19), (24, 25)], fill='white')
                expected = expected.rotate(-angle)
                expected.putpixel((0, 0), (len(rows) + 1, 0, 0))
                with Image.open(Path(source) / name) as opened:
                    actual = opened.convert('RGB')
                    require(actual.size == (32, 32) and actual.tobytes() == expected.tobytes(), 'Original full RGB geometry changed')
                    pixel_hashes[name] = sha(actual.tobytes())
                rows.append({'image': name, 'text': glyphs[angle], 'split': split})
    require(set(Path(source).iterdir()) == {Path(source) / row['image'] for row in rows}, 'Exact flat source16 required')
    return rows, pixel_hashes


def binding(file, digest):
    require(re.fullmatch('[0-9a-f]{64}', digest or '') is not None, 'Exact binding hash required')
    raw = read(Path(file))
    require(sha(raw) == digest, 'Binding changed')
    value = json.loads(raw)
    require(value['schema'] == 'modu-vision.retained-rotation-input/v1', 'Wrong binding')
    require(value['job_id'] == '1c95798117d2439998ff696638561e75'
            and value['project_id'] == '3e81ac07', 'Original identity changed')
    project, source = Path(value['original_project']), Path(value['original_source'])
    require(tree(project) == value['input']['project'] and tree(source) == value['input']['source'], 'Retained original tree changed')
    model = project / 'models' / 'rotation' / value['job_id']
    meta = json.loads(read(model / 'model_meta.json'))
    receipt = json.loads(read(model / 'job_receipt.json'))
    require(receipt['status'] == 'completed' and receipt['task'] == meta['task'] == 'rotation', 'Completed rotation authority required')
    require(receipt['job_id'] == value['job_id'] and receipt['training_provenance'] == meta['training_provenance'], 'Original completed binding differs')
    require(sha(read(model / 'best_model.pt')) == value['checkpoint_sha256'] == receipt['checkpoint_sha256'], 'Original checkpoint changed')
    rows, pixels = glyph_rows(source)
    return value, meta, rows, pixels


def archive(value, meta, output, workspace):
    output = Path(output)
    workspace = Path(workspace)
    require(workspace.is_absolute() and workspace.resolve(strict=True) == workspace and not workspace.is_symlink(), 'Canonical workspace required')
    require(output == workspace / 'logs' / 'retained-rotation-input.zip', 'Exact owned fixture archive required')
    require(output.is_absolute() and output.parent.resolve(strict=True) == output.parent, 'Canonical archive parent required')
    for parent in output.parents:
        require(not parent.is_symlink(), 'Linked archive parent')
    require(not output.exists() and not output.is_symlink(), 'Archive must be new')
    project, source = Path(value['original_project']), Path(value['original_source'])
    require(not output.is_relative_to(project) and not output.is_relative_to(source), 'Original input output forbidden')
    rows, payloads = [], {}
    for key, root in [('project', project), ('source', source)]:
        for relative, record in value['input'][key]['files'].items():
            member = key + '/' + relative
            raw = read(root / relative)
            require(record == {'bytes': len(raw), 'sha256': sha(raw)}, 'Archive input changed')
            payloads[member] = raw
            rows.append({'member': member, 'size': len(raw), 'sha256': sha(raw)})
    fingerprint = meta['training_provenance']['dataset_fingerprint']
    require(re.fullmatch('v1:[0-9a-f]{64}', fingerprint) is not None, 'Original fingerprint required')
    manifest = {'format': 'modu-project-backup-v1', 'project_id': value['project_id'],
                'original_project_dir': str(project), 'original_source_dir': str(source),
                'source_dataset_fingerprint': fingerprint, 'source_active_labelset_id': 'default',
                'source_dataset_fingerprints_by_labelset': {'default': fingerprint},
                'created_at': '2026-10-05T18:29:05Z', 'files': rows}
    with ZipFile(output, 'x', compression=ZIP_STORED) as zipped:
        for member, raw in sorted(payloads.items()):
            zipped.writestr(member, raw)
        zipped.writestr('backup-manifest.json', json.dumps(manifest, ensure_ascii=False, sort_keys=True))
    with ZipFile(output) as zipped:
        require(set(zipped.namelist()) == set(payloads) | {'backup-manifest.json'}, 'Archive inventory differs')
        require(all(zipped.read(member) == raw for member, raw in payloads.items()), 'Archive full bytes differ')
    require(tree(project) == value['input']['project'] and tree(source) == value['input']['source'], 'Original changed during fixture archive')
    raw = read(output)
    return {'path': str(output), 'bytes': len(raw), 'sha256': sha(raw), 'manifest': manifest,
            'synthetic_restore_input': True, 'product_backup_creation': False}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['check', 'archive'])
    parser.add_argument('--binding', required=True)
    parser.add_argument('--binding-sha256', required=True)
    parser.add_argument('--output')
    parser.add_argument('--workspace')
    args = parser.parse_args()
    value, meta, rows, pixels = binding(args.binding, args.binding_sha256)
    result = {'binding_sha256': args.binding_sha256, 'job_id': value['job_id'], 'checkpoint_sha256': value['checkpoint_sha256'],
              'original_project': value['original_project'], 'original_source': value['original_source'],
              'original_tree': value['input'], 'glyph_rows': rows, 'full_rgb_sha256': pixels,
              'checkpoint_loaded': False, 'model_executed': False, 'human_quality_approved': False}
    if args.action == 'archive':
        require(args.output is not None and args.workspace is not None, 'New owned archive output required')
        result['archive'] = archive(value, meta, args.output, args.workspace)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
