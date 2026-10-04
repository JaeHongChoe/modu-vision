"""Opt-in service admission metadata around the existing CaptureGroups join store."""
from dataclasses import asdict
import hashlib,json
from pathlib import Path
from backend.engine.capture_groups import CaptureGroups,CapturePolicy,CaptureKeyReused
from backend.engine.runtime_process_control import atomic_private_json


def validate_join_policy(record):
    if not isinstance(record,dict) or set(record)!={'revision','policy'} or type(record['revision']) is not int or record['revision']<1:
        raise ValueError('Capture group policy requires positive revision and policy')
    try:policy=CapturePolicy(**record['policy'])
    except TypeError as exc:raise ValueError('Invalid capture group policy fields') from exc
    return {'revision':record['revision'],'policy':{key:value for key,value in policy.canonical().items() if key!='version'}}


class ServiceCaptureGroups:
    def __init__(self,store):self.store=store;self.root=store.state_dir

    def current(self):
        path=self.root/'capture-group-policy.json'
        if path.is_symlink():raise ValueError('Capture group policy cannot follow links')
        return validate_join_policy(json.loads(path.read_text(encoding='utf-8'))) if path.is_file() else None

    def configure(self,record,expected_revision,*,package_import=False):
        record=validate_join_policy(record)
        from backend.engine.runtime_process_control import runtime_state_lock
        with runtime_state_lock(self.root):
            previous=self.current();revision=previous['revision'] if previous else 0
            if type(expected_revision) is not int or expected_revision!=revision or (record['revision']!=revision+1 and not (package_import and previous is None)):
                raise ValueError('Capture group policy revision changed; reopen before saving')
            atomic_private_json(self.root/'capture-group-policy.json',record)
        return record

    def groups(self,record):
        checked=validate_join_policy(record);policy=CapturePolicy(**checked['policy'])
        identity=hashlib.sha256(json.dumps(checked,sort_keys=True).encode()).hexdigest()
        path=self.root/f'capture-groups-{identity}.sqlite3'
        if path.is_symlink():raise ValueError('Capture group store cannot follow links')
        return CaptureGroups(path,policy)

    def admit(self,conn,job_id,capture,recipe):
        record=self.current()
        if not record:return None,None
        if (not isinstance(capture,dict) or set(capture)!={'part_id','trigger_id','view_id','captured_at_ms'}
                or not all(isinstance(capture.get(key),str) and capture[key].strip() and len(capture[key])<=160 for key in ('part_id','trigger_id','view_id'))
                or type(capture.get('captured_at_ms')) is not int):
            return None,'CAPTURE_METADATA_INCOMPLETE'
        recipe_sha=(recipe or {}).get('manifest_sha256')
        if not isinstance(recipe_sha,str) or len(recipe_sha)!=64:
            return None,'CAPTURE_RECIPE_UNKNOWN'
        key=(capture['part_id'],capture['trigger_id'])
        group=conn.execute('SELECT policy_json,recipe_sha256 FROM capture_bindings WHERE part_id=? AND trigger_id=?',key).fetchone()
        if group:
            record=json.loads(group['policy_json'])
        else:
            conn.execute('INSERT INTO capture_bindings VALUES(?,?,?,?)',(*key,json.dumps(record,sort_keys=True),recipe_sha))
        groups=self.groups(record);groups.reserve(*key);groups.expire_due();status=groups.group(*key)
        metadata={**capture,'policy':record,'recipe_sha256':group['recipe_sha256'] if group else recipe_sha,'frame_ref':job_id}
        failure=None
        if group and group['recipe_sha256']!=recipe_sha:failure='CAPTURE_RECIPE_MISMATCH'
        elif status.state!='OPEN':failure='CAPTURE_GROUP_CLOSED'
        elif capture['view_id'] not in record['policy']['required_view_ids']:failure='CAPTURE_VIEW_UNKNOWN'
        existing=conn.execute('SELECT job_id,image_sha256,capture_json FROM jobs WHERE capture_json IS NOT NULL').fetchall()
        for row in existing:
            prior=json.loads(row['capture_json'])
            if prior.get('admission_failure'):continue
            if (prior['part_id'],prior['trigger_id'],prior['view_id'])==(*key,capture['view_id']):
                failure=failure or 'CAPTURE_VIEW_CONFLICT';break
        if failure:
            try:
                groups.add_frame(*key,'__rejected__' if status.state=='OPEN' else capture['view_id'],job_id,capture['captured_at_ms'],'REVIEW')
            except CaptureKeyReused:
                pass  # Provider records the refusal and preserves the immutable closed verdict.
        metadata['admission_failure']=failure
        return metadata,failure

    def finish(self,metadata,verdict):
        groups=self.groups(metadata['policy'])
        try:
            joined=groups.add_frame(metadata['part_id'],metadata['trigger_id'],metadata['view_id'],metadata['frame_ref'],metadata['captured_at_ms'],verdict)
        except CaptureKeyReused:
            joined=groups.group(metadata['part_id'],metadata['trigger_id'])
        return {**asdict(joined),'recipe_sha256':metadata['recipe_sha256'],'policy_revision':metadata['policy']['revision']}

    def enqueue_deadlines(self,conn,*,require_delivery):
        """Reconcile persisted closure even if readback or a crash already expired it.

        A stable existing input key and jobs/outbox transaction fence every sweep.
        The in-memory cursor bounds work and rotates across all historical bindings.
        """
        from backend.engine.inspection_service import _canonical,_now,InboxFull
        import time
        cursor=getattr(self.store,'_capture_deadline_cursor',0)
        bindings=conn.execute('SELECT rowid AS cursor,* FROM capture_bindings WHERE rowid>? ORDER BY rowid LIMIT 100',(cursor,)).fetchall()
        outputs=[]
        for binding in bindings:
            record=json.loads(binding['policy_json']);provider=self.groups(record)
            provider.expire_due();group=provider.group(binding['part_id'],binding['trigger_id'])
            if group is None or group.state not in ('EXPIRED','INCOMPLETE'):continue
            identity={'part_id':binding['part_id'],'trigger_id':binding['trigger_id'],
                      'policy':record,'recipe_sha256':binding['recipe_sha256']}
            digest=hashlib.sha256(_canonical(identity).encode()).hexdigest();key='capture-deadline:'+digest
            if conn.execute('SELECT 1 FROM jobs WHERE idempotency_key=?',(key,)).fetchone():continue
            admitted=[]
            for row in conn.execute('SELECT * FROM jobs WHERE capture_json IS NOT NULL ORDER BY rowid'):
                metadata=json.loads(row['capture_json'])
                if metadata.get('admission_failure'):continue
                if (metadata['part_id'],metadata['trigger_id'])!=(binding['part_id'],binding['trigger_id']):continue
                original=json.loads(row['runtime_binding_json']) if row['runtime_binding_json'] else {}
                if original.get('manifest_sha256')!=binding['recipe_sha256']:continue
                admitted.append((row,metadata))
            if not admitted:continue  # Cannot substitute the current runtime for an unknown admission.
            try:self.store._capacity(conn)
            except InboxFull:continue  # Retry from this immutable provider closure on a later sweep.
            anchor=admitted[0][0];identifier='capture-deadline-'+digest[:32];stamp=_now()
            joined={**asdict(group),'recipe_sha256':binding['recipe_sha256'],'policy_revision':record['revision']}
            result={'final_verdict':'REVIEW','outcome_kind':'capture_group_deadline','capture_group':joined,
                    'capture_policy':record,'runtime_identity':json.loads(anchor['runtime_binding_json']),
                    'admitted_frames':[{'job_id':row['job_id'],'image_sha256':row['image_sha256'],
                         **{name:metadata[name] for name in ('part_id','trigger_id','view_id','captured_at_ms')}}
                         for row,metadata in admitted]}
            state='delivery_pending' if require_delivery else 'completed'
            conn.execute("""INSERT INTO jobs(job_id,image_path,image_id,image_sha256,source,state,verdict,
                model_verdict,result_json,created_at,updated_at,runtime_binding_json,runtime_binding_sha256,
                binding_provenance,idempotency_key,payload_sha256,deadline_at)
                VALUES(?,?,?,?,?,?, 'REVIEW','REVIEW',?,?,?,?,?,?,?,?,?)""",
                (identifier,anchor['image_path'],anchor['image_id'],anchor['image_sha256'],'capture-group-deadline',state,
                 _canonical(result),stamp,stamp,anchor['runtime_binding_json'],anchor['runtime_binding_sha256'],
                 'admission_snapshot',key,digest,time.time()+self.store.max_queue_age_seconds))
            if require_delivery:
                conn.execute("INSERT INTO deliveries(job_id,state,updated_at) VALUES(?,'pending',?)",(identifier,stamp))
            self.store._event(conn,identifier,state,'Whole-part collection deadline: REVIEW')
            outputs.append(identifier)
        self.store._capture_deadline_cursor=bindings[-1]['cursor'] if bindings else 0
        return outputs

    def status(self,limit=100):
        if type(limit) is not int or not 1<=limit<=500:raise ValueError('Read 1–500 capture groups')
        with self.store._connection() as conn:
            bindings=conn.execute('SELECT * FROM capture_bindings ORDER BY rowid DESC LIMIT ?',(limit,)).fetchall()
        rows=[]
        for binding in bindings:
            record=json.loads(binding['policy_json']);groups=self.groups(record);groups.expire_due();result=groups.group(binding['part_id'],binding['trigger_id'])
            if result:rows.append({**asdict(result),'recipe_sha256':binding['recipe_sha256'],'policy_revision':record['revision'],
                                  'pending':next((row for row in groups.pending() if row['part_id']==result.part_id and row['trigger_id']==result.trigger_id),None)})
        return {'policy':self.current(),'groups':rows,'total':len(rows)}
