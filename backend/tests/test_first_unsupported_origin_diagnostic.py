"""Original admission/middleware and typed refusal; diagnostic data only.

No app factory, listener, model, process, lease or native runtime is started.
The typed control constructs the original refusing attempt around a genuine
current ASGI carrier; it does not qualify that attempt's authority or lifetime.
"""
import asyncio
import json
from types import SimpleNamespace

from starlette.responses import Response

from backend.tests.test_original_request_activity_witness import setup, scope, receive
from backend.tests.test_owned_health_diagnostic import own_app, ordinary


def origin(observer):
    supplier = getattr(observer, 'first_unsupported_origin_diagnostic', None)
    return supplier() if supplier is not None else None


def typed_refusal(observer, admission, private_reason='private_reason_not_exported', *, original_scope=None, carrier=None):
    from backend.engine.original_current_project_request import _Attempt
    if carrier is None:
        carrier = next(row['carrier'] for row in observer._records.values() if not row['released']
            and (original_scope is None or row['carrier'].scope is original_scope))
    adapter = SimpleNamespace(admission=admission, observer=observer, shared=None)
    attempt = _Attempt(adapter, carrier)
    with admission._condition:attempt.fail(private_reason)
    assert attempt.failed


def test_direct_uncovered_enter_retains_first_request_without_changing_response_or_refusal():
    async def scenario():
        held, admission, middleware = setup(Response(b'original-body'))
        frames = []
        async def send(message): frames.append(message)
        sc = scope('/private-path-never-exported')
        sc['method'] = 'POST'
        await middleware(sc, receive, send)
        assert frames[-1]['body'] == b'original-body'
        assert admission.snapshot() == {'active_scopes': 0, 'unsupported': ['uncovered_request_protocol']}
        assert held._failures == [] and held._uncertain is False
        value = origin(held)
        assert value is not None, 'Original direct unsupported entry has no diagnostic origin'
        assert value['first'] == {'source': 'DIRECT_ENTER', 'family': 'ADMISSION',
            'request_identity': 1, 'entered_sequence': 1, 'observed_sequence': 1, 'method': 'POST'}
        assert 'private_' not in json.dumps(value) and 'private-path' not in json.dumps(value)
        admission.close()
    asyncio.run(scenario())


def test_first_direct_origin_is_immutable_across_duplicates_typed_failures_and_32_reason_cap():
    async def scenario():
        async def app(sc, rc, se):
            typed_refusal(held, admission)
            for i in range(40): held.poison('/private/token/' + str(i))
            await Response(b'unchanged')(sc, rc, se)
        held, admission, middleware = setup(app)
        async def send(message): pass
        await middleware(scope('/private-first'), receive, send)
        first = origin(held)['first']
        assert first == dict(source='DIRECT_ENTER', family='ADMISSION', request_identity=1,
            entered_sequence=1, observed_sequence=1, method='GET')
        assert len(held._failures) == 32 and len(json.dumps(origin(held)).encode()) < 1024
        assert '/private' not in json.dumps(origin(held))
        admission.close()
        assert held.snapshot()['all_settled'] is False
    asyncio.run(scenario())


def test_two_simultaneous_original_requests_attribute_only_the_actual_failing_carrier():
    async def scenario():
        first_waiting, second_entered = asyncio.Event(), asyncio.Event()
        first_scope, normal_scope = scope(), scope()
        async def app(sc, rc, se):
            if sc is first_scope:
                first_waiting.set()
                await second_entered.wait()
                typed_refusal(held, admission, original_scope=sc)
            else:
                second_entered.set()
                await first_waiting.wait()
                await asyncio.sleep(0)
            await Response(b'original')(sc, rc, se)
        held, admission, middleware = setup(app)
        async def send(message): pass
        await asyncio.gather(middleware(first_scope, receive, send), middleware(normal_scope, receive, send))
        first = origin(held)['first']
        assert first['family'] == 'CURRENT_PROJECT' and first['request_identity'] == 1
        assert first['entered_sequence'] == 1 and first['observed_sequence'] > 1
        assert len(held._records) == 2
        assert all(row['completed'] and row['normal_return'] for row in held._records.values())
        assert held._failures == ['current_project_private_reason_not_exported']
        admission.close()
    asyncio.run(scenario())


def test_earlier_unbound_poison_stays_unknown_and_later_typed_fail_cannot_claim_it():
    async def scenario():
        async def app(sc, rc, se):
            typed_refusal(held, admission)
            await Response(b'ok')(sc, rc, se)
        held, admission, middleware = setup(app)
        held.poison('owned_flow_private_forged_prefix')
        async def send(message): pass
        await middleware(scope(), receive, send)
        assert origin(held)['first'] == dict(source='OTHER_UNSUPPORTED', family='UNKNOWN',
            request_identity=None, entered_sequence=None, observed_sequence=0, method='NONE')
        assert held._failures == ['owned_flow_private_forged_prefix', 'current_project_private_reason_not_exported']
        admission.close()
    asyncio.run(scenario())


def test_foreign_observer_carrier_is_never_correlated_to_current_request():
    async def scenario():
        async def other_app(sc, rc, se):
            foreign.append(next(row['carrier'] for row in other._records.values()))
            await Response(b'foreign')(sc, rc, se)
        other, other_admission, other_middleware = setup(other_app)
        foreign = []
        async def send(message): pass
        await other_middleware(scope(), receive, send)
        async def app(sc, rc, se):
            typed_refusal(held, admission, carrier=foreign[0])
            await Response(b'unchanged')(sc, rc, se)
        held, admission, middleware = setup(app)
        await middleware(scope(), receive, send)
        first = origin(held)['first']
        assert first['family'] == 'CURRENT_PROJECT'
        assert first['request_identity'] is first['entered_sequence'] is None and first['method'] == 'NONE'
        assert held._failures == ['current_project_private_reason_not_exported']
        admission.close();other_admission.close()
    asyncio.run(scenario())


def test_other_original_uncovered_lane_precedes_later_typed_poison():
    async def scenario():
        async def app(sc, rc, se):
            typed_refusal(held, admission)
            await Response(b'original')(sc, rc, se)
        held, admission, middleware = setup(app)
        admission.uncovered('startup_background_protocols')
        async def send(message): pass
        await middleware(scope(), receive, send)
        assert origin(held)['first'] == dict(source='OTHER_UNSUPPORTED', family='ADMISSION',
            request_identity=None, entered_sequence=None, observed_sequence=0, method='NONE')
        assert admission.snapshot()['unsupported'] == ['startup_background_protocols', 'uncovered_request_protocol']
        admission.close()
    asyncio.run(scenario())


def test_optional_inspection_exception_cannot_change_original_refusal_or_response(monkeypatch):
    async def scenario():
        async def app(sc, rc, se):
            typed_refusal(held, admission)
            await Response(b'unchanged')(sc, rc, se)
        held, admission, middleware = setup(app)
        def unavailable(*args): raise ValueError('private optional inspection failure')
        monkeypatch.setattr(held, '_observe_poison_origin', unavailable)
        async def send(message): frames.append(message)
        frames = []
        await middleware(scope(), receive, send)
        assert frames[-1]['body'] == b'unchanged'
        assert held._failures == ['current_project_private_reason_not_exported'] and held._uncertain
        assert origin(held)['first']['family'] == 'UNKNOWN'
        admission.close()
    asyncio.run(scenario())


def test_copied_attempt_binding_cannot_correlate_its_current_carrier():
    async def scenario():
        from backend.engine.original_current_project_request import _Attempt
        async def app(sc, rc, se):
            carrier=next(row['carrier'] for row in held._records.values() if not row['released'])
            owner=SimpleNamespace(admission=admission,observer=held,shared=None)
            original=_Attempt(owner,carrier)
            copied=object.__new__(_Attempt)
            copied.__dict__.update(original.__dict__)
            with admission._condition:copied.fail('private_copied_attempt')
            await Response(b'original')(sc,rc,se)
        held,admission,middleware=setup(app)
        async def send(message): pass
        await middleware(scope(),receive,send)
        first=origin(held)['first']
        assert first['family']=='CURRENT_PROJECT'
        assert first['request_identity'] is first['entered_sequence'] is None
        assert held._failures==['current_project_private_copied_attempt']
        admission.close()
    asyncio.run(scenario())


def test_missing_earlier_direct_observation_cannot_be_claimed_by_later_typed_failure(monkeypatch):
    async def scenario():
        async def app(sc,rc,se):
            if sc['path']=='/health':typed_refusal(held,admission,original_scope=sc)
            await Response(b'unchanged')(sc,rc,se)
        held,admission,middleware=setup(app)
        original=held.observe_unsupported_origin
        def unavailable(*args):raise ValueError('private diagnostic unavailable')
        monkeypatch.setattr(held,'observe_unsupported_origin',unavailable)
        async def send(message):pass
        await middleware(scope('/uncovered-earlier'),receive,send)
        assert held._first_unsupported_origin is None and held._uncertain is False
        monkeypatch.setattr(held,'observe_unsupported_origin',original)
        await middleware(scope(),receive,send)
        first=origin(held)['first']
        assert first['source']=='OTHER_UNSUPPORTED' and first['family']=='UNKNOWN'
        assert first['request_identity'] is first['entered_sequence'] is None
        assert admission.snapshot()=={'active_scopes':0,'unsupported':['uncovered_request_protocol']}
        assert held._failures==['current_project_private_reason_not_exported']
        admission.close()
    asyncio.run(scenario())


def test_real_health_optional_sibling_uses_same_auth_and_keeps_original_subobjects(own_app):
    app,client,admission,held=own_app
    assert client.get('/private-origin-route',params={'token':'not-exported'},headers={
        'authorization':'Bearer not-exported','x-private':'not-exported'}).status_code==401
    before=admission.snapshot()
    assert client.get('/health').json()==ordinary(app)
    body=client.get('/health',headers={'x-vision-token':app.state.api_token}).json()
    value=body['owned_first_unsupported_origin'];old=body['owned_request_diagnostic']
    assert value['sequence']==old['sequence'] and value['request_count']==old['request_count']
    assert value['first']['source']=='DIRECT_ENTER' and value['first']['request_identity']==1
    assert body['owned_request_failure_diagnostic']['reason_sha256']==[]
    assert body['owned_request_failure_diagnostic']['failure_count']==0
    assert body['owned_ancillary_failure_attribution']['reasons']==[]
    assert set(old)=={'schema','sequence','request_count','sampled_active_scopes','pending_records',
        'settled_records','released_records','diagnostic_request_identity','diagnostic_request_in_flight',
        'uncertain','truncated','records','whole_writer_coverage','process_tree_exit_verified','can_release_launch_lease'}
    assert before==admission.snapshot()
    assert 'private-origin' not in json.dumps(body) and 'not-exported' not in json.dumps(body)


def test_oversized_optional_origin_cannot_suppress_original_bounded_final_packet(monkeypatch):
    """Original emitter/old snapshot, controlled optional supplier format boundary.

    The oversized optional supplier is deliberately invalid metadata, not a
    claim that the genuine fixed-size origin can naturally produce padding.
    The external descriptor sink is captured without writing any real FD.
    """
    from backend.engine import application_launch_handshake as handshake
    async def scenario():
        held,admission,middleware=setup(Response(b'original'))
        async def send(message):pass
        await middleware(scope(),receive,send)
        admission.close()
        old=held.final_drain_diagnostic(admission)
        assert old is not None
        genuine=origin(held)
        monkeypatch.setattr(held,'first_unsupported_origin_diagnostic',lambda:{**genuine,'padding':'x'*8192})
        writes=[]
        def sink(fd,raw):
            assert fd==2 and type(raw)is bytes
            writes.append(raw);return True
        monkeypatch.setattr(handshake,'_write_final_diagnostic_descriptor',sink)
        receipt=admission.snapshot()
        assert handshake._emit_final_drain_diagnostic(admission,receipt) is True
        assert len(writes)==1 and len(writes[0])<=8192
        packet=json.loads(writes[0].decode().split(' ',1)[1])
        assert 'first_unsupported_origin' not in packet
        packet.pop('capture_phase');packet.pop('receipt_sha256')
        assert packet==old
        assert admission.snapshot()==receipt and held._uncertain is False
    asyncio.run(scenario())


def test_unlocked_typed_fail_keeps_original_refusal_but_unknown_origin():
    async def scenario():
        from backend.engine.original_current_project_request import _Attempt
        async def app(sc,rc,se):
            carrier=next(row['carrier'] for row in held._records.values() if not row['released'])
            attempt=_Attempt(SimpleNamespace(admission=admission,observer=held,shared=None),carrier)
            attempt.fail('private_unlocked_failure')
            await Response(b'unchanged')(sc,rc,se)
        held,admission,middleware=setup(app)
        async def send(message):pass
        await middleware(scope(),receive,send)
        first=origin(held)['first']
        assert first['family']=='UNKNOWN' and first['request_identity'] is None
        assert held._failures==['current_project_private_unlocked_failure']
        assert admission.snapshot()=={'active_scopes':0,'unsupported':['uncovered_request_protocol']}
        admission.close()
    asyncio.run(scenario())


def test_original_typed_fail_poison_retains_its_family_and_existing_current_carrier():
    async def scenario():
        async def app(sc, rc, se):
            typed_refusal(held, admission)
            await Response(b'original-typed-body')(sc, rc, se)
        held, admission, middleware = setup(app)
        frames = []
        async def send(message): frames.append(message)
        await middleware(scope(), receive, send)
        assert frames[-1]['body'] == b'original-typed-body'
        assert admission.snapshot() == {'active_scopes': 0, 'unsupported': ['uncovered_request_protocol']}
        assert held._failures == ['current_project_private_reason_not_exported'] and held._uncertain
        value = origin(held)
        assert value is not None, 'Original typed fail poison has no diagnostic origin'
        assert value['first'] == {'source': 'TYPED_POISON', 'family': 'CURRENT_PROJECT',
            'request_identity': 1, 'entered_sequence': 1, 'observed_sequence': 1, 'method': 'GET'}
        assert 'private_' not in json.dumps(value)
        admission.close()
    asyncio.run(scenario())


def test_healthy_original_request_has_no_unsupported_origin_and_no_new_authority():
    async def scenario():
        held, admission, middleware = setup(Response(b'ok'))
        async def send(message): pass
        await middleware(scope(), receive, send)
        value = origin(held)
        assert value is not None and value['first'] is None
        assert all(value[k] is False for k in ('whole_writer_coverage', 'process_tree_exit_verified', 'can_release_launch_lease'))
        assert admission.snapshot() == {'active_scopes': 0, 'unsupported': []}
        admission.close()
        assert held.snapshot()['all_settled']
    asyncio.run(scenario())
