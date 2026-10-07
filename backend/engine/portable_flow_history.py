"""Read-only original portable reports, independent of any training ledger parent.

Each archive retains its original model-list digest and selected compute profile.
No model is loaded, worker adopted, connection opened or quality decision made.
Registered projects stay at their original path through owned control cutover.
"""
import hashlib
import json
from pathlib import Path
import re


def _digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def _parent(journal):
    from backend.remote.profiles import ComputeProfile
    from backend.engine.flowchart_engine import FlowchartPipeline, ordered_linear_nodes, debug_ancestor_ids
    from backend.engine.specialized_models import flow_model_task, valid_flow_job, FLOW_TASKS
    spec=journal.get('spec')
    if not isinstance(spec,dict) or spec.get('operation')!='flowchart_run' or spec.get('portable_models') is not True:
        raise ValueError('Independent report must retain an original portable flow specification')
    models=spec.get('models')
    if not isinstance(models,list) or len(models)>24:
        raise ValueError('Portable flow model inventory is missing or unbounded')
    fields={'job_id','task','checkpoint_path','checkpoint_sha256','checkpoint_size','metadata_path','metadata_sha256','metadata_size'}
    seen=set();total=0
    for row in models:
        if not isinstance(row,dict) or set(row)!=fields:
            raise ValueError('Portable model reference fields differ')
        job=row['job_id'];task=row['task']
        if not isinstance(task,str) or task not in FLOW_TASKS or not valid_flow_job(job,task) or job in seen:
            raise ValueError('Portable model identity is invalid or repeated')
        seen.add(job)
        prefix='inputs/models/'+job
        for name,limit,filename in [('checkpoint',512*1024*1024,'best_model.pt'),('metadata',4*1024*1024,'model_meta.json')]:
            if (row[name+'_path']!=prefix+'/'+filename or type(row[name+'_size']) is not int
                    or not 0<row[name+'_size']<=limit or not isinstance(row[name+'_sha256'],str)
                    or not re.fullmatch('[0-9a-f]{64}',row[name+'_sha256'])):
                raise ValueError('Portable model artifact binding differs or exceeds limits')
            total+=row[name+'_size']
    if total>2*1024*1024*1024 or models!=sorted(models,key=lambda row:(row['job_id'],row['task'])):
        raise ValueError('Portable model inventory is excessive or noncanonical')
    binding=_digest(models);job='job_flow_'+binding[:24]
    if spec.get('input_manifest_sha256')!=binding or spec.get('job_id')!=job or spec.get('task')!=(models[0]['task'] if models else 'classification'):
        raise ValueError('Portable flow synthetic parent differs from its original model binding')
    profile=ComputeProfile.model_validate(journal.get('profile'))
    if spec.get('execution_profile_sha256')!=_digest(profile.model_dump()):
        raise ValueError('Portable selected profile differs from its original execution binding')
    graph=FlowchartPipeline.model_validate(spec.get('pipeline'));stop=spec.get('stop_node_id')
    if stop is not None and (not isinstance(stop,str) or not stop):raise ValueError('Portable debug stop is invalid')
    scope=debug_ancestor_ids(graph,stop)
    needed={(node.data.model_job_id,flow_model_task(node)) for node in ordered_linear_nodes(graph)
        if node.id in scope and flow_model_task(node)}
    if needed!={(row['job_id'],row['task']) for row in models} or (not stop and not models):
        raise ValueError('Portable models differ from the original selected flow graph')
    if (spec.get('image_path') not in {'inputs/image'+suffix for suffix in ('.png','.jpg','.jpeg','.bmp','.tif','.tiff','.webp')}
            or not isinstance(spec.get('image_sha256'),str) or not re.fullmatch('[0-9a-f]{64}',spec['image_sha256'])
            or not isinstance(spec.get('device'),str) or not re.fullmatch(r'cpu|mps|cuda(?::[0-9]+)?',spec['device'])):
        raise ValueError('Portable selected image or execution device binding is invalid')
    if 'comparison_binding_sha256' in spec or 'comparison_operation_contract' in spec:
        if (type(spec.get('comparison_operation_contract')) is not int or spec['comparison_operation_contract']!=1
                or not isinstance(spec.get('comparison_binding_sha256'),str)
                or not re.fullmatch('[0-9a-f]{64}',spec['comparison_binding_sha256'])):
            raise ValueError('Portable comparison contract is invalid')
    return dict(job_id=job,task=spec['task'],input_manifest_sha256=binding,profile=profile.model_dump())


def validate_project(root,directory,workspace,project_id):
    from backend.engine.terminal_runtime_history import _read
    from backend.engine.terminal_operation_history import validate_operations
    root=Path(root).absolute();directory=Path(directory)
    if (not directory.is_absolute() or not directory.is_relative_to(root) or not directory.resolve().is_relative_to(root)
            or any(p.is_symlink() for p in (directory,*directory.parents)) or not directory.is_dir()):
        raise ValueError('Portable report project must remain in the original owned installation')
    output=directory/'reports/remote_flow'
    if not output.exists() and not output.is_symlink():return
    project,_=_read(root,directory/'project.json')
    if (project.get('id')!=project_id or project.get('workspace_id',workspace)!=workspace
            or project.get('project_dir')!=str(directory) or project.get('models_dir')!=str(directory/'models')
            or project.get('reports_dir')!=str(directory/'reports')):
        raise ValueError('Portable report project differs from its original registered namespace/storage')
    if not output.is_dir() or any(p.is_symlink() for p in (output,output.parent)):
        raise ValueError('Portable report storage is linked or unavailable')
    for path,journal in validate_operations(root,output,_parent):
        if journal['state']!='completed':continue
        spec=journal['spec'];result,_=_read(root,path/'outputs/flowchart_result.json')
        if (result.get('image_path')!=spec['image_path'] or result.get('image_sha256')!=spec['image_sha256']
                or result.get('model_job_ids')!=sorted(row['job_id'] for row in spec['models'])
                or result.get('execution_device')!=spec['device'] or not isinstance(result.get('device_name'),str) or not result['device_name']
                or (spec.get('stop_node_id') and (result.get('stop_node_id')!=spec['stop_node_id'] or result.get('status')!='partial'))
                or (spec.get('comparison_binding_sha256') and result.get('comparison_binding_sha256')!=spec['comparison_binding_sha256'])):
            raise ValueError('Portable archived result differs from its original execution binding')


def project_blockers(original,locations):
    if len(locations)>1000:return ['Portable history exceeds the bounded 1000-project conversion']
    errors=[]
    for key,workspace,project,path in locations:
        try:validate_project(original,path,workspace,project)
        except (ValueError,KeyError,TypeError,OSError) as exc:errors.append('Portable report '+str(key)+': '+str(exc))
    return errors
