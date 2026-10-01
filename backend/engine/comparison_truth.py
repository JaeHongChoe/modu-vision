"""Resolve comparison truth from agreed model semantics and reviewed declarations."""
from __future__ import annotations
import json
from pathlib import Path

from backend.engine import dataset_metadata as dm, image_truth
from backend.engine.class_semantics import class_semantics_record, recorded_roles
from backend.engine.evaluation_history import binary_verdict


def bind_truth(project, source, task, models, images):
    classes=[];roles={};explicit=False;metadata_hashes={}
    for model in models:
        checkpoint=Path(model['checkpoint_path']);metadata_file=checkpoint.parent/'model_meta.json'
        metadata=json.loads(metadata_file.read_text())
        metadata_hashes[str(metadata_file)]=dm._hash(metadata_file)
        names=metadata.get('classes') or metadata.get('class_names') or []
        model_roles=None
        if model['task'] in {'classification','segmentation','patch_classification','anomaly'}:
            import torch
            payload=torch.load(checkpoint,map_location='cpu',weights_only=True)
            payload_names=payload.get('classes') or payload.get('class_names') or names
            payload_roles=recorded_roles(payload,task=model['task'],classes=payload_names)
            if names and payload_names and names!=payload_names:
                raise ValueError('Comparison checkpoint and metadata class vocabulary differ')
            names=payload_names
            model_roles=recorded_roles(metadata,task=model['task'],classes=names)
            if model_roles is not None and class_semantics_record(names,model_roles,task=model['task'])['roles']!=class_semantics_record(names,payload_roles,task=model['task'])['roles']:
                raise ValueError('Comparison checkpoint and metadata class roles differ')
            model_roles=payload_roles
        if not names:
            from backend.engine.flow_workspace import catalog_class_vocabulary
            names=catalog_class_vocabulary(metadata).get('class_names') or []
        if model['task'] not in {'classification','segmentation','patch_classification','anomaly'}:
            model_roles=recorded_roles(metadata,task=model['task'],classes=names)
        resolved=class_semantics_record(names,model_roles,task=model['task'])
        explicit=explicit or model_roles is not None
        for name,role in resolved['roles'].items():
            if name in roles and roles[name]!=role:
                raise ValueError('Comparison models have conflicting class roles: '+name)
            if name not in classes:classes.append(name)
            roles[name]=role
    # Legacy metadata with no vocabulary remains usable with its alias truth.
    if not classes:return None
    truth_project={**project,'source_dataset_dir':str(source)}
    tasks=sorted({model['task'] for model in models})
    mixed=len(tasks)>1
    scope_task='mixed' if mixed else tasks[0]
    scope_roles=roles if explicit or mixed else None
    scope=image_truth.truth_scope(truth_project,scope_task,classes,scope_roles)
    bindings=[]
    for row in images:
        current=image_truth.read_truth(truth_project,row['file_path'],task=scope_task,classes=classes,
                                      class_roles=scope_roles,participating_tasks=tasks if mixed else None)
        # An intentional UNKNOWN declaration or invalidated declaration takes
        # precedence over any descriptive folder/annotation label.
        reviewed=current['declaration'] is not None or current.get('has_prior_scoped_declarations',False)
        row['ground_truth_verdict']=(None if current['verdict']=='UNKNOWN' else current['verdict']) if reviewed else binary_verdict(row.get('ground_truth_label'),roles)
        row['truth_sha256']=current['truth_sha256']
        row['truth_scope']=scope
        row['truth_basis']='reviewed_declaration' if reviewed else 'class_label'
        bindings.append({'file_path':row['file_path'],'truth_sha256':current['truth_sha256']})
    binding={'scope':scope,'images':bindings,'metadata_sha256':metadata_hashes}
    if mixed:binding['participating_tasks']=tasks
    return binding


def verify_truth(project, source, binding):
    if binding is None:return
    scope=binding['scope'];roles=scope['class_semantics']['roles'] if any(value=='explicit' for value in scope['class_semantics']['basis'].values()) else None
    for path,checksum in binding['metadata_sha256'].items():
        if dm._hash(Path(path))!=checksum:raise ValueError('Comparison model metadata changed during execution')
    for row in binding['images']:
        current=image_truth.read_truth({**project,'source_dataset_dir':str(source)},row['file_path'],task=scope['task'],
                                      classes=scope['classes'],class_roles=roles,participating_tasks=binding.get('participating_tasks'))
        if current['truth_sha256']!=row['truth_sha256']:raise ValueError('Comparison reviewed truth changed during execution')
