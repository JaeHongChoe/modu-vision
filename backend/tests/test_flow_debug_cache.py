"""Exact debug reuse must never turn changed inputs into an old inspection."""
from copy import deepcopy
from pathlib import Path
import os
import pytest
from backend.engine.flow_debug_cache import DebugRunCache, file_sha256


def control(tmp_path, *, max_bytes=8192, max_entries=4):
    image=tmp_path/'image.bin';image.write_bytes(b'image-one')
    model=tmp_path/'model.bin';model.write_bytes(b'model-one')
    cache=DebugRunCache(max_bytes=max_bytes,max_entries=max_entries)
    identity=lambda:{'graph':{'roi':[0,0,16,16]},'input':file_sha256(image),'model':file_sha256(model),'scope':'project-a','device':'cpu','slots':1}
    calls=[]
    def run():
        calls.append(1)
        return {'status':'partial','final_verdict':'REVIEW','is_ok':False,'total_latency_ms':12,'crops':[],
            'execution_steps':[{'node_id':'roi','latency_ms':10,'status':'passed'}]}
    return cache,identity,run,calls,image,model


def test_repeated_exact_debug_reuses_copy_and_records_original_timing(tmp_path):
    cache,identity,run,calls,*_=control(tmp_path)
    a=cache.execute(identity,run);a['execution_steps'][0]['status']='error'
    b=cache.execute(identity,run)
    assert len(calls)==1 and a['debug_cache']['status']=='miss' and b['debug_cache']['status']=='hit'
    assert b['execution_steps'][0]['status']=='passed' and b['execution_steps'][0]['latency_ms']==0
    assert b['debug_cache']['original_latency_ms']==12 and b['final_verdict']=='REVIEW' and b['is_ok'] is False


@pytest.mark.parametrize('change',['input','model','graph','scope','device','slots'])
def test_each_semantic_identity_change_executes_again(tmp_path,change):
    cache,identity,run,calls,image,model=control(tmp_path)
    cache.execute(identity,run)
    if change in ('input','model'):
        file=image if change=='input' else model;old=file.stat();file.write_bytes(b'other-one');os.utime(file,ns=(old.st_atime_ns,old.st_mtime_ns))
        updated=identity
    else:
        updated=lambda:{**identity(),change:'different'}
    assert cache.execute(updated,run)['debug_cache']['status']=='miss'
    assert len(calls)==2


def test_identity_changed_during_execution_is_not_cached(tmp_path):
    cache,identity,run,calls,image,_=control(tmp_path)
    def changing():
        result=run();image.write_bytes(b'image-two');return result
    assert cache.execute(identity,changing)['debug_cache']['status']=='bypassed'
    cache.execute(identity,run)
    assert len(calls)==2


@pytest.mark.parametrize('bad',[{'status':'success','final_verdict':'OK','is_ok':True},
    {'status':'partial','final_verdict':'REVIEW','is_ok':False,'execution_steps':[{'status':'error'}]},
    {'status':'partial','final_verdict':'REVIEW','is_ok':False,'execution_steps':[{'status':'warning_untrained'}]}])
def test_failures_untrained_and_production_results_cannot_be_cached(tmp_path,bad):
    cache,identity,run,calls,*_=control(tmp_path)
    def invalid():return {**run(),**deepcopy(bad)}
    assert cache.execute(identity,invalid)['debug_cache']['status']=='bypassed'
    cache.execute(identity,invalid);assert len(calls)==2


def test_oversized_preview_bypasses_and_lru_evicts(tmp_path):
    cache,identity,run,calls,*_=control(tmp_path,max_bytes=1024,max_entries=1)
    cache.execute(identity,lambda:{**run(),'annotated_image':'x'*2000})
    cache.execute(identity,run);assert len(calls)==2
    cache.execute(lambda:{**identity(),'scope':'b'},run)
    cache.execute(identity,run);assert len(calls)==4


def test_linked_input_cannot_claim_exact_identity(tmp_path):
    file=tmp_path/'source';file.write_bytes(b'original');link=tmp_path/'linked';link.symlink_to(file)
    with pytest.raises(ValueError,match='linked'):file_sha256(link)


def test_actual_local_roi_trace_reuses_then_invalidates_graph_and_pixels(tmp_path,monkeypatch):
    from PIL import Image
    from backend.engine.flow_debug_cache import local_debug_run
    from backend.engine.flowchart_engine import FlowchartEngine,get_fixed_roi_flowchart
    engine=FlowchartEngine(device='cpu');original=engine.execute;calls=[]
    monkeypatch.setattr(engine,'execute',lambda **kw:(calls.append(1),original(**kw))[1])
    graph=get_fixed_roi_flowchart();roi=next(n for n in graph.nodes if n.data.node_type=='fixed_roi');roi.data.params['roi_bbox']=[0,0,24,24]
    image=tmp_path/'image.png';Image.new('RGB',(48,48),'red').save(image)
    project={'id':'a','project_dir':str(tmp_path)};cache=DebugRunCache()
    def run():return local_debug_run(cache,engine,graph,str(image),None,roi.id,project,{})
    assert run()['debug_cache']['status']=='miss'
    assert run()['debug_cache']['status']=='hit' and len(calls)==1
    roi.data.params['roi_bbox']=[8,8,40,40]
    changed=run();step=next(s for s in changed['execution_steps'] if s['node_id']==roi.id)
    assert changed['debug_cache']['status']=='miss' and step['artifacts'][0]['bbox']==[8,8,40,40]
    Image.new('RGB',(48,48),'blue').save(image);assert run()['debug_cache']['status']=='miss' and len(calls)==3
    project['id']='b';assert run()['debug_cache']['status']=='miss' and len(calls)==4
    assert run()['is_ok'] is False and run()['status']=='partial'
