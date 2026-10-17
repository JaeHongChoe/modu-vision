"""One original shutdown transaction; full bytes precede and follow physical drain.

Metadata here is a custody guard BETWEEN genuine full-byte admissions. It is
never a verification result, reusable cache, release authority or serialized
capability. Full original validators retain their own ordering and worker joins.
"""
import copy
import os
from pathlib import Path
import stat
import threading
import time
import weakref

from backend.engine.application_launch_handshake import HandshakeError, send_frame, validate_backend_exit

PREPARATION_BUDGET_MS = 30000
POSTCHECK_BUDGET_MS = 30000
MAX_CUSTODY_MEMBERS = 41000
_MINTS = set()
_CONTROLLERS = weakref.WeakKeyDictionary()


def _update():
    from backend.engine import runtime_update
    return runtime_update


def _node():
    from backend.engine import application_node_writer_authority
    return application_node_writer_authority


def _stat(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_nlink,
            info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def asset_custody(root, binding):
    """Fresh closed asset namespace; mutable lifecycle files are excluded."""
    u = _update(); root = Path(root)
    application = root/u.GENERATIONS/binding['application_generation']/'application'
    starts = (application, root/u.UPDATES/binding['update_id'], Path(binding['authority_path']))
    values = {}; pending = list(starts); links = None
    while pending:
        path = pending.pop(); u._unlinked(path.parent); info = path.lstat()
        name = str(path)
        if name in values: continue
        if len(values) >= MAX_CUSTODY_MEMBERS:
            raise HandshakeError('Shutdown asset namespace exceeds its reviewed bound')
        if stat.S_ISLNK(info.st_mode):
            # Signed schema2 aliases are leaves only. Parents, updates and
            # authority retain the original strict no-link storage policy.
            try: relative = path.relative_to(application).as_posix()
            except ValueError as error:
                raise HandshakeError('Shutdown alias escaped the original application namespace') from error
            if links is None:
                raw = u._read(application/'portable-application.json', u.MAX_APPLICATION_MANIFEST)
                if u._sha(raw) != binding['application_manifest_sha256']:
                    raise HandshakeError('Shutdown original application manifest binding changed')
                manifest = u._json(raw)
                if manifest.get('schema_version') != 2:
                    raise HandshakeError('Shutdown alias lacks original signed native layout')
                links, _ = u._native_layout(manifest)
            target = os.fsencode(os.readlink(path))
            if info.st_nlink != 1 or relative not in links or target != links[relative].encode('utf-8'):
                raise HandshakeError('Shutdown signed native alias target or custody changed')
            if os.fsencode(os.readlink(path)) != target:
                raise HandshakeError('Shutdown signed native alias changed while reading')
            u._unlinked(path.parent); after = path.lstat()
            if _stat(after) != _stat(info) or after.st_gid != info.st_gid:
                raise HandshakeError('Shutdown signed native alias identity changed while reading')
            values[name] = (_stat(info), info.st_gid, target)
            continue  # Never enumerate or open an alias target.
        path = u._unlinked(path)
        if stat.S_ISREG(info.st_mode):
            if info.st_nlink != 1:
                raise HandshakeError('Shutdown asset has foreign link custody')
        elif stat.S_ISDIR(info.st_mode):
            with os.scandir(path) as entries:
                for entry in entries:
                    if len(pending)+len(values) >= MAX_CUSTODY_MEMBERS:
                        raise HandshakeError('Shutdown asset namespace exceeds its reviewed bound')
                    pending.append(path/entry.name)
        else:
            raise HandshakeError('Shutdown asset is not an original regular file/directory')
        values[name] = _stat(info)
    return tuple(sorted(values.items()))


class ControllerShutdown:
    __slots__ = ('__weakref__',)

    def __init__(self, *, _mint=None):
        if _mint not in _MINTS:
            raise HandshakeError('Original shutdown transaction mint is unavailable')

    def __copy__(self):
        raise HandshakeError('Shutdown transaction cannot be copied')

    def __deepcopy__(self, memo):
        raise HandshakeError('Shutdown transaction cannot be copied')

    def __reduce_ex__(self, protocol):
        raise HandshakeError('Shutdown transaction cannot be serialized')


def _fail(s, message):
    s['failed']=True
    raise HandshakeError(message)


def _state(cap):
    s = _CONTROLLERS.get(cap) if type(cap) is ControllerShutdown else None
    if (s is None or s['pid'] != os.getpid() or s['thread'] is not threading.current_thread()
            or s['owner']._shutdown_transaction is not cap or s['failed']):
        if s is not None:s['failed']=True
        raise HandshakeError('Original shutdown process/thread capability differs or interrupted')
    return s


def _deadline(s):
    name = 'post_deadline' if s['phase'] in {'postchecking', 'verified', 'final'} else (
        'drain_deadline' if s['phase'] == 'draining' else 'preparation_deadline')
    deadline = s[name]
    if deadline is None or time.monotonic() >= deadline:
        raise HandshakeError('Original shutdown phase deadline expired; it cannot renew')
    return deadline


def _controls(s, row):
    owner = s['owner']; u = _update()
    if (owner.root is not s['root_object'] or owner.nonce != s['nonce']
            or owner._process is not s['main'] or owner._bootstrap_channel is not s['channel']
            or owner._writer_epoch is not s['epoch'] or owner._lock is not s['database_lock']
            or _node()._endpoint(owner._bootstrap_channel) != s['endpoint']
            or owner._authenticated_backend_proof != s['proof']
            or _node()._state(s['authority'])['proof'] != s['proof']
            or owner._database_fd != s['database_fd']
            or _stat(os.fstat(s['database_fd'])) != s['database_fd_stat']
            or u._canonical(row) != s['row']):
        raise HandshakeError('Original shutdown handle, ownership OFD or lease publication changed')
    # Original kernel-held database lock and epoch authority remain live; small
    # signed/control documents and full pair pointer/fence are checked each use.
    root, identity = u._root(owner.root); u.validate_attachment(root)
    pointer = u._pointer(root)
    from backend.engine.global_store_paths import active_generation
    current = active_generation(root)
    binding = row['binding']
    if (identity['installation_id'] != binding['installation_id'] or pointer is None
            or pointer['update_id'] != binding['update_id']
            or pointer['application_generation'] != binding['application_generation']
            or pointer['database_pointer'] != binding['database_pointer']
            or current is None or current[1] != binding['database_pointer']
            or str(current[0]) != binding['database_generation_path']):
        raise HandshakeError('Original shutdown installation/database fence changed')
    s['epoch']._original()
    if (u._canonical(s['registry_snapshot']) != s['registry']
            or u._canonical(s['epoch'].snapshot()) != s['registry']):
        raise HandshakeError('Original shutdown writer registry changed outside its single transition')
    if s['phase'] in {'postchecking','verified','final'} and asset_custody(root, binding) != s['assets']:
        # Fresh full-validator refusal has its original signed ZIP/installed
        # error priority. A restored valid pair still cannot adopt lost custody.
        owner._binding(row)
        raise HandshakeError('Original shutdown asset custody changed during transaction')
    owner._live(row, s['main_identity'])


def current_controller(cap, authority, row):
    s = _state(cap)
    try:
        if s['authority'] is not authority:
            raise HandshakeError('Shutdown transaction belongs to another original Node authority')
        _deadline(s); _controls(s, row); _deadline(s)
        return s
    except BaseException:
        s['failed']=True
        raise


def prepare_controller(owner, authority, frame, *, started=None, admission=None):
    started = time.monotonic() if started is None else started
    names = {'schema_version','kind','nonce','epoch','binding_sha256','backend_claim_sha256','request_id','budget_ms'}
    u = _update(); proof = owner._authenticated_backend_proof
    if (type(frame) is not dict or set(frame) != names or type(frame['schema_version']) is not int
            or frame['schema_version'] != 1 or frame['kind'] != 'main_shutdown_prepare'
            or frame['nonce'] != owner.nonce or frame['epoch'] != proof['epoch']
            or frame['binding_sha256'] != proof['binding_sha256']
            or frame['backend_claim_sha256'] != u._sha(u._canonical(proof))
            or not u._hex(frame['request_id'],32) or type(frame['budget_ms']) is not int
            or not 0 < frame['budget_ms'] <= PREPARATION_BUDGET_MS
            or getattr(owner,'_shutdown_transaction',None) is not None):
        raise HandshakeError('Original shutdown preparation differs or replayed')
    state,row = (_node()._fresh(authority) if admission is None else
                 _node().consume_shutdown_preparation(admission,authority,owner,frame))
    if (state['phase'] != 'bound' or state['deadline'] is not None
            or row['writer_drain']['phase'] != 'enrolled' or owner._lock is None
            or type(owner._database_fd) is not int):
        raise HandshakeError('Shutdown preparation lacks original ready ownership')
    # _fresh just completed original whole signed bundle + installed pair.
    deadline = started+frame['budget_ms']/1000
    if time.monotonic() >= deadline:
        raise HandshakeError('Shutdown full-byte preparation completed late')
    mint = object(); _MINTS.add(mint)
    try: cap = ControllerShutdown(_mint=mint)
    finally: _MINTS.discard(mint)
    snap = owner._writer_epoch.snapshot()
    _CONTROLLERS[cap] = {'owner':owner,'authority':authority,'pid':os.getpid(),'thread':threading.current_thread(),
        'root_object':owner.root,'nonce':owner.nonce,'main':owner._process,'main_identity':copy.deepcopy(row['process']),
        'channel':owner._bootstrap_channel,'endpoint':_node()._endpoint(owner._bootstrap_channel),'epoch':owner._writer_epoch,'database_lock':owner._lock,
        'database_fd':owner._database_fd,'database_fd_stat':_stat(os.fstat(owner._database_fd)),
        'row':u._canonical(row),'registry':u._canonical(snap),'registry_snapshot':copy.deepcopy(snap),'assets':asset_custody(owner.root,row['binding']),
        'request_id':frame['request_id'],'proof':copy.deepcopy(proof),'phase':'preparing','failed':False,
        'preparation_deadline':deadline,'drain_deadline':None,'post_deadline':None,'receipt':None,
        'exit_event':None,'exit_frame':None,'post_verified':False}
    owner._shutdown_transaction = cap
    current_controller(cap,authority,owner._owned())
    return cap


def prepared_controller(cap, frame):
    s = _state(cap); current_controller(cap,s['authority'],s['owner']._owned())
    expected = {'schema_version':1,'kind':'main_shutdown_prepared','nonce':s['nonce'],
        'request_id':s['request_id'],'backend_claim_sha256':_update()._sha(_update()._canonical(s['proof']))}
    if s['phase'] != 'preparing' or frame != expected:
        _fail(s,'Original backend shutdown preparation differs or replayed')
    if asset_custody(s['owner'].root,s['owner']._owned()['binding'])!=s['assets']:
        s['owner']._binding(s['owner']._owned())
        _fail(s,'Original preparation asset custody changed before commit')
    s['phase'] = 'prepared'


def begin_controller_drain(cap, frame, started):
    s = _state(cap); current_controller(cap,s['authority'],s['owner']._owned())
    if (s['phase'] != 'prepared' or frame.get('request_id') != s['request_id']
            or type(frame.get('budget_ms')) is not int or not 0 < frame['budget_ms'] <= 4000):
        _fail(s,'Prepared shutdown commit differs or attempts renewed drain')
    s['drain_deadline'] = started+frame['budget_ms']/1000; s['phase'] = 'draining'
    _deadline(s)


def close_controller_epoch(cap):
    s = _state(cap); owner = s['owner']; current_controller(cap,s['authority'],owner._owned())
    before = owner._writer_epoch.snapshot()
    if s['phase'] != 'draining' or before['registry']['state'] != 'open':
        _fail(s,'Original shutdown epoch close replayed')
    owner._writer_epoch.close_epoch(expected_registry_sha256=before['registry_sha256'])
    after = owner._writer_epoch.snapshot()
    expected = copy.deepcopy(before['registry']);expected.update(state='closed',revision=expected['revision']+1)
    if after['registry'] != expected:
        raise HandshakeError('Original shutdown epoch close changed writer custody')
    s['registry'] = _update()._canonical(after)
    s['registry_snapshot'] = copy.deepcopy(after)
    current_controller(cap,s['authority'],owner._owned())
    return after


def publish_controller(cap, *, phase, request=None, receipt=None, backend_exit=None):
    s = _state(cap); owner = s['owner']; row = owner._owned(); current_controller(cap,s['authority'],row)
    permitted = (phase == 'closing' and s['phase'] == 'draining' and row['writer_drain']['phase'] == 'enrolled'
                 or phase in {'drained','refused'} and s['post_verified'] and row['writer_drain']['phase'] == 'closing'
                 or phase == 'backend_exited' and s['post_verified'] and row['writer_drain']['phase'] == 'drained')
    if not permitted: _fail(s,'Shutdown publication is premature or replayed')
    changes = {**row['writer_drain'],'phase':phase}
    for key,value in (('request',request),('receipt',receipt),('backend_exit',backend_exit)):
        if value is not None:
            if changes[key] is not None:raise HandshakeError('Shutdown evidence publication replayed')
            changes[key]=value
    expected = {**row,'revision':row['revision']+1,'writer_drain':changes}
    # Called under caller's original transition mutex; exact CAS remains inside
    # _persist. No socket wait or whole-byte verifier is held under this mutex.
    result = owner._persist(row,writer_drain=changes)
    actual = owner._owned()
    if actual != expected:raise HandshakeError('Original shutdown publication changed unexpectedly')
    s['row'] = _update()._canonical(actual)
    current_controller(cap,s['authority'],actual)
    return result


def provisional_receipt(cap, receipt):
    s = _state(cap);current_controller(cap,s['authority'],s['owner']._owned())
    if s['phase'] != 'draining' or s['receipt'] is not None:
        _fail(s,'Original shutdown provisional receipt replayed')
    s['receipt']=copy.deepcopy(receipt)


def capture_controller_exit(cap, event):
    s = _state(cap);node = _node();current_controller(cap,s['authority'],s['owner']._owned())
    if s['phase'] != 'draining' or s['receipt'] is None or s['exit_event'] is not None:
        _fail(s,'Original shutdown exit witness is premature or replayed')
    frame = node.frame_value(s['authority'],event,shutdown=cap)
    validate_backend_exit(frame,s['receipt']['request'],s['proof']['process'])
    received = node._event(s['authority'],event,shutdown=cap)
    received['shutdown_reserved'] = cap
    s['exit_event']=event;s['exit_frame']=copy.deepcopy(frame)
    # Exact original typed event arrived before the original physical deadline.
    # The independent postvalidation phase begins once; physical clock unchanged.
    _deadline(s);s['post_deadline']=time.monotonic()+POSTCHECK_BUDGET_MS/1000;s['phase']='postchecking'


def finalize_controller(cap):
    s = _state(cap);owner=s['owner'];node=_node();u=_update()
    if s['phase'] != 'postchecking' or s['exit_event'] is None:
        _fail(s,'Original shutdown cannot finalize without its exit witness')
    # Full validator FIRST: signed raw bundle -> ZIP -> installed bytes, original
    # fault priority and complete worker joins. No early cached success allowed.
    row=owner._owned();owner._binding(row)
    current_controller(cap,s['authority'],owner._owned())
    s['post_verified']=True;s['phase']='verified'
    receipt=s['receipt'];frame=s['exit_frame']
    from backend.engine import application_launch_lease as lease
    with lease._transition_admission(owner.root,owner.nonce):
        publish_controller(cap,phase='drained' if receipt['status']=='managed_scopes_drained' else 'refused',receipt=receipt)
    if receipt['status']=='managed_scopes_drained':
        # Original registry receives only the held typed Node witness after the
        # full-byte postcheck. This does not prove descendants or release leases.
        owner._writer_epoch.observe_authenticated_node_backend_exit(owner._owned()['writer_drain']['writer_id'],s['exit_event'],
            expected_registry_sha256=owner._writer_epoch.snapshot()['registry_sha256'])
        # finish_exit_publication already accepted only the exact planned
        # registry transition. Never adopt a new post-callback snapshot here.
        with lease._transition_admission(owner.root,owner.nonce):publish_controller(cap,phase='backend_exited',backend_exit=frame)
    else:
        received=node._event(s['authority'],s['exit_event'],shutdown=cap);received['used']=True
    current_controller(cap,s['authority'],owner._owned())
    final={'schema_version':1,'kind':'shutdown_validation_final','nonce':s['nonce'],'request_id':s['request_id'],
        'receipt_sha256':u._sha(u._canonical(receipt)),'exit_sha256':u._sha(u._canonical(frame)),
        'status':receipt['status'],'whole_writer_coverage':False,'process_tree_exit_verified':False,'can_release_launch_lease':False}
    if receipt['status'] == 'managed_scopes_drained':
        from backend.engine.application_store_writer_closure import prepare_original_controller
        prepare_original_controller(owner, cap)  # Provisional until this exact send succeeds.
    send_frame(s['channel'],final,absolute_deadline=_deadline(s));_deadline(s)
    s['phase']='final'
    return final


def original_exit_postdeadline(cap, authority, event, epoch, writer_id):
    s=_state(cap);current_controller(cap,authority,s['owner']._owned())
    if (not s['post_verified'] or s['phase']!='verified' or s['exit_event'] is not event
            or s['epoch'] is not epoch or s['owner']._owned()['writer_drain']['writer_id']!=writer_id):
        raise HandshakeError('Original verified shutdown exit gateway differs')
    return s['post_deadline']


def accept_original_registry_exit(cap, authority):
    """Only the exact predeclared active->direct_exited original publication."""
    s=_state(cap);u=_update()
    if s['authority'] is not authority or not s['post_verified'] or s['phase']!='verified':
        raise HandshakeError('Original shutdown registry exit publication is unavailable')
    if u._canonical(s['registry_snapshot'])!=s['registry']:
        raise HandshakeError('Original shutdown retained registry snapshot changed')
    before=s['registry_snapshot'];expected=copy.deepcopy(before['registry'])
    writer_id=s['owner']._owned()['writer_drain']['writer_id']
    rows=[row for row in expected['writers'] if row['writer_id']==writer_id]
    if len(rows)!=1 or rows[0]['status']!='active':raise HandshakeError('Original shutdown active backend row differs')
    rows[0].update(status='direct_exited',exit_code=0);expected['revision']+=1
    actual=s['epoch'].snapshot()
    from backend.engine.runtime_process_control import private_json_bytes
    if actual!={'registry':expected,'registry_sha256':u._sha(private_json_bytes(expected))}:
        raise HandshakeError('Original shutdown registry changed outside exact exit transition')
    s['registry']=u._canonical(actual)
    s['registry_snapshot']=copy.deepcopy(actual)
    current_controller(cap,authority,s['owner']._owned())
