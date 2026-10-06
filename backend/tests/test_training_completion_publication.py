"""Completion readers cannot race a coordinator's final provenance write."""
import hashlib,json,threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import pytest,torch
from backend.api import routes_training as routes
from backend.engine.training_provenance import persist_model_binding

@pytest.mark.parametrize('mode',['fresh','reattached'])
def test_completed_job_is_not_observable_until_terminal_receipt_is_persisted(tmp_path,monkeypatch,mode):
    monkeypatch.setenv('VISION_AI_STUDIO_USER_DATA_DIR',str(tmp_path/'user'))
    # This barrier tests publication ordering. Unrelated GC/device-cache work
    # after thousands of prior model tests must not consume its wait budget.
    monkeypatch.setattr(routes,'clear_device_cache',lambda:None)
    entered,release,finished=threading.Event(),threading.Event(),threading.Event()
    real_write=routes._write_job_receipt
    def delayed(record):
        if record.status=='completed':entered.set();assert release.wait(5)
        real_write(record);finished.set()
    class Trainer:
        def __init__(self,**kwargs):pass
        def train(self,job_id):return {'status':'completed','best_metric':.25}
    monkeypatch.setattr(routes,'UnifiedAutoMLTrainer',Trainer);monkeypatch.setattr(routes,'_write_job_receipt',delayed)
    manager=routes.TrainingJobManager(local_execution='embedded')
    if mode=='fresh':record=manager.start_job('job_completion_fence','segmentation',str(tmp_path/'source'),str(tmp_path/'model'),device='cpu')
    else:
        record=routes.JobRecord(job_id='job_completion_fence',task='segmentation',preset='fast',dataset_path=str(tmp_path/'source'),output_dir=str(tmp_path/'model'),status='disconnected')
        manager.restore_local_job(record,runner=lambda cb:{'status':'completed','best_metric':.25},leases=manager._leases)
    try:
        assert entered.wait(5)
        with ThreadPoolExecutor(max_workers=1) as pool:
            seen=threading.Event();reader_started=threading.Event()
            def read():
                reader_started.set();current=manager.get_job(record.job_id);seen.set();return current.status,finished.is_set()
            result=pool.submit(read)
            assert reader_started.wait(5), 'The competing completion reader did not start'
            premature=seen.wait(.1);release.set();observed=result.result(5)
            assert not premature, 'A completed status escaped before its checkpoint/receipt stopped changing'
            assert observed==('completed',True)
    finally:release.set();record.thread.join(5)
    receipt=json.loads((Path(record.output_dir)/'job_receipt.json').read_text());assert receipt['status']=='completed'

def test_repeated_identical_provenance_preserves_checkpoint_bytes_and_metadata(tmp_path):
    model=tmp_path/'best_model.pt';meta=tmp_path/'model_meta.json';binding={'dataset_version_id':'owned_fixture','labelset_id':'default'}
    torch.save({'task':'segmentation','model_state_dict':{'weight':torch.zeros(1)},'training_provenance':binding},model)
    digest=hashlib.sha256(model.read_bytes()).hexdigest();meta.write_text(json.dumps({'training_provenance':binding,'checkpoint_sha256':digest}))
    before=(model.read_bytes(),meta.read_bytes(),model.stat().st_mtime_ns,meta.stat().st_mtime_ns)
    persist_model_binding(tmp_path,binding)
    assert before==(model.read_bytes(),meta.read_bytes(),model.stat().st_mtime_ns,meta.stat().st_mtime_ns)
