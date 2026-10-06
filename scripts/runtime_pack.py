"""S6-03 reviewed local runtime inventory and inert, isolated installation.

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
import tempfile
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


def _copy_payload(source, destination, files):
    """Copy bounded reviewed bytes; never import, unpack, or run a payload."""
    for row in files:
        path = source / row['path']
        if any(p.is_symlink() for p in (path, *path.parents)):
            raise ValueError('Source pack became linked during installation')
        target = destination / row['path']; target.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
        try:
            original = os.fstat(fd)
            if not stat.S_ISREG(original.st_mode) or original.st_size != row['size']:
                raise ValueError('Source payload changed before copying')
            digest = hashlib.sha256(); count = 0
            with target.open('xb') as writer:
                while block := os.read(fd, min(1024 * 1024, row['size'] - count + 1)):
                    count += len(block)
                    if count > row['size']: raise ValueError('Source payload grew during copying')
                    digest.update(block); writer.write(block)
                writer.flush(); os.fsync(writer.fileno())
            current = os.fstat(fd)
            if (count != row['size'] or digest.hexdigest() != row['sha256']
                    or (original.st_dev,original.st_ino,original.st_size,original.st_mtime_ns)
                    != (current.st_dev,current.st_ino,current.st_size,current.st_mtime_ns)):
                raise ValueError('Source payload changed during copying')
        finally: os.close(fd)


def _publish_directory(source,target):
    """Atomic no-replace publication; unsupported filesystems are refused."""
    import ctypes
    import errno
    import sys
    if os.name=='nt':
        os.rename(source,target)
        return
    library=ctypes.CDLL(None,use_errno=True)
    if sys.platform=='darwin':
        function=getattr(library,'renamex_np',None)
        arguments=(os.fsencode(source),os.fsencode(target),4)  # RENAME_EXCL in SDK sys/stdio.h
        types=(ctypes.c_char_p,ctypes.c_char_p,ctypes.c_uint)
    elif sys.platform.startswith('linux'):
        function=getattr(library,'renameat2',None)
        arguments=(-100,os.fsencode(source),-100,os.fsencode(target),1)  # AT_FDCWD, RENAME_NOREPLACE
        types=(ctypes.c_int,ctypes.c_char_p,ctypes.c_int,ctypes.c_char_p,ctypes.c_uint)
    else:function=None
    if function is None:raise OSError(errno.ENOTSUP,'Atomic no-replace publication is unavailable')
    function.argtypes=types;function.restype=ctypes.c_int
    if function(*arguments)!=0:
        code=ctypes.get_errno();raise OSError(code,os.strerror(code),str(target))


def install_pack(root, raw, *, store, expected_sha256, platform, arch, compatibility, driver_version=None):
    """Publish an immutable inactive installation after source and copy checks.

    The store must already be an explicit local directory. No system dependency,
    activation pointer, driver, service, environment or existing pack is changed.
    Independently reviewed inventory pins remain necessary; installation supplies
    neither publisher identity nor target execution qualification.
    """
    source, store = Path(root).absolute(), Path(store).absolute()
    for directory in (source, store):
        if (any(p.is_symlink() for p in (directory,*directory.parents))
                or not directory.is_dir()):
            raise ValueError('Installation source and store must be explicit unlinked directories')
    source, store = source.resolve(), store.resolve()
    if source.is_relative_to(store) or store.is_relative_to(source):
        raise ValueError('Installation store and source pack must be disjoint')
    args = dict(expected_sha256=expected_sha256, platform=platform, arch=arch,
                compatibility=compatibility, driver_version=driver_version)
    verified = verify_pack(source, raw, free_bytes=shutil.disk_usage(store).free, **args)
    target = store / f"{verified['id']}-{json.loads(raw)['version']}-{expected_sha256}"
    receipt = {**verified, 'state':'installed_inactive_runtime_unqualified', 'installed':True,
               'activated':False, 'installation_path':str(target)}

    def existing():
        if target.is_symlink() or not target.is_dir():
            raise ValueError('Existing installation is linked or not a directory')
        if {p.name for p in target.iterdir()} != {'payload','inventory.json','receipt.json'}:
            raise ValueError('Existing installation contains unreviewed entries')
        for name in ('inventory.json','receipt.json'):
            file = target/name
            if file.is_symlink() or not file.is_file() or file.stat().st_size>1024*1024:
                raise ValueError('Existing installation record is unavailable')
        if (target/'inventory.json').read_bytes() != raw or (target/'receipt.json').read_bytes() != canonical_bytes(receipt):
            raise ValueError('Existing installation records differ from the reviewed installation')
        verify_pack(target/'payload', raw, free_bytes=shutil.disk_usage(store).free, **args)
        return receipt

    if target.exists() or target.is_symlink(): return existing()
    with tempfile.TemporaryDirectory(prefix='.pack-', dir=store) as temporary:
        staging = Path(temporary)/'installation'; payload = staging/'payload'; payload.mkdir(parents=True)
        _copy_payload(source, payload, verified['files'])
        verify_pack(payload, raw, free_bytes=shutil.disk_usage(store).free, **args)
        verify_pack(source, raw, free_bytes=shutil.disk_usage(store).free, **args)
        for name, data in (('inventory.json',raw),('receipt.json',canonical_bytes(receipt))):
            with (staging/name).open('xb') as writer:
                writer.write(data); writer.flush(); os.fsync(writer.fileno())
        if target.exists() or target.is_symlink(): return existing()
        # Publish the complete nonempty directory. A competing publisher may win;
        # its bytes must verify identically, and are never overwritten/repaired.
        try: _publish_directory(staging, target)
        except OSError:
            if target.exists() or target.is_symlink(): return existing()
            raise
    return existing()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode',choices=['inventory','verify','install']); parser.add_argument('--root',type=Path,required=True)
    parser.add_argument('--store',type=Path)
    parser.add_argument('--document',type=Path,required=True); parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--expected-sha256'); parser.add_argument('--platform'); parser.add_argument('--arch')
    parser.add_argument('--worker-protocol',type=int); parser.add_argument('--runtime-protocol',type=int); parser.add_argument('--driver-version')
    args=parser.parse_args()
    if args.document.stat().st_size>1024*1024: raise ValueError('Inventory document exceeds 1MiB')
    raw=args.document.read_bytes()
    if args.mode=='inventory': result=inventory_pack(args.root,json.loads(raw))
    elif args.mode=='install':
        if args.store is None: raise ValueError('An explicit existing local --store is required for installation')
        result=install_pack(args.root,raw,store=args.store,expected_sha256=args.expected_sha256,platform=args.platform,arch=args.arch,
                            compatibility={'worker':args.worker_protocol,'runtime':args.runtime_protocol},driver_version=args.driver_version)
    else: result=verify_pack(args.root,raw,expected_sha256=args.expected_sha256,platform=args.platform,arch=args.arch,
                             compatibility={'worker':args.worker_protocol,'runtime':args.runtime_protocol},
                             driver_version=args.driver_version,free_bytes=shutil.disk_usage(args.root).free)
    with args.output.open('xb') as out: out.write(canonical_bytes(result)); out.flush(); os.fsync(out.fileno())
    print(json.dumps({'output':str(args.output),'sha256':hashlib.sha256(canonical_bytes(result)).hexdigest(),
                      'installed':result.get('installed',False),'execution_verified':False}))


if __name__=='__main__': main()
