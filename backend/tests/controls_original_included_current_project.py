import asyncio
import pytest

@pytest.fixture
def owned_app(tmp_path,monkeypatch):
 from backend.engine import application_launch_handshake as h
 from backend import main
 admission=h._new_original_work_admission()
 monkeypatch.setattr(h,'_CACHE',{'admission':admission})
 app=main.create_app(project_dir=str(tmp_path/'owned-projects'))
 return app,admission

async def current(app,on_start=None,query=b''):
 scope={'type':'http','asgi':{'version':'3.0'},'http_version':'1.1','scheme':'http','method':'GET','path':'/api/project/current','raw_path':b'/api/project/current','query_string':query,'headers':[(b'x-vision-token',app.state.api_token.encode())],'client':('127.0.0.1',1),'server':('127.0.0.1',2)}
 async def receive():return {'type':'http.request','body':b'','more_body':False}
 frames=[]
 async def send(message):
  if message['type']=='http.response.start' and on_start is not None:on_start(scope)
  frames.append(message)
 await app(scope,receive,send)
 return frames

def test_actual_original_factory_first_current_can_complete_owned_effects(owned_app):
 app,admission=owned_app
 frames=asyncio.run(current(app));assert frames[0]['status']==200
 assert admission.snapshot()=={'active_scopes':0,'unsupported':[]}
 named=admission._current_project_requests.snapshot()
 assert named['pending']==0 and named['records']==[{'settled':True,'failed':False,'handler_calls':1,'awaited_worker_return':True,'storage_scopes':1,'resolution_calls':3,'publications':4,'database_context_exits':3}]
 assert all(named[k] is False for k in ('whole_writer_coverage','process_tree_exit_verified','can_release_launch_lease'))
 admission.close();assert admission.drain(.03)['status']=='managed_scopes_drained'



def helper(app,admission):
 held=admission._current_project_requests
 assert held.included_current is not None,'These controls require actual original modern runtime; no fake dispatcher or skipped credit'
 app.middleware_stack=app.build_middleware_stack()
 assert held.original()
 return held,held.included_current


def test_original_producer_context_is_owned_and_shared_original_route_never_changes(owned_app):
 from backend.api import routes_project
 app,admission=owned_app;held,m=helper(app,admission)
 route=m.route;before=(route.endpoint,route.dependant.call,route.app)
 assert m.context is None and all(b['cache'] is None and not b['minted'] for b in m.branches)
 frames=asyncio.run(current(app));assert frames[0]['status']==200
 assert (route.endpoint,route.dependant.call,route.app)==before
 assert route in routes_project.router.routes and m.context.original_route is route
 assert m.selected_once and m.context_fields.owner is m.context
 assert m.context.dependant is m.dependant_fields.owner
 assert all(b['cache'] is b['node']._effective_candidates and len(b['minted'])==len(b['rows']) for b in m.branches)
 assert admission.snapshot()=={'active_scopes':0,'unsupported':[]}


@pytest.mark.parametrize('mode',['prefix','router','methods','endpoint','producer','preceding_matches','unknown_node'])
def test_changed_original_modern_dispatch_before_ingress_is_sticky_refusal(owned_app,monkeypatch,mode):
 from fastapi.routing import APIRouter
 from types import MethodType
 app,admission=owned_app;held,m=helper(app,admission)
 if mode=='prefix':monkeypatch.setattr(m.branch.include_context,'prefix','/changed-original-prefix')
 elif mode=='router':monkeypatch.setattr(m.branch,'original_router',APIRouter())
 elif mode=='methods':monkeypatch.setattr(m.route,'methods',{'GET','POST'})
 elif mode=='endpoint':
  from functools import wraps
  original=m.route.endpoint
  @wraps(original)
  def changed(request):return original(request)
  monkeypatch.setattr(m.route,'endpoint',changed)
 elif mode=='producer':
  original=m.included_type._build_effective_context
  def changed(owner,route):return original(owner,route)
  monkeypatch.setattr(m.branch,'_build_effective_context',MethodType(changed,m.branch))
 elif mode=='preceding_matches':
  preceding=m.direct[0][0];original=preceding.matches
  def changed(owner,scope):return original(scope)
  monkeypatch.setattr(preceding,'matches',MethodType(changed,preceding))
 elif mode=='unknown_node':monkeypatch.setattr(app.router,'routes',[*app.router.routes[:1],object(),*app.router.routes[1:]])
 assert not held.original()
 if mode=='unknown_node':
  with pytest.raises(AttributeError):asyncio.run(current(app))
 else:assert asyncio.run(current(app))[0]['status'] in (200,404)
 admission.close();receipt=admission.drain(.03)
 assert receipt['status']=='refused' and 'uncovered_request_protocol' in receipt['unsupported']
 assert held.snapshot()['records']==[]


@pytest.mark.parametrize('mode',['prefix','router_version','methods','endpoint','context_copy','scope_context_copy','carrier','query','cache_copy','cache_version_equal_foreign','dependant_call'])
def test_original_modern_mutation_after_enrollment_cannot_settle(owned_app,monkeypatch,mode):
 import copy
 app,admission=owned_app;held,m=helper(app,admission)
 def at_start(scope):
  assert admission.snapshot()['active_scopes']==2 and held.snapshot()['pending']==1
  assert m.context is not None and m.selected_once
  if mode=='prefix':monkeypatch.setattr(m.branch.include_context,'prefix','/changed')
  elif mode=='router_version':monkeypatch.setattr(m.branch.original_router,'_routes_version',m.branch.original_router._routes_version+1)
  elif mode=='methods':monkeypatch.setattr(m.route,'methods',{'GET','POST'})
  elif mode=='endpoint':monkeypatch.setattr(m.route,'endpoint',lambda request:{})
  elif mode=='context_copy':m.context=copy.copy(m.context)
  elif mode=='scope_context_copy':scope[m.framework._FASTAPI_SCOPE_KEY][m.framework._FASTAPI_EFFECTIVE_ROUTE_CONTEXT_KEY]=copy.copy(m.context)
  elif mode=='carrier':scope['route']=object()
  elif mode=='query':scope['query_string']=b'changed=1'
  elif mode=='cache_copy':m.branch._effective_candidates=list(m.branch._effective_candidates)
  elif mode=='cache_version_equal_foreign':
   class EqualVersion:
    def __eq__(self,other):return True
   m.branch._effective_candidates_version=EqualVersion()
  elif mode=='dependant_call':m.context.dependant.call=lambda request:{}
  assert not held.original()
 assert asyncio.run(current(app,at_start))[0]['status']==200
 admission.close();receipt=admission.drain(.03)
 assert receipt['status']=='refused' and receipt['active_scopes']==1 and 'uncovered_request_protocol' in receipt['unsupported']
 assert held.snapshot()['pending']==1 and held.snapshot()['records'][0]['failed'] and not held.snapshot()['records'][0]['settled']


def test_original_producer_called_without_original_active_request_is_not_late_authority(owned_app):
 app,admission=owned_app;held,m=helper(app,admission)
 real=m.branch.effective_candidates()
 assert real and m.failed
 assert asyncio.run(current(app))[0]['status']==200
 admission.close();r=admission.drain(.03)
 assert r['status']=='refused' and 'uncovered_request_protocol' in r['unsupported']
 assert held.snapshot()['records']==[]


def test_original_second_current_stays_uncovered_and_never_rebinds_first_context(owned_app):
 app,admission=owned_app;held,m=helper(app,admission)
 assert asyncio.run(current(app))[0]['status']==200
 first=m.observation_binding
 assert asyncio.run(current(app))[0]['status']==200
 assert m.observation_binding is first and len(held.snapshot()['records'])==1
 admission.close();r=admission.drain(.03)
 assert r['status']=='refused' and r['active_scopes']==0 and 'uncovered_request_protocol' in r['unsupported']


def test_original_nonempty_query_cannot_enroll_modern_named_handler(owned_app):
 app,admission=owned_app;held,m=helper(app,admission)
 assert asyncio.run(current(app,query=b'unqualified=1'))[0]['status']==200
 admission.close();r=admission.drain(.03)
 assert r['status']=='refused' and r['active_scopes']==0 and 'uncovered_request_protocol' in r['unsupported']
 assert held.snapshot()['records']==[]


def test_original_existing_unsupported_reason_survives_genuine_modern_success(owned_app):
 app,admission=owned_app;held,m=helper(app,admission)
 admission.uncovered('startup_background_protocols')
 assert asyncio.run(current(app))[0]['status']==200
 assert held.snapshot()['records'][0]['settled']
 admission.close();r=admission.drain(.03)
 assert r=={'status':'refused','active_scopes':0,'unsupported':['startup_background_protocols']}



def test_preprimed_original_factory_cache_is_explicit_refusal(tmp_path,monkeypatch):
 from backend import main
 from backend.engine import application_launch_handshake as h
 from backend.engine.original_included_current_project import OriginalIncludedCurrentProject,IncludedCurrentProjectError
 from backend.api import routes_project
 plain=h.BackendWorkAdmission()
 monkeypatch.setattr(h,'_CACHE',{'admission':plain})
 app=main.create_app(project_dir=str(tmp_path/'owned-plain'))
 assert asyncio.run(current(app))[0]['status']==200
 with pytest.raises(IncludedCurrentProjectError,match='Nonfresh'):
  OriginalIncludedCurrentProject(app,h._new_original_work_admission(),routes_project.router,main.artifacts_router)


def test_genuine_original_send_error_keeps_modern_named_effects_pending(owned_app):
 app,admission=owned_app;held,m=helper(app,admission)
 def at_start(scope):raise OSError('original owned ASGI send refused')
 with pytest.raises(OSError,match='original owned ASGI send refused'):asyncio.run(current(app,at_start))
 admission.close();r=admission.drain(.03)
 assert r['status']=='refused' and r['active_scopes']==1 and 'uncovered_request_protocol' in r['unsupported']
 assert held.snapshot()['pending']==1 and held.snapshot()['records'][0]['failed']


def test_genuine_original_database_error_cannot_settle_modern_scope(owned_app):
 import sqlite3
 app,admission=owned_app;held,m=helper(app,admission)
 with app.state.context_registry._db() as db:
  db.execute("CREATE TRIGGER owned_refusal BEFORE INSERT ON projects BEGIN SELECT RAISE(ABORT,'original owned registration refused'); END")
 with pytest.raises(sqlite3.IntegrityError,match='original owned registration refused'):asyncio.run(current(app))
 admission.close();r=admission.drain(.03)
 assert r['status']=='refused' and r['active_scopes']==1 and 'uncovered_request_protocol' in r['unsupported']
 assert held.snapshot()['pending']==1 and held.snapshot()['records'][0]['failed']



def test_original_include_context_path_callee_extra_write_never_gains_completion(owned_app,monkeypatch,tmp_path):
 app,admission=owned_app;held,m=helper(app,admission)
 kind=type(m.branch.include_context);original=kind.path_for
 marker=tmp_path/'unknown-original-path-callee-writer.txt'
 def changed(owner,route):
  marker.write_text('actual unknown original producer callee write')
  return original(owner,route)
 monkeypatch.setattr(kind,'path_for',changed)
 assert asyncio.run(current(app))[0]['status']==200 and marker.is_file()
 admission.close();r=admission.drain(.03)
 assert r['status']=='refused' and 'uncovered_request_protocol' in r['unsupported']
 assert held.snapshot()['records']==[] or not held.snapshot()['records'][0]['settled']


def test_original_strict_default_placeholder_mutation_is_not_copied_configuration_authority(owned_app,monkeypatch):
 from fastapi.datastructures import DefaultPlaceholder
 app,admission=owned_app;held,m=helper(app,admission)
 original=m.branch.original_router.strict_content_type
 assert type(original) is DefaultPlaceholder and original.value is True
 monkeypatch.setattr(original,'value',False)
 assert asyncio.run(current(app))[0]['status']==200
 admission.close();r=admission.drain(.03)
 assert r['status']=='refused' and 'uncovered_request_protocol' in r['unsupported']
 assert held.snapshot()['records']==[] or not held.snapshot()['records'][0]['settled']



@pytest.mark.parametrize('kind',['value_selector','context_constructor','bound_handler','path_after_enrollment'])
def test_original_producer_callee_substitution_cannot_settle_scope(owned_app,monkeypatch,kind,tmp_path):
 from types import MethodType
 app,admission=owned_app;held,m=helper(app,admission)
 marker=tmp_path/('changed-original-'+kind+'.txt')
 if kind=='value_selector':
  original=m.framework.get_value_or_default
  def changed(*args,**kwargs):marker.write_text('actual original callee substitution');return original(*args,**kwargs)
  monkeypatch.setattr(m.framework,'get_value_or_default',changed)
 elif kind=='context_constructor':
  original=m.context_type.__init__
  def changed(owner,*args,**kwargs):marker.write_text('actual original constructor substitution');return original(owner,*args,**kwargs)
  monkeypatch.setattr(m.context_type,'__init__',changed)
 elif kind=='bound_handler':
  original=m.route.get_route_handler
  def changed(owner):marker.write_text('actual original bound handler substitution');return original()
  monkeypatch.setattr(m.route,'get_route_handler',MethodType(changed,m.route))
 if kind=='path_after_enrollment':
  def at_start(scope):
   original=type(m.branch.include_context).path_for
   def changed(owner,route):return original(owner,route)
   monkeypatch.setattr(type(m.branch.include_context),'path_for',changed)
  assert asyncio.run(current(app,at_start))[0]['status']==200
 else:assert asyncio.run(current(app))[0]['status']==200 and marker.is_file()
 admission.close();r=admission.drain(.03)
 assert r['status']=='refused' and 'uncovered_request_protocol' in r['unsupported']
 assert held.snapshot()['records']==[] or not held.snapshot()['records'][0]['settled']
