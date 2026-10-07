"""Preserve actual frozen dependency license files without granting release approval."""
from __future__ import annotations
import email
import hashlib
import importlib.metadata as metadata
import json
import os
from pathlib import Path
import re
import tempfile

LICENSE_NAME = re.compile(r'^(?:licen[cs]es?|copying|notices?|copyright)(?:[._-].*)?$', re.I)
MAX_LICENSE_BYTES = 32 * 1024 * 1024


def _read(path: Path, base: Path) -> bytes:
    if not path.is_relative_to(base) or any(p.is_symlink() for p in (path,*path.parents) if p==base or base in p.parents):
        raise ValueError('License input is linked or outside its package')
    if not path.is_file() or path.stat().st_size > MAX_LICENSE_BYTES:
        raise ValueError('License input is missing or unbounded')
    with path.open('rb') as stream:
        before=os.fstat(stream.fileno());data=stream.read(MAX_LICENSE_BYTES+1);after=os.fstat(stream.fileno())
    if len(data)>MAX_LICENSE_BYTES or (before.st_ino,before.st_size,before.st_mtime_ns)!=(after.st_ino,after.st_size,after.st_mtime_ns):
        raise ValueError('License input changed while collecting')
    return data


def _normalize(name): return re.sub(r'[-_.]+','-',name.lower())


def collect_frozen_licenses(build: Path, pyz_toc: Path, destination: Path) -> dict:
    from scripts.license_inventory import _pyz_top_level_modules
    build,destination=Path(build).absolute(),Path(destination).absolute()
    if destination.exists() or destination.is_symlink(): raise ValueError('License output already exists')
    if any(p.is_symlink() for p in (build,destination.parent,*destination.parents)): raise ValueError('License directory is linked')
    internal=build/'_internal';internal=internal if internal.is_dir() else build
    components={};sources=[];missing=[]
    for info in sorted(internal.rglob('*.dist-info')):
        if info.is_symlink(): raise ValueError('License metadata directory is linked')
        path=info/'METADATA'
        if not path.is_file():continue
        meta=email.message_from_bytes(_read(path,build))
        name,version=_normalize(meta.get('Name') or info.name),meta.get('Version')
        key=name+'@'+str(version);components[key]={'name':name,'version':version,'basis':'frozen_metadata'}
        entries=list(info.rglob('*'))
        if any(p.is_symlink() for p in entries):raise ValueError('License metadata input is linked')
        paths=[p for p in sorted(entries) if LICENSE_NAME.match(p.name) and not p.is_dir()]
        if not paths:
            # Only the exact distribution version used by this compiler can
            # supply a file omitted from the onedir metadata collection.
            try:dist=metadata.distribution(name)
            except metadata.PackageNotFoundError:dist=None
            if dist is not None and dist.version==version:
                paths=[Path(dist.locate_file(p)) for p in (dist.files or ()) if LICENSE_NAME.match(Path(p).name)]
                for p in paths:sources.append((key,p,Path(dist.locate_file('')).absolute(),p.name))
            if not paths:missing.append('license_text:'+key)
        else:
            for p in paths:sources.append((key,p,build,p.relative_to(info).as_posix()))
    mapping=metadata.packages_distributions()
    for module in sorted(_pyz_top_level_modules(Path(pyz_toc))):
        providers=mapping.get(module,[])
        if not providers:missing.append('unmapped_module:'+module);continue
        for name in providers:
            try:dist=metadata.distribution(name)
            except metadata.PackageNotFoundError:
                missing.append('metadata:'+_normalize(name));continue
            key=_normalize(name)+'@'+dist.version
            if key in components:continue
            components[key]={'name':_normalize(name),'version':dist.version,'basis':'inventory_interpreter_PYZ_mapping'}
            paths=[p for p in (dist.files or ()) if LICENSE_NAME.match(Path(p).name)]
            if not paths:missing.append('license_text:'+key)
            for p in paths:sources.append((key,Path(dist.locate_file(p)),Path(dist.locate_file('')).absolute(),Path(p).name))
    destination.parent.mkdir(parents=True,exist_ok=True)
    # Validate every input before creating visible output. Identifier strings
    # and metadata classifiers are never substituted for missing license bytes.
    values=[]
    for key,path,base,label in sources:
        data=_read(path.absolute(),base)
        digest=hashlib.sha256(data).hexdigest()
        relative='texts/'+hashlib.sha256(key.encode()).hexdigest()[:16]+'/'+digest[:16]+'-'+re.sub(r'[^A-Za-z0-9._-]','_',Path(label).name)
        values.append((relative,data,{'component':key,'path':relative,'sha256':digest,'size':len(data)}))
    staging=Path(tempfile.mkdtemp(prefix='.license-texts-',dir=destination.parent))
    files={}
    for relative,data,row in values:
        target=staging/relative;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(data);files[relative]=row
    receipt={'schema_version':1,'scope':'frozen_python_license_bytes','status':'partial' if missing else 'collected',
             'public_distribution_approved':False,'components':list(components.values()),
             'files':[files[key] for key in sorted(files)],'missing':sorted(set(missing)),
             'native_library_terms':'separate_inventory_review_required'}
    (staging/'manifest.json').write_text(json.dumps(receipt,indent=2)+'\n',encoding='utf-8')
    verify_license_bundle(staging)
    if destination.exists():raise ValueError('License output appeared while collecting')
    staging.rename(destination)
    return receipt


def verify_license_bundle(folder: Path) -> dict:
    folder=Path(folder).absolute()
    receipt=json.loads(_read(folder/'manifest.json',folder))
    if receipt.get('schema_version')!=1 or not isinstance(receipt.get('files'),list):raise ValueError('Invalid license manifest')
    listed={'manifest.json'}
    for row in receipt['files']:
        relative=Path(row['path'])
        if relative.is_absolute() or '..' in relative.parts or relative.as_posix() in listed:raise ValueError('Invalid license path')
        data=_read(folder/relative,folder)
        if len(data)!=row['size'] or hashlib.sha256(data).hexdigest()!=row['sha256']:raise ValueError('License checksum mismatch')
        listed.add(relative.as_posix())
    found={p.relative_to(folder).as_posix() for p in folder.rglob('*') if p.is_file() or p.is_symlink()}
    if found!=listed:raise ValueError('License bundle contains unlisted files')
    return receipt
