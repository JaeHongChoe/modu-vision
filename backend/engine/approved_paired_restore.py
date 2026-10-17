"""Explicit offline approved inverse of one exact ordinary app/database upgrade.

Only its retained prior signed app and immutable backup in the exact current
schema are supported. New generations and a fresh original canary are required.
No old pointer, session, receipt, process or general downgrade is authorized.
"""
from dataclasses import dataclass
from pathlib import Path
import shutil
import uuid

from backend.engine import runtime_update as update, global_migration as migration
from backend.engine.application_launch_lease import assert_quiescent
from backend.engine.global_store_paths import active_generation, staged_construction, store_admission

POLICY='prior_signed_application_and_immutable_backup_current_schema_revoke_sessions_v1'
KIND='approved_paired_restore'
APPROVAL_LIMIT=256*1024


@dataclass(frozen=True)
class ApprovedPairedRestorePlan:
    root:str
    approval_path:str
    approval_sha256:str
    document:bytes


def _pointer(record):
    return {'schema_version':1,'installation_id':record['installation_id'],
        'update_id':record['update_id'],'application_generation':record['application_generation'],
        'database_pointer':record['database_pointer']}


def _basis(root,application,canary):
    """Recompute the saved original pair; never select an arbitrary old target."""
    from backend.engine.staged_update_canary import validate_spec,review_spec,review_candidate,validate_ready_review
    root,owner=update._root(root)
    update._fields(application,{'schema_version','installation_id','update_id','application_generation','database_pointer'})
    current,_=update._intent(root,application['update_id'])
    if(current.get('kind')is not None or current['status']!='committed'
            or current.get('recovery_history')!=[] or application!=_pointer(current)):
        raise update.UpdateError('Approved inverse requires the exact ordinary committed current pair')
    current,current_dir,current_manifest=update._validated_intent(root,application['update_id'])
    prior_application=current['previous_application'];prior_database=current['previous_database']
    if(not isinstance(prior_application,dict)or not isinstance(prior_database,dict)
            or prior_application.get('update_id')==current['update_id']):
        raise update.UpdateError('Approved inverse has no distinct previous application/database pair')
    prior,_=update._intent(root,prior_application['update_id'])
    if(prior.get('kind')is not None or prior['status']!='committed' or prior.get('recovery_history')!=[]
            or prior_application!=_pointer(prior)or prior_database!=prior['database_pointer']
            or prior_application['database_pointer']!=prior_database):
        raise update.UpdateError('Approved inverse previous application/database pair changed')
    prior,prior_dir,prior_manifest=update._validated_intent(root,prior_application['update_id'])
    if any(record['schema_version']!=2 for record in(current,prior)):
        raise update.UpdateError('Approved inverse supports only reviewed schema-2 application intents')
    if(current['authority_path']!=prior['authority_path']or current['authority_sha256']!=prior['authority_sha256']
            or any(current['target'][n]!=prior['target'][n]for n in('platform','arch','channel','origin'))
            or current['release']['compatibility']!=prior['release']['compatibility']):
        raise update.UpdateError('Approved inverse signed release compatibility or publisher pair differs')
    current_database=current['database_pointer']
    for pointer in(current_database,prior_database):
        update._fields(pointer,{'schema_version','installation_id','generation_id','sealed_sha256','fence'})
        if(pointer['schema_version']!=1 or pointer['installation_id']!=owner['installation_id']
                or not update._hex(pointer['generation_id'],32)or not update._hex(pointer['sealed_sha256'])
                or type(pointer['fence'])is not int or pointer['fence']<1):
            raise update.UpdateError('Approved inverse database pointer is not the supported original pair')
    journal_path,journal=migration._journal(root,current['migration_id'])
    if(journal.get('kind')!='forward'or journal.get('status')!='applied'
            or journal.get('application_update_id')!=current['update_id']
            or journal.get('previous_pointer')!=prior_database or journal.get('source_sha256')!=current['source_sha256']
            or current_database['generation_id']!=current['migration_id']
            or current_database['fence']!=prior_database['fence']+1
            or journal.get('fence')!=current_database['fence']
            or journal.get('target_sha256')!=current_database['sealed_sha256']):
        raise update.UpdateError('Approved inverse current migration does not bind the previous pair')
    generation=update._unlinked(root/'.global-generations'/current_database['generation_id'])
    prior_generation=update._unlinked(root/'.global-generations'/prior_database['generation_id'])
    before=migration._forward_view(root,owner,generation,current_database)
    previous=migration._forward_view(root,owner,prior_generation,prior_database)
    if(not before['can_apply']or not previous['can_apply']
            or migration.digest(before['inventory'])!=journal['target_sha256']
            or previous['source_sha256']!=current['source_sha256']):
        raise update.UpdateError('Approved inverse source or current database has later writes or undrained authority')
    backup=update._unlinked(journal_path.parent/'original');backup_view=migration._snapshot(backup)
    if(not backup_view['can_apply']or backup_view['inventory']!=journal['backup_inventory']
            or previous['inventory']!=journal['backup_inventory']):
        raise update.UpdateError('Approved inverse original backup or previous source integrity changed')
    known=migration._known_schemas(owner['scopes'])
    for name,expected in known.items():
        if migration._schema(backup/owner['scopes'][name])!=expected:
            raise update.UpdateError('Approved inverse requires the exact current control schema')
    spec=validate_spec(canary)
    if spec is None:raise update.UpdateError('Approved inverse requires fresh preactivation pins')
    capability=review_spec(root,spec)
    installer=next(row for row in prior['release']['artifacts']if row['kind']=='installer')
    preflight=review_candidate(root,prior_manifest,spec,capability,archive=prior_dir/'bundle'/installer['path'])
    if(not isinstance(preflight,dict)or preflight.get('supported')is not True
            or(preflight.get('protocol'),preflight.get('status'),preflight.get('policy'))not in
            ((1,'source_ready','same_reviewed_source_runtime_worker_v1'),(2,'frozen_ready','same_reviewed_frozen_runtime_worker_v1'))):
        raise update.UpdateError('Approved inverse requires a newly reviewed supported preactivation candidate')
    validate_ready_review(preflight,spec,capability)
    def item(record,directory,manifest,pointer):
        return {'application_pointer':pointer,'database_pointer':record['database_pointer'],
            'journal_sha256':update._sha(update._read(directory/'journal.json',1024**2)),
            'envelope_sha256':record['envelope_sha256'],'authority_sha256':record['authority_sha256'],
            'release_sha256':update._sha(update._canonical(record['release'])),
            'manifest_sha256':update._sha(update._canonical(manifest)),'artifacts':record['release']['artifacts']}
    return {'schema_version':1,'kind':'approved_paired_restore_conversion','policy':POLICY,
        'installation_id':owner['installation_id'],'current':item(current,current_dir,current_manifest,application),
        'previous':item(prior,prior_dir,prior_manifest,prior_application),'source_sha256':before['source_sha256'],
        'backup':{'migration_id':current['migration_id'],'inventory':backup_view['inventory'],
            'inventory_sha256':migration.digest(backup_view['inventory'])},
        'current_schema_sha256':migration.digest(known),'canary':spec,
        'canary_capability_sha256':capability,'canary_preflight':preflight}


def review(root,*,canary):
    """Return a proposal, never approval or mutation of either active pointer."""
    root,_=update._root(root)
    with store_admission(root,exclusive=True):
        assert_quiescent(root);update.validate_attachment(root)
        if(root/update.PENDING).exists():raise update.UpdateError('Approved inverse cannot supersede pending recovery')
        current=update._pointer(root)
        if current is None:raise update.UpdateError('Approved inverse requires a current and previous pair')
        return update._json(update._canonical(_basis(root,current,canary)))


def plan(root,approval_path,*,pinned_approval_sha256):
    """Require the separately supplied canonical approval hash and fresh CAS."""
    root,_=update._root(root);path=update._unlinked(Path(approval_path).absolute());raw=update._read(path,APPROVAL_LIMIT)
    if not update._hex(pinned_approval_sha256)or update._sha(raw)!=pinned_approval_sha256:
        raise update.UpdateError('Explicit approved conversion hash differs')
    document=update._json(raw)
    if update._canonical(document)!=raw:raise update.UpdateError('Approved conversion must use exact canonical JSON')
    if(not isinstance(document,dict)or document.get('schema_version')!=1
            or document.get('kind')!='approved_paired_restore_conversion'or document.get('policy')!=POLICY):
        raise update.UpdateError('Unsupported approved conversion')
    if document!=review(root,canary=document.get('canary')):
        raise update.UpdateError('Approved conversion source, backup or application pair changed')
    return ApprovedPairedRestorePlan(str(root),str(path),pinned_approval_sha256,raw)


def validate_intent(root,record):
    """Additional exact new-kind guard inside the original intent validator."""
    approval=record.get('approved_conversion')
    if(record.get('kind')!=KIND or type(approval)is not dict or set(approval)!={'sha256','document'}
            or not update._hex(approval['sha256'])or update._sha(update._canonical(approval['document']))!=approval['sha256']):
        raise update.UpdateError('Approved inverse intent has no exact reviewed conversion hash')
    document=approval['document']
    if not isinstance(document,dict)or not isinstance(document.get('current'),dict):
        raise update.UpdateError('Approved inverse conversion pair is missing')
    if document!=_basis(root,document['current']['application_pointer'],record.get('canary')):
        raise update.UpdateError('Approved inverse retained source or backup changed')
    previous=document['current']['application_pointer'];prior_id=document['previous']['application_pointer']['update_id']
    prior,_,_=update._validated_intent(root,prior_id)
    if(record['schema_version']!=2 or record['update_id']in(previous['update_id'],prior_id)
            or record.get('recovery_history')!=[] or record['previous_application']!=previous
            or record['previous_database']!=document['current']['database_pointer']
            or record['source_sha256']!=document['source_sha256']
            or any(record[k]!=prior[k]for k in('release','target','authority_path','authority_sha256','envelope_sha256'))
            or record['canary_capability_sha256']!=document['canary_capability_sha256']
            or record.get('canary_execution_protocol')!=document['canary_preflight']['protocol']
            or record.get('canary_worker_binding')!=document['canary_preflight'].get('worker_binding')):
        raise update.UpdateError('Approved inverse new app intent differs from the reviewed pair')
    if record['migration_id']is not None:
        _,journal=migration._journal(root,record['migration_id'])
        if(journal.get('kind')!='paired_restore'or journal.get('application_update_id')!=record['update_id']
                or journal.get('approved_conversion_sha256')!=approval['sha256']
                or journal.get('restore_source_migration_id')!=document['backup']['migration_id']
                or journal.get('restore_source_inventory_sha256')!=document['backup']['inventory_sha256']
                or journal.get('fence')!=record['previous_database']['fence']+1
                or migration.digest(journal.get('backup_inventory'))!=record['previous_database']['sealed_sha256']):
            raise update.UpdateError('Approved inverse database conversion or fresh fence differs')


def validate_database(root,journal):
    """Standalone migration recovery cannot invent an approved app owner."""
    identifier=journal.get('application_update_id')
    if not update._hex(identifier,32):raise migration.GlobalMigrationError('Paired restore requires its approved app intent')
    record,_=update._intent(root,identifier)
    if record.get('kind')!=KIND or record.get('migration_id')!=journal.get('migration_id'):
        raise migration.GlobalMigrationError('Paired restore is not bound to its approved app intent')
    validate_intent(root,record)
    if migration._journal(root,record['migration_id'])[1]!=journal:
        raise migration.GlobalMigrationError('Paired restore journal changed during recovery')


def _prepare_database(root,owner,record,document):
    current=active_generation(root)
    if current is None or current[1]!=record['previous_database']:
        raise update.UpdateError('Approved inverse current database pointer changed')
    before=migration._forward_view(root,owner,*current)
    source=update._unlinked(root/'.global-migrations'/document['backup']['migration_id']/'original')
    original=migration._snapshot(source)
    if original['inventory']!=document['backup']['inventory']or before['source_sha256']!=document['source_sha256']:
        raise update.UpdateError('Approved inverse source or immutable backup changed before staging')
    identifier=uuid.uuid4().hex;directory=update._unlinked(root/'.global-migrations'/identifier)
    directory.mkdir(mode=0o700,parents=True,exist_ok=False);backup=directory/'original'
    migration._copy(before,current[0],backup,scopes=owner['scopes'])
    staged=update._unlinked(root/'.global-generations'/identifier)
    migration._copy(original,source,staged,scopes=owner['scopes']);known=migration._known_schemas(owner['scopes'])
    if migration.digest(known)!=document['current_schema_sha256']:
        raise update.UpdateError('Approved inverse current schema conversion changed')
    for name,expected in known.items():
        if migration._schema(staged/owner['scopes'][name])!=expected:
            raise update.UpdateError('Approved inverse staged schema is not the exact reviewed schema')
    with staged_construction(root,staged):migration._construct(staged,owner['scopes']);migration._revoke(staged,owner['scopes'])
    seal=migration._seal(staged,owner,identifier)
    if(update._pointer(root)!=record['previous_application']or active_generation(root)[1]!=current[1]
            or _basis(root,record['previous_application'],record['canary'])!=document):
        raise update.UpdateError('Approved inverse source or pair changed during staging')
    journal={'schema_version':1,'kind':'paired_restore','installation_id':owner['installation_id'],
        'migration_id':identifier,'status':'prepared','source_sha256':before['source_sha256'],
        'source_inventory':before['inventory'],'target_sha256':seal['sealed_sha256'],
        'backup_inventory':migration._snapshot(backup)['inventory'],'previous_pointer':current[1],
        'fence':current[1]['fence']+1,'scopes':owner['scopes'],'application_update_id':record['update_id'],
        'approved_conversion_sha256':record['approved_conversion']['sha256'],
        'restore_source_migration_id':document['backup']['migration_id'],
        'restore_source_inventory_sha256':document['backup']['inventory_sha256']}
    migration.atomic_private_json(directory/'journal.json',journal);migration._sync_directories(directory)
    return journal


def advance(root,*,expected_source_sha256,on_prepared=None,application_update_id=None):
    """The original finish callback must verify this fresh prepared conversion."""
    root,owner=update._root(root)
    with store_admission(root,exclusive=True):
        assert_quiescent(root)
        record,_=update._intent(root,application_update_id)
        validate_intent(root,record)
        if(record['migration_id']is not None or expected_source_sha256!=record['source_sha256']
                or record['status']!='staged'):
            raise update.UpdateError('Approved inverse has no fresh exact database preparation')
        journal=_prepare_database(root,owner,record,record['approved_conversion']['document'])
        if on_prepared is not None:on_prepared(dict(journal))
        staged=root/'.global-generations'/journal['migration_id']
        seal=update._json(update._read(staged/'.global-generation.json',1024**2))
        migration._publish(root,owner,journal['migration_id'],seal,fence=journal['fence'])
        journal['status']='applied'
        migration.atomic_private_json(root/'.global-migrations'/journal['migration_id']/'journal.json',journal)
        return {'status':'applied','migration_id':journal['migration_id'],'fence':journal['fence'],
            'kind':'paired_restore','session_policy':'copied sessions and OIDC pending revoked'}


def install(root,proposal):
    root,owner=update._root(root)
    if type(proposal)is not ApprovedPairedRestorePlan or proposal.root!=str(root):
        raise update.UpdateError('Approved inverse plan belongs to another installation')
    with store_admission(root,exclusive=True):
        assert_quiescent(root);update.validate_attachment(root)
        fresh=plan(root,proposal.approval_path,pinned_approval_sha256=proposal.approval_sha256)
        if fresh!=proposal:raise update.UpdateError('Approved inverse source changed since plan')
        document=update._json(proposal.document);prior_id=document['previous']['application_pointer']['update_id']
        prior,prior_dir,_=update._validated_intent(root,prior_id)
        total=sum(row['size']for row in prior['release']['artifacts'])
        if shutil.disk_usage(root).free<total*3+64*1024**2:raise update.UpdateError('Insufficient approved inverse staging reserve')
        identifier=uuid.uuid4().hex;directory=update._unlinked(root/update.UPDATES/identifier)
        directory.mkdir(mode=0o700,parents=True,exist_ok=False)
        update._bundle(prior_dir/'bundle',prior['release'],destination=directory/'bundle')
        update._write_raw(directory/'envelope.json',update._read(prior_dir/'envelope.json'))
        generation=update._unlinked(root/update.GENERATIONS/identifier);generation.mkdir(mode=0o700,parents=True,exist_ok=False)
        installer=next(row for row in prior['release']['artifacts']if row['kind']=='installer')
        update._portable(directory/'bundle'/installer['path'],prior['release'],destination=generation/'application')
        migration._sync_directories(directory);migration._sync_directories(generation)
        migration._sync_directories(root/update.UPDATES,recursive=False);migration._sync_directories(root/update.GENERATIONS,recursive=False)
        record={'schema_version':2,'kind':KIND,'installation_id':owner['installation_id'],
            'update_id':identifier,'application_generation':identifier,'status':'staged',
            'authority_path':prior['authority_path'],'authority_sha256':prior['authority_sha256'],
            'envelope_sha256':prior['envelope_sha256'],'target':prior['target'],'release':prior['release'],
            'source_sha256':document['source_sha256'],'previous_database':document['current']['database_pointer'],
            'previous_application':document['current']['application_pointer'],'migration_id':None,'database_pointer':None,'recovery_history':[],
            'approved_conversion':{'sha256':proposal.approval_sha256,'document':document},
            'canary':document['canary'],'canary_requirement_sha256':None,'canary_receipt_sha256':None,
            'canary_capability_sha256':document['canary_capability_sha256'],
            'canary_execution_protocol':document['canary_preflight']['protocol'],
            'canary_worker_binding':document['canary_preflight'].get('worker_binding')}
        update._write(directory/'journal.json',record);validate_intent(root,record)
        return update._finish(root,identifier)
