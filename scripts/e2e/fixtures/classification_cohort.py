"""Read the actual default loader cohort of an owned UI fixture; never train or save a split."""
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from backend.engine.dataset_loaders import ClassificationDataset

source = Path(sys.argv[1]).resolve()
partitions = {}
for split in ('train', 'val', 'test'):
    dataset = ClassificationDataset(source, split=split)
    partitions[split] = [
        {'path': path.relative_to(source).as_posix(), 'label': dataset.classes[label]}
        for path, label in dataset.samples
    ]
print(json.dumps({'source': str(source), 'partitions': partitions,
                  'files': {file.relative_to(source).as_posix(): hashlib.sha256(file.read_bytes()).hexdigest()
                            for file in sorted(source.rglob('*.png')) if file.is_file()}}))
