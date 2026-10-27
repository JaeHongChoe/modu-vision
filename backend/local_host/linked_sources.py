"""Explicit localhost-only review -> native consent -> immutable linked-source copy.

No project source or job is changed here. Ordinary staging and shared follow-links
remain denied; only a one-use server-held plan can materialize canonical targets.
"""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import secrets
import stat
import threading
import time
import uuid

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from .grants import _scope, literal_path


class MetadataLimitError(ValueError):
    """A bounded, path-free explanation safe to return to the local owner."""


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()


def _sha(value):
    return hashlib.sha256(_json(value)).hexdigest()


def _identity_info(info):
    return [info.st_dev, info.st_ino, stat.S_IFMT(info.st_mode), info.st_nlink,
            info.st_size, info.st_mtime_ns, info.st_ctime_ns]


def _identity(path):
    return _identity_info(Path(path).lstat())


def _source(project):
    return {'project_id': project['id'], 'source_dataset_dir': project.get('source_dataset_dir'),
            'task': project['task'], 'annotations_dir': project['annotations_dir'],
            'active_labelset_id': project.get('active_labelset_id', 'default')}


def _read_file(path, expected, *, writer=None, guard=lambda: None):
    literal_path(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | getattr(os, 'O_NONBLOCK', 0))
    try:
        if _identity_info(os.fstat(fd)) != expected or not stat.S_ISREG(os.fstat(fd).st_mode) or os.fstat(fd).st_nlink != 1:
            raise ValueError('Reviewed input identity changed')
        digest = hashlib.sha256(); count = 0
        while True:
            guard(); block = os.read(fd, 1024 * 1024)
            if not block: break
            count += len(block)
            if count > expected[4]: raise ValueError('Reviewed input grew')
            digest.update(block)
            if writer is not None:
                offset = 0
                while offset < len(block): offset += os.write(writer, block[offset:])
        if count != expected[4] or _identity_info(os.fstat(fd)) != expected or _identity(path) != expected:
            raise ValueError('Reviewed input changed while reading')
        literal_path(path)
        return digest.hexdigest()
    finally: os.close(fd)


class SnapshotWriter:
    """All output mutation stays relative to pinned no-follow directory descriptors."""
    def __init__(self, project, max_attempts, expected_identity, *, container='.local-host-linked-sources'):
        self.fds=[]; self.project=project
        try:
            if container not in {'.local-host-imports','.local-host-linked-sources'}:raise ValueError('Invalid snapshot container')
            if os.name!='posix' or not getattr(os,'O_NOFOLLOW',0) or not getattr(os,'O_DIRECTORY',0) or os.open not in os.supports_dir_fd:
                raise ValueError('Import snapshots require qualified POSIX no-follow filesystem operations')
            project_fd=self.directory(project)
            if _identity_info(os.fstat(project_fd))[:3] != expected_identity or _identity(project)[:3]!=expected_identity:raise ValueError('Project changed')
            self.project_identity=expected_identity
            try: os.mkdir(container, 0o700, dir_fd=project_fd)
            except FileExistsError: pass
            parent_fd=self.directory(container, project_fd)
            if max_attempts is not None:
                with os.scandir(parent_fd) as entries:
                    for count,_ in enumerate(entries,1):
                        if count>=max_attempts:raise ValueError('Linked-source snapshot retention limit reached')
            self.identifier=uuid.uuid4().hex;os.mkdir(self.identifier,0o700,dir_fd=parent_fd)
            self.destination_fd=self.directory(self.identifier,parent_fd)
            os.mkdir('payload',0o700,dir_fd=self.destination_fd);self.payload_fd=self.directory('payload',self.destination_fd)
            self.payload=project/container/self.identifier/'payload'
        except BaseException: self.close();raise

    def directory(self, name, parent=None):
        fd=os.open(name,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=parent);self.fds.append(fd);return fd

    def output(self, relative):
        parts=Path(relative).parts
        if not parts or Path(relative).is_absolute() or any(p in {'','..','.'} for p in parts): raise ValueError('Invalid snapshot path')
        parent=self.payload_fd;opened=[]
        try:
            for part in parts[:-1]:
                try:os.mkdir(part,0o700,dir_fd=parent)
                except FileExistsError:pass
                parent=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=parent);opened.append(parent)
            return os.open(parts[-1],os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=parent)
        finally:
            for fd in reversed(opened):os.close(fd)

    def receipt(self, value):
        # Never open an existing receipt for writing: a concurrent filesystem
        # writer may have installed a regular hardlink to an original file.
        temporary=f'.snapshot-{uuid.uuid4().hex}.json'
        fd=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=self.destination_fd)
        try:
            raw=_json(value);offset=0
            while offset<len(raw):offset+=os.write(fd,raw[offset:])
            os.fchmod(fd,0o400);os.fsync(fd)
            identity=_identity_info(os.fstat(fd))
            if identity[3]!=1 or _identity_info(os.stat(temporary,dir_fd=self.destination_fd,follow_symlinks=False))!=identity:
                raise ValueError('Snapshot receipt changed')
            os.replace(temporary,'snapshot.json',src_dir_fd=self.destination_fd,dst_dir_fd=self.destination_fd)
            published=os.stat('snapshot.json',dir_fd=self.destination_fd,follow_symlinks=False)
            if not stat.S_ISREG(published.st_mode) or published.st_nlink!=1 or (published.st_dev,published.st_ino)!=(identity[0],identity[1]):
                raise ValueError('Snapshot receipt publication changed')
            os.fsync(self.destination_fd)
        finally:os.close(fd)
        # A failed publication intentionally leaves its exclusive temporary
        # receipt as evidence. The failure path publishes a separate failed one.

    def freeze(self):
        def walk(fd):
            for name in os.listdir(fd):
                info=os.stat(name,dir_fd=fd,follow_symlinks=False)
                if stat.S_ISDIR(info.st_mode):
                    child=os.open(name,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd)
                    try:walk(child)
                    finally:os.close(child)
                elif not stat.S_ISREG(info.st_mode) or info.st_nlink!=1:raise ValueError('Snapshot changed')
            os.fchmod(fd,0o500);os.fsync(fd)
        walk(self.payload_fd)
        if _identity(literal_path(self.project))[:3]!=self.project_identity or _identity(literal_path(self.payload))[:3]!=_identity_info(os.fstat(self.payload_fd))[:3]:
            raise ValueError('Snapshot location changed')

    def close(self):
        for fd in reversed(self.fds):os.close(fd)
        self.fds=[]


def copy_reviewed_file(entry, writer, guard):
    guard();fd=writer.output(entry['path'])
    try:
        digest=_read_file(Path(entry['canonical']),entry['identity'],writer=fd,guard=guard)
        if digest!=entry['sha256']:raise ValueError('Reviewed input content changed')
        os.fchmod(fd,0o400);os.fsync(fd)
        return {'path':entry['path'],'bytes':entry['identity'][4],'sha256':digest,'identity':_identity_info(os.fstat(fd))}
    finally:os.close(fd)


class LinkedSources:
    def __init__(self,controller,*,max_bytes=4*1024**3,max_files=10_000,max_links=32,max_depth=24,max_nodes=25_000,max_plan_bytes=8*1024**2,max_plans=16,max_snapshots=16,ttl=120,now=time.monotonic):
        self.controller=controller;self.max_bytes=max_bytes;self.max_files=max_files;self.max_links=max_links;self.max_depth=max_depth
        self.max_nodes=max_nodes;self.max_plan_bytes=max_plan_bytes
        self.max_plans=max_plans;self.max_snapshots=max_snapshots;self.ttl=ttl;self.now=now;self.plans={};self.snapshots={};self.active=0;self.lock=threading.RLock()

    def authorize(self,request,expected=None,previous=None):
        authority=(self.controller.recheck(request,'linked_sources',previous,project=True) if previous else self.controller.authorize(request,'linked_sources',project=True))
        if authority['role']!='owner':raise HTTPException(403,'Only the current project owner may review linked source targets')
        project=authority['project']
        if expected is not None and _source(project)!=expected:raise HTTPException(409,'The registered source or label set changed; review it again')
        return authority

    def supported(self):
        if os.name!='posix' or not getattr(os,'O_NOFOLLOW',0) or os.open not in os.supports_dir_fd:
            raise ValueError('Reviewed link snapshots require qualified POSIX no-follow filesystem operations')

    def scan(self,project):
        self.supported();workspace=literal_path(project['project_dir']);source=literal_path(project.get('source_dataset_dir') or '')
        protected=[Path(p).resolve() for p in self.controller.protected]
        def safe(path,directory=False):
            canonical=literal_path(path)
            private_parts={'.ssh','.aws','.azure','.gnupg','.kube','.git','.codex','.claude','keychains'}
            private_names={'.env','id_rsa','id_ed25519','credentials','credentials.json','providers.json','accounts.sqlite','chatgpt-sessions.json','chatgpt-sessions.lock','auth.json','oauth_credentials.json','tokens.json','token.json','.netrc','.npmrc','.pypirc'}
            if any(part.casefold() in private_parts for part in canonical.parts) or canonical.name.casefold() in private_names or canonical.name.casefold().startswith('.env.') or canonical.suffix.lower() in {'.key','.pem','.p12','.pfx'}:
                raise ValueError('Private credential material cannot be reviewed')
            if any(part.casefold() in {'.local-host-imports','.local-host-linked-sources'} for part in canonical.parts):raise ValueError('Existing snapshots cannot be imported again')
            if any(canonical==p or canonical.is_relative_to(p) or directory and p.is_relative_to(canonical) for p in protected):raise ValueError('Private host state cannot be reviewed')
            if directory and (canonical==workspace or workspace.is_relative_to(canonical)):raise ValueError('Source cannot contain the project')
            return canonical
        if not source.is_dir():raise ValueError('A registered directory source is required')
        safe(source,True);files=[];nodes=[];targets=[];total=0;claimed=0;metadata_bytes=0
        def claim_node():
            nonlocal claimed
            if claimed>=self.max_nodes:raise MetadataLimitError(f'Linked source exceeds metadata node limit ({self.max_nodes:,} nodes, including directories and link targets)')
            claimed+=1
        def retain(rows,row):
            nonlocal metadata_bytes
            metadata_bytes+=len(_json(row))+1
            if metadata_bytes>self.max_plan_bytes:raise MetadataLimitError(f'Linked source exceeds metadata limit ({self.max_plan_bytes:,} bytes)')
            rows.append(row)
        def walk(path,relative,ancestors,depth,already_claimed=False):
            nonlocal total
            if not already_claimed:claim_node()
            if depth>self.max_depth:raise ValueError('Linked source exceeds depth limit')
            info=path.lstat()
            if stat.S_ISLNK(info.st_mode):
                if len(targets)>=self.max_links:raise ValueError('Too many linked targets')
                canonical=path.resolve(strict=True);kind='directory' if canonical.is_dir() else 'file';safe(canonical,kind=='directory')
                retain(targets,{'link':str(relative),'path':str(canonical),'kind':kind});retain(nodes,{'path':str(relative),'link_identity':_identity_info(info),'target':str(canonical),'link_text':os.readlink(path)})
                walk(canonical,relative,ancestors,depth+1);return
            canonical=safe(path,stat.S_ISDIR(info.st_mode));identity=_identity_info(info)
            retain(nodes,{'path':str(relative),'canonical':str(canonical),'identity':identity})
            if stat.S_ISDIR(info.st_mode):
                key=(info.st_dev,info.st_ino)
                if key in ancestors:raise ValueError('Linked source cycle is not allowed')
                # Claim queued children too, before sorting or retaining them.
                # Path.iterdir/listdir can eagerly allocate an entire directory.
                children=[]
                with os.scandir(path) as entries:
                    for entry in entries:
                        claim_node();children.append(Path(entry.path))
                for child in sorted(children,key=lambda p:p.name):walk(child,relative/child.name,ancestors|{key},depth+1,True)
            elif stat.S_ISREG(info.st_mode) and info.st_nlink==1:
                total+=info.st_size
                if total>self.max_bytes or len(files)>=self.max_files:raise ValueError('Linked source exceeds snapshot limits')
                retain(files,{'path':str(relative),'canonical':str(canonical),'identity':identity})
            else:raise ValueError('Only single-link regular files are supported')
        walk(source,Path('.'),set(),0)
        if not targets or not files:raise ValueError('Registered source must contain linked data files')
        if len(_json(targets))+len(str(source).encode())+len(str(workspace).encode())>16000:raise ValueError('Linked target review text exceeds native display bound')
        value={'source':str(source),'project_dir':str(workspace),'project_identity':_identity(workspace)[:3],'expected_source':_source(project),'targets':targets,'nodes':nodes,'files':files,'file_count':len(files),'total_bytes':total}
        if len(_json(value))+len(',"plan_sha256":""')+64>self.max_plan_bytes:raise MetadataLimitError(f'Linked source exceeds metadata limit ({self.max_plan_bytes:,} bytes)')
        value['plan_sha256']=_sha(value);return value

    def preview(self,authority):
        plan=self.scan(authority['project']);identifier=secrets.token_urlsafe(24)
        with self.lock:
            self.plans={k:v for k,v in self.plans.items() if v['expires']>self.now()}
            if len(self.plans)>=self.max_plans:raise ValueError('Too many unconsumed linked-source reviews')
            self.plans[identifier]={'scope':_scope(authority),'plan':plan,'expires':self.now()+self.ttl}
        return {'plan_id':identifier,'plan_sha256':plan['plan_sha256'],'source':plan['source'],'targets':plan['targets'],'file_count':plan['file_count'],'total_bytes':plan['total_bytes'],'expires_in':self.ttl}

    def take(self,authority,identifier,digest):
        with self.lock:
            row=self.plans.get(identifier)
            if not row or row['expires']<=self.now():self.plans.pop(identifier,None);raise HTTPException(409,'Linked-source review expired or already consumed')
            if row['scope']!=_scope(authority):raise HTTPException(403,'Linked-source review belongs to another tab or authority')
            if row['plan']['plan_sha256']!=digest:raise HTTPException(409,'Linked-source review hash changed')
            del self.plans[identifier]
        return row

    def unchanged(self,authority,row):
        if row['expires']<=self.now() or self.scan(authority['project'])['plan_sha256']!=row['plan']['plan_sha256']:
            raise ValueError('Reviewed source or linked targets changed')

    def materialize(self,request,authority,row,stop):
        plan=row['plan'];files=[];writer=None;reserved=False
        def guard():
            if stop.is_set():raise ValueError('Linked-source copy was cancelled')
            current=self.authorize(request,plan['expected_source'],authority)
            if str(literal_path(current['project']['project_dir']))!=plan['project_dir']:raise ValueError('Project location changed')
        try:
            guard()
            with self.lock:
                if len(self.snapshots)+self.active>=self.max_snapshots:raise ValueError('Linked-source snapshot limit reached')
                writer=SnapshotWriter(Path(plan['project_dir']),self.max_snapshots,plan['project_identity']);self.active+=1;reserved=True
            for entry in plan['files']:
                # Content authority begins only after the exact metadata plan received native consent.
                guard();reviewed={**entry,'sha256':_read_file(Path(entry['canonical']),entry['identity'],guard=guard)}
                files.append(copy_reviewed_file(reviewed,writer,guard))
            guard();self.unchanged(self.authorize(request,plan['expected_source'],authority),row);writer.freeze();guard()
            manifest={'schema_version':1,'state':'complete','purpose':'reviewed-linked-source','plan_sha256':plan['plan_sha256'],'expected_source':plan['expected_source'],'targets':plan['targets'],'files':files}
            writer.receipt(manifest);digest=_sha(manifest)
            result={'snapshot_id':writer.identifier,'manifest_sha256':digest,'source':str(writer.payload),'file_count':len(files),'total_bytes':plan['total_bytes']}
            with self.lock:self.snapshots[writer.identifier]={'scope':_scope(authority),'expected_source':plan['expected_source'],'project_dir':plan['project_dir'],'project_identity':plan['project_identity'],'expires':self.now()+1800,'result':result}
            return result
        except BaseException as error:
            if writer:writer.receipt({'schema_version':1,'state':'failed','purpose':'reviewed-linked-source','plan_sha256':plan['plan_sha256'],'files':files,'error':type(error).__name__})
            raise
        finally:
            if writer:writer.close()
            if reserved:
                with self.lock:self.active-=1

    def use_check(self,request,authority,identifier,digest):
        row=self.snapshots.get(identifier)
        if not row or row['expires']<=self.now() or row['result']['manifest_sha256']!=digest:raise HTTPException(409,'Linked-source copy expired or changed')
        if row['scope']!=_scope(authority):raise HTTPException(403,'Linked-source copy belongs to another tab or authority')
        self.authorize(request,row['expected_source'],authority)
        if _identity(literal_path(row['project_dir']))[:3]!=row['project_identity']:raise ValueError('Project location changed')
        root=literal_path(row['result']['source']);receipt=literal_path(root.parent/'snapshot.json')
        if receipt.stat().st_size>8*1024**2:raise ValueError('Snapshot manifest exceeds limit')
        manifest=json.loads(receipt.read_bytes())
        if _sha(manifest)!=digest or manifest.get('state')!='complete':raise ValueError('Snapshot manifest changed')
        expected=set()
        for entry in manifest['files']:
            relative=Path(entry['path'])
            if relative.is_absolute() or '..' in relative.parts:raise ValueError('Invalid snapshot path')
            candidate=literal_path(root/relative)
            if not candidate.is_relative_to(root) or _read_file(candidate,entry['identity'])!=entry['sha256']:raise ValueError('Snapshot content changed')
            expected.add(str(relative))
        actual={str(p.relative_to(root)) for p in root.rglob('*') if p.is_file() or p.is_symlink()}
        if actual!=expected:raise ValueError('Snapshot inventory changed')
        self.authorize(request,row['expected_source'],authority);return row['result']


class ExpectedSource(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    project_id:str=Field(min_length=1,max_length=128)
    source_dataset_dir:str|None=Field(max_length=4096)
    task:str=Field(min_length=1,max_length=80)
    annotations_dir:str=Field(min_length=1,max_length=4096)
    active_labelset_id:str=Field(min_length=1,max_length=128)

class Preview(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    expected_source:ExpectedSource

class Materialize(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    plan_id:str=Field(min_length=20,max_length=128)
    plan_sha256:str=Field(pattern=r'^[a-f0-9]{64}$')

class UseCheck(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    snapshot_id:str=Field(pattern=r'^[a-f0-9]{32}$')
    manifest_sha256:str=Field(pattern=r'^[a-f0-9]{64}$')


def install_linked_sources(controller):
    store=LinkedSources(controller);controller.linked_sources=store
    router=APIRouter(prefix='/api/local-host/linked-sources',tags=['local-linked-sources'])
    def reply(value):return JSONResponse(value,headers={'Cache-Control':'no-store'})
    @router.post('/preview')
    async def preview(body:Preview,request:Request):
        authority=store.authorize(request,body.expected_source.model_dump())
        try:result=await asyncio.to_thread(store.preview,authority)
        except MetadataLimitError as error:raise HTTPException(422,str(error)) from None
        except (ValueError,OSError,RuntimeError):raise HTTPException(422,f'링크 복사는 POSIX의 no-follow 파일 작업이 필요합니다. 비공개·순환·변경된 대상은 거절하며, 최대 {store.max_files:,}개 파일·4 GiB·{store.max_links}개 링크·깊이 {store.max_depth}·{store.max_nodes:,}개 메타데이터 노드·{store.max_plan_bytes:,}바이트 메타데이터까지 검토합니다.') from None
        store.authorize(request,body.expected_source.model_dump(),authority);return reply(result)

    @router.post('/materialize')
    async def materialize(body:Materialize,request:Request):
        authority=store.authorize(request);row=store.take(authority,body.plan_id,body.plan_sha256);plan=row['plan'];stop=threading.Event()
        async def watch():
            while True:
                if await request.is_disconnected():stop.set();return
                await asyncio.sleep(.1)
        watcher=asyncio.create_task(watch())
        try:
            store.authorize(request,plan['expected_source'],authority)
            await asyncio.to_thread(store.unchanged,authority,row)
            args={key:plan[key] for key in ('source','project_dir','targets','plan_sha256','file_count','total_bytes')}
            args.update(project_id=authority['project_id'],workspace_id=authority['workspace_id'])
            approved=await controller.client.call(authority,'host.approveLinkedSource',args)
            current=store.authorize(request,plan['expected_source'],authority)
            if stop.is_set():raise ValueError('Request cancelled')
            if not isinstance(approved,dict) or approved.get('approved') is not True:return reply({'cancelled':True})
            if approved.get('plan_sha256')!=plan['plan_sha256']:raise ValueError('Native review changed')
            await asyncio.to_thread(store.unchanged,current,row)
            result=await asyncio.to_thread(store.materialize,request,authority,row,stop)
            store.authorize(request,plan['expected_source'],authority);return reply(result)
        except HTTPException:raise
        except (ValueError,OSError,RuntimeError,TimeoutError):raise HTTPException(409,'Linked source or authority changed; no copy was selected. Review again') from None
        finally:
            stop.set();watcher.cancel();await asyncio.gather(watcher,return_exceptions=True)

    @router.post('/use-check')
    async def use_check(body:UseCheck,request:Request):
        authority=store.authorize(request)
        try:result=await asyncio.to_thread(store.use_check,request,authority,body.snapshot_id,body.manifest_sha256)
        except (ValueError,OSError,RuntimeError):raise HTTPException(409,'Linked-source copy or registered source changed; review again') from None
        store.authorize(request,previous=authority);return reply(result)
    return router
