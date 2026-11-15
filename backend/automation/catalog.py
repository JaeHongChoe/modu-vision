"""Describe registered HTTP contracts without resolving any project or executing work.

The explicit assistant policy is deliberately narrower than CLI discoverability.
A task mapping suggests an existing immutable-plan action; it is never a grant.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
from threading import RLock

from fastapi import HTTPException, routing
from starlette.convertors import CONVERTOR_TYPES
from backend.engine.assistant_tasks import ACTIONS

CATALOG_PATH = '/api/automation/catalog'
HTTP_METHODS = frozenset(('get', 'post', 'put', 'patch', 'delete', 'head', 'options', 'trace'))
SAFE_OBSERVATIONS = frozenset((
    'GET /health',
    'GET /api/automation/catalog',
    'GET /api/project/labelsets',
    'GET /api/training/status',
    'GET /api/training/queue',
    'GET /api/training/jobs',
    'GET /api/training-workspace/tasks',
    'GET /api/provenance/impact',
    'GET /api/models/capabilities',
    'GET /api/label-suggestions/stats',
))
TASK_ACTIONS = {
    'POST /api/project/create': 'create_project',
    'POST /api/dataset/import': 'import_dataset',
    'POST /api/dataset/split': 'save_split',
    'POST /api/training/input-preparation/preview': 'configure_input_recipe',
    'POST /api/label-suggestions/generate': 'generate_label_drafts',
    'POST /api/label-suggestions/batches': 'generate_label_drafts',
    'POST /api/data-workbench/label-quality-analyses': 'analyze_label_quality',
    'POST /api/training/start': 'start_training',
    'GET /api/evaluation/results': 'evaluate_model',
    'POST /api/flowchart/pipeline': 'save_flow',
    'POST /api/flow-evaluations': 'evaluate_flow',
    'POST /api/inspections/runs': 'inspect_test',
    'POST /api/report/export': 'export_report',
}
# These exact installed handlers consume Request.stream() (or a middleware
# form) directly, so FastAPI cannot infer their requestBody. This metadata does
# not add body requirements to other writes or change any handler policy.
def _json_body(schema, maximum):
    return {'required': True, 'content': {'application/json': {'schema': schema}},
            'x-max-bytes': maximum}


_BODY_OVERRIDES = {
    'PUT /api/artifacts/uploads/{upload_id}': {
        'required': True, 'content': {'application/octet-stream': {'schema': {
            'type': 'string', 'format': 'binary', 'maxLength': 4194304}}}},
    'POST /api/assistant-tasks': _json_body({
        'type': 'object', 'additionalProperties': False,
        'required': ['goal', 'binding', 'steps'], 'properties': {
            'goal': {'type': 'string', 'maxLength': 4096},
            'binding': {'type': 'string', 'maxLength': 16384},
            'steps': {'type': 'array', 'minItems': 1, 'maxItems': 24, 'items': {
                'type': 'object', 'additionalProperties': False,
                'required': ['action', 'args', 'label'], 'properties': {
                    'action': {'type': 'string', 'enum': sorted(ACTIONS)},
                    'args': {'type': 'object'}, 'label': {'type': 'string', 'maxLength': 160}}}}}}, 65536),
    'POST /api/assistant-tasks/{task_id}/events': _json_body({
        'type': 'object', 'required': ['type', 'binding', 'expected_revision'],
        'description': 'An existing task event. The server validates event-specific fields and authority.'}, 65536),
    'POST /api/assistant-tasks/{task_id}/quality': _json_body({
        'type': 'object', 'additionalProperties': False,
        'required': ['binding', 'expected_revision', 'step_id', 'attempt_id', 'assessment_id'],
        'properties': {'binding': {'type': 'string'}, 'expected_revision': {'type': 'integer', 'minimum': 1},
            'step_id': {'type': 'string'}, 'attempt_id': {'type': 'string'}, 'assessment_id': {'type': 'string'}}}, 65536),
    'POST /api/local-host/remote/login': _json_body({
        'type': 'object', 'required': ['server_url', 'username', 'password'], 'properties': {
            'server_url': {'type': 'string'}, 'username': {'type': 'string', 'minLength': 3, 'maxLength': 80},
            'password': {'type': 'string', 'minLength': 12, 'maxLength': 1024, 'writeOnly': True}}}, 16384),
    'POST /api/local-host/remote/{connection_id}/context': _json_body({
        'type': 'object', 'required': ['project_id'], 'properties': {
            'project_id': {'type': 'string'}, 'context': {'type': ['object', 'null']}}}, 16384),
    'POST /api/local-host/project-assets/download': {
        'required': True, 'x-max-bytes': 2048,
        'content': {'application/x-www-form-urlencoded': {'schema': {
            'type': 'object', 'additionalProperties': False,
            'required': ['ticket', 'csrf_token', 'kind', 'export_id'], 'properties': {
                'ticket': {'type': 'string', 'writeOnly': True},
                'csrf_token': {'type': 'string', 'writeOnly': True},
                'kind': {'type': 'string', 'enum': ['formats', 'masks']},
                'export_id': {'type': 'string'}}}}}},
}
for _verb in ('POST', 'PUT', 'PATCH', 'DELETE'):
    _BODY_OVERRIDES[_verb + ' /api/local-host/remote/{connection_id}/api/{path}'] = {
        'required': False, 'x-max-bytes': 8388608,
        'description': 'Optional forwarded body; the existing remote endpoint validates its business schema.',
        'content': {
            'application/json': {'schema': {}},
            'application/octet-stream': {'schema': {'type': 'string', 'format': 'binary', 'maxLength': 8388608}},
            'multipart/form-data': {'schema': {'type': 'object'}},
            'application/x-www-form-urlencoded': {'schema': {'type': 'object'}}}}


def _path_converters(app):
    """Read converter instances from installed effective routes, including lazy prefixes."""
    result = {}
    if hasattr(routing, 'iter_route_contexts'):
        routes = ((context.path_format, context.methods, context._effective_route)
                  for context in routing.iter_route_contexts(app.routes)
                  if isinstance(context.original_route, routing.APIRoute))
    else:
        routes = ((route.path_format, route.methods, route) for route in app.routes
                  if isinstance(route, routing.APIRoute))
    for path, methods, route in routes:
        converters = {name: kind for name, instance in route.param_convertors.items()
                      for kind, registered in CONVERTOR_TYPES.items() if instance is registered}
        for method in methods or ():
            result[method + ' ' + path] = converters
    return result


_SCHEMA_LOCK = RLock()


def is_catalog_metadata(path: str, method: str | None) -> bool:
    """Only this exact metadata GET may bypass project/storage selection."""
    return method == 'GET' and path == CATALOG_PATH


def _referenced_schemas(value, schemas):
    """Return only the transitive component-schema closure, including cycles."""
    selected = {}
    pending = [value]
    prefix = '#/components/schemas/'
    while pending:
        node = pending.pop()
        if isinstance(node, dict):
            ref = node.get('$ref')
            if isinstance(ref, str) and ref.startswith(prefix):
                name = ref[len(prefix):].replace('~1', '/').replace('~0', '~')
                if name in schemas and name not in selected:
                    selected[name] = deepcopy(schemas[name])
                    pending.append(schemas[name])
            pending.extend(node.values())
        elif isinstance(node, list):
            pending.extend(node)
    return dict(sorted(selected.items()))


def operation_catalog(app, *, group=None, operation=None, offset=0, limit=100, schemas=None):
    # FastAPI caches OpenAPI, but native-host routes can be installed after an
    # earlier metadata request. Regenerate through the app's own implementation:
    # route walking alone omits lazy/nested included routers on newer FastAPI.
    with _SCHEMA_LOCK:
        app.openapi_schema = None
        document = app.openapi()
        converters = _path_converters(app)
    rows = []
    definitions = {}
    for path, path_item in document.get('paths', {}).items():
        if not (path.startswith('/api/') or path == '/health'):
            continue
        for method, definition in path_item.items():
            if method not in HTTP_METHODS:
                continue
            identifier = method.upper() + ' ' + path
            tags = definition.get('tags') or []
            selected_group = tags[0] if tags else ('health' if path == '/health' else path.split('/')[2])
            safe = identifier in SAFE_OBSERVATIONS
            task_action = TASK_ACTIONS.get(identifier)
            assistant = {'mode': 'read' if safe else 'task' if task_action else 'manual'}
            if task_action:
                assistant['task_action'] = task_action
            rows.append({
                'id': identifier, 'method': method.upper(), 'path': path,
                'group': selected_group, 'summary': definition.get('summary') or definition.get('operationId') or identifier,
                'transport': 'native' if path.startswith('/api/local-host/') else 'http',
                'effect': 'read' if safe else 'execute', 'assistant': assistant,
            })
            definitions[identifier] = (path_item, definition)
    rows.sort(key=lambda row: row['id'])
    groups = Counter(row['group'] for row in rows)
    if group is not None and group not in groups:
        raise HTTPException(404, 'Operation group not found')
    if operation is not None and operation not in definitions:
        raise HTTPException(404, 'Operation not found')
    rows = [row for row in rows if (group is None or row['group'] == group)
            and (operation is None or row['id'] == operation)]
    if operation is not None and not rows:
        raise HTTPException(404, 'Operation not found in group')
    total = len(rows)
    page = rows[offset:offset + limit]
    details = []
    for row in page:
        path_item, definition = definitions[row['id']]
        detail = {
            'input': {'parameters': deepcopy([*path_item.get('parameters', []), *definition.get('parameters', [])])},
            'responses': deepcopy(definition.get('responses', {})),
        }
        detail['input']['path_converters'] = converters.get(row['id'], {})
        body = definition.get('requestBody', _BODY_OVERRIDES.get(row['id']))
        if body is not None:
            detail['input']['request_body'] = deepcopy(body)
        details.append(detail)
        if operation is not None:
            row.update(detail)
    include_schemas = schemas if schemas is not None else operation is not None
    components = _referenced_schemas(details, document.get('components', {}).get('schemas', {})) if include_schemas else {}
    return {
        'schema': 'StudioOperations/v1', 'operations': page,
        'components': {'schemas': components},
        'groups': [{'name': name, 'count': count} for name, count in sorted(groups.items())],
        'total': total, 'next_offset': offset + len(page) if offset + len(page) < total else None,
        'server_version': document.get('info', {}).get('version', app.version),
    }
