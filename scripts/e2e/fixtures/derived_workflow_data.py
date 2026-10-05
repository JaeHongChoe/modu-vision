"""Owned unique synthetic inputs for actual edit/training/ancestry qualification.

No pretrained weights, receipts or approvals are synthesized. The saved split
is a controlled fixture; the original API freezes and executes actual models.
"""
import hashlib
import json
from pathlib import Path
import sys
from PIL import Image


def seed(root, project=None):
    root = Path(root).resolve()
    source = root / 'derived-workflow-source'
    if project is None:
        source.mkdir(exist_ok=False)
        rows = []
        for part_index, (part, count) in enumerate((('train', 8), ('val', 4), ('test', 8))):
            for label, base in (('OK', 220), ('NG', 35)):
                folder = source / part / label; folder.mkdir(parents=True)
                for index in range(count):
                    image = Image.new('RGB', (64, 64), (base, base, base))
                    image.putpixel((index + 2, part_index + 2), (index, part_index, base))
                    file = folder / f'{part}_{label}_{index:02}.png'; image.save(file)
                    rows.append({'path': str(file), 'relative_path': file.relative_to(source).as_posix(),
                                 'sha256': hashlib.sha256(file.read_bytes()).hexdigest(), 'split': part})
        return {'source': str(source), 'images': rows, 'synthetic': True}
    project_root = Path(project['project_dir']).resolve()
    assert project_root.is_relative_to(root) and source.is_relative_to(root)
    file = Path(project['dataset_dir']) / 'splits' / (hashlib.sha256(str(source).encode()).hexdigest()+'.json')
    file.parent.mkdir(parents=True, exist_ok=True)
    assignments = {image.relative_to(source).as_posix(): image.relative_to(source).parts[0] for image in source.rglob('*.png')}
    file.write_text(json.dumps({'folder_path': str(source), 'assignments': assignments, 'seed': 104}))
    return {'split_path': str(file), 'assignments': assignments}


if __name__ == '__main__':
    print(json.dumps(seed(sys.argv[1], json.loads(sys.argv[2]) if len(sys.argv)>2 else None)))
