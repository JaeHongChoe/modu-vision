"""Explicit offline POSIX portable application / owned database cutover.

This command does not discover an installed home, change OS registration,
download code, grant quality approval or provision publisher trust. The caller
must supply an independently pinned authority and an owned, drained installation.
Ordinary stores refuse attachment while a durable update intent is unfinished.
"""
from contextlib import contextmanager
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import asdict, dataclass
import base64
import errno
import hashlib
import io
import json
import os
import platform
from pathlib import Path, PosixPath, PurePosixPath
import re
import shutil
import stat
import threading
import uuid
from urllib.parse import urlsplit
import zipfile

from backend.engine import global_migration as migration
from backend.engine.global_store_paths import active_generation, owned_root, store_admission
from backend.engine.runtime_process_control import atomic_private_json
from backend.engine.application_launch_lease import assert_quiescent

ACTIVE='application-active.json'
PENDING='application-update-pending.json'
UPDATES='.application-updates'
GENERATIONS='.application-generations'
CONTROL_PATHS={ACTIVE,PENDING,UPDATES,GENERATIONS,'application-launch-lease.json','.application-launches','application-database-ownership.lock'}
MATRIX={'api_context','worker','runtime','dataset_index'}
# Fixed lexical grammar only; this holds no path, bytes or verification proof.
_SAFE_PATH_PART_MATCH=re.compile(r'[A-Za-z0-9_.@][A-Za-z0-9_. +@^\-()]{0,159}').fullmatch
_SAFE_PATH_DEVICE_MATCH=re.compile(r'^(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)',re.I).match


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
    native_parent=type(path)is PosixPath
    # Exact absolute native paths already have the original lexical value.
    # This skips only a clone; every existing fresh stat below still runs.
    # Other input kinds retain the original constructor/absolute fallback.
    if not(os.name=='posix'and native_parent and path.is_absolute()):
        path=Path(path).absolute()
    current=os.fspath(path)
    # Lexical native names are not filesystem authority: every existing stat
    # below remains fresh. All other input kinds retain the original dirname.
    native_parent=(native_parent and type(path)is PosixPath and type(current)is str
        and current.startswith('/')and not current.startswith('///')
        and '//'not in current[2:]and(current in('/','//')or not current.endswith('/')))
    while True:
        try:linked=stat.S_ISLNK(os.stat(current,follow_symlinks=False).st_mode)
        except OSError as error:
            # Preserve Path.is_symlink's selective missing/unusable-path
            # errors; os.path.islink would also hide permission and I/O errors.
            if not (getattr(error,'errno',None)in(errno.ENOENT,errno.ENOTDIR,errno.EBADF,errno.ELOOP)
                    or getattr(error,'winerror',None)in(21,123,1921)):raise
            linked=False
        except ValueError:linked=False
        if linked:raise UpdateError('Update storage cannot follow links')
        if native_parent:
            slash=current.rfind('/')
            parent=current[:slash]if slash>1 else current[:slash+1]
        else:parent=os.path.dirname(current)
        if parent==current:return path
        current=parent


@contextmanager
def _file(path,limit,*,allow_empty=False):
    path=_unlinked(path)
    fd=os.open(path,os.O_RDONLY|getattr(os,'O_NOFOLLOW',0)|getattr(os,'O_NONBLOCK',0))
    try:
        before=os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink!=1 or not (0 if allow_empty else 1)<=before.st_size<=limit:
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
    with _file(path,1024**3,allow_empty=row.get('size')==0 and row.get('executable') is False) as (reader,before):
        if before.st_size!=row['size']:raise UpdateError('Update artifact size differs')
        writer=open(copy_to,'xb') if copy_to is not None else None
        try:
            read_size=min(1024**2,before.st_size+1)
            buffer=bytearray(read_size)if(type(reader)is io.BufferedReader and copy_to is None and before.st_size>=1024**2)else None
            view=memoryview(buffer)if buffer is not None else None
            while True:
                chunk=view[:reader.readinto(buffer)]if buffer is not None else reader.read(read_size)
                if not chunk:break
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
    if type(value)is str:
        if any(p in ('.','..') or not _SAFE_PATH_PART_MATCH(p) or p.endswith(('.', ' '))
                or _SAFE_PATH_DEVICE_MATCH(p) for p in parts):
            raise UpdateError('Unsafe portable application path')
        return value
    if any(p in ('.','..') or not re.fullmatch(r'[A-Za-z0-9_.@][A-Za-z0-9_. +@^\-()]{0,159}',p) or p.endswith(('.', ' '))
            or re.match(r'^(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)',p,re.I) for p in parts):
        raise UpdateError('Unsafe portable application path')
    return value


def _application_namespaces(names):
    namespaces={}
    for name in names:
        parts=name.split('/');native_prefix=type(name)is str;end=0
        for count in range(1,len(parts)+1):
            if native_prefix:
                end+=len(parts[count-1])+(count>1);prefix=name[:end]
            else:prefix='/'.join(parts[:count])
            fold=prefix.casefold();kind='file' if count==len(parts) else 'directory'
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

    directory_aliases={}
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
        if candidate in directories:directory_aliases[name]=candidate
    # Canonical directory edges plus directory aliases must also remain acyclic.
    # Two sibling folders can point into one another without either alias being
    # an ancestor or a cyclic raw-target chain; recursive consumers would loop.
    graph={name:set() for name in directories}
    for name in directories:
        if '/' in name:
            parent=name.rsplit('/',1)[0]
            if parent in graph:graph[parent].add(name)
    for name,target in directory_aliases.items():graph[name.rsplit('/',1)[0]].add(target)
    colors={}
    for start in graph:
        if colors.get(start):continue
        colors[start]=1;stack=[(start,iter(graph[start]))]
        while stack:
            node,children=stack[-1]
            child=next(children,None)
            if child is None:colors[node]=2;stack.pop();continue
            if colors.get(child)==1:raise UpdateError('Cyclic native directory aliases')
            if not colors.get(child):colors[child]=1;stack.append((child,iter(graph[child])))
    return expected,directories

MAX_APPLICATION_MEMBERS=20000
MAX_APPLICATION_MANIFEST=8*1024**2


def _portable(archive,release,*,destination=None):
    with _file(archive,1024**3) as (reader,_),zipfile.ZipFile(reader) as bundle:
        members=bundle.infolist();names=[m.filename for m in members]
        if (not 1<len(members)<=MAX_APPLICATION_MEMBERS+1 or len(names)!=len(set(n.casefold() for n in names))
                or sum(m.file_size for m in members)>4*1024**3):raise UpdateError('Invalid portable archive bounds or duplicates')
        for member in members:
            _safe_path(member.filename);mode=member.external_attr>>16
            if (member.is_dir() or member.flag_bits&1 or member.file_size>1024**3
                    or stat.S_IFMT(mode) not in (0,stat.S_IFREG,stat.S_IFLNK)):
                raise UpdateError('Portable archive cannot contain special or encrypted files')
        if ('portable-application.json' not in names or bundle.getinfo('portable-application.json').file_size>MAX_APPLICATION_MANIFEST
                or stat.S_IFMT(bundle.getinfo('portable-application.json').external_attr>>16)not in (0,stat.S_IFREG)):
            raise UpdateError('Portable application regular manifest missing or excessive')
        raw=bundle.read('portable-application.json');manifest=_json(raw)
        native=isinstance(manifest,dict) and manifest.get('schema_version')==2
        _fields(manifest,{'schema_version','version','platform','arch','entrypoint','files'}|({'links'}if native else set()))
        if type(manifest['schema_version'])is not int or manifest['schema_version']not in (1,2) or any(manifest[k]!=release[k] for k in ('version','platform','arch')):
            raise UpdateError('Portable application target/version differs from signed release')
        rows=manifest['files']
        if not isinstance(rows,list) or not 0<len(rows)<=MAX_APPLICATION_MEMBERS:raise UpdateError('Invalid portable application inventory')
        expected={};folded=set()
        for row in rows:
            _fields(row,{'path','sha256','size','executable'});name=_safe_path(row['path'])
            if (name=='portable-application.json' or name.casefold() in folded or not _hex(row['sha256'])
                    or type(row['size'])is not int or not 0<=row['size']<=1024**3 or type(row['executable'])is not bool
                    or row['size']==0 and row['executable']):
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
    canary:dict|None=None
    canary_capability_sha256:str|None=None
    canary_preflight:dict|None=None


def plan_update(root,bundle,envelope,authority,*,pinned_authority_sha256,target,canary=None):
    if canary is not None:
        from backend.engine.staged_update_canary import validate_spec
        canary=validate_spec(canary)
    root,owner=_root(root)
    with store_admission(root):
        from backend.engine.staged_update_canary import review_spec
        canary_capability_sha256=review_spec(root,canary) if canary is not None else None
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
        manifest,_=_portable(Path(bundle)/installer['path'],release)
        from backend.engine.staged_update_canary import review_candidate
        canary_preflight=review_candidate(root,manifest,canary,canary_capability_sha256,
            archive=Path(bundle)/installer['path'])
        source=migration.preview_forward(root) if current else migration.preview(root)
        if not source['can_apply']:raise UpdateError('; '.join(source['blockers']))
        return UpdatePlan(str(root),str(_unlinked(bundle)),str(_unlinked(envelope)),str(_unlinked(authority)),
            pinned_authority_sha256,_sha(raw),release,release['compatibility']['runtime'],release['compatibility'],
            release['compatibility']['dataset_index'],tuple(row for row in release['artifacts'] if row['kind']=='runtime_pack'),
            source['source_sha256'],current[1] if current else None,previous,dict(target),canary,canary_capability_sha256,canary_preflight)


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
        'preactivation_canary':plan.canary_preflight,
        'native_signature_acceptance':'unqualified','model_quality_acceptance':'required'}


def inspect_update(root,authority,*,pinned_authority_sha256):
    """Reopen only this installation's pinned current or unfinished intent.

    Committed readback shares the live backend fence. Pending recovery requires
    exclusive admission, without repairing or granting execution rights. A
    pending pointer appearing before shared entry is refused by store admission.
    """
    root,owner=_root(root)
    raw=_read(authority,32768)
    if not _hex(pinned_authority_sha256) or _sha(raw)!=pinned_authority_sha256:
        raise UpdateError('Pinned publisher authority changed')
    with store_admission(root,exclusive=(root/PENDING).exists()):
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
    if (not isinstance(record,dict) or type(record.get('schema_version')) is not int or record['schema_version'] not in (1,2) or record.get('installation_id')!=owner['installation_id']
            or record.get('update_id')!=identifier or record.get('application_generation')!=identifier
            or record.get('status') not in ('staged','database_prepared','committed','aborted')):
        raise UpdateError('Foreign or invalid application update intent')
    return record,directory


class _IntentCheckWork:
    """One invocation-local validator; never a cached or transferable proof."""
    def __init__(self,function,args,kwargs):
        self.function=function;self.args=args;self.kwargs=kwargs
        self.lock=threading.Lock();self.complete=threading.Event()
        self.started=False;self.rejected=False;self.future=None
        self.value=None;self.error=None;self.submission_error=None

    def run(self):
        with self.lock:
            if self.rejected:return None
            self.started=True
        try:
            self.value=self.function(*self.args,**self.kwargs)
        except BaseException as error:
            self.error=error
            raise
        finally:
            self.complete.set()
        return self.value

    def reject_before_start(self):
        # submit may enqueue before raising without returning its Future. The
        # same lock prevents that late wrapper from starting original file I/O.
        with self.lock:
            if not self.started:
                self.rejected=True
                self.complete.set()


def _intent_checks_join(checks,first):
    """Keep original work custody despite interrupted caller waits."""
    for work in checks:
        if work.future is None:
            while True:
                try:
                    work.reject_before_start();break
                except BaseException as error:
                    if first is None:first=error
    pending={work.future for work in checks if work.future is not None}
    while pending:
        try:
            done,_=wait(pending,return_when=FIRST_COMPLETED)
            for future in done:
                if future.done():pending.discard(future)
        except BaseException as error:
            if first is None:first=error
    for work in checks:
        if work.future is not None:
            while True:
                try:
                    # done/result proves Future completion independently of
                    # Thread.join/shutdown's interpreter interruption behavior.
                    if not work.future.done():
                        wait((work.future,),return_when=FIRST_COMPLETED);continue
                    work.future.result();break
                except BaseException as error:
                    if error is work.error:break
                    if first is None:first=error
        while True:
            try:
                if work.complete.is_set():break
                work.complete.wait()
            except BaseException as error:
                if first is None:first=error
    return first


def _intent_parallel_checks(executor,calls,retained):
    values=[]
    for offset in range(0,len(calls),4):
        checks=[];first=None
        try:
            for function,args,kwargs in calls[offset:offset+4]:
                work=_IntentCheckWork(function,args,kwargs)
                retained.append(work);checks.append(work)
                try:
                    work.future=executor.submit(work.run)
                except BaseException as error:
                    work.submission_error=error
                    if not isinstance(error,Exception) and first is None:first=error
                    break
        except BaseException as error:
            first=error
        first=_intent_checks_join(checks,first)
        if first is not None:raise first
        # Completion order cannot replace original stage/manifest order.
        for work in checks:
            if work.submission_error is not None:raise work.submission_error
            if work.error is not None:raise work.error
            values.append(work.value)
    return values


def _intent_executor_close(executor,retained,first):
    while True:
        try:
            first=_intent_checks_join(retained,first);break
        except BaseException as error:
            if first is None:first=error
    while True:
        try:
            executor.shutdown(wait=True,cancel_futures=False);break
        except BaseException as error:
            if first is None:first=error
    # No shutdown return, including an interrupted Thread.join, substitutes
    # for the original worker completion record and known Future.done/result.
    while True:
        try:
            return _intent_checks_join(retained,first)
        except BaseException as error:
            if first is None:first=error


def _intent_namespace_range(application,manifest,links,directories,paths):
    actual=set();relative_prefix=None;native_names=type(application)is PosixPath
    for path in paths:
            if native_names and type(path)is PosixPath:
                # A lexical display prefix is not filesystem validation authority.
                # Every original guard below still runs fresh on this exact path.
                if relative_prefix is None:
                    relative_prefix=str(application)
                    if not relative_prefix.endswith('/'):relative_prefix+='/'
                member=str(path)
                name=member[len(relative_prefix):]if(member.startswith(relative_prefix)and len(member)>len(relative_prefix)and(relative_prefix!='/'or not member.startswith('//')))else path.relative_to(application).as_posix()
            else:name=path.relative_to(application).as_posix()
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
    return actual


def _intent_parallel_namespace(executor,application,manifest,retained,*,single_range=False):
    # Fresh full enumeration and original per-path guards on each pass. The
    # same invocation executor/work records are retained; no proof is reused.
    links,directories=_native_layout(manifest) if manifest['schema_version']==2 else ({},set())
    expected={row['path'] for row in manifest['files']}|set(links)|{'portable-application.json'}
    paths=[];enumeration_error=None
    try:
        for path in application.rglob('*'):paths.append(path)
    except BaseException as error:
        enumeration_error=error
    if enumeration_error is not None:
        # Keep the original prefix-path error priority without submitting work
        # after the caller has already observed incomplete enumeration.
        _intent_namespace_range(application,manifest,links,directories,paths)
        raise enumeration_error
    # Owning full-tree scans use one retained job: fresh path checks are
    # unchanged, while default direct controls retain the four-range schedule.
    count=min(1 if single_range is True else 4,len(paths))
    calls=[(_intent_namespace_range,(application,manifest,links,directories,
            paths[len(paths)*ordinal//count:len(paths)*(ordinal+1)//count]),{})
            for ordinal in range(count)]
    values=_intent_parallel_checks(executor,calls,retained)
    actual=set()
    for value in values:actual.update(value)
    # A later enumeration BaseException cannot replace an earlier original
    # path error: unchanged work/Future custody joins/selects those errors first.
    if actual!=expected:raise UpdateError('Installed application membership differs')


def _installed_intent_namespace(application,manifest):
    links,directories=_native_layout(manifest) if manifest['schema_version']==2 else ({},set())
    expected={row['path'] for row in manifest['files']}|set(links)|{'portable-application.json'}
    actual=set();relative_prefix=None;native_names=type(application)is PosixPath
    for path in application.rglob('*'):
        if native_names and type(path)is PosixPath:
            # A lexical display prefix is not filesystem validation authority.
            # Every original guard below still runs fresh on this exact path.
            if relative_prefix is None:
                relative_prefix=str(application)
                if not relative_prefix.endswith('/'):relative_prefix+='/'
            member=str(path)
            name=member[len(relative_prefix):]if(member.startswith(relative_prefix)and len(member)>len(relative_prefix)and(relative_prefix!='/'or not member.startswith('//')))else path.relative_to(application).as_posix()
        else:name=path.relative_to(application).as_posix()
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


def _installed_intent_row(application,row):
    _check_file(application/row['path'],row)
    if stat.S_IMODE((application/row['path']).stat().st_mode)!=(0o500 if row['executable'] else 0o400):
        raise UpdateError('Installed application executable mode changed')


def _intent_domain_ranges(rows,floor):
    """Two contiguous ranges; their original row identity/order is unchanged."""
    if type(floor)is not int or floor not in (64*1024,1024**2):raise UpdateError('Invalid fixed intent range floor')
    groups=[];offset=0;remaining=sum(max(row['size'],floor)for row in rows)
    count=min(2,len(rows))
    for ordinal in range(count):
        left=count-ordinal;target=(remaining+left-1)//left;start=offset;weight=0
        while offset<len(rows)-(left-1)and (offset==start or weight<target):
            weight+=max(rows[offset]['size'],floor);offset+=1
        groups.append(rows[start:offset]);remaining-=weight
    return groups


def _intent_zip_metadata(bundle,release):
    members=bundle.infolist();names=[m.filename for m in members]
    if (not 1<len(members)<=MAX_APPLICATION_MEMBERS+1 or len(names)!=len(set(n.casefold() for n in names))
            or sum(m.file_size for m in members)>4*1024**3):raise UpdateError('Invalid portable archive bounds or duplicates')
    for member in members:
        _safe_path(member.filename);mode=member.external_attr>>16
        if (member.is_dir() or member.flag_bits&1 or member.file_size>1024**3
                or stat.S_IFMT(mode) not in (0,stat.S_IFREG,stat.S_IFLNK)):
            raise UpdateError('Portable archive cannot contain special or encrypted files')
    if ('portable-application.json' not in names or bundle.getinfo('portable-application.json').file_size>MAX_APPLICATION_MANIFEST
            or stat.S_IFMT(bundle.getinfo('portable-application.json').external_attr>>16)not in (0,stat.S_IFREG)):
        raise UpdateError('Portable application regular manifest missing or excessive')
    raw=bundle.read('portable-application.json');manifest=_json(raw)
    native=isinstance(manifest,dict) and manifest.get('schema_version')==2
    _fields(manifest,{'schema_version','version','platform','arch','entrypoint','files'}|({'links'}if native else set()))
    if type(manifest['schema_version'])is not int or manifest['schema_version']not in (1,2) or any(manifest[k]!=release[k] for k in ('version','platform','arch')):
        raise UpdateError('Portable application target/version differs from signed release')
    rows=manifest['files']
    if not isinstance(rows,list) or not 0<len(rows)<=MAX_APPLICATION_MEMBERS:raise UpdateError('Invalid portable application inventory')
    expected={};folded=set()
    for row in rows:
        _fields(row,{'path','sha256','size','executable'});name=_safe_path(row['path'])
        if (name=='portable-application.json' or name.casefold() in folded or not _hex(row['sha256'])
                or type(row['size'])is not int or not 0<=row['size']<=1024**3 or type(row['executable'])is not bool
                or row['size']==0 and row['executable']):
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
    return manifest,_sha(raw),expected


def _intent_zip_range(archive,release,ordinal):
    # This fixed internal worker reopens and rechecks the original archive;
    # no supplied manifest, receipt, cache or authority marker is accepted.
    if type(ordinal)is not int or not 0<=ordinal<2:raise UpdateError('Invalid fixed ZIP range ordinal')
    manifest=None;manifest_sha=None;metadata_error=None;row_error=None;final_error=None
    try:
        with _file(archive,1024**3)as(reader,_),zipfile.ZipFile(reader)as bundle:
            manifest,manifest_sha,expected=_intent_zip_metadata(bundle,release)
            groups=_intent_domain_ranges(manifest['files'],64*1024)
            selected=groups[ordinal]if ordinal<len(groups)else[]
            indices={id(row):index for index,row in enumerate(manifest['files'])}
            for row in selected:
                try:
                    name=row['path']
                    if bundle.getinfo(name).file_size!=row['size']:raise UpdateError('Portable application size differs')
                    digest=hashlib.sha256();total=0
                    with bundle.open(name)as source:
                        while chunk:=source.read(1024**2):
                            total+=len(chunk)
                            if total>row['size']:raise UpdateError('Portable application grew while unpacking')
                            digest.update(chunk)
                    if total!=row['size']or digest.hexdigest()!=row['sha256']:raise UpdateError('Portable application checksum differs')
                except BaseException as error:
                    row_error=(indices[id(row)],error);break
    except BaseException as error:
        if manifest is None:metadata_error=error
        else:final_error=error
    # Values are invocation-local bookkeeping. Every original error object
    # survives; caller interruption/submission custody is owned by the unchanged
    # _intent_parallel_checks machinery, before domain/index error selection.
    return manifest,manifest_sha,metadata_error,row_error,final_error


def _intent_installed_range(application,rows):
    for index,row in rows:
        try:_installed_intent_row(application,row)
        except BaseException as error:return index,error
    return None


def _intent_overlap_error(values,manifest,manifest_sha,installed_pre_error):
    zipped=values[:2]
    for checked,checked_sha,metadata_error,_,_ in zipped:
        if metadata_error is not None:return metadata_error
        if checked_sha!=manifest_sha or _canonical(checked)!=_canonical(manifest):
            return UpdateError('Original ZIP metadata changed within fresh invocation')
    errors=[value[3]for value in zipped if value[3]is not None]
    if errors:return min(errors,key=lambda value:value[0])[1]
    for value in zipped:
        if value[4]is not None:return value[4]
    if len(zipped)==2:
        if installed_pre_error is not None:return installed_pre_error
        errors=[value for value in values[2:]if value is not None]
        if errors:return min(errors,key=lambda value:value[0])[1]
    return None


def _validated_intent(root,identifier):
    record,directory=_intent(root,identifier)
    release,raw=verify_release(directory/'envelope.json',record['authority_path'],record['authority_sha256'],record['target'])
    if _sha(raw)!=record['envelope_sha256']or release!=record['release']:raise UpdateError('Application update release binding changed')
    installer=next(row for row in release['artifacts']if row['kind']=='installer')
    executor=ThreadPoolExecutor(max_workers=4);retained=[];first=None;result=None
    try:
        archive=directory/'bundle'/installer['path']
        # Keep an original archive OFD alive after the signed raw check across
        # bounded metadata, all jobs and original namespace/mode checks.
        # Every worker additionally runs its own original _file pre/post guard.
        # Signed raw bundle errors retain their original priority over archive
        # open/ZIP parsing errors. No portable or installed read precedes it.
        _intent_parallel_checks(executor,[(_bundle,(directory/'bundle',release),{})],retained)
        with _file(archive,1024**3)as(reader,_),zipfile.ZipFile(reader)as bundle:
            manifest,manifest_sha,_=_intent_zip_metadata(bundle,release)
            installed_pre_error=None;application=None
            try:
                generation=_unlinked(root/GENERATIONS/identifier);application=generation/'application'
                if _sha(_read(application/'portable-application.json',MAX_APPLICATION_MANIFEST))!=manifest_sha:raise UpdateError('Installed application manifest integrity differs')
                _intent_parallel_namespace(executor,application,manifest,retained,single_range=True)
            except BaseException as error:
                # Caller interruption is never converted to a verifier result
                # or a reason to launch more work. Ordinary pre-scan refusal is
                # deferred solely to preserve original archive error priority.
                if not isinstance(error,Exception):raise
                installed_pre_error=error
            calls=[(_intent_zip_range,(archive,release,ordinal),{})for ordinal in range(2)]
            if installed_pre_error is None:
                indices={id(row):index for index,row in enumerate(manifest['files'])}
                groups=_intent_domain_ranges(manifest['files'],1024**2)
                calls.extend((_intent_installed_range,(application,[(indices[id(row)],row)for row in group]),{})for group in groups)
            stage_start=len(retained)
            try:results=_intent_parallel_checks(executor,calls,retained)
            except Exception as error:
                # Original helper has already joined every started/hidden job.
                # Only an exact ordinary submission failure may be preceded by
                # a deferred domain error from earlier original work ordinals.
                # Caller wait/result errors and KI/SystemExit keep their object.
                checks=retained[stage_start:]
                for index,work in enumerate(checks):
                    if work.submission_error is error:
                        earlier=checks[:index]
                        if all(value.error is None for value in earlier):
                            prior=_intent_overlap_error([value.value for value in earlier],manifest,manifest_sha,installed_pre_error)
                            if prior is not None:raise prior
                        break
                raise
            error=_intent_overlap_error(results,manifest,manifest_sha,installed_pre_error)
            if error is not None:raise error
            _intent_parallel_namespace(executor,application,manifest,retained,single_range=True)
            for row in manifest['files']:
                if stat.S_IMODE((application/row['path']).stat().st_mode)!=(0o500 if row['executable']else 0o400):
                    raise UpdateError('Installed application executable mode changed')
            if record['migration_id']is not None:
                _,database=migration._journal(root,record['migration_id'])
                if(database.get('installation_id')!=record['installation_id']
                        or database.get('source_sha256')!=record['source_sha256']
                        or database.get('previous_pointer')!=record['previous_database']):
                    raise UpdateError('Application update database intent binding changed')
            result=record,directory,manifest
    except BaseException as error:
        first=error
    finally:
        first=_intent_executor_close(executor,retained,first)
    if first is not None:raise first
    return result


def _pending(root,record):
    path=root/PENDING
    if path.exists():
        current=_json(_read(path))
        if current!={'schema_version':1,'installation_id':record['installation_id'],'update_id':record['update_id']}:
            raise UpdateError('Another application update owns recovery admission')
    else:_write(path,{'schema_version':1,'installation_id':record['installation_id'],'update_id':record['update_id']})


def _finish(root,identifier):
    record,directory,manifest=_validated_intent(root,identifier)
    if record['status']=='aborted':raise UpdateError('Aborted update cannot be activated')
    current=active_generation(root)
    if record['status']=='committed':
        expected={'schema_version':1,'installation_id':record['installation_id'],'update_id':identifier,
            'application_generation':identifier,'database_pointer':record['database_pointer']}
        if _pointer(root)!=expected or current is None or current[1]!=record['database_pointer']:
            raise UpdateError('Committed application/database pair changed; forward recovery required')
        _pending(root,record)
    else:
        from backend.engine.staged_update_canary import ensure_verified,validate_spec
        if record['schema_version']!=2:raise UpdateError('Legacy unfinished application update lacks required preactivation canary; abort or review a new update')
        validate_spec(record.get('canary'))
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
                ensure_verified(root,record,journal,manifest);_checkpoint('canary_verified')
            migrate=migration.advance if current else migration.apply
            migrate(root,expected_source_sha256=record['source_sha256'],on_prepared=prepared,application_update_id=identifier)
        else:
            _,database=migration._journal(root,record['migration_id'])
            ensure_verified(root,record,database,manifest)
            migration.recover(root,record['migration_id'],action='finish')
        current=active_generation(root)
        if current is None or current[1]['generation_id']!=record['migration_id']:raise UpdateError('Prepared database generation differs')
        record['database_pointer']=current[1]
        _write(directory/'journal.json',record);_checkpoint('after_database')
        from backend.engine.staged_update_canary import require_publication
        require_publication(root,migration._journal(root,record['migration_id'])[1])
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
    from backend.engine.staged_update_canary import validate_spec,validate_ready_review
    validate_spec(plan.canary)
    if (not isinstance(plan.canary_preflight,dict)
            or (plan.canary_preflight.get('protocol'),plan.canary_preflight.get('status'),plan.canary_preflight.get('policy')) not in
                ((1,'source_ready','same_reviewed_source_runtime_worker_v1'),(2,'frozen_ready','same_reviewed_frozen_runtime_worker_v1'))
            or plan.canary_preflight.get('supported') is not True):
        reason=plan.canary_preflight.get('reason') if isinstance(plan.canary_preflight,dict) else None
        raise UpdateError('Preactivation canary '+str(reason or 'requires a newly reviewed supported source candidate'))
    validate_ready_review(plan.canary_preflight,plan.canary,plan.canary_capability_sha256)
    with store_admission(root,exclusive=True):
        assert_quiescent(root)
        validate_attachment(root)
        fresh=plan_update(root,plan.bundle,plan.envelope,plan.authority,
            pinned_authority_sha256=plan.authority_sha256,target=plan.target,canary=plan.canary)
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
        record={'schema_version':2,'installation_id':owner['installation_id'],'update_id':identifier,
            'application_generation':identifier,'status':'staged','authority_path':plan.authority,
            'authority_sha256':plan.authority_sha256,'envelope_sha256':plan.envelope_sha256,'target':plan.target,
            'release':plan.app,'source_sha256':plan.source_sha256,'previous_database':plan.previous_database,
            'previous_application':plan.previous_application,'migration_id':None,'database_pointer':None,'recovery_history':[],
            'canary':dict(plan.canary),'canary_requirement_sha256':None,'canary_receipt_sha256':None}
        record['canary_capability_sha256']=plan.canary_capability_sha256
        record['canary_execution_protocol']=plan.canary_preflight['protocol']
        record['canary_worker_binding']=plan.canary_preflight.get('worker_binding')
        _write(directory/'journal.json',record)
        return _finish(root,identifier)


def recover_update(root,intent,*,action='finish'):
    root,owner=_root(root)
    with store_admission(root,exclusive=True):
        assert_quiescent(root)
        record,directory,_=_validated_intent(root,intent)
        if action=='abort':
            if record['status'] not in ('staged','database_prepared'):raise UpdateError('Applied database update requires finish or forward recovery')
            current=active_generation(root)
            if (current[1] if current else None)!=record['previous_database'] or _pointer(root)!=record['previous_application']:
                raise UpdateError('Original application/database pair changed')
            from backend.engine.staged_update_canary import assert_no_spawn_abort
            assert_no_spawn_abort(root,record)
            if record['migration_id'] is not None:
                path,database=migration._journal(root,record['migration_id'])
                database['application_update_id']=intent;migration.atomic_private_json(path,database)
                migration._sync_directories(path.parent,recursive=False)
            _pending(root,record);record['status']='aborted';_write(directory/'journal.json',record)
            (root/PENDING).unlink();migration._sync_directories(root,recursive=False)
            return {'status':'aborted','update_id':intent,'staged_artifacts':'retained'}
        if action=='forward':
            from backend.engine.staged_update_canary import validate_spec
            if record['schema_version']!=2:raise UpdateError('Legacy application forward recovery requires a newly reviewed preactivation canary')
            validate_spec(record.get('canary'))
            current=active_generation(root)
            # Only this update's current generation may be advanced. A foreign
            # database cutover is not adopted, even if its schema looks similar.
            if current is None or current[1]['generation_id']!=record['migration_id']:
                raise UpdateError('Forward recovery requires this update current database generation')
            _pending(root,record);source=migration.preview_forward(root)
            if not source['can_apply']:raise UpdateError('; '.join(source['blockers']))
            record['recovery_history'].append({'migration_id':record['migration_id'],'database_pointer':current[1]})
            record.update(status='staged',source_sha256=source['source_sha256'],previous_database=current[1],
                previous_application=_pointer(root),migration_id=None,database_pointer=None,
                canary_requirement_sha256=None,canary_receipt_sha256=None)
            _write(directory/'journal.json',record)
        elif action!='finish':raise UpdateError('Select finish, forward or pre-database abort; rollback needs separately approved conversion')
        return _finish(root,intent)


def _launch_binding_pair(root,authority,backend_frame,*,pinned_authority_sha256):
    """Freshly inspect one launch pair and its optional fixed backend row.

    The validated intent never leaves this invocation as an input capability.
    A backend frame selects fixed checks, not a supplied manifest or validator.
    Each caller's original owner pre/post pass independently enters this path.
    """
    root,owner=_root(root)
    validate_attachment(root);pointer=_pointer(root)
    if pointer is None:raise UpdateError('No owned portable application is installed')
    record,directory,manifest=_validated_intent(root,pointer['update_id'])
    if str(_unlinked(authority))!=record['authority_path'] or pinned_authority_sha256!=record['authority_sha256']:
        raise UpdateError('Launch publisher authority differs from installed binding')
    if record['status']!='committed' or record['database_pointer']!=pointer['database_pointer']:
        raise UpdateError('Application update receipt differs from launch pair')
    application=root/GENERATIONS/pointer['application_generation']/'application'
    executable=application/manifest['entrypoint']
    entrypoint=next(row for row in manifest['files'] if row['path']==manifest['entrypoint'])
    current=active_generation(root)
    binding={'installation_id':owner['installation_id'],'update_id':pointer['update_id'],
        'application_generation':pointer['application_generation'],'database_pointer':pointer['database_pointer'],
        'database_generation_path':str(current[0]),'executable':str(executable),'executable_sha256':entrypoint['sha256'],
        'application_manifest_sha256':_sha(_read(application/'portable-application.json',MAX_APPLICATION_MANIFEST)),
        'source_sha256':record['source_sha256'],'envelope_sha256':record['envelope_sha256'],
        'authority_path':record['authority_path'],'authority_sha256':record['authority_sha256'],
        'version':record['release']['version'],
        'runtime_packs':[row['path'] for row in record['release']['artifacts'] if row['kind']=='runtime_pack']}

    if backend_frame is None:
        return binding, None
    from backend.engine.application_launch_handshake import HandshakeError, _same_process
    from backend.engine import application_launch_lease as lease
    # Preserve the original fresh-pair -> original parent -> executable order.
    # Read the original lease afresh under the caller's retained transition
    # OFD; no caller-provided record, manifest or admission marker is accepted.
    if _canonical(backend_frame['binding']) != _canonical(binding):
        raise HandshakeError('Committed backend launch pair changed')
    original = lease._load(root)
    if (original is None or original['nonce'] != backend_frame['nonce']
            or original['state'] not in {'starting', 'ready'} or not original['spawn_attempted']
            or original['process'] is None):
        raise HandshakeError('Backend challenge has no current spawned main owner')
    if _canonical(original['binding']) != _canonical(binding):
        raise HandshakeError('Committed backend launch pair changed')
    try: main = lease._identity(os.getppid())
    except Exception as exc:
        import psutil
        if not isinstance(exc, psutil.Error): raise
        raise HandshakeError('Backend parent process identity is unavailable') from exc
    if not _same_process(backend_frame['main_process'], main) or not _same_process(original['process'], main):
        raise HandshakeError('Backend challenge main process birth or parent identity differs')
    application = root/GENERATIONS/binding['application_generation']/'application'
    executable = backend_frame['backend_executable']
    if not isinstance(executable, str) or not Path(executable).is_absolute() or str(Path(executable)) != executable:
        raise HandshakeError('Backend executable must be an absolute current application row')
    path = _unlinked(executable)
    try: relative = path.relative_to(application).as_posix()
    except ValueError as exc: raise HandshakeError('Backend executable is outside current application') from exc
    rows = [row for row in manifest['files'] if row['path'] == relative and row['executable']]
    if len(rows) != 1: raise HandshakeError('Backend executable is not an approved current application row')
    row = rows[0]; _check_file(path, row)
    import psutil, sys
    command = psutil.Process(os.getpid()).cmdline()
    frozen = bool(getattr(sys, 'frozen', False)); build = backend_frame['backend_build_identity_sha256']
    if frozen:
        if sys.executable != executable or not command or command[0] != executable or not _hex(build):
            raise HandshakeError('Frozen backend executable or build identity differs')
        receipt_path = path.parent/'backend-release.json'
        receipt_rows = [item for item in manifest['files'] if item['path'] == receipt_path.relative_to(application).as_posix()]
        if len(receipt_rows) != 1: raise HandshakeError('Frozen backend release receipt is not an approved application row')
        _check_file(receipt_path, receipt_rows[0]); receipt = _json(_read(receipt_path, 8*1024**2))
        inventory = _json(_read(Path(getattr(sys, '_MEIPASS', ''))/'backend-build-inventory.json', 8*1024**2))
        if (not isinstance(receipt, dict) or receipt.get('schema_version') != 1 or receipt.get('executable') != path.name
                or receipt.get('executable_sha256') != row['sha256'] or not isinstance(inventory, dict)
                or inventory.get('build_identity_sha256') != build
                or _canonical(receipt.get('inventory')) != _canonical(inventory)):
            raise HandshakeError('Frozen backend release receipt or embedded inventory differs')
    else:
        if build is not None or not sys.argv or sys.argv[0] != executable or executable not in command[1:2]:
            raise HandshakeError('Source backend must execute its approved script and cannot claim a frozen build')
    return binding, {'executable': executable, 'executable_sha256': row['sha256'],
            'build_identity_sha256': build, 'frozen': frozen}


def _launch_binding(root,authority,*,pinned_authority_sha256):
    """Derive fixed launch identities under the caller's installation admission."""
    return _launch_binding_pair(root,authority,None,pinned_authority_sha256=pinned_authority_sha256)[0]


def _backend_launch_binding(root,authority,frame,*,pinned_authority_sha256):
    """Fixed backend inspection composed with a freshly validated launch pair.

    No manifest, validation marker, callback or cached proof is accepted.
    This plain result is evidence; original lease/backend admission remains
    the sole authority and performs this entire inspection before and after.
    """
    if frame is None:
        raise UpdateError('Backend launch inspection requires its original frame')
    return _launch_binding_pair(root,authority,frame,pinned_authority_sha256=pinned_authority_sha256)


def launch_plan(root,authority,*,pinned_authority_sha256):
    root,owner=_root(root)
    with store_admission(root):
        binding=_launch_binding(root,authority,pinned_authority_sha256=pinned_authority_sha256)
        return {'argv':[binding['executable']],'environment':{'VISION_AI_STUDIO_USER_DATA_DIR':str(root)},
            'version':binding['version'],'database_pointer':binding['database_pointer'],
            'runtime_packs':binding['runtime_packs'],
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
        for name in ('canary-workspace-id','canary-project-id','canary-plan-sha256'):install.add_argument('--'+name)
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
                pinned_authority_sha256=args.pinned_authority_sha256,target=target,
                canary={'workspace_id':args.canary_workspace_id,'project_id':args.canary_project_id,
                        'plan_sha256':args.canary_plan_sha256}
                    if any((args.canary_workspace_id,args.canary_project_id,args.canary_plan_sha256)) else None)
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
