"""Owned synthetic service receipts for intake UI QA; no model is executed."""
import hashlib
import json
from pathlib import Path
import sys

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from backend.engine.inspection_service import InspectionStore
from backend.engine import dataset_metadata as dm


def seed(root, project):
    root = Path(root).resolve()
    project_root = Path(project['project_dir']).resolve()
    source = Path(project['source_dataset_dir']).resolve()
    if not project_root.is_relative_to(root) or not source.is_relative_to(root):
        raise ValueError('Capture fixtures require an isolated owned test workspace')
    images = sorted(source.rglob('*.png'))
    assert len(images) == 2
    assignments = {image.relative_to(source).as_posix(): 'test' if image.parent.name == 'ok' else 'train' for image in images}
    split = Path(project['dataset_dir']) / 'splits' / (hashlib.sha256(str(source).encode()).hexdigest()+'.json')
    split.parent.mkdir(parents=True, exist_ok=True)
    split.write_text(json.dumps({'folder_path': str(source), 'assignments': assignments, 'seed': 42}))
    with dm.metadata_transaction(project_root, source, project['annotations_dir']) as ledger:
        ledger['team_data'] = {'schema_version':1, 'settings':{'revision':4, 'editing_enabled':True, 'review_enabled':True,
            'required_reviews':2, 'prevent_self_review':True, 'approved_only_training':True}, 'books':[]}
    store = InspectionStore(project_root/'runtime_service'/'state')
    jobs = {}
    for reason, color, payload in [
        ('disagreement','green',{'final_verdict':'NG','models_disagree':True}),
        ('review','blue',{'final_verdict':'REVIEW','review_required':True}),
        ('threshold','red',{'final_verdict':'OK','max_defect_score':.51,'score_unit':'fraction'}),
        ('plain','yellow',{'final_verdict':'OK'}),
        ('duplicate',None,{'final_verdict':'OK'}),
        ('damaged',None,{'final_verdict':'REVIEW'}),
    ]:
        image = store.state_dir/'uploads'/f'{reason}.png'
        if reason == 'duplicate': image = next(image for image in images if image.parent.name=='ok')
        elif reason == 'damaged': image.write_bytes(b'owned intentionally invalid image fixture')
        else: Image.new('RGB', (32,32), color).save(image)
        identifier = store.enqueue(image, 'http')
        assert store.claim()['job_id'] == identifier
        store.finish(identifier, result={'status':'success', **payload, 'graph_sha256':'a'*64,
            'execution_steps':[{'node_id':'decision','branch_verdict':payload['final_verdict']}],
            'crops':[], 'runtime_identity':{'manifest_sha256':'b'*64,'model_sha256':{'controlled_fixture':'c'*64}}})
        jobs[reason] = identifier
    return {'jobs':jobs, 'split_path':str(split), 'assignments':assignments, 'model_executed':False,
        'inputs':'synthetic images and controlled persisted service results'}


if __name__ == '__main__':
    print(json.dumps(seed(sys.argv[1], json.loads(sys.argv[2]))))
