"""Actual ASGI envelopes and Starlette BackgroundTask, isolated source only."""
import asyncio
import copy
import importlib
import threading
import pytest
from starlette.responses import Response
from starlette.background import BackgroundTask


def api():
    try:return importlib.import_module('backend.engine.original_request_activity')
    except ModuleNotFoundError as error:
        if error.name=='backend.engine.original_request_activity':pytest.fail('Original request/activity witness is missing')
        raise


def scope(path='/health',kind='http'):
    return {'type':kind,'asgi':{'version':'3.0'},'http_version':'1.1','method':'GET','path':path,'raw_path':path.encode(),
            'query_string':b'private_query_not_to_record','headers':[(b'authorization',b'private_token_not_to_record')],
            'scheme':'http','client':('127.0.0.1',1),'server':('127.0.0.1',2)}


async def receive():return {'type':'http.request','body':b'private_body_not_to_record','more_body':False}


def setup(app,**kwargs):
    from backend.engine.application_launch_handshake import BackendWorkAdmission
    from backend.main import OwnedWorkAdmissionMiddleware
    held=api().OriginalRequestActivityObserver(**kwargs);admission=BackendWorkAdmission(activity_observer=held)
    return held,admission,OwnedWorkAdmissionMiddleware(app,admission)


def flags(snap):
    assert all(snap[key] is False for key in ('whole_writer_coverage','process_tree_exit_verified','can_release_launch_lease'))


def test_final_http_body_keeps_original_token_until_actual_starlette_background_task_returns():
    async def scenario():
        begun=asyncio.Event();release=asyncio.Event();writes=[];frames=[]
        async def background():begun.set();await release.wait();writes.append('original background completed')
        response=Response(b'ok',background=BackgroundTask(background));held,admission,middleware=setup(response)
        task=asyncio.create_task(middleware(scope(),receive,lambda msg:send(frames,msg)))
        await asyncio.wait_for(begun.wait(),1)
        snap=held.snapshot();assert snap['pending_requests']==1 and not snap['all_settled']
        assert snap['records'][0]['terminal_forwarded'] and not snap['records'][0]['completed'] and writes==[]
        admission.close();release.set();await task
        snap=held.snapshot();assert snap['all_settled'] and snap['pending_requests']==0 and not snap['uncertain']
        assert snap['records'][0]['completed'] and writes==['original background completed'];flags(snap)
        assert admission.snapshot()=={'active_scopes':0,'unsupported':[]}
        assert 'private_' not in repr(snap)
    asyncio.run(scenario())


async def send(frames,msg):frames.append(dict(msg))


@pytest.mark.parametrize('kind',['http.response.start','http.response.body'])
def test_original_send_failure_is_not_forwarded_completion(kind):
    async def scenario():
        held,admission,middleware=setup(Response(b'ok'))
        async def fail(message):
            if message['type']==kind:raise OSError('original send failure')
        with pytest.raises(OSError,match='original send failure'):await middleware(scope(),receive,fail)
        admission.close();snap=held.snapshot();assert snap['uncertain'] and not snap['all_settled']
        assert snap['records'][0]['error_type']=='OSError' and not snap['records'][0]['completed'];flags(snap)
        assert admission.snapshot()=={'active_scopes':0,'unsupported':[]}
    asyncio.run(scenario())


def test_actual_background_cancellation_remains_sticky_after_original_count_release():
    async def scenario():
        begun=asyncio.Event();release=asyncio.Event()
        async def background():begun.set();await release.wait()
        held,admission,middleware=setup(Response(b'ok',background=BackgroundTask(background)))
        async def discard(message):pass
        task=asyncio.create_task(middleware(scope(),receive,discard));await begun.wait();task.cancel()
        with pytest.raises(asyncio.CancelledError):await task
        admission.close();snap=held.snapshot();assert snap['uncertain'] and not snap['all_settled']
        assert snap['records'][0]['canceled'] and snap['records'][0]['error_type']=='CancelledError'
        assert snap['pending_requests']==0 and admission.snapshot()['active_scopes']==0;flags(snap)
    asyncio.run(scenario())


def test_distinct_original_tokens_for_sequential_requests_on_one_actual_task():
    async def scenario():
        held,admission,middleware=setup(Response(b'ok'));frames=[]
        for _ in range(2):await middleware(scope(),receive,lambda msg:send(frames,msg))
        admission.close();snap=held.snapshot();assert snap['all_settled'] and snap['request_count']==2
        assert len({r['identity'] for r in snap['records']})==2
        assert snap['sequence']>snap['request_count'] and snap['close_sequence']==snap['sequence'];flags(snap)
    asyncio.run(scenario())


@pytest.mark.parametrize('mode',['no_terminal','body_before_start','duplicate_start','unfinished_trailers'])
def test_normal_asgi_return_does_not_certify_bad_or_incomplete_response_framing(mode):
    async def scenario():
        async def app(sc,rc,se):
            if mode=='body_before_start':await se({'type':'http.response.body','body':b'x'})
            else:
                await se({'type':'http.response.start','status':200,'headers':[],'trailers':mode=='unfinished_trailers'})
                if mode=='duplicate_start':await se({'type':'http.response.start','status':200,'headers':[]})
                if mode!='no_terminal':await se({'type':'http.response.body','body':b'x'})
        held,admission,middleware=setup(app)
        async def discard(message):pass
        await middleware(scope(),receive,discard);admission.close();snap=held.snapshot()
        assert snap['uncertain'] and not snap['all_settled'] and not snap['records'][0]['completed'];flags(snap)
    asyncio.run(scenario())


def test_actual_terminal_trailers_require_successful_forwarding_and_downstream_return():
    async def scenario():
        async def app(sc,rc,se):
            await se({'type':'http.response.start','status':200,'headers':[],'trailers':True})
            await se({'type':'http.response.body','body':b'ok'})
            await se({'type':'http.response.trailers','headers':[],'more_trailers':False})
        held,admission,middleware=setup(app)
        async def discard(message):pass
        await middleware(scope(),receive,discard);admission.close();snap=held.snapshot();assert snap['all_settled'] and not snap['uncertain'];flags(snap)
    asyncio.run(scenario())


def test_original_close_race_never_runs_inner_app_after_a_rejected_entry():
    async def scenario():
        calls=[];barrier=threading.Barrier(2)
        async def app(sc,rc,se):calls.append('entered');await Response(b'ok')(sc,rc,se)
        held,admission,middleware=setup(app);frames=[]
        closer=threading.Thread(target=lambda:(barrier.wait(1),admission.close()));closer.start()
        barrier.wait(1);await middleware(scope(),receive,lambda msg:send(frames,msg));closer.join(1)
        assert not closer.is_alive();snap=held.snapshot();assert snap['pending_requests']==0
        status=next(m['status'] for m in frames if m['type']=='http.response.start')
        assert (status==200 and calls==['entered']) or (status==409 and calls==[])
        assert snap['close_sequence'] is not None and admission.snapshot()['active_scopes']==0;flags(snap)
    asyncio.run(scenario())


def test_anonymous_entry_and_producer_never_become_complete_activity_coverage():
    from backend.engine.application_launch_handshake import BackendWorkAdmission
    held=api().OriginalRequestActivityObserver();admission=BackendWorkAdmission(activity_observer=held)
    assert admission.enter(scope());admission.leave()
    with admission.producer():pass
    admission.close();snap=held.snapshot();assert snap['uncertain'] and not snap['all_settled'];flags(snap)
    assert admission.snapshot()=={'active_scopes':0,'unsupported':[]}


def test_late_observer_binding_cannot_adopt_prior_completed_activity():
    from backend.engine.application_launch_handshake import BackendWorkAdmission
    admission=BackendWorkAdmission();assert admission.enter(scope());admission.leave()
    held=api().OriginalRequestActivityObserver()
    with pytest.raises(api().ActivityObservationError):held.bind(admission)
    assert held.snapshot()['uncertain'] and not held.snapshot()['all_settled'];flags(held.snapshot())


def test_observer_event_overflow_remains_incomplete_without_changing_request_or_reasons():
    async def scenario():
        held,admission,middleware=setup(Response(b'ok'),max_events=2);frames=[]
        await middleware(scope('/uncovered'),receive,lambda msg:send(frames,msg));admission.close()
        snap=held.snapshot();assert snap['uncertain'] and not snap['all_settled'] and snap['event_count']<=2;flags(snap)
        assert admission.snapshot()=={'active_scopes':0,'unsupported':['uncovered_request_protocol']}
        assert frames[-1]['body']==b'ok'
    asyncio.run(scenario())


def test_default_none_business_return_values_counts_and_sticky_reasons_are_preserved():
    from backend.engine.application_launch_handshake import BackendWorkAdmission
    admission=BackendWorkAdmission();assert admission.enter(scope('/uncovered')) is True
    assert admission.leave() is None;assert admission.close() is None;assert admission.enter(scope()) is False
    assert admission.snapshot()=={'active_scopes':0,'unsupported':['uncovered_request_protocol']}


def test_copied_observer_and_unmatched_original_leave_never_qualify():
    from backend.engine.application_launch_handshake import BackendWorkAdmission
    held=api().OriginalRequestActivityObserver();admission=BackendWorkAdmission(activity_observer=held)
    with pytest.raises(api().ActivityObservationError):copy.copy(held)
    with pytest.raises(ValueError):admission.leave()
    admission.close();snap=held.snapshot();assert snap['uncertain'] and not snap['all_settled'];flags(snap)


def test_serialized_settlement_cannot_precede_atomic_original_count_decrement(monkeypatch):
    async def scenario():
        held,admission,middleware=setup(Response(b'ok'));mid=[];original=admission._activity_transition
        def observe(action,*args):
            original(action,*args)
            if action=='leave':
                assert admission._active==1
                admission.close()
                mid.append(held.snapshot())
        monkeypatch.setattr(admission,'_activity_transition',observe)
        async def discard(msg):pass
        await middleware(scope(),receive,discard)
        assert len(mid)==1 and not mid[0]['all_settled']
        flags(mid[0])
    asyncio.run(scenario())


def test_equal_but_foreign_scope_cannot_replace_the_original_token_scope():
    async def scenario():
        held,admission,middleware=setup(Response(b'ok'));original_scope=scope()
        carrier=admission.request_activity(middleware,middleware.app,original_scope)
        assert admission.enter(original_scope,activity=carrier)
        carrier.scope=scope()
        for message in ({'type':'http.response.start','status':200,'headers':[]},{'type':'http.response.body','body':b'ok'}):
            admission.observe_activity('send_attempt',carrier,message);admission.observe_activity('send_success',carrier,message)
        admission.leave(activity=carrier,normal_return=True);admission.close()
        assert held.snapshot()['uncertain'] and not held.snapshot()['all_settled'];flags(held.snapshot())
    asyncio.run(scenario())


def test_foreign_task_observation_does_not_borrow_original_request_owner():
    async def scenario():
        held,admission,middleware=setup(Response(b'ok'));sc=scope();carrier=admission.request_activity(middleware,middleware.app,sc)
        assert admission.enter(sc,activity=carrier)
        async def foreign():admission.observe_activity('send_attempt',carrier,{'type':'http.response.start','status':200})
        await asyncio.create_task(foreign());admission.leave(activity=carrier,normal_return=False);admission.close()
        assert held.snapshot()['uncertain'] and not held.snapshot()['all_settled'];flags(held.snapshot())
    asyncio.run(scenario())


def test_unknown_send_extension_is_forwarded_but_never_certifies_completion():
    async def scenario():
        frames=[]
        async def app(sc,rc,se):await se({'type':'http.response.pathsend','path':'private_path_not_to_record'})
        held,admission,middleware=setup(app);await middleware(scope(),receive,lambda msg:send(frames,msg));admission.close()
        assert frames==[{'type':'http.response.pathsend','path':'private_path_not_to_record'}]
        assert held.snapshot()['uncertain'] and not held.snapshot()['all_settled'] and 'private_' not in repr(held.snapshot());flags(held.snapshot())
    asyncio.run(scenario())


def test_request_bound_overflow_keeps_normal_outputs_and_sticky_uncertainty():
    async def scenario():
        held,admission,middleware=setup(Response(b'ok'),max_requests=1);frames=[]
        for _ in range(2):await middleware(scope(),receive,lambda msg:send(frames,msg))
        admission.close();snap=held.snapshot();assert snap['request_count']==2 and len(snap['records'])==1 and snap['uncertain'] and not snap['all_settled'];flags(snap)
        assert len([m for m in frames if m['type']=='http.response.body'])==2 and admission.snapshot()['active_scopes']==0
    asyncio.run(scenario())


def test_recording_failure_never_replaces_original_downstream_exception(monkeypatch):
    async def scenario():
        async def app(sc,rc,se):raise RuntimeError('original downstream failure')
        held,admission,middleware=setup(app)
        def broken(*args):raise OSError('controlled observer failure')
        monkeypatch.setattr(held,'event',broken)
        async def discard(msg):pass
        with pytest.raises(RuntimeError,match='original downstream failure'):await middleware(scope(),receive,discard)
        admission.close();assert held.snapshot()['uncertain'] and not held.snapshot()['all_settled'];flags(held.snapshot())
        assert admission.snapshot()['active_scopes']==0
    asyncio.run(scenario())


def test_old_unsupported_reason_survives_later_complete_original_http_envelope():
    async def scenario():
        held,admission,middleware=setup(Response(b'ok'));admission.uncovered('startup_background_protocols')
        async def discard(msg):pass
        await middleware(scope(),receive,discard);admission.close();snap=held.snapshot()
        assert snap['records'][0]['completed'] and not snap['all_settled'];flags(snap)
        assert admission.snapshot()=={'active_scopes':0,'unsupported':['startup_background_protocols']}
    asyncio.run(scenario())


@pytest.mark.parametrize('mode',['close','disconnect','send_failure'])
def test_original_websocket_envelope_or_failure_preserves_uncovered_protocol(mode):
    async def scenario():
        async def app(sc,rc,se):
            if mode=='disconnect':assert (await rc())['type']=='websocket.disconnect'
            else:await se({'type':'websocket.close','code':1000})
        held,admission,middleware=setup(app)
        async def recv():return {'type':'websocket.disconnect','code':1000}
        async def out(msg):
            if mode=='send_failure':raise OSError('original WS send failure')
        if mode=='send_failure':
            with pytest.raises(OSError):await middleware(scope('/ws/telemetry','websocket'),recv,out)
        else:await middleware(scope('/ws/telemetry','websocket'),recv,out)
        admission.close();snap=held.snapshot();assert not snap['all_settled'];flags(snap)
        if mode=='send_failure':assert snap['uncertain'] and not snap['records'][0]['completed']
        else:assert snap['records'][0]['completed']
        assert admission.snapshot()=={'active_scopes':0,'unsupported':['uncovered_request_protocol']}
    asyncio.run(scenario())


def test_original_condition_restoration_never_clears_observed_substitution():
    async def scenario():
        held,admission,middleware=setup(Response(b'ok'));original=admission._condition
        admission._condition=threading.Condition();assert held.snapshot()['uncertain']
        admission._condition=original;admission.close();assert held.snapshot()['uncertain'] and not held.snapshot()['all_settled'];flags(held.snapshot())
    asyncio.run(scenario())



@pytest.mark.parametrize('mode',['send_before_accept','duplicate_accept','send_after_close'])
def test_invalid_websocket_framing_is_uncertainty_even_with_a_final_close(mode):
    async def scenario():
        async def app(sc,rc,se):
            if mode=='send_before_accept':await se({'type':'websocket.send','text':'original bytes'})
            else:
                await se({'type':'websocket.accept'})
                if mode=='duplicate_accept':await se({'type':'websocket.accept'})
            await se({'type':'websocket.close','code':1000})
            if mode=='send_after_close':await se({'type':'websocket.send','text':'late original bytes'})
        held,admission,middleware=setup(app)
        async def discard(msg):pass
        await middleware(scope('/ws/telemetry','websocket'),receive,discard);admission.close();snap=held.snapshot()
        assert snap['uncertain'] and not snap['records'][0]['completed'];flags(snap)
        assert admission.snapshot()['unsupported']==['uncovered_request_protocol']
    asyncio.run(scenario())


def test_healthy_original_websocket_accept_send_disconnect_records_local_completion_only():
    async def scenario():
        async def app(sc,rc,se):
            await se({'type':'websocket.accept'});await se({'type':'websocket.send','text':'original payload'});await rc()
        held,admission,middleware=setup(app)
        async def recv():return {'type':'websocket.disconnect','code':1000}
        async def discard(msg):pass
        await middleware(scope('/ws/telemetry','websocket'),recv,discard);admission.close();snap=held.snapshot()
        assert snap['records'][0]['completed'] and not snap['uncertain'] and not snap['all_settled'];flags(snap)
        assert 'original payload' not in repr(snap)
    asyncio.run(scenario())


def test_copied_original_request_carrier_is_refused_and_original_data_stays_uncertain():
    async def scenario():
        held,admission,middleware=setup(Response(b'ok'));sc=scope();carrier=admission.request_activity(middleware,middleware.app,sc)
        assert admission.enter(sc,activity=carrier)
        with pytest.raises(api().ActivityObservationError):copy.copy(carrier)
        admission.leave(activity=carrier,normal_return=False);admission.close();snap=held.snapshot()
        assert snap['uncertain'] and not snap['all_settled'];flags(snap)
        assert admission.snapshot()['active_scopes']==0
    asyncio.run(scenario())


def test_unmatched_carrier_release_never_adopts_another_original_pending_request():
    async def scenario():
        held,admission,middleware=setup(Response(b'ok'));sc=scope();carrier=admission.request_activity(middleware,middleware.app,sc)
        assert admission.enter(sc,activity=carrier)
        other=admission.request_activity(middleware,middleware.app,scope());admission.leave(activity=other,normal_return=True);admission.close()
        snap=held.snapshot();assert snap['uncertain'] and not snap['all_settled'] and snap['pending_requests']==1;flags(snap)
        assert admission.snapshot()['active_scopes']==0
    asyncio.run(scenario())
