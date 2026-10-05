"""Durable dataset import (S3-01): one ledger job builds one prepared, completely validated index revision.

The job is a JobStore record of kind ``dataset_import``. Submission is idempotent and captures the server-resolved
project scope (namespace, project folder, registered source) before any work. A run holds a fenced attempt: it
heartbeats the attempt while files are read, checks the durable cancel intent between files, and publishes the
revision receipt only under its own fence, so a stale attempt can neither complete the job nor attach a receipt.

Acceptance is a separate, explicit action: a finished scan never activates anything. ``accept`` activates exactly the
revision the job published, for the job's own project namespace, with a compare-and-swap on the active revision.

The index and the ledger are separate databases, so this is not one global transaction. The job's id is the
revision's publication key: if a run crashed after the revision was sealed and before the job was completed, the next
run of the same job returns that revision instead of building another. A job the next start reports as interrupted is
resumed by a continuation job, whose unchanged files are reused from the stat cache.
Live progress is held in memory by the process that runs the job; the ledger records states, attempts and receipts.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace
import json
import hashlib
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional

from backend.engine.dataset_index import DatasetIndex, StaleActiveRevision
from backend.engine.job_store import JobRef, JobStore, StaleFencingToken, StaleRevision
from backend.engine.job_state import TERMINAL

logger = logging.getLogger(__name__)
KIND = 'dataset_import'
_HEARTBEAT_SECONDS = 2.0
_CANCEL_CHECK_SECONDS = 0.5


@dataclass(frozen=True)
class ImportSpec:
    """What an import reads and how; every path is resolved by the server, never taken from the caller."""
    project_root: str
    source_root: str
    task: str
    invalid_policy: str = 'exclude'
    verify: bool = False
    follow_links: bool = False
    artifact: Optional[dict] = None  # the uploaded archive (id, revision, sha256) an extracted source came from
    annotation_root: Optional[str] = None  # the project's active annotation folder (Studio overlays label images there)
    archive_pending: bool = False  # new jobs extract their immutable artifact inside the fenced attempt


class ImportNotAcceptable(Exception):
    """The job did not publish a prepared revision of this project that can be accepted."""


class _AttemptLost(Exception):
    """A newer attempt owns the job; this run stops without reporting anything."""


class DatasetImportJobs:
    def __init__(self, store: JobStore, index: DatasetIndex, *, lease_seconds: float = 30.0,
                 artifact_store=None, authorize_archive=None):
        self.store, self.index, self.lease_seconds = store, index, lease_seconds
        self.artifact_store, self.authorize_archive = artifact_store, authorize_archive
        self._lock = threading.Lock()
        self._progress: dict[str, dict] = {}

    def submit(self, context: Any, project_key: str, spec: ImportSpec, idempotency_key: Optional[str] = None,
               *, parent_id: Optional[str] = None) -> JobRef:
        """Reserve the job before any work; a repeated key with the same spec returns the same job."""
        payload = asdict(spec)
        for optional in ('artifact', 'annotation_root'):
            if payload[optional] is None:
                del payload[optional]  # a request keeps the digest it had before these fields existed
        if not payload['archive_pending']:
            del payload['archive_pending']
        ref = self.store.submit(context, project_key, KIND, payload, idempotency_key, parent_id=parent_id,
                                 project_dir=str(Path(spec.project_root).resolve()))
        if ref.created:
            try:
                snapshot = self._archive_identity(spec) if spec.archive_pending else self._snapshot(spec)
            except (OSError, ImportNotAcceptable):
                snapshot = None  # legacy submission still records a failed run; unsafe resume is refused
            self.store.checkpoint(ref.id, {'source_snapshot': snapshot,
                'expected_target_revision': self.index.active(project_key), 'progress_unit': 'image',
                'staged_output': str(self.index.path), 'expires_at': None})
        return ref

    @staticmethod
    def _snapshot(spec: ImportSpec, *, check=lambda: None) -> str:
        digest = hashlib.sha256()
        for label, root in (('source', spec.source_root), ('annotations', spec.annotation_root)):
            if root is None:
                continue
            folder = Path(root)
            if not folder.is_dir():
                raise ImportNotAcceptable('source folder is unavailable')
            for directory, folders, files in os.walk(folder, followlinks=spec.follow_links):
                check()
                folders.sort()
                if spec.follow_links and any((Path(directory) / name).is_symlink() for name in folders):
                    raise ImportNotAcceptable('Durable resume does not support linked source directories')
                for name in sorted(files):
                    check()
                    path = Path(directory) / name
                    if path.is_symlink() and not spec.follow_links:
                        digest.update((label + str(path.relative_to(folder)) + ':link').encode())
                        continue
                    content = hashlib.sha256()
                    with path.open('rb') as handle:
                        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
                            check()
                            content.update(chunk)
                    digest.update(json.dumps([label, str(path.relative_to(folder)), content.hexdigest()], ensure_ascii=False).encode('utf-8'))
        return digest.hexdigest()

    def _archive_identity(self, spec: ImportSpec, *, check=lambda: None) -> str:
        """No ZIP/extracted-source IO at submission; bind the artifact and the captured annotation bytes."""
        annotations = self._snapshot(ImportSpec(spec.project_root, spec.annotation_root, spec.task), check=check) if spec.annotation_root else None
        return 'archive-input:' + hashlib.sha256(json.dumps([spec.artifact, spec.annotation_root, annotations],
                                                          sort_keys=True).encode()).hexdigest()

    def _archive_context(self, record):
        from backend.contracts.context import ProjectContext, ArtifactRef
        if self.artifact_store is None:
            raise ImportNotAcceptable('Archive artifact storage is unavailable')
        context = ProjectContext(**{name: record[name] for name in ('workspace_id', 'project_id', 'actor_id', 'mode')})
        if self.artifact_store.registry.project_key(context) != record['project_key']:
            raise ImportNotAcceptable('Archive project scope changed')
        if context.mode == 'local' and context.actor_id != self.artifact_store.registry.local_actor_id:
            raise ImportNotAcceptable('Archive actor scope changed')
        if self.authorize_archive is not None:
            self.authorize_archive(context)
        elif context.mode != 'local':
            raise ImportNotAcceptable('Archive team authority is unavailable')
        ref = ArtifactRef(**json.loads(record['spec_json'])['artifact'])
        self.artifact_store.reference(context, ref)
        return context, ref

    def resume(self, job_id: str, project_key: str, actor_id: str) -> JobRef:
        self.view(job_id, project_key)
        record = self.store.record(job_id)
        if record['actor_id'] != actor_id:
            raise KeyError(job_id)
        ref = self.store.get(job_id)
        if ref.state in ('accepted', 'running', 'completed'):
            return ref
        if ref.state != 'interrupted' or self.store.cancel_intent(job_id):
            raise ImportNotAcceptable('Only an interrupted, uncancelled import can resume')
        operation = self.store.checkpoint_value(job_id) or {}
        if not operation.get('source_snapshot'):
            raise ImportNotAcceptable('Legacy import has no immutable source snapshot; submit a new import')
        spec = ImportSpec(**json.loads(record['spec_json']))
        if spec.archive_pending:
            try:
                self._archive_context(record)
            except Exception as exc:
                raise ImportNotAcceptable('Archive artifact or project authority is unavailable') from exc
            snapshot = self._archive_identity(spec)
            extracted = operation.get('archive_source_snapshot')
            if extracted and extracted != self._snapshot(replace(spec, source_root=operation['extracted_source_root'])):
                raise ImportNotAcceptable('The extracted source changed since verification')
        else:
            snapshot = self._snapshot(spec)
        if operation['source_snapshot'] != snapshot:
            raise ImportNotAcceptable('The source changed since submission')
        if operation['expected_target_revision'] != self.index.active(project_key):
            raise ImportNotAcceptable('The target revision changed since submission')
        return self.store.transition(job_id, ref.revision, 'resume')

    def run(self, job_id: str, *, executor: str = 'local-thread') -> JobRef:
        """Build the revision under a fenced attempt and finish the job with its receipt (or the reason it ended).

        Every way out of a run that owns its attempt ends the job with a recorded reason; only a newer attempt's
        ownership makes a run stop without reporting.
        """
        record = self.store.record(job_id)
        if record['kind'] != KIND:
            raise ValueError(f'{job_id} is not a dataset import')
        spec = ImportSpec(**json.loads(record['spec_json']))
        ref = self._start(job_id)
        if ref.state in TERMINAL:
            return ref
        fence = self._begin_attempt(job_id, executor)
        try:
            return self._run_attempt(job_id, fence, record, spec)
        except _AttemptLost:
            self._set_progress(job_id, {'phase': 'superseded'}, persist=False)
            return self.store.get(job_id)
        except InterruptedError:
            return self._finish(job_id, fence, 'abort', {'reason': 'cancelled before a revision was recorded'})
        except Exception as exc:
            sealed = self.index._published(record['project_key'], job_id)
            if sealed is not None:  # the revision exists: the job is never failed; complete it now or at startup
                try:
                    return self._finish(job_id, fence, 'complete', {'revision': asdict(sealed)})
                except Exception:
                    logger.exception('Dataset import %s sealed its revision but could not record it; the next start completes it', job_id)
                    self._set_progress(job_id, {'phase': 'recording'})
                    return self.store.get(job_id)
            return self._finish(job_id, fence, 'fail', {'error': {'message': f'{type(exc).__name__}: {exc}'}})

    def _start(self, job_id: str) -> JobRef:
        """Move an accepted job to running; a stop recorded before that ends it aborted (cancel intents move the revision)."""
        for _ in range(4):
            ref = self.store.get(job_id)
            if ref.state != 'accepted':
                return ref
            try:
                if self.store.cancel_intent(job_id) is not None:
                    ref = self.store.transition(job_id, ref.revision, 'abort', {'reason': 'cancelled before the import started'})
                    self._set_progress(job_id, {'phase': ref.state})
                    return ref
                return self.store.transition(job_id, ref.revision, 'start')
            except StaleRevision:
                continue
        raise RuntimeError('The import could not start: its record kept changing; start it again')

    def _begin_attempt(self, job_id: str, executor: str) -> int:
        """A new fenced attempt at the job's current revision (a cancel intent recorded meanwhile moves the revision)."""
        for _ in range(3):
            ref = self.store.get(job_id)
            try:
                return self.store.begin_attempt(job_id, ref.revision, executor, None, os.getpid()).fencing_token
            except StaleRevision:
                continue
        ref = self.store.get(job_id)
        return self.store.begin_attempt(job_id, ref.revision, executor, None, os.getpid()).fencing_token

    def _run_attempt(self, job_id: str, fence: int, record: dict, spec: ImportSpec) -> JobRef:
        def owns() -> None:
            try:
                self.store.heartbeat(job_id, fence, self.lease_seconds)
            except StaleFencingToken as exc:
                raise _AttemptLost(str(exc)) from None

        owns()
        if self.store.cancel_intent(job_id) is not None:  # a stop that landed while the attempt was starting
            raise InterruptedError('cancelled before reading started')
        clock = {'beat': time.monotonic(), 'cancel': time.monotonic(), 'cancelled': False}

        def progress(done: int, total: int) -> None:
            self._set_progress(job_id, {'phase': 'reading', 'processed': done, 'total': total, 'total_known': True, 'unit': 'image'}, fence)
            if time.monotonic() - clock['beat'] >= _HEARTBEAT_SECONDS:
                owns()
                clock['beat'] = time.monotonic()

        def cancelled() -> bool:
            if time.monotonic() - clock['cancel'] >= _CANCEL_CHECK_SECONDS:
                clock['cancel'] = time.monotonic()
                clock['cancelled'] = self.store.cancel_intent(job_id) is not None
            return clock['cancelled']

        def check() -> None:
            if time.monotonic() - clock['beat'] >= _HEARTBEAT_SECONDS:
                owns()
                clock['beat'] = time.monotonic()
            if cancelled():
                raise InterruptedError('cancelled during archive processing')

        def promotion_guard() -> None:
            owns()
            if self.store.cancel_intent(job_id) is not None:
                raise InterruptedError('cancelled before archive promotion')

        archive_spec = spec
        if spec.archive_pending:
            spec = self._extract_archive(job_id, fence, record, spec, check, promotion_guard)
        self._set_progress(job_id, {'phase': 'listing', 'processed': 0, 'total': None, 'total_known': False, 'unit': 'image'}, fence)

        def verify_before_seal() -> None:
            owns()
            if self.store.cancel_intent(job_id) is not None:
                raise InterruptedError('cancelled before verification')
            operation = self.store.checkpoint_value(job_id)
            if archive_spec.archive_pending:
                self._archive_context(record)
                if operation['source_snapshot'] != self._archive_identity(archive_spec, check=check):
                    raise ImportNotAcceptable('The archive annotations changed during verification')
                actual = self._snapshot(spec, check=check)
                expected = operation.get('archive_source_snapshot')
            else:
                expected = operation.get('source_snapshot')
                actual = self._snapshot(spec) if expected else None
            if expected and expected != actual:
                raise ImportNotAcceptable('The source changed during verification')
            if 'expected_target_revision' in operation and operation['expected_target_revision'] != self.index.active(record['project_key']):
                raise ImportNotAcceptable('The target revision changed during verification')
            owns()
            if self.store.cancel_intent(job_id) is not None:
                raise InterruptedError('cancelled during verification')

        # The job id is the publication key: whichever attempt seals first defines this job's revision, and a later
        # attempt (or a crash recovery) receives that same receipt; owns() right before sealing keeps a superseded
        # attempt from sealing work the current attempt is still doing.
        receipt = self.index.build_revision(record['project_key'], spec.project_root, spec.source_root, spec.task,
                                            spec.invalid_policy, verify=spec.verify, follow_links=spec.follow_links,
                                            publication_key=job_id, progress=progress, cancelled=cancelled, before_seal=verify_before_seal,
                                            overlay_root=spec.annotation_root)
        return self._finish(job_id, fence, 'complete', {'revision': asdict(receipt)})

    def _extract_archive(self, job_id, fence, record, spec, check, promotion_guard):
        from backend.engine.dataset_archive_input import extract_dataset_archive, verify_extraction
        operation = self.store.checkpoint_value(job_id) or {}
        context, ref = self._archive_context(record)
        promotion_guard()
        self._set_progress(job_id, {'phase': 'archive_verifying', 'processed': 0, 'total': None, 'total_known': False, 'unit': 'byte'}, fence)
        if operation.get('source_snapshot') != self._archive_identity(spec, check=check):
            raise ImportNotAcceptable('The archive annotations changed since submission')
        imports = Path(spec.project_root) / 'dataset_imports'
        if imports.is_symlink() or not imports.resolve().is_relative_to(Path(spec.project_root).resolve()):
            raise ImportNotAcceptable('Archive output is outside its project')
        target = imports / ref.sha256
        if Path(spec.source_root) != target:
            raise ImportNotAcceptable('Archive destination does not match its immutable reference')
        selected = operation.get('extracted_source_root')
        if selected:
            target = Path(selected)
            if target.parent != imports or target.is_symlink() or not target.name.startswith(ref.sha256):
                raise ImportNotAcceptable('Archive checkpoint destination is outside its project')
        if operation.get('archive_source_snapshot'):
            if not verify_extraction(target, ref.sha256, check=check) or operation['archive_source_snapshot'] != self._snapshot(replace(spec, source_root=str(target)), check=check):
                raise ImportNotAcceptable('The extracted source changed since verification')
        elif target.exists() and not verify_extraction(target, ref.sha256, check=check):
            import uuid
            copies = sorted(p for p in imports.glob(f'{ref.sha256}.*') if p.is_dir() and not p.is_symlink())
            target = next((p for p in copies if verify_extraction(p, ref.sha256, check=check)), imports / f'{ref.sha256}.{uuid.uuid4().hex[:12]}')
        if not target.exists():
            with self.artifact_store.open(context, ref, check=check) as handle:
                promotion_guard()
                def progress(done, total):
                    self._set_progress(job_id, {'phase': 'extracting', 'processed': done, 'total': total, 'total_known': True, 'unit': 'byte'}, fence)
                extract_dataset_archive(handle, target, archive_sha256=ref.sha256, check=check, progress=progress, before_publish=promotion_guard)
        promotion_guard()
        spec = replace(spec, source_root=str(target))
        operation = self.store.checkpoint_value(job_id) or {}
        operation['extracted_source_root'] = str(target)
        self.store.checkpoint(job_id, operation, fence)
        self._set_progress(job_id, {'phase': 'source_snapshot', 'processed': 0, 'total': None, 'total_known': False, 'unit': 'byte'}, fence)
        operation = self.store.checkpoint_value(job_id) or {}
        snapshot = self._snapshot(spec, check=check)
        if operation.get('archive_source_snapshot') and operation['archive_source_snapshot'] != snapshot:
            raise ImportNotAcceptable('The extracted source changed during verification')
        operation['archive_source_snapshot'] = snapshot
        promotion_guard()
        self.store.checkpoint(job_id, operation, fence)
        return spec

    def _finish(self, job_id: str, fence: int, event: str, payload: dict) -> JobRef:
        persisted = True
        try:
            ref = self.store.finish(job_id, event, payload, fencing_token=fence)
        except StaleFencingToken:
            persisted = False
            ref = self.store.get(job_id)  # a newer attempt owns the job: this run reports nothing
        self._set_progress(job_id, {'phase': ref.state}, persist=persisted)
        return ref

    def start(self, job_id: str, *, on_error: Optional[Callable[[BaseException], None]] = None) -> threading.Thread:
        """Run the job on a daemon thread of this process (the desktop backend executes imports in-process).

        A failure before the attempt could record its own end (a busy ledger at the first write, say) ends the job
        failed here; if even that cannot be written, the next start recovers the job.
        """
        def target() -> None:
            try:
                self.run(job_id)
            except BaseException as exc:
                logger.exception('Dataset import %s stopped unexpectedly', job_id)
                self._fail_unfinished(job_id, exc)
                if on_error is not None:
                    on_error(exc)
        thread = threading.Thread(target=target, daemon=True, name=f'DatasetImport-{job_id[:8]}')
        thread.start()
        return thread

    def _fail_unfinished(self, job_id: str, exc: BaseException) -> None:
        try:
            ref = self.store.get(job_id)
            if self.index._published(self.store.record(job_id)['project_key'], job_id) is not None:
                return  # its revision is sealed: the next start completes the job with it
            for _ in range(4):  # a cancel intent recorded meanwhile moves the revision: read it again
                if ref.state in TERMINAL:
                    break
                try:
                    self.store.transition(job_id, ref.revision, 'fail', {'error': {'message': f'{type(exc).__name__}: {exc}'}})
                    break
                except StaleRevision:
                    ref = self.store.get(job_id)
        except Exception:
            logger.exception('Could not record the end of dataset import %s; the next start recovers it', job_id)
        self._set_progress(job_id, {'phase': 'failed'})

    def view(self, job_id: str, project_key: str) -> dict:
        """The job as its own project sees it; another namespace reads nothing (KeyError)."""
        record = self.store.record(job_id)
        if record['kind'] != KIND or record['project_key'] != project_key:
            raise KeyError(job_id)
        ended = next((event for event in reversed(self.store.events(job_id)) if event['to_state'] == record['state']
                      and record['state'] in TERMINAL), None)
        with self._lock:
            live = dict(self._progress.get(job_id) or {})
        spec = json.loads(record['spec_json'])
        return {'job_id': job_id, 'state': record['state'], 'revision': record['revision'],
                'attempts': len(self.store.attempts(job_id)), 'cancel_requested': self.store.cancel_intent(job_id) is not None,
                'progress': live or (self.store.checkpoint_value(job_id) or {}).get('progress'), 'result': ended['payload'] if ended else None,
                'operation': self._operation(job_id, ended), 'resumable': record['state'] == 'interrupted' and bool((self.store.checkpoint_value(job_id) or {}).get('source_snapshot')),
                # what the import read: the registered source, or an uploaded archive extracted into the project
                'source': {'root': (self.store.checkpoint_value(job_id) or {}).get('extracted_source_root', spec['source_root']), 'artifact': spec.get('artifact')}}

    def cancel(self, job_id: str, project_key: str, actor_id: str) -> dict:
        """Record the durable intent; the running attempt stops between files and ends the job aborted."""
        self.view(job_id, project_key)
        self.store.request_cancel(job_id, actor_id, 'cancelled by the user')
        return self.view(job_id, project_key)

    def accept(self, job_id: str, project_key: str, revision_id: str, expected_active: Optional[str]) -> str:
        """Activate the revision this job published, only if the project's active revision is still the expected one."""
        result = self.view(job_id, project_key)
        published = (result['result'] or {}).get('revision') if result['state'] == 'completed' else None
        if not published or published.get('revision_id') != revision_id:
            raise ImportNotAcceptable('Only the revision this completed import published can be accepted')
        return self.index.activate(project_key, revision_id, expected_active)

    def recover_orphans(self, reason: str = 'the backend restarted while the import was running') -> dict:
        """At startup no import thread survives. An import whose revision was already sealed under its job id is
        completed with that receipt under a new attempt; every other unfinished import ends interrupted (resuming is
        an explicit continuation). Only the backend that owns the data folder may call this.
        """
        outcome = {'completed': [], 'interrupted': []}
        for row in self.store.active(KIND):
            try:
                sealed = self.index._published(row['project_key'], row['id'])
                if sealed is not None:
                    ref = self.store.get(row['id'])
                    if ref.state == 'accepted':
                        ref = self.store.transition(row['id'], ref.revision, 'start')
                    fence = self.store.begin_attempt(row['id'], ref.revision, 'startup-recovery', None, os.getpid()).fencing_token
                    self.store.finish(row['id'], 'complete', {'revision': asdict(sealed), 'recovered': True}, fencing_token=fence)
                    outcome['completed'].append(row['id'])
                else:
                    self.store.transition(row['id'], row['revision'], 'interrupt', {'reason': reason})
                    outcome['interrupted'].append(row['id'])
            except Exception:  # changed concurrently; the next start reconciles it
                logger.exception('Could not recover dataset import %s', row['id'])
        return outcome

    def _operation(self, job_id: str, ended: Optional[dict]) -> dict:
        operation = self.store.checkpoint_value(job_id) or {}
        receipt = (ended['payload'] or {}).get('revision') if ended else None
        return {**operation, 'attempt': len(self.store.attempts(job_id)),
            'result_ref': {'revision_id': receipt['revision_id'], 'sha256': receipt['manifest_sha256'],
                           'count': receipt['image_count']} if receipt else None,
            'capabilities': {'resume': bool(operation.get('source_snapshot')), 'download': False},
            'resume_reason': None if operation.get('source_snapshot') else 'No immutable snapshot: source unavailable, unsupported links, or legacy job; submit a new import',
            'downloadable': False}  # index revisions are accepted, never downloadable files

    def _set_progress(self, job_id: str, value: dict, fence: Optional[int] = None, *, persist: bool = True) -> None:
        operation = self.store.checkpoint_value(job_id) or {}
        operation['progress'] = {**operation.get('progress', {}), **value}
        if value.get('unit'):
            operation['progress_unit'] = value['unit']
        try:
            if persist:
                self.store.checkpoint(job_id, operation, fence)
        except StaleFencingToken as exc:
            raise _AttemptLost(str(exc)) from None
        with self._lock:
            self._progress[job_id] = {**self._progress.pop(job_id, {}), **value}
            while len(self._progress) > 256:  # live progress is a convenience; the ledger keeps the record
                self._progress.pop(next(iter(self._progress)))


__all__ = ['DatasetImportJobs', 'ImportNotAcceptable', 'ImportSpec', 'KIND', 'StaleActiveRevision']
