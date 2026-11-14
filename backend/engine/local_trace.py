"""Local-only bounded OTel SDK. No global provider, detector, environment exporter or persisted spans."""
from __future__ import annotations
from collections import OrderedDict
import hashlib
import json
import re
from threading import RLock

from opentelemetry.context import Context
from opentelemetry.trace import NonRecordingSpan, SpanContext, TraceFlags, Status, StatusCode, set_span_in_context
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import SpanLimits, SpanProcessor, TracerProvider
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult
from opentelemetry.sdk.trace.sampling import ALWAYS_ON

OPERATIONS = frozenset('api.app_request api.training api.dataset api.annotation api.evaluation api.flow api.inspection api.compute api.assistant api.project api.account api.provenance api.local_trace api.catalog api.models'.split())
EPOCH = re.compile(r'^[a-f0-9]{32}$')
PARENT = re.compile(r'^00-([a-f0-9]{32})-([a-f0-9]{16})-(0[01])$')


def operation(path):
    """Whitelist category only. Never retain the incoming path or query."""
    part = path.split('?', 1)[0].split('/')
    group = part[2] if len(part) > 2 and part[1] == 'api' else ''
    category = {'training-workspace': 'training', 'annotations': 'annotation', 'accounts': 'account',
                'local-trace': 'local_trace', 'automation': 'catalog'}.get(group, group)
    if category.startswith('flow'): category = 'flow'
    if category.startswith('assistant'): category = 'assistant'
    if category.startswith('inspection'): category = 'inspection'
    result = 'api.' + category
    return result if result in OPERATIONS else 'api.app_request'


def _scope(context, epoch):
    if not EPOCH.fullmatch(epoch or ''): raise ValueError('Local trace scope epoch required')
    # Identity is private, scoped by the authenticated context; never a client-supplied actor.
    return hashlib.sha256(json.dumps([context.workspace_id, context.project_id, context.actor_id, context.mode, epoch], separators=(',', ':')).encode()).hexdigest()


class _Exporter(SpanExporter):
    def __init__(self, owner): self.owner = owner
    def export(self, spans):
        owner = self.owner
        with owner.lock:
            for span in spans:
                bucket = owner.scopes.get(span.attributes.get('local.scope'))
                if bucket is None: continue
                parent = format(span.parent.span_id, '016x') if span.parent else None
                bucket['spans'].append({'operation': span.name if span.name in OPERATIONS else 'api.app_request',
                    'trace_id': format(span.context.trace_id, '032x'), 'span_id': format(span.context.span_id, '016x'),
                    'parent_span_id': parent, 'start_ms': span.start_time / 1e6, 'end_ms': span.end_time / 1e6,
                    'duration_ms': (span.end_time - span.start_time) / 1e6,
                    'status': span.attributes.get('local.outcome', 'error'), 'code': span.attributes.get('local.code'),
                    'coverage': 'explicit_parent' if parent else 'unlinked'})
                if len(bucket['spans']) > 256: bucket['spans'].pop(0);bucket['dropped_spans'] += 1
        return SpanExportResult.SUCCESS
    def shutdown(self): pass


class _Processor(SpanProcessor):
    def __init__(self, owner): self.owner = owner
    def on_end(self, span):
        try: self.owner.exporter.export([span])
        except Exception:
            with self.owner.lock: self.owner.export_errors += 1
    def shutdown(self): pass
    def force_flush(self, timeout_millis=30000): return True


class _Handle:
    def __init__(self, owner, span): self.owner, self.span, self.ended = owner, span, False
    def end(self, status, code=None):
        if self.ended: return
        self.ended = True
        try:
            outcome = status if status in ('ok', 'error', 'cancelled') else 'error'
            self.span.set_attribute('local.outcome', outcome)
            if type(code) is int and 100 <= code <= 599: self.span.set_attribute('local.code', code)
            self.span.set_status(Status(StatusCode.OK if outcome == 'ok' else StatusCode.ERROR))
            self.span.end()
        except Exception:
            with self.owner.lock: self.owner.export_errors += 1


class LocalTraceStore:
    def __init__(self):
        self.lock = RLock();self.scopes = OrderedDict();self.dropped_scopes = 0;self.export_errors = 0
        self.exporter = _Exporter(self)
        self.provider = TracerProvider(resource=Resource({}), sampler=ALWAYS_ON, shutdown_on_exit=False,
            span_limits=SpanLimits(max_attributes=3, max_attribute_length=64, max_events=0, max_links=0,
                                   max_event_attributes=0, max_link_attributes=0))
        self.provider.add_span_processor(_Processor(self))
        self.tracer = self.provider.get_tracer('modu-local', '1')

    def _bucket(self, key):
        with self.lock:
            if key not in self.scopes:
                self.scopes[key] = {'spans': [], 'dropped_spans': 0}
                if len(self.scopes) > 8: self.scopes.popitem(last=False);self.dropped_scopes += 1
            self.scopes.move_to_end(key)
            return self.scopes[key]

    def begin(self, context, epoch, name, parent=None):
        key = _scope(context, epoch);self._bucket(key)
        match = PARENT.fullmatch(parent or '')
        upstream = None
        if match and int(match[1], 16) and int(match[2], 16):
            upstream = SpanContext(int(match[1], 16), int(match[2], 16), is_remote=True, trace_flags=TraceFlags(1))
        root = set_span_in_context(NonRecordingSpan(upstream), Context()) if upstream else Context()
        return _Handle(self, self.tracer.start_span(name if name in OPERATIONS else 'api.app_request',
                       context=root, attributes={'local.scope': key}))

    def read(self, context, epoch):
        key = _scope(context, epoch)
        with self.lock:
            bucket = self._bucket(key)
            doc = {'sdk': 'opentelemetry-python-1.45.2', 'spans': [dict(row) for row in bucket['spans']],
                   'dropped_spans': bucket['dropped_spans'], 'dropped_scopes': self.dropped_scopes, 'export_errors': self.export_errors,
                   'capacity': 256, 'max_export_bytes': 1048576, 'async_jobs': 'unlinked'}
        while len(json.dumps(doc).encode()) > 1048576 and doc['spans']:
            doc['spans'].pop(0);doc['dropped_spans'] += 1
        return doc


local_traces = LocalTraceStore()


def begin_request_trace(scope):
    """Passive hook only after the existing context/auth checks. It cannot reject application work."""
    try:
        from starlette.datastructures import Headers
        context = scope.get('state', {}).get('project_context')
        if scope.get('type') != 'http' or context is None: return None
        headers = Headers(scope=scope);epoch = headers.get('x-vision-trace-scope')
        if not EPOCH.fullmatch(epoch or ''): return None
        return local_traces.begin(context, epoch, operation(scope.get('path', '')), headers.get('traceparent'))
    except Exception: return None
