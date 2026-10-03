"""Generate TypeScript types for the backend API from its pydantic models (S1-10).

Usage: python scripts/generate_api_types.py [--check] [--file PATH]

Writes src/renderer/services/generated/apiTypes.ts: one type per request body and response model of every route (and
the models they refer to), with ApiRequestBody and ApiResponseBody maps keyed by "METHOD /path". With --check it writes
nothing and exits 1 when the committed file differs, so CI fails when a backend model changes without the renderer
types. The schemas come from pydantic itself (the same pydantic version in development and CI), not from FastAPI's
OpenAPI document, whose details differ between FastAPI versions. The app is imported with its data stores pointed at a
temporary folder, so generating never opens a user store.
"""
from __future__ import annotations

import argparse
import inspect
import json
import os
import re
import sys
import tempfile
import types
import typing
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / 'src' / 'renderer' / 'services' / 'generated' / 'apiTypes.ts'
_STORES = ('VISION_AI_STUDIO_USER_DATA_DIR', 'MODU_FLOW_TEMPLATE_DIR', 'MODU_SPLIT_MANIFEST_DIR', 'MODU_THUMBNAIL_CACHE_DIR')
_IDENTIFIER = re.compile(r'[^A-Za-z0-9_]')
_KEY = re.compile(r'[A-Za-z_$][A-Za-z0-9_$]*\Z')


def type_name(name: str) -> str:
    cleaned = _IDENTIFIER.sub('_', name)
    return cleaned if not cleaned[:1].isdigit() else f'_{cleaned}'


def ts_type(schema: dict, indent: str = '') -> str:
    """The TypeScript type of one JSON schema (pydantic's dialect); unknown shapes are `unknown`, never `any`."""
    if not isinstance(schema, dict) or not schema:
        return 'unknown'
    if '$ref' in schema:
        return type_name(schema['$ref'].rsplit('/', 1)[-1])
    if 'const' in schema:
        return json.dumps(schema['const'], ensure_ascii=False)
    if 'enum' in schema:
        return ' | '.join(json.dumps(value, ensure_ascii=False) for value in schema['enum']) or 'never'
    for key in ('anyOf', 'oneOf'):
        if key in schema:
            parts = list(dict.fromkeys(ts_type(part, indent) for part in schema[key]))
            return ' | '.join(parts) if len(parts) > 1 else parts[0]
    if 'allOf' in schema:
        parts = list(dict.fromkeys(ts_type(part, indent) for part in schema['allOf']))
        return ' & '.join(parts) if len(parts) > 1 else parts[0]
    kind = schema.get('type')
    if isinstance(kind, list):
        return ' | '.join(ts_type({**schema, 'type': item}, indent) for item in kind)
    if kind == 'string':
        return 'string'
    if kind in ('integer', 'number'):
        return 'number'
    if kind == 'boolean':
        return 'boolean'
    if kind == 'null':
        return 'null'
    if kind == 'array':
        if isinstance(schema.get('prefixItems'), list):
            return '[' + ', '.join(ts_type(item, indent) for item in schema['prefixItems']) + ']'
        inner = ts_type(schema.get('items', {}), indent)
        return f'Array<{inner}>'
    if kind == 'object' or 'properties' in schema:
        return ts_object(schema, indent)
    return 'unknown'


def ts_object(schema: dict, indent: str = '') -> str:
    properties = schema.get('properties') or {}
    required = set(schema.get('required') or [])
    extra = schema.get('additionalProperties')
    inner = indent + '  '
    lines = []
    for name, value in properties.items():
        key = name if _KEY.match(name) else json.dumps(name, ensure_ascii=False)
        lines.append(f"{inner}{key}{'' if name in required else '?'}: {ts_type(value, inner)};")
    if extra is True or (isinstance(extra, dict) and not properties):
        value = ts_type(extra, inner) if isinstance(extra, dict) else 'unknown'
        lines.append(f'{inner}[key: string]: {value};' if not properties else f'{inner}[key: string]: unknown;')
    elif isinstance(extra, dict) and properties:
        lines.append(f'{inner}[key: string]: unknown;')
    if not lines:
        return '{ [key: string]: unknown }' if extra is not False else 'Record<string, never>'
    return '{\n' + '\n'.join(lines) + f'\n{indent}}}'


def _api_routes(app):
    """(path, methods, endpoint, response model) of every API route. Newer FastAPI releases keep an included router as
    one lazy entry and yield its effective routes (with the include prefix) through iter_route_contexts; older releases
    list the included routes themselves. Both give the same routes."""
    from fastapi import routing
    if hasattr(routing, 'iter_route_contexts'):
        for context in routing.iter_route_contexts(app.routes):
            route = context.original_route
            if isinstance(route, routing.APIRoute):
                yield context.path, context.methods, context.endpoint, route.response_model
        return
    for route in app.routes:
        if isinstance(route, routing.APIRoute):
            yield route.path, route.methods, route.endpoint, route.response_model


def _body_model(hint):
    """The pydantic model a body parameter carries: the class itself, or the one model of an optional body
    (``Model | None``); anything else names no body model."""
    from pydantic import BaseModel
    if inspect.isclass(hint) and issubclass(hint, BaseModel):
        return hint
    if typing.get_origin(hint) in (typing.Union, types.UnionType):
        options = [arg for arg in typing.get_args(hint) if arg is not type(None)]
        if len(options) == 1 and inspect.isclass(options[0]) and issubclass(options[0], BaseModel):
            return options[0]
    return None


def _routes():
    """(method, path, body model or None, response model or None) of every API route, sorted."""
    from pydantic import BaseModel
    import backend.main as main
    app = main.create_app(project_dir=os.environ['VISION_AI_STUDIO_USER_DATA_DIR'] + '/projects')
    rows = []
    for path, methods, endpoint, response_model in _api_routes(app):
        try:
            hints = typing.get_type_hints(endpoint)
        except Exception:  # an annotation that cannot be resolved names no body model
            hints = {}
        bodies = [model for model in (_body_model(hints.get(name)) for name in inspect.signature(endpoint).parameters) if model]
        response = response_model if inspect.isclass(response_model) and issubclass(response_model, BaseModel) else None
        for method in sorted(methods):
            rows.append((method, path, bodies[0] if len(bodies) == 1 else None, response))
    return sorted(set(rows), key=lambda row: (row[1], row[0]))


def generate() -> str:
    from pydantic.json_schema import models_json_schema
    routes = _routes()
    models = sorted({(row[2], 'validation') for row in routes if row[2]} | {(row[3], 'serialization') for row in routes if row[3]},
                    key=lambda item: (item[0].__module__, item[0].__qualname__, item[1]))
    keys, document = models_json_schema(models, ref_template='#/$defs/{model}')
    definitions = document.get('$defs', {})
    lines = ['// Generated by scripts/generate_api_types.py from the backend\'s pydantic models. Do not edit by hand;',
             '// run `python scripts/generate_api_types.py` after changing a request or response model (CI checks it).', '']
    for name in sorted(definitions):
        lines.append(f'export type {type_name(name)} = {ts_type(definitions[name])};')
        lines.append('')
    def reference(model, mode):
        schema = keys.get((model, mode))
        return ts_type(schema) if schema else 'unknown'
    lines.append('/** The request body model of each route that takes one, keyed by "METHOD /path". */')
    lines.append('export interface ApiRequestBody {')
    lines += [f'  {json.dumps(f"{method} {path}")}: {reference(body, "validation")};' for method, path, body, _ in routes if body]
    lines += ['}', '', '/** The response model of each route that declares one, keyed by "METHOD /path". */', 'export interface ApiResponseBody {']
    lines += [f'  {json.dumps(f"{method} {path}")}: {reference(response, "serialization")};' for method, path, _, response in routes if response]
    lines += ['}', '']
    return '\n'.join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--check', action='store_true', help='exit 1 when the committed file differs; write nothing')
    parser.add_argument('--file', type=Path, default=OUTPUT, help='the generated file (default: the committed one)')
    options = parser.parse_args(argv)
    output = options.file.resolve()
    saved_env, saved_cwd = {name: os.environ.get(name) for name in _STORES}, os.getcwd()
    with tempfile.TemporaryDirectory(prefix='api-types-') as folder:
        for name in _STORES:
            os.environ[name] = str(Path(folder) / name.lower())
        sys.path.insert(0, str(ROOT))
        # Backend modules create working folders relative to the current directory when imported; they go to the
        # temporary folder, not into the checkout.
        os.chdir(folder)
        try:
            text = generate()
        finally:
            os.chdir(saved_cwd)
            for name, value in saved_env.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value
    if options.check:
        current = output.read_text(encoding='utf-8') if output.is_file() else ''
        if current != text:
            print(f'{output.name} is out of date; run python scripts/generate_api_types.py', file=sys.stderr)
            return 1
        print('API types are current')
        return 0
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(text, encoding='utf-8', newline='\n')
    print(f'wrote {output.name}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
