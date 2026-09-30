"""Registry validation checks real repository evidence without upgrading acceptance."""
import importlib.util
import json
from pathlib import Path

import pytest


ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('feature_program_verifier',ROOT/'scripts/verify_feature_program.py')
verifier=importlib.util.module_from_spec(spec);spec.loader.exec_module(verifier)


def registry(tmp_path):
    root=tmp_path/'repo';(root/'docs').mkdir(parents=True);(root/'src').mkdir()
    (root/'src/module.py').write_text('source fixture')
    (root/'docs/evidence.md').write_text('regression and workflow fixture')
    phases={f'P{i:02}':{'title':f'Phase {i}','goal':'Recorded feature work'} for i in range(1,11)}
    rows=[{'id':f'F{i:03}','feature':f'Feature {i}','phase':f'P{(i-1)%10+1:02}',
           'implementation':'integrated','verification':'api','acceptance':'Separate acceptance remains pending',
           'source_files':['src/module.py'],'evidence':[{'kind':'test','path':'docs/evidence.md'},{'kind':'workflow','reference':'docs/evidence.md'}]} for i in range(1,124)]
    program={'scope_count':123,'phases':phases,'features':rows};path=root/'docs/feature-program.json'
    return root,path,program


def check(root,path,program):
    path.write_text(json.dumps(program))
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(verifier,'REPO_ROOT',root,raising=False)
        return verifier.verify(path)


def test_current_123_feature_registry_is_valid():
    assert verifier.verify(ROOT/'docs/feature-program.json')=={'scope':123,'phases':10,'accepted':0,'pending':0}


def test_verified_references_keep_api_unit_and_hardware_separate_from_accepted(tmp_path):
    root,path,program=registry(tmp_path)
    for index,level in enumerate(('unit','api','ui','real_input','hardware','pending')):
        program['features'][index]['verification']=level
    program['features'][0]['implementation']='implemented'
    program['features'][0]['evidence']=[{'kind':'test','ref':'docs/evidence.md'}]
    assert check(root,path,program)['accepted']==0


@pytest.mark.parametrize('mutation,expected',[
    ('missing_source','source'),('missing_evidence','evidence'),('outside_source','repository'),
    ('outside_evidence','repository'),('absolute_source','relative'),('windows_source','relative'),
    ('symlink_source','repository'),('missing_test','test'),('missing_workflow','workflow'),
    ('wrong_count','scope_count'),('wrong_phases','phase'),
])
def test_invalid_resource_or_evidence_cannot_pass_registry(tmp_path,mutation,expected):
    root,path,program=registry(tmp_path);row=program['features'][0]
    if mutation=='missing_source':row['source_files']=['src/missing.py']
    elif mutation=='missing_evidence':row['evidence'][0]['path']='docs/missing.md'
    elif mutation=='outside_source':row['source_files']=['../outside.py']
    elif mutation=='outside_evidence':row['evidence'][0]['path']='../outside.md'
    elif mutation=='absolute_source':row['source_files']=[str(root/'src/module.py')]
    elif mutation=='windows_source':row['source_files']=['C:\\private\\source.py']
    elif mutation=='symlink_source':
        outside=tmp_path/'outside.py';outside.write_text('foreign source fixture');(root/'src/link.py').symlink_to(outside);row['source_files']=['src/link.py']
    elif mutation=='missing_test':row['evidence']=[{'kind':'workflow','path':'docs/evidence.md'}]
    elif mutation=='missing_workflow':row['evidence']=[{'kind':'test','reference':'docs/evidence.md'}]
    elif mutation=='wrong_count':program['scope_count']=122
    elif mutation=='wrong_phases':program['phases']['P11']={'title':'Unregistered phase','goal':'No assigned scope'}
    with pytest.raises(ValueError,match=expected):check(root,path,program)
