"""Image-grain, active-labelset statistics without mutating source data."""
from __future__ import annotations
from backend.engine.original_owned_UI_ancillary_request import observe_original_UI_ancillary_handler, observe_original_dataset_UI_operation, observe_original_dataset_UI_connection, original_dataset_UI_read_text, original_dataset_UI_read_bytes, original_dataset_UI_image_open, original_dataset_UI_file, original_dataset_UI_diagnostics_missing

from collections import Counter
from pathlib import Path, PurePath
from typing import Any

from backend.engine.annotation_storage import dataset_annotation_dir
from backend.engine.grouped_dataset_views import _annotations,_mask_class_mapping,_mask_pixels,source_image_paths
from backend.engine.dataset_inventory import folder_label_split


def active_mask_foreground_labels(image:Path,annotations:list[dict],declared_mask:Any)->list[str]|None:
    """Read present class names from the active overlay's canonical raster.

    A saved pointer only declares a raster; it must never select an arbitrary
    file outside this image's active annotation scope. None retains the caller's
    existing non-raster behavior, while [] is a real background-only raster.
    """
    mask=dataset_annotation_dir(image.parent)/'masks'/f'{image.stem}.png'
    if not declared_mask or not mask.is_file():return None
    classes=_mask_class_mapping(image)
    for annotation in annotations:
        if annotation.get('type')!='tag':
            cid=annotation.get('category_id') or 1
            name=annotation['label']
            if cid in classes and classes[cid]!=name:
                raise ValueError('Saved mask class IDs have conflicting names')
            classes[cid]=name
    import numpy as np
    return list(dict.fromkeys(classes.get(int(cid),f'class_{int(cid)}')
        for cid in np.unique(_mask_pixels(mask,classes)) if cid>0))


@observe_original_dataset_UI_operation('dataset_summary')
def dataset_summary(source:Path,task:str,*,assignments:dict[str,str]|None=None,
                    usage:dict[str,str]|None=None,metadata:dict[str,dict]|None=None,
                    tolerate_schema_errors:bool=False)->dict[str,Any]:
    source=Path(source).expanduser().resolve()
    assignments=assignments or {}
    usage=usage or {}
    metadata=metadata or {}
    items=[]
    class_counts=Counter()
    split_counts=Counter({k:0 for k in ('train','val','test','not_used','not_split')})
    state_counts=Counter({'labeled':0,'unlabeled':0})
    from backend.engine.class_overrides import read_class_overrides, effective_class
    overrides=read_class_overrides(source) if task=='classification' else {}
    for image in source_image_paths(source,task,include_unused=True):
        relative=image.relative_to(source).as_posix()
        try:
            annotations,mask=_annotations(source,image)
            raster_labels=active_mask_foreground_labels(image,annotations or [],mask) if task=='segmentation' else None
        except (ValueError,KeyError,TypeError):
            if not tolerate_schema_errors:raise
            annotations,mask=None,None
            raster_labels=None
        labels=list(dict.fromkeys(a['label'] for a in annotations or []
             if isinstance(a.get('label'),str) and a['label']))
        if raster_labels is not None:
            labels=list(dict.fromkeys([*raster_labels,*(a['label'] for a in annotations or []
                if a.get('type')=='tag' and isinstance(a.get('label'),str) and a['label'])]))
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
        class_source=None
        if task=='classification':
            # Training reads the folder class (or the class chosen for this image in step 2); box and polygon labels
            # are for patch classification and evidence, so they never replace the class shown here.
            chosen,class_source=effective_class(image,folder_label,overrides)
            if chosen:
                labels=[chosen]
                labeled=True
            elif not labels:
                class_source=None
        partition=assignments.get(str(image),assignments.get(relative))
        if partition not in {'train','val','test'}:
            partition=folder_split or 'not_split'
        state=usage.get(str(image),usage.get(relative,row.get('usage_state','active')))
        if state=='not_used':
            partition='not_used'
        label_state='labeled' if labeled else 'unlabeled'
        # Class counts are of the images training reads; an image marked not used (an unreadable file the import
        # excluded, or one a reviewer set aside) stays listed but no longer counts toward its class (audit F-01).
        if state!='not_used':class_counts.update(labels)
        split_counts[partition]+=1
        state_counts[label_state]+=1
        items.append({'image_id':image.stem,'file_name':image.name,'file_path':str(image),
            'relative_path':relative,'labels':labels,'label':labels[0] if labels else None,
            'folder_label':folder_label,'class_source':class_source,
            'label_status':label_state,'split':partition,
            'thumbnail_url':f'/api/dataset/thumbnail/{image.name}?file_path={image}'})
    total=len(items)
    def stats(counts):
        return {name:{'count':count,'ratio':count/total if total else 0.0}
            for name,count in counts.items()}
    return {'total':total,'labeling':stats(state_counts),'assignments':stats(split_counts),
        'classes':stats(dict(sorted(class_counts.items()))),'items':items,
        'class_count_grain':'distinct source images per class; multi-class counts may exceed total'}
