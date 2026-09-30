import hashlib
import json
from pathlib import Path
import pytest
from fastapi import HTTPException
from PIL import Image
from backend.api.routes_model_comparisons import _owned_patch_test_images


def test_owned_patch_holdout_uses_originals_and_rejects_conflicting_cohorts(tmp_path):
    source=tmp_path/'originals';source.mkdir()
    owned=tmp_path/'project'/'dataset';owned.mkdir(parents=True)
    entries=[]
    for index,split in enumerate(('train','val','test')):
        p=source/f'{index}.png';Image.new('RGB',(8,8),(index*50,0,0)).save(p)
        entries.append((p,split,hashlib.sha256(p.read_bytes()).hexdigest()))
    models=[]
    for number in (1,2):
        dataset=owned/f'prepared{number}';dataset.mkdir()
        rows=[];mapping={}
        for path,split,digest in entries:
            dest=dataset/path.name;dest.write_bytes(path.read_bytes())
            rows.append({'image':path.name,'box':[0,0,8,8],'label':'NG','split':split,'source_sha256':digest})
            mapping[path.name]={'source_relative_path':path.name,'source_sha256':digest}
        (dataset/'patches.json').write_text(json.dumps({'version':1,'classes':['OK','NG'],'normal_class':'OK','patch_size':8,'stride':8,'patches':rows,'source_dataset_path':str(source),'source_map':mapping,'test_image_verdicts':{'2.png':'NG'}}))
        models.append({'task':'patch_classification','family_dataset_path':str(dataset)})
    project={'dataset_dir':str(owned),'models_dir':str(tmp_path/'project'/'models')}
    images,total=_owned_patch_test_images(project,source,models,10)
    assert total==1 and images[0]['file_path']==str(source/'2.png')
    assert images[0]['ground_truth_verdict']=='NG'
    second=Path(models[1]['family_dataset_path'])/'patches.json';payload=json.loads(second.read_text());payload['test_image_verdicts']['2.png']='OK';second.write_text(json.dumps(payload))
    with pytest.raises(HTTPException,match='') as error:_owned_patch_test_images(project,source,models,10)
    assert error.value.status_code==409
