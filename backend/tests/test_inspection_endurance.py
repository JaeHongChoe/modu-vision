from pathlib import Path
import hashlib
import math
import pytest
from scripts.acceptance.inspection_endurance import run

@pytest.mark.parametrize('seconds',[0,-1,math.nan,math.inf,259201])
def test_unbounded_or_invalid_endurance_duration_is_refused_before_outputs(tmp_path,seconds):
    with pytest.raises(ValueError):run(tmp_path/'out',checkpoint=tmp_path/'missing',checkpoint_sha256='0'*64,images=[],seconds=seconds)
    assert not (tmp_path/'out').exists()

def test_changed_checkpoint_and_input_pins_cannot_start_measurement(tmp_path):
    ck=tmp_path/'best_model.pt';ck.write_bytes(b'original')
    with pytest.raises(ValueError,match='Checkpoint differs'):run(tmp_path/'out',checkpoint=ck,checkpoint_sha256='0'*64,images=[])
    image=tmp_path/'image.png';image.write_bytes(b'original')
    digest=hashlib.sha256(ck.read_bytes()).hexdigest()
    with pytest.raises(ValueError,match='Image differs'):run(tmp_path/'out',checkpoint=ck,checkpoint_sha256=digest,images=[{'path':str(image),'sha256':'0'*64}])
    assert not (tmp_path/'out').exists()

def test_real_cpu_flow_retains_review_results_duplicates_backpressure_and_short_run_boundary(tmp_path):
    import torch
    from PIL import Image
    from backend.engine.classification.model import create_classification_model
    torch.set_num_threads(1);model=create_classification_model('resnet18',2,pretrained=False)
    ck=tmp_path/'best_model.pt';torch.save({'task':'classification','backbone':'resnet18','classes':['OK','NG'],
        'image_size':[32,32],'model_state_dict':model.state_dict()},ck)
    image=tmp_path/'input.png';Image.new('RGB',(32,32),'gray').save(image)
    digest=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
    result=run(tmp_path/'out',checkpoint=ck,checkpoint_sha256=digest(ck),images=[{'path':str(image),'sha256':digest(image)}],seconds=.3,interval=.1)
    assert result['status']=='completed' and result['completed']>=1
    assert result['completed']==result['duplicate_requests']==result['backpressure_refusals']
    assert result['source_unchanged'] and result['actual_cpu_inference']
    assert not result['soak_72h_completed'] and not result['approved_field_service'] and not result['model_quality_approved']
    assert '"published_verdict": "REVIEW"' in (tmp_path/'out/intervals.jsonl').read_text(encoding='utf-8')


def test_initial_disk_reserve_refusal_does_not_claim_cpu_inference(tmp_path):
    import json
    import shutil
    import signal
    import sqlite3
    from PIL import Image

    # An explicitly non-model sentinel is sufficient for this pre-inference
    # input-pin boundary. It must never reach checkpoint loading or execution.
    checkpoint=tmp_path/'pre-inference-sentinel.pt'
    checkpoint.write_bytes(b'S7-05 pre-inference disk-reserve control; not a model')
    image=tmp_path/'input.png'
    with Image.new('RGB',(32,32),'gray') as pixels:pixels.save(image)
    def snapshot(path):
        before=path.stat();body=path.read_bytes();after=path.stat()
        raw9=lambda s:(s.st_dev,s.st_ino,s.st_mode,s.st_nlink,s.st_uid,s.st_gid,
                       s.st_size,s.st_mtime_ns,s.st_ctime_ns)
        assert raw9(before)==raw9(after)
        return hashlib.sha256(body).hexdigest(),raw9(after)
    originals={path:snapshot(path) for path in (checkpoint,image)}
    handlers={value:signal.getsignal(value) for value in (signal.SIGTERM,signal.SIGINT)}
    reserve=shutil.disk_usage(tmp_path).total+1
    output=tmp_path/'out'
    with pytest.raises(RuntimeError,match='^Owned output quota or free-disk reserve reached$'):
        run(output,checkpoint=checkpoint,checkpoint_sha256=originals[checkpoint][0],
            images=[{'path':str(image),'sha256':originals[image][0]}],seconds=.3,interval=.1,
            minimum_free_bytes=reserve)
    receipt=json.loads((output/'receipt.json').read_text())
    assert receipt['status']=='failed' and receipt['error_type']=='RuntimeError'
    assert receipt['error']=='Owned output quota or free-disk reserve reached'
    assert receipt['minimum_free_bytes']==reserve
    assert receipt['cycles']==receipt['completed']==receipt['duplicate_requests']==receipt['backpressure_refusals']==0
    assert not (output/'intervals.jsonl').read_bytes()
    with sqlite3.connect((output/'queue/inspection_service.sqlite3').as_uri()+'?mode=ro',uri=True) as db:
        assert db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0]==0
        assert db.execute('SELECT COUNT(*) FROM events').fetchone()[0]==0
    assert receipt['model_quality_approved'] is False
    assert receipt['approved_field_service'] is False and receipt['hardware_delivery_exercised'] is False
    assert receipt['soak_72h_completed'] is False
    assert {path:snapshot(path) for path in originals}==originals
    assert {value:signal.getsignal(value) for value in handlers}==handlers
    assert receipt['actual_cpu_inference'] is False
