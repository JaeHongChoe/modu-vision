"""Genuine selected profile-read boundary; no supported whole Run is claimed.

Original global migration, factory, auth, store/IO, handler and ASGI execute.
A tiny synthetic checkpoint dictionary permits the original header/source
validation. An explicit provider failstop then refuses before any transport,
thread, model construction or forward. No training/model acceptance is claimed.
"""
import asyncio
import hashlib
import json
import os
from pathlib import Path

import httpx
from PIL import Image
import pytest


def _pin(path):
    def raw(s):
        return [s.st_dev, s.st_ino, s.st_mode, s.st_nlink, s.st_size,
                s.st_mtime_ns, s.st_ctime_ns, s.st_uid, s.st_gid]
    before = path.lstat()
    data = path.read_bytes()
    assert raw(before) == raw(path.lstat())
    return {'raw9': raw(before), 'sha256': hashlib.sha256(data).hexdigest()}


@pytest.mark.parametrize('generation', [False, True], ids=['nominal', 'active-generation'])
def test_original_selected_profile_read_uses_current_owned_store(generation, tmp_path, monkeypatch):
    from backend.tests.test_global_migration import owned
    from backend.engine.global_migration import preview, apply
    from backend.engine.global_store_paths import resolve_store_path
    from backend.remote.profiles import ComputeProfile, ProfileStore, default_profile_path

    monkeypatch.chdir(tmp_path)
    root, scopes, *_ = owned(tmp_path)
    monkeypatch.setenv('VISION_AI_STUDIO_USER_DATA_DIR', str(root))
    monkeypatch.setenv('VISION_RESOURCE_LEASE_DB', str(root / scopes['leases']))
    profile = ComputeProfile(id='source-only-profile', name='Source-only read fixture',
        ssh_target='fixture-only', ssh_port=22, remote_root='/source-only-not-contacted',
        runtime_kind='docker', runtime_value='fixture-only-image', gpu_selector='2')
    nominal = default_profile_path()
    ProfileStore().save(profile)
    if generation:
        plan = preview(root)
        assert plan['can_apply']
        assert apply(root, expected_source_sha256=plan['source_sha256'])['status'] == 'applied'
    actual = resolve_store_path(nominal)
    assert (actual != nominal) is generation
    assert ProfileStore().get(profile.id).model_dump() == profile.model_dump()

    from backend import main
    from backend.engine.application_launch_handshake import BackendWorkAdmission
    from backend.engine.original_request_activity import OriginalRequestActivityObserver
    from backend.tests.test_original_owned_account_request_lifetime import _owned_projects
    from backend.tests.test_original_owned_flow_lifetime import _login_current, _no_whole_credit
    from backend.api import routes_flowchart as flows
    from backend.engine.flowchart_engine import get_single_segmentation_flowchart
    from backend.remote import operations
    from backend.remote.coordinator import ArtifactValidationError
    from backend.engine.original_owned_selected_compute_request import observe_original_selected_compute
    import torch

    stop_calls = []
    @observe_original_selected_compute('run')
    def source_only_failstop(profile, project, pipeline, checkpoints, image, image_id, **kwargs):
        # Explicit provider boundary; original handler and profile read stay
        # intact. Nothing can create transport, heartbeat or worker threads.
        stop_calls.append((profile.id, len(checkpoints)))
        raise ArtifactValidationError('source-only failstop before transport or thread')
    monkeypatch.setattr(operations, 'run_verified_flowchart_on_compute', source_only_failstop)

    observer = OriginalRequestActivityObserver()
    admission = BackendWorkAdmission(activity_observer=observer)
    monkeypatch.setattr(main, 'backend_work_admission', lambda: admission)
    app = main.create_app(project_dir=str(root / 'projects'), shared_auth_dir=str(root / 'auth'))
    _, (project,) = _owned_projects(app, observer, 'Original profile boundary')
    project_root = Path(project['project_dir'])
    source = Path(project['dataset_dir']).resolve()
    project.update(task='segmentation', source_dataset_dir=str(source))
    flows._write_json(project_root / 'project.json', project)
    job = 'job_profile_boundary'
    graph = get_single_segmentation_flowchart(job)
    seed = flows._save_version(graph, 'segmentation', str(source), project_root)
    recipe = flows._recipe_file('segmentation', str(source), project_root)
    flows._write_pipeline(recipe, graph)
    image = source / 'profile-boundary.png'
    with Image.new('RGB', (8, 6), (30, 70, 90)) as pixels:
        pixels.save(image)
    directory = Path(project['models_dir']) / job
    directory.mkdir()
    checkpoint = directory / 'best_model.pt'
    # Synthetic provenance-format fixture data, never trained model evidence.
    # No module, tensor, forward, model loader or device runtime is constructed.
    torch.save({'task': 'segmentation', 'model_state_dict': {}}, checkpoint)
    metadata = directory / 'model_meta.json'
    metadata.write_text(json.dumps({'task': 'segmentation', 'dataset_path': str(source),
        'checkpoint_sha256': hashlib.sha256(checkpoint.read_bytes()).hexdigest()}))
    from backend.api import routes_dataset
    from backend.api.routes_evaluation import fingerprint_dataset
    fingerprint = fingerprint_dataset(source, studio_root=routes_dataset.STUDIO_ANNOTATIONS_DIR,
                                      split_manifest=routes_dataset._split_manifest_file(source))
    receipt = directory / 'job_receipt.json'
    receipt.write_text(json.dumps({'status': 'completed', 'task': 'segmentation',
        'source_dataset_path': str(source), 'dataset_fingerprint': fingerprint}))
    protected = [actual, image, recipe, project_root / 'project.json',
                 flows._version_dir(project_root) / (seed + '.json'), checkpoint, metadata, receipt]
    basis = {str(path): _pin(path) for path in protected}

    async def request():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://owned.invalid') as client:
            headers = await _login_current(client, app, project)
            response = await client.post('/api/flowchart/run', headers=headers, json={
                'pipeline': graph.model_dump(), 'image_path': str(image), 'image_id': None,
                'project_id': project['id'], 'execution_target': 'selected_compute',
                'device': 'cuda:0', 'compute_profile_id': profile.id})
            assert response.status_code == 502, response.text
            assert response.json() == {'detail': 'Selected flow execution could not be verified: source-only failstop before transport or thread'}
            assert response.headers['x-vision-context'] == headers['x-vision-context']
    asyncio.run(request())
    assert stop_calls == [(profile.id, 1)]
    assert {str(path): _pin(path) for path in protected} == basis
    records = observer.snapshot()['records']
    assert len(records) == 3 and all(row['released'] and row['normal_return'] and row['terminal_forwarded'] for row in records)
    selected = admission._flow_execution_requests.records[-1].selected
    assert selected.profile_store.path == actual
    admission.close()
    drain = admission.drain(.03)
    reasons = list(observer._failures)
    output = os.environ.get('MODU_PROFILE_REPRO_EVIDENCE_DIR')
    if output:
        with (Path(output) / ('active-generation.json' if generation else 'nominal.json')).open('x') as f:
            json.dump({'case': 'active-generation' if generation else 'nominal',
                'HTTP_status': 502, 'explicit_provider_failstop_before_transport_or_thread': True,
                'synthetic_checkpoint_dictionary_only': True,
                'actual_profile_path_differs_from_nominal': actual != nominal,
                'profile_bytes_and_other_selected_inputs_unchanged': True,
                'original_reasons': reasons, 'drain': drain,
                'full_Run_native_model_GPU_transport_acceptance': False}, f, indent=2)
            f.write('\n')
    # The explicit provider failstop keeps the original whole-execution owner
    # refused. Only the valid original profile-read boundary is claimed.
    assert drain['status'] == 'refused'
    _no_whole_credit(observer)
    assert 'owned_flow_selected_compute_profile_entry' not in reasons
    assert reasons == ['owned_flow_selected_compute_run_entry',
        'owned_flow_selected_compute_run_failed', 'owned_flow_execution_handler_failed',
        'owned_flow_unconfirmed_execution_lifetime']


@pytest.mark.parametrize('change', [
    'foreign-file', 'retired-generation', 'resolver-alias', 'malformed-pointer', 'linked-pointer',
])
def test_current_profile_projection_refuses_unproved_paths(change, tmp_path, monkeypatch):
    from backend.tests.test_global_migration import owned
    from backend.engine.global_migration import preview, apply
    from backend.engine import global_store_paths
    from backend.remote import profiles
    from backend.engine.original_owned_selected_compute_request import _current_profile_path

    monkeypatch.chdir(tmp_path)
    root, *_ = owned(tmp_path)
    monkeypatch.setenv('VISION_AI_STUDIO_USER_DATA_DIR', str(root))
    nominal = profiles.default_profile_path()
    plan = preview(root)
    assert apply(root, expected_source_sha256=plan['source_sha256'])['status'] == 'applied'
    store = profiles.ProfileStore()
    assert store.path == global_store_paths.resolve_store_path(nominal) != nominal
    assert _current_profile_path(store)
    pointer = root / global_store_paths.POINTER_FILE
    if change == 'foreign-file':
        foreign = tmp_path / 'foreign' / 'compute_profiles.json'
        foreign.parent.mkdir()
        foreign.write_bytes(store.path.read_bytes())
        store = profiles.ProfileStore(foreign)
    elif change == 'retired-generation':
        retired = root / '.global-generations' / ('f' * 32) / 'compute_profiles.json'
        retired.parent.mkdir()
        retired.write_bytes(store.path.read_bytes())
        store = profiles.ProfileStore(retired)
    elif change == 'resolver-alias':
        # A late equivalent delegate must not become the original resolver.
        original = profiles.resolve_store_path
        monkeypatch.setattr(profiles, 'resolve_store_path', lambda path: original(path))
    elif change == 'malformed-pointer':
        pointer.write_text('{"schema_version":1}')
    elif change == 'linked-pointer':
        retained = root / 'pointer-copy.json'
        retained.write_bytes(pointer.read_bytes())
        pointer.unlink()
        pointer.symlink_to(retained)
    assert not _current_profile_path(store)
