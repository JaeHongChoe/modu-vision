"""Real known Starlette Middleware APIs; original whole-ASGI request path."""
import asyncio,json
from pathlib import Path
import httpx
import pytest

@pytest.fixture
def owned_app(tmp_path,monkeypatch):
 from backend.engine import application_launch_handshake as h
 from backend import main
 admission=h._new_original_work_admission()
 monkeypatch.setattr(h,'_CACHE',{'admission':admission})
 app=main.create_app(project_dir=str(tmp_path/'owned-projects'))
 return app,admission

async def current(app,on_start=None):
 scope={'type':'http','asgi':{'version':'3.0'},'http_version':'1.1','scheme':'http','method':'GET','path':'/api/project/current','raw_path':b'/api/project/current','query_string':b'','headers':[(b'x-vision-token',app.state.api_token.encode())],'client':('127.0.0.1',1),'server':('127.0.0.1',2)}
 async def receive():return {'type':'http.request','body':b'','more_body':False}
 frames=[]
 async def send(message):
  if message['type']=='http.response.start' and on_start is not None:on_start()
  frames.append(message)
 await app(scope,receive,send)
 return frames

def test_actual_runtime_create_app_current_business_and_exact_protocol_boundary(owned_app,tmp_path):
 import starlette,fastapi
 app,admission=owned_app
 frames=asyncio.run(current(app))
 assert frames[0]['status']==200
 held=admission._current_project_requests;named=held.snapshot()
 assert held.middleware_binding.original(app.user_middleware)
 if held.route is None:
  # Actual modern FastAPI's distinct lazy included-router dispatch remains
  # unqualified by the unchanged original route guard. Middleware compatibility
  # restores business startup; it supplies no new route or whole authority.
  from fastapi.routing import _IncludedRouter
  assert any(type(r) is _IncludedRouter for r in app.router.routes)
  assert all(set(vars(e))=={'cls','args','kwargs'} for e in app.user_middleware)
  assert named['records']==[] and named['pending']==0
  assert admission.snapshot()=={'active_scopes':0,'unsupported':['uncovered_request_protocol']}
  admission.close();assert admission.drain(.03)['status']=='refused'
 else:
  assert admission.snapshot()=={'active_scopes':0,'unsupported':[]}
  assert named['records'][0]['settled'] and named['pending']==0
  admission.close();assert admission.drain(.03)['status']=='managed_scopes_drained'
 assert all(named[k] is False for k in ('whole_writer_coverage','process_tree_exit_verified','can_release_launch_lease'))
 (tmp_path/'actual-runtime.json').write_text(json.dumps({'starlette':starlette.__version__,'fastapi':fastapi.__version__,'schemas':[sorted(vars(e)) for e in app.user_middleware],'active_before_close':0,'first_current_status':frames[0]['status'],'named_route_eligible':held.route is not None,'whole_flags':False}))


def options(entry):
 fields=vars(entry)
 if set(fields)=={'cls','options'}:return fields['options']
 assert set(fields)=={'cls','args','kwargs'}
 return fields['kwargs']

@pytest.mark.parametrize('phase',['before_ingress','after_enrollment'])
def test_real_nested_cors_mutation_cannot_settle_named_original_scope(owned_app,phase):
 from starlette.middleware.cors import CORSMiddleware
 app,admission=owned_app
 held=admission._current_project_requests
 app.middleware_stack=app.build_middleware_stack()
 assert held.middleware_binding.original(app.user_middleware)
 if held.route is not None:assert held.original()
 entry=next(e for e in app.user_middleware if e.cls is CORSMiddleware)
 original=options(entry)['allow_origins']
 def mutate():original.append('https://owned.invalid')
 if phase=='before_ingress':mutate();frames=asyncio.run(current(app))
 else:frames=asyncio.run(current(app,mutate))
 assert frames[0]['status']==200
 assert not held.middleware_binding.original(app.user_middleware) and not held.original()
 admission.close();receipt=admission.drain(.03)
 assert receipt['status']=='refused' and 'uncovered_request_protocol' in receipt['unsupported']
 if phase=='before_ingress' or held.route is None:assert held.snapshot()['records']==[] and receipt['active_scopes']==0
 else:assert held.snapshot()['pending']==1 and held.snapshot()['records'][0]['failed'] and receipt['active_scopes']==1

@pytest.mark.parametrize('mutation',['entry','class','options_container','nested_equal_copy','extra_field','removed_field','collection'])
def test_original_entry_class_layout_and_config_identity_mutations_refuse(owned_app,mutation):
 from starlette.middleware import Middleware
 from starlette.middleware.cors import CORSMiddleware
 app,admission=owned_app;held=admission._current_project_requests
 app.middleware_stack=app.build_middleware_stack();assert held.middleware_binding.original(app.user_middleware)
 index=next(i for i,e in enumerate(app.user_middleware) if e.cls is CORSMiddleware);entry=app.user_middleware[index]
 config=options(entry)
 if mutation=='entry':app.user_middleware[index]=Middleware(CORSMiddleware,**config)
 elif mutation=='class':entry.cls=object
 elif mutation=='options_container':vars(entry)['options' if 'options' in vars(entry) else 'kwargs']=dict(config)
 elif mutation=='nested_equal_copy':config['allow_origins']=list(config['allow_origins'])
 elif mutation=='extra_field':entry.unknown_original_field=True
 elif mutation=='removed_field':del vars(entry)['options' if 'options' in vars(entry) else 'kwargs']
 elif mutation=='collection':app.user_middleware=list(app.user_middleware)
 assert not held.original()
 # No unknown-layout build/callback is executed; capture refusal grants no authority.
 assert all(held.snapshot()[k] is False for k in ('whole_writer_coverage','process_tree_exit_verified','can_release_launch_lease'))

@pytest.mark.parametrize('variant',['normal_keyword','original_positional'])
def test_actual_original_middleware_schema_and_positional_identity(variant):
 from starlette.middleware import Middleware
 from starlette.middleware.cors import CORSMiddleware
 from backend.engine.original_current_project_request import _OriginalMiddlewareConfiguration
 if variant=='original_positional' and 'options' in vars(Middleware(CORSMiddleware)):
  # Legacy constructor explicitly has no positional API; the same actual positive
  # is exercised by its real supported keyword representation, never a fake class.
  entry=Middleware(CORSMiddleware,allow_origins=['null'])
 else:
  entry=Middleware(CORSMiddleware,['null']) if variant=='original_positional' else Middleware(CORSMiddleware,allow_origins=['null'])
 entries=[entry];capture=_OriginalMiddlewareConfiguration(entries,{CORSMiddleware},())
 assert capture.original(entries)
 if 'args' in vars(entry) and entry.args:
  entry.args[0].append('https://owned.invalid');assert not capture.original(entries)
 else:
  options(entry)['allow_origins'].append('https://owned.invalid');assert not capture.original(entries)

@pytest.mark.parametrize('mutation',['unknown_class','unhashable_class','extra_schema','missing_schema','bad_options_type','entry_subclass','cycle','unknown_nested','overflow','bad_key'])
def test_unknown_original_middleware_configuration_is_explicit_refusal(mutation):
 from starlette.middleware import Middleware
 from starlette.middleware.cors import CORSMiddleware
 from backend.engine.original_current_project_request import _OriginalMiddlewareConfiguration,CurrentProjectObservationError
 entry=Middleware(CORSMiddleware,allow_origins=['null'])
 if mutation=='unknown_class':entry.cls=object
 elif mutation=='unhashable_class':entry.cls=[]
 elif mutation=='extra_schema':entry.other=True
 elif mutation=='missing_schema':del vars(entry)['options' if 'options' in vars(entry) else 'kwargs']
 elif mutation=='bad_options_type':vars(entry)['options' if 'options' in vars(entry) else 'kwargs']=[]
 elif mutation=='entry_subclass':
  class OtherMiddleware(Middleware):pass
  entry=OtherMiddleware(CORSMiddleware)
 elif mutation=='cycle':
  cyclic=[];cyclic.append(cyclic);options(entry)['allow_origins']=cyclic
 elif mutation=='unknown_nested':options(entry)['unknown']=object()
 elif mutation=='overflow':options(entry)['allow_origins']=['x']*257
 elif mutation=='bad_key':options(entry)[1]='unknown'
 with pytest.raises(CurrentProjectObservationError):_OriginalMiddlewareConfiguration([entry],{CORSMiddleware},())


def test_original_modern_args_container_replacement_is_not_equal_configuration():
 from starlette.middleware import Middleware
 from starlette.middleware.cors import CORSMiddleware
 from backend.engine.original_current_project_request import _OriginalMiddlewareConfiguration
 entry=Middleware(CORSMiddleware,allow_origins=['null']);entries=[entry]
 capture=_OriginalMiddlewareConfiguration(entries,{CORSMiddleware},())
 if 'args' in vars(entry):
  entry.args=(None,)
 else:
  entry.args=()
 assert not capture.original(entries)


@pytest.mark.parametrize('slot',['__init__','__iter__'])
def test_foreign_callable_equality_cannot_replace_original_middleware_method(monkeypatch,slot):
 from starlette.middleware import Middleware
 from starlette.middleware.cors import CORSMiddleware
 from backend.engine.original_current_project_request import _OriginalMiddlewareConfiguration
 entries=[Middleware(CORSMiddleware,allow_origins=['null'])]
 capture=_OriginalMiddlewareConfiguration(entries,{CORSMiddleware},())
 assert capture.original(entries)
 class EqualForeignCallable:
  def __eq__(self,other):return True
  def __call__(self,*args,**kwargs):raise AssertionError('Foreign method must not execute')
 foreign=EqualForeignCallable()
 monkeypatch.setattr(Middleware,slot,foreign)
 assert not capture.original(entries)
