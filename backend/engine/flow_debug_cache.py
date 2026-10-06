"""Bounded, process-local memoization of verified partial inspection traces.

Identity is supplied by the execution adapter, not by the client. Every hit
rechecks it; production, failed and untrained runs are never memoized.
"""
from collections import OrderedDict
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import threading
import time


def file_sha256(file):
    path=Path(file)
    if path.is_symlink():raise ValueError('linked cache input')
    digest=hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda:stream.read(1024*1024),b''):digest.update(chunk)
    return digest.hexdigest()


class DebugRunCache:
    def __init__(self, *, max_bytes=32*1024*1024, max_entries=16):
        self.max_bytes=max_bytes;self.max_entries=max_entries
        self._rows=OrderedDict();self._size=0;self._lock=threading.RLock()

    @staticmethod
    def _key(identity):
        return hashlib.sha256(json.dumps(identity(),sort_keys=True,separators=(',',':'),
            ensure_ascii=False,allow_nan=False).encode()).hexdigest()

    def execute(self, identity, run):
        started=time.perf_counter();key=self._key(identity)
        with self._lock:
            row=self._rows.get(key)
            if row is not None:
                self._rows.move_to_end(key);cached=deepcopy(row[0])
            else:cached=None
        if cached is not None and self._key(identity)==key:
            original=cached.get('total_latency_ms',0)
            for step in cached.get('execution_steps',[]):step['latency_ms']=0
            cached['total_latency_ms']=round((time.perf_counter()-started)*1000,2)
            cached['debug_cache']={'status':'hit','identity_sha256':key,'original_latency_ms':original}
            return cached
        result=run()
        stable=self._key(identity)==key
        eligible=(stable and result.get('status')=='partial' and result.get('final_verdict')=='REVIEW'
            and result.get('is_ok') is False and not result.get('error_message')
            and all(s.get('status') not in ('error','warning_untrained') for s in result.get('execution_steps',[])))
        # Check serialized preview size before making a second in-memory copy.
        size=0
        if eligible:
            for part in json.JSONEncoder(ensure_ascii=False,allow_nan=False).iterencode(result):
                size+=len(part.encode())
                if size>self.max_bytes:eligible=False;break
        if eligible and self.max_entries>0:
            with self._lock:
                old=self._rows.pop(key,None)
                if old:self._size-=old[1]
                self._rows[key]=(deepcopy(result),size);self._size+=size
                while self._size>self.max_bytes or len(self._rows)>self.max_entries:
                    _,(_,removed)=self._rows.popitem(last=False);self._size-=removed
        result['debug_cache']={'status':'miss' if eligible else 'bypassed','identity_sha256':key}
        return result


def checkpoint_files(checkpoints):
    """Checkpoint and provenance/config sidecars; absence is an identity too."""
    files={}
    for checkpoint in checkpoints.values():
        path=Path(checkpoint)
        for file in [path,*(path.parent/name for name in
                ('job_config.json','training_config.json','model_meta.json','job_receipt.json','remote_job.json','remote_artifacts.json','artifact_manifest.json'))]:
            files[file]=file_sha256(file) if file.exists() or file.is_symlink() else None
    return files


def check_checkpoint_files(files):
    return all((file_sha256(file) if file.exists() or file.is_symlink() else None)==expected
               for file,expected in files.items())


def local_debug_run(cache, engine, pipeline, image_path, image_id, stop_node_id, project, checkpoints):
    from backend.engine.flowchart_engine import debug_ancestor_ids
    from backend.engine.model_runtime import active_model_runtime
    run=lambda:engine.execute(pipeline=pipeline,image_path=image_path,image_id=image_id,stop_node_id=stop_node_id)
    if not project or not stop_node_id or engine.device.type!='cpu' or active_model_runtime.get() is not None:
        return run()
    scope=debug_ancestor_ids(pipeline,stop_node_id)
    # External fixtures/calibration/preprocessing and specialized adapters have
    # additional dependencies. Until bound here they always execute afresh.
    if any(node.data.node_type not in ('input','fixed_roi','patch_split','inspection','aggregate','decision','output')
        or (node.data.node_type=='inspection' and node.data.task not in ('classification','segmentation'))
        for node in pipeline.nodes if node.id in scope):
        return {**run(),'debug_cache':{'status':'bypassed'}}
    def identity():
        try:
            files=checkpoint_files(checkpoints);input_sha=file_sha256(image_path)
        except (OSError,ValueError) as exc:raise ValueError('Debug input or model identity is unavailable; run again with intact files') from exc
        return {'schema':1,'pipeline':pipeline.model_dump(),'stop_node_id':stop_node_id,
            'input_path':str(Path(image_path).resolve()),'input_sha256':input_sha,
            'image_id':image_id,'device':str(engine.device),'slots':engine.execution_resources()['engine_device_capacity'],
            'context':{key:project.get(key) for key in ('id','project_dir','models_dir','source_dataset_dir','active_labelset_id','task')},
            'models':[(job,task,str(path),files[Path(path)]) for (job,task),path in sorted(checkpoints.items())],
            'model_files':{str(file):sha for file,sha in files.items()}}
    return cache.execute(identity,run)
