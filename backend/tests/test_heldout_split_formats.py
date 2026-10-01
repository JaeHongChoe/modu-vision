import json
from pathlib import Path

import pytest
from backend.engine import flow_evaluation, capture_intake
from backend.tests.test_whole_flow_evaluation import workspace, model_provider


@pytest.mark.parametrize('absolute',[False,True])
def test_app_saved_absolute_and_relative_heldout_keys_freeze_without_rewriting_split(workspace,absolute):
    project,_,version,checkpoint=workspace;source=Path(project['source_dataset_dir'])
    file=next((Path(project['dataset_dir'])/'splits').glob('*.json'));record=json.loads(file.read_text())
    record['assignments']={(str(source/key) if absolute else key):value for key,value in record['assignments'].items()}
    file.write_text(json.dumps(record));before=file.read_bytes()
    cohort=flow_evaluation.freeze_cohort(project,version,model_provider=model_provider(checkpoint))
    assert [row['relative_path'] for row in cohort['samples']]==['defect.png','normal.png','unknown.png']
    assert capture_intake._split(project)[1]['assignments']=={'defect.png':'test','normal.png':'test','unknown.png':'test'}
    assert file.read_bytes()==before


@pytest.mark.parametrize('change',['outside','traversal','conflict','partition','partition_type','blank','backslash','link'])
def test_saved_split_normalization_fails_closed_for_unsafe_or_conflicting_keys(workspace,change):
    project,_,_,_=workspace;source=Path(project['source_dataset_dir']);file=next((Path(project['dataset_dir'])/'splits').glob('*.json'))
    record=json.loads(file.read_text())
    key={'outside':str(source.parent/'outside.png'),'traversal':'x/../normal.png','conflict':str(source/'normal.png'),'partition':'new.png','partition_type':'new.png','blank':'','backslash':'x\\normal.png','link':'linked.png'}[change]
    if change=='link': (source/key).symlink_to(source/'normal.png')
    record['assignments'][key]=[] if change=='partition_type' else 'invalid' if change=='partition' else 'train'
    file.write_text(json.dumps(record));before=file.read_bytes()
    with pytest.raises(ValueError):flow_evaluation._split(project)
    with pytest.raises(ValueError):capture_intake._split(project)
    assert file.read_bytes()==before
