import json
from pathlib import Path
import pytest
from PIL import Image
from backend.engine import dataset_metadata as dm
from backend.tests.test_dataset_metadata_api import client_workspace
from backend.tests.test_team_data import workspace

def rows():
 return [{'relative_path':str(i)+'.png','content_hash':str(i),'product':str(i),'lot':str(i),'group':'original-A' if i<3 else str(i)} for i in range(8)]

def test_common_original_family_is_preserved_when_product_is_selected():
 value=dm.preview_split(rows(),['product'],.5,.25,.25,seed=7)
 assert len({value['assignments'][str(i)+'.png'] for i in range(3)})==1
 assert value['group_count']==6

def test_unknown_content_hash_cannot_join_unrelated_images():
 values=rows();values[0]['content_hash']=None
 with pytest.raises(ValueError,match='content'):dm.preview_split(values,['product'])

def test_split_qualification_binds_grouping_and_current_membership():
 from backend.engine.dataset_readiness import split_qualification,split_staleness
 value=split_qualification(rows(),['product'],7,{'task':'classification','active_labelset_id':'default'})
 assert not split_staleness(value,rows(),{'task':'classification','active_labelset_id':'default'})
 values=rows();values[0]['group']='changed'
 assert split_staleness(value,values,{'task':'classification','active_labelset_id':'default'})
 assert split_staleness(value,rows()[:-1],{'task':'classification','active_labelset_id':'default'})

def test_detection_readiness_rejects_classification_tag_and_invalid_geometry(tmp_path):
 from backend.engine.dataset_readiness import task_schema
 from backend.engine.annotation_storage import set_request_annotation_root,reset_request_annotation_root,dataset_annotation_dir
 source=tmp_path/'source';source.mkdir();image=source/'part.png';Image.new('RGB',(32,32)).save(image)
 token=set_request_annotation_root(tmp_path/'annotations')
 try:
  path=dataset_annotation_dir(source)/'part.json';path.parent.mkdir(parents=True,exist_ok=True)
  path.write_text(json.dumps({'annotations':[{'type':'tag','label':'defect'}]}))
  row={'file_path':str(image),'relative_path':'part.png','width':32,'height':32}
  assert task_schema(source,'detection',[row])['invalid_count']==1
  path.write_text(json.dumps({'annotations':[{'type':'bbox','label':'defect','bbox':[-1,0,20,20]}]}))
  assert task_schema(source,'detection',[row])['invalid_count']==1
  path.write_text(json.dumps({'annotations':[{'type':'bbox','label':'defect','bbox':[1,1,20,20]}]}))
  assert task_schema(source,'detection',[row])['invalid_count']==0
 finally:reset_request_annotation_root(token)

def test_segmentation_readiness_requires_single_channel_mask_in_active_scope(tmp_path):
 from backend.engine.dataset_readiness import task_schema
 from backend.engine.annotation_storage import set_request_annotation_root,reset_request_annotation_root,dataset_annotation_dir
 source=tmp_path/'source';source.mkdir();image=source/'part.png';Image.new('RGB',(32,32)).save(image)
 token=set_request_annotation_root(tmp_path/'annotations')
 try:
  path=dataset_annotation_dir(source)/'part.json';path.parent.mkdir(parents=True,exist_ok=True);mask=path.parent/'mask.png';Image.new('RGB',(32,32)).save(mask)
  path.write_text(json.dumps({'annotations':[],'mask_file':str(mask)}));row={'file_path':str(image),'relative_path':'part.png','width':32,'height':32}
  assert task_schema(source,'segmentation',[row])['invalid_count']==1
  Image.new('L',(32,32)).save(mask)
  assert task_schema(source,'segmentation',[row])['invalid_count']==0
 finally:reset_request_annotation_root(token)

def test_grouped_split_api_persists_receipt_and_refuses_changed_preview(client_workspace):
 client,project,source=client_workspace
 for i in range(6):Image.new('RGB',(100,80),(i*30,80,120)).save(source/f'part{i}.png')
 files=sorted(source.glob('*.png'))
 (source/'annotations.json').write_text(json.dumps({'images':[{'id':i,'file_name':f.name,'width':100,'height':80} for i,f in enumerate(files)],'categories':[{'id':1,'name':'part'}],'annotations':[{'id':i,'image_id':i,'category_id':1,'bbox':[1,1,20,20]} for i,f in enumerate(files)]}))
 metadata=client.get('/api/dataset/metadata').json()['items']
 for i,row in enumerate(metadata):
  response=client.patch('/api/dataset/metadata/'+row['image_uuid'],json={'expected_revision':row['revision'],'actor':'fixture','changes':{'product':f'product-{i}','group':'same-original' if i<3 else f'original-{i}'}})
  assert response.status_code==200,response.text
 request={'group_by':['product'],'train_ratio':.5,'val_ratio':.25,'test_ratio':.25,'seed':7}
 preview=client.post('/api/dataset/metadata/split',json=request);assert preview.status_code==200,preview.text
 saved=client.post('/api/dataset/metadata/split',json={**request,'apply':True,'expected_qualification_sha256':preview.json()['qualification']['sha256']});assert saved.status_code==200,saved.text
 reloaded=client.get('/api/dataset/metadata/split').json();assert not reloaded['stale'] and reloaded['group_count']==6
 assert reloaded['qualification']==saved.json()['qualification'] and reloaded['assignments']==saved.json()['assignments']
 path=Path(project['dataset_dir'])/'splits';before={p:p.read_bytes() for p in path.glob('*.json')}
 row=client.get('/api/dataset/metadata').json()['items'][0];client.patch('/api/dataset/metadata/'+row['image_uuid'],json={'expected_revision':row['revision'],'actor':'fixture','changes':{'lot':'new'}})
 assert client.get('/api/dataset/metadata/split').json()['stale']
 refused=client.post('/api/dataset/metadata/split',json={**request,'apply':True,'expected_qualification_sha256':preview.json()['qualification']['sha256']});assert refused.status_code==409
 assert before=={p:p.read_bytes() for p in path.glob('*.json')}

def test_heldout_shortage_is_unavailable_without_writing_split(client_workspace):
 client,project,source=client_workspace
 for row in client.get('/api/dataset/metadata').json()['items']:
  client.patch('/api/dataset/metadata/'+row['image_uuid'],json={'expected_revision':row['revision'],'actor':'fixture','changes':{'group':'one-original'}})
 result=client.post('/api/dataset/metadata/split',json={'group_by':['group'],'apply':True})
 assert result.status_code==422 and result.json()['detail']['availability']=='unavailable'
 assert not [p for p in (Path(project['dataset_dir'])/'splits').glob('*.json') if not p.name.startswith('.')]

def test_actual_primary_loader_filters_approved_cohort_and_pins_receipt(workspace):
 from backend.engine import team_data as td
 from backend.engine.dataset_loaders import ClassificationDataset,split_root_scope
 from backend.api.routes_dataset import _write_split_manifest
 from backend.engine.training_provenance import bind_training_version,validate_training_binding
 project,source=workspace;assignments={}
 for category in ('OK','NG'):
  (source/category).mkdir()
  for i in range(2):
   path=source/category/f'{i}.png';Image.new('RGB',(40,30),(50+i*100,80,120)).save(path)
   assignments[path.relative_to(source).as_posix()]='train' if category=='OK' else 'val'
 for name in ('OK/0.png','NG/0.png'):
  row=td.image_state(project,source,source/name)
  dm.update_metadata(Path(project['project_dir']),source,row['image_uuid'],row['revision'],'fixture-reviewer',{'workflow_state':'approved'})
 td.update_settings(project,source,1,'fixture-owner',{'approved_only_training':True})
 with split_root_scope(Path(project['dataset_dir'])/'splits'):
  _write_split_manifest(source,assignments,7)
  train=ClassificationDataset(source,split='train');val=ClassificationDataset(source,split='val')
  assert [p.relative_to(source).as_posix() for p,_ in train.samples]==['OK/0.png']
  assert [p.relative_to(source).as_posix() for p,_ in val.samples]==['NG/0.png']
  assert train[0][0].shape[0]==3 and val[0][0].shape[0]==3
  binding=bind_training_version(project,source);validate_training_binding(binding)
  receipt=Path(binding['version_dir'])/'team-data.json';assert json.loads(receipt.read_text())==binding['team_data']
  assert {r['relative_path'] for r in binding['team_data']['eligibility']}=={'OK/0.png','NG/0.png'}
  row=td.image_state(project,source,source/'OK/0.png')
  dm.update_metadata(Path(project['project_dir']),source,row['image_uuid'],row['revision'],'fixture-reviewer',{'workflow_state':'needs_review'})
  with pytest.raises(ValueError,match='Team-data'):validate_training_binding(binding)
