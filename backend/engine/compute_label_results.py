"""Bind verified remote foundation results to existing human review proposals."""
from __future__ import annotations
import copy
import hashlib
import json
import time
from pathlib import Path
from uuid import uuid4
from fastapi import HTTPException


def verified_result(record):
    root=Path(record.output_dir)
    name='label_results.json' if (record.launch_spec or {}).get('operation')=='label' else 'model_meta.json'
    target=root/name;manifest_path=root/'remote_artifacts.json'
    if root.is_symlink() or target.is_symlink() or manifest_path.is_symlink() or not target.is_file() or not manifest_path.is_file():
        raise HTTPException(409,'Verified remote result receipt is unavailable')
    try:
        manifest=json.loads(manifest_path.read_text())
        row=next(row for row in manifest['artifacts'] if row['path']=='outputs/'+name)
        if (manifest.get('job_id')!=record.job_id or manifest.get('operation')!=(record.launch_spec or {}).get('operation','train')
                or target.stat().st_size!=row['size'] or hashlib.sha256(target.read_bytes()).hexdigest()!=row['sha256']):
            raise ValueError('Result bytes differ from the verified remote receipt')
        payload=json.loads(target.read_text())
        if name=='label_results.json' and (payload.get('job_id')!=record.job_id or payload.get('input_manifest_sha256')!=manifest.get('input_manifest_sha256') or payload.get('automatically_approved') is not False):
            raise ValueError('Remote label result identity changed')
        return payload,row['sha256']
    except (ValueError,KeyError,StopIteration,OSError) as exc:raise HTTPException(409,'Remote result receipt changed or is invalid') from exc


def capture_label_baseline(project,options):
    from backend.api import routes_label_suggestions as suggestions,routes_label_candidates as candidates
    from backend.engine.grouped_dataset_views import source_image_paths
    from backend.engine.annotation_storage import dataset_annotation_dir
    from backend.engine.dataset_metadata import metadata_for_path
    from backend.engine.dicom_input import open_source_image
    if project['task'] not in {'detection','segmentation'}:raise ValueError('Region candidates require a detection or segmentation labeling project')
    allowed={'prompt','label','points','boxes','positive_examples','negative_examples','threshold','text_threshold','min_area','max_area','min_width','max_width','min_height','max_height','max_candidates','output_geometry','class_ids','image_paths'}
    if set(options)-allowed:raise ValueError('Unsupported remote labeling options')
    validated=candidates.CandidateRequest.model_validate({'backend':'foundation','image_path':'remote',**{k:v for k,v in options.items() if k!='image_paths'}})
    normalized={k:v for k,v in validated.model_dump().items() if k in allowed and k not in {'class_ids','image_paths'}}
    source=Path(project['source_dataset_dir']);fingerprint=suggestions._dataset_fingerprint(project)
    selected=options.get('image_paths')
    images=[suggestions._image_path(project,p) for p in selected] if selected else source_image_paths(source,project['task'])
    if not images or len(images)>5000:raise ValueError('Select between one and 5000 project images for remote labeling')
    if len(set(map(str,images)))!=len(images):raise ValueError('Remote labeling images must be unique')
    rows=[]
    for image in images:
        image=suggestions._image_path(project,str(image));metadata=metadata_for_path(Path(project['project_dir']),source,image,Path(project['annotations_dir']))
        studio=dataset_annotation_dir(image.parent,Path(project['annotations_dir']),use_scope=False)/f'{image.stem}.json'
        with open_source_image(image) as opened:width,height=opened.size
        rows.append({'image_path':str(image),'relative_path':image.relative_to(source).as_posix(),'image_sha256':suggestions._sha256(image),
                     'studio_sha256':suggestions._sha256(studio),'labelme_sha256':suggestions._sha256(image.with_suffix('.json')),
                     'image_uuid':metadata['image_uuid'],'image_revision':metadata['revision'],'image_width':width,'image_height':height})
    examples={}
    for group in ('positive_examples','negative_examples'):
        examples[group]=[]
        for example in normalized[group]:
            path=suggestions._image_path(project,example['image_path'])
            examples[group].append({**example,'image_path':str(path),'sha256':suggestions._sha256(path)})
        normalized[group]=[{**example,'image_path':Path(example['image_path']).relative_to(source).as_posix()} for example in normalized[group]]
    return {'project_id':project['id'],'task':project['task'],'source_dataset_dir':str(source),'labelset_id':project.get('active_labelset_id','default'),
            'dataset_fingerprint':fingerprint,'foundation_setup':candidates._setup(project),'images':rows,'examples':examples,
            'class_ids':validated.class_ids,'worker_options':normalized}


def import_label_proposals(project,record):
    from backend.api import routes_label_suggestions as suggestions
    from backend.api.routes_project import _write_json
    from backend.api.routes_dataset_versions import _VERSION_LOCK
    from backend.engine import labeling_tasks
    payload,result_hash=verified_result(record)
    baseline=(record.launch_spec or {}).get('label_baseline')
    if not baseline:raise HTTPException(409,'This remote job has no pinned human review baseline')
    if (baseline['project_id'],baseline['task'],baseline['source_dataset_dir'],baseline['labelset_id'])!=(project['id'],project['task'],project.get('source_dataset_dir'),project.get('active_labelset_id','default')):
        raise HTTPException(409,'Project source, task or label set changed')
    mapping=Path(record.output_dir)/'label_proposals.json'
    with _VERSION_LOCK,suggestions._REVIEW_LOCK:
        if mapping.is_symlink():raise HTTPException(409,'Invalid label import receipt')
        if mapping.is_file():
            prior=json.loads(mapping.read_text())
            if prior['result_sha256']!=result_hash:raise HTTPException(409,'Imported remote receipt changed')
            return {'batch_id':prior['batch_id'],'proposals':[suggestions._read_proposal(project,p) for p in prior['proposal_ids']]}
        if suggestions._dataset_fingerprint(project)!=baseline['dataset_fingerprint']:raise HTTPException(409,'Source images or labels changed; generate remote candidates again')
        from backend.api.routes_label_candidates import _setup
        if _setup(project)!=baseline['foundation_setup']:raise HTTPException(409,'Foundation provider setup changed')
        for group in baseline['examples'].values():
            if any(suggestions._sha256(Path(item['image_path']))!=item['sha256'] for item in group):raise HTTPException(409,'Image example changed')
        by_image={item['relative_path']:item for item in baseline['images']}
        results=payload.get('results')
        if not isinstance(results,list) or len(results)!=len(by_image) or {r.get('image_path') for r in results}!=set(by_image):raise HTTPException(409,'Remote candidates do not match selected images')
        batch_id='batch_'+uuid4().hex[:24];proposals=[];entries=[];created=time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())
        for result in results:
            original=by_image[result['image_path']]
            if result.get('image_sha256')!=original['image_sha256'] or result.get('review_state')!='pending':raise HTTPException(409,'Remote candidate source identity changed')
            identifier='suggestion_'+hashlib.sha256((record.job_id+'\0'+result['image_path']).encode()).hexdigest()[:24]
            candidates=copy.deepcopy(result.get('candidates'))
            if not isinstance(candidates,list):raise HTTPException(409,'Invalid remote candidate list')
            for index,candidate in enumerate(candidates,1):
                if not isinstance(candidate,dict) or not isinstance(candidate.get('annotation'),dict) or not isinstance(candidate.get('confidence'),(int,float)):raise HTTPException(409,'Invalid remote candidate geometry')
                candidate['id']=f'{identifier}_c{index}';candidate['annotation']['id']=candidate['id']
                label=candidate['annotation'].get('label');class_ids=baseline.get('class_ids',{})
                if label in class_ids:candidate['annotation']['category_id']=class_ids[label]
                candidate.setdefault('provenance',{})['remote_receipt']={'job_id':record.job_id,'result_sha256':result_hash,'input_manifest_sha256':payload['input_manifest_sha256']}
            options=baseline['worker_options']
            proposal={'id':identifier,'project_id':project['id'],'status':'pending','created_at':created,**{k:v for k,v in original.items() if k!='relative_path'},
                      'image_id':Path(original['image_path']).stem,'task':project['task'],'backend':'foundation','job_id':record.job_id,'batch_id':batch_id,
                      'threshold':options['threshold'],'candidates':candidates,'confidence':max((c['confidence'] for c in candidates),default=0),
                      'latency_ms':None,'accepted_candidate_ids':[],'backup_version_id':None,'labelset_id':baseline['labelset_id'],
                      'labelset_version':baseline['dataset_fingerprint'],'dataset_fingerprint':baseline['dataset_fingerprint'],
                      'foundation_setup':baseline['foundation_setup'],'positive_examples':baseline['examples']['positive_examples'],'negative_examples':baseline['examples']['negative_examples'],
                      'device':(record.launch_spec or {}).get('device','cpu'),'prompt':options['prompt'],'output_geometry':options['output_geometry'],
                      'support_limits':'Verified remote foundation candidates; review and explicitly adopt selected labels.','remote_result_sha256':result_hash}
            proposals.append(proposal);entries.append({'image_path':original['image_path'],'status':'generated' if candidates else 'zero_candidates','proposal_id':identifier,'candidate_count':len(candidates)})
        batch={'id':batch_id,'project_id':project['id'],'source_dataset_dir':project['source_dataset_dir'],'labelset_id':baseline['labelset_id'],
               'kind':'candidate_batch','status':'completed','created_at':created,'finished_at':created,'review_fingerprint':baseline['dataset_fingerprint'],
               'total':len(entries),'processed':len(entries),'generated':sum(bool(p['candidates']) for p in proposals),'failed':0,'zero_candidates':sum(not p['candidates'] for p in proposals),
               'proposals':[p['id'] for p in proposals],'entries':entries,'remote_job_id':record.job_id}
        for proposal in proposals:_write_json(suggestions._proposal_path(project,proposal['id']),proposal)
        labeling_tasks.write(project,batch)
        _write_json(mapping,{'job_id':record.job_id,'result_sha256':result_hash,'batch_id':batch_id,'proposal_ids':batch['proposals']})
        return {'batch_id':batch_id,'proposals':proposals}
