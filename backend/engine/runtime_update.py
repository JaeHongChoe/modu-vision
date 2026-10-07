"""Explicit offline POSIX portable application / owned database cutover.

This command does not discover an installed home, change OS registration,
download code, grant quality approval or provision publisher trust. The caller
must supply an independently pinned authority and an owned, drained installation.
Ordinary stores refuse attachment while a durable update intent is unfinished.
"""
from contextlib import contextmanager
from dataclasses import asdict, dataclass
import base64
import hashlib
import json
import os
import platform
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import uuid
from urllib.parse import urlsplit
import zipfile

from backend.engine import global_migration as migration
from backend.engine.global_store_paths import active_generation, owned_root, store_admission
from backend.engine.runtime_process_control import atomic_private_json

ACTIVE='application-active.json'
PENDING='application-update-pending.json'
UPDATES='.application-updates'
GENERATIONS='.application-generations'
CONTROL_PATHS={ACTIVE,PENDING,UPDATES,GENERATIONS}
MATRIX={'api_context','worker','runtime','dataset_index'}


class UpdateError(ValueError):pass


def _canonical(value):
    return json.dumps(value,sort_keys=True,separators=(',', ':'),ensure_ascii=False,allow_nan=False).encode('utf-8')


def _sha(value):return hashlib.sha256(value).hexdigest()


def _hex(value,length=64):
    return isinstance(value,str) and len(value)==length and all(c in '0123456789abcdef' for c in value)


def _json(raw):
    def pairs(items):
        result={}
        for key,value in items:
            if key in result:raise UpdateError('Duplicate update document field')
            result[key]=value
        return result
    try:return json.loads(raw.decode('utf-8','strict'),object_pairs_hook=pairs,
        parse_constant=lambda _:(_ for _ in ()).throw(UpdateError('Nonfinite update value')))
    except (ValueError,UnicodeError) as exc:raise UpdateError('Invalid update document: '+str(exc)) from exc


def _fields(value,names):
    if not isinstance(value,dict) or set(value)!=set(names):raise UpdateError('Invalid update document fields')


def _unlinked(path):
    path=Path(path).absolute()
    if any(p.is_symlink() for p in (path,*path.parents)):raise UpdateError('Update storage cannot follow links')
    return path


@contextmanager
def _file(path,limit):
    path=_unlinked(path)
    fd=os.open(path,os.O_RDONLY|getattr(os,'O_NOFOLLOW',0)|getattr(os,'O_NONBLOCK',0))
    try:
        before=os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink!=1 or not 0<before.st_size<=limit:
            raise UpdateError('Update input is not a bounded unlinked regular file')
        with os.fdopen(fd,'rb',closefd=False) as reader:yield reader,before
        _unlinked(path);after=os.fstat(fd);current=path.stat()
        identity=lambda s:(s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns,s.st_ctime_ns,s.st_nlink)
        if identity(before)!=identity(after) or identity(before)!=identity(current):
            raise UpdateError('Update input identity changed while reading')
    finally:os.close(fd)


def _read(path,limit=65536):
    with _file(path,limit) as (reader,before):
        raw=reader.read(limit+1)
        if len(raw)!=before.st_size:raise UpdateError('Update document changed while reading')
        return raw


def _check_file(path,row,*,copy_to=None):
    digest=hashlib.sha256();total=0
    with _file(path,1024**3) as (reader,before):
        if before.st_size!=row['size']:raise UpdateError('Update artifact size differs')
        writer=open(copy_to,'xb') if copy_to is not None else None
        try:
            while chunk:=reader.read(1024**2):
                total+=len(chunk)
                if total>row['size']:raise UpdateError('Update artifact grew while reading')
                digest.update(chunk)
                if writer:writer.write(chunk)
            if digest.hexdigest()!=row['sha256'] or total!=row['size']:raise UpdateError('Update artifact checksum differs')
            if writer:writer.flush();os.fsync(writer.fileno())
        finally:
            if writer:writer.close()


def _base64(value):
    if not isinstance(value,str) or not 0<len(value)<=90000:raise UpdateError('Invalid update signature encoding')
    try:raw=base64.b64decode(value,validate=True)
    except ValueError as exc:raise UpdateError('Invalid update signature encoding') from exc
    if base64.b64encode(raw).decode()!=value:raise UpdateError('Noncanonical update signature encoding')
    return raw


def _version(value):
    match=re.fullmatch(r'(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?',value) if isinstance(value,str) and len(value)<=128 else None
    if not match:raise UpdateError('Invalid application version')
    pre=(match[4] or '').split('.') if match[4] else []
    if any(re.fullmatch(r'0[0-9]+',part) for part in pre):raise UpdateError('Invalid prerelease version')
    return tuple(int(match[i]) for i in (1,2,3)),pre


def _compare(a,b):
    major,x=_version(a);prior,y=_version(b)
    if major!=prior:return 1 if major>prior else -1
    if not x or not y:return 0 if x==y else -1 if x else 1
    for left,right in zip(x,y):
        if left==right:continue
        ln,rn=left.isdigit(),right.isdigit()
        if ln and rn:return 1 if int(left)>int(right) else -1
        if ln!=rn:return -1 if ln else 1
        return 1 if left>right else -1
    return (len(x)>len(y))-(len(x)<len(y))


def _origin(value):
    if not isinstance(value,str):raise UpdateError('Invalid release origin')
    try:url=urlsplit(value);port=url.port
    except ValueError as exc:raise UpdateError('Invalid release origin') from exc
    if url.scheme!='https' or not url.hostname or url.username or url.password or url.fragment:
        raise UpdateError('Release origin requires credential-free HTTPS')
    host=url.hostname.lower()
    if ':' in host:host='['+host+']'
    return 'https://'+host+(':'+str(port) if port is not None and port!=443 else '')


def _matrix(value):
    _fields(value,MATRIX)
    if any(type(v)is not int or not 0<v<=2**53-1 for v in value.values()):raise UpdateError('Invalid compatibility matrix')


def verify_release(envelope_path,authority_path,pinned_authority_sha256,target):
    """Verify the existing Electron Ed25519 envelope, with the same field set."""
    _fields(target,{'platform','arch','channel','current_version','origin'})
    authority_raw=_read(authority_path,32768)
    if not _hex(pinned_authority_sha256) or _sha(authority_raw)!=pinned_authority_sha256:
        raise UpdateError('Pinned publisher authority changed')
    authority=_json(authority_raw)
    _fields(authority,{'schema_version','publisher','keys','revoked_key_ids','allowed_origins','compatibility'})
    if (type(authority['schema_version'])is not int or authority['schema_version']!=1
            or not isinstance(authority['publisher'],str) or not 0<len(authority['publisher'])<=256
            or not isinstance(authority['keys'],dict) or not 0<len(authority['keys'])<=32
            or not isinstance(authority['revoked_key_ids'],list) or not all(isinstance(k,str) for k in authority['revoked_key_ids'])
            or not isinstance(authority['allowed_origins'],list) or not 0<len(authority['allowed_origins'])<=32
            or any(_origin(v)!=v for v in authority['allowed_origins'])):
        raise UpdateError('Invalid pinned publisher authority')
    _matrix(authority['compatibility'])
    envelope_raw=_read(envelope_path);envelope=_json(envelope_raw)
    _fields(envelope,{'schema_version','key_id','payload_b64','signature_b64'})
    key_id=envelope['key_id']
    if (type(envelope['schema_version'])is not int or envelope['schema_version']!=1
            or not isinstance(key_id,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}',key_id)
            or key_id not in authority['keys'] or key_id in authority['revoked_key_ids']):
        raise UpdateError('Release signing key is not pinned or is revoked')
    raw=_base64(envelope['payload_b64']);signature=_base64(envelope['signature_b64'])
    if len(raw)>65536 or len(signature)!=64:raise UpdateError('Invalid signed release bounds')
    release=_json(raw)
    if _canonical(release)!=raw:raise UpdateError('Signed release must use canonical JSON')
    from cryptography.hazmat.primitives.serialization import load_der_public_key
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    from cryptography.exceptions import InvalidSignature
    try:
        key=load_der_public_key(_base64(authority['keys'][key_id]))
        if not isinstance(key,Ed25519PublicKey):raise UpdateError('Publisher key must be Ed25519')
        key.verify(signature,raw)
    except (ValueError,InvalidSignature) as exc:raise UpdateError('Release manifest signature did not verify') from exc
    _fields(release,{'version','channel','platform','arch','url','sha256','size','publisher','compatibility','artifacts'})
    if release['publisher']!=authority['publisher']:raise UpdateError('Release publisher differs from pinned authority')
    if any(release[k]!=target[k] for k in ('channel','platform','arch')):raise UpdateError('Release target differs')
    if _compare(release['version'],target['current_version'])<0:raise UpdateError('Application downgrade requires a separate approved rollback record')
    if release['channel'] not in ('stable','beta') or release['channel']=='stable' and _version(release['version'])[1]:raise UpdateError('Invalid release channel/version')
    if _origin(release['url'])!=target['origin'] or target['origin'] not in authority['allowed_origins']:raise UpdateError('Release origin is not pinned')
    _matrix(release['compatibility'])
    if release['compatibility']!=authority['compatibility']:raise UpdateError('Release protocol/schema compatibility differs')
    artifacts=release['artifacts']
    if not isinstance(artifacts,list) or not 0<len(artifacts)<=32:raise UpdateError('Invalid release artifact inventory')
    names=set()
    for row in artifacts:
        _fields(row,{'path','kind','sha256','size'});name=row['path']
        if (not isinstance(name,str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,159}',name)
                or name.endswith('.') or re.match(r'^(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)',name,re.I)
                or name.lower() in names or row['kind'] not in ('installer','runtime_pack')
                or not _hex(row['sha256']) or type(row['size'])is not int or not 0<row['size']<=1024**3):
            raise UpdateError('Invalid release artifact inventory')
        names.add(name.lower())
    installers=[row for row in artifacts if row['kind']=='installer']
    if (len(installers)!=1 or installers[0]['path']!=PurePosixPath(urlsplit(release['url']).path).name
            or installers[0]['sha256']!=release['sha256'] or installers[0]['size']!=release['size']):
        raise UpdateError('Installer differs from signed artifact inventory')
    return release,envelope_raw


def _safe_path(value):
    if not isinstance(value,str) or not 0<len(value)<=240:raise UpdateError('Invalid portable application path')
    parts=value.split('/')
    if any(not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_. -]{0,159}',p) or p.endswith(('.', ' '))
            or re.match(r'^(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)',p,re.I) for p in parts):
        raise UpdateError('Unsafe portable application path')
    return value


def _application_namespaces(names):
    namespaces={}
    for name in names:
        parts=name.split('/')
        for count in range(1,len(parts)+1):
            prefix='/'.join(parts[:count]);fold=prefix.casefold();kind='file' if count==len(parts) else 'directory'
            prior=namespaces.get(fold)
            if prior and prior!=(prefix,kind):raise UpdateError('Portable file/directory/link namespace conflicts')
            namespaces[fold]=(prefix,kind)
    return namespaces


def _native_layout(manifest):
    """Resolve signed aliases lexically without touching the host filesystem.

    Schema1 retains its no-link contract. Schema2 is one darwin .app whose
    ordinary inventory uses only canonical paths; aliases have no descendants
    in that inventory and can never escape into data, trust or host locations.
    """
    if manifest['platform']!='darwin':raise UpdateError('Native linked layout requires darwin')
    files={row['path'] for row in manifest['files']};links=manifest['links']
    if not isinstance(links,list) or len(links)>512:raise UpdateError('Invalid native link inventory')
    if not files:raise UpdateError('Native application inventory is empty')
    app=next(iter(files)).split('/')[0]
    if not app.endswith('.app'):raise UpdateError('Native application must use one app root')
    root=app+'/Contents/'
    if any(not name.startswith(root) for name in files):raise UpdateError('Native files escaped the app Contents root')
    expected={}
    for row in links:
        _fields(row,{'path','target'});name=_safe_path(row['path']);target=row['target']
        if (not name.startswith(root) or name in expected or name in files or not isinstance(target,str)
                or not 0<len(target)<=240 or target.startswith('/') or '\\' in target
                or any(part=='' for part in target.split('/'))):raise UpdateError('Invalid native internal link')
        expected[name]=target
    namespaces=_application_namespaces([*files,*expected,'portable-application.json'])
    directories={prefix for prefix,kind in namespaces.values() if kind=='directory'}
    _safe_path(manifest['entrypoint'])
    if (not manifest['entrypoint'].startswith(root+'MacOS/') or manifest['entrypoint']not in files):
        raise UpdateError('Native entrypoint must be a canonical regular Contents/MacOS file')

    for name,target in expected.items():
        parts=name.rsplit('/',1)[0].split('/');pending=target.split('/');seen=set();expansions=0
        while pending:
            part=pending.pop(0)
            if part=='.':continue
            if part=='..':
                if len(parts)<=2:raise UpdateError('Native link escaped the app Contents root')
                parts.pop();continue
            parts.append(part);candidate=_safe_path('/'.join(parts))
            if candidate in expected:
                state=(candidate,tuple(pending))
                if state in seen or expansions>=64:raise UpdateError('Cyclic or excessive native internal links')
                seen.add(state);expansions+=1
                pending=expected[candidate].split('/')+pending;parts=parts[:-1];continue
            if candidate not in files and candidate not in directories:raise UpdateError('Dangling native internal link')
            if pending and candidate not in directories:raise UpdateError('Native link traverses a regular file')
        candidate='/'.join(parts)
        if candidate not in files and candidate not in directories:raise UpdateError('Dangling native internal link')
        if candidate in directories and (name.startswith(candidate+'/') or name==candidate):
            raise UpdateError('Native directory link forms an ancestor cycle')
    return expected,directories


def _portable(archive,release,*,destination=None):
    with _file(archive,1024**3) as (reader,_),zipfile.ZipFile(reader) as bundle:
        members=bundle.infolist();names=[m.filename for m in members]
        if (not 1<len(members)<=10001 or len(names)!=len(set(n.casefold() for n in names))
                or sum(m.file_size for m in members)>4*1024**3):raise UpdateError('Invalid portable archive bounds or duplicates')
        for member in members:
            _safe_path(member.filename);mode=member.external_attr>>16
            if (member.is_dir() or member.flag_bits&1 or member.file_size>1024**3
                    or stat.S_IFMT(mode) not in (0,stat.S_IFREG,stat.S_IFLNK)):
                raise UpdateError('Portable archive cannot contain special or encrypted files')
        if ('portable-application.json' not in names or bundle.getinfo('portable-application.json').file_size>1024**2
                or stat.S_IFMT(bundle.getinfo('portable-application.json').external_attr>>16)not in (0,stat.S_IFREG)):
            raise UpdateError('Portable application regular manifest missing or excessive')
        raw=bundle.read('portable-application.json');manifest=_json(raw)
        native=isinstance(manifest,dict) and manifest.get('schema_version')==2
        _fields(manifest,{'schema_version','version','platform','arch','entrypoint','files'}|({'links'}if native else set()))
        if type(manifest['schema_version'])is not int or manifest['schema_version']not in (1,2) or any(manifest[k]!=release[k] for k in ('version','platform','arch')):
            raise UpdateError('Portable application target/version differs from signed release')
        rows=manifest['files']
        if not isinstance(rows,list) or not 0<len(rows)<=10000:raise UpdateError('Invalid portable application inventory')
        expected={};folded=set()
        for row in rows:
            _fields(row,{'path','sha256','size','executable'});name=_safe_path(row['path'])
            if (name=='portable-application.json' or name.casefold() in folded or not _hex(row['sha256'])
                    or type(row['size'])is not int or not 0<row['size']<=1024**3 or type(row['executable'])is not bool):
                raise UpdateError('Invalid portable application inventory')
            folded.add(name.casefold());expected[name]=row
        links,_=_native_layout(manifest) if native else ({},set())
        if not native and any(stat.S_IFMT(member.external_attr>>16)not in (0,stat.S_IFREG)for member in members):
            raise UpdateError('Portable schema1 archive cannot contain links')
        if set(names)!=set(expected)|set(links)|{'portable-application.json'}:raise UpdateError('Portable application membership differs')
        _application_namespaces([*expected,*links,'portable-application.json'])
        _safe_path(manifest['entrypoint'])
        if manifest['entrypoint'] not in expected or not expected[manifest['entrypoint']]['executable']:
            raise UpdateError('Portable application entrypoint is not an executable inventory member')
        # Validate each signed alias before creating a destination or any link.
        for name,target in links.items():
            member=bundle.getinfo(name);encoded=target.encode('utf-8')
            if stat.S_IFMT(member.external_attr>>16)!=stat.S_IFLNK or member.file_size!=len(encoded)or bundle.read(name)!=encoded:
                raise UpdateError('Native link archive type or target differs from signed inventory')
        if any(stat.S_IFMT(bundle.getinfo(name).external_attr>>16)not in (0,stat.S_IFREG)for name in expected):
            raise UpdateError('Canonical application inventory must contain only regular files')
        if destination is not None:
            destination=_unlinked(destination);destination.mkdir(mode=0o700,parents=True,exist_ok=False)
            _write_raw(destination/'portable-application.json',raw)
        for name,row in expected.items():
            if bundle.getinfo(name).file_size!=row['size']:raise UpdateError('Portable application size differs')
            writer=None;digest=hashlib.sha256();total=0
            if destination is not None:
                target=_unlinked(destination/name);target.parent.mkdir(mode=0o700,parents=True,exist_ok=True);writer=open(target,'xb')
            try:
                with bundle.open(name) as source:
                    while chunk:=source.read(1024**2):
                        total+=len(chunk)
                        if total>row['size']:raise UpdateError('Portable application grew while unpacking')
                        digest.update(chunk)
                        if writer:writer.write(chunk)
                if total!=row['size'] or digest.hexdigest()!=row['sha256']:raise UpdateError('Portable application checksum differs')
                if writer:writer.flush();os.fchmod(writer.fileno(),0o500 if row['executable'] else 0o400);os.fsync(writer.fileno())
            finally:
                if writer:writer.close()
        # All regular bytes are now verified. Namespace admission forbids every
        # symbolic parent, so a link is never traversed while materializing files.
        if destination is not None:
            for name,target in links.items():
                file=_unlinked(destination/name);file.parent.mkdir(mode=0o700,parents=True,exist_ok=True);os.symlink(target,file)
        return manifest,_sha(raw)


def _bundle(directory,release,*,destination=None):
    directory=_unlinked(directory)
    names={row['path'] for row in release['artifacts']}
    if not directory.is_dir() or set(p.name for p in directory.iterdir())!=names:raise UpdateError('Offline artifact inventory has missing or additional files')
    if destination is not None:destination.mkdir(mode=0o700,parents=True,exist_ok=False)
    for row in release['artifacts']:
        _check_file(directory/row['path'],row,copy_to=destination/row['path'] if destination is not None else None)
    if set(p.name for p in directory.iterdir())!=names:raise UpdateError('Offline artifact inventory changed')


def _root(path):
    if os.name=='nt':raise UpdateError('Portable cutover currently requires qualified POSIX durability; Windows is unsupported')
    path=_unlinked(path);root,owner=owned_root(path)
    if root!=path or owner is None:raise UpdateError('Explicit owned application installation required')
    for name in CONTROL_PATHS:_unlinked(root/name)
    return root,owner


def _write(path,value):
    atomic_private_json(_unlinked(path),value);migration._sync_directories(path.parent,recursive=False)


def _write_raw(path,raw):
    with open(_unlinked(path),'xb') as writer:
        writer.write(raw);writer.flush();os.fchmod(writer.fileno(),0o400);os.fsync(writer.fileno())


def _checkpoint(point):
    """Durable fault boundaries, also exercised by subprocess power-loss tests."""


def _pointer(root):
    path=root/ACTIVE
    return _json(_read(path)) if path.exists() else None


def validate_attachment(root):
    """Lightweight store fence; complete application hashing happens at launch."""
    root,owner=_root(root)
    if (root/PENDING).exists():raise UpdateError('Application update requires recovery before store attachment')
    pointer=_pointer(root)
    if pointer is None:return
    _fields(pointer,{'schema_version','installation_id','update_id','application_generation','database_pointer'})
    current=active_generation(root)
    if (type(pointer['schema_version'])is not int or pointer['schema_version']!=1
            or pointer['installation_id']!=owner['installation_id'] or not _hex(pointer['update_id'],32)
            or pointer['application_generation']!=pointer['update_id'] or current is None
            or pointer['database_pointer']!=current[1]):raise UpdateError('Application/database generation pair changed; recovery required')


@dataclass(frozen=True)
class UpdatePlan:
    root:str
    bundle:str
    envelope:str
    authority:str
    authority_sha256:str
    envelope_sha256:str
    app:dict
    runtime:int
    protocol:dict
    schema:int
    packs:tuple
    source_sha256:str
    previous_database:dict|None
    previous_application:dict|None
    target:dict


def plan_update(root,bundle,envelope,authority,*,pinned_authority_sha256,target):
    root,owner=_root(root)
    with store_admission(root):
        release,raw=verify_release(envelope,authority,pinned_authority_sha256,target)
        native={'Darwin':'darwin','Linux':'linux'}.get(platform.system())
        arch={'arm64':'arm64','aarch64':'arm64','x86_64':'x64','AMD64':'x64'}.get(platform.machine())
        if release['platform']!=native or release['arch']!=arch:raise UpdateError('Portable release does not match this actual host target')
        previous=_pointer(root);current=active_generation(root)
        if previous:
            old,_=_intent(root,previous['update_id'])
            if target['current_version']!=old['release']['version']:raise UpdateError('Current application version differs from owned installation')
        _bundle(bundle,release)
        installer=next(row for row in release['artifacts'] if row['kind']=='installer')
        _portable(Path(bundle)/installer['path'],release)
        source=migration.preview_forward(root) if current else migration.preview(root)
        if not source['can_apply']:raise UpdateError('; '.join(source['blockers']))
        return UpdatePlan(str(root),str(_unlinked(bundle)),str(_unlinked(envelope)),str(_unlinked(authority)),
            pinned_authority_sha256,_sha(raw),release,release['compatibility']['runtime'],release['compatibility'],
            release['compatibility']['dataset_index'],tuple(row for row in release['artifacts'] if row['kind']=='runtime_pack'),
            source['source_sha256'],current[1] if current else None,previous,dict(target))


def review_update(plan):
    """Bounded review; no application launch, migration or credential disclosure."""
    installer=next(row for row in plan.app['artifacts'] if row['kind']=='installer')
    manifest,_=_portable(Path(plan.bundle)/installer['path'],plan.app)
    _,owner=_root(plan.root)
    return {'status':'reviewed','installation_id':owner['installation_id'],
        'plan_sha256':_sha(_canonical(asdict(plan))), 'source_sha256':plan.source_sha256,
        'envelope_sha256':plan.envelope_sha256,'authority_sha256':plan.authority_sha256,
        'current_version':plan.target['current_version'],'version':plan.app['version'],
        'publisher':plan.app['publisher'],'channel':plan.app['channel'],
        'application_file_count':len(manifest['files']),'application_layout':'darwin-app/v2' if manifest['schema_version']==2 else 'portable/v1',
        'application_link_count':len(manifest.get('links',[])),'pack_count':len(plan.packs),
        'artifact_bytes':sum(row['size'] for row in plan.app['artifacts']),
        'database_fence':plan.previous_database['fence'] if plan.previous_database else 0,
        'copied_session_policy':'revoked','application_started':False,
        'native_signature_acceptance':'unqualified','model_quality_acceptance':'required'}


def inspect_update(root,authority,*,pinned_authority_sha256):
    """Reopen only this installation's pinned current or unfinished intent.

    Exclusive admission permits inspection while recovery blocks ordinary stores;
    it does not repair, attach stores, start applications or grant execution rights.
    """
    root,owner=_root(root)
    raw=_read(authority,32768)
    if not _hex(pinned_authority_sha256) or _sha(raw)!=pinned_authority_sha256:
        raise UpdateError('Pinned publisher authority changed')
    with store_admission(root,exclusive=True):
        pending=_json(_read(root/PENDING)) if (root/PENDING).exists() else None
        pointer=_pointer(root)
        if pending:
            _fields(pending,{'schema_version','installation_id','update_id'})
            if type(pending['schema_version']) is not int or pending['schema_version']!=1 or pending['installation_id']!=owner['installation_id']:
                raise UpdateError('Foreign recovery pointer')
        identifier=pending['update_id'] if pending else pointer['update_id'] if pointer else None
        result={'status':'ready','installation_id':owner['installation_id'], 'version':'0.0.0',
            'update_id':None,'database_fence':0,'allowed_recovery':[],
            'application_started':False,'native_signature_acceptance':'unqualified',
            'model_quality_acceptance':'required'}
        if identifier is None:return result
        record,_=_intent(root,identifier)
        # Check the externally pinned bytes before following the intent's stored
        # authority path. A copied/edited intent cannot choose a new publisher.
        if record.get('authority_sha256')!=pinned_authority_sha256:
            raise UpdateError('Update intent differs from the pinned authority')
        record,_,_=_validated_intent(root,identifier)
        if not pending:validate_attachment(root)
        current=active_generation(root)
        result.update(status='recovery_required' if pending else 'committed',
            version=record['release']['version'],update_id=identifier,
            database_fence=current[1]['fence'] if current else 0,
            allowed_recovery=['finish','abort'] if pending and record['status']=='staged' and record['migration_id'] is None
                else ['finish'] if pending else ['finish','forward'])
        return result


def _intent(root,identifier):
    if not _hex(identifier,32):raise UpdateError('Invalid application update identity')
    directory=_unlinked(root/UPDATES/identifier);record=_json(_read(directory/'journal.json',1024**2))
    _,owner=_root(root)
    if (record.get('schema_version')!=1 or record.get('installation_id')!=owner['installation_id']
            or record.get('update_id')!=identifier or record.get('application_generation')!=identifier
            or record.get('status') not in ('staged','database_prepared','committed','aborted')):
        raise UpdateError('Foreign or invalid application update intent')
    return record,directory


def _validated_intent(root,identifier):
    record,directory=_intent(root,identifier)
    release,raw=verify_release(directory/'envelope.json',record['authority_path'],record['authority_sha256'],record['target'])
    if _sha(raw)!=record['envelope_sha256'] or release!=record['release']:raise UpdateError('Application update release binding changed')
    _bundle(directory/'bundle',release)
    installer=next(row for row in release['artifacts'] if row['kind']=='installer')
    manifest,manifest_sha=_portable(directory/'bundle'/installer['path'],release)
    generation=_unlinked(root/GENERATIONS/identifier);application=generation/'application'
    if _sha(_read(application/'portable-application.json',1024**2))!=manifest_sha:raise UpdateError('Installed application manifest integrity differs')
    links,directories=_native_layout(manifest) if manifest['schema_version']==2 else ({},set())
    expected={row['path'] for row in manifest['files']}|set(links)|{'portable-application.json'}
    actual=set()
    for path in application.rglob('*'):
        name=path.relative_to(application).as_posix()
        if path.is_symlink():
            _unlinked(path.parent)
            if name not in links or os.readlink(path)!=links[name]:raise UpdateError('Installed native link target changed')
            actual.add(name)
        else:
            _unlinked(path)
            if name in links:raise UpdateError('Installed native link replaced with a regular member')
            if path.is_file():actual.add(name)
            elif not path.is_dir():raise UpdateError('Installed application contains a special file')
            elif manifest['schema_version']==2 and name not in directories:raise UpdateError('Installed native directory membership differs')
    if actual!=expected:raise UpdateError('Installed application membership differs')
    for row in manifest['files']:
        _check_file(application/row['path'],row)
        if stat.S_IMODE((application/row['path']).stat().st_mode)!=(0o500 if row['executable'] else 0o400):
            raise UpdateError('Installed application executable mode changed')
    if record['migration_id'] is not None:
        _,database=migration._journal(root,record['migration_id'])
        if (database.get('installation_id')!=record['installation_id']
                or database.get('source_sha256')!=record['source_sha256']
                or database.get('previous_pointer')!=record['previous_database']):
            raise UpdateError('Application update database intent binding changed')
    return record,directory,manifest


def _pending(root,record):
    path=root/PENDING
    if path.exists():
        current=_json(_read(path))
        if current!={'schema_version':1,'installation_id':record['installation_id'],'update_id':record['update_id']}:
            raise UpdateError('Another application update owns recovery admission')
    else:_write(path,{'schema_version':1,'installation_id':record['installation_id'],'update_id':record['update_id']})


def _finish(root,identifier):
    record,directory,_=_validated_intent(root,identifier)
    if record['status']=='aborted':raise UpdateError('Aborted update cannot be activated')
    current=active_generation(root)
    if record['status']=='committed':
        expected={'schema_version':1,'installation_id':record['installation_id'],'update_id':identifier,
            'application_generation':identifier,'database_pointer':record['database_pointer']}
        if _pointer(root)!=expected or current is None or current[1]!=record['database_pointer']:
            raise UpdateError('Committed application/database pair changed; forward recovery required')
        _pending(root,record)
    else:
        if record['migration_id'] is None and (current[1] if current else None)!=record['previous_database']:
            raise UpdateError('Database generation changed before update')
        if record['migration_id'] is not None and current and current[1]['generation_id']!=record['migration_id'] and current[1]!=record['previous_database']:
            raise UpdateError('Another database generation owns the installation')
        _pending(root,record)
        if record['migration_id'] is None:
            if (current[1] if current else None)!=record['previous_database']:raise UpdateError('Database generation changed before update')
            _checkpoint('before_database')
            def prepared(journal):
                record.update(status='database_prepared',migration_id=journal['migration_id'])
                _write(directory/'journal.json',record);_checkpoint('database_prepared')
            migrate=migration.advance if current else migration.apply
            migrate(root,expected_source_sha256=record['source_sha256'],on_prepared=prepared)
        else:migration.recover(root,record['migration_id'],action='finish')
        current=active_generation(root)
        if current is None or current[1]['generation_id']!=record['migration_id']:raise UpdateError('Prepared database generation differs')
        record['database_pointer']=current[1]
        _write(directory/'journal.json',record);_checkpoint('after_database')
        _write(root/ACTIVE,{'schema_version':1,'installation_id':record['installation_id'],'update_id':identifier,
            'application_generation':identifier,'database_pointer':current[1]})
        _checkpoint('after_application')
        record['status']='committed';_write(directory/'journal.json',record);_checkpoint('after_receipt')
    (root/PENDING).unlink();migration._sync_directories(root,recursive=False)
    return {'status':'committed','update_id':identifier,'version':record['release']['version'],
        'database_pointer':record['database_pointer'],'publisher':record['release']['publisher'],
        'native_signature_acceptance':'unqualified','known_image_acceptance':'required','model_quality_acceptance':'required'}


def install_update(root,plan):
    root,owner=_root(root)
    if not isinstance(plan,UpdatePlan) or plan.root!=str(root):raise UpdateError('Update plan belongs to another installation')
    with store_admission(root,exclusive=True):
        validate_attachment(root)
        fresh=plan_update(root,plan.bundle,plan.envelope,plan.authority,
            pinned_authority_sha256=plan.authority_sha256,target=plan.target)
        if fresh!=plan:raise UpdateError('Update source or release changed since preview')
        total=sum(row['size'] for row in plan.app['artifacts'])
        if shutil.disk_usage(root).free<total*3+64*1024**2:raise UpdateError('Insufficient update staging reserve')
        identifier=uuid.uuid4().hex;directory=root/UPDATES/identifier;directory.mkdir(mode=0o700,parents=True,exist_ok=False)
        _bundle(plan.bundle,plan.app,destination=directory/'bundle')
        _write_raw(directory/'envelope.json',_read(plan.envelope))
        if _sha((directory/'envelope.json').read_bytes())!=plan.envelope_sha256:raise UpdateError('Update envelope changed during staging')
        generation=root/GENERATIONS/identifier;generation.mkdir(mode=0o700,parents=True,exist_ok=False)
        installer=next(row for row in plan.app['artifacts'] if row['kind']=='installer')
        _portable(directory/'bundle'/installer['path'],plan.app,destination=generation/'application')
        migration._sync_directories(directory);migration._sync_directories(generation)
        migration._sync_directories(root/UPDATES,recursive=False);migration._sync_directories(root/GENERATIONS,recursive=False)
        record={'schema_version':1,'installation_id':owner['installation_id'],'update_id':identifier,
            'application_generation':identifier,'status':'staged','authority_path':plan.authority,
            'authority_sha256':plan.authority_sha256,'envelope_sha256':plan.envelope_sha256,'target':plan.target,
            'release':plan.app,'source_sha256':plan.source_sha256,'previous_database':plan.previous_database,
            'previous_application':plan.previous_application,'migration_id':None,'database_pointer':None,'recovery_history':[]}
        _write(directory/'journal.json',record)
        return _finish(root,identifier)


def recover_update(root,intent,*,action='finish'):
    root,owner=_root(root)
    with store_admission(root,exclusive=True):
        record,directory,_=_validated_intent(root,intent)
        if action=='abort':
            if record['migration_id'] is not None or record['status']!='staged':raise UpdateError('Database preparation already began; finish or forward recovery is required')
            current=active_generation(root)
            if (current[1] if current else None)!=record['previous_database'] or _pointer(root)!=record['previous_application']:
                raise UpdateError('Original application/database pair changed')
            _pending(root,record);record['status']='aborted';_write(directory/'journal.json',record)
            (root/PENDING).unlink();migration._sync_directories(root,recursive=False)
            return {'status':'aborted','update_id':intent,'staged_artifacts':'retained'}
        if action=='forward':
            current=active_generation(root)
            # Only this update's current generation may be advanced. A foreign
            # database cutover is not adopted, even if its schema looks similar.
            if current is None or current[1]['generation_id']!=record['migration_id']:
                raise UpdateError('Forward recovery requires this update current database generation')
            _pending(root,record);source=migration.preview_forward(root)
            if not source['can_apply']:raise UpdateError('; '.join(source['blockers']))
            record['recovery_history'].append({'migration_id':record['migration_id'],'database_pointer':current[1]})
            record.update(status='staged',source_sha256=source['source_sha256'],previous_database=current[1],
                previous_application=_pointer(root),migration_id=None,database_pointer=None)
            _write(directory/'journal.json',record)
        elif action!='finish':raise UpdateError('Select finish, forward or pre-database abort; rollback needs separately approved conversion')
        return _finish(root,intent)


def launch_plan(root,authority,*,pinned_authority_sha256):
    root,owner=_root(root)
    with store_admission(root):
        validate_attachment(root);pointer=_pointer(root)
        if pointer is None:raise UpdateError('No owned portable application is installed')
        record,directory,manifest=_validated_intent(root,pointer['update_id'])
        if str(_unlinked(authority))!=record['authority_path'] or pinned_authority_sha256!=record['authority_sha256']:
            raise UpdateError('Launch publisher authority differs from installed binding')
        if record['status']!='committed' or record['database_pointer']!=pointer['database_pointer']:
            raise UpdateError('Application update receipt differs from launch pair')
        executable=root/GENERATIONS/pointer['application_generation']/'application'/manifest['entrypoint']
        return {'argv':[str(executable)],'environment':{'VISION_AI_STUDIO_USER_DATA_DIR':str(root)},
            'version':record['release']['version'],'database_pointer':pointer['database_pointer'],
            'runtime_packs':[row['path'] for row in record['release']['artifacts'] if row['kind']=='runtime_pack'],
            'native_signature_acceptance':'unqualified','model_quality_acceptance':'required'}


def main(argv=None):
    import argparse
    parser=argparse.ArgumentParser(description=__doc__);commands=parser.add_subparsers(dest='command',required=True)
    for command in ('install','preview'):
        install=commands.add_parser(command)
        for name in ('root','bundle','envelope','authority','pinned-authority-sha256'):install.add_argument('--'+name,required=True)
        targets=install.add_mutually_exclusive_group(required=True)
        targets.add_argument('--target-file');targets.add_argument('--target-json')
        install.add_argument('--use-owned-version',action='store_true')
        if command=='install':install.add_argument('--expected-plan-sha256')
    inspect=commands.add_parser('inspect')
    for name in ('root','authority','pinned-authority-sha256'):inspect.add_argument('--'+name,required=True)
    recover=commands.add_parser('recover');recover.add_argument('--root',required=True);recover.add_argument('--intent',required=True)
    recover.add_argument('--action',choices=('finish','forward','abort'),required=True)
    recover.add_argument('--authority');recover.add_argument('--pinned-authority-sha256')
    launch=commands.add_parser('launch-plan')
    for name in ('root','authority','pinned-authority-sha256'):launch.add_argument('--'+name,required=True)
    args=parser.parse_args(argv)
    try:
        if args.command in ('install','preview'):
            target=_json(_read(args.target_file)) if args.target_file else _json(args.target_json.encode())
            if args.use_owned_version:
                _fields(target,{'platform','arch','channel','current_version','origin'})
                target['current_version']=inspect_update(args.root,args.authority,
                    pinned_authority_sha256=args.pinned_authority_sha256)['version']
            plan=plan_update(args.root,args.bundle,args.envelope,args.authority,
                pinned_authority_sha256=args.pinned_authority_sha256,target=target)
            review=review_update(plan)
            if args.command=='install':
                if args.expected_plan_sha256 is not None and (not _hex(args.expected_plan_sha256) or args.expected_plan_sha256!=review['plan_sha256']):
                    raise UpdateError('Installation source changed after review; review it again')
                result=install_update(args.root,plan)
            else:result=review
        elif args.command=='inspect':result=inspect_update(args.root,args.authority,pinned_authority_sha256=args.pinned_authority_sha256)
        elif args.command=='recover':
            if args.authority or args.pinned_authority_sha256:
                if not args.authority or not args.pinned_authority_sha256:raise UpdateError('Both pinned authority fields are required')
                state=inspect_update(args.root,args.authority,pinned_authority_sha256=args.pinned_authority_sha256)
                if state['update_id']!=args.intent or args.action not in state['allowed_recovery']:
                    raise UpdateError('Recovery selection differs from the inspected current intent')
            result=recover_update(args.root,args.intent,action=args.action)
        else:result=launch_plan(args.root,args.authority,pinned_authority_sha256=args.pinned_authority_sha256)
        print(json.dumps(result,ensure_ascii=False));return 0
    except (ValueError,OSError,zipfile.BadZipFile) as exc:
        print(json.dumps({'status':'refused','error':str(exc)},ensure_ascii=False));return 2


if __name__=='__main__':raise SystemExit(main())
