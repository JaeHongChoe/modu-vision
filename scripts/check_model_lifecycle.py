"""Offline LifecycleReceipt reference integrity; never execute or approve a model.

Only the declared dataset-version / completed-job / EvaluationHistory / saved
flow / flow-package formats are joined. Unsupported history and GAN adoption
remain pending. A passed record is not proof of reproduced execution, human
truth, representative quality, installed target or S7-02 acceptance.
"""
from pathlib import Path
import errno
import hashlib
import json
import math
import os
import re
import stat

FAMILIES=('classification','patch_classification','segmentation','detection','rotated_detection','ocr','rotation','anomaly','enhancement','defect_gan')
TRUTH=('class_labels','patch_class_labels','per_class_masks','boxes','oriented_boxes','text','angles','reviewed_normal_and_defect_truth','paired_reference_images','reviewed_generator_adoption')
STAGES=('dataset','labels','train','eval','flow_or_adoption','export','target')
HEX=re.compile(r'[0-9a-f]{64}\Z')
JOB=re.compile(r'job_[A-Za-z0-9][A-Za-z0-9_-]{0,119}\Z')
MAX_FILES=20_000
MAX_FILE_BYTES=2*1024**3
MAX_JSON_BYTES=2*1024**2
MAX_JSON_DEPTH=128
PARITY_TOLERANCE={'defect_score_abs':1e-4,'float_abs':1e-4,'raster_abs':1e-6}
PARITY_FIELDS=['final_verdict','roi_count','defective_roi_count','routed_output_node_id','rejection_reason','inspected_image_size','execution_resources','execution_steps','crops']

def canonical(value):
    return json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode()

class Gap(ValueError):
    def __init__(self,reason,state='refused'):
        super().__init__(reason);self.reason=reason;self.state=state

def need(condition,reason):
    if not condition:raise Gap(reason)

def shape(value,keys):
    if value is None:raise Gap('missing stage reference','pending')
    need(type(value) is dict and set(value)==set(keys),'LifecycleReceipt stage schema differs')
    return value

def relative(path):
    need(type(path) is str and path and '\\' not in path and not path.startswith('/'),'artifact path is not relative')
    need(all(part not in ('','..','.') for part in path.split('/')),'artifact path contains traversal or normalization')
    return path.split('/')

def _identity(s):
    return s.st_dev,s.st_ino,s.st_mode,s.st_size,s.st_mtime_ns,s.st_ctime_ns

def _unique_object(rows):
    result={}
    for key,value in rows:
        need(key not in result,'duplicate JSON object key');result[key]=value
    return result

def _read_pin(root,path,pin,parse=False):
    """Fresh full raw hash through retained no-follow directory descriptors."""
    parts=relative(path);shape(pin,('sha256','size'))
    need(type(pin['size']) is int and 0<=pin['size']<=MAX_FILE_BYTES,'artifact size pin is invalid')
    need(type(pin['sha256']) is str and HEX.fullmatch(pin['sha256']) is not None,'artifact digest pin is invalid')
    root=Path(root);need(root.is_absolute(),'artifact root must be absolute')
    ancestor_states=[]
    try:
        for ancestor in (*reversed(root.parents),root):
            s=ancestor.lstat();need(stat.S_ISDIR(s.st_mode),'artifact root ancestor is linked or not a directory')
            ancestor_states.append((ancestor,_identity(s)))
    except OSError as error:
        raise Gap('missing artifact root' if error.errno==errno.ENOENT else 'unsafe artifact root',
                  'pending' if error.errno==errno.ENOENT else 'refused') from error
    descriptors=[];entries=[]
    try:
        directory=os.open(root,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW);descriptors.append(directory)
        need(_identity(os.fstat(directory))==ancestor_states[-1][1],'artifact root was replaced')
        for part in parts[:-1]:
            child=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=directory)
            descriptors.append(child);entries.append((directory,part,child));directory=child
        fd=os.open(parts[-1],os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=directory);descriptors.append(fd)
        initial=os.fstat(fd);need(stat.S_ISREG(initial.st_mode),'artifact is not an original regular file')
        need(initial.st_size==pin['size'],'artifact size changed')
        if parse:need(initial.st_size<=MAX_JSON_BYTES,'JSON artifact exceeds bound')
        digest=hashlib.sha256();chunks=[];total=0
        while True:
            block=os.read(fd,1024*1024)
            if not block:break
            total+=len(block);need(total<=pin['size'],'artifact grew during read');digest.update(block)
            if parse:chunks.append(block)
        need(total==pin['size'] and digest.hexdigest()==pin['sha256'],'artifact raw bytes changed')
        need(_identity(initial)==_identity(os.fstat(fd))==_identity(os.stat(parts[-1],dir_fd=directory,follow_symlinks=False)),'artifact identity changed during read')
        for parent,name,child in entries:
            need(_identity(os.fstat(child))==_identity(os.stat(name,dir_fd=parent,follow_symlinks=False)),'artifact parent was replaced')
        for ancestor,identity in ancestor_states:need(_identity(ancestor.lstat())==identity,'artifact root namespace changed')
        if not parse:return None
        def nonfinite(value):raise Gap('nonfinite JSON number')
        try:
            value=json.loads(b''.join(chunks),object_pairs_hook=_unique_object,parse_constant=nonfinite)
        except RecursionError as error:
            raise Gap('JSON artifact nesting exceeds parser capacity') from error
        pending=[(value,0)]
        while pending:
            item,depth=pending.pop()
            need(depth<=MAX_JSON_DEPTH,'JSON artifact nesting exceeds the declared bound')
            if type(item) is dict:pending.extend((child,depth+1) for child in item.values())
            elif type(item) is list:pending.extend((child,depth+1) for child in item)
        return value
    except OSError as error:
        raise Gap('missing artifact' if error.errno==errno.ENOENT else 'unsafe or unreadable artifact',
                  'pending' if error.errno==errno.ENOENT else 'refused') from error
    finally:
        for fd in reversed(descriptors):os.close(fd)

def _graph_identities(graph):
    need(type(graph) is dict and type(graph.get('nodes')) is list and graph['nodes'],
         'saved graph node inventory is invalid')
    need(type(graph.get('edges')) is list,'saved graph edge inventory is invalid')
    nodes=set();edges={}
    for node in graph['nodes']:
        need(type(node) is dict and type(node.get('id')) is str and node['id'] and node['id'] not in nodes,
             'saved graph node identity is empty or duplicated')
        need(type(node.get('data')) is dict,'saved graph node data is not an object')
        if node['data'].get('node_type')=='preprocess':
            need(type(node['data'].get('params',{})) is dict,'saved preprocessing parameters are not an object')
        nodes.add(node['id'])
    for edge in graph['edges']:
        need(type(edge) is dict and type(edge.get('id')) is str and edge['id'] and edge['id'] not in edges,
             'saved graph edge identity is empty or duplicated')
        need(edge.get('source') in nodes and edge.get('target') in nodes,'saved graph edge refers to a foreign node')
        edges[edge['id']]=edge
    return nodes,edges

def _model_jobs(graph):
    _graph_identities(graph)
    jobs={};nodes={}
    for node in graph['nodes']:
        data=node['data'];kind=data.get('node_type');task=None
        if kind=='detection_crop':task='rotated_detection' if data.get('task')=='rotated_detection' else 'detection'
        elif kind=='inspection':task=data.get('task')
        elif kind=='preprocess' and data.get('params',{}).get('operation')=='enhancement':task='enhancement'
        elif kind=='preprocess' and data.get('params',{}).get('operation')=='learned_rotation':task='rotation'
        if task is None:continue
        job=data.get('model_job_id')
        need(task in FAMILIES[:-1] and type(job) is str and
             (JOB.fullmatch(job) or task in ('ocr','rotated_detection','enhancement','rotation') and re.fullmatch(r'[0-9a-f]{32}',job)), 'saved flow has invalid model reference')
        need(job not in jobs or jobs[job]==task,'saved flow has conflicting model tasks')
        jobs[job]=task;nodes.setdefault(job,set()).add(node['id'])
    return jobs,nodes

def _same_summary(left,right):
    """Compare the recorded _flow_evidence view at its declared float tolerance."""
    if type(left) is float or type(right) is float:
        return (type(left) in (int,float) and type(right) in (int,float) and
                math.isfinite(left) and math.isfinite(right) and abs(left-right)<=1e-4)
    if type(left) is not type(right):return False
    if type(left) is dict:return set(left)==set(right) and all(_same_summary(left[key],right[key]) for key in left)
    if type(left) is list:return len(left)==len(right) and all(_same_summary(a,b) for a,b in zip(left,right))
    return left==right

def verify_lifecycle(receipt,root):
    """Return integrity states only; no output writes, model loads or jobs."""
    report={'contract':'model_lifecycle_evidence_v1','state':'pending','record_chain_verified':False,
      'stages':{},'refusals':[],'prerequisites':[],'runtime_execution_reproduced':False,
      'human_truth_approved':False,'model_quality_approved':False,'target_execution_approved':False,'parent_accepted':False,
      'scope':'Declared durable record bytes and causal joins only; no runtime reproduction or human/quality/target approval; undeclared artifacts not checked'}
    context={};used=set();failures={}
    try:
        shape(receipt,('schema_version','family','dataset','labels','target','train','eval','flow_or_adoption','export','hashes'))
        need(type(receipt['schema_version']) is int and receipt['schema_version']==1,'LifecycleReceipt version differs')
        family=receipt['family'];need(family in FAMILIES,'unsupported model family')
        pins=receipt['hashes'];need(type(pins) is dict and 0<len(pins)<=MAX_FILES,'artifact pin inventory is invalid')
        for path,pin in pins.items():
            try:_read_pin(root,path,pin)
            except (Gap,ValueError,TypeError) as error:failures[path]=error
    except (Gap,KeyError,ValueError,TypeError) as error:
        report['refusals'].append({'stage':'schema','reason':str(error)})
        report['prerequisites'].append('a supported complete LifecycleReceipt schema and pin inventory');return report
    report['family']=family
    report['prerequisites'].append('human-reviewed representative '+TRUTH[FAMILIES.index(family)])
    report['prerequisites'].extend(('independent model-quality decision','target-specific runtime qualification'))
    def file(path,parse=True):
        if path is None:raise Gap('missing artifact reference','pending')
        relative(path);used.add(path)
        if path not in pins:raise Gap('referenced artifact is not pinned')
        if path in failures:
            error=failures[path]
            if isinstance(error,Gap):raise error
            raise Gap('artifact bytes or JSON are invalid') from error
        return _read_pin(root,path,pins[path],parse)
    def require(*stages):
        if any(report['stages'].get(stage,{}).get('state')!='verified' for stage in stages):raise Gap('required earlier record is unverified','pending')
    def stage(name,work):
        try:work();report['stages'][name]={'state':'verified','scope':'record integrity/control only'}
        except (Gap,KeyError,TypeError,ValueError,OverflowError) as error:
            state=error.state if isinstance(error,Gap) else 'pending'
            reason=error.reason if isinstance(error,Gap) else 'unsupported or malformed producer record'
            report['stages'][name]={'state':state,'reason':reason};report['prerequisites'].append(name+': '+reason)
            if state=='refused':report['refusals'].append({'stage':name,'reason':reason})
    def dataset():
        refs=shape(receipt['dataset'],('manifest','files'));manifest=file(refs['manifest'])
        need(type(manifest) is dict,'dataset manifest is not an object')
        if not all(key in manifest for key in ('project_id','id','source_dataset_dir','dataset_fingerprint','labelset_id','files','content_digest')):raise Gap('unsupported dataset version manifest','pending')
        need(type(manifest.get('schema_version')) is int and manifest['schema_version']==1,'dataset manifest version differs')
        need(all(type(manifest[key]) is str and manifest[key] for key in ('project_id','id','source_dataset_dir','labelset_id')),'dataset identity is empty or invalid')
        need(type(manifest['dataset_fingerprint']) is str and re.fullmatch(r'v1:[0-9a-f]{64}',manifest['dataset_fingerprint']) is not None,'dataset fingerprint domain is unsupported')
        need(manifest['content_digest']==hashlib.sha256(canonical({k:v for k,v in manifest.items() if k!='content_digest'})).hexdigest(),'dataset semantic digest changed')
        need(type(refs['files']) is dict and type(manifest['files']) is list and manifest['files'],'dataset file inventory missing')
        keys=set();images={}
        for row in manifest['files']:
            relative(row['relative_path'])
            key=row['origin']+':'+row['relative_path'];need(key not in keys,'duplicate dataset member');keys.add(key)
            path=refs['files'].get(key);file(path,False)
            need(pins[path]['sha256']==row['sha256'] and type(row['size_bytes']) is int and pins[path]['size']==row['size_bytes'],'dataset member differs')
            if row['kind']=='image':images[row['source_path']]=row['sha256']
        need(set(refs['files'])==keys and images,'dataset inventory is incomplete or extra')
        context.update(dataset=manifest,images=images)
    def labels():
        require('dataset');refs=shape(receipt['labels'],('receipt','kind'));body=file(refs['receipt']);manifest=context['dataset']
        need(refs['kind'] in ('synthetic_control','unqualified_truth'),'truth kind cannot mint human approval')
        need(body['scope']=={'project_id':manifest['project_id'],'source':manifest['source_dataset_dir'],'labelset_id':manifest['labelset_id']},'truth project/source/labelset differs')
        context['labels']=body;report['truth_kind']=refs['kind']
    def train():
        require('dataset','labels');refs=shape(receipt['train'],('receipt','metadata','checkpoint'))
        job=file(refs['receipt']);meta=file(refs['metadata']);file(refs['checkpoint'],False);manifest=context['dataset']
        required=('job_id','task','status','source_dataset_path','dataset_fingerprint','training_provenance','checkpoint_sha256')
        need(type(job) is dict and type(meta) is dict,'training receipt or metadata is not an object')
        if not all(key in job for key in required):raise Gap('unsupported completed training receipt','pending')
        need(job['status']=='completed' and job['task']==family,'training is incomplete or family differs')
        cp=pins[refs['checkpoint']]['sha256'];binding=job['training_provenance']
        need(type(binding) is dict,'training provenance is not an object')
        if binding.get('family_inputs'):raise Gap('prepared family input inventory format not yet supported','pending')
        need(job['checkpoint_sha256']==cp==meta['checkpoint_sha256'] and meta['task']==family,'checkpoint or metadata family differs')
        need(meta['training_provenance']==binding,'checkpoint metadata provenance differs')
        need(job['source_dataset_path']==manifest['source_dataset_dir'] and job['dataset_fingerprint']==manifest['dataset_fingerprint'],'training source differs')
        for key,expected in (('dataset_version_id',manifest['id']),('manifest_sha256',manifest['content_digest']),('dataset_fingerprint',manifest['dataset_fingerprint']),('labelset_id',manifest['labelset_id'])):
            need(binding[key]==expected,'training version/provenance differs')
        split_rows=[row['sha256'] for row in manifest['files'] if row['origin']=='split']
        split_digest=split_rows[0] if len(split_rows)==1 else hashlib.sha256(json.dumps(
            [{key:row[key] for key in ('origin','relative_path','sha256')} for row in manifest['files']],
            sort_keys=True,separators=(',',':')).encode()).hexdigest()
        if 'split_sha256' not in binding or 'split_binding' not in binding:
            raise Gap('legacy training receipt has no immutable split binding','pending')
        need(binding['split_sha256']==split_digest and
             binding['split_binding']==('saved_manifest' if split_rows else 'versioned_dataset_layout'),
             'training split differs from the original immutable manifest')
        need(binding['team_data']==context['labels'] and binding['team_data_sha256']==hashlib.sha256(canonical(context['labels'])).hexdigest(),'training truth receipt differs')
        context.update(job=job,checkpoint=cp,binding=binding)
    def evaluation():
        require('train');body=file(receipt['eval']);binding=body['binding'];manifest=context['dataset'];job=context['job']
        need(type(body['evaluation_id']) is str and re.fullmatch(r'evaluation_[0-9a-f]{32}',body['evaluation_id']) is not None,'evaluation identity is invalid')
        need(body['evidence_sha256']==hashlib.sha256(canonical({k:v for k,v in body.items() if k!='evidence_sha256'})).hexdigest(),'evaluation semantic digest differs')
        need(body['result']['job_id']==job['job_id'] and body['result']['task']==family,'evaluation job/family differs')
        for key,expected in (('source_dataset_path',manifest['source_dataset_dir']),('dataset_fingerprint',manifest['dataset_fingerprint']),('checkpoint_sha256',context['checkpoint']),('labelset_id',manifest['labelset_id']),('training_labelset_id',manifest['labelset_id']),('training_provenance',context['binding'])):
            need(binding[key]==expected,'evaluation source/checkpoint/truth provenance differs')
    def flow():
        if family=='defect_gan':raise Gap('GAN generation/adoption receipt format','pending')
        require('eval');refs=shape(receipt['flow_or_adoption'],('kind','graph'))
        if refs['kind']!='flow':raise Gap('unsupported adoption receipt format','pending')
        graph=file(refs['graph']);jobs,nodes=_model_jobs(graph);job=context['job']['job_id']
        need(jobs.get(job)==family,'saved flow does not reference the original trained job')
        graph_nodes,graph_edges=_graph_identities(graph)
        output_nodes={node['id'] for node in graph['nodes'] if node['data'].get('node_type')=='output'}
        context.update(graph=graph,jobs=jobs,selected_nodes=nodes[job],graph_nodes=graph_nodes,graph_edges=graph_edges,
                       output_nodes=output_nodes)
    def export():
        require('flow_or_adoption');refs=shape(receipt['export'],('manifest','parity'));manifest=file(refs['manifest'])
        need(type(manifest) is dict,'package manifest is not an object')
        need(type(manifest.get('schema_version')) is int and manifest['schema_version']==1,'package manifest version differs')
        need(manifest['pipeline_id']==context['graph']['id'],'package pipeline identity differs')
        prefix='/'.join(relative(refs['manifest'])[:-1]);prefix=(prefix+'/') if prefix else ''
        members={}
        for row in manifest['files']:
            shape(row,('path','size','sha256'));relative(row['path']);need(row['path'] not in members,'duplicate package member')
            path=prefix+row['path'];file(path,False);need(pins[path]=={'size':row['size'],'sha256':row['sha256']},'package member pin differs');members[row['path']]=path
        need(file(members['pipeline.json'])==context['graph'],'saved and packaged graph bodies differ')
        models={}
        for row in manifest['models']:
            shape(row,('job_id','task','checkpoint'));need(row['job_id'] not in models,'duplicate package model')
            need(row['checkpoint']=='models/'+row['job_id']+'/best_model.pt','package checkpoint path differs')
            models[row['job_id']]=row['task']
        need(models==context['jobs'],'package model set differs from saved flow')
        checkpoint_map={row['job_id']:pins[members[row['checkpoint']]]['sha256'] for row in manifest['models']}
        need(checkpoint_map[context['job']['job_id']]==context['checkpoint'],'packaged checkpoint differs from training')
        parity=file(refs['parity']);need(parity['contract']=='flow_parity_v1' and parity['status']=='passed' and parity['error'] is None,'package parity did not pass')
        need(parity['tolerance']==PARITY_TOLERANCE and parity['compared_fields']==PARITY_FIELDS,'parity comparison contract differs')
        reference,packaged=parity['reference_runtime'],parity['packaged_runtime']
        need(reference['kind']=='in_process_app_engine' and reference['device']=='cpu' and HEX.fullmatch(reference['engine_sha256']) is not None,'reference runtime declaration differs')
        need(packaged['kind'] in ('isolated_python_runner','frozen_package_dispatcher') and packaged['independent_process'] is True,'packaged runtime declaration differs')
        for field,path in (('package_runner_sha256','run_flow.py'),('package_runtime_sha256','backend/engine/flow_package_runtime.py')):
            need(packaged[field]==pins[members[path]]['sha256'],'packaged runtime bytes differ')
        need(parity['manifest_sha256']==pins[refs['manifest']]['sha256'] and parity['graph_sha256']==pins[members['pipeline.json']]['sha256'] and parity['checkpoints']==checkpoint_map,'parity package/graph/checkpoint identity differs')
        need(parity['mismatched_fields']==[],'parity records mismatches')
        rows=parity['images'];need(type(rows) is list and rows,'parity has no completed input evidence')
        need(type(parity['image_count']) is int and type(parity['completed_count']) is int and len(rows)==parity['image_count']==parity['completed_count'],'parity input/completion counts differ')
        need(parity['scope'] in ('single_image','cohort') and (len(rows)==1 if parity['scope']=='single_image' else 2<=len(rows)<=64),'parity scope/cardinality differs')
        seen=set();selected=False
        for index,row in enumerate(rows):
            need(type(row['index']) is int and row['index']==index and row['image_path'] not in seen,'parity input index/identity duplicated')
            seen.add(row['image_path']);need(context['images'].get(row['image_path'])==row['image_sha256'],'parity input belongs to another source')
            need(row['status']=='passed' and row['mismatched_fields']==[],'parity input did not pass')
            left,right=row['reference'],row['packaged'];need(type(left) is dict and set(('final_verdict','routed_output_node_id','roi_count','branch_path','rois'))<=set(left),'unsupported flow evidence view')
            need(_same_summary(left,right),'recorded app/package outcomes differ')
            routed=left['routed_output_node_id']
            need(routed is None or type(routed) is str and routed in context['output_nodes'],
                 'parity routed output refers to a foreign saved output node')
            need(type(left['branch_path']) is list,'parity branch inventory is invalid')
            branch_nodes=set()
            for step in left['branch_path']:
                need(type(step) is dict and step.get('node_id') in context['graph_nodes'],'parity branch refers to a foreign saved node')
                need(step['node_id'] not in branch_nodes,'parity branch repeats a saved node identity')
                branch_nodes.add(step['node_id'])
                selected_edges=step.get('selected_edge_ids')
                need(type(selected_edges) is list and all(type(edge) is str and edge in context['graph_edges'] and
                    context['graph_edges'][edge]['source']==step['node_id'] for edge in selected_edges),
                    'parity branch refers to a foreign saved edge')
                need(len(selected_edges)==len(set(selected_edges)),'parity branch repeats a saved edge identity')
            selected=selected or any(step['node_id'] in context['selected_nodes'] and step['status'] in ('passed','flagged_ng','review_required') for step in left['branch_path'])
        need(selected,'original trained model was never exercised by parity records')
        need(parity['cohort_sha256']==hashlib.sha256(canonical(sorted(row['image_sha256'] for row in rows))).hexdigest(),'parity cohort digest differs')
        context['parity']=parity;context['runtime']=manifest.get('runtime',{})
    def target():
        require('export');refs=shape(receipt['target'],('kind','device'))
        if refs['kind']!='source_cpu':raise Gap('target-specific runtime qualification unavailable','pending')
        need(type(context['runtime']) is dict,'recorded package runtime is not an object')
        need(refs['device']=='cpu'==context['parity']['device']==context['parity']['resolved_device']==context['parity']['package_runtime_device']==context['runtime'].get('device'),'recorded target device differs')
    for name,work in zip(STAGES,(dataset,labels,train,evaluation,flow,export,target)):stage(name,work)
    if used!=set(pins):report['refusals'].append({'stage':'inventory','reason':'unused or unreachable artifact references'})
    for path,pin in pins.items():
        try:_read_pin(root,path,pin)
        except (Gap,ValueError,TypeError) as error:report['refusals'].append({'stage':'final-readback','reason':str(error)})
    report['record_chain_verified']=all(report['stages'][name]['state']=='verified' for name in STAGES) and not report['refusals']
    report['state']='verified' if report['record_chain_verified'] else 'pending'
    return report

def main(argv=None):
    """Explicit read-only CLI; root must separately authorize original artifacts."""
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',required=True)
    parser.add_argument('--receipt',required=True,help='relative JSON wrapper path under root')
    parser.add_argument('--receipt-sha256',required=True)
    parser.add_argument('--receipt-size',required=True,type=int)
    args=parser.parse_args(argv)
    try:
        receipt=_read_pin(args.root,args.receipt,{'sha256':args.receipt_sha256,'size':args.receipt_size},True)
        result=verify_lifecycle(receipt,args.root)
    except (Gap,ValueError,TypeError) as error:
        result={'contract':'model_lifecycle_evidence_v1','state':'pending','record_chain_verified':False,'refusals':[{'stage':'input','reason':str(error)}]}
    print(json.dumps(result,ensure_ascii=False,sort_keys=True,allow_nan=False))
    return 0 if result['record_chain_verified'] else 1

if __name__=='__main__':
    raise SystemExit(main())
