"""Explicit specialist labels and native pixels in project-owned storage."""
from __future__ import annotations
from hashlib import sha256
import json
from pathlib import Path, PurePosixPath
import shutil

MANIFESTS={'ocr':'ocr.json','rotated_detection':'rotated_boxes.json'}

def prepared_source_provenance(root, raw, source_hashes):
    """Validate copied inputs against canonical originals whenever re-opened."""
    if not raw.get('source_dataset_path'):
        return {}
    from backend.engine.source_aliases import resolve_source_root
    canonical=Path(raw['source_dataset_path']).expanduser().resolve()
    source=resolve_source_root(canonical); mapping=raw.get('source_map')
    if not isinstance(mapping,dict) or set(mapping)!=set(source_hashes):
        raise ValueError('Prepared source map must cover every labeled image')
    for image,digest in source_hashes.items():
        row=mapping[image]
        relative=PurePosixPath(row.get('source_relative_path','')) if isinstance(row,dict) else None
        if relative is None or relative.is_absolute() or not relative.parts or '..' in relative.parts:
            raise ValueError('Prepared source map path must be inside the canonical source')
        original=source/relative
        if (original.is_symlink() or not original.is_file() or not original.resolve().is_relative_to(source)
                or row.get('source_sha256')!=digest or sha256(original.read_bytes()).hexdigest()!=digest):
            raise ValueError('Prepared original source image hash changed')
    return {'source_dataset_path':str(canonical),'source_map':mapping}

def load_family_manifest(task, dataset):
    if task=='ocr':
        from backend.engine.ocr import load_ocr_manifest
        return load_ocr_manifest(dataset)
    if task=='rotated_detection':
        from backend.engine.rotated_detection import load_rotated_manifest
        return load_rotated_manifest(dataset)
    raise ValueError('Unsupported prepared specialist family')

def prepare_family_dataset(task, source, output, rows):
    if task not in MANIFESTS:raise ValueError('Unsupported prepared specialist family')
    source=Path(source).expanduser().resolve(); output=Path(output).expanduser().resolve()
    if not source.is_dir() or output.exists() or output==source or output.is_relative_to(source):
        raise ValueError('Specialist preparation needs a new owned directory outside originals')
    if not rows:raise ValueError('Explicit specialist truth is required')
    mapping={}; output.mkdir(parents=True)
    try:
        for row in rows:
            image=row.get('image'); relative=PurePosixPath(image) if isinstance(image,str) else None
            if relative is None or relative.is_absolute() or not relative.parts or '..' in relative.parts:
                raise ValueError('Specialist image must be a relative original source path')
            path=source/relative
            if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(source):
                raise ValueError('Specialist image is unavailable in original source')
            digest=sha256(path.read_bytes()).hexdigest()
            if row.get('source_sha256') and row['source_sha256']!=digest:raise ValueError('Specialist original image hash differs')
            destination=output/relative; destination.parent.mkdir(parents=True,exist_ok=True); shutil.copyfile(path,destination)
            if sha256(destination.read_bytes()).hexdigest()!=digest:raise ValueError('Original changed during specialist preparation')
            mapping[image]={'source_relative_path':image,'source_sha256':digest}
        if task=='ocr':
            from backend.engine.ocr import write_ocr_manifest
            write_ocr_manifest(output,rows)
        else:
            from backend.engine.rotated_detection import write_rotated_manifest
            write_rotated_manifest(output,rows)
        path=output/MANIFESTS[task]; raw=json.loads(path.read_text())
        raw.update(source_dataset_path=str(source),source_map=mapping)
        path.write_text(json.dumps(raw,ensure_ascii=False,indent=2))
        return load_family_manifest(task,output)
    except BaseException:
        shutil.rmtree(output,ignore_errors=True); raise

def resolve_family_dataset(project, task, supplied):
    configured=project.get('source_dataset_dir')
    if not configured:raise ValueError('Select the active project original source first')
    source=Path(configured).expanduser().resolve(); requested=Path(supplied).expanduser(); dataset=requested.resolve()
    if requested.is_symlink():raise ValueError('Specialist data cannot be a linked directory')
    if dataset==source:
        if (source/MANIFESTS[task]).is_file():return load_family_manifest(task,source)
        # Legacy callers may retain the source path after /manifest preparation.
        rows=list_prepared_family_datasets(project,task)
        if rows:return load_family_manifest(task,rows[-1]['dataset_path'])
        return load_family_manifest(task,source)
    owned=Path(project['dataset_dir'])
    if owned.is_symlink() or not dataset.is_relative_to(owned.resolve()):raise ValueError('Specialist prepared data must belong to the active project')
    manifest=load_family_manifest(task,dataset)
    if manifest.provenance.get('source_dataset_path')!=str(source):raise ValueError('Specialist prepared data differs from active original source')
    return manifest

def list_prepared_family_datasets(project,task):
    root=Path(project['dataset_dir'])/task; rows=[]
    for path in sorted(root.glob(f'*/{MANIFESTS[task]}'),key=lambda path:path.stat().st_mtime):
        try:
            manifest=resolve_family_dataset(project,task,path.parent)
            rows.append({'dataset_path':str(manifest.root),'sample_count':manifest.provenance['source_image_count'],
                         'provenance':manifest.provenance})
        except (ValueError,OSError,KeyError,TypeError):continue
    return rows
