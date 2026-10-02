"""Image-grain, active-labelset statistics without mutating source data."""
from __future__ import annotations

from collections import Counter
from pathlib import Path, PurePath
from typing import Any

from backend.engine.annotation_storage import dataset_annotation_dir
from backend.engine.grouped_dataset_views import _annotations,source_image_paths
from backend.engine.dataset_inventory import folder_label_split


def dataset_summary(source:Path,task:str,*,assignments:dict[str,str]|None=None,
                    usage:dict[str,str]|None=None,metadata:dict[str,dict]|None=None)->dict[str,Any]:
    source=Path(source).expanduser().resolve()
    assignments=assignments or {}
    usage=usage or {}
    metadata=metadata or {}
    items=[]
    class_counts=Counter()
    split_counts=Counter({k:0 for k in ('train','val','test','not_used','not_split')})
    state_counts=Counter({'labeled':0,'unlabeled':0})
    for image in source_image_paths(source,task,include_unused=True):
        relative=image.relative_to(source).as_posix()
        annotations,mask=_annotations(source,image)
        labels=list(dict.fromkeys(a['label'] for a in annotations or []
             if isinstance(a.get('label'),str) and a['label']))
        overlay=dataset_annotation_dir(image.parent)/f'{image.stem}.json'
        # Clearing an editable overlay is intentionally different from a
        # source COCO/LabelMe document declaring a background-only image.
        mask_exists=bool(mask) and Path(mask).is_file()
        valid_regions=any(a.get('type') in {'bbox','polygon','rotated_bbox','tag'} for a in annotations or [])
        labeled=valid_regions or mask_exists or (annotations is not None and not overlay.is_file() and not image.with_suffix('.json').is_file())
        row=metadata.get(str(image),{})
        if row.get('workflow_state')=='approved':
            labeled=True
        # Folder label and split follow the same rule as the persistent dataset index (dataset_inventory).
        folder_label,folder_split=folder_label_split(PurePath(relative),task)
        if task in ('anomaly','anomaly_detection'):
            labels=[folder_label]
            labeled=True
        elif task=='classification' and not labels and folder_label:
            labels=[folder_label]
            labeled=True
        partition=assignments.get(str(image),assignments.get(relative))
        if partition not in {'train','val','test'}:
            partition=folder_split or 'not_split'
        state=usage.get(str(image),usage.get(relative,row.get('usage_state','active')))
        if state=='not_used':
            partition='not_used'
        label_state='labeled' if labeled else 'unlabeled'
        class_counts.update(labels)
        split_counts[partition]+=1
        state_counts[label_state]+=1
        items.append({'image_id':image.stem,'file_name':image.name,'file_path':str(image),
            'relative_path':relative,'labels':labels,'label':labels[0] if labels else None,
            'label_status':label_state,'split':partition,
            'thumbnail_url':f'/api/dataset/thumbnail/{image.name}?file_path={image}'})
    total=len(items)
    def stats(counts):
        return {name:{'count':count,'ratio':count/total if total else 0.0}
            for name,count in counts.items()}
    return {'total':total,'labeling':stats(state_counts),'assignments':stats(split_counts),
        'classes':stats(dict(sorted(class_counts.items()))),'items':items,
        'class_count_grain':'distinct source images per class; multi-class counts may exceed total'}
