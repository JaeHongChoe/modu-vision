"""Host-bound field credentials; project databases contain references only.

POSIX servers use private owned files outside project/export trees. Windows
uses current-user DPAPI with no machine-wide scope or interactive prompt:
https://learn.microsoft.com/en-us/windows/win32/api/dpapi/nf-dpapi-cryptprotectdata
Native Windows qualification is separate from source/control verification.
"""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import uuid

PREFIX='server-secret:v1:'
POLICY_VERSION=1
MAX_BYTES=16384


def validate_token(token):
    if not isinstance(token,str) or not 1<=len(token)<=4096 or any(not 33<=ord(c)<=126 for c in token):
        raise ValueError('Agent token must be bounded printable ASCII without whitespace')
    return token


def _dpapi(data: bytes, scope: str, *, decrypt: bool) -> bytes:
    import ctypes
    from ctypes import wintypes
    class Blob(ctypes.Structure):
        _fields_=[('cbData',wintypes.DWORD),('pbData',ctypes.POINTER(ctypes.c_ubyte))]
    buffer=ctypes.create_string_buffer(data)
    entropy=ctypes.create_string_buffer(hashlib.sha256(scope.encode()).digest())
    source=Blob(len(data),ctypes.cast(buffer,ctypes.POINTER(ctypes.c_ubyte)))
    salt=Blob(32,ctypes.cast(entropy,ctypes.POINTER(ctypes.c_ubyte)));output=Blob()
    try:
        library=ctypes.WinDLL('crypt32',use_last_error=True)
        function=library.CryptUnprotectData if decrypt else library.CryptProtectData
        function.argtypes=[ctypes.POINTER(Blob),ctypes.c_void_p,ctypes.POINTER(Blob),
                           ctypes.c_void_p,ctypes.c_void_p,wintypes.DWORD,ctypes.POINTER(Blob)]
        function.restype=wintypes.BOOL
        if not function(ctypes.byref(source),None,ctypes.byref(salt),None,None,1,ctypes.byref(output)):
            raise ValueError('Server credential protection is unavailable for this Windows user')
        if not output.pbData or not 0<output.cbData<=MAX_BYTES:
            raise ValueError('Server credential protection returned invalid bytes')
        return ctypes.string_at(output.pbData,output.cbData)
    finally:
        if output.pbData:
            ctypes.memset(output.pbData,0,output.cbData)
            kernel=ctypes.WinDLL('kernel32',use_last_error=True)
            kernel.LocalFree.argtypes=[ctypes.c_void_p];kernel.LocalFree.restype=ctypes.c_void_p
            kernel.LocalFree(ctypes.cast(output.pbData,ctypes.c_void_p))
        ctypes.memset(buffer,0,len(buffer));ctypes.memset(entropy,0,len(entropy))


def _unlinked(path):
    if any(p.is_symlink() for p in (path,*path.parents)):raise ValueError('Server secret storage is linked')


def _directory(path):
    _unlinked(path)
    path.mkdir(mode=0o700,parents=True,exist_ok=True)
    value=path.stat()
    if not stat.S_ISDIR(value.st_mode) or os.name!='nt' and (value.st_uid!=os.geteuid() or value.st_mode&0o077):
        raise ValueError('Server secret storage is not private to this owner')


class ServerSecretStore:
    def __init__(self,project):
        from backend.engine.onboarding import user_data_dir
        base=user_data_dir().absolute()/'fleet_secrets'
        _directory(base)
        self.scope=hashlib.sha256((str(Path(project).resolve())+'\0'+str(base)).encode()).hexdigest()
        self.root=base/self.scope;_directory(self.root)

    def save(self,target_id,token):
        validate_token(token)
        reference=PREFIX+uuid.uuid4().hex
        payload=json.dumps({'schema_version':1,'scope_sha256':self.scope,'target_id':target_id,'token':token},separators=(',',':')).encode()
        if os.name=='nt':payload=b'DPAPI1\0'+_dpapi(payload,self.scope,decrypt=False)
        file=self.root/(reference[len(PREFIX):]+'.secret');_unlinked(file)
        descriptor=os.open(file,os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,'O_NOFOLLOW',0),0o600)
        with os.fdopen(descriptor,'wb') as stream:
            stream.write(payload);stream.flush();os.fsync(stream.fileno())
        # Verify the newly retained bytes before allowing a database reference.
        if self.read(target_id,reference)!=token:raise ValueError('Server secret readback differs')
        return reference

    def read(self,target_id,reference):
        suffix=reference.removeprefix(PREFIX)
        if not reference.startswith(PREFIX) or not re.fullmatch('[0-9a-f]{32}',suffix):raise ValueError('Invalid server secret reference')
        _directory(self.root)
        file=self.root/(suffix+'.secret');_unlinked(file)
        try:
            descriptor=os.open(file,os.O_RDONLY|getattr(os,'O_NOFOLLOW',0)|getattr(os,'O_NONBLOCK',0))
        except OSError as exc:raise ValueError('Agent credentials are unavailable on this server scope') from exc
        with os.fdopen(descriptor,'rb') as stream:
            before=os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode) or before.st_nlink!=1 or before.st_size>MAX_BYTES or os.name!='nt' and (before.st_uid!=os.geteuid() or before.st_mode&0o077):
                raise ValueError('Server secret file is not private to this owner')
            payload=stream.read(MAX_BYTES+1);after=os.fstat(stream.fileno())
        if (before.st_ino,before.st_size,before.st_mtime_ns)!=(after.st_ino,after.st_size,after.st_mtime_ns):raise ValueError('Server secret changed during readback')
        if os.name=='nt':
            if not payload.startswith(b'DPAPI1\0'):raise ValueError('Unprotected Windows server secret is refused')
            payload=_dpapi(payload[7:],self.scope,decrypt=True)
        try:value=json.loads(payload)
        except (ValueError,UnicodeError) as exc:raise ValueError('Server secret has invalid content') from exc
        if not isinstance(value,dict) or set(value)!={'schema_version','scope_sha256','target_id','token'} or value['schema_version']!=1 or value['scope_sha256']!=self.scope or value['target_id']!=target_id:
            raise ValueError('Server secret identity differs from this project/target scope')
        return validate_token(value['token'])
