"""Preserve actual frozen dependency license files without granting release approval."""
from __future__ import annotations
import email
import hashlib
import importlib.metadata as metadata
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import io
import urllib.parse
import zipfile
import tarfile

LICENSE_NAME = re.compile(r'^(?:licen[cs]es?|copying|notices?|copyright)(?:[._-].*)?$', re.I)
MAX_LICENSE_BYTES = 32 * 1024 * 1024


def _read(path: Path, base: Path, limit=MAX_LICENSE_BYTES) -> bytes:
    if not path.is_relative_to(base) or any(p.is_symlink() for p in (path,*path.parents) if p==base or base in p.parents):
        raise ValueError('License input is linked or outside its package')
    if not path.is_file() or path.stat().st_size > limit:
        raise ValueError('License input is missing or unbounded')
    descriptor=os.open(path,os.O_RDONLY|getattr(os,'O_NOFOLLOW',0)|getattr(os,'O_NONBLOCK',0))
    with os.fdopen(descriptor,'rb') as stream:
        before=os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_size>limit:
            raise ValueError('License input is not a bounded regular file')
        data=stream.read(limit+1);after=os.fstat(stream.fileno())
    if len(data)>limit or (before.st_ino,before.st_size,before.st_mtime_ns)!=(after.st_ino,after.st_size,after.st_mtime_ns):
        raise ValueError('License input changed while collecting')
    if any(p.is_symlink() for p in (path,*path.parents) if p==base or base in p.parents):
        raise ValueError('License input identity changed while collecting')
    current=path.stat(follow_symlinks=False)
    if not stat.S_ISREG(current.st_mode) or (current.st_dev,current.st_ino,current.st_size,current.st_mtime_ns)!=(before.st_dev,before.st_ino,before.st_size,before.st_mtime_ns):
        raise ValueError('License input identity changed while collecting')
    return data


def _normalize(name): return re.sub(r'[-_.]+','-',name.lower())


def _pinned_input(base,record,limit):
    if not isinstance(record,dict):raise ValueError('Missing hash-pinned supplier input')
    name,checksum=record.get('path'),record.get('sha256')
    if (not isinstance(name,str) or name in ('','.','..') or Path(name).name!=name
            or not isinstance(checksum,str) or not re.fullmatch('[0-9a-f]{64}',checksum)):
        raise ValueError('Invalid hash-pinned supplier input')
    raw=_read(base/name,base,limit)
    if hashlib.sha256(raw).hexdigest()!=checksum:raise ValueError('Offline supplier checksum differs')
    return raw


def _public_url(value,host):
    if not isinstance(value,str):raise ValueError('Missing public supplier URL')
    url=urllib.parse.urlparse(value)
    if (url.scheme!='https' or url.hostname!=host or url.username or url.password
            or url.port not in (None,443) or url.query or url.fragment):
        raise ValueError('Supplier provenance must name a public host without credentials')
    return url


def _upstream_license(base,row,name,version):
    """Provisioned original license bytes, exact wheel metadata and immutable release ref.

    This verifies offline provenance consistency, not publisher signatures or legal
    compatibility. Nothing is fetched, extracted, installed or executed here.
    """
    url=_public_url(row.get('source_url'),'raw.githubusercontent.com')
    parts=url.path.lstrip('/').split('/')
    if (len(parts)<4 or not re.fullmatch('[0-9a-f]{40}',parts[2])
            or any(not part or part in ('.','..') or '\\' in part for part in parts)
            or not LICENSE_NAME.match(parts[-1])):
        raise ValueError('Upstream license must name immutable original license bytes')
    repository='/'.join(parts[:2]);commit=parts[2]
    package=row.get('package_archive');_public_url(package.get('source_url') if isinstance(package,dict) else None,'files.pythonhosted.org')
    package_raw=_pinned_input(base,package,64*1024*1024)
    if not package['path'].endswith('.whl'):raise ValueError('Upstream supplier requires exact wheel metadata')
    _,entries=_archive_licenses(package_raw,package['path'])
    if (len(entries)!=1 or _normalize(entries[0].get('Name',''))!=_normalize(name)
            or entries[0].get('Version')!=version):
        raise ValueError('Upstream supplier package version differs')
    # Some distributions declare their repository in their long description.
    if not re.search(r'https?://github\.com/'+re.escape(repository)+r'(?=[/\s)\]>]|$)',str(entries[0]),re.I):
        raise ValueError('Upstream repository differs from original package metadata')
    ref_raw=_pinned_input(base,row.get('release_ref'),1024*1024);ref=json.loads(ref_raw)
    prefix='https://api.github.com/repos/'+repository+'/git/'
    if not isinstance(ref,dict):raise ValueError('Invalid upstream release reference')
    reference=ref.get('ref');obj=ref.get('object')
    if (not isinstance(reference,str) or not reference.startswith('refs/tags/')
            or not isinstance(obj,dict) or not isinstance(ref.get('url'),str)
            or urllib.parse.unquote(ref['url'])!=prefix+reference):
        raise ValueError('Upstream release reference differs from its repository')
    tag=reference.removeprefix('refs/tags/')
    if obj.get('type')=='tag':
        tag_raw=_pinned_input(base,row.get('release_tag'),1024*1024);peeled=json.loads(tag_raw)
        if (not isinstance(peeled,dict) or obj.get('url')!=prefix+'tags/'+str(obj.get('sha')) or peeled.get('sha')!=obj.get('sha')
                or peeled.get('url')!=obj['url'] or peeled.get('tag')!=tag):
            raise ValueError('Annotated upstream tag differs from its release reference')
        obj=peeled.get('object')
    if (not isinstance(obj,dict) or obj.get('type')!='commit' or obj.get('sha')!=commit
            or obj.get('url')!=prefix+'commits/'+commit):
        raise ValueError('Upstream license commit differs from its release reference')
    version_proof=row.get('version_file')
    if version_proof is not None:
        version_url=_public_url(version_proof.get('source_url'),'raw.githubusercontent.com')
        if not version_url.path.startswith('/'+repository+'/'+commit+'/'):
            raise ValueError('Upstream version file belongs to another commit')
        version_raw=_pinned_input(base,version_proof,1024*1024);version_data=json.loads(version_raw)
        if (not isinstance(version_data,dict) or _normalize(version_data.get('name',''))!=_normalize(name) or version_data.get('version')!=version):
            raise ValueError('Upstream source version differs from the exact wheel')
    elif tag not in (version,'v'+version,name+'@'+version,name+'-'+version):
        raise ValueError('Upstream release tag differs from exact component version')
    raw=_pinned_input(base,row,MAX_LICENSE_BYTES)
    if not raw:raise ValueError('Upstream license bytes are empty')
    record={'component':_normalize(name)+'@'+version,'sha256':row['sha256'],'source_url':row['source_url'],
            'basis':'exact_version_wheel_and_immutable_upstream_license_bytes','upstream_commit':commit,
            'package_sha256':package['sha256'],'package_source_url':package['source_url'],
            'release_ref_sha256':row['release_ref']['sha256']}
    if row.get('release_tag'):record['release_tag_sha256']=row['release_tag']['sha256']
    if version_proof:record['version_file_sha256']=version_proof['sha256']
    return [(parts[-1],raw)],record


def _archive_licenses(raw,filename):
    texts=[];metadata_entries=[];names=set();total=0;archive_size=0
    def consume(name,size,is_directory,is_link,read):
        nonlocal total,archive_size
        parts=name.rstrip('/').split('/')
        if (name in names or name.startswith('/') or '\\' in name
                or any(part in ('','.','..') for part in parts) or is_link):
            raise ValueError('Supplier archive has repeated, linked or unsafe members')
        names.add(name);archive_size+=size
        if len(names)>65536 or archive_size>512*1024*1024:raise ValueError('Supplier archive exceeds member limits')
        if is_directory:return
        license_file=bool(LICENSE_NAME.match(parts[-1]))
        is_metadata=(parts[-1]=='PKG-INFO' or parts[-1]=='METADATA' and len(parts)>=2 and parts[-2].endswith('.dist-info'))
        if not (license_file or is_metadata):return
        if not 0<size<=(MAX_LICENSE_BYTES if license_file else 1024*1024):
            raise ValueError('Supplier license or metadata is empty or unbounded')
        total+=size
        if total>128*1024*1024:raise ValueError('Supplier license content exceeds limits')
        data=read()
        if len(data)!=size:raise ValueError('Supplier archive member size differs')
        if license_file:texts.append((parts[-1],data))
        if is_metadata:metadata_entries.append(email.message_from_bytes(data))
    if filename.endswith('.whl'):
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            for member in archive.infolist():
                consume(member.filename,member.file_size,member.is_dir(),stat.S_ISLNK(member.external_attr>>16),lambda:archive.read(member))
    else:
        with tarfile.open(fileobj=io.BytesIO(raw),mode='r|gz') as archive:
            for member in archive:
                def read():
                    with archive.extractfile(member) as stream:return stream.read(member.size+1)
                consume(member.name,member.size,member.isdir(),not(member.isfile() or member.isdir()),read)
    return texts,metadata_entries


def _supplier_licenses(manifest_path):
    """Read provisioned hash-pinned vendor archives; never install, execute or download."""
    if manifest_path is None:return {}
    path=Path(manifest_path).absolute();base=path.parent
    def unique(pairs):
        found={}
        for key,value in pairs:
            if key in found:raise ValueError('Duplicate supplier manifest key')
            found[key]=value
        return found
    manifest=json.loads(_read(path,base,1024*1024),object_pairs_hook=unique)
    if (not isinstance(manifest,dict) or manifest.get('schema_version')!=1
            or not isinstance(manifest.get('files'),list) or len(manifest['files'])>1000):
        raise ValueError('Invalid offline supplier manifest')
    found={}
    for row in manifest['files']:
        if not isinstance(row,dict):raise ValueError('Invalid offline supplier record')
        name,version,relative,checksum=(row.get(key) for key in ('name','version','path','sha256'))
        if (not isinstance(name,str) or not re.fullmatch('[A-Za-z0-9][A-Za-z0-9._-]{0,127}',name)
                or not isinstance(version,str) or not re.fullmatch('[A-Za-z0-9][A-Za-z0-9.!+_-]{0,127}',version)
                or not isinstance(relative,str) or Path(relative).name!=relative
                or (row.get('kind')!='upstream_license' and not relative.endswith(('.whl','.tar.gz')))
                or not isinstance(checksum,str) or not re.fullmatch('[0-9a-f]{64}',checksum)):
            raise ValueError('Invalid offline supplier identity')
        key=_normalize(name)+'@'+version
        if key in found:raise ValueError('Duplicate offline supplier component')
        if row.get('kind')=='upstream_license':
            found[key]=_upstream_license(base,row,name,version);continue
        _public_url(row.get('source_url'),'files.pythonhosted.org')
        raw=_read(base/relative,base,64*1024*1024)
        if hashlib.sha256(raw).hexdigest()!=checksum:raise ValueError('Offline supplier checksum differs')
        texts,metadata_entries=_archive_licenses(raw,relative)
        if (not metadata_entries or len(metadata_entries)>32 or (relative.endswith('.whl') and len(metadata_entries)!=1)
                or any(_normalize(meta.get('Name',''))!=_normalize(name) or meta.get('Version')!=version for meta in metadata_entries)):
            raise ValueError('Supplier package metadata differs from exact component version')
        if not texts:raise ValueError('Supplier archive lacks actual license bytes')
        found[key]=(texts,{'component':key,'sha256':checksum,'source_url':row['source_url'],
                         'basis':'exact_version_offline_vendor_archive_license_bytes'})
    return found


def collect_frozen_licenses(build: Path, pyz_toc: Path, destination: Path, *, supplier_manifest=None) -> dict:
    from scripts.license_inventory import _pyz_top_level_modules
    build,destination=Path(build).absolute(),Path(destination).absolute()
    if destination.exists() or destination.is_symlink(): raise ValueError('License output already exists')
    if any(p.is_symlink() for p in (build,destination.parent,*destination.parents)): raise ValueError('License directory is linked')
    internal=build/'_internal';internal=internal if internal.is_dir() else build
    components={};sources=[];missing=[];suppliers=_supplier_licenses(supplier_manifest)
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
    supplier_archives=[]
    for key in sorted(components):
        missing_key='license_text:'+key
        if missing_key not in missing or key not in suppliers:continue
        texts,record=suppliers[key];supplier_archives.append(record)
        for label,data in texts:
            digest=hashlib.sha256(data).hexdigest()
            relative='texts/'+hashlib.sha256(key.encode()).hexdigest()[:16]+'/'+digest[:16]+'-'+re.sub(r'[^A-Za-z0-9._-]','_',label)
            values.append((relative,data,{'component':key,'path':relative,'sha256':digest,'size':len(data)}))
        missing=[value for value in missing if value!=missing_key]
    staging=Path(tempfile.mkdtemp(prefix='.license-texts-',dir=destination.parent))
    files={}
    for relative,data,row in values:
        target=staging/relative;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(data);files[relative]=row
    receipt={'schema_version':1,'scope':'frozen_python_license_bytes','status':'partial' if missing else 'collected',
             'public_distribution_approved':False,'components':list(components.values()),
             'files':[files[key] for key in sorted(files)],'missing':sorted(set(missing)),
             'supplier_archives':supplier_archives,
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
