"""Public product CLI: python -m backend.studio_cli --help. No engine imports."""
from __future__ import annotations

import argparse
import json
import os
import sys

from backend.automation.client import (CommandError, StudioClient, checked_output,
    json_object, redacted, result_envelope)


class Parser(argparse.ArgumentParser):
    def error(self, message):
        # Unknown arguments may accidentally contain a password. Never echo argv.
        raise CommandError('invalid_arguments', 'Invalid or missing arguments; run --help for this command')


def parser():
    root = Parser(description='모두비전 공통 CLI: 실행 중인 앱/웹 서버의 실제 기능·입력·결과를 JSON으로 읽고 실행합니다.')
    root.add_argument('--url', default=os.environ.get('MODU_STUDIO_URL'), help='Server root URL (or MODU_STUDIO_URL); HTTP only on loopback')
    root.add_argument('--auth', choices=('auto', 'desktop', 'bearer'), default='auto', help='Choose the environment credential transport; no credentials in arguments')
    root.add_argument('--project', help='Explicit X-Vision-Project identifier')
    root.add_argument('--context-json', help='Explicit workspace_id/project_id/actor_id/mode JSON')
    root.add_argument('--session-file', help='Explicit private CLI-owned bearer file; login creates it without printing credentials')
    root.add_argument('--ca-file', help='Explicit trusted CA PEM file for localhost/self-signed HTTPS')
    root.add_argument('--timeout', type=float, default=60, help='Per-request seconds; mutations are never retried')
    commands = root.add_subparsers(dest='command', required=True)
    login = commands.add_parser('login', help='Log in with an existing account; create a new private origin-bound CLI session file')
    login.add_argument('--username', required=True)
    login.add_argument('--password-stdin', action='store_true', help='Read one password line from explicit stdin instead of a hidden terminal prompt')
    capabilities = commands.add_parser('capabilities', help='List live installed commands; paginate with next_offset')
    capabilities.add_argument('--group')
    capabilities.add_argument('--offset', type=int, default=0)
    capabilities.add_argument('--limit', type=int, default=50)
    capabilities.add_argument('--schemas', action='store_true')
    describe = commands.add_parser('describe', help='Read exact input/response schemas and assistant execution policy')
    describe.add_argument('operation', help='Exact operation id, for example "GET /api/project/current"')
    call = commands.add_parser('call', help='Call a catalog operation with explicit inputs and existing server authority')
    call.add_argument('operation')
    call.add_argument('--path-json', default='{}')
    call.add_argument('--query-json', default='{}')
    body = call.add_mutually_exclusive_group()
    body.add_argument('--body-json')
    body.add_argument('--body-file')
    body.add_argument('--upload-file', help='Stream an explicit application/octet-stream file')
    call.add_argument('--file', action='append', default=[], help='Multipart FIELD=PATH; repeat for files')
    call.add_argument('--form-json', help='Multipart text fields as a JSON object')
    call.add_argument('--output', help='Save response bytes to a new file; never overwrite')
    diagnostic = commands.add_parser('diagnostics', help='Read bounded project/training observations; never start evaluation')
    diagnostic.add_argument('--section', choices=('overview', 'project', 'training', 'jobs'), default='overview')
    return root


def diagnostics(client, section):
    if 'X-Vision-Project' not in client.headers and 'X-Vision-Context' not in client.headers:
        raise CommandError('project_required', 'Diagnostics requires an explicit --project or --context-json; it never initializes a default project')
    selected = {'project': ['GET /api/project/labelsets'], 'training': ['GET /api/training/status'],
                'jobs': ['GET /api/training/jobs']}
    operations = selected.get(section, selected['project'] + selected['training'] + selected['jobs'])
    observations = []
    for operation in operations:
        try:
            detail = client.describe(operation)['result']['operations'][0]
            if detail.get('effect') != 'read' or detail.get('assistant', {}).get('mode') != 'read':
                raise CommandError('unsafe_observation', 'Catalog does not identify this exact operation as an observation')
            result = client.call(operation)
        except CommandError as error:
            result = result_envelope(operation, status=error.status, outcome=error.outcome,
                error={'code': error.code, 'message': redacted(str(error), client.secrets),
                       'detail': redacted(error.detail, client.secrets), 'retryable': False})
        observations.append(result)
    partial = any(not row['ok'] for row in observations)
    return result_envelope('diagnostics', result={'section': section, 'observations': observations, 'partial': partial,
        'note': 'Server observations only; unsaved screen state and complete historical logs are not included.'})


def main(argv=None):
    client = None
    operation = None
    credential_secrets = tuple(os.environ.get(key, '') for key in ('VISION_AI_STUDIO_API_TOKEN', 'MODU_STUDIO_BEARER_TOKEN'))
    try:
        options = parser().parse_args(argv)
        operation = getattr(options, 'operation', options.command)
        if options.command == 'login':
            from backend.automation.session import login
            client = StudioClient(options.url, ca_file=options.ca_file, timeout=options.timeout, authenticated=False)
            result = result_envelope('login', result=login(client, username=options.username,
                filename=options.session_file, stdin=options.password_stdin), outcome='responded')
            print(json.dumps(result, ensure_ascii=False, allow_nan=False))
            return 0
        output = checked_output(getattr(options, 'output', None))
        token = os.environ.get('VISION_AI_STUDIO_API_TOKEN') if options.auth != 'bearer' else None
        bearer = os.environ.get('MODU_STUDIO_BEARER_TOKEN') if options.auth != 'desktop' else None
        if options.session_file:
            from backend.automation.session import read_session
            if options.auth == 'desktop': raise CommandError('invalid_arguments', '--session-file is an account bearer transport')
            if token or bearer: raise CommandError('ambiguous_authentication', 'Choose the explicit session file or environment credentials, not both')
            bearer = read_session(options.session_file, options.url)
        credential_secrets = (*credential_secrets, token, bearer)
        client = StudioClient(options.url, token=token, bearer=bearer, project=options.project,
            context=json_object(options.context_json, '--context-json') if options.context_json else None,
            ca_file=options.ca_file, timeout=options.timeout)
        if options.command == 'capabilities':
            if options.offset < 0 or not 1 <= options.limit <= 1000:
                raise CommandError('invalid_arguments', 'offset must be >=0 and limit must be 1..1000')
            result = client.catalog(group=options.group, offset=options.offset, limit=options.limit, schemas=options.schemas)
        elif options.command == 'describe': result = client.describe(options.operation)
        elif options.command == 'diagnostics': result = diagnostics(client, options.section)
        else:
            result = client.call(options.operation, path_args=json_object(options.path_json, '--path-json'),
                query_args=json_object(options.query_json, '--query-json'), body_json=options.body_json,
                body_file=options.body_file, upload_file=options.upload_file, files=options.file,
                form_json=options.form_json, output=output)
        print(json.dumps(result, ensure_ascii=False, allow_nan=False))
        return 0
    except (CommandError, KeyboardInterrupt) as error:
        if isinstance(error, KeyboardInterrupt):
            error = CommandError('interrupted', 'CLI interrupted; no request is retried. Read the owning job before resubmitting.', exit_code=130, outcome='unconfirmed')
        secrets = client.secrets if client else credential_secrets
        value = result_envelope(redacted(operation, credential_secrets), status=error.status, outcome=error.outcome,
            error={'code': error.code, 'message': redacted(str(error), secrets),
                   'detail': redacted(error.detail, secrets), 'retryable': False})
        print(json.dumps(value, ensure_ascii=False, allow_nan=False))
        return error.exit_code


if __name__ == '__main__': raise SystemExit(main())
