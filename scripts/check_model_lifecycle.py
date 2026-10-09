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

def _read_pin(root,path,pin,parse=False,*,raw=False):
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
        if parse or raw:need(initial.st_size<=MAX_JSON_BYTES,'JSON artifact exceeds bound')
        digest=hashlib.sha256();chunks=[];total=0
        while True:
            block=os.read(fd,1024*1024)
            if not block:break
            total+=len(block);need(total<=pin['size'],'artifact grew during read');digest.update(block)
            if parse or raw:chunks.append(block)
        need(total==pin['size'] and digest.hexdigest()==pin['sha256'],'artifact raw bytes changed')
        need(_identity(initial)==_identity(os.fstat(fd))==_identity(os.stat(parts[-1],dir_fd=directory,follow_symlinks=False)),'artifact identity changed during read')
        for parent,name,child in entries:
            need(_identity(os.fstat(child))==_identity(os.stat(name,dir_fd=parent,follow_symlinks=False)),'artifact parent was replaced')
        for ancestor,identity in ancestor_states:need(_identity(ancestor.lstat())==identity,'artifact root namespace changed')
        if raw:return b''.join(chunks)
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

def _absolute_record_path(value):
    """A producer identity, never an instruction to read outside copied pins."""
    need(type(value) is str and value.startswith('/') and '\\' not in value and '\x00' not in value,
         'family producer path is not a canonical absolute identity')
    relative(value[1:])
    return value

def _prepared_rotated_inputs(binding,manifest,images,copies,file,pins):
    """Join the original version-2 OBB producer records, including backgrounds.

    bind_family_training records object-bearing images in family_inputs, while
    the loader/source map includes every image and the UUID binding includes
    every mapped original. Extra background copies prove those original bytes;
    they do not rewrite family_inputs. This is raw record integrity, not image
    decoding, geometric truth, reproduced training or quality approval.
    """
    rows=binding.get('family_inputs')
    need(type(rows) is list and 0<len(rows)<=MAX_FILES,'OBB family input inventory is empty or malformed')
    if any(type(row) is dict and 'restored_source_sha256' in row for row in rows):
        raise Gap('relocated OBB family aliases require a separate adapter','pending')
    if copies is None:raise Gap('OBB prepared input copies are unavailable','pending')
    need(type(copies) is dict and copies,'OBB family copy mapping is empty or malformed')
    need(binding.get('family_task')=='rotated_detection','OBB family purpose differs')
    dataset=_absolute_record_path(binding['family_dataset_path'])
    source=_absolute_record_path(manifest['source_dataset_dir'])
    version=_absolute_record_path(binding['version_dir'])
    need(version.rsplit('/',1)[-1]==manifest['id'] and version.rsplit('/',2)[-2]=='versions','OBB frozen version identity differs')
    original_images={}
    for row in manifest['files']:
        if row.get('kind')!='image':continue
        relative(row['relative_path']);original=_absolute_record_path(row['source_path'])
        need(original==source+'/'+row['relative_path'] and original not in original_images,'OBB original image identity is foreign or duplicated')
        original_images[original]=row['sha256']
    need(original_images==images,'OBB original source inventory differs from the immutable dataset')
    name='rotated_boxes.json';manifest_path=dataset+'/'+name
    snapshot=version+'/labels/family/rotated_detection/'+name
    by_relative={};source_order=[]
    for row in rows:
        shape(row,('relative_path','source_path','sha256','snapshot_path'))
        relative(row['relative_path']);original=_absolute_record_path(row['source_path'])
        need(original==dataset+'/'+row['relative_path'],'OBB family member belongs to another dataset')
        need(row['relative_path'] not in by_relative,'duplicate OBB family input member')
        need(type(row['sha256']) is str and HEX.fullmatch(row['sha256']) is not None,'OBB member digest is invalid')
        need(row['snapshot_path']==(snapshot if original==manifest_path else None),'OBB frozen manifest identity differs')
        source_order.append(original);by_relative[row['relative_path']]=row
    need(source_order==sorted(source_order) and name in by_relative,'OBB family input order or manifest differs')
    need(binding.get('family_inputs_sha256')==hashlib.sha256(json.dumps(rows,sort_keys=True,separators=(',',':')).encode()).hexdigest(),
         'OBB original family input inventory digest differs')
    need(manifest_path in copies and snapshot in copies,'OBB current or frozen manifest copy is missing')
    need(all(type(path) is str for path in copies.values()) and len(set(copies.values()))==len(copies),'OBB copies repeat an artifact identity')
    for original,path in copies.items():_absolute_record_path(original);relative(path)
    body=file(copies[manifest_path]);raw=file(copies[manifest_path],False,raw=True)
    if type(body) is not dict or type(body.get('version')) is not int or body['version']!=2:
        raise Gap('only original version-2 OBB prepared records are supported','pending')
    shape(body,('version','samples','source_dataset_path','source_map'))
    need(file(copies[snapshot])==body,'OBB frozen manifest body differs')
    need(pins[copies[manifest_path]]['sha256']==by_relative[name]['sha256']==pins[copies[snapshot]]['sha256'],
         'OBB current/frozen raw manifest bytes differ')
    entries=body['samples'];mapping=body['source_map']
    need(type(entries) is list and 0<len(entries)<=MAX_FILES,'OBB sample inventory is empty or malformed')
    need(body['source_dataset_path']==source and type(mapping) is dict,'OBB original source mapping differs')
    hashes={};counts={'train':0,'val':0,'test':0};hash_splits={};object_images=set();digest_rows=[];object_count=0;directions=[];train_objects=0
    for entry in entries:
        shape(entry,('image','source_sha256','split','objects'))
        image=entry['image'];relative(image)
        value=entry['source_sha256'];split=entry['split'];objects=entry['objects']
        need(image!=name and image not in hashes,'OBB image identity repeats')
        need(type(value) is str and HEX.fullmatch(value) is not None,'OBB sample source digest is invalid')
        need(type(split) is str and split in counts,'OBB sample partition is invalid')
        need(value not in hash_splits or hash_splits[value]==split,'identical OBB pixels cross partitions')
        need(type(objects) is list and len(objects)<=MAX_FILES,'OBB explicit objects are absent or malformed')
        hashes[image]=value;hash_splits[value]=split;counts[split]+=1
        object_count+=len(objects);need(object_count<=MAX_FILES,'OBB object inventory exceeds record bound')
        if objects:object_images.add(image)
        for item in objects:
            need(type(item) is dict and set(item) in ({'label','box'},{'label','box','direction_deg'}),'OBB object schema differs')
            label=item['label'];box=item['box']
            need(type(label) is str and label and label==label.strip() and '/' not in label and '\\' not in label
                 and all(ord(char)>=32 for char in label),'OBB object label identity is invalid')
            shape(box,('cx','cy','width','height','angle_deg'))
            need(all(type(box[key]) in (int,float) and math.isfinite(box[key]) for key in box),'OBB box values are nonfinite or malformed')
            need(box['width']>0 and box['height']>0 and -90<=box['angle_deg']<90,'OBB box size or axial angle is invalid')
            direction=item.get('direction_deg')
            need(direction is None or type(direction) in (int,float) and math.isfinite(direction) and 0<=direction<360,'OBB direction identity is invalid')
            directions.append(direction is not None);digest_rows.append((image,value))
            if split=='train':train_objects+=1
        if not objects:digest_rows.append((image,value))
    need(counts['train']>0 and counts['val']>0 and train_objects>0,'OBB training partitions have no labeled training object')
    need(not directions or not any(directions) or all(directions),'OBB direction targets are incomplete')
    need(set(mapping)==set(hashes),'OBB source map omits or adds a background/image')
    need(set(by_relative)=={name,*object_images},'OBB family_inputs differs from original object-bearing producer inventory')
    identities={manifest_path,snapshot,*[dataset+'/'+image for image in hashes]}
    need(set(copies)==identities,'OBB copied inventory omits background bytes or contains extras')
    originals=[]
    for image,value in hashes.items():
        row=shape(mapping[image],('source_relative_path','source_sha256'));relative(row['source_relative_path'])
        original=source+'/'+row['source_relative_path']
        need(row['source_sha256']==value==images.get(original),'OBB member belongs to another original source')
        originals.append(row['source_relative_path'])
        path=copies[dataset+'/'+image];file(path,False)
        need(pins[path]['sha256']==value,'OBB copied image bytes differ')
        if image in object_images:need(by_relative[image]['sha256']==value,'OBB object-bearing inventory digest differs')
    manifest_sha=pins[copies[manifest_path]]['sha256']
    digest=hashlib.sha256(b'rotated-detection-single-object-v1\0'+manifest_sha.encode('ascii'))
    for image,value in sorted(digest_rows):digest.update(b'\0'+image.encode('utf-8')+b'\0'+value.encode('ascii'))
    expected={'dataset_sha256':'sha256:'+digest.hexdigest(),'manifest_sha256':manifest_sha,'source_sha256':hashes,
              'split_counts':counts,'source_image_count':len(hashes),'object_count':object_count,
              'direction_enabled':any(directions),'source_dataset_path':source,'source_map':mapping}
    need(type(binding.get('family_provenance')) is dict and canonical(binding['family_provenance'])==canonical(expected),
         'OBB provenance differs from original manifest and every image byte')
    need(binding.get('family_dataset_sha256')==expected['dataset_sha256'],'OBB training family dataset digest differs')
    if 'family_source_image_uuids' not in binding:raise Gap('legacy OBB record has no source UUID inventory','pending')
    eligibility=binding['team_data']['eligibility']
    need(type(eligibility) is list and all(type(row) is dict and type(row.get('relative_path')) is str
         and type(row.get('image_uuid')) is str for row in eligibility),'OBB eligibility UUID inventory is malformed')
    inventory={row['relative_path']:row['image_uuid'] for row in eligibility}
    need(len(inventory)==len(eligibility),'OBB eligibility repeats an original path')
    settings=binding['team_data'].get('settings')
    if type(settings) is not dict or 'approved_only_training' not in settings:
        raise Gap('legacy OBB record has no approved-only training setting','pending')
    need(type(settings['approved_only_training']) is bool,'OBB approved-only training setting is malformed')
    if settings['approved_only_training']:need(all(path in inventory for path in originals),'OBB background/object input is not approved for training')
    need(binding['family_source_image_uuids']==sorted({inventory[path] for path in originals if path in inventory}),
         'OBB source UUID inventory differs')

def _prepared_pairs_generator_inputs(binding,family,manifest,images,copies,file,pins):
    """Join retained explicit-pair/GAN producer records through copied raw pins.

    The original file reader supplies every raw-byte/OFD/namespace guard. This
    neither decodes pixels/crop dimensions nor adopts GAN output or approves
    quality. Producer absolute paths are identities, never live read authority.
    """
    need(family in ('enhancement','defect_gan'),'unsupported pair/generator family')
    rows=binding.get('family_inputs')
    need(type(rows) is list and 0<len(rows)<=MAX_FILES,'pair/generator input inventory is empty or malformed')
    if any(type(row) is dict and 'restored_source_sha256' in row for row in rows):
        raise Gap('relocated pair/generator aliases require a separate adapter','pending')
    if copies is None:raise Gap('pair/generator original input copies are unavailable','pending')
    need(type(copies) is dict and copies,'pair/generator copy mapping is empty or malformed')
    need(binding.get('family_task')==family,'pair/generator family purpose differs')
    dataset=_absolute_record_path(binding['family_dataset_path'])
    source=_absolute_record_path(manifest['source_dataset_dir'])
    version=_absolute_record_path(binding['version_dir'])
    need(version.rsplit('/',1)[-1]==manifest['id'] and version.rsplit('/',2)[-2]=='versions',
         'pair/generator frozen version identity differs')
    original_images={}
    for row in manifest['files']:
        if row.get('kind')!='image':continue
        relative(row['relative_path']);original=_absolute_record_path(row['source_path'])
        need(original==source+'/'+row['relative_path'] and original not in original_images,
             'pair/generator original image identity is foreign or duplicated')
        original_images[original]=row['sha256']
    need(original_images==images,'pair/generator original source inventory differs')
    name='pairs.json' if family=='enhancement' else 'defect_gan.json'
    manifest_path=dataset+'/'+name;snapshot=version+'/labels/family/'+family+'/'+name
    by_relative={};identities=set();order=[]
    for row in rows:
        shape(row,('relative_path','source_path','sha256','snapshot_path'))
        relative(row['relative_path']);original=_absolute_record_path(row['source_path'])
        need(original==dataset+'/'+row['relative_path'] and original not in identities,
             'pair/generator member identity is foreign or duplicated')
        need(type(row['sha256']) is str and HEX.fullmatch(row['sha256']) is not None,
             'pair/generator member digest is invalid')
        need(row['snapshot_path']==(snapshot if original==manifest_path else None),
             'pair/generator frozen snapshot identity differs')
        identities.add(original);order.append(original);by_relative[row['relative_path']]=row
        if row['snapshot_path'] is not None:identities.add(_absolute_record_path(row['snapshot_path']))
    need(order==sorted(order) and name in by_relative,'pair/generator input order or manifest differs')
    need(set(copies)==identities,'pair/generator copied inventory is incomplete or extra')
    need(all(type(path) is str for path in copies.values()) and len(set(copies.values()))==len(copies),
         'pair/generator copies repeat an artifact identity')
    for original,path in copies.items():
        _absolute_record_path(original);relative(path);file(path,False)
        expected=by_relative[name]['sha256'] if original==snapshot else by_relative[original[len(dataset)+1:]]['sha256']
        need(pins[path]['sha256']==expected,'pair/generator copied bytes differ from the producer inventory')
    need(binding.get('family_inputs_sha256')==hashlib.sha256(json.dumps(rows,sort_keys=True,separators=(',',':')).encode()).hexdigest(),
         'pair/generator original input inventory digest differs')
    body=file(copies[manifest_path]);raw=file(copies[manifest_path],False,raw=True)
    need(type(body) is dict and type(body.get('version')) is int and body['version']==1,
         'pair/generator manifest version differs')
    need(file(copies[snapshot])==body,'pair/generator frozen manifest body differs')
    need(body.get('source_dataset_path')==source,'pair/generator original source identity differs')
    counts={'train':0,'val':0,'test':0};members=set();originals=[];hash_splits={}
    if family=='enhancement':
        shape(body,('version','task','mode','source_dataset_path','target_source_path','alignment','records'))
        need(body['task']=='enhancement' and body['mode']=='explicit_pairs',
             'only original explicit enhancement pairs are supported')
        _absolute_record_path(body['target_source_path'])
        need(body['alignment']=='exact pixel dimensions; operator-supplied registration, not automatic alignment',
             'enhancement alignment declaration differs')
        entries=body['records'];need(type(entries) is list and entries,'enhancement pair records are empty or malformed')
        inputs=set();target_splits={}
        for entry in entries:
            shape(entry,('input','target','split','source_relative_path','source_sha256','input_sha256','target_sha256','target_source_relative_path'))
            split=entry['split'];need(type(split) is str and split in counts,'enhancement split is invalid')
            for key in ('input','target','source_relative_path','target_source_relative_path'):relative(entry[key])
            need(entry['input']!=entry['target'] and entry['input'] not in inputs,'enhancement input is repeated or aliases target')
            inputs.add(entry['input'])
            for key in ('input','target'):
                member=entry[key];need(member!=name and member in by_relative,'enhancement pair member is missing')
                need(entry[key+'_sha256']==by_relative[member]['sha256'],'enhancement pair byte digest differs')
                members.add(member)
            original=source+'/'+entry['source_relative_path']
            need(entry['source_sha256']==images.get(original),'enhancement original source digest differs')
            need(entry['input_sha256']==entry['source_sha256'],'explicit enhancement input differs from its original source pixels')
            value=entry['target_sha256'];need(value not in target_splits or target_splits[value]==split,
                 'enhancement target bytes cross original partitions')
            target_splits[value]=split;originals.append(entry['source_relative_path']);counts[split]+=1
        need(all(counts.values()),'enhancement train/val/test partitions are incomplete')
        portable={key:value for key,value in body.items() if key not in {'source_dataset_path','target_source_path','dataset_path','provenance'}}
        expected={'dataset_sha256':hashlib.sha256(json.dumps(portable,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest(),
                  'sample_count':len(entries),'source_dataset_path':source}
        # Original enhancement bind_family_training did not persist a separate
        # family_dataset_sha256. If a later producer supplies one, it must match.
        if 'family_dataset_sha256' in binding:
            need(binding['family_dataset_sha256']==expected['dataset_sha256'],'enhancement family dataset digest differs')
    else:
        shape(body,('version','image_size','samples','source_dataset_path','source_map','provenance'))
        need(type(body['image_size']) is int and body['image_size']==64,'GAN original image size differs')
        entries=body['samples'];mapping=body['source_map']
        need(type(entries) is list and entries and type(mapping) is list and len(mapping)==len(entries),
             'GAN original crop/source mapping is incomplete')
        expected_map={}
        for entry,row in zip(entries,mapping):
            shape(entry,('image','bbox','split','source_sha256','label'))
            shape(row,('image','source_image','source_sha256','source_bbox','split','label'))
            image=entry['image'];relative(image);relative(row['source_image'])
            need(image!=name and image in by_relative and image not in members,'GAN crop image is missing or repeated')
            members.add(image);value=by_relative[image]['sha256'];split=entry['split'];box=entry['bbox']
            need(type(split) is str and split in counts,'GAN original split is invalid')
            need(type(box) is list and len(box)==4 and all(type(v) is int for v in box)
                 and 0<=box[0]<box[2] and 0<=box[1]<box[3] and box[2]-box[0]>=16 and box[3]-box[1]>=16,
                 'GAN original crop bounds are invalid')
            need(type(entry['label']) is str and entry['label'].strip(),'GAN original crop label is invalid')
            need(row=={'image':image,'source_image':row['source_image'],'source_sha256':entry['source_sha256'],
                       'source_bbox':box,'split':split,'label':entry['label']},'GAN source map differs from original crop/split/label')
            need(entry['source_sha256']==value==images.get(source+'/'+row['source_image']),
                 'GAN prepared pixels differ from the original source')
            need(value not in hash_splits or hash_splits[value]==split,'GAN source bytes cross original partitions')
            hash_splits[value]=split;counts[split]+=1;originals.append(row['source_image'])
            expected_map[image]={'source_relative_path':row['source_image'],'source_sha256':value}
        need(counts['train']>0,'GAN original training partition is empty')
        expected={'source_dataset_path':source,'source_map':expected_map,'manifest_sha256':hashlib.sha256(raw).hexdigest()}
        need(body['provenance']=={key:value for key,value in expected.items() if key!='manifest_sha256'},
             'GAN original manifest provenance differs')
        if 'family_dataset_sha256' not in binding:raise Gap('legacy GAN binding has no raw family manifest digest','pending')
        need(binding['family_dataset_sha256']==expected['manifest_sha256'],'GAN raw family manifest digest differs')
    need(set(by_relative)=={name,*members},'pair/generator manifest and full byte inventory differ')
    need(type(binding.get('family_provenance')) is dict and canonical(binding['family_provenance'])==canonical(expected),
         'pair/generator provenance differs from the original manifest and member bytes')
    if 'family_source_image_uuids' not in binding:raise Gap('legacy pair/generator binding has no source UUID inventory','pending')
    eligibility=binding['team_data']['eligibility']
    need(type(eligibility) is list and all(type(row) is dict and type(row.get('relative_path')) is str
         and type(row.get('image_uuid')) is str for row in eligibility),'pair/generator eligibility UUID inventory is malformed')
    inventory={row['relative_path']:row['image_uuid'] for row in eligibility}
    need(len(inventory)==len(eligibility),'pair/generator eligibility repeats an original path')
    settings=binding['team_data'].get('settings')
    if type(settings) is not dict or 'approved_only_training' not in settings:
        raise Gap('legacy pair/generator binding has no approved-only training setting','pending')
    need(type(settings['approved_only_training']) is bool,'pair/generator approved-only training setting is malformed')
    if settings['approved_only_training']:
        need(all(path in inventory for path in originals),'pair/generator input is missing from the approved training inventory')
    need(binding['family_source_image_uuids']==sorted({inventory[path] for path in originals if path in inventory}),
         'pair/generator original source UUID inventory differs')


def _prepared_family_inputs(binding,family,manifest,images,copies,file,pins):
    """Join original OCR/Patch/Rotation records to full raw-pinned copies only.

    This is not a training loader, image decoder or live storage authorization.
    Original producer paths are retained as identities and are never reopened.
    """
    if family=='rotated_detection':return _prepared_rotated_inputs(binding,manifest,images,copies,file,pins)
    if family in ('enhancement','defect_gan'):
        return _prepared_pairs_generator_inputs(binding,family,manifest,images,copies,file,pins)
    if family not in ('ocr','patch_classification','rotation'):
        raise Gap('prepared family format is not supported by this adapter','pending')
    rows=binding.get('family_inputs')
    need(type(rows) is list and 0<len(rows)<=MAX_FILES,'family input inventory is empty or malformed')
    if any(type(row) is dict and 'restored_source_sha256' in row for row in rows):
        raise Gap('relocated family input aliases require a separate adapter','pending')
    if copies is None:raise Gap('prepared family input copies are unavailable','pending')
    need(type(copies) is dict and copies,'family file mapping is empty or malformed')
    need(binding.get('family_task')==family,'prepared family purpose differs from the completed task')
    dataset=_absolute_record_path(binding['family_dataset_path'])
    source=_absolute_record_path(manifest['source_dataset_dir'])
    original_images={}
    for row in manifest['files']:
        if row.get('kind')!='image':continue
        relative(row['relative_path']);original=_absolute_record_path(row['source_path'])
        need(original==source+'/'+row['relative_path'] and original not in original_images,
             'original dataset image identity is foreign or duplicated')
        original_images[original]=row['sha256']
    need(original_images==images,'family original source inventory differs from the immutable dataset')
    version=_absolute_record_path(binding['version_dir'])
    need(version.rsplit('/',1)[-1]==manifest['id'] and version.rsplit('/',2)[-2]=='versions',
         'family snapshot version identity differs')
    name={'ocr':'ocr.json','patch_classification':'patches.json','rotation':'rotation.json'}[family]
    manifest_path=dataset+'/'+name;snapshot=version+'/labels/family/'+family+'/'+name
    by_relative={};identities=set();source_order=[]
    for row in rows:
        shape(row,('relative_path','source_path','sha256','snapshot_path'))
        relative(row['relative_path']);original=_absolute_record_path(row['source_path'])
        need(original==dataset+'/'+row['relative_path'],'family member path differs from its dataset identity')
        need(row['relative_path'] not in by_relative and original not in identities,'duplicate family input member')
        need(type(row['sha256']) is str and HEX.fullmatch(row['sha256']) is not None,'family member digest is invalid')
        need(row['snapshot_path']==(snapshot if original==manifest_path else None),'family frozen snapshot identity differs')
        identities.add(original);source_order.append(original);by_relative[row['relative_path']]=row
        if row['snapshot_path'] is not None:identities.add(_absolute_record_path(row['snapshot_path']))
    need(source_order==sorted(source_order),'family input order differs from the original producer')
    need(name in by_relative,'family inventory has no original manifest member')
    need(set(copies)==identities,'family copied inventory is incomplete or extra')
    need(all(type(path) is str for path in copies.values()) and len(set(copies.values()))==len(copies),
         'family copied inventory repeats an artifact identity')
    for original,path in copies.items():
        _absolute_record_path(original);relative(path);file(path,False)
        expected=by_relative[name]['sha256'] if original==snapshot else by_relative[original[len(dataset)+1:]]['sha256']
        need(pins[path]['sha256']==expected,'family copied bytes differ from the original inventory')
    digest=hashlib.sha256(json.dumps(rows,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    need(binding.get('family_inputs_sha256')==digest,'family input inventory digest differs from the original producer')
    body=file(copies[manifest_path]);raw=file(copies[manifest_path],False,raw=True)
    need(type(body) is dict and type(body.get('version')) is int and body['version']==1,'family manifest version differs')
    need(file(copies[snapshot])==body,'frozen family manifest body differs')
    entries=body.get('patches' if family=='patch_classification' else 'samples')
    need(type(entries) is list and entries,'family manifest has no explicit labeled members')
    hashes={};splits={};hash_splits={};counts={'train':0,'val':0,'test':0};characters=set();rotation_rows=[]
    if family=='patch_classification':
        classes=body.get('classes')
        need(type(classes) is list and len(classes)>=2 and all(type(label) is str and label and label==label.strip() for label in classes)
             and len(set(classes))==len(classes) and body.get('normal_class') in classes,'Patch manifest class identity differs')
        need(type(body.get('patch_size')) is int and body['patch_size']>0 and type(body.get('stride')) is int
             and 1<=body['stride']<=body['patch_size'],'Patch manifest grid is invalid')
    for entry in entries:
        need(type(entry) is dict,'family manifest member is not an object')
        image=entry.get('image');relative(image)
        need(image!=name and image in by_relative,'family manifest references a missing input member')
        digest=by_relative[image]['sha256'];split=entry.get('split')
        need(type(split) is str and split in counts,'family manifest split is invalid')
        need(image not in splits or family=='patch_classification' and splits[image]==split,'family image repeats or crosses partitions')
        need(digest not in hash_splits or hash_splits[digest]==split,'identical family bytes cross partitions')
        splits[image]=split;hash_splits[digest]=split;hashes[image]=digest;counts[split]+=1
        if family=='ocr':
            need(entry.get('source_sha256')==digest,'OCR manifest source digest differs')
            label=entry.get('text')
            need(type(label) is str and label.strip() and all(ord(char)>=32 for char in label),'OCR text identity is invalid')
            if split=='train':characters.update(label)
        elif family=='patch_classification':
            need((entry.get('source_sha256') is None or entry['source_sha256']==digest)
                 and entry.get('label') in classes,'Patch label/source identity differs')
            box=entry.get('box')
            need(type(box) is list and len(box)==4 and all(type(value) is int for value in box)
                 and 0<=box[0]<box[2] and 0<=box[1]<box[3],'Patch box identity is invalid')
        else:
            need(set(entry)=={'image','correction_deg','split','source_sha256'},
                 'Rotation member differs from the supported original format')
            angle=entry['correction_deg']
            need(type(angle) in (int,float) and math.isfinite(angle) and -180<=angle<=180,
                 'Rotation correction angle identity is invalid')
            need(entry['source_sha256']==digest,'Rotation manifest source digest differs')
            rotation_rows.append({'image':image,'correction_deg':(float(angle)+180)%360-180,
                                  'split':split,'source_sha256':digest})
    need(counts['train']>0 and counts['val']>0,'family training partitions are incomplete')
    if family=='ocr':
        need(all(set(entry['text'])<=characters for entry in entries if entry['split']!='train'),
             'OCR held-out text differs from the recorded training alphabet')
    need(set(by_relative)=={name,*hashes},'family manifest and input inventory differ')
    manifest_sha=pins[copies[manifest_path]]['sha256']
    if family=='rotation':
        # Exact _manifest producer domain: ordered normalized correction rows,
        # ASCII-default JSON and an unprefixed digest. This does not decode an
        # image or confer angle truth/quality approval.
        expected={'dataset_sha256':hashlib.sha256(json.dumps(rotation_rows,sort_keys=True,separators=(',',':')).encode()).hexdigest(),
                  'source_sha256':hashes,'split_counts':counts,
                  'angle_semantics':'counterclockwise_upright_correction_degrees_360'}
    else:
        domain=b'ocr-dataset-v1\0'+raw if family=='ocr' else b'patch-classification-dataset-v1\0'+manifest_sha.encode('ascii')
        digest=hashlib.sha256(domain)
        for image,value in sorted(hashes.items()):digest.update(b'\0'+image.encode('utf-8')+b'\0'+value.encode('ascii'))
        expected={'dataset_sha256':'sha256:'+digest.hexdigest(),'manifest_sha256':manifest_sha,
                  'source_sha256':dict(sorted(hashes.items())),'split_counts':counts,'source_image_count':len(hashes)}
        if family=='patch_classification':expected['patch_count']=len(entries)
    mapping=body.get('source_map')
    if family=='rotation':
        need(body.get('source_dataset_path')==source and 'source_map' not in body,
             'Rotation original source mapping differs')
        need(all(images.get(source+'/'+image)==value for image,value in hashes.items()),
             'Rotation member belongs to another original source')
        originals=list(hashes);expected['source_dataset_path']=source
    elif 'source_dataset_path' in body:
        need(body['source_dataset_path']==source and type(mapping) is dict and set(mapping)==set(hashes),
             'prepared family original source mapping differs')
        originals=[]
        for image,value in hashes.items():
            row=shape(mapping[image],('source_relative_path','source_sha256'));relative(row['source_relative_path'])
            original=source+'/'+row['source_relative_path']
            need(row['source_sha256']==value==images.get(original),'prepared member belongs to another original source')
            originals.append(row['source_relative_path'])
        expected.update(source_dataset_path=source,source_map=mapping)
    else:
        if family=='patch_classification':
            raise Gap('legacy Patch record has no producer original source mapping','pending')
        need(dataset==source and mapping is None,'prepared family original source mapping is unavailable')
        need(all(images.get(source+'/'+image)==value for image,value in hashes.items()),'family member belongs to another source')
        originals=list(hashes)
    need(type(binding.get('family_provenance')) is dict and canonical(binding['family_provenance'])==canonical(expected),
         'family provenance differs from original manifest and member bytes')
    if family=='ocr':
        if 'family_dataset_sha256' not in binding:raise Gap('legacy OCR record has no family dataset digest','pending')
        need(binding['family_dataset_sha256']==expected['dataset_sha256'],'OCR training family dataset digest differs')
    if 'family_source_image_uuids' not in binding:raise Gap('legacy family record has no source UUID inventory','pending')
    eligibility=binding['team_data']['eligibility']
    need(type(eligibility) is list and all(type(row) is dict and type(row.get('relative_path')) is str
         and type(row.get('image_uuid')) is str for row in eligibility),'family eligibility UUID inventory is malformed')
    inventory={row['relative_path']:row['image_uuid'] for row in eligibility}
    need(len(inventory)==len(eligibility),'family eligibility repeats an original path')
    settings=binding['team_data'].get('settings')
    if type(settings) is not dict or 'approved_only_training' not in settings:
        raise Gap('legacy family record has no approved-only training setting','pending')
    need(type(settings['approved_only_training']) is bool,'family approved-only training setting is malformed')
    if settings['approved_only_training']:
        need(all(path in inventory for path in originals),'family input is missing from the approved training inventory')
    need(binding['family_source_image_uuids']==sorted({inventory[path] for path in originals if path in inventory}),
         'family source UUID inventory differs')

def _gan_generator_evaluation(context,evaluation,file):
    """A canonical generator diagnostic is distinct from classifier A/B evidence."""
    result=evaluation['result'];binding=context['binding']
    prepared=file(context['gan_family_manifest'])
    test_count=sum(row['split']=='test' for row in prepared['samples'])
    need(type(result) is dict and result.get('split')=='test' and test_count>0,
         'GAN diagnostic has no original held-out test partition')
    need(type(result.get('real_sample_count')) is int and result['real_sample_count']==test_count
         and type(result.get('generated_count')) is int and result['generated_count']==2
         and type(result.get('seed')) is int and result['seed']==41,
         'GAN diagnostic sample count or fixed seed differs')
    score=result.get('rgb_statistics_mmd')
    need(type(score) in (int,float) and math.isfinite(score) and 0<=score<=2
         and result.get('metric_backend')=='RGB mean/std Gaussian-kernel MMD (diagnostic)',
         'GAN diagnostic metric is absent or invalid')
    need(result.get('quality_status')=='unvalidated' and result.get('checkpoint_sha256')==context['checkpoint']
         and result.get('manifest_sha256')==binding['family_dataset_sha256']
         and result.get('test_predictions')==[],
         'GAN diagnostic checkpoint/prepared input or non-verdict contract differs')

def _gan_composition_regions(regions,size):
    need(type(size) is list and len(size)==2 and all(type(v) is int and v>0 for v in size)
         and size[0]*size[1]<=100_000_000,'GAN original composition size exceeds producer bounds')
    need(type(regions) is list and 1<=len(regions)<=32,'GAN source region inventory differs')
    seen=set()
    for row in regions:
        shape(row,('id','bbox','opacity','feather_px','mask_polygon'))
        need(type(row['id']) is str and row['id'] and row['id'] not in seen,'GAN source region identity repeats')
        seen.add(row['id']);box=row['bbox']
        need(type(box) is list and len(box)==4 and all(type(v) is int for v in box),'GAN original region coordinates differ')
        x1,y1,x2,y2=box
        need(0<=x1<x2<=size[0] and 0<=y1<y2<=size[1],'GAN source region exceeds original bounds')
        need(type(row['opacity']) in (int,float) and math.isfinite(row['opacity']) and 0<row['opacity']<=1
             and type(row['feather_px']) is int and 0<=row['feather_px']<=1024,'GAN composition blend policy differs')
        polygon=row['mask_polygon']
        if polygon is not None:
            need(type(polygon) is list and 3<=len(polygon)<=MAX_FILES,'GAN blend polygon inventory differs')
            need(all(type(p) is list and len(p)==2 and all(type(v) in (int,float) and math.isfinite(v) for v in p)
                     and x1<=p[0]<=x2 and y1<=p[1]<=y2 for p in polygon),'GAN blend polygon leaves source region')
            area=abs(sum(a[0]*b[1]-b[0]*a[1] for a,b in zip(polygon,polygon[1:]+polygon[:1])))/2
            need(area>=1,'GAN blend polygon has no positive source area')

def _gan_adoption_records(context,refs,file,pins,evaluation):
    """Join copied original software-control decisions; never grant human approval."""
    shape(refs,('kind','review','source_snapshot','candidate_files','adoption','files','review_scope'))
    need(refs['kind']=='gan_adoption' and refs['review_scope']=='synthetic_control',
         'GAN adoption requires a distinct copied software-control scope')
    _gan_generator_evaluation(context,evaluation,file)
    review=file(refs['review']);audit=file(refs['adoption']);checkpoint=context['checkpoint'];binding=context['binding']
    shape(review,('model_kind','generator_sha256','source_manifest_sha256','quality_status','seed','generation_mode',
                  'source_image_path','source_image_sha256','source_size','source_snapshot','source_snapshot_sha256','regions','candidates'))
    shape(audit,('task','source_dataset_path','generator_sha256','reviewed_at','samples','decisions'))
    source=context['dataset']['source_dataset_dir'];_absolute_record_path(source)
    need(review['model_kind']=='dcgan_defect_crop' and review['generator_sha256']==audit['generator_sha256']==checkpoint
         and review['source_manifest_sha256']==binding['family_dataset_sha256'] and review['quality_status']=='unvalidated',
         'GAN adoption generator or original prepared digest differs')
    need(review['generation_mode']=='source_composition' and type(review['seed']) is int and review['seed']==43
         and audit['task']=='classification' and audit['source_dataset_path']==source,
         'GAN adoption original source or composition mode differs')
    original=_absolute_record_path(review['source_image_path']);snapshot=_absolute_record_path(review['source_snapshot'])
    need(context['images'].get(original)==review['source_image_sha256'],'GAN composition source is outside original pixels')
    file(refs['source_snapshot'],False)
    need(type(review['source_snapshot_sha256']) is str and HEX.fullmatch(review['source_snapshot_sha256']) is not None
         and pins[refs['source_snapshot']]['sha256']==review['source_snapshot_sha256'],
         'GAN preserved source snapshot bytes differ')
    _gan_composition_regions(review['regions'],review['source_size'])
    rows=review['candidates'];need(type(rows) is list and len(rows)==2,'GAN original candidate inventory differs')
    copies=refs['candidate_files'];need(type(copies) is dict and len(copies)==len(rows)
         and len(set(copies.values()))==len(copies),'GAN candidate copies are incomplete or alias')
    candidates={};paths=set();review_root=snapshot.rsplit('/',1)[0]
    for index,row in enumerate(rows):
        shape(row,('id','path','sha256','status','composition_regions','review'))
        path=_absolute_record_path(row['path']);cid=row['id']
        need(cid=='candidate_'+str(index+1).zfill(4) and cid not in candidates and path not in paths
             and path.rsplit('/',1)[0]==review_root and path.rsplit('/',1)[1]=='synthetic_'+cid+'.png',
             'GAN candidate identity or original review directory differs')
        need(type(row['sha256']) is str and HEX.fullmatch(row['sha256']) is not None,'GAN candidate digest is invalid')
        file(copies.get(path),False);need(pins[copies[path]]['sha256']==row['sha256'],'GAN candidate copied pixels differ')
        parts=row['composition_regions'];need(type(parts) is list and len(parts)==len(review['regions']),
             'GAN candidate original region inventory differs')
        for offset,(part,region) in enumerate(zip(parts,review['regions'])):
            shape(part,('id','bbox','opacity','feather_px','mask_polygon','coordinate_space','generated_patch_sha256',
                        'blend_mask_sha256','generation_index','blend_mode','mask_dtype','mask_shape'))
            need(all(part[key]==region[key] for key in region) and part['coordinate_space']=='original_image'
                 and part['blend_mode']=='alpha_source_over' and part['mask_dtype']=='float32',
                 'GAN candidate original coordinate or blend provenance differs')
            need(type(part['generation_index']) is int and part['generation_index']==index*len(parts)+offset
                 and part['mask_shape']==[region['bbox'][3]-region['bbox'][1],region['bbox'][2]-region['bbox'][0]],
                 'GAN candidate generation ordinal or native mask shape differs')
            need(all(type(part[key]) is str and HEX.fullmatch(part[key]) is not None
                     for key in ('generated_patch_sha256','blend_mask_sha256')),'GAN candidate patch/mask digest is invalid')
        candidates[cid]=row;paths.add(path)
    need(set(copies)==paths,'GAN candidate copies contain foreign members')
    need(type(audit['reviewed_at']) is str and audit['reviewed_at'] and type(audit['decisions']) is list
         and len(audit['decisions'])==len(rows),'GAN adoption decision inventory/time differs')
    decisions={};adopted=set();adoption_root=None
    for decision in audit['decisions']:
        shape(decision,('candidate_id','decision','label','reviewer','reason'));cid=decision['candidate_id']
        need(cid in candidates and cid not in decisions and decision['decision'] in ('adopt','reject'),
             'GAN adoption decision identity repeats or differs')
        need(decision['reviewer']=='Codex synthetic software qualification'
             and decision['reason']=='Synthetic control transition only; no human or model-quality approval',
             'GAN historical software review cannot become human approval')
        recorded=candidates[cid]['review'];shape(recorded,(*decision.keys(),'dataset_path','reviewed_at'))
        root=_absolute_record_path(recorded['dataset_path'])
        need(all(recorded[k]==v for k,v in decision.items()) and recorded['reviewed_at']==audit['reviewed_at']
             and (adoption_root is None or adoption_root==root),'GAN adoption decision/review/time joins differ')
        need(not root.startswith(source+'/') and root!=source,'GAN adoption cannot alias original source')
        adoption_root=root
        need(candidates[cid]['status']==('synthetic_adopted' if decision['decision']=='adopt' else 'synthetic_rejected'),
             'GAN candidate reviewed state differs')
        if decision['decision']=='adopt':adopted.add(cid)
        else:need(decision['label'] is None,'GAN rejected candidate has a defect label')
        decisions[cid]=decision
    need(adopted,'GAN adoption has no original selected candidate')
    samples=audit['samples'];copies=refs['files']
    need(type(samples) is list and 0<len(samples)<=MAX_FILES and type(copies) is dict
         and len(copies)==len(samples) and len(set(copies.values()))==len(copies),
         'GAN adoption copied inventory is incomplete or aliases')
    originals={};classes=set()
    for row in context['dataset']['files']:
        if row.get('kind')!='image':continue
        parts=relative(row['relative_path']);need(len(parts)==3 and parts[0] in ('train','val','test'),
             'GAN original classification split/class layout is unsupported')
        path=_absolute_record_path(row['source_path'])
        need(path==source+'/'+row['relative_path'] and path not in originals,'GAN original real pixel identity repeats')
        originals[path]=(row['sha256'],parts[0],parts[1])
        if parts[0]=='train':classes.add(parts[1])
    real=set();synthetic=set();destinations=set()
    for sample in samples:
        need(type(sample) is dict,'GAN adoption sample is not an object')
        image=sample['image'];parts=relative(image)
        need(len(parts)==3 and image not in destinations and parts[:2]==[sample['split'],sample['label']],
             'GAN adoption destination split/class identity differs')
        destinations.add(image);file(copies.get(image),False);copied=pins[copies[image]]['sha256']
        if sample.get('kind')=='real':
            shape(sample,('kind','source_image','source_sha256','image','split','label'))
            original=_absolute_record_path(sample['source_image'])
            need(original in originals and original not in real and originals[original]==
                 (sample['source_sha256'],sample['split'],sample['label']) and copied==sample['source_sha256'],
                 'GAN adoption changed/duplicated original real pixels or split')
            real.add(original)
        else:
            shape(sample,('kind','image','split','label','candidate_id','source_sha256','generator_sha256','reviewer','reason',
                          'source_image_sha256','composition_regions','generation_seed'))
            cid=sample['candidate_id'];need(sample['kind']=='synthetic_reviewed' and cid in adopted and cid not in synthetic,
                 'GAN adoption includes an unselected/duplicate synthetic candidate')
            decision=decisions[cid];candidate=candidates[cid];label=sample['label']
            need(type(label) is str and label in classes and label==decision['label']
                 and label.casefold() not in ('ok','normal','good','pass','정상') and sample['split']=='train',
                 'GAN generated pixels require an existing defect label and train only')
            need(sample['source_sha256']==copied==candidate['sha256'] and sample['generator_sha256']==checkpoint
                 and sample['reviewer']==decision['reviewer'] and sample['reason']==decision['reason']
                 and sample['source_image_sha256']==review['source_image_sha256']
                 and sample['composition_regions']==candidate['composition_regions']
                 and type(sample['generation_seed']) is int and sample['generation_seed']==review['seed'],
                 'GAN adoption generated pixels or original review provenance differs')
            synthetic.add(cid)
    need(real==set(originals) and synthetic==adopted and set(copies)==destinations,
         'GAN adoption omitted original pixels or added foreign copies')

def _gan_generation_records(body,copies,file,pins,context):
    shape(body,('model_kind','generator_sha256','source_manifest_sha256','quality_status','seed','candidates'))
    need(body['model_kind']=='dcgan_defect_crop' and body['generator_sha256']==context['checkpoint']
         and body['source_manifest_sha256']==context['binding']['family_dataset_sha256']
         and body['quality_status']=='unvalidated' and type(body['seed']) is int and body['seed']==41,
         'GAN generation original checkpoint/prepared input/seed differs')
    rows=body['candidates'];need(type(rows) is list and len(rows)==2 and type(copies) is dict
         and len(copies)==2 and len(set(copies.values()))==2,'GAN generation copied inventory differs')
    paths=set();hashes=[];directory=None
    for index,row in enumerate(rows):
        shape(row,('id','path','sha256','status'));path=_absolute_record_path(row['path']);parent=path.rsplit('/',1)[0]
        need(row['id']=='candidate_'+str(index+1).zfill(4) and path.rsplit('/',1)[1]=='synthetic_'+row['id']+'.png' and path not in paths
             and (directory is None or directory==parent) and row['status']=='synthetic_unreviewed',
             'GAN generation candidate order/path/state differs')
        file(copies.get(path),False);need(pins[copies[path]]['sha256']==row['sha256'] and pins[copies[path]]['size']>0,
             'GAN generation copied candidate bytes differ')
        paths.add(path);directory=parent;hashes.append(row['sha256'])
    need(set(copies)==paths,'GAN generation contains foreign copied candidates')
    return hashes,paths

def _gan_package_records(context,refs,file,pins):
    """New generator parity records, never relabeled legacy flow parity booleans."""
    shape(refs,('manifest','parity','reference_code','reference_manifest','reference_files','packaged_manifest','packaged_files'))
    manifest=file(refs['manifest']);shape(manifest,('schema_version','task','generator_sha256','files'))
    need(type(manifest['schema_version']) is int and manifest['schema_version']==1 and manifest['task']=='defect_gan'
         and manifest['generator_sha256']==context['checkpoint'],'GAN package original generator identity differs')
    prefix='/'.join(relative(refs['manifest'])[:-1]);prefix=(prefix+'/') if prefix else ''
    rows=manifest['files'];need(type(rows) is list and 0<len(rows)<=MAX_FILES,'GAN package member inventory differs')
    members={}
    for row in rows:
        shape(row,('path','sha256'));relative(row['path']);name=row['path']
        need(name not in members and name!='manifest.json','GAN package member is duplicated or self-referential')
        path=prefix+name;file(path,False);need(pins[path]['sha256']==row['sha256'],'GAN package member bytes differ')
        members[name]=path
    need({'best_model.pt','model_meta.json','generate.py','workflow.json','backend/engine/gan_package_runtime.py'}<=set(members),
         'GAN package lacks the original generator/entry/runtime contract')
    need(pins[members['best_model.pt']]['sha256']==context['checkpoint']
         and pins[members['model_meta.json']]==context['gan_metadata_pin']
         and file(members['model_meta.json'])==context['gan_metadata'],'GAN package checkpoint/metadata bytes differ')
    workflow=file(members['workflow.json']);shape(workflow,('task','stages','output_state','quality_status'))
    need(workflow=={'task':'defect_gan','stages':['explicit_defect_crops','trained_generator','heldout_diagnostic',
         'generate_unreviewed','human_review','adopt_train_only'],'output_state':'synthetic_unreviewed','quality_status':'unvalidated'},
         'GAN package workflow cannot grant verdicts or quality approval')
    parity=file(refs['parity']);shape(parity,('schema_version','contract','status','task','generator_sha256',
        'source_manifest_sha256','manifest_sha256','workflow_sha256','seed','count','candidate_sha256','reference_runtime',
        'packaged_runtime','error','quality_approved','human_review_approved'))
    need(type(parity['schema_version']) is int and parity['schema_version']==1
         and parity['contract']=='gan_generation_parity_v1' and parity['status']=='passed' and parity['task']=='defect_gan'
         and parity['error'] is None and parity['quality_approved'] is False and parity['human_review_approved'] is False,
         'GAN parity is not a new passed non-approval generator record')
    need(parity['generator_sha256']==context['checkpoint'] and parity['source_manifest_sha256']==context['binding']['family_dataset_sha256']
         and parity['manifest_sha256']==pins[refs['manifest']]['sha256']
         and parity['workflow_sha256']==pins[members['workflow.json']]['sha256']
         and type(parity['seed']) is int and parity['seed']==41 and type(parity['count']) is int and parity['count']==2,
         'GAN parity package/source/seed/count binding differs')
    left,left_paths=_gan_generation_records(file(refs['reference_manifest']),refs['reference_files'],file,pins,context)
    right,right_paths=_gan_generation_records(file(refs['packaged_manifest']),refs['packaged_files'],file,pins,context)
    need(left==right==parity['candidate_sha256'] and left_paths.isdisjoint(right_paths)
         and set(refs['reference_files'].values()).isdisjoint(refs['packaged_files'].values()),
         'GAN independent generation copied outputs differ or alias')
    reference=parity['reference_runtime'];packaged=parity['packaged_runtime']
    shape(reference,('kind','device','cpu_threads','process_id','process_birth','code_sha256'))
    shape(packaged,('kind','device','cpu_threads','process_id','process_birth','independent_process',
                    'package_runner_sha256','package_runtime_sha256'))
    file(refs['reference_code'],False)
    need(reference['kind']=='owned_python_generator' and reference['code_sha256']==pins[refs['reference_code']]['sha256']
         and packaged['kind']=='isolated_python_generator_runner' and packaged['independent_process'] is True
         and packaged['package_runner_sha256']==pins[members['generate.py']]['sha256']
         and packaged['package_runtime_sha256']==pins[members['backend/engine/gan_package_runtime.py']]['sha256'],
         'GAN reference/package runtime source bytes or independence differs')
    for runtime in (reference,packaged):
        need(runtime['device']=='cpu' and type(runtime['cpu_threads']) is int and runtime['cpu_threads']==1
             and type(runtime['process_id']) is int and runtime['process_id']>0
             and type(runtime['process_birth']) in (int,float) and math.isfinite(runtime['process_birth']) and runtime['process_birth']>0,
             'GAN recorded source CPU process declaration differs')
    need(reference['process_id']!=packaged['process_id'],'GAN recorded reference/package processes are not distinct')
    context['gan_parity']=parity

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
    def file(path,parse=True,*,raw=False):
        if path is None:raise Gap('missing artifact reference','pending')
        relative(path);used.add(path)
        if path not in pins:raise Gap('referenced artifact is not pinned')
        if path in failures:
            error=failures[path]
            if isinstance(error,Gap):raise error
            raise Gap('artifact bytes or JSON are invalid') from error
        return _read_pin(root,path,pins[path],parse,raw=True) if raw else _read_pin(root,path,pins[path],parse)
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
        require('dataset','labels');refs=shape(receipt['train'],('receipt','metadata','checkpoint','family_files')
            if type(receipt['train']) is dict and 'family_files' in receipt['train'] else ('receipt','metadata','checkpoint'))
        job=file(refs['receipt']);meta=file(refs['metadata']);file(refs['checkpoint'],False);manifest=context['dataset']
        required=('job_id','task','status','source_dataset_path','dataset_fingerprint','training_provenance','checkpoint_sha256')
        need(type(job) is dict and type(meta) is dict,'training receipt or metadata is not an object')
        # Original enhancement publish omitted only the top fingerprint; its
        # byte-pinned nested training binding and exact producer shape remain.
        legacy_enhancement=(family=='enhancement' and 'dataset_fingerprint' not in job and
            set(job)=={'job_id','task','status','source_dataset_path','dataset_path','training_provenance','checkpoint_sha256'})
        if not all(key in job for key in required) and not legacy_enhancement:
            raise Gap('unsupported completed training receipt','pending')
        need(job['status']=='completed' and job['task']==family,'training is incomplete or family differs')
        cp=pins[refs['checkpoint']]['sha256'];binding=job['training_provenance']
        need(type(binding) is dict,'training provenance is not an object')
        need(job['checkpoint_sha256']==cp==meta['checkpoint_sha256'] and meta['task']==family,'checkpoint or metadata family differs')
        need(meta['training_provenance']==binding,'checkpoint metadata provenance differs')
        if legacy_enhancement:
            need(binding.get('family_task')=='enhancement' and type(binding.get('family_inputs')) is list
                 and 0<len(binding['family_inputs'])<=MAX_FILES,'legacy enhancement receipt has no original prepared pair binding')
        recorded_fingerprint=binding.get('dataset_fingerprint') if legacy_enhancement else job['dataset_fingerprint']
        need(job['source_dataset_path']==manifest['source_dataset_dir'] and recorded_fingerprint==manifest['dataset_fingerprint'],'training source differs')
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
        if 'family_inputs' in binding:need(type(binding['family_inputs']) is list,'family input inventory is not a list')
        if binding.get('family_inputs') or any(key in binding for key in ('family_task','family_dataset_path','family_provenance')):
            if family in ('ocr','patch_classification','rotation'):
                if 'dataset_path' not in job or family in ('ocr','rotation') and 'dataset_path' not in meta:
                    raise Gap('legacy prepared training record has no dataset identity','pending')
                need(type(job['dataset_path']) is str and job['dataset_path']==binding.get('family_dataset_path'),
                     'prepared training dataset identities differ')
                # Generic Patch metadata persists the entire original binding,
                # already compared above, but does not add dataset_path itself.
                if 'dataset_path' in meta:
                    need(type(meta['dataset_path']) is str and meta['dataset_path']==job['dataset_path'],
                         'prepared checkpoint metadata dataset identity differs')
            if family=='rotated_detection':
                if 'dataset_path' not in job or 'dataset_path' not in meta:
                    raise Gap('legacy OBB training record has no dataset identity','pending')
                need(type(job['dataset_path']) is str and job['dataset_path']==binding.get('family_dataset_path'),
                     'OBB prepared training dataset identities differ')
                need(type(meta['dataset_path']) is str and meta['dataset_path']==job['dataset_path'],
                     'OBB checkpoint metadata dataset identity differs')
            if family in ('enhancement','defect_gan'):
                if 'dataset_path' not in job or 'dataset_path' not in meta:
                    raise Gap('legacy pair/generator record has no prepared dataset identity','pending')
                need(type(job['dataset_path']) is str and job['dataset_path']==binding.get('family_dataset_path')==meta['dataset_path'],
                     'pair/generator prepared dataset identities differ')
                need(meta.get('source_dataset_path')==manifest['source_dataset_dir'],
                     'pair/generator checkpoint original source identity differs')
                if family=='enhancement':
                    need(type(meta.get('version')) is int and meta['version']==1 and meta.get('architecture')=='rgb_residual_cnn'
                         and meta.get('mode')=='explicit_pairs','enhancement original checkpoint producer differs')
                    need(meta.get('provenance')==binding.get('family_provenance')==meta.get('dataset_provenance'),
                         'enhancement checkpoint pair provenance differs')
                else:
                    need(meta.get('model_kind')=='dcgan_defect_crop' and type(meta.get('image_size')) is int
                         and meta['image_size']==64,'GAN original checkpoint producer differs')
                    need(meta.get('source_manifest_sha256')==binding.get('family_dataset_sha256'),
                         'GAN checkpoint original manifest digest differs')
            _prepared_family_inputs(binding,family,manifest,context['images'],refs.get('family_files'),file,pins)
        else:
            need('family_files' not in refs,'family copies are declared without prepared input records')
        context.update(job=job,checkpoint=cp,binding=binding)
    def evaluation():
        require('train');body=file(receipt['eval']);binding=body['binding'];manifest=context['dataset'];job=context['job']
        need(type(body['evaluation_id']) is str and re.fullmatch(r'evaluation_[0-9a-f]{32}',body['evaluation_id']) is not None,'evaluation identity is invalid')
        need(body['evidence_sha256']==hashlib.sha256(canonical({k:v for k,v in body.items() if k!='evidence_sha256'})).hexdigest(),'evaluation semantic digest differs')
        need(body['result']['job_id']==job['job_id'] and body['result']['task']==family,'evaluation job/family differs')
        for key,expected in (('source_dataset_path',manifest['source_dataset_dir']),('dataset_fingerprint',manifest['dataset_fingerprint']),('checkpoint_sha256',context['checkpoint']),('labelset_id',manifest['labelset_id']),('training_labelset_id',manifest['labelset_id']),('training_provenance',context['binding'])):
            need(binding[key]==expected,'evaluation source/checkpoint/truth provenance differs')
    def flow():
        if family=='defect_gan':
            if type(receipt['flow_or_adoption']) is not dict or receipt['flow_or_adoption'].get('kind')!='gan_adoption':
                raise Gap('GAN generation/adoption receipt format','pending')
            require('eval')
            family_copies=receipt['train']['family_files']
            context['gan_family_manifest']=family_copies[context['binding']['family_dataset_path']+'/defect_gan.json']
            context['gan_metadata']=file(receipt['train']['metadata'])
            context['gan_metadata_pin']=dict(pins[receipt['train']['metadata']])
            _gan_adoption_records(context,receipt['flow_or_adoption'],file,pins,file(receipt['eval']))
            return
        require('eval');refs=shape(receipt['flow_or_adoption'],('kind','graph'))
        if refs['kind']!='flow':raise Gap('unsupported adoption receipt format','pending')
        graph=file(refs['graph']);jobs,nodes=_model_jobs(graph);job=context['job']['job_id']
        need(jobs.get(job)==family,'saved flow does not reference the original trained job')
        graph_nodes,graph_edges=_graph_identities(graph)
        output_nodes={node['id'] for node in graph['nodes'] if node['data'].get('node_type')=='output'}
        context.update(graph=graph,jobs=jobs,selected_nodes=nodes[job],graph_nodes=graph_nodes,graph_edges=graph_edges,
                       output_nodes=output_nodes)
    def export():
        require('flow_or_adoption')
        if family=='defect_gan':
            _gan_package_records(context,receipt['export'],file,pins)
            return
        refs=shape(receipt['export'],('manifest','parity'));manifest=file(refs['manifest'])
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
        if family=='defect_gan':
            need(refs['device']=='cpu' and context['gan_parity']['reference_runtime']['device']=='cpu'
                 and context['gan_parity']['packaged_runtime']['device']=='cpu','recorded GAN target device differs')
            return
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
