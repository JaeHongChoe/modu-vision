"""Bound original ASGI/activity data only; no writer/tree/lease authority."""
from __future__ import annotations
import asyncio
import hashlib
import json
import os
import threading
import sys
from types import MethodType


class ActivityObservationError(ValueError):pass


def _context():
    try:return (asyncio.current_task(),asyncio.get_running_loop(),threading.current_thread(),os.getpid())
    except RuntimeError:return (None,None,threading.current_thread(),os.getpid())


class _RequestActivity:
    def __init__(self,observer,middleware,downstream,scope):
        self.observer,self.middleware,self.downstream,self.scope=observer,middleware,downstream,scope
        self.context=_context();self.ingress={key:scope.get(key) for key in ('type','method','path')};self.token=None
        self.binding=(self,observer,middleware,downstream,scope,self.context,tuple(self.ingress.items()))
    def __copy__(self):
        self.observer.poison('token_copy_refused');raise ActivityObservationError('Original request token cannot be copied')
    def __deepcopy__(self,memo):return self.__copy__()
    def __reduce_ex__(self,protocol):
        self.observer.poison('token_serialization_refused');raise ActivityObservationError('Original request token cannot be serialized')


class OriginalRequestActivityObserver:
    """Finite original ingress/envelope history, never transferable authority."""
    def __init__(self,*,max_events=1024,max_requests=256):
        if any(type(value) is not int or not 1<=value<=10000 for value in (max_events,max_requests)):
            raise ActivityObservationError('Original activity observation bounds differ')
        self._lock=threading.RLock();self._max_events=max_events;self._max_requests=max_requests
        self._pid=os.getpid();self._admission=None;self._condition=None;self._binding=None
        self._middleware=None;self._downstream=None;self._records={};self._events=[];self._sequence=0
        self._request_count=0;self._close_sequence=None;self._uncertain=False;self._failures=[]
        self._telemetry_sender=None
        # One bounded summary slot, never another request/capability or reset.
        self._settled_history=(0,0,hashlib.sha256(b'').hexdigest())
        self._settled_history_binding=self._settled_history
        self._settled_ready={};self._event_owners={};self._settlement_caller=None

    def poison(self,reason):
        with self._lock:
            self._uncertain=True
            if reason not in self._failures and len(self._failures)<32:self._failures.append(reason)

    def __copy__(self):
        self.poison('observer_copy_refused');raise ActivityObservationError('Original activity observer cannot be copied')
    def __deepcopy__(self,memo):return self.__copy__()
    def __reduce_ex__(self,protocol):
        self.poison('observer_serialization_refused');raise ActivityObservationError('Original activity observer cannot be serialized')

    def bind(self,admission):
        from backend.engine.application_launch_handshake import BackendWorkAdmission
        if type(admission) is not BackendWorkAdmission:
            self.poison('foreign_admission');raise ActivityObservationError('Original admission is required')
        with admission._condition:
            if (self._binding is not None or admission._activity_observer is not self or admission._activity_sequence!=0
                    or admission._active!=0 or admission._closed or os.getpid()!=self._pid):
                self.poison('late_or_replayed_binding');raise ActivityObservationError('Original activity observer must bind before all activity')
            self._admission,self._condition=admission,admission._condition
            self._binding=(self,admission,admission._condition,self._pid)
            self._settlement_caller=(BackendWorkAdmission.leave,BackendWorkAdmission.leave.__code__)

    def _original(self,admission):
        return (self._binding is not None and self is self._binding[0] and admission is self._binding[1]
                and self._admission is admission and self._condition is self._binding[2]
                and admission._condition is self._condition and admission._activity_observer is self
                and os.getpid()==self._pid==self._binding[3])

    def bind_middleware(self,admission,middleware,downstream):
        from backend.main import OwnedWorkAdmissionMiddleware
        if (not self._original(admission) or type(middleware) is not OwnedWorkAdmissionMiddleware
                or middleware.admission is not admission or middleware.app is not downstream
                or self._middleware is not None or admission._activity_sequence!=0):
            self.poison('late_or_changed_middleware');return
        self._middleware,self._downstream=middleware,downstream

    def capture(self,admission,middleware,downstream,scope):
        if (not self._original(admission) or middleware is not self._middleware or downstream is not self._downstream
                or middleware.app is not downstream):self.poison('original_middleware_changed')
        return _RequestActivity(self,middleware,downstream,scope)

    def bind_telemetry_sender(self,admission,owner,sender):
        from backend.engine.original_owned_telemetry_socket_request import OriginalOwnedTelemetrySocketRequest
        if (not self._original(admission) or self._sequence or self._telemetry_sender is not None
                or type(owner) is not OriginalOwnedTelemetrySocketRequest or owner.observer is not self
                or owner.admission is not admission or type(sender) is not MethodType or sender.__self__ is not owner
                or sender.__func__ is not OriginalOwnedTelemetrySocketRequest.sender
                or sys._getframe(1).f_code is not OriginalOwnedTelemetrySocketRequest.__init__.__code__):
            self.poison('unknown_telemetry_sender_binding');return
        self._telemetry_sender=owner,sender.__func__,sender.__func__.__code__

    def _valid(self,carrier):
        current=_context()
        valid=(type(carrier) is _RequestActivity and carrier is carrier.binding[0] and carrier.observer is self is carrier.binding[1]
               and carrier.middleware is self._middleware is carrier.binding[2] and carrier.downstream is self._downstream is carrier.binding[3]
               and carrier.scope is carrier.binding[4] and carrier.context is carrier.binding[5]
               and tuple(carrier.ingress.items())==carrier.binding[6] and self._middleware.app is self._downstream
               and all(carrier.context[i] is current[i] for i in (0,1,2)) and carrier.context[3]==current[3]
               and carrier.context[0] is not None and carrier.context[1] is not None
               and {key:carrier.scope.get(key) for key in ('type','method','path')}==carrier.ingress)
        if not valid:self.poison('original_request_owner_changed')
        return valid

    def _row(self,carrier,socket_sender=False):
        if socket_sender:
            current=_context()
            valid=(type(carrier) is _RequestActivity and carrier is carrier.binding[0] and carrier.observer is self is carrier.binding[1]
                and carrier.middleware is self._middleware is carrier.binding[2] and carrier.downstream is self._downstream is carrier.binding[3]
                and carrier.scope is carrier.binding[4] and carrier.context is carrier.binding[5]
                and tuple(carrier.ingress.items())==carrier.binding[6] and self._middleware.app is self._downstream
                and all(carrier.context[i] is current[i] for i in (1,2)) and carrier.context[3]==current[3]
                and {key:carrier.scope.get(key) for key in ('type','method','path')}==carrier.ingress)
        else:valid=self._valid(carrier)
        if not valid or carrier.token not in self._records:
            self.poison('unmatched_original_token');return None
        row=self._records[carrier.token]
        if row['carrier'] is not carrier or row['released']:
            self.poison('replayed_original_token');return None
        return row

    def _frame(self,message):
        kind=message.get('type');frame={'type':kind}
        if kind=='http.response.start':frame.update(status=message.get('status'),trailers=message.get('trailers',False))
        elif kind=='http.response.body':frame['more_body']=message.get('more_body',False)
        elif kind=='http.response.trailers':frame['more_trailers']=message.get('more_trailers',False)
        return frame

    def _settled_history_original(self,extra_requests=0):
        value=self._settled_history
        return (type(value)is tuple and len(value)==3 and value is self._settled_history_binding
            and all(type(v)is int and v>=0 for v in value[:2])
            and type(value[2])is str and len(value[2])==64 and all(c in '0123456789abcdef'for c in value[2])
            and (self._uncertain or self._request_count==value[0]+len(self._records)+extra_requests)
            and set(self._event_owners)=={id(event)for event in self._events}
            and set(self._settled_ready)<=set(self._records))

    def _settled_payload(self,token,row):
        carrier=row['carrier']
        if (type(carrier)is not _RequestActivity or carrier.token is not token
                or carrier is not carrier.binding[0] or not (carrier.observer is self is carrier.binding[1])
                or not (carrier.middleware is self._middleware is carrier.binding[2])
                or not (carrier.downstream is self._downstream is carrier.binding[3])
                or carrier.scope is not carrier.binding[4]
                or carrier.context is not carrier.binding[5]
                or tuple(carrier.ingress.items())!=carrier.binding[6]
                or {key:carrier.scope.get(key)for key in ('type','method','path')}!=carrier.ingress
                or not row['accepted'] or not row['released'] or not row['completed']
                or not row['normal_return'] or row['transport_failed'] or row['framing_error']
                or row['send_attempt']is not None):
            raise ActivityObservationError('Original completed envelope changed')
        projection={k:v for k,v in row.items()if k not in('carrier','telemetry_streams')}
        projection['telemetry_streams']=[dict(value['projection'])for value in row['telemetry_streams'].values()]
        events=[dict(event)for event in self._events if self._event_owners[id(event)]is carrier]
        return json.dumps({'record':projection,'events':events},sort_keys=True,separators=(',',':'),allow_nan=False).encode()

    def original_request_settled(self,admission,carrier):
        """Data only, after EVERY original adapter finish under its condition."""
        caller=sys._getframe(1);function,code=self._settlement_caller
        from backend.engine.application_launch_handshake import BackendWorkAdmission
        with self._lock:
            if (not self._original(admission) or not self._condition._is_owned()
                    or BackendWorkAdmission.leave is not function or function.__code__ is not code
                    or caller.f_code is not code or caller.f_globals is not function.__globals__
                    or caller.f_locals.get('self')is not admission or caller.f_locals.get('activity')is not carrier):
                self.poison('foreign_settled_history_caller');return
            if carrier is None or self._uncertain or admission._unsupported:return
            if not self._valid(carrier):return
            row=self._records.get(carrier.token)
            if row is None:return
            for name in ('_current_project_requests','_desktop_denials','_shared_denials',
                    '_account_requests','_flow_requests','_flow_execution_requests',
                    '_UI_ancillary_requests','_telemetry_socket_requests'):
                owner=getattr(admission,name)
                if owner is not None:
                    if type(owner.pending)is not dict:
                        self.poison('changed_pending_history_owner');return
                    if any(getattr(attempt,'activity',None)is carrier for attempt in owner.pending.values()):return
            try:self._settled_ready[carrier.token]=(row,self._settled_payload(carrier.token,row),carrier.binding,carrier.context)
            except BaseException:self.poison('unconfirmed_settled_history')

    def _room_for_original_event(self,*,events=0,records=0):
        if not self._settled_history_original(records):
            self.poison('settled_history_changed');return False
        count,archived_events,digest=self._settled_history
        slot=int(count>0)
        if len(self._events)+slot+events<=self._max_events and len(self._records)+slot+records<=self._max_requests:return True
        if self._uncertain or self._admission._unsupported:return False
        # The original cap must hold summary + all retained/pending + new work.
        # For max_requests1 there is no room for summary and a new request.
        candidates=[];remaining_events=len(self._events);remaining_records=len(self._records)
        for token,(held,payload,binding,context)in sorted(self._settled_ready.items(),key=lambda item:item[1][0]['identity']):
            row=self._records.get(token)
            try:valid=(row is held and row['carrier'].binding is binding and row['carrier'].context is context
                and self._settled_payload(token,row)==payload)
            except BaseException:valid=False
            if not valid:self.poison('settled_history_changed');return False
            carrier=row['carrier'];owned=[event for event in self._events if self._event_owners[id(event)]is carrier]
            candidates.append((token,payload,owned));remaining_events-=len(owned);remaining_records-=1
            if remaining_events+1+events<=self._max_events and remaining_records+1+records<=self._max_requests:break
        else:return False
        removed_events={id(event)for _,_,owned in candidates for event in owned}
        for token,payload,owned in candidates:
            digest=hashlib.sha256(bytes.fromhex(digest)+payload).hexdigest();count+=1;archived_events+=len(owned)
            del self._records[token];del self._settled_ready[token]
        self._events[:]=[event for event in self._events if id(event)not in removed_events]
        for identity in removed_events:del self._event_owners[identity]
        self._settled_history=(count,archived_events,digest);self._settled_history_binding=self._settled_history
        return True

    def event(self,admission,sequence,action,*args):
        with self._lock:
            if not self._original(admission) or not self._condition._is_owned():
                self.poison('original_condition_changed');return
            if type(sequence) is not int or sequence!=self._sequence+1:self.poison('activity_sequence_replayed_or_missing')
            self._sequence=sequence
            carrier=(args[1] if action=='enter' and len(args)>1 else args[0] if args else None)
            socket_sender=False;row=stream=None
            if action in {'send_attempt','send_success','send_error'} and self._telemetry_sender is not None:
                owner,function,code=self._telemetry_sender
                current=getattr(owner,'sender',None)
                if (function.__code__ is code and type(current) is MethodType
                        and current.__self__ is owner and current.__func__ is function):
                    socket_sender=function(owner,carrier,action,args[1])
            if socket_sender:
                row=self._row(carrier,True)
                if row is None:return
                stream=row['telemetry_streams'].get(socket_sender)
                if stream is None:
                    if action!='send_attempt':self.poison('unmatched_telemetry_stream');return
                    if not self._room_for_original_event(events=1):self.poison('event_overflow');return
                    projection={'sequence':sequence,'action':'telemetry_send_stream','role':socket_sender.role,
                        'first_sequence':sequence,'last_sequence':sequence,'attempts':0,'successes':0,'errors':0,'pending':False}
                    stream={'projection':projection,'frame':None}
                    row['telemetry_streams'][socket_sender]=stream;self._events.append(projection);self._event_owners[id(projection)]=carrier
                stream['projection']['last_sequence']=sequence
            elif not self._room_for_original_event(events=1):self.poison('event_overflow')
            else:
                projection={'sequence':sequence,'action':action}
                self._events.append(projection);self._event_owners[id(projection)]=carrier
            if action=='enter':
                scope,carrier,accepted=args;self._request_count+=1
                if carrier is None or not self._valid(carrier) or carrier.scope is not scope:
                    self.poison('anonymous_entry');return
                if carrier.token is not None:self.poison('entry_token_replayed');return
                if not self._room_for_original_event(records=1):self.poison('request_overflow');return
                carrier.token=object()
                self._records[carrier.token]={'carrier':carrier,'identity':self._request_count,'ingress':dict(carrier.ingress),
                    'entered_sequence':sequence,'released_sequence':None,
                    'accepted':accepted,'released':False,'completed':False,'normal_return':False,'error_type':None,'canceled':False,
                    'terminal_forwarded':False,'start_forwarded':False,'response_status':None,'trailers_expected':False,'trailers_forwarded':False,
                    'ws_disconnect':False,'ws_accepted':False,'send_attempt':None,'framing_error':False,'transport_failed':False,
                    'telemetry_streams':{}}
                return
            if action=='producer':self.poison('anonymous_producer');return
            if action=='close':
                if args[0] or self._close_sequence is not None:self.poison('close_replayed')
                else:self._close_sequence=sequence
                return
            if action=='unmatched_leave':self.poison('unmatched_original_leave');return
            if carrier is None:self.poison('anonymous_release');return
            if not socket_sender:row=self._row(carrier)
            if row is None:return
            if action=='send_attempt':
                frame=self._frame(args[1])
                if row['send_attempt'] is not None:row['framing_error']=True;self.poison('overlapping_send')
                row['send_attempt']=frame
                if stream is not None:
                    if stream['frame'] is not None:row['framing_error']=True;self.poison('overlapping_send')
                    stream['frame']=frame;stream['projection']['attempts']+=1;stream['projection']['pending']=True
            elif action=='send_success':
                frame=self._frame(args[1])
                if frame!=row['send_attempt']:row['framing_error']=True;self.poison('send_frame_changed')
                row['send_attempt']=None;kind=frame['type'];protocol=row['ingress']['type']
                if stream is not None:
                    if frame!=stream['frame']:row['framing_error']=True;self.poison('send_frame_changed')
                    stream['frame']=None;stream['projection']['successes']+=1;stream['projection']['pending']=False
                bad=False
                if protocol=='http':
                    if kind=='http.response.start':
                        bad=row['start_forwarded'] or type(frame['status']) is not int or not 100<=frame['status']<=599 or type(frame['trailers']) is not bool
                        row['start_forwarded']=True;row['trailers_expected']=frame['trailers'] is True
                        row['response_status']=frame['status']
                    elif kind=='http.response.body':
                        bad=not row['start_forwarded'] or row['terminal_forwarded'] or type(frame['more_body']) is not bool
                        if frame['more_body'] is False:row['terminal_forwarded']=True
                    elif kind=='http.response.trailers':
                        bad=not row['trailers_expected'] or not row['terminal_forwarded'] or row['trailers_forwarded'] or type(frame['more_trailers']) is not bool
                        if frame['more_trailers'] is False:row['trailers_forwarded']=True
                    else:bad=True
                elif protocol=='websocket':
                    if kind=='websocket.close':
                        bad=row['terminal_forwarded'];row['terminal_forwarded']=True
                    elif kind=='websocket.accept':
                        bad=row['ws_accepted'] or row['terminal_forwarded'];row['ws_accepted']=True
                    elif kind=='websocket.send':bad=not row['ws_accepted'] or row['terminal_forwarded']
                    else:bad=True
                else:bad=True
                if bad:row['framing_error']=True;self.poison('invalid_response_framing')
            elif action=='receive':
                kind=args[1].get('type');protocol=row['ingress']['type']
                if protocol=='websocket' and kind=='websocket.disconnect':row['ws_disconnect']=True
                elif kind not in ({'websocket.connect','websocket.receive'} if protocol=='websocket' else {'http.request','http.disconnect'}):
                    row['framing_error']=True;self.poison('unknown_receive_frame')
            elif action in {'send_error','receive_error','downstream_error'}:
                if stream is not None:stream['projection']['errors']+=1
                error=args[1];row['transport_failed']=True;row['error_type']=row['error_type'] or type(error).__name__
                row['canceled']|=isinstance(error,asyncio.CancelledError);self.poison(action)
            elif action in {'leave','rejected_leave'}:
                normal=args[1];rejected=action=='rejected_leave'
                if rejected==row['accepted']:self.poison('wrong_original_release')
                row['released']=True;row['normal_return']=normal is True
                row['released_sequence']=sequence
                protocol=row['ingress']['type']
                terminal=(row['start_forwarded'] and row['terminal_forwarded'] and (not row['trailers_expected'] or row['trailers_forwarded']) if protocol=='http'
                          else row['terminal_forwarded'] or row['ws_disconnect'] if protocol=='websocket' else False)
                streams_settled=all(value['frame'] is None and not value['projection']['pending']
                    and value['projection']['attempts']==value['projection']['successes'] and value['projection']['errors']==0
                    for value in row['telemetry_streams'].values())
                row['completed']=(normal is True and terminal and not row['framing_error'] and not row['transport_failed']
                    and row['send_attempt'] is None and streams_settled and rejected!=row['accepted'])
                if not row['completed']:self.poison('incomplete_original_envelope')
            else:self.poison('unknown_activity_event')

    def snapshot(self):
        condition=self._binding[2] if self._binding is not None else self._lock
        with condition:
            return self._snapshot_locked()

    def health_diagnostic(self, original_scope):
        """Bounded, sanitized observation only; the health request stays active."""
        condition = self._binding[2] if self._binding is not None else self._lock
        with condition, self._lock:
            if (self._admission is None or not self._original(self._admission)
                    or self._middleware is None or self._middleware.app is not self._downstream
                    or self._sequence != self._admission._activity_sequence):
                return None
            routes = {
                '/health': 'HEALTH', '/api/project/current': 'PROJECT_CURRENT',
                '/api/onboarding': 'ONBOARDING', '/api/project/list': 'PROJECT_LIST',
                '/api/accounts/config': 'ACCOUNTS_CONFIG', '/api/errors': 'ERRORS',
                '/ws/telemetry': 'TELEMETRY', '/ws/training': 'TRAINING_TELEMETRY',
            }
            methods = {'GET', 'HEAD', 'OPTIONS', 'POST', 'PUT', 'PATCH', 'DELETE'}
            records = []; diagnostic_identity = None; pending = 0; settled = released = self._settled_history[0]
            for row in self._records.values():
                carrier = row['carrier']
                if type(carrier) is not _RequestActivity: return None
                binding = carrier.binding
                if (carrier is not binding[0]
                        or carrier.observer is not self or binding[1] is not self
                        or carrier.middleware is not self._middleware or binding[2] is not self._middleware
                        or carrier.downstream is not self._downstream or binding[3] is not self._downstream
                        or carrier.scope is not binding[4]
                        or type(row['identity']) is not int
                        or type(row['entered_sequence']) is not int
                        or row['released_sequence'] is not None and type(row['released_sequence']) is not int
                        or any(type(row[k]) is not bool for k in ('accepted', 'released', 'completed', 'normal_return'))):
                    return None
                pending += not row['released']; settled += row['completed']; released += row['released']
                if carrier.scope is original_scope and not row['released']:
                    if diagnostic_identity is not None: return None
                    diagnostic_identity = row['identity']
                if len(records) < 128 or (carrier.scope is original_scope and not row['released']):
                    ingress = row['ingress']; path = ingress.get('path'); method = ingress.get('method')
                    protocol = ingress.get('type'); status = row.get('response_status')
                    projected = {
                        'identity': row['identity'], 'entered_sequence': row['entered_sequence'],
                        'released_sequence': row['released_sequence'],
                        'protocol': 'HTTP' if type(protocol) is str and protocol == 'http' else
                            'WEBSOCKET' if type(protocol) is str and protocol == 'websocket' else 'UNKNOWN',
                        'method': method if type(method) is str and method in methods else 'NONE' if method is None else 'UNKNOWN',
                        'route': routes.get(path, 'UNKNOWN') if type(path) is str else 'UNKNOWN',
                        'status': status if type(status) is int and 100 <= status <= 599 else None,
                        'accepted': row['accepted'], 'released': row['released'],
                        'completed': row['completed'], 'normal_return': row['normal_return'],
                    }
                    if len(records) < 128: records.append(projected)
                    else: records[-1] = projected
            if diagnostic_identity is None: return None
            return {
                'schema': 'modu-vision.original-owned-health-diagnostic/v1',
                'sequence': self._sequence, 'request_count': self._request_count,
                'sampled_active_scopes': self._admission._active,
                'pending_records': pending, 'settled_records': settled, 'released_records': released,
                'diagnostic_request_identity': diagnostic_identity, 'diagnostic_request_in_flight': True,
                'uncertain': self._uncertain,
                'truncated': bool(self._settled_history[0]) or len(self._records) > 128 or self._request_count != len(self._records),
                'records': records,
                'whole_writer_coverage': False, 'process_tree_exit_verified': False,
                'can_release_launch_lease': False,
            }

    def health_failure_diagnostic(self, original_scope):
        """Read-only reason hashes; no strings, reset, or quiescence authority."""
        if (type(original_scope) is not dict
                or any(type(original_scope.get(key)) is not str or original_scope[key] != value
                    for key, value in (('type', 'http'), ('method', 'GET'), ('path', '/health')))):
            return None
        condition = self._binding[2] if self._binding is not None else self._lock
        with condition, self._lock:
            original = self.health_diagnostic(original_scope)
            reasons = self._failures
            if (original is None or type(self._uncertain) is not bool
                    or type(reasons) is not list or len(reasons) > 32):
                return None
            hashes = []
            for reason in reasons:
                if type(reason) is not str or not 1 <= len(reason) <= 256:
                    return None
                try: hashes.append(hashlib.sha256(reason.encode('utf-8')).hexdigest())
                except UnicodeEncodeError: return None
            if len(set(hashes)) != len(hashes) or self._uncertain != bool(hashes): return None
            return {
                'schema': 'modu-vision.original-owned-health-failure-diagnostic/v1',
                'sequence': original['sequence'], 'request_count': original['request_count'],
                'diagnostic_request_identity': original['diagnostic_request_identity'],
                'uncertain': self._uncertain, 'failure_count': len(hashes), 'reason_sha256': hashes,
                'whole_writer_coverage': False, 'process_tree_exit_verified': False,
                'can_release_launch_lease': False,
            }

    def _snapshot_locked(self):
        with self._lock:
            if self._admission is None or not self._original(self._admission):self.poison('original_observer_unavailable')
            if not self._settled_history_original():self.poison('settled_history_changed')
            if self._middleware is not None and self._middleware.app is not self._downstream:self.poison('original_downstream_changed')
            if self._admission is not None and self._sequence!=self._admission._activity_sequence:self.poison('activity_history_missing')
            if self._sequence and self._middleware is None:self.poison('missing_original_middleware')
            records=[{key:row[key] for key in ('identity','ingress','accepted','released','completed','normal_return','error_type','canceled','terminal_forwarded')} for row in self._records.values()]
            records=[{**row,'ingress':dict(row['ingress'])} for row in records]
            pending=sum(not row['released'] for row in records)
            closed=self._admission is not None and self._admission._closed and self._admission._active==0
            unsupported=sorted(self._admission._unsupported) if self._admission is not None else []
            return {'uncertain':self._uncertain,'all_settled':self._middleware is not None and closed and self._close_sequence is not None and not unsupported and not self._uncertain and pending==0 and all(row['completed'] for row in records),
                'unsupported':unsupported,
                'request_count':self._request_count,'pending_requests':pending,'event_count':len(self._events),'sequence':self._sequence,
                'close_sequence':self._close_sequence,'records':records,'events':[dict(row) for row in self._events],'failures':list(self._failures),
                'settled_history':dict(zip(('requests','events','sha256'),self._settled_history)),
                'whole_writer_coverage':False,'process_tree_exit_verified':False,'can_release_launch_lease':False}
