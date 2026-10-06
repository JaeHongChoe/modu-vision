"""S6-03 reviewed local runtime inventory. Never installs, imports, or fetches.

The expected inventory SHA comes from a separately reviewed source/release.
This unsigned inventory is not a substitute for the signed release authority
or a successful worker preflight on the target hardware.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
from urllib.parse import urlsplit

DESCRIPTOR_FIELDS = {'id','version','kind','platform','arch','source','license','driver_minimum','compatibility','files'}
MANIFEST_FIELDS = DESCRIPTOR_FIELDS | {'schema_version','total_bytes'}


def canonical_bytes(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode('utf-8')


def _driver(value):
    if not isinstance(value,str) or not re.fullmatch(r'[0-9]{1,5}(?:\.[0-9]{1,5}){1,3}',value):
        raise ValueError('Explicit numeric NVIDIA driver version is required')
    return tuple(int(x) for x in value.split('.')) + (0,) * (4-len(value.split('.')))


def _descriptor(value):
    if not isinstance(value,dict) or set(value)!=DESCRIPTOR_FIELDS: raise ValueError('Invalid runtime pack descriptor fields')
    if not isinstance(value['id'],str) or not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,79}',value['id']): raise ValueError('Invalid pack ID')
    if not isinstance(value['version'],str) or not re.fullmatch(r'[0-9]{1,6}\.[0-9]{1,6}\.[0-9]{1,6}',value['version']): raise ValueError('Invalid version')
    if value['kind'] not in {'nvidia','ocr','dicom','openvino'} or value['platform'] not in {'linux','windows','darwin'} or value['arch'] not in {'x64','arm64'}:
        raise ValueError('Unsupported pack kind or target')
    try: source = urlsplit(value['source'])
    except (TypeError,ValueError): raise ValueError('Reviewed HTTPS source is required') from None
    if source.scheme!='https' or not source.hostname or source.username or source.password or source.fragment or source.query:
        raise ValueError('Reviewed HTTPS source without credentials or query is required')
    if not isinstance(value['license'],str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9.+-]{0,99}',value['license']): raise ValueError('Explicit reviewed license identifier is required')
    if value['kind']=='nvidia': _driver(value['driver_minimum'])
    elif value['driver_minimum'] is not None: raise ValueError('Only NVIDIA packs declare a driver minimum')
    compat=value['compatibility']
    if not isinstance(compat,dict) or set(compat)!={'worker','runtime'} or any(type(v) is not int or not 1<=v<=10000 for v in compat.values()):
        raise ValueError('Exact worker/runtime protocol versions are required')


def _relative(value):
    if not isinstance(value,str) or len(value)>240: raise ValueError('Invalid pack file path')
    parts=PurePosixPath(value).parts
    if not 1<=len(parts)<=8 or str(PurePosixPath(value))!=value or PurePosixPath(value).is_absolute(): raise ValueError('Unsafe pack file path')
    for part in parts:
        if not re.fullmatch(r'[A-Za-z0-9_-][A-Za-z0-9._-]{0,99}',part) or part.endswith('.') or part.split('.')[0].upper() in {'CON','PRN','AUX','NUL',*(f'COM{i}' for i in range(10)),*(f'LPT{i}' for i in range(10))}:
            raise ValueError('Unsafe or reserved pack file path')
    return value


def _files(root):
    root=Path(root).absolute()
    if root.is_symlink() or not root.is_dir(): raise ValueError('Unlinked local pack directory is required')
    root=root.resolve(); result=[]
    for base,dirs,files in os.walk(root,followlinks=False):
        for name in dirs+files:
            p=Path(base)/name
            if p.is_symlink(): raise ValueError('Pack symlinks are refused')
            if name in files:
                if not stat.S_ISREG(p.lstat().st_mode): raise ValueError('Pack special files are refused')
                result.append(_relative(p.relative_to(root).as_posix()))
                if len(result)>2048: raise ValueError('Runtime inventory exceeds 2048 files')
    if not result or len({p.casefold() for p in result})!=len(result): raise ValueError('Empty or ambiguous pack inventory')
    return root,sorted(result)


def _hash(root,relative):
    file=root/relative
    for p in (file,*file.parents):
        if p.is_symlink(): raise ValueError('Pack path changed to a link')
    before=file.lstat()
    fd=os.open(file,os.O_RDONLY|getattr(os,'O_NOFOLLOW',0)); digest=hashlib.sha256()
    try:
        opened=os.fstat(fd)
        if not stat.S_ISREG(opened.st_mode) or not 0<opened.st_size<=16*1024**3: raise ValueError('Pack payload must be a bounded nonempty regular file')
        consumed=0
        while True:
            block=os.read(fd,min(1024*1024,opened.st_size-consumed+1))
            if not block: break
            consumed+=len(block)
            if consumed>opened.st_size: raise ValueError('Pack payload grew while verifying')
            digest.update(block)
        after=os.fstat(fd); current=file.lstat()
        identity=lambda s:(s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns)
        if len({identity(s) for s in (before,opened,after,current)})!=1: raise ValueError('Pack bytes changed while verifying')
        return {'path':relative,'size':opened.st_size,'sha256':digest.hexdigest()}
    finally: os.close(fd)


def inventory_pack(root,descriptor):
    _descriptor(descriptor)
    listed=descriptor['files']
    if not isinstance(listed,list) or not 1<=len(listed)<=2048 or any(not isinstance(p,str) for p in listed): raise ValueError('Explicit file inventory is required')
    root,actual=_files(root)
    expected=sorted(_relative(p) for p in listed)
    if actual!=expected: raise ValueError('Pack contains missing, extra or duplicate files')
    records=[_hash(root,p) for p in actual]; total=sum(r['size'] for r in records)
    if total>64*1024**3: raise ValueError('Pack exceeds inventory size limit')
    if _files(root)[1]!=actual: raise ValueError('Pack inventory changed while verifying')
    return {**descriptor,'schema_version':1,'files':records,'total_bytes':total}


def verify_pack(root,raw,*,expected_sha256,platform,arch,compatibility,free_bytes,driver_version=None):
    if not isinstance(raw,bytes) or not 1<=len(raw)<=1024*1024: raise ValueError('Bounded inventory bytes are required')
    if not isinstance(expected_sha256,str) or not re.fullmatch(r'[a-f0-9]{64}',expected_sha256) or hashlib.sha256(raw).hexdigest()!=expected_sha256:
        raise ValueError('Inventory differs from the independently reviewed SHA-256')
    manifest=json.loads(raw)
    if not isinstance(manifest,dict) or set(manifest)!=MANIFEST_FIELDS or type(manifest['schema_version']) is not int or manifest['schema_version']!=1 or canonical_bytes(manifest)!=raw:
        raise ValueError('Noncanonical or unsupported runtime inventory')
    if not isinstance(manifest['files'],list) or not 1<=len(manifest['files'])<=2048: raise ValueError('Invalid file inventory')
    for row in manifest['files']:
        if not isinstance(row,dict) or set(row)!={'path','size','sha256'} or type(row['size']) is not int or not 0<row['size']<=16*1024**3 or not isinstance(row['sha256'],str) or not re.fullmatch(r'[a-f0-9]{64}',row['sha256']):
            raise ValueError('Invalid file inventory record')
        _relative(row['path'])
    descriptor={k:manifest[k] for k in DESCRIPTOR_FIELDS}; descriptor['files']=[r['path'] for r in manifest['files']]
    _descriptor(descriptor)
    if not isinstance(compatibility,dict) or any(type(v) is not int for v in compatibility.values()): raise ValueError('Observed protocol versions must be integers')
    if (platform,arch,compatibility)!=(manifest['platform'],manifest['arch'],manifest['compatibility']): raise ValueError('Target or protocol compatibility differs from this pack')
    if manifest['kind']=='nvidia' and _driver(driver_version)<_driver(manifest['driver_minimum']): raise ValueError('NVIDIA driver is below the declared minimum')
    if type(manifest['total_bytes']) is not int or type(free_bytes) is not int or free_bytes<manifest['total_bytes']*3: raise ValueError('Insufficient estimated staging space')
    actual=inventory_pack(root,descriptor)
    if actual!=manifest: raise ValueError('Runtime payload bytes or sizes differ from the reviewed inventory')
    return {'schema_version':1,'state':'inventory_verified_runtime_unqualified','id':manifest['id'],
            'kind':manifest['kind'],'platform':platform,'arch':arch,'compatibility':compatibility,
            'inventory_sha256':expected_sha256,'total_bytes':manifest['total_bytes'],
            'estimated_staging_bytes':manifest['total_bytes']*3,'source':manifest['source'],'license':manifest['license'],
            'driver_minimum':manifest['driver_minimum'],'observed_driver':driver_version,
            'installed':False,'signature_verified':False,'execution_verified':False,'files':manifest['files']}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode',choices=['inventory','verify']); parser.add_argument('--root',type=Path,required=True)
    parser.add_argument('--document',type=Path,required=True); parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--expected-sha256'); parser.add_argument('--platform'); parser.add_argument('--arch')
    parser.add_argument('--worker-protocol',type=int); parser.add_argument('--runtime-protocol',type=int); parser.add_argument('--driver-version')
    args=parser.parse_args()
    if args.document.stat().st_size>1024*1024: raise ValueError('Inventory document exceeds 1MiB')
    raw=args.document.read_bytes()
    if args.mode=='inventory': result=inventory_pack(args.root,json.loads(raw))
    else: result=verify_pack(args.root,raw,expected_sha256=args.expected_sha256,platform=args.platform,arch=args.arch,
                             compatibility={'worker':args.worker_protocol,'runtime':args.runtime_protocol},
                             driver_version=args.driver_version,free_bytes=shutil.disk_usage(args.root).free)
    with args.output.open('xb') as out: out.write(canonical_bytes(result)); out.flush(); os.fsync(out.fileno())
    print(json.dumps({'output':str(args.output),'sha256':hashlib.sha256(canonical_bytes(result)).hexdigest(),
                      'installed':False,'execution_verified':False}))


if __name__=='__main__': main()
