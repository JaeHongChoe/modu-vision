"""Typed authenticated Geti gateway; no browser-selected upstream UUID or proxy."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import time
from typing import Literal
from urllib.parse import quote
import uuid
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field
from backend.platforms.geti.client import GetiError
from backend.platforms.geti.policy import authority, admitted_image, public_data

router = APIRouter(prefix='/api/platform/geti', tags=['geti-platform'])
_TASKS = {'classification': 'classification', 'detection': 'detection', 'instance_segmentation': 'instance_segmentation'}
_NATIVE_TASKS = ['classification', 'segmentation', 'detection', 'anomaly', 'patch_classification', 'ocr', 'rotated_detection', 'defect_gan', 'enhancement', 'rotation']


class StrictBody(BaseModel):
    model_config = ConfigDict(extra='forbid')


class StartBody(StrictBody): pass


class OperationBody(StrictBody):
    operation_id: str = Field(pattern=r'^[A-Za-z0-9_.:-]{1,128}$')


class PrepareBody(OperationBody):
    dataset_version_id: str | None = Field(default=None, max_length=128)
    preparation_id: str | None = Field(default=None, max_length=128)
    input_recipe: dict | None = None
    target_task: Literal['instance_segmentation'] | None = None


class WeightsBody(OperationBody):
    architecture_id: str = Field(pattern=r'^[A-Za-z0-9_.:-]{1,160}$')


class TrainBody(WeightsBody):
    max_epochs: int = Field(ge=1, le=10000, strict=True)


class PredictBody(StrictBody):
    model_id: str = Field(pattern=r'^[A-Za-z0-9_.:-]{1,128}$')
    variant_id: str = Field(pattern=r'^[A-Za-z0-9_.:-]{1,128}$')
    image_path: str = Field(min_length=1, max_length=4096)


class AdoptBody(OperationBody):
    expected_revision: str = Field(min_length=1, max_length=256)


def services(request):
    runtime = getattr(request.app.state, 'geti_runtime', None)
    store = getattr(request.app.state, 'geti_store', None)
    if runtime is None or store is None: raise HTTPException(503, '플랫폼 실행 환경이 아직 연결되지 않았습니다.')
    return runtime, store


def client_for(runtime, key, *, request=None, action='read'):
    try: client = runtime.client(key)
    except (RuntimeError, OSError, ValueError) as exc:
        raise HTTPException(503, '플랫폼을 시작할 수 없습니다. 실행 환경 설치와 상태를 확인하세요.') from exc
    if request is None: return client
    class AuthorizedClient:
        def request(self, method, path, **kwargs):
            project, context, current_key, permissions = authority(request, action=action)
            if current_key != key: raise HTTPException(403, 'Platform project authority changed')
            return client.request(method, path, **kwargs)
    return AuthorizedClient()


def platform_call(client, method, path, **kwargs):
    try: return client.request(method, path, **kwargs)
    except GetiError as exc: raise HTTPException(exc.status_code if 400 <= exc.status_code < 600 else 502, str(exc)) from exc


def adapter_call(function, *args, **kwargs):
    try: return function(*args, **kwargs)
    except GetiError as exc: raise HTTPException(exc.status_code, str(exc)) from exc
    except (ValueError, FileNotFoundError) as exc:
        detail = str(exc)
        code = 409 if any(word in detail.lower() for word in ('conflict', 'changed', 'differs', 'another project', 'revision')) else 422
        raise HTTPException(code, public_data(detail)) from exc


def receipt_for(store, key):
    receipt = store.get_dataset(key)
    if not receipt: raise HTTPException(409, '학습에 사용할 데이터 버전을 먼저 준비하세요.')
    return receipt


def current_receipt(project, receipt):
    """Cheap context fence for readiness; full immutable checks stay at actions."""
    if not receipt: return None, None
    source_task = receipt.get('source_task') or receipt.get('task')
    source = project.get('source_dataset_dir')
    saved_source = receipt.get('source_root')
    matches = (receipt.get('project_id') == project['id'] and source_task == project['task']
               and receipt.get('labelset_id') == project.get('active_labelset_id', 'default')
               and source and saved_source and Path(source).resolve() == Path(saved_source).resolve())
    if not matches:
        return None, '현재 작업·원본 폴더·정답 묶음이 준비 당시와 달라졌습니다. 현재 데이터 버전을 다시 준비하세요. 이전 기록은 보존되어 있습니다.'
    return receipt, None


def _image_source_root(project, receipt):
    """Read the current identity without activating Library or migrating files."""
    matched, message = current_receipt(project, receipt)
    if not matched: raise HTTPException(409, message or '학습에 사용할 데이터 버전을 먼저 준비하세요.')
    try:
        root = Path(project['source_dataset_dir']).expanduser().resolve(strict=True)
        project_root = Path(project['project_dir']).resolve(strict=True)
        manifest = json.loads((project_root / 'project.json').read_text(encoding='utf-8'))
        labelsets = json.loads((project_root / 'labelsets.json').read_text(encoding='utf-8'))
        matches = (root.is_dir() and manifest['id'] == project['id'] and manifest['task'] == project['task']
                   and Path(manifest['source_dataset_dir']).expanduser().resolve() == root
                   and labelsets['active_id'] == project.get('active_labelset_id', 'default'))
    except (OSError, ValueError, KeyError, TypeError):
        matches = False
    if not matches:
        raise HTTPException(409, '현재 프로젝트 또는 원본 데이터 연결이 달라졌습니다. 프로젝트를 다시 열고 준비 상태를 확인하세요.')
    return root


def _source_image_sha256(path, root):
    """Hash an admitted original and refuse a changed path or partial read."""
    try:
        resolved = path.resolve(strict=True)
        if not resolved.is_relative_to(root): raise OSError('Image left admitted source')
        before = resolved.stat()
        digest = hashlib.sha256()
        with resolved.open('rb') as handle:
            opened = os.fstat(handle.fileno())
            for block in iter(lambda: handle.read(1024 * 1024), b''): digest.update(block)
            finished = os.fstat(handle.fileno())
        after = path.stat()
        identity = lambda stat: (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
        if (identity(before) != identity(opened) or identity(opened) != identity(finished)
                or identity(finished) != identity(after) or path.resolve(strict=True) != resolved):
            raise OSError('Image changed during read')
        return digest.hexdigest()
    except OSError as exc:
        raise HTTPException(409, '검사 사진을 읽는 동안 원본이 달라졌습니다. 사진 목록을 다시 확인하세요.') from exc


@router.get('/images')
def images(request: Request, offset: int = Query(default=0, ge=0), limit: int = Query(default=100, ge=1, le=200)):
    project, context, key, permissions = authority(request)
    _, store = services(request)
    receipt = receipt_for(store, key)
    root = _image_source_root(project, receipt)
    from backend.engine.grouped_dataset_views import source_image_paths
    try:
        inventory = sorted((path.relative_to(root).as_posix(), path) for path in source_image_paths(root, project['task'])
                           if path.resolve().is_relative_to(root))
    except (OSError, ValueError) as exc:
        raise HTTPException(409, '현재 원본 사진 목록을 읽을 수 없습니다. 원본 데이터 연결을 확인하세요.') from exc
    frozen = {row['relative_path']: row for row in receipt.get('source_mapping', [])}
    items = []
    for relative, path in inventory[offset:offset + limit]:
        digest = _source_image_sha256(path, root)
        transferred = frozen.get(relative)
        if transferred and digest != transferred.get('sha256'):
            raise HTTPException(409, '준비한 데이터 버전의 원본 사진이 달라졌습니다. 현재 데이터를 다시 준비하세요.')
        identifier = transferred.get('media_id') if transferred else str(uuid.uuid5(uuid.NAMESPACE_URL, key + ':' + relative + ':' + digest))
        if not identifier: raise HTTPException(409, '준비한 데이터의 사진 식별자를 확인할 수 없습니다. 현재 데이터를 다시 준비하세요.')
        items.append({'image_uuid': identifier, 'relative_path': relative, 'file_name': path.name,
                      'source_path': str(path.resolve()), 'sha256': digest, 'transferred': bool(transferred)})
    current_project, current_context, current_key, _ = authority(request)
    latest = receipt_for(store, current_key)
    if current_key != key or latest != receipt or _image_source_root(current_project, latest) != root:
        raise HTTPException(409, '사진 목록을 읽는 동안 현재 프로젝트 또는 데이터 버전이 달라졌습니다. 다시 확인하세요.')
    # These are explicitly admitted original paths, never private Geti storage.
    return {'items': items, 'pagination': {'offset': offset, 'limit': limit, 'total': len(inventory)}}


def _operation(store, key, actor, body, kind):
    try: return store.reserve_operation(key, actor, body.operation_id, kind, body.model_dump(exclude={'operation_id'}))
    except ValueError as exc: raise HTTPException(409, str(exc)) from exc


def previous_result(reservation):
    if reservation['status'] == 'completed': return public_data(reservation['result'])
    raise HTTPException(409, '이 요청의 이전 실행 결과를 확정할 수 없습니다. 상태와 작업 기록을 확인하세요. 자동으로 다시 실행하지 않았습니다.')


def remember_failure(store, key, actor, operation_id):
    store.complete_operation(key, actor, operation_id, {'message': 'Operation did not return a confirmed result'}, status='submission_unconfirmed')


def _job_record(row, *, identifier, operation_id, architecture_id=None):
    metadata = row.get('metadata') or {}
    model = metadata.get('model') or {}
    return {'id': identifier, 'operation_id': operation_id, 'platform_job_id': row.get('job_id'),
            'status': str(row.get('status', 'submission_unconfirmed')).lower(), 'progress': row.get('progress', 0),
            'message': row.get('message'), 'error': row.get('error'), 'model_id': model.get('id'),
            'architecture_id': architecture_id or model.get('architecture'), 'created_at': row.get('started_at'),
            'finished_at': row.get('finished_at'), 'worker_exited': row.get('worker_exited', False),
            'cancel_requested': row.get('cancel_requested', False)}


def reconcile(client, store, key, actor, job):
    if job.get('worker_exited') and job.get('status') in {'done', 'failed', 'cancelled', 'interrupted', 'rejected'}: return job
    row = platform_call(client, 'GET', '/modu/jobs/' + quote(job['operation_id'], safe=''))
    result = _job_record(row, identifier=job['id'], operation_id=job['operation_id'], architecture_id=job.get('architecture_id'))
    result['cancel_requested'] = bool(job.get('cancel_requested') or result.get('cancel_requested'))
    return store.save_job(key, actor, {**job, **result})


def reconcile_exited_generation(runtime, store, key, actor, jobs):
    for index, job in enumerate(jobs):
        if not job.get('generation') or job.get('worker_exited'): continue
        proof = runtime.exit_evidence(key, job['generation'])
        if proof and proof.get('worker_exited') and proof.get('generation') == job['generation']:
            terminal = job.get('status') in {'done', 'failed', 'cancelled', 'interrupted', 'rejected'}
            record = {**job, 'worker_exited': True, 'exit_evidence': proof}
            if not terminal:
                record.update(status='interrupted', finished_at=proof['finished_at'],
                    message='이 실행 환경의 작업 프로세스가 모두 종료됐습니다. 이전 제출 기록은 보존했고 다시 제출하지 않았습니다.',
                    submission_evidence={name: job.get(name) for name in ('status', 'platform_job_id', 'operation_id', 'message')})
            jobs[index] = store.save_job(key, actor, record)
    return jobs


@router.get('/status')
def status(request: Request):
    project, context, key, permissions = authority(request)
    runtime, store = services(request)
    state = runtime.status(key)
    binding = store.get_project(key) or {}
    receipt, binding_message = current_receipt(project, store.get_dataset(key))
    jobs = reconcile_exited_generation(runtime, store, key, context.actor_id, store.list_jobs(key))
    models = [row for row in binding.get('models', []) if receipt and row.get('transfer_id') == receipt['id']]
    architectures = [] if binding_message else binding.get('architectures', [])
    if state['state'] == 'running':
        try:
            client = client_for(runtime, key, request=request)
            if not binding_message:
                architectures = platform_call(client, 'GET', '/modu/architectures?task=' + quote((receipt or {}).get('task') or _TASKS.get(project['task'], project['task']), safe=''))
            for index, job in enumerate(jobs):
                if job.get('operation_id'): jobs[index] = reconcile(client, store, key, context.actor_id, job)
            if receipt:
                from backend.platforms.geti.model_runtime import list_platform_models
                models = adapter_call(list_platform_models, client, receipt)
            if not binding_message: store.save_project(key, {**binding, 'architectures': architectures, 'models': models})
        except (HTTPException, GetiError, ValueError, OSError) as exc:
            state = {**state, 'degraded': True, 'message': '일부 플랫폼 기록을 새로 읽지 못했습니다. 저장된 기록을 표시합니다. 잠시 뒤 다시 확인하세요.'}
    return public_data({'available': state['state'] != 'unavailable', 'runtime': state,
            'project': {'name': project['name'], 'task': project['task'], 'supported': project['task'] in _TASKS or bool(project['task'] == 'segmentation' and receipt and receipt.get('task') == 'instance_segmentation'), 'linked': bool(receipt), 'target_task_options': ['instance_segmentation'] if project['task'] == 'segmentation' else [], 'binding_state': 'stale' if binding_message else 'current' if receipt else 'unlinked', 'binding_message': binding_message},
            'dataset': receipt, 'jobs': jobs, 'models': models, 'architectures': architectures,
            'native_tasks': _NATIVE_TASKS, 'permissions': permissions})


@router.post('/start')
def start(body: StartBody, request: Request):
    project, context, key, permissions = authority(request, action='train')
    runtime, store = services(request)
    client_for(runtime, key, request=request)
    return status(request)


@router.post('/recover')
def recover(body: StartBody, request: Request):
    project, context, key, permissions = authority(request, action='train')
    runtime, store = services(request)
    try: runtime.recover(key)
    except (OSError, RuntimeError, ValueError) as exc: raise HTTPException(409, public_data(str(exc))) from exc
    reconcile_exited_generation(runtime, store, key, context.actor_id, store.list_jobs(key))
    return status(request)


@router.post('/prepare')
def prepare(body: PrepareBody, request: Request):
    project, context, key, permissions = authority(request, action='train')
    if project['task'] not in _TASKS and not (project['task'] == 'segmentation' and body.target_task == 'instance_segmentation'):
        raise HTTPException(422, '이 작업은 기존 모두비전 학습 경로에서 지원합니다. 개체 분할은 명시적으로 선택하고 벡터 정답을 준비하세요.')
    runtime, store = services(request)
    reservation = _operation(store, key, context.actor_id, body, 'prepare')
    if not reservation['is_new']: return previous_result(reservation)
    from backend.platforms.geti.dataset_transfer import prepare_dataset
    try:
        receipt = adapter_call(prepare_dataset, project, {**context.model_dump(), 'input_recipe': body.input_recipe, 'target_task': body.target_task}, client_for(runtime, key, request=request, action='train'),
                                  dataset_version_id=body.dataset_version_id, preparation_id=body.preparation_id,
                                  transfer_root=store.root / 'transfers' / key)
        store.save_dataset(key, receipt)
        binding = store.get_project(key) or {}
        store.save_project(key, {**binding, 'workspace_id': context.workspace_id, 'project_id': project['id'], 'geti_project_id': receipt['geti_project_id'], 'models': [], 'architectures': []})
        store.complete_operation(key, context.actor_id, body.operation_id, receipt)
        return public_data(receipt)
    except BaseException:
        remember_failure(store, key, context.actor_id, body.operation_id)
        raise


@router.post('/weights')
def prepare_weights(body: WeightsBody, request: Request):
    project, context, key, permissions = authority(request, action='train')
    runtime, store = services(request)
    reservation = _operation(store, key, context.actor_id, body, 'weights')
    if not reservation['is_new']: return previous_result(reservation)
    try:
        result = platform_call(client_for(runtime, key, request=request, action='train'), 'POST', '/modu/weights', json={'architecture_id': body.architecture_id}, timeout=900)
        store.complete_operation(key, context.actor_id, body.operation_id, result)
        return public_data(result)
    except BaseException:
        remember_failure(store, key, context.actor_id, body.operation_id)
        raise


@router.post('/train')
def train(body: TrainBody, request: Request):
    project, context, key, permissions = authority(request, action='train')
    runtime, store = services(request)
    receipt = receipt_for(store, key)
    from backend.platforms.geti.dataset_transfer import validate_transfer, validate_platform_dataset
    adapter_call(validate_transfer, project, receipt)
    # Include the frozen receipt identity in the operation digest. A retry after
    # another prepare can never silently train a different cohort or recipe.
    spec = {**body.model_dump(exclude={'operation_id'}), 'receipt_id': receipt['id'], 'manifest_sha256': receipt['manifest_sha256'], 'recipe_sha256': receipt.get('recipe_sha256')}
    try: reservation = store.reserve_operation(key, context.actor_id, body.operation_id, 'train', spec)
    except ValueError as exc: raise HTTPException(409, str(exc)) from exc
    child_operation = 'op_' + hashlib.sha256((key + ':' + body.operation_id).encode()).hexdigest()
    identifier = 'gt_' + hashlib.sha256((key + ':' + body.operation_id).encode()).hexdigest()[:32]
    client = client_for(runtime, key, request=request, action='train')
    if not reservation['is_new']:
        existing = store.get_job(key, identifier) or {'id': identifier, 'operation_id': child_operation, 'architecture_id': body.architecture_id}
        try: return public_data(reconcile(client, store, key, context.actor_id, existing))
        except HTTPException as exc:
            if exc.status_code == 404: raise HTTPException(409, '이전 제출의 결과를 확정할 수 없습니다. 자동 재제출하지 않았습니다.') from exc
            raise
    adapter_call(validate_platform_dataset, client, receipt)
    pending = {'id': identifier, 'operation_id': child_operation, 'architecture_id': body.architecture_id,
               'submission_id': body.operation_id, 'status': 'submission_unconfirmed', 'progress': 0, 'worker_exited': False, 'created_at': time.time(), 'receipt_id': receipt['id'], 'generation': runtime.status(key).get('generation'), 'project_root': project['project_dir']}
    store.save_job(key, context.actor_id, pending)
    activity = getattr(request.app.state, 'geti_activity', None)
    if activity is not None: activity.track(key, context.actor_id, pending)
    try:
        request_body = {'job_type': 'train', 'project_id': receipt['geti_project_id'], 'parameters': {'device': 'cpu', 'model_architecture_id': body.architecture_id}}
        if receipt.get('dataset_revision_id'): request_body['parameters']['dataset_revision_id'] = receipt['dataset_revision_id']
        row = platform_call(client, 'POST', '/modu/jobs', json={'operation_id': child_operation, 'request': request_body, 'max_epochs': body.max_epochs})
        job = store.save_job(key, context.actor_id, {**pending, **_job_record(row, identifier=identifier, operation_id=child_operation, architecture_id=body.architecture_id)})
        store.complete_operation(key, context.actor_id, body.operation_id, job)
        return public_data(job)
    except BaseException:
        remember_failure(store, key, context.actor_id, body.operation_id)
        raise


@router.get('/jobs/{job_id}')
def read_job(job_id: str, request: Request):
    project, context, key, permissions = authority(request)
    runtime, store = services(request)
    try: job = store.get_job(key, job_id)
    except ValueError as exc: raise HTTPException(404, '플랫폼 작업을 찾을 수 없습니다.') from exc
    if not job: raise HTTPException(404, '현재 프로젝트의 플랫폼 작업을 찾을 수 없습니다.')
    if runtime.status(key)['state'] != 'running': return public_data(job)
    return public_data(reconcile(client_for(runtime, key, request=request), store, key, context.actor_id, job))


@router.post('/jobs/{job_id}/cancel')
def cancel_job(job_id: str, body: StartBody, request: Request):
    project, context, key, permissions = authority(request, action='train')
    runtime, store = services(request)
    try: job = store.get_job(key, job_id)
    except ValueError as exc: raise HTTPException(404, '플랫폼 작업을 찾을 수 없습니다.') from exc
    if not job: raise HTTPException(404, '현재 프로젝트의 플랫폼 작업을 찾을 수 없습니다.')
    row = platform_call(client_for(runtime, key, request=request, action='train'), 'POST', '/modu/jobs/' + quote(job['operation_id'], safe='') + '/cancel', json={})
    result = {**job, **_job_record(row, identifier=job_id, operation_id=job['operation_id'], architecture_id=job.get('architecture_id')), 'cancel_requested': True}
    store.save_job(key, context.actor_id, result)
    return public_data(result)


@router.get('/models')
def list_models(request: Request):
    project, context, key, permissions = authority(request)
    runtime, store = services(request)
    receipt, binding_message = current_receipt(project, store.get_dataset(key))
    if not receipt: return []
    if runtime.status(key)['state'] != 'running': return public_data([row for row in (store.get_project(key) or {}).get('models', []) if row.get('transfer_id') == receipt['id']])
    from backend.platforms.geti.model_runtime import list_platform_models
    rows = adapter_call(list_platform_models, client_for(runtime, key, request=request), receipt)
    store.save_project(key, {**(store.get_project(key) or {}), 'models': rows})
    return public_data(rows)


@router.post('/predict')
def predict(body: PredictBody, request: Request):
    project, context, key, permissions = authority(request)
    runtime, store = services(request)
    receipt = receipt_for(store, key)
    path = admitted_image(project, receipt, body.image_path)
    from backend.platforms.geti.model_runtime import predict_inspection_image
    return public_data(adapter_call(predict_inspection_image, project, client_for(runtime, key, request=request), receipt, body.model_id, body.variant_id, path))


@router.post('/adopt-labels')
def adopt(body: AdoptBody, request: Request):
    project, context, key, permissions = authority(request, action='adopt')
    runtime, store = services(request)
    receipt = receipt_for(store, key)
    reservation = _operation(store, key, context.actor_id, body, 'adopt-labels')
    if not reservation['is_new']: return previous_result(reservation)
    from backend.platforms.geti.dataset_transfer import adopt_labels
    try:
        account = getattr(request.state, 'account_user', None)
        actor = account.get('username', context.actor_id) if account else context.actor_id
        result = adapter_call(adopt_labels, project, {**context.model_dump(), 'actor': actor}, client_for(runtime, key, request=request, action='adopt'), receipt, expected_revision=body.expected_revision)
        store.complete_operation(key, context.actor_id, body.operation_id, result)
        return public_data(result)
    except BaseException:
        remember_failure(store, key, context.actor_id, body.operation_id)
        raise


@router.get('/models/{model_id}/export/{variant_id}')
def export_model(model_id: str, variant_id: str, request: Request):
    project, context, key, permissions = authority(request)
    runtime, store = services(request)
    receipt = receipt_for(store, key)
    client = client_for(runtime, key, request=request)
    from backend.platforms.geti.model_runtime import export_platform_model
    result = adapter_call(export_platform_model, client, receipt, model_id, variant_id)
    content = result['data']
    if not isinstance(content, bytes): raise HTTPException(502, '플랫폼 모델 파일 형식이 올바르지 않습니다.')
    return Response(content, media_type='application/zip', headers={'Content-Disposition': 'attachment; filename="platform-model.zip"', 'Cache-Control': 'no-store', 'X-Artifact-SHA256': result['export_sha256']})
