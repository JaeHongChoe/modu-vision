"""Owned signed schema2 macOS layout controls; no real publisher/OS installation."""
import json
import os
import platform
from pathlib import Path
import stat
import subprocess
import zipfile

import pytest
from backend.tests.test_service_s6_04 import canonical,fixture,plan,sha
from backend.tests.test_global_migration import owned

APP='ModuFixture.app'
ENTRY=APP+'/Contents/MacOS/fixture'
FRAME=APP+'/Contents/Frameworks/Fixture.framework'
FILES={ENTRY:b'#!/bin/sh\ncat "$(dirname "$0")/../Frameworks/Fixture.framework/Resources/marker.txt"\n',
       FRAME+'/Versions/A/Resources/marker.txt':b'owned-native-layout\n',
       FRAME+'/Versions/A/Fixture':b'owned non-executable framework content'}
LINKS={FRAME+'/Versions/Current':'A',FRAME+'/Resources':'Versions/Current/Resources',FRAME+'/Fixture':'Versions/Current/Fixture'}

def native(tmp_path,change=None):
 value=fixture(tmp_path);payloads=dict(FILES);links=dict(LINKS)
 document={'schema_version':2,'version':'1.0.0','platform':'darwin','arch':value['target']['arch'],
           'entrypoint':ENTRY,'files':[{'path':p,'sha256':sha(v),'size':len(v),'executable':p==ENTRY}for p,v in payloads.items()],
           'links':[{'path':p,'target':v}for p,v in links.items()]}
 if change:change(document,payloads,links)
 archive=value['directory']/'application.zip'
 with zipfile.ZipFile(archive,'w')as bundle:
  bundle.writestr('portable-application.json',canonical(document))
  for p,raw in payloads.items():bundle.writestr(p,raw)
  for p,target in links.items():
   row=zipfile.ZipInfo(p);row.create_system=3;row.external_attr=(stat.S_IFLNK|0o777)<<16;bundle.writestr(row,target.encode())
 raw=archive.read_bytes();value['payload'].update(platform=document['platform'],sha256=sha(raw),size=len(raw));value['payload']['artifacts'][0].update(sha256=sha(raw),size=len(raw));value['target']['platform']=document['platform'];value['sign'](value['payload']);value['document']=document
 return value

@pytest.mark.skipif(platform.system()!='Darwin',reason='Actual macOS host required for the signed native cutover')
def test_signed_native_internal_framework_install_launch_and_forward_recovery(tmp_path):
 from backend.engine import runtime_update as update
 from backend.engine.global_store_paths import active_generation,resolve_store_path
 root,scopes,*_=owned(tmp_path);value=native(tmp_path);proposal=plan(root,value);review=update.review_update(proposal);assert review['application_layout']=='darwin-app/v2'and review['application_link_count']==3 and review['application_started']is False
 installed=update.install_update(root,proposal);assert installed['status']=='committed'
 command=update.launch_plan(root,value['authority'],pinned_authority_sha256=value['pinned_authority_sha256']);app=Path(command['argv'][0]).parents[2]
 for file,target in LINKS.items():assert os.readlink(app.parent/file)==target
 before=active_generation(root)[1];resolve_store_path(root/scopes['profiles']).write_bytes(b'{"profiles":[],"selected":null,"native_new_write":true}')
 process=subprocess.run(command['argv'],capture_output=True,text=True,timeout=5);assert process.returncode==0 and process.stdout=='owned-native-layout\n'
 fixed=update.recover_update(root,installed['update_id'],action='forward');assert fixed['status']=='committed'and active_generation(root)[1]['fence']==before['fence']+1
 assert json.loads(resolve_store_path(root/scopes['profiles']).read_bytes())['native_new_write']
 assert (root/'projects/labels.json').read_bytes()==b'{"label":"original"}'
 command=update.launch_plan(root,value['authority'],pinned_authority_sha256=value['pinned_authority_sha256']);assert subprocess.run(command['argv'],capture_output=True,text=True,timeout=5).stdout=='owned-native-layout\n'

@pytest.mark.parametrize('change',['absolute','escape','backslash','dangling','cycle','ancestor','link_payload','unsigned_link','file_through_link','case_collision','entrypoint_alias','outside_app','linux_schema2','schema1_link','link_metadata_extra','regular_checksum'])
def test_invalid_signed_native_layout_never_stages_or_changes_original(tmp_path,change):
 from backend.engine import runtime_update as update
 root,scopes,*_=owned(tmp_path)
 def modify(d,files,links):
  key=FRAME+'/Resources'
  if change in ['absolute','escape','backslash','dangling','ancestor']:
   target={'absolute':'/tmp/native-outside','escape':'../../../../../../outside','backslash':'Versions\\A\\Resources','dangling':'Versions/A/absent','ancestor':'.'}[change];links[key]=target;next(r for r in d['links']if r['path']==key)['target']=target
  elif change=='cycle':
   links[key]='Fixture';links[FRAME+'/Fixture']='Resources'
   for row in d['links']:row['target']=links[row['path']]
  elif change=='link_payload':links[key]='Versions/A/Resources'
  elif change=='unsigned_link':links[FRAME+'/Unsigned']='Versions/A/Fixture'
  elif change=='file_through_link':
   p=FRAME+'/Resources/unsigned.txt';files[p]=b'alias descendant';d['files'].append({'path':p,'size':len(files[p]),'sha256':sha(files[p]),'executable':False})
  elif change=='case_collision':
   p=FRAME+'/resources';links[p]='Versions/A/Resources';d['links'].append({'path':p,'target':links[p]})
  elif change=='entrypoint_alias':d['entrypoint']=FRAME+'/Fixture';next(r for r in d['files']if r['path']==FRAME+'/Versions/A/Fixture')['executable']=True
  elif change=='outside_app':
   p='outside.txt';files[p]=b'outside';d['files'].append({'path':p,'size':len(files[p]),'sha256':sha(files[p]),'executable':False})
  elif change=='linux_schema2':d['platform']='linux'
  elif change=='schema1_link':d['schema_version']=1;del d['links']
  elif change=='link_metadata_extra':d['links'][0]['authority']='unsigned-new-access'
  elif change=='regular_checksum':files[FRAME+'/Versions/A/Fixture']=b'changed after signed inventory'
 value=native(tmp_path,modify)
 with pytest.raises(update.UpdateError):update._portable(value['directory']/'application.zip',value['payload'])
 with pytest.raises(update.UpdateError):plan(root,value)
 assert not(root/'.application-updates').exists()and not(root/'application-active.json').exists()and not(root/'global-active.json').exists()
 assert (root/'projects/labels.json').read_bytes()==b'{"label":"original"}'

@pytest.mark.skipif(platform.system()!='Darwin',reason='Actual macOS host required for installed native tamper checks')
@pytest.mark.parametrize('change',['changed_target','absolute_target','missing_link','regular_replacement','extra_link','extra_directory','changed_regular','linked_parent'])
def test_installed_native_tamper_refuses_launch_and_recovery(tmp_path,change):
 from backend.engine import runtime_update as update
 root,scopes,*_=owned(tmp_path);value=native(tmp_path);installed=update.install_update(root,plan(root,value));command=update.launch_plan(root,value['authority'],pinned_authority_sha256=value['pinned_authority_sha256']);application=Path(command['argv'][0]).parents[3];link=application/(FRAME+'/Resources');outside=tmp_path/'unrelated-outside';outside.mkdir();(outside/'marker.txt').write_bytes(b'foreign-preserved')
 if change in ['changed_target','absolute_target','missing_link','regular_replacement']:
  link.unlink()
  if change=='changed_target':link.symlink_to('Versions/A')
  elif change=='absolute_target':link.symlink_to(outside)
  elif change=='regular_replacement':link.write_bytes(b'not symbolic')
 elif change=='extra_link':(application/(FRAME+'/Extra')).symlink_to(outside)
 elif change=='extra_directory':(application/(APP+'/Contents/Unlisted')).mkdir()
 elif change=='changed_regular':
  file=application/(FRAME+'/Versions/A/Fixture');file.chmod(0o600);file.write_bytes(b'tampered')
 elif change=='linked_parent':
  versions=application/(FRAME+'/Versions');versions.rename(versions.parent/'preserved-versions');versions.symlink_to(outside)
 with pytest.raises(update.UpdateError):update.launch_plan(root,value['authority'],pinned_authority_sha256=value['pinned_authority_sha256'])
 with pytest.raises(update.UpdateError):update.recover_update(root,installed['update_id'],action='finish')
 assert (outside/'marker.txt').read_bytes()==b'foreign-preserved'

@pytest.mark.parametrize('entry',[None,[],{},7])
def test_native_entrypoint_requires_a_canonical_string(tmp_path,entry):
 from backend.engine import runtime_update as update
 value=native(tmp_path,lambda d,f,l:d.update(entrypoint=entry))
 with pytest.raises(update.UpdateError):update._portable(value['directory']/'application.zip',value['payload'])

@pytest.mark.parametrize('target',['Versions/A/absent/../Resources','Versions/A/Fixture/../Resources','Versions/Current/../../../..'])
def test_native_resolution_rejects_missing_file_or_escaping_intermediate_component(tmp_path,target):
 from backend.engine import runtime_update as update
 def change(d,f,l):
  key=FRAME+'/Resources';l[key]=target;next(r for r in d['links']if r['path']==key)['target']=target
 value=native(tmp_path,change)
 with pytest.raises(update.UpdateError):update._portable(value['directory']/'application.zip',value['payload'])

@pytest.mark.skipif(platform.system()!='Darwin',reason='Actual macOS host required for installed alias resolution')
def test_native_dotdot_is_resolved_after_the_real_alias_expansion(tmp_path):
 from backend.engine import runtime_update as update
 root,scopes,*_=owned(tmp_path)
 def change(d,f,l):
  l[FRAME+'/Alias']='Versions/A/Resources';l[FRAME+'/Resources']='Alias/../Resources'
  d['links']=[{'path':p,'target':v}for p,v in l.items()]
 value=native(tmp_path,change);installed=update.install_update(root,plan(root,value));command=update.launch_plan(root,value['authority'],pinned_authority_sha256=value['pinned_authority_sha256'])
 assert subprocess.run(command['argv'],capture_output=True,text=True,timeout=5).stdout=='owned-native-layout\n'
