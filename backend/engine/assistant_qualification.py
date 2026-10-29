"""Authenticate a narrowly scoped, completed evaluation step for policy qualification."""
from fastapi import HTTPException


def authorized_step(request,project,task_id,step_id,evaluation_id,action):
    from backend.api.routes_assistant_tasks import _scope
    from backend.engine.assistant_tasks import TaskError, EXECUTION_POLICY_VERSION
    scope=_scope(request)
    try:task=request.app.state.assistant_tasks.read(scope,task_id)
    except TaskError as exc:raise HTTPException(exc.status_code,str(exc)) from exc
    grant=task.get('authorization')
    if (not grant or grant.get('scope')!=scope.document() or grant.get('policy_version')!=EXECUTION_POLICY_VERSION
            or grant.get('plan_sha256')!=task.get('plan_sha256')
            or task.get('status') not in {'running','completed','ready'}):
        raise HTTPException(403,'An active explicit execution authorization is required for policy qualification')
    step=next((row for row in task['steps'] if row['id']==step_id),None)
    allowed=grant.get('allowed_steps')
    if (step is None or step.get('action')!=action or step.get('status')!='completed'
            or allowed is not None and step_id not in allowed):
        raise HTTPException(403,'Qualification must follow its exact completed authorized evaluation step')
    # The separately persisted quality observation may change after this call;
    # it never changes the immutable execution evidence or its idempotence key.
    receipt={key:value for key,value in (step.get('receipt') or {}).items() if key!='quality_assessment'}
    if (receipt.get('evaluation_id')!=evaluation_id or receipt.get('project_id')!=project['id']
            or receipt.get('source_dataset_path')!=project['source_dataset_dir']
            or receipt.get('labelset_id')!=project.get('active_labelset_id','default')
            or action=='evaluate_model' and step['args'].get('job_id') not in (None,receipt.get('job_id'))):
        raise HTTPException(409,'Evaluation receipt does not match the authorized project, labels and step')
    # Stable authority excludes changing task revision so retries after later steps are idempotent.
    return {'kind':'assistant_task','task_id':task_id,'step_id':step_id,'plan_sha256':grant['plan_sha256'],
            'execution_policy_version':grant['policy_version'],'source':grant['source'],'scope':scope.document(),
            'authorized_at':grant['authorized_at'],'evaluation_id':evaluation_id,'receipt':receipt}
