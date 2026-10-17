"""Read-only retained custody and declared restore input; no ML execution.

Only archive writes to the exact fresh workspace/logs path. Model metadata
relocation is performed by the real authenticated restore API, never here.
"""
import argparse, base64, hashlib, io, json, os, stat, zipfile
from pathlib import Path

def require(value, message):
    if not value: raise ValueError(message)

def raw7(s):
    return [s.st_dev, s.st_ino, s.st_mode, s.st_nlink, s.st_size, s.st_mtime_ns, s.st_ctime_ns]

def read(path):
    p = Path(path)
    require(p.is_absolute() and p.resolve(strict=True) == p and not any(a.is_symlink() for a in [p, *p.parents]), 'Canonical original/input required')
    with os.fdopen(os.open(p, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0)), 'rb') as f:
        before = os.fstat(f.fileno()); require(stat.S_ISREG(before.st_mode), 'Regular original/input required')
        body = f.read(); after = os.fstat(f.fileno())
    require(raw7(before) == raw7(after) == raw7(p.stat()), 'Original/input changed during full read')
    return body, {'path': str(p), 'sha256': hashlib.sha256(body).hexdigest(), 'size': len(body), 'identity': raw7(before)}

def exact(row):
    body, current = read(row['path']); require(current == row, 'Original/input full raw7/SHA changed: ' + row['path']); return body

def source_tree(root):
    root = Path(root); require(root.resolve(strict=True) == root and not root.is_symlink(), 'Canonical source tree required')
    names, rows = [], {}
    for p in sorted(root.rglob('*')):
        require(not p.is_symlink(), 'Linked source member refused')
        rel = p.relative_to(root).as_posix()
        if p.is_dir(): names.append(rel)
        else: rows[rel] = read(p)[1]
    return names, rows

def original_check(c):
    for name, root in [('project', c['original_project']), ('source', c['original_source'])]:
        names, rows = source_tree(root); snapshot = c['input_snapshot'][name]
        require(rows == snapshot['files'] and set(names) == set(snapshot['directories']) - {'.'}, 'Whole original namespace or raw7/SHA changed')
        for rel, expected in snapshot['directories'].items():
            p = Path(root) / rel
            require(raw7(p.lstat()) == expected['raw7'] and sorted(x.name for x in p.iterdir()) == expected['children'], 'Original directory identity/children changed')
    receipt = json.loads(exact(c['original_job_receipt'])); meta = json.loads(exact(c['original_model_metadata']))
    require(receipt['status'] == 'completed' and receipt['task'] == meta['task'] == c['control'] and receipt['job_id'] == c['job_id'], 'Completed original task/job receipt required')
    require(receipt['training_provenance'] == meta['training_provenance'], 'Original training provenance mismatch')
    require(receipt['checkpoint_sha256'] == meta['checkpoint_sha256'] == c['original_checkpoint']['sha256'], 'Original checkpoint receipt/hash mismatch')
    require(receipt['source_dataset_path'] == c['original_source'] and receipt['dataset_fingerprint'] == c['original_fingerprint'], 'Original source/fingerprint authority differs')
    from PIL import Image
    with Image.open(io.BytesIO(exact(c['selected_image']))) as image:
        rgb = image.convert('RGB'); require(list(rgb.size) == c['selected_image_size'] and hashlib.sha256(rgb.tobytes()).hexdigest() == c['selected_image_RGB_SHA'], 'Original whole RGB changed')
    return {'control': c['control'], 'job_id': c['job_id'], 'whole_original_raw7_SHA_namespace_unchanged': True, 'checkpoint_sha256': c['original_checkpoint']['sha256'], 'image_size': c['selected_image_size'], 'image_RGB_SHA': c['selected_image_RGB_SHA']}

def load(args):
    b, _ = read(args.binding); require(hashlib.sha256(b).hexdigest() == args.binding_sha256, 'Root binding SHA differs')
    grant = json.loads(b)
    require(grant.get('ready') is True and grant.get('Root_sole_actual_executor') is True and grant.get('current_model_engine_actual_review_passed') is True, 'Actual source/engine review and Root admission required')
    require(grant.get('training_fit_download_adoption_deployment_permitted') is False and grant.get('device') == 'cpu', 'Nontraining CPU-only admission required')
    plan = json.loads(exact(grant['source_plan']))
    require(plan['schema'] == 'modu-vision.retained-class-patch-Studio-source-plan/v1' and plan['ready'] is False, 'Original immutable source plan required')
    c = next(c for c in plan['controls'] if c['control'] == args.control)
    for p in plan['already_executed_GPU_engine_control']: exact(p)
    genuine = json.loads(exact(plan['already_executed_GPU_engine_control'][0]))
    require(genuine['session_id'] == 72119 and genuine['exit_code'] == 0 and genuine['GPU_engine_standalone_production_parity_pass'] is True, 'Original already-executed model control differs')
    original_check(c)
    return grant, c

def owned(args, c):
    w = Path(args.workspace)
    require(w.is_absolute() and w.resolve(strict=True) == w and not any(p.is_symlink() for p in [w, *w.parents]), 'Canonical fresh owned workspace required')
    require(not any(w.is_relative_to(Path(c[k])) for k in ('original_project', 'original_source')), 'Original output prohibited')
    return w

def archive(args, c):
    w = owned(args, c); output = w / 'logs' / ('retained-' + c['control'] + '-restore-input.zip')
    require(output.parent.resolve(strict=True) == output.parent and not output.exists(), 'Exact fresh owned archive required')
    rows = []
    with zipfile.ZipFile(output, 'x', compression=zipfile.ZIP_STORED) as z:
        for member, p in c['archive_members'].items():
            require(member.startswith(('project/', 'source/')) and '..' not in Path(member).parts, 'Declared archive member required')
            body = exact(p); z.writestr(member, body); rows.append({'member': member, 'size': len(body), 'sha256': p['sha256']})
        manifest = {'format': 'modu-project-backup-v1', 'project_id': c['original_project_id'], 'original_project_dir': c['original_project'], 'original_source_dir': c['original_source'], 'source_dataset_fingerprint': c['original_fingerprint'], 'source_active_labelset_id': 'default', 'source_dataset_fingerprints_by_labelset': {'default': c['original_fingerprint']}, 'created_at': '2026-10-14T00:00:00Z', 'files': rows}
        z.writestr('backup-manifest.json', json.dumps(manifest, sort_keys=True))
    original_check(c)
    return {'archive': read(output)[1], 'member_count': len(rows), 'declared_synthetic_restore_input': True, 'product_backup_creation_claim': False, 'original_checkpoint_source_bytes_exact': True}

def restored(args, c):
    w = owned(args, c); project = json.loads(read(args.project)[0]); target = Path(project['project_dir'])
    require(target.resolve(strict=True) == target and target.is_relative_to(w) and project['id'] == c['original_project_id'], 'Fresh authenticated restored project required')
    source = Path(project['source_dataset_dir']); require(source == target / 'dataset/restored_source' and source.resolve(strict=True) == source, 'Product restored source identity required')
    _, rows = source_tree(source)
    require(set(rows) == set(c['input_snapshot']['source']['files']), 'Restored complete source names differ')
    for rel, p in rows.items():
        before = c['input_snapshot']['source']['files'][rel]
        require((p['sha256'], p['size']) == (before['sha256'], before['size']) and p['identity'][1] != before['identity'][1] and p['identity'][3] == 1, 'Restored source bytes differ or alias original')
    model = target / 'models' / c['job_id']; checkpoint = read(model / 'best_model.pt')[1]
    require(checkpoint['sha256'] == c['original_checkpoint']['sha256'] and checkpoint['identity'][1] != c['original_checkpoint']['identity'][1] and checkpoint['identity'][3] == 1, 'Owned checkpoint must be exact independent copy')
    meta_b, meta_pin = read(model / 'model_meta.json'); receipt_b, receipt_pin = read(model / 'job_receipt.json'); meta, receipt = json.loads(meta_b), json.loads(receipt_b)
    require(receipt['status'] == 'completed' and receipt['task'] == meta['task'] == c['control'] and receipt['job_id'] == c['job_id'], 'Restored completed model identity differs')
    require(receipt['source_dataset_path'] == str(source) and receipt['training_provenance'] == meta['training_provenance'], 'Real restore source/provenance relocation missing')
    require(receipt['checkpoint_sha256'] == meta['checkpoint_sha256'] == checkpoint['sha256'], 'Relocation changed checkpoint authority')
    # Full relocated JSON is saved by the test. Its authenticity is established
    # by the exact original restore API Request/Response and production catalog,
    # never by a fixture rewrite of metadata, source or fingerprint.
    image = source / c['selected_image_relative']; require(read(image)[1]['sha256'] == c['selected_image']['sha256'], 'Restored selected image differs')
    return {'control': c['control'], 'job_id': c['job_id'], 'project': project, 'source': str(source), 'image': str(image), 'size': c['selected_image_size'], 'image_RGB_SHA': c['selected_image_RGB_SHA'], 'checkpoint': checkpoint, 'relocated_metadata': meta_pin, 'relocated_receipt': receipt_pin, 'original_metadata': c['original_model_metadata'], 'original_receipt': c['original_job_receipt'], 'original_pipeline': json.loads(exact(c['original_pipeline'])), 'metadata_written_by_helper': False, 'original_check': original_check(c)}

def rgb_result(args, c):
    from PIL import Image
    w = owned(args, c); file = Path(args.result); require(file.is_relative_to(w / 'logs'), 'Owned full result receipt required')
    result = json.loads(read(file)[0]); project = json.loads(read(args.project)[0]); source = Path(project['source_dataset_dir']); image = source / c['selected_image_relative']
    with Image.open(io.BytesIO(read(image)[0])) as im: expected = im.convert('RGB')
    require(result['status'] == 'success' and result['image_path'] == str(image) and result['inspected_image_size'] == list(expected.size), 'Actual inspection/input identity differs')
    require(result['execution_target'] == 'local' and result['execution_device'] == 'cpu', 'Actual CPU route execution required')
    crops = []
    for index, crop in enumerate(result['crops']):
        uri = crop['crop_thumbnail']
        require(isinstance(uri, str) and uri.startswith('data:image/png;base64,'), 'Original actual crop PNG required')
        box = crop['bbox']
        require(isinstance(box, list) and len(box) == 4 and all(type(v) is int for v in box) and 0 <= box[0] < box[2] <= expected.width and 0 <= box[1] < box[3] <= expected.height, 'Original integer source crop bounds required')
        body = base64.b64decode(uri.split(',', 1)[1], validate=True)
        with Image.open(io.BytesIO(body)) as im: actual = im.convert('RGB')
        target = expected.crop(tuple(box))
        require(actual.size == target.size and actual.tobytes() == target.tobytes(), 'Original result crop full RGB differs from source coordinates')
        crops.append({'index': index, 'bbox': crop['bbox'], 'size': list(actual.size), 'PNG_SHA': hashlib.sha256(body).hexdigest(), 'RGB_SHA': hashlib.sha256(actual.tobytes()).hexdigest()})
    require(len(crops) == (1 if c['control'] == 'classification' else 6), 'Exact retained full/patch crop count differs')
    return {'actual_result_pin': read(file)[1], 'source_original_RGB_exact': True, 'crops': crops, 'quality_approved': False, 'training_executed': False, 'original_check': original_check(c)}

if __name__ == '__main__':
    p = argparse.ArgumentParser(); p.add_argument('action', choices=['check', 'archive', 'restored', 'rgb-result']); p.add_argument('--binding', required=True); p.add_argument('--binding-sha256', required=True); p.add_argument('--control', choices=['classification', 'patch_classification'], required=True); p.add_argument('--workspace'); p.add_argument('--project'); p.add_argument('--result'); a = p.parse_args()
    _, c = load(a)
    value = original_check(c) if a.action == 'check' else archive(a, c) if a.action == 'archive' else restored(a, c) if a.action == 'restored' else rgb_result(a, c)
    print(json.dumps(value, ensure_ascii=False))
