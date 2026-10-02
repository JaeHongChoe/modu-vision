"""Manifest-selected task datasets retain exact original source paths.

No images or labels are relocated. The same helper supplies training and
reevaluation partitions. A missing annotation/mask or a defect selected for
anomaly training fails explicitly.
"""
from pathlib import Path
import json
import cv2
import numpy as np
import torch
from PIL import Image
from backend.engine.dicom_input import UNDECODABLE_IMAGE_ERRORS, open_source_image
from backend.engine.source_text import read_source_text
from backend.engine.annotation_storage import dataset_annotation_dir
from backend.engine.annotation_formats import import_annotations,export_annotations,source_annotations_for_image
from backend.engine.dataset_loaders import DetectionDataset,AnomalyDataset,_classification_split_assignments,_read_image_rgb,SUPPORTED_IMAGE_EXTENSIONS

NORMAL_NAMES={'good','ok','normal','pass'}

def source_image_paths(source,task,*,include_unused=False):
    source=Path(source).resolve()
    from backend.engine.annotation_storage import request_project_root
    project=request_project_root()
    # An archived project owns its restored source below dataset/. Exclude the
    # project only when it is a child of the selected source being scanned.
    exclude_project=project is not None and project.is_relative_to(source)
    from backend.engine.dataset_usage import unused_image_paths
    unused=set() if include_unused else unused_image_paths(source)
    # The same inventory rules as the persistent dataset index (dataset_inventory).
    from backend.engine.dataset_inventory import is_inventory_path, scan_root as inventory_root
    return sorted(p for p in inventory_root(source,task).rglob('*') if p.is_file() and str(p.resolve()) not in unused and (not exclude_project or not p.is_relative_to(project))
                  and is_inventory_path(p.relative_to(source).parts,task))

def is_anomaly_normal(image,source=None):
    path=Path(image)
    if source is not None:path=path.relative_to(Path(source))
    parts=path.parts[:-1]
    if len(parts)==1 and parts[0]=='train':return True
    return any(part.casefold() in NORMAL_NAMES or part=='test_crop_output' for part in parts)

def _annotations(source,image):
    studio=dataset_annotation_dir(image.parent)/f'{image.stem}.json'
    if studio.is_file():
        data=json.loads(studio.read_text(encoding='utf-8'));annotations=data.get('annotations')
        if not isinstance(annotations,list):raise ValueError(f'Invalid Studio labels: {image.name}')
        return annotations,data.get('mask_file')
    adjacent=image.with_suffix('.json')
    if adjacent.is_file():
        document=json.loads(read_source_text(adjacent))  # a source LabelMe file: UTF-8, BOM, or this machine's code page
        document.setdefault('imagePath',image.name)
        if 'imageWidth' not in document or 'imageHeight' not in document:  # the image is opened only for a missing size
            try:
                with open_source_image(image) as opened:width,height=opened.size
            except UNDECODABLE_IMAGE_ERRORS:
                # Bytes that can never decode bind no labels (listings show the image without a size, loaders refuse it).
                # A transient failure (a file locked by an indexer, a share that dropped) is raised, never turned into
                # an unlabeled image: training reads these labels too.
                return [],None
            document.setdefault('imageWidth',width);document.setdefault('imageHeight',height)
        return import_annotations(document,'labelme')[0]['annotations'],None
    annotations=source_annotations_for_image(source,image)
    return annotations,None

def _paired_mask(source,image):
    relative=image.relative_to(source);parts=list(relative.parts);candidates=[]
    if 'images' in parts:
        parts[parts.index('images')]='masks';base=source/Path(*parts).with_suffix('.png');candidates.extend([base,base.with_name(f'{image.stem}_mask.png')])
    candidates.extend([source/'masks'/f'{image.stem}.png',source/'masks'/f'{image.stem}_mask.png'])
    return next((p for p in candidates if p.is_file()),None)

def _mask_class_mapping(image):
    studio=dataset_annotation_dir(Path(image).parent)/f'{Path(image).stem}.json'
    if not studio.is_file():return {}
    classes=json.loads(studio.read_text(encoding='utf-8')).get('mask_classes',[])
    return {c['id']:c['name'] for c in classes if c['id']>0}

def _mask_pixels(path,explicit_ids=None):
    with Image.open(path) as image:
        pixels=np.asarray(image).copy()
    if pixels.ndim!=2:raise ValueError('Training masks must contain single-channel class IDs')
    if 255 not in (explicit_ids or {}) and set(np.unique(pixels)).issubset({0,255}):pixels=np.where(pixels==255,1,pixels).astype(np.uint8)
    return pixels

class ManifestSegmentationDataset:
    def __init__(self,source,images,transform=None,image_size=None,max_dim=1600,class_names=None):
        self.source=source;self.transform=transform;self.image_size=image_size;self.max_dim=max_dim;self.samples=[];self._regions={};labels=set();max_mask_id=1;mask_classes={}
        for image in images:
            annotations,overlay_mask=_annotations(source,image)
            mask=Path(overlay_mask) if overlay_mask and Path(overlay_mask).is_file() else _paired_mask(source,image)
            if mask:
                mask_classes.update(_mask_class_mapping(image))
                for annotation in annotations or []:
                    if annotation.get('type')!='tag':
                        cid=annotation.get('category_id') or 1;name=annotation['label']
                        if cid in mask_classes and mask_classes[cid]!=name:raise ValueError('Saved mask class IDs have conflicting names')
                        mask_classes[cid]=name
                values=np.unique(_mask_pixels(mask,mask_classes))
                max_mask_id=max(max_mask_id,int(values.max(initial=0)))
            elif annotations is not None:
                self._regions[str(image)]=annotations
                labels.update(a['label'] for a in annotations if a.get('type') in {'bbox','polygon','rotated_bbox'})
                if any(a.get('type')=='brush_mask' for a in annotations):raise ValueError('Brush annotations require their saved raster mask')
            else:raise ValueError(f'Missing segmentation annotation/mask: {image}')
            self.samples.append((image,mask))
        if class_names is not None:self.classes=list(class_names)
        elif mask_classes:self.classes=['background',*[mask_classes.get(i,f'class_{i}') for i in range(1,max(max_mask_id,max(mask_classes))+1)]]
        elif labels:self.classes=['background',*sorted(labels)]
        else:self.classes=['background',*[f'class_{i}' for i in range(1,max_mask_id+1)]]
        self._class_ids={name:i for i,name in enumerate(self.classes)}
    def __len__(self):return len(self.samples)
    def __getitem__(self,index):
        image,mask_file=self.samples[index];rgb=_read_image_rgb(image);h,w=rgb.shape[:2]
        if mask_file:
            mask=_mask_pixels(mask_file,_mask_class_mapping(image))
            if mask.max(initial=0)>=len(self.classes):raise ValueError('Mask class IDs exceed training class mapping')
        else:
            mask=np.zeros((h,w),dtype=np.uint8)
            for a in self._regions.get(str(image),[]):
                if a.get('is_normal') or a.get('label','').casefold() in NORMAL_NAMES:continue
                kind=a.get('type');class_id=self._class_ids.get(a['label'])
                if class_id is None:raise ValueError('Region class absent from training mapping')
                if kind=='bbox':x1,y1,x2,y2=a['bbox'];cv2.rectangle(mask,(int(x1),int(y1)),(int(x2),int(y2)),class_id,-1)
                elif kind=='polygon':cv2.fillPoly(mask,[np.round(a.get('polygon') or a.get('points')).astype(np.int32)],class_id)
                elif kind=='rotated_bbox':cx,cy,bw,bh,angle=a['rotated_bbox'];cv2.fillPoly(mask,[np.round(cv2.boxPoints(((cx,cy),(bw,bh),angle))).astype(np.int32)],class_id)
                else:raise ValueError(f'Unsupported segmentation region: {kind}')
        size=self.image_size
        if size is None and max(h,w)>self.max_dim:scale=self.max_dim/max(h,w);size=(max(16,int(w*scale)),max(16,int(h*scale)))
        if size:rgb=cv2.resize(rgb,size);mask=cv2.resize(mask,size,interpolation=cv2.INTER_NEAREST)
        elif mask.shape!=(h,w):mask=cv2.resize(mask,(w,h),interpolation=cv2.INTER_NEAREST)
        tensor=torch.from_numpy(rgb.transpose(2,0,1).copy()).float()/255
        mask_tensor=torch.from_numpy(mask.astype(np.int64)).long()
        if self.transform:
            from backend.engine.augmentations import apply_sample_transform
            sample=apply_sample_transform(self.transform,tensor,mask_tensor,task='segmentation')
            tensor,mask_tensor=sample.image,sample.targets
        return tensor,mask_tensor

def load_manifest_dataset(task,source,split,transform=None,image_size=None,class_names=None):
    source=Path(source).resolve();assignments=_classification_split_assignments(source)
    if assignments is None:return None
    available={str(p):p for p in source_image_paths(source,task)}
    selected=[]
    for name,partition in assignments.items():
        if name not in available:
            # A persisted split keeps its original partition; an explicitly
            # excluded review/usage sample must not be reassigned to training.
            from backend.engine.dataset_usage import unused_image_paths
            if name in unused_image_paths(source):continue
            raise ValueError(f'Saved split references missing/non-image source: {name}')
        if partition==split:selected.append(available[name])
    if task=='detection':
        rows=[]
        for image in available.values():
            with open_source_image(image) as pil:width,height=pil.size
            annotations,_=_annotations(source,image)
            if annotations is None:raise ValueError(f'Missing detection labels: {image}')
            regions=[]
            for a in annotations:
                if a.get('type')=='tag':
                    if not a.get('is_normal') and a.get('label','').casefold() not in NORMAL_NAMES:raise ValueError('Detection tag has no region')
                    continue
                if a.get('type')=='rotated_bbox':
                    cx,cy,w,h,angle=a['rotated_bbox'];points=cv2.boxPoints(((cx,cy),(w,h),angle));xs,ys=points[:,0],points[:,1]
                    a={**a,'type':'bbox','bbox':[max(0,float(xs.min())),max(0,float(ys.min())),min(width,float(xs.max())),min(height,float(ys.max()))]}
                regions.append(a)
            rows.append({'file_name':image.relative_to(source).as_posix(),'width':width,'height':height,'annotations':regions})
        # COCO is used only as an internal AABB/class adapter. Keep native
        # orientation/direction beside it rather than exporting a lossy target.
        coco_rows=[{**row,'annotations':[{k:v for k,v in a.items() if k!='direction_deg'} for a in row['annotations']]} for row in rows]
        data=export_annotations(coco_rows,'coco')
        native_regions=[a for row in rows for a in row['annotations']]
        for annotation,native in zip(data['annotations'],native_regions):
            for key in ('rotated_bbox','direction_deg'):
                if native.get(key) is not None:annotation[key]=native[key]
        selected_names={p.relative_to(source).as_posix() for p in selected}
        data['images']=[r for r in data['images'] if r['file_name'] in selected_names]
        selected_ids={r['id'] for r in data['images']}
        data['annotations']=[a for a in data['annotations'] if a['image_id'] in selected_ids]
        if not data['categories'] and class_names:data['categories']=[{'id':i+1,'name':name} for i,name in enumerate(class_names)]
        return DetectionDataset(images_dir=source,annotation_data=data,transform=transform,image_size=image_size,class_names=class_names)
    if task=='segmentation':
        if class_names is None:
            labels=set();max_mask_id=1;mask_classes={}
            for image in available.values():
                annotations,overlay_mask=_annotations(source,image)
                mask=Path(overlay_mask) if overlay_mask and Path(overlay_mask).is_file() else _paired_mask(source,image)
                if mask:
                    mask_classes.update(_mask_class_mapping(image))
                    for annotation in annotations or []:
                        if annotation.get('type')!='tag':
                            cid=annotation.get('category_id') or 1;name=annotation['label']
                            if cid in mask_classes and mask_classes[cid]!=name:raise ValueError('Saved mask class IDs have conflicting names')
                            mask_classes[cid]=name
                    values=np.unique(_mask_pixels(mask,mask_classes))
                    max_mask_id=max(max_mask_id,int(values.max(initial=0)))
                elif annotations is not None:
                    labels.update(a['label'] for a in annotations if a.get('type') in {'bbox','polygon','rotated_bbox'})
            class_names=['background',*[mask_classes.get(i,f'class_{i}') for i in range(1,max(max_mask_id,max(mask_classes))+1)]] if mask_classes else ['background',*sorted(labels)] if labels else ['background',*[f'class_{i}' for i in range(1,max_mask_id+1)]]
        return ManifestSegmentationDataset(source,selected,transform,image_size,class_names=class_names)
    if task in {'anomaly','anomaly_detection'}:
        dataset=AnomalyDataset.__new__(AnomalyDataset);dataset.root_dir=source;dataset.split=split;dataset.transform=transform;dataset.image_size=image_size;dataset.max_dim=1600;dataset.samples=[]
        for image in selected:
            normal=is_anomaly_normal(image,source)
            if split=='train' and not normal:raise ValueError('Anomaly training must contain normal images exclusively')
            category=image.parent.name;mask=next((p for p in [source/'ground_truth'/category/f'{image.stem}_mask.png',source/'ground_truth'/category/image.name] if p.is_file()),None)
            dataset.samples.append((image,0 if normal else 1,mask))
        return dataset
    return None
