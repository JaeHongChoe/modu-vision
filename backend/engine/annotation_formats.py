"""Strict LabelMe, COCO polygons/boxes and YOLO detection/segmentation exchange.

Brush masks, COCO RLE and lossy rotated-box conversions fail explicitly. YOLO
needs classes and image dimensions; the export includes an image manifest for
round trips without copying or modifying source images.
"""
from __future__ import annotations
import json
import math
import re
from pathlib import PurePosixPath
from backend.engine.source_text import read_source_text

def safe_name(name):
    if not isinstance(name,str) or not name or '\\' in name: raise ValueError('Invalid image filename')
    path=PurePosixPath(name)
    if path.is_absolute() or '..' in path.parts or ':' in name: raise ValueError('Unsafe image path')
    return name

def _number(value):
    result=float(value)
    if not math.isfinite(result): raise ValueError('Nonfinite coordinate')
    return result

def _shape(item,width,height):
    if not isinstance(item,dict): raise ValueError('Annotation must be an object')
    label=item.get('label')
    if not isinstance(label,str) or not label.strip(): raise ValueError('Annotation needs a class label')
    kind=item.get('type','bbox'); shape={'type':kind,'label':label,'category_id':item.get('category_id')}
    if item.get('color') is not None:
        if not isinstance(item['color'],str) or not re.fullmatch(r'#[0-9a-fA-F]{6}',item['color']):raise ValueError('Invalid class color')
        shape['color']=item['color']
    if kind=='bbox':
        bbox=item.get('bbox',[])
        if len(bbox)!=4: raise ValueError('Box needs four coordinates')
        bbox=list(map(_number,bbox)); x1,y1,x2,y2=bbox
        if not (0<=x1<x2<=width and 0<=y1<y2<=height): raise ValueError('Box outside image or empty')
        shape['bbox']=bbox
    elif kind=='polygon':
        points=item.get('polygon') or item.get('points') or []
        if len(points)<3 or any(len(p)!=2 for p in points): raise ValueError('Polygon needs three points')
        polygon=[list(map(_number,p)) for p in points]
        if any(not(0<=x<=width and 0<=y<=height) for x,y in polygon): raise ValueError('Polygon outside image')
        shape['polygon']=polygon
    elif kind=='rotated_bbox':
        rb=item.get('rotated_bbox',[])
        if len(rb)!=5: raise ValueError('Rotated box needs center, dimensions and angle')
        rb=list(map(_number,rb))
        if rb[2]<=0 or rb[3]<=0: raise ValueError('Rotated box is empty')
        shape['rotated_bbox']=rb
    elif kind=='tag': shape['is_normal']=bool(item.get('is_normal'))
    else: raise ValueError(f'Unsupported shape: {kind}. Brush masks require a raster-mask workflow.')
    if item.get('direction_deg') is not None:
        direction=_number(item['direction_deg'])
        if not 0<=direction<360:raise ValueError('Object direction must be [0,360)')
        shape['direction_deg']=direction
    return shape

def _row(row):
    if not isinstance(row,dict): raise ValueError('Image entry must be an object')
    name=safe_name(row['file_name']); width=int(row.get('width') or 0); height=int(row.get('height') or 0)
    if width<=0 or height<=0: raise ValueError(f'Image dimensions are required: {name} has no size (it may not decode)')
    return {'file_name':name,'width':width,'height':height,
            'annotations':[_shape(a,width,height) for a in row.get('annotations',[])]}


def _bind_classes(rows,metadata=None,class_names=None):
    """One name-to-index mapping across all images; optional native IDs survive exchange."""
    if metadata is not None and not isinstance(metadata,list):raise ValueError('Class mapping must be a list')
    palette={}; ids={}
    def add(entry):
        if not isinstance(entry,dict):raise ValueError('Invalid class mapping entry')
        label=entry.get('label'); cid=entry.get('category_id'); color=entry.get('color')
        if not isinstance(label,str) or not label.strip():raise ValueError('Class mapping needs a label')
        if isinstance(cid,bool) or not isinstance(cid,int) or not 0<=cid<=255:raise ValueError('Class index must be an integer from 0 to 255')
        if color is not None and (not isinstance(color,str) or not re.fullmatch(r'#[0-9a-fA-F]{6}',color)):raise ValueError('Invalid class color')
        value={'label':label,'category_id':cid,**({'color':color} if color is not None else {})}
        if label in palette and palette[label]!=value:raise ValueError('Class name has conflicting index or color')
        if cid in ids and ids[cid]!=label:raise ValueError('Class index is shared by different labels')
        palette[label]=value;ids[cid]=label
    for entry in metadata or []:add(entry)
    annotations=[a for r in rows for a in r['annotations']]
    if metadata is None:
        colors={}
        for a in annotations:
            if a.get('color') is not None:
                if a['label'] in colors and colors[a['label']]!=a['color']:raise ValueError('Class name has conflicting color')
                colors[a['label']]=a['color']
        for a in annotations:
            if a.get('category_id') is not None:add({**a,**({'color':colors[a['label']]} if a['label'] in colors else {})})
        names=class_names if class_names is not None else sorted({a['label'] for a in annotations})
        if len(set(names))!=len(names):raise ValueError('Class vocabulary has duplicate labels')
        for label in names:
            if label in palette:continue
            cid=next((n for n in range(1,256) if n not in ids),None)
            if cid is None:raise ValueError('Class mapping exceeds 255 foreground classes')
            add({'label':label,'category_id':cid,**({'color':colors[label]} if label in colors else {})})
    for a in annotations:
        if a['label'] not in palette:raise ValueError('Class mapping is missing an imported label')
        entry=palette[a['label']]
        if entry['category_id']==0 and a['type']!='tag':raise ValueError('Class index 0 is reserved for background or image tags')
        a['category_id']=entry['category_id']
        if entry.get('color') is not None:a['color']=entry['color']
    return [palette[label] for label in sorted(palette)]


def merge_class_mapping(existing,incoming,existing_classes=None,incoming_classes=None):
    """Validate a merge's bijection and retain palette entries without foreground pixels."""
    records=[{'category_id':c['id'],'label':c['name'],'color':c.get('color'),'palette':True}
             for c in [*(existing_classes or []),*(incoming_classes or [])]]
    records.extend(a for a in [*existing,*incoming] if a['type']!='tag')
    by_id={};by_name={}
    for item in records:
        cid=item.get('category_id');cid=1 if cid is None else cid
        label=item['label'];color=item.get('color')
        if isinstance(cid,bool) or not isinstance(cid,int) or not 0<=cid<=255 or (cid==0 and not item.get('palette')):raise ValueError('Merge class IDs must use valid foreground indices')
        if cid in by_id and by_id[cid]['name']!=label:raise ValueError('Merge class ID conflicts with an existing class name')
        if label in by_name and by_name[label]!=cid:raise ValueError('Merge class name conflicts with an existing class ID')
        previous=by_id.get(cid)
        if previous and previous.get('color') and color and previous['color']!=color:raise ValueError('Merge class palette colors conflict')
        by_name[label]=cid;by_id[cid]={'id':cid,'name':label,'color':color or (previous or {}).get('color')}
    return [{**by_id[cid],'color':by_id[cid]['color'] or '#22d3ee'} for cid in sorted(by_id)]

def export_annotations(images,format):
    rows=[_row(row) for row in images]
    if len({row['file_name'] for row in rows})!=len(rows): raise ValueError('Duplicate image names')
    class_mapping=_bind_classes(rows)
    classes=sorted({a['label'] for r in rows for a in r['annotations']})
    if format=='labelme':
        documents=[]
        for row in rows:
            shapes=[]
            for a in row['annotations']:
                flags={}; kind=a['type']
                if kind=='bbox': x1,y1,x2,y2=a['bbox']; points=[[x1,y1],[x2,y2]]; st='rectangle'
                elif kind=='polygon': points=a['polygon']; st='polygon'
                elif kind=='rotated_bbox':
                    cx,cy,w,h,angle=a['rotated_bbox']; rad=math.radians(angle)
                    points=[[cx+x*math.cos(rad)-y*math.sin(rad),cy+x*math.sin(rad)+y*math.cos(rad)] for x,y in [(-w/2,-h/2),(w/2,-h/2),(w/2,h/2),(-w/2,h/2)]]
                    st='polygon'; flags={'studio_rotated_bbox':a['rotated_bbox']}
                else: points=[]; st='tag'; flags={'studio_tag':True,'is_normal':a.get('is_normal',False)}
                if a.get('direction_deg') is not None:flags['studio_direction_deg']=a['direction_deg']
                shapes.append({'label':a['label'],'points':points,'shape_type':st,'group_id':None,'flags':flags})
            documents.append({'version':'5.0.0','flags':{'studio_classes':class_mapping},'imagePath':row['file_name'],'imageData':None,
                              'imageWidth':row['width'],'imageHeight':row['height'],'shapes':shapes})
        return {'format':'labelme','documents':documents}
    if format in {'coco','yolo'} and any(a.get('direction_deg') is not None for row in rows for a in row['annotations']):raise ValueError('Direction targets require LabelMe or native annotation export')
    if format=='coco':
        result={'images':[],'categories':[{'id':i+1,'name':label} for i,label in enumerate(classes)],'annotations':[],'studio_classes':class_mapping}
        for image_id,row in enumerate(rows,1):
            result['images'].append({'id':image_id,**{k:row[k] for k in ['file_name','width','height']}})
            for a in row['annotations']:
                if a['type'] not in {'bbox','polygon'}: raise ValueError('COCO export supports boxes and polygons only')
                if a['type']=='bbox': x1,y1,x2,y2=a['bbox']; segmentation=[]; area=(x2-x1)*(y2-y1)
                else:
                    points=a['polygon']; xs,ys=zip(*points); x1,y1,x2,y2=min(xs),min(ys),max(xs),max(ys)
                    segmentation=[[v for p in points for v in p]]
                    area=abs(sum(points[i][0]*points[(i+1)%len(points)][1]-points[(i+1)%len(points)][0]*points[i][1] for i in range(len(points))))/2
                result['annotations'].append({'id':len(result['annotations'])+1,'image_id':image_id,'category_id':classes.index(a['label'])+1,
                                              'bbox':[x1,y1,x2-x1,y2-y1],'area':area,'iscrowd':0,'segmentation':segmentation})
        return result
    if format=='yolo':
        labels={}
        for row in rows:
            lines=[]; width,height=row['width'],row['height']
            for a in row['annotations']:
                if a['type']=='bbox':
                    x1,y1,x2,y2=a['bbox']; numbers=[(x1+x2)/2/width,(y1+y2)/2/height,(x2-x1)/width,(y2-y1)/height]
                elif a['type']=='polygon': numbers=[v/(width if i%2==0 else height) for i,v in enumerate(v for p in a['polygon'] for v in p)]
                else: raise ValueError('YOLO export supports detection boxes and segmentation polygons only')
                lines.append(' '.join([str(classes.index(a['label'])),*[format_float(v) for v in numbers]]))
            name=str(PurePosixPath(row['file_name']).with_suffix('.txt'))
            if name in labels: raise ValueError('Images share a label filename; rename one before exporting YOLO')
            labels[name]='\n'.join(lines)
        return {'format':'yolo','classes':classes,'images':[{k:r[k] for k in ['file_name','width','height']} for r in rows],'labels':labels,'studio_classes':class_mapping}
    raise ValueError('Choose labelme, coco or yolo')

def format_float(v): return f'{v:.16g}'

def import_annotations(payload,format):
    rows=[];class_mapping=None;class_names=None
    if format=='labelme':
        documents=payload.get('documents') if isinstance(payload,dict) and 'documents' in payload else payload if isinstance(payload,list) else [payload]
        for doc in documents:
            mapping=doc.get('flags',{}).get('studio_classes')
            if mapping is not None:
                if not isinstance(mapping,list):raise ValueError('Class mapping must be a list')
                if class_mapping is None:class_mapping=[]
                class_mapping.extend(mapping)
            row={'file_name':safe_name(doc['imagePath']),'width':doc['imageWidth'],'height':doc['imageHeight'],'annotations':[]}
            for shape in doc.get('shapes',[]):
                points=shape.get('points',[]); kind=shape.get('shape_type','polygon'); flags=shape.get('flags',{})
                a={'label':shape['label']}
                if flags.get('studio_rotated_bbox'): a.update(type='rotated_bbox',rotated_bbox=flags['studio_rotated_bbox'])
                elif flags.get('studio_tag') or kind=='tag': a.update(type='tag',is_normal=flags.get('is_normal',False))
                elif kind=='rectangle' and len(points)==2:
                    x1,y1=points[0]; x2,y2=points[1]; a.update(type='bbox',bbox=[min(x1,x2),min(y1,y2),max(x1,x2),max(y1,y2)])
                elif kind=='polygon': a.update(type='polygon',polygon=points)
                else: raise ValueError(f'Unsupported LabelMe shape: {kind}')
                if flags.get('studio_direction_deg') is not None:a['direction_deg']=flags['studio_direction_deg']
                row['annotations'].append(a)
            rows.append(_row(row))
    elif format=='coco':
        class_mapping=payload.get('studio_classes')
        class_names=[c['name'] for c in payload['categories']]
        categories={c['id']:c['name'] for c in payload['categories']}; by_id={}
        for image in payload['images']:
            if image['id'] in by_id: raise ValueError('Duplicate COCO image ID')
            row={**image,'annotations':[]}; by_id[image['id']]=row; rows.append(row)
        for item in payload['annotations']:
            if item.get('iscrowd') or isinstance(item.get('segmentation'),dict): raise ValueError('COCO crowd/RLE masks are unsupported')
            if item['image_id'] not in by_id or item['category_id'] not in categories: raise ValueError('Unknown COCO image/category')
            label=categories[item['category_id']]; seg=item.get('segmentation')
            if seg:
                if not isinstance(seg,list): raise ValueError('Invalid COCO polygon')
                for polygon in seg:
                    if len(polygon)<6 or len(polygon)%2: raise ValueError('Invalid COCO polygon coordinates')
                    by_id[item['image_id']]['annotations'].append({'label':label,'type':'polygon','polygon':[polygon[i:i+2] for i in range(0,len(polygon),2)]})
            else:
                x,y,w,h=item['bbox']; by_id[item['image_id']]['annotations'].append({'label':label,'type':'bbox','bbox':[x,y,x+w,y+h]})
        rows=[_row(r) for r in rows]
    elif format=='yolo':
        class_mapping=payload.get('studio_classes')
        classes=payload['classes']; labels=payload['labels']
        class_names=classes
        for image in payload['images']:
            row={**image,'annotations':[]}; width,height=image['width'],image['height']
            text=labels.get(str(PurePosixPath(safe_name(image['file_name'])).with_suffix('.txt')),'')
            for line in text.splitlines():
                if not line.strip(): continue
                tokens=line.split(); index=int(tokens[0]); values=list(map(_number,tokens[1:]))
                if index<0 or index>=len(classes): raise ValueError('Unknown YOLO class')
                a={'label':classes[index]}
                if any(v<0 or v>1 for v in values): raise ValueError('YOLO coordinates must be normalized')
                if len(values)==4:
                    cx,cy,w,h=values; a.update(type='bbox',bbox=[(cx-w/2)*width,(cy-h/2)*height,(cx+w/2)*width,(cy+h/2)*height])
                elif len(values)>=6 and len(values)%2==0: a.update(type='polygon',polygon=[[values[i]*width,values[i+1]*height] for i in range(0,len(values),2)])
                else: raise ValueError('YOLO line needs a detection box or segmentation polygon')
                row['annotations'].append(a)
            rows.append(_row(row))
    else: raise ValueError('Choose labelme, coco or yolo')
    if len({r['file_name'] for r in rows})!=len(rows): raise ValueError('Duplicate imported image names')
    _bind_classes(rows,class_mapping,class_names)
    return rows

def bundle_files(payload,format):
    if format=='labelme': return {str(PurePosixPath(safe_name(d['imagePath'])).with_suffix('.json')):json.dumps(d,ensure_ascii=False,indent=2) for d in payload['documents']}
    if format=='coco': return {'annotations.json':json.dumps(payload,ensure_ascii=False,indent=2)}
    return {**payload['labels'],'classes.txt':'\n'.join(payload['classes']), 'image_manifest.json':json.dumps(payload['images'],ensure_ascii=False,indent=2),
            **({'studio_classes.json':json.dumps(payload['studio_classes'],ensure_ascii=False,indent=2)} if 'studio_classes' in payload else {})}


def source_annotation_files(source,image):
    """Find source-label files that can bind a nested image, with split identity."""
    from pathlib import Path
    source=Path(source).resolve(); image=Path(image)
    relative=image.relative_to(source); split=next((part for part in relative.parts[:-1] if part in {'train','val','test'}),None)
    folders=[source]
    for parent in image.parents:
        if parent==source: break
        if parent.is_relative_to(source) and parent.name not in {'train','val','test','images'}: folders.append(parent)
    candidates=[]
    # COCO paths can be relative to their document, including a split/images folder.
    # Keep the existing YOLO class-list precedence in ``folders`` below.
    coco_folders=[source, *(parent for parent in image.parents if parent != source and parent.is_relative_to(source))]
    for folder in dict.fromkeys(coco_folders):
        names=['annotations.json',f'annotations_{split}.json' if split else 'annotations.json']
        for name in names:
            path=folder/name
            if path.is_file() and path not in candidates: candidates.append(path)
    for folder in dict.fromkeys(folders):
        local = image.relative_to(folder) if image.is_relative_to(folder) else relative
        label_parts = list(local.parts)
        if 'images' in label_parts:
            label_parts[label_parts.index('images')] = 'labels'
            paired_label = folder / Path(*label_parts).with_suffix('.txt')
        else:
            paired_label = folder / 'labels' / local.with_suffix('.txt')
        for label in [image.with_suffix('.txt'), paired_label]:
            if label.is_file() and label not in candidates: candidates.append(label)
        for name in ['classes.txt','data.yaml','dataset.yaml']:
            if (folder/name).is_file(): candidates.append(folder/name)
    return list(dict.fromkeys(candidates))


def source_coco_image_names(source, image, document):
    """Supported names for this image in one COCO document, without basename guessing.

    Keep source-relative names for existing datasets and add document-relative names.
    Multiple matching rows/documents remain an explicit ambiguity at the caller.
    """
    from pathlib import Path
    source=Path(source).resolve(); image=Path(image); document=Path(document)
    relative=image.relative_to(source).as_posix()
    names={relative, relative.removeprefix('images/')}
    if image.is_relative_to(document.parent):
        local=image.relative_to(document.parent).as_posix()
        names.update((local, local.removeprefix('images/')))
    split=next((part for part in Path(relative).parts[:-1] if part in {'train','val','test'}),None)
    if split and document.stem==f'annotations_{split}': names.add(image.name)
    return names


def validate_source_coco_binding(source, document, name):
    """A COCO row must not name two existing files through supported relative aliases."""
    from pathlib import Path
    name=safe_name(name).removeprefix('./')
    bases={Path(source).resolve(), Path(document).parent}
    candidates={base/prefix/name for base in bases for prefix in ('', 'images')}
    existing=[]
    for path in candidates:
        if path.is_file() and not any(path.samefile(other) for other in existing):
            existing.append(path)
    if len(existing)>1:
        raise ValueError(f'COCO image mapping is ambiguous: {name} names more than one source image')


def source_annotations_for_image(source,image):
    """Read standard COCO/YOLO source labels without altering them."""
    from pathlib import Path
    from PIL import Image
    source=Path(source).resolve();image=Path(image);relative=image.relative_to(source).as_posix()
    files=source_annotation_files(source,image)
    if not any(path.suffix in {'.json','.txt'} for path in files):return None
    if any(not path.resolve().is_relative_to(source) for path in files):
        raise ValueError('Source annotation links outside the source')
    from backend.engine.dicom_input import IMAGE_OPEN_ERRORS,open_source_image
    try:
        with open_source_image(image) as pil: width,height=pil.size
    except IMAGE_OPEN_ERRORS:return None  # an undecodable image has no source labels to bind
    matched=[]
    for path in files:
        if path.suffix!='.json':continue
        data=json.loads(read_source_text(path))
        if not isinstance(data,dict) or not {'images','categories','annotations'}<=set(data):continue
        names=source_coco_image_names(source,image,path)
        images=[row for row in data['images'] if safe_name(row['file_name']).removeprefix('./') in names]
        if len(images)>1:raise ValueError('COCO image mapping is ambiguous')
        if not images:continue
        row=images[0]
        validate_source_coco_binding(source,path,row['file_name'])
        if (row['width'],row['height'])!=(width,height):raise ValueError('COCO dimensions differ from source image')
        sub={'images':[{**row,'file_name':relative}],'categories':data['categories'],'annotations':[a for a in data['annotations'] if a['image_id']==row['id']]}
        matched.append(import_annotations(sub,'coco')[0]['annotations'])
    if len(matched)>1:raise ValueError('Multiple COCO documents contain this image; import the chosen document explicitly')
    if matched:return matched[0]
    txt=next((p for p in files if p.suffix=='.txt' and p.name!='classes.txt'),None)
    if txt is None:return None
    classes_path=next((p for p in files if p.name=='classes.txt'),None)
    if classes_path:classes=read_source_text(classes_path).splitlines()
    else:
        config=next((p for p in files if p.suffix=='.yaml'),None)
        if config is None:raise ValueError('YOLO source labels require classes.txt or data.yaml names')
        import yaml
        names=yaml.safe_load(read_source_text(config)).get('names')
        if isinstance(names,list):classes=names
        elif isinstance(names,dict):
            indexed={int(k):v for k,v in names.items()}
            if set(indexed)!=set(range(len(indexed))):raise ValueError('YOLO class IDs must be contiguous')
            classes=[indexed[i] for i in range(len(indexed))]
        else:raise ValueError('YOLO names are missing')
    payload={'classes':classes,'images':[{'file_name':relative,'width':width,'height':height}], 'labels':{str(Path(relative).with_suffix('.txt')):read_source_text(txt)}}
    return import_annotations(payload,'yolo')[0]['annotations']
