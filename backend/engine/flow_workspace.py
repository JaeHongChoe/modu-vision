"""Immutable user templates and fixed-input flow result comparisons."""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import tempfile
import uuid
from backend.engine.flowchart_engine import FlowchartPipeline, ordered_linear_nodes
from backend.engine.specialized_models import flow_model_task


def _digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()


def catalog_class_vocabulary(row):
    """Expose recorded class identity; absence stays unknown for legacy models."""
    metadata=row.get('metadata') if isinstance(row.get('metadata'),dict) else {}
    value={**metadata,**row}
    names=value.get('class_names')
    if names is None:names=value.get('classes')
    if names is None and isinstance(value.get('class_name'),str):names=[value['class_name']]
    result={}
    if isinstance(names,list) and all(isinstance(name,str) and name for name in names):
        names=list(names)
        if value.get('task')=='detection':
            from backend.engine.detection.model import foreground_class_names
            names=foreground_class_names(names)
        result['class_names']=names
    ids=value.get('class_ids')
    if ids is None and 'class_names' in result:
        start=1 if value.get('task')=='detection' else 0
        ids=list(range(start,start+len(names)))
    if isinstance(ids,list) and all(type(i) is int and i>=0 for i in ids):result['class_ids']=list(ids)
    return result


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True,exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w',encoding='utf-8',dir=path.parent,delete=False) as f:
            temporary=Path(f.name)
            json.dump(value,f,ensure_ascii=False,indent=2)
            f.flush(); os.fsync(f.fileno())
        os.replace(temporary,path)
    finally:
        if temporary: temporary.unlink(missing_ok=True)


def _class_requirements(value):
    names=set(); ids=set()
    def visit(v):
        if isinstance(v,list):
            for child in v: visit(child)
        elif isinstance(v,dict):
            for key, child in v.items():
                if key=='class_name' and isinstance(child,str): names.add(child)
                elif key=='class_names' and isinstance(child,list): names.update(str(n) for n in child)
                elif key=='class_id' and type(child) is int: ids.add(child)
                elif key=='class_ids' and isinstance(child,list): ids.update(child)
                else: visit(child)
    visit(value)
    return {'names':sorted(names),'ids':sorted(ids)}


def save_template(root, pipeline, *, name, project_id, node_ids=None):
    if not isinstance(name,str) or not 1<=len(name.strip())<=120: raise ValueError('Template name is required (1-120 characters)')
    ordered_linear_nodes(pipeline)
    ids={node.id for node in pipeline.nodes}
    selected=set(node_ids or ids)
    if not selected or not selected<=ids: raise ValueError('Template nodes are invalid')
    full=selected==ids
    if not full and any(n.data.node_type in ('input','decision','output') for n in pipeline.nodes if n.id in selected):
        raise ValueError('Subgraph templates contain processing nodes only')
    graph=pipeline.model_dump()
    graph['nodes']=[n for n in graph['nodes'] if n['id'] in selected]
    graph['edges']=[e for e in graph['edges'] if e['source'] in selected and e['target'] in selected]
    models=[{'node_id':n.id,'name':n.data.label,'task':flow_model_task(n)} for n in pipeline.nodes if n.id in selected and flow_model_task(n)]
    # Source checkpoints are never silently reused when importing a template.
    for n in graph['nodes']: n['data']['model_job_id']=None
    record={'schema_version':1,'template_id':uuid.uuid4().hex,'name':name.strip(),'created_at':datetime.now(timezone.utc).isoformat(),
        'project_id':project_id,'kind':'flow' if full else 'subgraph','pipeline':graph,'models':models,
        'classes':_class_requirements(graph),'classes_by_node':{n['id']:_class_requirements([n['data']]+[e.get('predicate') for e in graph['edges'] if e['source']==n['id']]) for n in graph['nodes']},'ports':{
            'inputs':[{'node_id':e.target,'payload_type':e.payload_type or 'image'} for e in pipeline.edges if e.target in selected and e.source not in selected],
            'outputs':[{'node_id':e.source,'payload_type':e.payload_type or 'result'} for e in pipeline.edges if e.source in selected and e.target not in selected]}}
    record['sha256']=_digest(record)
    atomic_json(Path(root)/f"{record['template_id']}.json",record)
    return record


def load_templates(root):
    result=[]
    for p in Path(root).glob('*.json'):
        try:
            record=json.loads(p.read_text())
            digest=record.pop('sha256')
            if _digest(record)!=digest or record.get('schema_version')!=1: continue
            record['sha256']=digest
            result.append(record)
        except (OSError,ValueError,KeyError,TypeError): continue
    return sorted(result,key=lambda r:r['created_at'],reverse=True)


def map_template(record, model_mapping, class_mapping, catalog):
    graph=deepcopy(record['pipeline'])
    by_id={row['job_id']:row for row in catalog}
    for required in record['models']:
        job=model_mapping.get(required['node_id'])
        if not job: raise ValueError(f"Explicit model mapping required for {required['name']}")
        if job not in by_id or by_id[job]['task']!=required['task']: raise ValueError('Template model mapping is not compatible with the target project')
        next(n for n in graph['nodes'] if n['id']==required['node_id'])['data']['model_job_id']=job
    requirements = record.get('classes_by_node') or {
        n['id']: _class_requirements([n['data']] + [e.get('predicate') for e in graph['edges'] if e['source']==n['id']])
        for n in graph['nodes']
    }
    model_nodes={required['node_id']:by_id[model_mapping[required['node_id']]] for required in record['models']}
    def target_vocabulary(scope):
        pending=[scope];seen=set();vocabularies=[]
        while pending:
            node_id=pending.pop()
            if node_id in seen:continue
            seen.add(node_id)
            if node_id in model_nodes:
                vocabularies.append(catalog_class_vocabulary(model_nodes[node_id]))
            else:pending.extend(e['source'] for e in graph['edges'] if e['target']==node_id)
        return vocabularies
    def binding(scope, kind, value):
        keys = [f'{scope}:{kind}:{value}', f'{kind}:{value}']
        # Legacy unqualified mappings remain readable only when a name/ID
        # collision cannot make the intended class ambiguous.
        all_names = record.get('classes',{}).get('names',[])
        all_ids = record.get('classes',{}).get('ids',[])
        if not (str(value) in all_names and str(value) in {str(i) for i in all_ids}):
            keys.append(str(value))
        for key in keys:
            if key in class_mapping and class_mapping[key] not in ('',None):
                mapped=str(class_mapping[key]) if kind=='name' else int(class_mapping[key])
                field='class_names' if kind=='name' else 'class_ids'
                known=[v[field] for v in target_vocabulary(scope) if field in v]
                if known and not any(mapped in values for values in known):
                    raise ValueError(f'Target class {kind}:{mapped} is not recorded by the mapped model for {scope}')
                return mapped
        raise ValueError(f'Explicit class mapping required for {scope} {kind}:{value}')
    for scope, classes in requirements.items():
        for name in classes.get('names',[]): binding(scope,'name',name)
        for class_id in classes.get('ids',[]): binding(scope,'id',class_id)
    def transform(v, scope):
        if isinstance(v,list): return [transform(c,scope) for c in v]
        if not isinstance(v,dict): return v
        mapped={}
        for key,child in v.items():
            if key=='class_name': mapped[key]=binding(scope,'name',child)
            elif key=='class_names': mapped[key]=[binding(scope,'name',x) for x in child]
            elif key=='class_id': mapped[key]=binding(scope,'id',child)
            elif key=='class_ids': mapped[key]=[binding(scope,'id',x) for x in child]
            else: mapped[key]=transform(child,scope)
        return mapped
    for n in graph['nodes']:
        n['data']=transform(n['data'],n['id'])
        target=model_nodes.get(n['id'])
        if target and target['task']=='segmentation' and n['data'].get('params',{}).get('class_names'):
            vocabulary=catalog_class_vocabulary(target)
            if 'class_names' in vocabulary:
                # This list declares checkpoint channel order, unlike class_ids
                # and rules selecting a semantically mapped source class.
                n['data']['params']['class_names']=vocabulary['class_names']
    for e in graph['edges']:
        if e.get('predicate'): e['predicate']=transform(e['predicate'],e['source'])
    pipeline=FlowchartPipeline.model_validate(graph)
    if record['kind']=='flow': ordered_linear_nodes(pipeline)
    return pipeline


def compare_results(a,b):
    def rois(r): return sorted((str(c.get('roi_id')),c.get('bbox'),c.get('verdict')) for c in r.get('crops',[]))
    def steps(r): return {s['node_id']:{k:s.get(k) for k in ('status','input_count','output_count','branch_verdict','skip_reason','selected_edge_ids')} for s in r.get('execution_steps',[])}
    aa,bb=steps(a),steps(b)
    return {'verdict_changed':a.get('final_verdict')!=b.get('final_verdict'),'roi_changed':rois(a)!=rois(b),
        'changed_nodes':sorted(k for k in aa.keys()|bb.keys() if aa.get(k)!=bb.get(k)),
        'verdict_a':a.get('final_verdict'),'verdict_b':b.get('final_verdict'),
        'reason_a':a.get('rejection_reason'),'reason_b':b.get('rejection_reason')}
