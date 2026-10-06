"""Owned CPU model/queue endurance measurement; never an approved field service.

Requires explicit checkpoint and image pins. Uses real flow inference, retains
each interval's durable queue result, stops on source changes, quota or a missed
interval. A short run cannot produce a 72-hour completion claim. No delivery,
camera, PLC, production activation or model-quality approval is performed.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import signal
import sys
import time

sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from scripts.acceptance.scale_and_soak import sha,host,peak_rss


def source_binding():
    root=Path(__file__).resolve().parents[2]
    files=sorted((root/'backend').rglob('*.py'))+[Path(__file__).resolve(),root/'scripts/acceptance/scale_and_soak.py']
    return {str(p.relative_to(root)):sha(p) for p in files}


def unlinked(file):
    file=Path(file).absolute()
    if any(p.is_symlink() for p in (file,*file.parents)) or not file.is_file():
        raise ValueError('Explicit unlinked regular inputs are required')
    return file


def run(output,*,checkpoint,checkpoint_sha256,images,seconds=259200,interval=60,
        quota_bytes=512*1024**2,minimum_free_bytes=64*1024**2):
    if not isinstance(seconds,(int,float)) or not math.isfinite(seconds) or not 0<seconds<=259200:
        raise ValueError('Use a finite duration of at most 72 hours')
    if not isinstance(interval,(int,float)) or not math.isfinite(interval) or not .1<=interval<=60:
        raise ValueError('Use a finite 0.1–60 second interval')
    if type(quota_bytes) is not int or not 1024**2<=quota_bytes<=1024**3:
        raise ValueError('Use a bounded 1 MiB–1 GiB owned output quota')
    if type(minimum_free_bytes) is not int or minimum_free_bytes<64*1024**2:
        raise ValueError('Keep at least 64 MiB free')
    checkpoint=unlinked(checkpoint)
    if sha(checkpoint)!=checkpoint_sha256:raise ValueError('Checkpoint differs from the explicit pin')
    if not isinstance(images,list) or not 1<=len(images)<=16:raise ValueError('Pin 1–16 local images')
    rows=[]
    for row in images:
        if not isinstance(row,dict) or set(row)!={'path','sha256'}:raise ValueError('Image inventory fields differ')
        image=unlinked(row['path'])
        if sha(image)!=row['sha256']:raise ValueError('Image differs from its explicit pin')
        rows.append({'path':str(image),'sha256':row['sha256']})
    output=Path(output).absolute()
    if any(p.is_symlink() for p in (output,*output.parents)):raise ValueError('Linked output storage is refused')
    output.mkdir(parents=True,exist_ok=False)
    import torch
    from backend.engine.flowchart_engine import FlowchartEngine,get_fixed_roi_flowchart
    from backend.engine.inspection_service import InspectionStore,InboxFull
    from PIL import Image
    torch.set_num_threads(1)
    pipeline=get_fixed_roi_flowchart(inspection_task='classification',job_id='job_pinned_endurance')
    with Image.open(rows[0]['path']) as image:width,height=image.size
    if min(width,height)<16 or width*height>80000000:raise ValueError('Use an image between 16 pixels and 80M pixels')
    for row in rows:
        with Image.open(row['path']) as image:
            if image.size!=(width,height):raise ValueError('This control requires one fixed original-image coordinate space')
    next(n for n in pipeline.nodes if n.data.node_type=='fixed_roi').data.params['roi_bbox']=[0,0,width,height]
    binding=source_binding()
    engine=FlowchartEngine(device='cpu',checkpoint_resolver=lambda job,task:checkpoint if (job,task)==('job_pinned_endurance','classification') else None)
    state=output/'queue';store=InspectionStore(state,max_outstanding=len(rows),max_queue_age_seconds=600)
    started=time.monotonic();previous=time.time();deadline=started+seconds;cycles=completed=duplicates=overflow=0
    stopped=False
    def stop(signum,frame):
        nonlocal stopped
        stopped=True
    old_handlers={s:signal.signal(s,stop) for s in (signal.SIGTERM,signal.SIGINT)}
    receipt={'schema_version':1,'status':'running','pid':os.getpid(),'started_utc':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),
        'requested_seconds':seconds,'interval_seconds':interval,'checkpoint_sha256':checkpoint_sha256,
        'images':rows,'source_binding':binding,'actual_cpu_inference':True,'model_quality_approved':False,
        'approved_field_service':False,'hardware_delivery_exercised':False,'soak_72h_completed':False,
        'host':host(),'quota_bytes':quota_bytes,'minimum_free_bytes':minimum_free_bytes}
    log=output/'intervals.jsonl'
    def write():
        receipt.update(elapsed_seconds=time.monotonic()-started,cycles=cycles,completed=completed,
                       duplicate_requests=duplicates,backpressure_refusals=overflow,process_peak_rss_bytes=peak_rss())
        temporary=output/'heartbeat.pending'
        with temporary.open('w',encoding='utf-8') as handle:
            json.dump(receipt,handle,ensure_ascii=False,indent=2);handle.flush();os.fsync(handle.fileno())
        os.replace(temporary,output/'receipt.json')
    write()
    try:
        with log.open('x',encoding='utf-8') as events:
            while True:
                if stopped:raise InterruptedError('Endurance measurement interrupted')
                now=time.time()
                if cycles and now-previous>max(5,interval*3):raise RuntimeError('Missed continuous-operation interval; sleep or stall invalidates endurance')
                previous=now
                if source_binding()!=binding or sha(checkpoint)!=checkpoint_sha256:raise RuntimeError('Frozen source or checkpoint changed')
                if sum(p.stat().st_size for p in output.rglob('*') if p.is_file())>=quota_bytes or shutil.disk_usage(output).free<minimum_free_bytes:
                    raise RuntimeError('Owned output quota or free-disk reserve reached')
                for index,row in enumerate(rows):
                    if sha(unlinked(row['path']))!=row['sha256']:raise RuntimeError('Frozen source image changed')
                    key=f'endurance-{cycles}-{index}';job=store.enqueue(Path(row['path']),'file',idempotency_key=key)
                    if store.enqueue(Path(row['path']),'file',idempotency_key=key)!=job:raise RuntimeError('Duplicate input made another job')
                    duplicates+=1
                try:store.enqueue(Path(rows[0]['path']),'file',idempotency_key=f'overflow-{cycles}')
                except InboxFull:overflow+=1
                else:raise RuntimeError('Backpressure did not refuse an overflow')
                # Reopen the same queue; no reset, no inference/cache mocking.
                store=InspectionStore(state,max_outstanding=len(rows),max_queue_age_seconds=600);store.recover()
                for _ in rows:
                    job=store.claim()
                    if job is None:raise RuntimeError('Queued input missing after reopen')
                    t=time.monotonic();result=engine.execute(pipeline,image_path=job['image_path'])
                    if result.get('status') not in {'success','review'} or result.get('final_verdict') not in {'OK','NG','REVIEW'}:raise RuntimeError('Flow did not complete an explicit verdict')
                    if any(s.get('status') in {'error','warning_untrained'} for s in result.get('execution_steps',[])):raise RuntimeError('Unexecuted model or flow error invalidates endurance')
                    # Functional control predictions never become a released OK.
                    model_verdict=result['final_verdict']
                    store.finish(job['job_id'],result={'final_verdict':'REVIEW','model_verdict':model_verdict,'endurance_control':True})
                    events.write(json.dumps({'cycle':cycles,'job_id':job['job_id'],'model_verdict':model_verdict,
                        'published_verdict':'REVIEW','inference_ms':(time.monotonic()-t)*1000,'utc':time.time()})+'\n')
                    completed+=1
                events.flush();os.fsync(events.fileno());cycles+=1
                if store.pending_count():raise RuntimeError('Queue failed to drain')
                write()
                if time.monotonic()>=deadline:break
                time.sleep(min(interval,max(0,deadline-time.monotonic())))
        if source_binding()!=binding or sha(checkpoint)!=checkpoint_sha256:raise RuntimeError('Source changed before completion')
        receipt.update(status='completed',source_unchanged=True,
            soak_72h_completed=seconds>=259200 and time.monotonic()-started>=259200)
    except BaseException as exc:
        receipt.update(status='interrupted' if isinstance(exc,(InterruptedError,KeyboardInterrupt)) else 'failed',
            error_type=type(exc).__name__,error=str(exc)[:1000]);write();raise
    finally:
        for s,handler in old_handlers.items():signal.signal(s,handler)
    write();return receipt


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True);parser.add_argument('--checkpoint',type=Path,required=True)
    parser.add_argument('--checkpoint-sha256',required=True);parser.add_argument('--images',type=Path,required=True)
    parser.add_argument('--images-sha256',required=True);parser.add_argument('--seconds',type=float,default=259200)
    parser.add_argument('--interval',type=float,default=60)
    args=parser.parse_args();manifest=unlinked(args.images)
    if manifest.stat().st_size>16384 or sha(manifest)!=args.images_sha256:raise ValueError('Image manifest size or pin differs')
    result=run(args.output,checkpoint=args.checkpoint,checkpoint_sha256=args.checkpoint_sha256,
               images=json.loads(manifest.read_text(encoding='utf-8')),seconds=args.seconds,interval=args.interval)
    print(json.dumps({'status':result['status'],'completed':result['completed'],'soak_72h_completed':result['soak_72h_completed']}))

if __name__=='__main__':main()
