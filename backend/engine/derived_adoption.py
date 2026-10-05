"""Reviewed derived edits join owned train-only branches; original truth stays scoped.

Branch records reuse the checked intake ancestry format, without creating service
jobs or capture candidates. Review is an edit acceptance, never a quality approval.
"""
from __future__ import annotations
import copy
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import uuid

from backend.engine import capture_intake as intake, data_workbench as dw, dataset_metadata as dm
from backend.engine.annotation_storage import dataset_annotation_dir
from backend.engine.flow_workspace import atomic_json
from backend.engine.image_truth import digest, image_binding


def read_review(directory, version):
    path=Path(directory)/'review.json'
    if not path.exists():return {'revision':0,'review':None,'history':[]}
    if path.is_symlink():raise ValueError('Derived review cannot be a symbolic link')
    record=json.loads(path.read_text(encoding='utf-8'))
    if record.get('record_sha256')!=digest({key:item for key,item in record.items() if key!='record_sha256'}):
        raise ValueError('Derived review record changed')
    if record.get('version_sha256')!=version['evidence_sha256']:raise ValueError('Derived review annotation/image binding changed')
    return record


def review(project, identifier, *, expected_revision, actor, decision, note=''):
    source=Path(project['source_dataset_dir']).resolve();scope={'task':project['task'],'labelset_id':project.get('active_labelset_id','default')}
    if decision not in {'approve','reject'} or not isinstance(actor,str) or not actor.strip() or len(actor)>100:
        raise ValueError('Enter an edit reviewer and approve/reject decision')
    if not isinstance(note,str) or not note.strip() or len(note)>2000:raise ValueError('Enter an edit review reason')
    with dm._file_lock(dw._storage(project['project_dir'],source)/'derived_review.lock'):
        version=dw.read_derived(project['project_dir'],source,identifier,scope)
        if not version.get('annotation_files_sha256'):raise ValueError('Legacy edit has no label-byte binding; create a new derived version before review')
        current=read_review(version['dataset_path'],version)
        if type(expected_revision) is not int or current['revision']!=expected_revision:raise ValueError('Derived review revision changed; reload')
        value={'decision':decision,'actor':actor.strip(),'note':note.strip(),'at':dm._now(),
               'derived_sha256':version['derived_sha256'],'annotation_files_sha256':version['annotation_files_sha256']}
        record={'revision':current['revision']+1,'review':value,'history':[*current['history'],value],
                'version_sha256':version['evidence_sha256']}
        record['record_sha256']=digest(record);atomic_json(Path(version['dataset_path'])/'review.json',record)
        return {**version,'review_revision':record['revision'],'review':value}


def _copy_overlays(project, source, branch, copied):
    from backend.engine.grouped_dataset_views import source_image_paths
    for parent in {image.parent for image in source_image_paths(source,project['task'],include_unused=True)}:
        old=dataset_annotation_dir(parent,Path(project['annotations_dir']),use_scope=False)
        new=dataset_annotation_dir(branch/parent.relative_to(source),Path(project['annotations_dir']),use_scope=False)
        if not old.is_dir():continue
        if old.is_symlink() or any(path.is_symlink() for path in old.rglob('*')):raise ValueError('Original label overlay contains symbolic links')
        new.mkdir(parents=True,exist_ok=False);copied.append(new)
        for path in old.rglob('*'):
            relative=path.relative_to(old)
            if not path.is_file() or relative.parts[0]=='metadata' or path.name.startswith('.'):continue
            target=new/relative;target.parent.mkdir(parents=True,exist_ok=True)
            if path.suffix=='.json':
                data=json.loads(path.read_text(encoding='utf-8'));mask=data.get('mask_file')
                if isinstance(mask,str) and Path(mask).is_relative_to(old):data['mask_file']=str(new/Path(mask).relative_to(old))
                atomic_json(target,data)
            else:shutil.copyfile(path,target)


def adopt(project, identifiers, *, actor, name):
    if not identifiers or len(identifiers)>1000 or len(set(identifiers))!=len(identifiers):raise ValueError('Choose unique derived versions')
    if not isinstance(actor,str) or not actor.strip() or len(actor)>100 or not isinstance(name,str) or not name.strip() or len(name)>200:
        raise ValueError('Enter reviewer and new dataset name')
    source=Path(project['source_dataset_dir']).resolve();root=intake._root(project);scope=intake._scope(project)
    edit_scope={'task':project['task'],'labelset_id':scope['labelset_id']}
    with dm._file_lock(root/'intake.lock'),dm._file_lock(dw._storage(project['project_dir'],source)/'derived_review.lock'):
        versions=[dw.read_derived(project['project_dir'],source,identifier,edit_scope) for identifier in identifiers]
        binding=intake._source_binding(project);files=intake._files(project);_,split=intake._split(project)
        heldout_hashes={row['sha256'] for row in files if split['assignments'].get(row['relative_path']) in {'val','test'}}
        if sum(row['size'] for row in files)+sum(Path(row['file_path']).stat().st_size for row in versions)>intake.COPY_LIMIT_BYTES:
            raise ValueError('Derived branch exceeds the 2 GiB copy limit')
        for row in versions:
            if not row.get('annotation_files_sha256') or not row.get('review') or row['review']['decision']!='approve':raise ValueError('Every derived image requires explicit edit review')
            if split['assignments'].get(row['source_relative_path'])!='train' or row['source_sha256'] in heldout_hashes:
                raise ValueError('Only train-origin edits without heldout byte overlap can enter training')
        identifier='intake_'+uuid.uuid4().hex;store=intake._owned(project,root/'versions');store.mkdir(exist_ok=True)
        directory=store/identifier;staging=Path(tempfile.mkdtemp(prefix='.derived-adopting-',dir=store));branch=directory/'source'
        copied=[];split_path=None;published=False
        try:
            for row in files:
                target=staging/'source'/row['relative_path'];target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source/row['relative_path'],target)
                if dm._hash(target)!=row['sha256']:raise ValueError('Original source changed during derived adoption')
            assignments=dict(split['assignments']);adopted=[]
            for version in versions:
                original=Path(version['source_relative_path']);relative=(original.parent/(original.stem+'__'+version['id']+'.png')).as_posix()
                target=staging/'source'/relative;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(version['file_path'],target)
                if dm._hash(target)!=version['derived_sha256']:raise ValueError('Derived source changed during adoption')
                assignments[relative]='train'
                adopted.append({'candidate_id':version['id'],'relative_path':relative,'source_sha256':version['derived_sha256'],
                    'truth_verdict':'UNKNOWN','usage_state':'not_used','workflow_state':'needs_review',
                    'review':{'decision':'adopt','actor':actor.strip(),'at':dm._now(),'edit_review':version['review']},
                    'origin':{'kind':'derived-edit','version_id':version['id'],'version_sha256':version['evidence_sha256'],
                              'original_relative_path':version['source_relative_path'],'original_sha256':version['source_sha256'],
                              'operation':version['operation'],'parent_id':version['parent_id']}})
            if intake._source_binding(project)!=binding:raise ValueError('Original labels/split/policy changed during derived adoption')
            staging.rename(directory)
            _copy_overlays(project,source,branch,copied)
            for version,row in zip(versions,adopted):
                image=branch/row['relative_path'];overlay=dataset_annotation_dir(image.parent,Path(project['annotations_dir']),use_scope=False)
                if overlay not in copied:
                    overlay.mkdir(parents=True,exist_ok=False);copied.append(overlay)
                mask=overlay/'masks'/f'{image.stem}.png';mask.parent.mkdir(exist_ok=True)
                shutil.copyfile(Path(version['dataset_path'])/'masks/derived.png',mask)
                labels={'image_id':image.stem,'annotations':version['annotations'],'image_width':version['size'][0],
                        'image_height':version['size'][1],'mask_file':str(mask)}
                atomic_json(overlay/f'{image.stem}.json',labels)
            split_path=intake._owned(project,Path(project['dataset_dir'])/'splits'/(hashlib.sha256(str(branch).encode()).hexdigest()+'.json'))
            atomic_json(split_path,{'folder_path':str(branch),'assignments':assignments,'seed':split.get('seed',42),'intake_version_id':identifier})
            with dm.metadata_transaction(project['project_dir'],source,project['annotations_dir']) as original_ledger:policy=copy.deepcopy(original_ledger.get('team_data',{}))
            from backend.engine.grouped_dataset_views import source_image_paths
            source_metadata={image.relative_to(source).as_posix():dm.metadata_for_path(project['project_dir'],source,image,project['annotations_dir']) for image in source_image_paths(source,project['task'],include_unused=True)}
            origin_groups={row['origin']['original_relative_path']:source_metadata[row['origin']['original_relative_path']].get('group') or 'derived:'+row['origin']['original_sha256'] for row in adopted}
            metadata_scope=dataset_annotation_dir(branch,Path(project['annotations_dir']),use_scope=False)
            if metadata_scope not in copied:copied.append(metadata_scope)
            with dm.metadata_transaction(project['project_dir'],branch,project['annotations_dir']) as ledger:
                if policy:ledger['team_data']=policy
                for relative,old in source_metadata.items():
                    item=dm._ensure(ledger,project['project_dir'],branch,branch/relative,project['annotations_dir'])
                    item.update({key:copy.deepcopy(old[key]) for key in ('product','lot','tags','group','usage_state')})
                    item['workflow_state']='needs_review'
                    if relative in origin_groups:item['group']=origin_groups[relative]
                for row in adopted:
                    item=dm._ensure(ledger,project['project_dir'],branch,branch/row['relative_path'],project['annotations_dir'])
                    parent=source_metadata[row['origin']['original_relative_path']]
                    item.update(product=parent['product'],lot=parent['lot'],tags=copy.deepcopy(parent['tags']),workflow_state='needs_review',usage_state='not_used',group=origin_groups[row['origin']['original_relative_path']],derived_edit={'version_id':identifier,**copy.deepcopy(row)})
                    dm._event(item,actor.strip(),'derived_adopted_pending_label_review',{'derived_version_id':row['candidate_id'],'usage_state':'not_used'})
            test_records=[row for row in files if split['assignments'].get(row['relative_path'])=='test']
            record={'schema_version':1,'kind':'derived-edits','version_id':identifier,'name':name.strip(),'created_at':dm._now(),'actor':actor.strip(),
                'scope':scope,'source_dataset_path':str(branch),'parent_source_dataset_path':str(source),'parent_source_binding':binding,
                'base_files':files,'adopted':adopted,'split_assignments':assignments,'fixed_test_records':test_records,'fixed_test_sha256':digest(test_records),
                'fixed_cohorts':intake._fixed_cohorts(project),'review_policy':policy,'review_policy_sha256':digest(policy),'activated':False,
                'training_readiness':'Explicit inclusion and normal label/review-policy approval are required; edit review is not truth or quality approval',
                'lineage':{'parent_source_dataset_path':str(source),'task':project['task'],'labelset_id':scope['labelset_id'],'parent_model_rule':'Exact immutable ancestor checkpoint and unchanged frozen test truth required'},
                'copied_label_bindings':[{'relative_path':image.relative_to(branch).as_posix(),'binding':image_binding(dm.metadata_for_path(project['project_dir'],branch,image,project['annotations_dir']))} for image in source_image_paths(branch,project['task'],include_unused=True)]}
            # Recheck every immutable derived file under the review lock before publishing.
            for version in versions:dw.read_derived(project['project_dir'],source,version['id'],edit_scope)
            if intake._source_binding(project)!=binding:raise ValueError('Original source changed before branch publication')
            record['record_sha256']=digest(record);atomic_json(directory/'record.json',record)
            index=intake._index(project);index['versions'].append(identifier);atomic_json(root/'index.json',index);published=True
            return record
        finally:
            if staging.exists():shutil.rmtree(staging)
            if not published:
                if directory.exists():shutil.rmtree(directory)
                for overlay in copied:
                    if overlay.exists():shutil.rmtree(overlay)
                if split_path:split_path.unlink(missing_ok=True)
