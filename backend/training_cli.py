"""Run with python -m backend.training_cli; stdout is machine-readable JSONL."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import sys

def emit(value):print(json.dumps(value,ensure_ascii=False,allow_nan=False),flush=True)

def parser():
    from backend.engine.training_engine import TASKS
    root=argparse.ArgumentParser(description='Vision AI Studio folder/JSON training engine. Native sources remain read only.')
    commands=root.add_subparsers(dest='command',required=True)
    commands.add_parser('capabilities',help='Show real model families, applied search controls and metrics')
    prepare=commands.add_parser('prepare',help='Validate folder and external JSON truth; write owned task data')
    prepare.add_argument('--task',choices=TASKS,required=True);prepare.add_argument('--source',required=True);prepare.add_argument('--output',required=True)
    prepare.add_argument('--labels',help='Samples, COCO or LabelMe JSON file with explicit partitions');prepare.add_argument('--prepare-json',default='{}')
    train=commands.add_parser('train',help='Execute measured quick training, budgeted AutoDL or parent retraining')
    train.add_argument('--output');train.add_argument('--prepared-id');train.add_argument('--mode',choices=('quick','search','fast_retrain'))
    train.add_argument('--device',help='cpu, mps, cuda or cuda:N; unavailable device is an error');train.add_argument('--preset',choices=('fast','precision'))
    train.add_argument('--epochs',type=int);train.add_argument('--parent');train.add_argument('--config-json',help='Applied model/training controls as JSON')
    train.add_argument('--config-path',help='Reuse a delivered configuration.json and optionally override CLI fields')
    train.add_argument('--search-json',help='Per-family search dimensions as JSON');train.add_argument('--max-trials',type=int)
    train.add_argument('--max-total-epochs',type=int);train.add_argument('--max-seconds',type=float)
    train.add_argument('--background',action='store_true',help='Launch an independent local process; use status/cancel')
    worker=commands.add_parser('execute',help=argparse.SUPPRESS);worker.add_argument('--output',required=True);worker.add_argument('--run-id',required=True)
    worker.add_argument('--launch-handshake',action='store_true',help=argparse.SUPPRESS)
    basic=commands.add_parser('basic-execute',help=argparse.SUPPRESS)
    basic.add_argument('--spec',required=True);basic.add_argument('--launch-handshake',action='store_true',help=argparse.SUPPRESS)
    for name in ('status','cancel','evaluate','predict'):
        command=commands.add_parser(name,help={'status':'Reopen durable progress and verify delivered files','cancel':'Request cooperative cancellation','evaluate':'Write actual heldout metrics JSON','predict':'Write source-linked per-image predictions JSON'}[name])
        command.add_argument('--output',required=True);command.add_argument('--run-id',required=True)
        if name in ('evaluate','predict'):command.add_argument('--device',default='cpu')
        if name=='evaluate':command.add_argument('--split',choices=('val','test'),default='test')
        if name=='predict':command.add_argument('--image',action='append',help='Relative original image; repeat or omit for all');command.add_argument('--threshold',type=float,default=.5)
    return root

def main(argv=None):
    options=parser().parse_args(argv)
    import torch
    torch.set_num_threads(1)
    from backend.engine import training_engine as engine
    try:
        if options.command=='basic-execute':
            if options.launch_handshake:
                spec=json.loads(Path(options.spec).read_text())
                if sys.stdin.readline().strip()!=spec['job_id']:raise RuntimeError('Basic training launcher ended before publishing its owner')
            from backend.engine.local_training_worker import execute_basic
            result=execute_basic(options.spec)
        elif options.command=='capabilities':result=engine.capabilities()
        elif options.command=='prepare':
            result=engine.prepare(task=options.task,source_dataset_path=options.source,output_dir=options.output,
                labels_path=options.labels,prepare_options=json.loads(options.prepare_json))
        elif options.command=='train':
            recipe=json.loads(Path(options.config_path).read_text()) if options.config_path else {}
            # Delivered recipes include descriptive task/source fields; preparation
            # remains a separate explicit operation when inputs or truth change.
            recipe=engine.configuration_recipe(recipe)
            mapping={'output':'output_dir','prepared_id':'prepared_id','mode':'mode','device':'device','preset':'preset','epochs':'epochs_per_trial','parent':'parent_job_id'}
            for flag,key in mapping.items():
                if (value:=getattr(options,flag)) is not None:recipe[key]=value
            if options.config_json:recipe['config']=json.loads(options.config_json)
            if options.search_json:recipe['search_space']=json.loads(options.search_json)
            elif recipe.get('mode')!='search':recipe['search_space']=None
            budget=dict(recipe.get('budget') or {})
            for flag,key in [('max_trials','max_trials'),('max_total_epochs','max_total_epochs'),('max_seconds','max_seconds')]:
                if (value:=getattr(options,flag)) is not None:budget[key]=value
            if budget:recipe['budget']=budget
            if not recipe.get('output_dir'):raise ValueError('--output or a reusable --config-path is required')
            record=engine.create_run(**recipe)
            if options.background:
                logfile=Path(recipe['output_dir'])/'runs'/record['run_id']/'progress.jsonl'
                with logfile.open('ab') as log:
                    child=subprocess.Popen([sys.executable,'-m','backend.training_cli','execute','--output',recipe['output_dir'],'--run-id',record['run_id'],'--launch-handshake'],
                        cwd=Path(__file__).resolve().parent.parent,stdin=subprocess.PIPE,stdout=log,stderr=log,start_new_session=True)
                # Publish the execution owner before releasing the child. A fast
                # child can never have its running/completed journal overwritten
                # by a stale queued record from this launcher.
                try:
                    import psutil
                    with engine._LOCK,engine._file_lock(engine._path(recipe['output_dir'],record['run_id']).parent/'.journal.lock'):
                        record=engine.read_run(recipe['output_dir'],record['run_id'])
                        record.update(process_pid=child.pid,owner_pid=child.pid,owner_created_at=psutil.Process(child.pid).create_time())
                        engine._write(Path(recipe['output_dir'])/'runs'/record['run_id']/'run.json',record)
                    child.stdin.write((record['run_id']+'\n').encode());child.stdin.close()
                except Exception:
                    child.stdin.close();child.terminate();child.wait(timeout=10);raise
                result=record
            else:
                signal.signal(signal.SIGINT,lambda *_:engine.cancel_run(recipe['output_dir'],record['run_id']))
                signal.signal(signal.SIGTERM,lambda *_:engine.cancel_run(recipe['output_dir'],record['run_id']))
                result=engine.execute_run(recipe['output_dir'],record['run_id'],emit)
        elif options.command=='execute':
            if options.launch_handshake and sys.stdin.readline().strip()!=options.run_id:
                raise RuntimeError('Background launcher ended before publishing its execution owner')
            signal.signal(signal.SIGTERM,lambda *_:engine.cancel_run(options.output,options.run_id))
            result=engine.execute_run(options.output,options.run_id,emit)
        elif options.command=='status':result=engine.read_run(options.output,options.run_id)
        elif options.command=='cancel':result=engine.cancel_run(options.output,options.run_id)
        elif options.command=='evaluate':result=engine.evaluate(options.output,options.run_id,device=options.device,split=options.split)
        else:result=engine.predict(options.output,options.run_id,images=options.image,device=options.device,threshold=options.threshold)
        emit(result)
        return 1 if result.get('status') in {'failed','interrupted'} else 0
    except (ValueError,OSError,KeyError,TypeError,RuntimeError) as exc:
        emit({'event':'error','error':str(exc)});return 2

if __name__=='__main__':sys.exit(main())
