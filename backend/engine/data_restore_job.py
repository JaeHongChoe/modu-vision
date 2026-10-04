"""Actor-owned fresh-directory restores in the existing durable job ledger."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import shutil
import threading
import time

from backend.engine.data_backup_job import DataBackupJobs
from backend.engine.dataset_import_job import ImportNotAcceptable
from backend.engine.job_store import StaleRevision, StaleFencingToken
from backend.engine.project_archive import restore_archive, _digest_file, _publish_fresh_directory

KIND = 'project_restore'
MARKER = '.vision-restore-owner.json'


def _sync_directory(path: Path) -> None:
    if os.name == 'nt':return  # Windows directory flush requires native acceptance.
    descriptor=os.open(path,os.O_RDONLY | getattr(os,'O_DIRECTORY',0))
    try:os.fsync(descriptor)
    finally:os.close(descriptor)


def inventory(root: Path) -> dict:
    rows = []
    for path in sorted(root.rglob('*')):
        if path.is_symlink():
            raise ImportNotAcceptable('Restored output contains a linked entry')
        if path.is_file() and path != root / MARKER:
            rows.append({'path': path.relative_to(root).as_posix(), 'bytes': path.stat().st_size, 'sha256': _digest_file(path)})
        elif not path.is_file() and not path.is_dir():
            raise ImportNotAcceptable('Restored output contains an unsupported entry')
    return {'sha256': hashlib.sha256(json.dumps(rows,sort_keys=True,separators=(',',':')).encode('utf-8')).hexdigest(),
            'count': len(rows), 'bytes': sum(row['bytes'] for row in rows)}


def fresh_target(value: str, project: dict) -> Path:
    target = Path(value).expanduser().absolute()
    # Resolve only after validating each lexical ancestor, so links cannot disappear in canonicalization.
    if any(path.is_symlink() for path in (target,*target.parents)):
        raise ImportNotAcceptable('Restore target and parent must be unlinked')
    target = target.resolve()
    if not target.parent.is_dir():
        raise ImportNotAcceptable('Restore requires an existing owned parent directory')
    if target.exists():
        raise ImportNotAcceptable('Restore target already exists; choose a new directory')
    for text in (project['project_dir'],project.get('source_dataset_dir')):
        if text and target.is_relative_to(Path(text).resolve()):
            raise ImportNotAcceptable('Restore target must be outside original project and source')
    return target


class DataRestoreJobs:
    def __init__(self,store):
        self.store=store

    def _record(self,job_id,project_key,actor_id):
        row=self.store.record(job_id)
        if row['kind']!=KIND or row['project_key']!=project_key or row['actor_id']!=actor_id:
            raise KeyError(job_id)
        return row

    def _reservation(self,spec):
        target=Path(spec['target_dir'])
        return target.parent / f'.{target.name}.restore-reservation.json'

    def _owner(self,job_id,spec):
        return {'job_id':job_id,'backup_id':spec['backup_id'],'archive_sha256':spec['archive_sha256'],'target_dir':spec['target_dir']}

    def _check_reservation(self,job_id,spec):
        path=self._reservation(spec)
        try:
            if path.is_symlink() or json.loads(path.read_text(encoding='utf-8'))!=self._owner(job_id,spec):
                raise ValueError('reservation identity changed')
        except (OSError,ValueError) as exc:
            raise ImportNotAcceptable('Restore target reservation changed or is unavailable') from exc
        target=Path(spec['target_dir'])
        operation=self.store.checkpoint_value(job_id)
        current_parent=target.parent.stat()
        if operation.get('parent_identity') != [current_parent.st_dev,current_parent.st_ino]:
            raise ImportNotAcceptable('Restore parent directory identity changed')
        if any(path.is_symlink() for path in (target,*target.parents)) or not target.parent.is_dir():
            raise ImportNotAcceptable('Restore target parent changed')

    def _source(self,spec,project_key,actor_id):
        view=DataBackupJobs(self.store).view(spec['backup_id'],project_key,actor_id)
        if view.get('expires_at',0)<=time.time():
            raise ImportNotAcceptable('Backup output expired')
        if not view['downloadable']:
            raise ImportNotAcceptable('Backup output is unfinished, missing or changed')
        if view['result_ref']['sha256']!=spec['archive_sha256']:
            raise ImportNotAcceptable('Backup archive hash changed')
        return Path(view['result_ref']['path'])

    def submit(self,context,project_key,backup_id,target_dir,expected_archive_sha256,key=None):
        if context.mode!='local':
            raise ImportNotAcceptable('Restore to a new directory is supported only in local desktop mode')
        target=str(Path(target_dir).expanduser().absolute())
        spec={'backup_id':backup_id,'target_dir':target,'archive_sha256':expected_archive_sha256}
        if key is not None and self.store.reserved(context,project_key,KIND,key) is not None:
            return self.store.submit(context,project_key,KIND,spec,key,parent_id=backup_id)
        view=DataBackupJobs(self.store).view(backup_id,project_key,context.actor_id)
        if (view.get('result_ref') or {}).get('sha256')!=expected_archive_sha256:
            raise ImportNotAcceptable('Expected backup archive hash does not match')
        self._source(spec,project_key,context.actor_id)
        backup_spec=json.loads(self.store.record(backup_id)['spec_json'])
        fresh_target(target,backup_spec['project'])
        ref=self.store.submit(context,project_key,KIND,spec,key,parent_id=backup_id,
                              project_dir=backup_spec['project']['project_dir'])
        if ref.created:
            try:
                with self._reservation(spec).open('x',encoding='utf-8') as output:
                    json.dump(self._owner(ref.id,spec),output,sort_keys=True);output.flush();os.fsync(output.fileno())
                self.store.checkpoint(ref.id,{'kind':KIND,'source_snapshot':expected_archive_sha256,
                    'expected_target_revision':'absent','parent_identity':[Path(target).parent.stat().st_dev,Path(target).parent.stat().st_ino],'target_dir':target,'progress_unit':'file','attempt':0,
                    'progress':{'phase':'accepted','processed':0,'total':view['result_ref']['count']},
                    'staged_output':None,'result_ref':None,'expires_at':view['expires_at']})
            except Exception as exc:
                self.store.transition(ref.id,ref.revision,'fail',{'error':{'message':str(exc)}})
                raise ImportNotAcceptable('Restore target is already reserved or reservation failed') from exc
        return ref

    def _verified_target(self,job_id,spec,operation):
        result=operation.get('result_ref');target=Path(spec['target_dir'])
        if not result or not target.is_dir() or target.is_symlink():return False
        try:
            current=target.stat()
            if result.get('directory_identity')!=[current.st_dev,current.st_ino]:return False
            marker=target/MARKER
            return not marker.is_symlink() and json.loads(marker.read_text(encoding='utf-8'))==self._owner(job_id,spec) and inventory(target)=={key:result[key] for key in ('sha256','count','bytes')}
        except (OSError,ValueError,ImportNotAcceptable):return False

    def view(self,job_id,project_key,actor_id):
        row=self._record(job_id,project_key,actor_id);spec=json.loads(row['spec_json']);operation=self.store.checkpoint_value(job_id)
        return {'job_id':job_id,'state':row['state'],'revision':row['revision'],**operation,
            'attempt':len(self.store.attempts(job_id)), 'resumable':row['state']=='interrupted' and not self.store.cancel_intent(job_id),
            'downloadable':False,'result_available':row['state']=='completed' and operation.get('expires_at',0)>time.time() and self._verified_target(job_id,spec,operation),
            'capabilities':{'resume':True,'restore':False},'autoactivated':False}

    def resume(self,job_id,project_key,actor_id):
        view=self.view(job_id,project_key,actor_id);ref=self.store.get(job_id)
        if ref.state in ('accepted','running','completed'):return ref
        if not view['resumable']:raise ImportNotAcceptable('Only an interrupted uncancelled restore can resume')
        spec=json.loads(self.store.record(job_id)['spec_json']);self._source(spec,project_key,actor_id);self._check_reservation(job_id,spec)
        if Path(spec['target_dir']).exists():raise ImportNotAcceptable('Restore target changed; owned recovery requires exact verified receipt')
        return self.store.transition(job_id,ref.revision,'resume')

    def _check_stage(self,stage,operation):
        if stage.is_symlink() or not stage.is_dir():raise ImportNotAcceptable('Owned staging directory changed')
        current=stage.stat()
        if operation.get('stage_identity')!=[current.st_dev,current.st_ino]:raise ImportNotAcceptable('Owned staging directory identity changed')

    def _cleanup_cancelled(self,job_id,spec,operation):
        self._check_reservation(job_id,spec)
        stage=Path(operation['staged_output']) if operation.get('staged_output') else None
        if stage and stage.name.startswith(f'.{Path(spec["target_dir"]).name}.restore-{job_id}-') and stage.parent==Path(spec['target_dir']).parent and not stage.is_symlink():
            if stage.exists():
                self._check_stage(stage,operation)
                shutil.rmtree(stage,ignore_errors=True)
        self._reservation(spec).unlink()

    def run(self,job_id):
        row=self.store.record(job_id)
        if row['kind']!=KIND:raise ValueError('Not a restore job')
        ref=self.store.get(job_id)
        if ref.state!='accepted':return ref
        spec=json.loads(row['spec_json']);operation=self.store.checkpoint_value(job_id)
        def abort_accepted():
            current=self.store.get(job_id)
            if current.state!='accepted':return current
            try:self._cleanup_cancelled(job_id,spec,operation)
            except (OSError,ValueError,ImportNotAcceptable):pass
            current=self.store.get(job_id)  # Cancellation intent changes the ledger revision.
            try:return self.store.transition(job_id,current.revision,'abort',{'reason':'cancelled before staging'})
            except StaleRevision:return self.store.get(job_id)
        if self.store.cancel_intent(job_id):return abort_accepted()
        try:ref=self.store.transition(job_id,ref.revision,'start')
        except StaleRevision:
            if self.store.cancel_intent(job_id):return abort_accepted()
            return self.store.get(job_id)
        try:attempt=self.store.begin_attempt(job_id,ref.revision,'restore-thread',None,os.getpid())
        except StaleRevision:
            current=self.store.get(job_id)
            if current.state!='running':return current
            try:attempt=self.store.begin_attempt(job_id,current.revision,'restore-thread',None,os.getpid())
            except StaleRevision:return self.store.get(job_id)
        fence=attempt.fencing_token
        target=Path(spec['target_dir']);stage=target.parent/f'.{target.name}.restore-{job_id}-{fence}'
        try:
            if self.store.cancel_intent(job_id):raise InterruptedError('cancelled before staging')
            archive=self._source(spec,row['project_key'],row['actor_id']);self._check_reservation(job_id,spec)
            if target.exists():raise ImportNotAcceptable('Restore target changed')
            operation.update(error=None,result_ref=None,staged_output=str(stage),progress={'phase':'staging','processed':0,'total':operation['progress']['total']})
            self.store.checkpoint(job_id,operation,fence)
            def created(staging):
                current=staging.stat();operation['stage_identity']=[current.st_dev,current.st_ino]
                self.store.checkpoint(job_id,operation,fence)
            def progress(processed,total):
                self._check_stage(stage,operation)
                if self.store.cancel_intent(job_id):raise InterruptedError('cancelled during staging')
                operation['progress']={'phase':'staging','processed':processed,'total':total}
                self.store.checkpoint(job_id,operation,fence)
            def verified(staging,final):
                self._source(spec,row['project_key'],row['actor_id']);self._check_reservation(job_id,spec)
                result=inventory(staging)
                self._check_stage(staging,operation)
                (staging/MARKER).write_text(json.dumps(self._owner(job_id,spec),sort_keys=True),encoding='utf-8')
                for path in staging.rglob('*'):
                    if path.is_file():
                        with path.open('rb+') as content:os.fsync(content.fileno())
                for directory in [path for path in staging.rglob('*') if path.is_dir()] + [staging]:
                    _sync_directory(directory)
                operation.update(result_ref={'path':str(final),'directory_identity':operation['stage_identity'],**result},progress={'phase':'verified','processed':result['count'],'total':result['count']})
                self.store.checkpoint(job_id,operation,fence,require_uncancelled=True)
            def publish(staging,final):
                def install():
                    if self.store.cancel_intent(job_id):raise InterruptedError('cancelled before publication')
                    self._check_reservation(job_id,spec)
                    self._source(spec,row['project_key'],row['actor_id'])
                    self._check_stage(staging,operation)
                    if inventory(staging)!={key:operation['result_ref'][key] for key in ('sha256','count','bytes')}:
                        raise ImportNotAcceptable('Verified staged output changed')
                    _publish_fresh_directory(staging,final)
                    _sync_directory(final.parent)
                    return None
                self.store.finish(job_id,'complete',{'operation':operation},fencing_token=fence,publish=install)
            restore_archive(archive,target,staging_dir=stage,before_publish=verified,publish=publish,retain_staging=True,stage_created=created,progress=progress)
            return self.store.get(job_id)
        except StaleFencingToken:return self.store.get(job_id)
        except InterruptedError as exc:
            try:self._cleanup_cancelled(job_id,spec,operation)
            except (OSError,ValueError,ImportNotAcceptable):pass  # A replaced staging/reservation belongs to another writer.
            return self.store.finish(job_id,'abort',{'reason':str(exc)},fencing_token=fence)
        except Exception as exc:
            operation['error']={'message':str(exc)[:2048]}
            operation['progress']['phase']='publication_unconfirmed' if operation.get('result_ref') else 'failed'
            self.store.checkpoint(job_id,operation,fence)
            if operation.get('result_ref'):return self.store.get(job_id)
            return self.store.finish(job_id,'fail',{'error':{'message':str(exc)},'staged_output':operation.get('staged_output')},fencing_token=fence)

    def start(self,job_id):
        thread=threading.Thread(target=self.run,args=(job_id,),daemon=True,name=f'Restore-{job_id[:8]}');thread.start();return thread

    def recover_orphans(self):
        interrupted,completed=[],[]
        for row in self.store.active(KIND):
            try:
                spec=json.loads(row['spec_json']);operation=self.store.checkpoint_value(row['id'])
                # Publication preceded any later cancellation. Only this exact inode+receipt proves it.
                if self._verified_target(row['id'],spec,operation):
                    self._check_reservation(row['id'],spec)
                    ref=self.store.get(row['id'])
                    self.store.transition(row['id'],ref.revision,'complete',{'operation':operation,'recovered':True});completed.append(row['id'])
                elif self.store.cancel_intent(row['id']):
                    try:self._cleanup_cancelled(row['id'],spec,operation)
                    except (OSError,ValueError,ImportNotAcceptable):pass
                    ref=self.store.get(row['id'])
                    self.store.transition(row['id'],ref.revision,'abort',{'reason':'cancelled unpublished restore recovered'})
                else:
                    self._check_reservation(row['id'],spec)
                    ref=self.store.get(row['id'])
                    self.store.transition(row['id'],ref.revision,'interrupt',{'reason':'Backend restarted; explicit owned restore resume required'});interrupted.append(row['id'])
            except StaleRevision:pass
            except (OSError,ValueError,ImportNotAcceptable):
                ref=self.store.get(row['id'])
                try:self.store.transition(row['id'],ref.revision,'interrupt',{'reason':'Restore ownership or result drift requires review'})
                except StaleRevision:continue
                interrupted.append(row['id'])
        return {'interrupted':interrupted,'completed':completed}
