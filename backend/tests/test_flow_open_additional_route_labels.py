"""Finite data-only labels; no route ownership or handler execution is granted."""
from backend.engine.original_request_activity import _flow_open_get_route


def projection():
    return _flow_open_get_route


OLD={
 '/api/flowchart/pipeline':'FLOW_PIPELINE',
 '/api/flowchart/templates/single-segmentation':'FLOW_SINGLE_SEG_TEMPLATE',
 '/api/evaluation/results':'EVALUATION_RESULTS',
 '/api/flowchart/models/catalog':'FLOW_MODEL_CATALOG',
 '/api/flowchart/pipelines':'FLOW_PIPELINES',
 '/api/flowchart/pipeline/active/record':'FLOW_ACTIVE_RECORD',
 '/api/flowchart/pipeline/active-version':'FLOW_ACTIVE_VERSION',
 '/api/flowchart/draft':'FLOW_DRAFT',
 '/api/compute/profiles':'COMPUTE_PROFILES',
 '/api/compute/selection':'COMPUTE_SELECTION',
 '/api/flow-workspace/templates':'FLOW_WORKSPACE_TEMPLATES',
 '/api/flow-workspace/comparisons':'FLOW_WORKSPACE_COMPARISONS',
 '/api/dataset/images':'DATASET_IMAGES',
 '/api/dataset/library/images':'LIBRARY_IMAGES',
 '/api/flow-workspace/image-info':'FLOW_IMAGE_INFO',
}


def test_original_fifteen_literals_remain_exact():
 route=projection()
 for path,label in OLD.items(): assert route(path,'GET')==label


def test_exact_two_mount_related_literals():
 route=projection()
 assert route('/api/flowchart/execution-resources','GET')=='FLOW_EXECUTION_RESOURCES'
 assert route('/api/flowchart/changes','GET')=='FLOW_CHANGES'


def test_exact_saved_version_route_shape_only():
 route=projection();prefix='/api/flowchart/pipelines/'
 assert route(prefix+'0123456789abcdef'*2,'GET')=='FLOW_SAVED_VERSION'
 for suffix in ('','a'*31,'a'*33,'A'*32,'g'*32,'a'*32+'/', 'a'*32+'/activate','a'*32+'?token=private'):
  assert route(prefix+suffix,'GET')=='UNKNOWN'


def test_registered_thumbnail_path_template_is_fixed_label_only():
 route=projection();prefix='/api/dataset/thumbnail/'
 for suffix in ('preview','coordinate.png','nested/path.png','private-token-like-filename'):
  value=route(prefix+suffix,'GET');assert value=='DATASET_THUMBNAIL' and suffix not in value
 for path in (prefix[:-1],prefix,'/api/dataset/thumbnailish/preview','https://host/api/dataset/thumbnail/preview'):
  assert route(path,'GET')=='UNKNOWN'


def test_no_method_or_type_wildcard_and_no_unreviewed_literal():
 route=projection()
 paths=('/api/flowchart/execution-resources','/api/flowchart/changes',
  '/api/flowchart/pipelines/'+'a'*32,'/api/dataset/thumbnail/preview')
 for path in paths:
  for method in ('HEAD','POST','PUT','OPTIONS','get',None,True,1):assert route(path,method)=='UNKNOWN'
 class Foreign(str):pass
 for value in (None,True,1,[],{},Foreign('/api/flowchart/changes')):assert route(value,'GET')=='UNKNOWN'
 for path in ('/api/flowchart/changes/','/api/flowchart/changes?limit=1','/api/flowchart/changes/private',
              '/api/flowchart/unknown','/api/flowchart/templates/fixed-roi'):
  assert route(path,'GET')=='UNKNOWN'


def test_exact_conditionally_mounted_preflight_list_literal():
 route=projection()
 assert route('/api/export/flow/preflights','GET')=='FLOW_PREFLIGHTS'
 for path in ('/api/export/flow/preflights/','/api/export/flow/preflights?version_id=private',
              '/api/export/flow/preflight','/api/export/flow/preflights-extra'):
  assert route(path,'GET')=='UNKNOWN'
 for method in ('HEAD','POST','PUT','OPTIONS'):
  assert route('/api/export/flow/preflights',method)=='UNKNOWN'


def test_exact_retained_preflight_report_lowerhex_template():
 route=projection();prefix='/api/export/flow/preflights/'
 assert route(prefix+'0123456789abcdef'*2,'GET')=='FLOW_PREFLIGHT_REPORT'
 for suffix in ('','a'*31,'a'*33,'A'*32,'g'*32,'a'*32+'/',
                'a'*32+'/private','a'*32+'?target=private'):
  assert route(prefix+suffix,'GET')=='UNKNOWN'
 for method in ('HEAD','POST','PUT','OPTIONS'):
  assert route(prefix+'a'*32,method)=='UNKNOWN'
