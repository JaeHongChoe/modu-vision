"""Reviewed overlay importing the complete immutable Geti application."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, suppress
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import signal
import sys
import threading
import time
from typing import Annotated
from uuid import UUID, uuid4

SOURCE = Path(os.environ['MODU_GETI_SOURCE_ROOT']).resolve()
sys.path.insert(0, str(SOURCE / 'application/backend'))
sys.path.insert(0, str(SOURCE / 'library/src'))
from network_guard import install, public_weight_download
install()

from fastapi import Depends, HTTPException, UploadFile, File
from hypercorn.asyncio import serve
from hypercorn.config import Config
from pydantic import BaseModel, ConfigDict, Field
from starlette.responses import JSONResponse
from app.api.dependencies import get_data_dir, get_dataset_view_service, get_job_dir, get_job_queue, get_project_service, get_system_service, get_training_configuration_service, get_inference_server, get_model_service
from app.api.routers.jobs import _build_training_job
from app.api.schemas.jobs import JobView
from app.api.schemas.jobs.training import TrainingRequest
from app import settings as settings_module
# The reviewed launcher supplies every setting. Never read a source checkout's
# incidental .env or a user's home credentials.
settings_module.Settings.model_config['env_file'] = None
settings_module.get_settings.cache_clear()
from app.main import create_app
from app.models.model_manifest import DirectLinkPretrainedWeights
from app.services.base_weights_service import BaseWeightsService
from app.services.model_manifest_service import ModelManifestService
from correlation import CorrelationStore
from image_input import decode_prediction_image
from worker_status import WorkerRegistry
from app.core.jobs.exec.process_run import ProcessRun

SECRET = os.environ['MODU_GETI_SECRET']
DATA_DIR = Path(os.environ['DATA_DIR'])
DATA_DIR.mkdir(parents=True, exist_ok=True)
ledger = CorrelationStore(DATA_DIR / 'modu-submissions.sqlite3')
app = create_app()
submission_lock = asyncio.Lock()
workers = WorkerRegistry()
original_runner_start = ProcessRun.start
def start_observed_runner(runner):
    result = original_runner_start(runner)
    workers.register(runner._job.id, runner._proc)
    return result
ProcessRun.start = start_observed_runner


class SecretGuard:
    def __init__(self, app): self.app = app
    async def __call__(self, scope, receive, send):
        if scope['type'] in {'http', 'websocket'}:
            supplied = dict(scope.get('headers', [])).get(b'x-modu-geti-secret', b'')
            if not hmac.compare_digest(supplied, SECRET.encode()):
                if scope['type'] == 'websocket': await send({'type': 'websocket.close', 'code': 1008})
                else: await JSONResponse({'detail': 'Owned gateway authorization required'}, status_code=403)(scope, receive, send)
                return
        return await self.app(scope, receive, send)


class MutationCommitGuard:
    """Do not acknowledge JSON writes before upstream yield dependencies commit.

    Upstream's DB session commits during dependency teardown, after response
    transmission. Parent shutdown/response loss must not turn an acknowledged
    created project into a rolled-back project. Streaming reads stay streamed.
    """
    def __init__(self, app): self.app = app
    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http' or scope.get('method') not in {'POST', 'PUT', 'PATCH', 'DELETE'}:
            return await self.app(scope, receive, send)
        pending = []
        is_json = False
        async def committed_send(message):
            nonlocal is_json
            if message['type'] == 'http.response.start':
                is_json = b'json' in dict(message.get('headers', [])).get(b'content-type', b'')
            if is_json: pending.append(message)
            else: await send(message)
        await self.app(scope, receive, committed_send)
        for message in pending: await send(message)


app.add_middleware(MutationCommitGuard)
app.add_middleware(SecretGuard)


def supported_manifest(architecture_id):
    # Static audited manifests only. Never accept synthetic timm/gated/AGPL choices.
    manifest = ModelManifestService.get_model_manifests().get(architecture_id)
    if manifest is None or not isinstance(manifest.pretrained_weights, DirectLinkPretrainedWeights) or manifest.support_status.value == 'obsolete' or manifest.license.name.lower() not in {'apache 2.0', 'apache-2.0', 'mit', 'bsd-3-clause', 'bsd 3-clause'}:
        raise HTTPException(422, 'Architecture requires an unsupported or separately reviewed model preparation')
    return manifest


def weights_ready(manifest):
    try:
        BaseWeightsService(DATA_DIR).get_local_weights_path(manifest.task, manifest.id, allow_download=False)
        return True
    except (FileNotFoundError, ValueError): return False


@app.get('/modu/architectures')
def architectures(task: str):
    rows = []
    for manifest in ModelManifestService.get_model_manifests().values():
        if manifest.task.value != task: continue
        try:
            supported_manifest(manifest.id)
            supported = True
        except HTTPException: supported = False
        rows.append({'id': manifest.id, 'name': manifest.name, 'task': manifest.task.value, 'license': manifest.license.model_dump(),
                     'supported': supported, 'weights_ready': weights_ready(manifest) if supported else False})
    return rows


class WeightsBody(BaseModel):
    model_config = ConfigDict(extra='forbid')
    architecture_id: str = Field(min_length=1, max_length=160)


@app.post('/modu/weights')
def prepare_weights(body: WeightsBody):
    manifest = supported_manifest(body.architecture_id)
    service = BaseWeightsService(DATA_DIR)
    if not weights_ready(manifest):
        weights = manifest.pretrained_weights
        urls = [weights.url] + ([weights.mirror_url] if weights.mirror_url else [])
        with public_weight_download(urls):
            service.get_local_weights_path(manifest.task, manifest.id, allow_download=True)
    path = service.get_local_weights_path(manifest.task, manifest.id, allow_download=False)
    return {'ready': True, 'architecture_id': manifest.id, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
            'provenance': {'source': manifest.pretrained_weights.url, 'license': manifest.license.model_dump()}}


class SubmitBody(BaseModel):
    model_config = ConfigDict(extra='forbid')
    operation_id: str = Field(pattern=r'^[A-Za-z0-9_.:-]{1,128}$')
    request: TrainingRequest
    max_epochs: int = Field(ge=1, le=10000)


def snapshot(operation_id, queue):
    saved = ledger.get(operation_id)
    if saved is None: raise HTTPException(404, 'Owned job correlation not found')
    job = queue.get(UUID(saved['job_id']))
    if job is not None:
        data = JobView.of(job).model_dump(mode='json')
        data['worker_exited'] = saved.get('worker_exited', False) or workers.exited(saved['job_id'])
        return ledger.save(operation_id, data)
    if saved['status'].lower() not in {'done', 'failed', 'cancelled', 'rejected'}:
        return {**saved, 'status': 'submission_unconfirmed', 'message': 'Durable reservation exists; worker state after restart is unconfirmed. This operation will not be resubmitted.'}
    return saved


@app.post('/modu/jobs')
async def submit(body: SubmitBody, queue=Depends(get_job_queue), job_dir=Depends(get_job_dir), data_dir=Depends(get_data_dir),
                 projects=Depends(get_project_service), system=Depends(get_system_service), views=Depends(get_dataset_view_service),
                 configurations=Depends(get_training_configuration_service)):
    async with submission_lock:
        try: record = ledger.reserve(body.operation_id, body.model_dump(mode='json', exclude={'operation_id'}))
        except ValueError as exc: raise HTTPException(409, str(exc)) from exc
        if not record['is_new']: return snapshot(body.operation_id, queue)
        try:
            manifest = supported_manifest(body.request.parameters.model_architecture_id)
            if not weights_ready(manifest): raise HTTPException(409, 'Prepare checksum-verified model weights before training')
            if queue.list_non_completed(): raise HTTPException(409, 'Another owned training job is still active')
            config = configurations.get_by_model_architecture(project_id=body.request.project_id, model_architecture_id=manifest.id)
            config.apply_updates({'training.max_epochs': body.max_epochs})
            configurations.update(project_id=body.request.project_id, model_architecture_id=manifest.id, training_configuration=config)
            job = _build_training_job(UUID(record['job_id']), body.request, job_dir, data_dir, projects, system, views)
        except BaseException:
            ledger.save(body.operation_id, {'status': 'rejected', 'message': 'Submission validation failed before queue publication'})
            raise
        # Correlation is durably committed above. Any exception/crash from this
        # point remains ambiguous; this same operation can NEVER publish twice.
        await queue.submit(job)
        return snapshot(body.operation_id, queue)


@app.get('/modu/jobs/{operation_id}')
def get_owned_job(operation_id: str, queue=Depends(get_job_queue)):
    return snapshot(operation_id, queue)


@app.post('/modu/jobs/{operation_id}/cancel')
def cancel_owned_job(operation_id: str, queue=Depends(get_job_queue)):
    saved = ledger.get(operation_id)
    if saved is None: raise HTTPException(404, 'Owned job correlation not found')
    queue.cancel(UUID(saved['job_id']))
    record = snapshot(operation_id, queue)
    return {**record, 'cancel_requested': True}


@app.post('/modu/projects/{project_id}/models/{model_id}/variants/{variant_id}/predict')
async def predict_upload(project_id: UUID, model_id: UUID, variant_id: UUID, file: Annotated[UploadFile, File()],
                         projects=Depends(get_project_service), models=Depends(get_model_service),
                         inference=Depends(get_inference_server), system=Depends(get_system_service)):
    project = projects.get_project_by_id(project_id)
    models.get_model(project_id=project_id, model_id=model_id)
    variants = models.get_model_variants(project_id=project_id, model_id=model_id)
    variant = next((row for row in variants if row.id == variant_id), None)
    if variant is None: raise HTTPException(404, 'Model variant does not belong to the owned project/model')
    if variant.format.value not in {'openvino', 'onnx'}: raise HTTPException(422, 'Select an OpenVINO or ONNX inference variant')
    try:
        payload = await file.read(32 * 1024 * 1024 + 1)
        data = decode_prediction_image(payload)
    except ValueError as exc: raise HTTPException(422, str(exc)) from exc
    finally: await file.close()
    from app.models import BatchInferenceInput, BatchInferenceResult, BatchInferencePrediction, BatchInferenceMedia
    media_id = uuid4()
    result = await asyncio.to_thread(inference.infer_batch, project_id=project_id, model_id=model_id,
                                    model_variant_id=variant_id, device=system.inference_device('cpu'),
                                    labels=project.task.labels, inputs=[BatchInferenceInput(media_id=media_id, data=data)])
    # The synthetic media identity exists only in this response. No original,
    # annotation, subset or frozen dataset revision is changed by inspection.
    return BatchInferenceResult(predictions=[BatchInferencePrediction(media=BatchInferenceMedia(id=media_id), prediction=result[(media_id, None)])])


original_lifespan = app.router.lifespan_context
@asynccontextmanager
async def owned_lifespan(application):
    async with original_lifespan(application):
        async def persist():
            while True:
                for row in ledger.all(): snapshot(row['operation_id'], application.state.job_queue)
                await asyncio.sleep(0.2)
        task = asyncio.create_task(persist())
        try: yield
        finally:
            for row in ledger.all(): snapshot(row['operation_id'], application.state.job_queue)
            task.cancel()
            with suppress(asyncio.CancelledError): await task
app.router.lifespan_context = owned_lifespan


async def main():
    config = Config()
    config.bind = ['127.0.0.1:' + os.environ['PORT']]
    config.accesslog = '-'
    config.errorlog = '-'
    config.graceful_timeout = 10
    shutdown = asyncio.Event()
    loop = asyncio.get_running_loop()
    if os.name != 'nt':
        for sig in (signal.SIGINT, signal.SIGTERM): loop.add_signal_handler(sig, shutdown.set)
    if os.environ.get('MODU_GETI_PARENT_PIPE') == '1':
        def watch_parent():
            while sys.stdin.buffer.read(1): pass
            loop.call_soon_threadsafe(shutdown.set)
            # Lifespan may be waiting for an active trainer. Bound orphan
            # cleanup to this fresh owned session, including scheduler workers.
            time.sleep(15)
            if os.name != 'nt' and os.getpgrp() == os.getpid():
                os.killpg(os.getpid(), signal.SIGKILL)
            else: os._exit(0)
        threading.Thread(target=watch_parent, daemon=True).start()
    await serve(app, config, shutdown_trigger=shutdown.wait)


if __name__ == '__main__': asyncio.run(main())
