"""Read active-labelset usage policy without changing the original image inventory."""
from pathlib import Path
import json

from backend.engine.annotation_storage import dataset_annotation_dir,request_project_root


def unused_image_paths(source=None):
    project=request_project_root()
    if project is None:
        return set()
    configuration=project/'project.json'
    if configuration.is_file():
        configured=json.loads(configuration.read_text(encoding='utf-8')).get('source_dataset_dir')
        if configured:
            source=Path(configured)
    if source is None:
        return set()
    source=Path(source).resolve()
    # Gold samples of the label review (E05) are its reference, not training or test data, unless the policy keeps them.
    from backend.engine.annotation_quality import gold_image_paths
    gold=gold_image_paths({'project_dir':str(project),'source_dataset_dir':str(source)})
    ledger=dataset_annotation_dir(source)/'metadata'/'workflow.json'
    if ledger.is_symlink():
        raise ValueError('Usage ledger cannot be a symbolic link')
    if not ledger.is_file():
        return gold
    rows=json.loads(ledger.read_text(encoding='utf-8')).get('images',{})
    if not isinstance(rows,dict):
        raise ValueError('Invalid image usage ledger')
    excluded=set()
    for relative,row in rows.items():
        if row.get('usage_state')!='not_used':
            continue
        path=source/relative
        if not path.resolve().is_relative_to(source):
            raise ValueError('Usage image path escaped dataset')
        excluded.add(str(path.resolve()))
    # Team review is explicitly opt-in. Reuse this filter across core loaders,
    # saved split readers and remote preparation instead of a UI-only warning.
    from backend.engine.team_data import training_excluded_paths
    from backend.engine.annotation_storage import scoped_annotation_root
    configuration_data=json.loads(configuration.read_text(encoding='utf-8')) if configuration.is_file() else {}
    scoped={**configuration_data,'project_dir':str(project),'source_dataset_dir':str(source),
            'annotations_dir':str(scoped_annotation_root(project/'annotations'))}
    excluded.update(training_excluded_paths(scoped,source))
    return excluded|gold
