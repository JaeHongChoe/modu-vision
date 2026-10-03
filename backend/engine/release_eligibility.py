"""Live approval checks for new central commands, separate from offline seals."""
from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def evidence_context(project):
    """Use the project's annotations even outside an HTTP request."""
    from backend.engine.annotation_storage import (
        set_request_annotation_root, reset_request_annotation_root,
        set_request_project_root, reset_request_project_root,
    )
    annotation_token = set_request_annotation_root(Path(project['annotations_dir']))
    project_token = set_request_project_root(Path(project['project_dir']))
    try:
        yield
    finally:
        reset_request_project_root(project_token)
        reset_request_annotation_root(annotation_token)


def authorize_release_action(package, project, *, action, source=None):
    """Reject stale/revoked model evidence; historical valid approvals qualify.

    Sealed runtime startup intentionally does not call this function. A new
    central stage/apply/rollback must have access to the live project evidence.
    """
    if action not in {'export', 'stage', 'apply', 'rollback'}:
        raise ValueError('Unsupported central release action')
    verify_project_context(project)
    from backend.api.routes_export import _selected_release
    from backend.engine.flow_package_runtime import verify_flow_package
    root = Path(package)
    _, checkpoints = verify_flow_package(root)
    manifest = json.loads((root / 'manifest.json').read_text(encoding='utf-8'))
    approvals = manifest.get('release', {}).get('approval_revisions')
    if (not isinstance(approvals, list) or not approvals or len(approvals) != len(checkpoints)
            or {row.get('job_id') for row in approvals} != set(checkpoints)):
        raise ValueError('Every package model requires explicit project approval')
    source = Path(source or project.get('source_dataset_dir') or '')
    if project.get('source_dataset_dir') and source.resolve() != Path(project['source_dataset_dir']).resolve():
        raise ValueError('Release source differs from the current project source')
    with evidence_context(project):
        for approval in approvals:
            job_id = approval['job_id']
            selected = _selected_release(project, source, approval.get('task'), job_id,
                                         checkpoints[job_id], approval.get('revision_id'))
            if selected is None or selected != approval:
                raise ValueError(f'Release approval is revoked, stale, or unverified for {action}: {job_id}')
    return approvals


@contextmanager
def release_authority(project, *, source=None):
    """Serialize live authorization with truth edits and approval mutations.

    Metadata's OS lock fences supported truth/label writers. The project lock
    fences approve/rollback while a new release command checks and publishes.
    External filesystem changes are detected by the final hash revalidation.
    """
    from backend.engine.dataset_metadata import metadata_transaction
    from backend.engine.runtime_process_control import runtime_state_lock
    verify_project_context(project)
    with evidence_context(project):
        with metadata_transaction(project['project_dir'], source or project['source_dataset_dir'], project['annotations_dir']):
            with runtime_state_lock(project['project_dir']):
                # The caller may have waited while another writer changed scope.
                verify_project_context(project)
                yield


class ExampleProjectRefused(ValueError):
    """An example project (synthetic data, S2-01) never authorizes an approval or a release."""


def verify_project_context(project):
    """A caller's cached project cannot authorize a newly selected source/scope, and an example project authorizes
    nothing: its results come from synthetic data and say nothing about a real process."""
    from backend.api.routes_project import _load_project
    from backend.engine.onboarding import is_example_source
    current = _load_project(Path(project['project_dir']))
    # The marker or the data itself: a project whose source is an example folder is refused even when the marker was
    # never written (an import that stopped, a closed app, a hand-edited project file).
    if (current.get('example') or project.get('example') or is_example_source(current.get('source_dataset_dir'))
            or is_example_source(project.get('source_dataset_dir'))):
        raise ExampleProjectRefused('예제 프로젝트(합성 데이터)는 품질 승인·배포 대상이 아닙니다. 실제 공정 데이터로 만든 프로젝트에서 승인하세요.')
    for key in ('id', 'source_dataset_dir', 'active_labelset_id', 'annotations_dir'):
        if current.get(key) != project.get(key):
            raise ValueError('Current project source or labelset changed; reload and re-evaluate')
