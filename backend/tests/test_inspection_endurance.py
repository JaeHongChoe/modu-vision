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
