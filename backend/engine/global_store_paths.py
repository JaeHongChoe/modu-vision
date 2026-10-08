"""Opt-in owned offline generation paths; ordinary installations retain defaults."""
from contextlib import contextmanager
from contextvars import ContextVar
import json,os
import hashlib
from pathlib import Path
from backend.engine.migration_guard import maintenance_guard,exclusive_admitted,_admission_capability

OWNER_FILE='.global-migration-owner.json'
POINTER_FILE='global-active.json'
SUPPORTED_SCOPES={'ledger':'jobs/ledger.sqlite3','leases':'resource_leases.sqlite3','profiles':'compute_profiles.json',
                  'accounts':'auth/accounts.sqlite','context':'projects/.context.sqlite3',
                  'local_journals':'local_jobs','remote_journals':'remote_jobs'}

def _hex(value,size):return isinstance(value,str) and len(value)==size and all(c in '0123456789abcdef' for c in value)

def validate_owner(data):
    if not isinstance(data,dict) or data.get('schema_version')!=1 or not _hex(data.get('installation_id'),32) or data.get('scopes')!=SUPPORTED_SCOPES:
        raise ValueError('Unsupported owned global descriptor; exact declared current scope paths are required')

_STAGED=ContextVar('owned_global_staging',default=None)


def owned_root(path):
    path=Path(path).expanduser().absolute()
    for root in (path,*path.parents):
        marker=root/OWNER_FILE
        if marker.is_symlink():raise ValueError('Global maintenance ownership cannot follow links')
        if marker.is_file():
            if any(p.is_symlink() for p in (root,*root.parents,marker)):
                raise ValueError('Global maintenance ownership cannot follow links')
            data=json.loads(marker.read_bytes());validate_owner(data);identity=data.get('root_identity')
            st=root.stat()
            if identity!={'path':str(root.resolve()),'device':st.st_dev,'inode':st.st_ino}:
                raise ValueError('Global installation ownership differs from original directory')
            for name in ('.global-generations','.global-migrations','migration_admission.lock',POINTER_FILE,
                         'application-active.json','application-update-pending.json','.application-updates','.application-generations',
                         '.application-writer-epochs', '.application-runtime-homes'):
                if (root/name).is_symlink():raise ValueError('Global control paths cannot follow links')
            return root.resolve(),data
    return None,None


def active_generation(root):
    root=Path(root);pointer=root/POINTER_FILE
    if pointer.is_symlink():raise ValueError('Global active pointer cannot be linked')
    if not pointer.exists():return None
    record=json.loads(pointer.read_bytes());owner=json.loads((root/OWNER_FILE).read_bytes())
    identifier=record.get('generation_id')
    if (record.get('schema_version')!=1 or type(record.get('fence')) is not int or record['fence']<1
            or not _hex(record.get('sealed_sha256'),64)
            or record.get('installation_id')!=owner.get('installation_id') or not isinstance(identifier,str)
            or len(identifier)!=32 or any(c not in '0123456789abcdef' for c in identifier)):
        raise ValueError('Invalid owned global generation pointer')
    generation=root/'.global-generations'/identifier
    if any(p.is_symlink() for p in (generation,generation.parent)) or not generation.is_dir():
        raise ValueError('Global generation is missing or linked')
    seal=generation/'.global-generation.json'
    if seal.is_symlink():raise ValueError('Global generation seal is linked')
    data=json.loads(seal.read_bytes())
    if data.get('schema_version')!=1 or data.get('installation_id')!=record['installation_id'] or data.get('generation_id')!=identifier:
        raise ValueError('Global generation ownership is invalid')
    if data.get('sealed_sha256')!=record.get('sealed_sha256'):raise ValueError('Global generation seal differs from pointer')
    adoption=data.get('live_adoptions')
    if adoption is not None:
        from backend.engine.live_control_migration import validate_adoptions
        validate_adoptions(adoption,record['installation_id'])
        bound=hashlib.sha256(json.dumps(adoption,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        if record.get('live_adoptions_sha256')!=bound:
            raise ValueError('Live control adoption differs from its published pointer')
    elif record.get('live_adoptions_sha256') is not None:
        raise ValueError('Live control adoption seal is missing')
    return generation,record


def resolve_store_path(path):
    path=Path(path).expanduser().absolute();root,owner=owned_root(path)
    if root is None:return path
    relative=path.relative_to(root).as_posix()
    declared=(relative in owner['scopes'].values() or any(path.is_relative_to(root/owner['scopes'][key]) for key in ('local_journals','remote_journals')))
    if declared and any(p.is_symlink() for p in (path,*path.parents) if p==root or p.is_relative_to(root)):
        raise ValueError('Declared global control paths cannot follow links')
    active=active_generation(root)
    if not active:return path
    if not declared:return path
    return active[0]/relative


@contextmanager
def staged_construction(root,generation):
    """Only the original live exclusive admission can use this private scope."""
    scope={'root':str(Path(root).resolve()),'generation':str(Path(generation).resolve()),
           'active':True,'admission':_admission_capability(root,exclusive=True)}
    token=_STAGED.set(scope)
    try:yield
    finally:
        scope['active']=False
        _STAGED.reset(token)


def staged_generation_admitted(root,generation):
    """Copied/expired scopes cannot borrow or revive an exclusive capability."""
    scope=_STAGED.get()
    return (isinstance(scope,dict) and scope['active'] and scope['admission'] is not None
            and scope['root']==str(Path(root).resolve())
            and scope['generation']==str(Path(generation).resolve())
            and _admission_capability(root,exclusive=True) is scope['admission'])


def _assert_current_store(root,owner,path):
    path=Path(path).absolute();relative=path.relative_to(root)
    staged=_STAGED.get()
    if (staged and staged_generation_admitted(root,staged['generation'])
            and path.is_relative_to(Path(staged['generation']))):return
    active=active_generation(root)
    scopes=set(owner['scopes'].values())
    declared=(relative.as_posix() in scopes or any(path.is_relative_to(root/owner['scopes'][key]) for key in ('local_journals','remote_journals')))
    if declared and active:
        raise ValueError('Global generation changed; restart this store before accessing it')
    if relative.parts and relative.parts[0]=='.global-generations':
        if not active or not path.is_relative_to(active[0]):
            raise ValueError('This store belongs to a retired global generation; restart required')


@contextmanager
def store_admission(path,*,exclusive=False):
    root,owner=owned_root(path)
    if root is None:
        yield
    else:
        with maintenance_guard(root,exclusive=exclusive):
            if not exclusive:
                # Recovery owns exclusive admission. Ordinary constructors and
                # writers cannot attach a partially switched app/DB pair.
                if not exclusive_admitted(root) and ((root/'application-update-pending.json').exists() or (root/'application-active.json').exists()):
                    from backend.engine.runtime_update import validate_attachment
                    validate_attachment(root)
                _assert_current_store(root,owner,path)
            yield


def configured_admission_root():
    value=os.environ.get('VISION_AI_STUDIO_USER_DATA_DIR')
    if not value:return None
    root,_=owned_root(Path(value))
    return root


@contextmanager
def startup_admission():
    root=configured_admission_root()
    if root is None:yield
    else:
        with store_admission(root):yield


def validate_startup_scopes(project_dir,shared_auth_dir):
    root=configured_admission_root()
    if root is None:return
    _,owner=owned_root(root);scopes=owner['scopes']
    actual={'context':Path(project_dir).absolute()/'.context.sqlite3',
            'accounts':Path(shared_auth_dir).absolute()/'accounts.sqlite' if shared_auth_dir else None,
            'ledger':root/'jobs/ledger.sqlite3','profiles':root/'compute_profiles.json',
            'leases':Path(os.environ.get('VISION_RESOURCE_LEASE_DB') or root/'resource_leases.sqlite3').absolute()}
    if any(value!=root/scopes[key] for key,value in actual.items()):
        raise ValueError('Owned global startup scopes differ from declared installation; no partial attachment')
    for value in actual.values():
        resolve_store_path(value)
