"""Controlled saved reports for actual queue UI; no model or human truth claim."""
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[3]))
from backend.engine.evaluation_history import EvaluationHistory

def seed(root,project,source):
    root,project,source=(Path(p).resolve() for p in (root,project,source))
    if not project.is_relative_to(root) or not source.is_relative_to(root):
        raise ValueError('Queue fixtures require an isolated test workspace')
    rows=[]
    for name,fields in [('error',{'error':'controlled prediction failure','confidence':.5}),
                        ('disagreement',{'disagreement':True,'confidence':.95}),
                        ('threshold',{'confidence':.52})]:
        image=source/(name+'.png')
        rows.append({'file_path':str(image),'image_sha256':hashlib.sha256(image.read_bytes()).hexdigest(),**fields})
    history=EvaluationHistory(project/'reports/evaluations')
    record=history.append({'job_id':'controlled-review-ui','task':'segmentation','test_predictions':rows,
                          'fixture_kind':'controlled_reports_no_model_inference'},
                         {'source_dataset_path':str(source),'task':'segmentation','labelset_id':'default',
                          'fixture_kind':'controlled_reports_no_model_inference'})
    path=history.directory/(record['evaluation_id']+'.json')
    return {'record':record,'path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
            'controlled_reports_not_model_inference':True}

if __name__=='__main__':print(json.dumps(seed(*sys.argv[1:])))
