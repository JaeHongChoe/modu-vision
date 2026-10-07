"""Seed an owned 100k-row SQL fixture with three real images, never an import receipt.

Most rows intentionally have no file, no digest and READ_ERROR. This measures
metadata paging/search and the actual picker; it is not a 100k image inventory,
validated dataset, training run or quality acceptance.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
import time
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from PIL import Image
from backend.contracts.context import ContextRegistry, ProjectContext
from backend.engine.dataset_index import DatasetIndex, image_identity, index_path

ROWS = 100000
ACTUAL = (0, 359, ROWS - 1)


def unlinked(path):
    path = Path(path).absolute()
    if any(item.is_symlink() for item in (path, *path.parents)):
        raise ValueError('Owned fixture paths cannot be linked')
    return path.resolve()


def seed(workspace, project):
    root = unlinked(workspace)
    run = unlinked(os.environ['MV_E2E_RUN_DIR'])
    if root.parent != run / 'ws' or not re.fullmatch(r'\d+-[a-f0-9]{8}-r\d+', root.name):
        raise ValueError('Exact owned E2E workspace required')
    if unlinked(os.environ['VISION_AI_STUDIO_USER_DATA_DIR']) != root / 'userData':
        raise ValueError('Exact owned E2E profile required')
    project_root = unlinked(project['project_dir'])
    if not project_root.is_relative_to(root) or not project_root.is_dir():
        raise ValueError('Owned created project required')
    context = ProjectContext.model_validate(project['project_context'])
    registries = []
    for directory in (root / 'projects', root / 'userData' / 'projects'):
        database = directory / '.context.sqlite3'
        if not database.is_file():
            continue
        unlinked(database)
        with sqlite3.connect(database.as_uri() + '?mode=ro', uri=True) as db:
            registered = db.execute('SELECT path FROM project_locations WHERE workspace_id=? AND project_id=?',
                                    (context.workspace_id, context.project_id)).fetchone()
            if registered and unlinked(registered[0]) == project_root:
                registries.append(directory)
    if len(registries) != 1:
        raise ValueError('Exact originating project registry required')
    registry = ContextRegistry(registries[0])
    key = registry.project_key(context)
    index = DatasetIndex(index_path(registry.root))
    source = root / 'scale-library-metadata-only'
    # A repeated helper invocation cannot replace previous fixture evidence.
    with sqlite3.connect(index.path) as db:
        if db.execute('SELECT 1 FROM dataset_revisions WHERE project_key=?', (key,)).fetchone():
            raise ValueError('A pristine owned fixture project is required')
        if db.execute('SELECT 1 FROM dataset_active WHERE project_key=?', (key,)).fetchone():
            raise ValueError('An existing active dataset cannot be replaced')
    source.mkdir(mode=0o700, exist_ok=False)
    (source / 'metadata').mkdir(mode=0o700)
    real = {}
    for ordinal in ACTUAL:
        relative = f'metadata/entry-{ordinal:06}.png'
        file = source / relative
        Image.new('RGB', (32, 32), (ordinal % 251, (ordinal // 251) % 251, 113)).save(file)
        raw = file.read_bytes()
        with Image.open(file) as image:
            image.load()
            dimensions = image.size
        real[ordinal] = {'path': str(file), 'relative_path': relative,
                         'image_uuid': image_identity(project_root, source, relative),
                         'sha256': hashlib.sha256(raw).hexdigest(), 'size': len(raw),
                         'width': dimensions[0], 'height': dimensions[1]}
    revision = uuid.uuid4().hex
    declaration = {'fixture_kind': 'metadata_only_not_image_inventory', 'rows': ROWS,
                   'actual_ordinals': list(ACTUAL), 'verified_all': False}
    manifest = hashlib.sha256(json.dumps(declaration, sort_keys=True).encode()).hexdigest()
    def rows():
        for ordinal in range(ROWS):
            relative = f'metadata/entry-{ordinal:06}.png'
            actual = real.get(ordinal)
            yield (revision, relative, image_identity(project_root, source, relative),
                   actual['sha256'] if actual else None, actual['size'] if actual else 0,
                   actual['width'] if actual else None, actual['height'] if actual else None,
                   'metadata-only-fixture', None, 1 if actual else 0,
                   None if actual else 'READ_ERROR',
                   None if actual else 'Metadata-only fixture: no physical image exists', 0)
    with sqlite3.connect(index.path) as db:
        db.execute('''INSERT INTO dataset_revisions
            (revision_id,project_key,source_root,project_root,task,invalid_policy,state,manifest_sha256,
             image_count,valid_count,error_count,unreadable_folders,skipped_links,reused_entries,
             verified_all,follow_links,created_ns) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
            (revision, key, str(source), str(project_root), 'classification', 'exclude', 'fixture_only',
             manifest, ROWS, len(real), ROWS - len(real), 0, 0, 0, 0, 0, time.time_ns()))
        db.executemany('''INSERT INTO dataset_index_images
            (revision_id,relative_path,image_uuid,sha256,size,width,height,label,split,valid,error_code,error_detail,via_link)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)''', rows())
        # The UI's active pointer selects this explicitly unvalidated test fixture.
        # No import/accept endpoint or production revision is impersonated.
        db.execute('INSERT INTO dataset_active(project_key,revision_id) VALUES(?,?)', (key, revision))
        counts = db.execute('SELECT COUNT(*), SUM(valid), SUM(sha256 IS NOT NULL) FROM dataset_index_images WHERE revision_id=?',
                            (revision,)).fetchone()
        if counts != (ROWS, len(real), len(real)):
            raise ValueError('Metadata/physical-image count mismatch')
    physical = list(source.rglob('*.png'))
    if len(physical) != len(real):
        raise ValueError('Fixture must contain exactly three real images')
    return {'fixture_kind': declaration['fixture_kind'], 'metadata_only': True, 'metadata_rows': ROWS,
            'actual_images': len(real), 'missing_files': ROWS - len(real), 'source_root': str(source),
            'revision_id': revision, 'revision_state': 'fixture_only', 'index_path': str(index.path),
            'project_key': key, 'project_context': context.model_dump(), 'verified_all': False,
            'source_hashes': [{'path': row['path'], 'sha256': row['sha256']} for row in real.values()],
            'tail': real[ROWS - 1], 'actual_training': False, 'model_quality_approved': False}


if __name__ == '__main__':
    print(json.dumps(seed(sys.argv[1], json.loads(sys.argv[2]))))
