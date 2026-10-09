"""Copied synthetic record integrity controls; never infer, review or adopt a model."""
import copy
import hashlib
import json
from types import SimpleNamespace

import pytest
from backend.tests import test_enhancement_gan_lifecycle_evidence as prepared

sha = prepared.sha
canonical = prepared.canonical
REVIEWER = 'Codex synthetic software qualification'
REASON = 'Synthetic control transition only; no human or model-quality approval'


def _json(case, path):
    return json.loads((case.root/path).read_bytes())


@pytest.fixture
def gan_control():
    gen = prepared.prepared_control.__wrapped__(SimpleNamespace(param='defect_gan'))
    case = next(gen)
    try:
        # Retain only reachable GAN pins, not the base classifier's old graph
        # or package. Physical synthetic fixture files remain untouched.
        for path in list(case.files):
            if path.startswith(('saved/', 'package/')):
                del case.files[path]
        rename = {'a.bin':'train/scratch/a.bin','b.bin':'val/scratch/b.bin',
                  'c.bin':'test/scratch/c.bin','d.bin':'train/OK/d.bin'}
        manifest = _json(case, 'version/manifest.json')
        for row in manifest['files']:
            if row.get('kind') == 'image':
                old = row['relative_path']; new = rename[old]
                row.update(relative_path=new, source_path='/original/source/'+new)
                case.receipt['dataset']['files']['source:'+new] = case.receipt['dataset']['files'].pop('source:'+old)
        manifest['content_digest'] = sha(canonical({k:v for k,v in manifest.items() if k!='content_digest'}))
        case.put('version/manifest.json', manifest)
        labels = _json(case, 'version/team-data.json')
        for row in labels['eligibility']: row['relative_path'] = rename[row['relative_path']]
        case.put('version/team-data.json', labels)
        def rename_sources(body):
            for row in body['source_map']:
                row['source_image'] = rename[row['source_image']]
                body['provenance']['source_map'][row['image']]['source_relative_path'] = row['source_image']
        prepared._rewrite(case, rename_sources, rebind_gan_raw=True)
        binding = _json(case, 'model/job_receipt.json')['training_provenance']
        binding.update(manifest_sha256=manifest['content_digest'], team_data=labels,
                       team_data_sha256=sha(canonical(labels)),
                       split_sha256=sha(json.dumps([{k:r[k] for k in ('origin','relative_path','sha256')} for r in manifest['files']],sort_keys=True,separators=(',',':')).encode()))
        prepared._write_binding(case, binding)
        checkpoint = case.files['model/best_model.pt']['sha256']
        case.change('evaluation.json', lambda b: b['result'].update(
            split='test', real_sample_count=1, generated_count=2, seed=41,
            rgb_statistics_mmd=0.0, metric_backend='RGB mean/std Gaussian-kernel MMD (diagnostic)',
            quality_status='unvalidated', checkpoint_sha256=checkpoint,
            manifest_sha256=binding['family_dataset_sha256'], test_predictions=[]), 'evidence_sha256')
        source = '/original/source/train/OK/d.bin'; source_hash=case.files['source/d.bin']['sha256']
        case.put('gan/source-snapshot.bin', (case.root/'source/d.bin').read_bytes())
        adopt_root='/original/project/dataset/synthetic_adoptions/'+('1'*32)
        review_root='/original/project/synthetic_review/'+('2'*32)+'/'+('3'*32)
        regions=[{'id':'region','bbox':[4,4,20,20],'opacity':1.0,'feather_px':0,'mask_polygon':None}]
        candidates=[];decisions=[];candidate_files={};samples=[];copies={}
        for i,decision in enumerate(('adopt','reject')):
            cid='candidate_'+str(i+1).zfill(4);path=review_root+'/synthetic_'+cid+'.png'
            value=b'actual synthetic candidate byte control '+str(i).encode();member='gan/candidate-'+str(i)+'.bin';case.put(member,value);candidate_files[path]=member
            review={'candidate_id':cid,'decision':decision,'label':'scratch' if decision=='adopt' else None,'reviewer':REVIEWER,'reason':REASON}
            decisions.append(review)
            composition=[{**regions[0],'coordinate_space':'original_image','generated_patch_sha256':'4'*64,
                          'blend_mask_sha256':'5'*64,'generation_index':i,'blend_mode':'alpha_source_over',
                          'mask_dtype':'float32','mask_shape':[16,16]}]
            candidates.append({'id':cid,'path':path,'sha256':sha(value),'status':'synthetic_adopted' if decision=='adopt' else 'synthetic_rejected',
                               'composition_regions':composition,'review':{**review,'dataset_path':adopt_root,'reviewed_at':'original-control-time'}})
        for old,new in rename.items():
            split,label,_=new.split('/');dest=split+'/'+label+'/real_'+old
            source_path='/original/source/'+new;data=(case.root/('source/'+old)).read_bytes();member='gan/adoption/'+dest
            case.put(member,data);copies[dest]=member
            samples.append({'kind':'real','source_image':source_path,'source_sha256':sha(data),'image':dest,'split':split,'label':label})
        candidate=candidates[0];dest='train/scratch/synthetic.bin';member='gan/adoption/'+dest;case.put(member,(case.root/candidate_files[candidate['path']]).read_bytes());copies[dest]=member
        samples.append({'kind':'synthetic_reviewed','image':dest,'split':'train','label':'scratch','candidate_id':candidate['id'],
                        'source_sha256':candidate['sha256'],'generator_sha256':checkpoint,'reviewer':REVIEWER,'reason':REASON,
                        'source_image_sha256':source_hash,'composition_regions':candidate['composition_regions'],'generation_seed':43})
        review={'model_kind':'dcgan_defect_crop','generator_sha256':checkpoint,'source_manifest_sha256':binding['family_dataset_sha256'],
                'quality_status':'unvalidated','seed':43,'generation_mode':'source_composition','source_image_path':source,
                'source_image_sha256':source_hash,'source_size':[64,48],'source_snapshot':review_root+'/source_image.png',
                'source_snapshot_sha256':source_hash,'regions':regions,'candidates':candidates}
        adoption={'task':'classification','source_dataset_path':'/original/source','generator_sha256':checkpoint,
                  'reviewed_at':'original-control-time','samples':samples,'decisions':decisions}
        case.put('gan/review.json',review);case.put('gan/adoption.json',adoption)
        case.receipt['flow_or_adoption']={'kind':'gan_adoption','review':'gan/review.json','source_snapshot':'gan/source-snapshot.bin',
            'candidate_files':candidate_files,'adoption':'gan/adoption.json','files':copies,'review_scope':'synthetic_control'}
        yield case
    finally:
        try: next(gen)
        except StopIteration: pass


def _export(case):
    checkpoint=case.files['model/best_model.pt']['sha256'];binding=_json(case,'model/job_receipt.json')['training_provenance']
    prefix='gan-package/'
    for rel,data in {'best_model.pt':(case.root/'model/best_model.pt').read_bytes(),
                     'model_meta.json':(case.root/'model/model_meta.json').read_bytes(),
                     'generate.py':b'actual synthetic entrypoint bytes; never executed',
                     'backend/engine/gan_package_runtime.py':b'actual synthetic runtime bytes; never executed',
                     'requirements.txt':b'original synthetic requirement bytes'}.items(): case.put(prefix+rel,data)
    workflow={'task':'defect_gan','stages':['explicit_defect_crops','trained_generator','heldout_diagnostic','generate_unreviewed','human_review','adopt_train_only'],
              'output_state':'synthetic_unreviewed','quality_status':'unvalidated'}
    case.put(prefix+'workflow.json',workflow)
    members=[{'path':rel,'sha256':pin['sha256']} for member,pin in case.files.items() if member.startswith(prefix) for rel in [member[len(prefix):]]]
    manifest={'schema_version':1,'task':'defect_gan','generator_sha256':checkpoint,'files':members};case.put(prefix+'manifest.json',manifest)
    generation_refs=[]
    for phase in ('reference','packaged'):
        root='/new/owned/'+phase;rows=[];files={}
        for i in range(2):
            cid='candidate_'+str(i+1).zfill(4);path=root+'/synthetic_'+cid+'.png';member='parity/'+phase+'/'+cid+'.bin'
            case.put(member,b'actual synthetic same-seed parity '+str(i).encode());files[path]=member
            rows.append({'id':cid,'path':path,'sha256':case.files[member]['sha256'],'status':'synthetic_unreviewed'})
        body={'model_kind':'dcgan_defect_crop','generator_sha256':checkpoint,'source_manifest_sha256':binding['family_dataset_sha256'],
              'quality_status':'unvalidated','seed':41,'candidates':rows};member='parity/'+phase+'/generation.json';case.put(member,body)
        generation_refs.append((member,files))
    case.put('parity/reference-code.py',b'actual synthetic producer bytes; never executed')
    parity={'schema_version':1,'contract':'gan_generation_parity_v1','status':'passed','task':'defect_gan',
            'generator_sha256':checkpoint,'source_manifest_sha256':binding['family_dataset_sha256'],
            'manifest_sha256':case.files[prefix+'manifest.json']['sha256'],'workflow_sha256':case.files[prefix+'workflow.json']['sha256'],
            'seed':41,'count':2,'candidate_sha256':[_json(case,generation_refs[0][0])['candidates'][i]['sha256'] for i in range(2)],
            'reference_runtime':{'kind':'owned_python_generator','device':'cpu','cpu_threads':1,'process_id':101,'process_birth':1.0,
                                 'code_sha256':case.files['parity/reference-code.py']['sha256']},
            'packaged_runtime':{'kind':'isolated_python_generator_runner','device':'cpu','cpu_threads':1,'process_id':102,'process_birth':2.0,
                                'independent_process':True,'package_runner_sha256':case.files[prefix+'generate.py']['sha256'],
                                'package_runtime_sha256':case.files[prefix+'backend/engine/gan_package_runtime.py']['sha256']},
            'error':None,'quality_approved':False,'human_review_approved':False}
    case.put('parity/parity.json',parity)
    case.receipt['export']={'manifest':prefix+'manifest.json','parity':'parity/parity.json','reference_code':'parity/reference-code.py',
        'reference_manifest':generation_refs[0][0],'reference_files':generation_refs[0][1],
        'packaged_manifest':generation_refs[1][0],'packaged_files':generation_refs[1][1]}
    case.receipt['target']={'kind':'source_cpu','device':'cpu'}


def test_genuine_copied_adoption_verifies_after_original_first_four(gan_control):
    result=gan_control.check()
    for stage in ('dataset','labels','train','eval'): assert result['stages'][stage]['state']=='verified',result
    assert result['stages']['flow_or_adoption']['state']=='verified',result
    assert result['stages']['export']['state']=='pending',result
    assert result['human_truth_approved'] is result['model_quality_approved'] is False


def test_complete_generator_chain_uses_distinct_copied_parity_without_approval(gan_control):
    _export(gan_control);result=gan_control.check()
    assert result['record_chain_verified'] is True,result
    for flag in ('runtime_execution_reproduced','human_truth_approved','model_quality_approved','target_execution_approved','parent_accepted'):assert result[flag] is False


def test_original_coordinate_polygon_records_join_without_geometric_truth_approval(gan_control):
    case=gan_control;polygon=[[4.0,4.0],[20.0,4.0],[12.0,20.0]]
    def review(body):
        body['regions'][0]['mask_polygon']=polygon
        for candidate in body['candidates']:
            candidate['composition_regions'][0]['mask_polygon']=polygon
    case.change('gan/review.json',review)
    case.change('gan/adoption.json',lambda b:b['samples'][-1]['composition_regions'][0].update(mask_polygon=polygon))
    result=case.check();assert result['stages']['flow_or_adoption']['state']=='verified',result
    assert result['human_truth_approved'] is result['model_quality_approved'] is result['target_execution_approved'] is False


@pytest.mark.parametrize('change',['source','generator','source_snapshot','copy_missing','copy_different','candidate_copy','duplicate_real','foreign_real','synthetic_test','label','reviewer','reason','decision','status','time','seed','composition','extra_candidate','scope','region_bounds','region_integer','region_polygon','region_index','candidate_alias'])
def test_adoption_changes_cannot_rebind_original_truth_or_pixels(gan_control,change):
    case=gan_control;assert case.check()['stages']['flow_or_adoption']['state']=='verified',case.check()
    if change=='source':case.change('gan/adoption.json',lambda b:b.update(source_dataset_path='/foreign/source'))
    elif change=='generator':case.change('gan/adoption.json',lambda b:b.update(generator_sha256='0'*64))
    elif change=='source_snapshot':case.put('gan/source-snapshot.bin',b'changed original source snapshot')
    elif change=='copy_missing':case.receipt['flow_or_adoption']['files'].pop(next(iter(case.receipt['flow_or_adoption']['files'])))
    elif change=='copy_different':case.put(next(iter(case.receipt['flow_or_adoption']['files'].values())),b'changed adopted bytes')
    elif change=='candidate_copy':case.put(next(iter(case.receipt['flow_or_adoption']['candidate_files'].values())),b'changed candidate bytes')
    elif change=='duplicate_real':case.change('gan/adoption.json',lambda b:b['samples'].append(copy.deepcopy(b['samples'][0])))
    elif change=='foreign_real':case.change('gan/adoption.json',lambda b:b['samples'][0].update(source_image='/foreign/source/image.bin'))
    elif change=='synthetic_test':case.change('gan/adoption.json',lambda b:b['samples'][-1].update(split='test'))
    elif change=='label':case.change('gan/adoption.json',lambda b:b['samples'][-1].update(label='OK'))
    elif change=='reviewer':case.change('gan/adoption.json',lambda b:b['decisions'][0].update(reviewer='invented human authorization'))
    elif change=='reason':case.change('gan/review.json',lambda b:b['candidates'][0]['review'].update(reason='human approved'))
    elif change=='decision':case.change('gan/adoption.json',lambda b:b['decisions'][0].update(decision='reject'))
    elif change=='status':case.change('gan/review.json',lambda b:b['candidates'][0].update(status='synthetic_unreviewed'))
    elif change=='time':case.change('gan/review.json',lambda b:b['candidates'][0]['review'].update(reviewed_at='changed-time'))
    elif change=='seed':case.change('gan/adoption.json',lambda b:b['samples'][-1].update(generation_seed=44))
    elif change=='composition':case.change('gan/adoption.json',lambda b:b['samples'][-1]['composition_regions'][0].update(coordinate_space='crop_image'))
    elif change=='extra_candidate':case.receipt['flow_or_adoption']['candidate_files']['/foreign/candidate']='source/a.bin'
    elif change.startswith('region_'):
        def mutate_region(body):
            if change=='region_bounds':body['regions'][0]['bbox'][2]=65
            elif change=='region_integer':body['regions'][0]['bbox'][0]=True
            elif change=='region_polygon':body['regions'][0]['mask_polygon']=[[0,0],[1,1],[2,2]]
            else:body['candidates'][0]['composition_regions'][0]['generation_index']=True
        case.change('gan/review.json',mutate_region)
    elif change=='candidate_alias':
        copies=case.receipt['flow_or_adoption']['candidate_files'];copies[list(copies)[1]]=copies[list(copies)[0]]
    else:case.receipt['flow_or_adoption']['review_scope']='human_approved'
    result=case.check();assert result['stages']['flow_or_adoption']['state']!='verified',result
    assert result['record_chain_verified'] is False


@pytest.mark.parametrize('change',['generator','manifest','workflow','duplicate_member','member_copy','runtime_code','quality','human','same_process','independence','gpu','threads','entrypoint','count','seed','candidate_order','candidate_pixels','candidate_state','extra_parity_copy','error','target','legacy_boolean','metadata','birth','alias_output','schema_bool'])
def test_generation_package_or_target_changes_do_not_verify(gan_control,change):
    case=gan_control;_export(case);assert case.check()['record_chain_verified'] is True,case.check()
    if change=='generator':case.change('gan-package/manifest.json',lambda b:b.update(generator_sha256='0'*64))
    elif change=='manifest':case.change('parity/parity.json',lambda b:b.update(manifest_sha256='0'*64))
    elif change=='workflow':case.change('gan-package/workflow.json',lambda b:b.update(output_state='approved'))
    elif change=='duplicate_member':case.change('gan-package/manifest.json',lambda b:b['files'].append(copy.deepcopy(b['files'][0])))
    elif change=='member_copy':case.put('gan-package/best_model.pt',b'changed package model bytes')
    elif change=='runtime_code':case.put('gan-package/backend/engine/gan_package_runtime.py',b'changed package runtime bytes')
    elif change=='quality':case.change('parity/parity.json',lambda b:b.update(quality_approved=True))
    elif change=='human':case.change('parity/parity.json',lambda b:b.update(human_review_approved=True))
    elif change=='same_process':case.change('parity/parity.json',lambda b:b['packaged_runtime'].update(process_id=101))
    elif change=='independence':case.change('parity/parity.json',lambda b:b['packaged_runtime'].update(independent_process=False))
    elif change=='gpu':case.change('parity/parity.json',lambda b:b['packaged_runtime'].update(device='cuda'))
    elif change=='threads':case.change('parity/parity.json',lambda b:b['reference_runtime'].update(cpu_threads=True))
    elif change=='entrypoint':case.change('parity/parity.json',lambda b:b['packaged_runtime'].update(package_runner_sha256='0'*64))
    elif change=='count':case.change('parity/parity.json',lambda b:b.update(count=1))
    elif change=='seed':case.change('parity/packaged/generation.json',lambda b:b.update(seed=42))
    elif change=='candidate_order':case.change('parity/packaged/generation.json',lambda b:b['candidates'].reverse())
    elif change=='candidate_pixels':case.put('parity/packaged/candidate_0001.bin',b'different generated candidate bytes')
    elif change=='candidate_state':case.change('parity/packaged/generation.json',lambda b:b['candidates'][0].update(status='synthetic_adopted'))
    elif change=='extra_parity_copy':case.receipt['export']['packaged_files']['/foreign/extra']='source/a.bin'
    elif change=='error':case.change('parity/parity.json',lambda b:b.update(error='actual mismatch'))
    elif change=='legacy_boolean':case.put('parity/parity.json',{'fresh_process':True,'exact_pixel_parity':True,'quality_approved':False})
    elif change=='metadata':
        case.change('gan-package/model_meta.json',lambda b:b.update(source_dataset_path='/foreign/source'))
        case.change('gan-package/manifest.json',lambda b:next(row for row in b['files'] if row['path']=='model_meta.json').update(sha256=case.files['gan-package/model_meta.json']['sha256']))
        case.change('parity/parity.json',lambda b:b.update(manifest_sha256=case.files['gan-package/manifest.json']['sha256']))
    elif change=='birth':case.change('parity/parity.json',lambda b:b['packaged_runtime'].update(process_birth=True))
    elif change=='alias_output':case.receipt['export']['packaged_files']=copy.deepcopy(case.receipt['export']['reference_files'])
    elif change=='schema_bool':case.change('gan-package/manifest.json',lambda b:b.update(schema_version=True))
    else:case.receipt['target']['device']='cuda'
    result=case.check();assert result['record_chain_verified'] is False,result
    assert result['stages']['export']['state']!='verified' or result['stages']['target']['state']!='verified',result


@pytest.mark.parametrize('change',['classifier_task','AB_schema','negative_mmd','missing_metric','wrong_checkpoint','wrong_manifest','verdict','count','real_count','metric_backend'])
def test_classifier_comparison_or_invalid_MMD_is_not_generator_evaluation(gan_control,change):
    case=gan_control
    if change=='AB_schema':case.put('evaluation.json',{'models':{'before':{},'after':{}},'metric_delta':{},'quality_approved':False})
    else:
        def mutate(body):
            result=body['result']
            if change=='classifier_task':result['task']='classification'
            elif change=='negative_mmd':result['rgb_statistics_mmd']=-1.0
            elif change=='missing_metric':result.pop('rgb_statistics_mmd')
            elif change=='wrong_checkpoint':result['checkpoint_sha256']='0'*64
            elif change=='wrong_manifest':result['manifest_sha256']='0'*64
            elif change=='verdict':result['test_predictions']=[{'predicted_class':'OK'}]
            elif change=='real_count':result['real_sample_count']=2
            elif change=='metric_backend':result['metric_backend']='classifier accuracy'
            else:result['generated_count']=True
        case.change('evaluation.json',mutate,'evidence_sha256')
    result=case.check();assert result['stages']['eval']['state']!='verified' or result['stages']['flow_or_adoption']['state']!='verified',result
    assert result['record_chain_verified'] is False


def test_reserialized_metadata_cannot_rebind_byte_exact_copied_package(gan_control):
    case=gan_control;_export(case)
    assert case.check()['record_chain_verified'] is True,case.check()
    original=(case.root/'model/model_meta.json').read_bytes()
    rewritten=json.dumps(_json(case,'gan-package/model_meta.json'),sort_keys=True,separators=(',',':')).encode()
    assert rewritten!=original and json.loads(rewritten)==json.loads(original)
    case.put('gan-package/model_meta.json',rewritten)
    case.change('gan-package/manifest.json',lambda b:next(row for row in b['files'] if row['path']=='model_meta.json').update(sha256=case.files['gan-package/model_meta.json']['sha256']))
    case.change('parity/parity.json',lambda b:b.update(manifest_sha256=case.files['gan-package/manifest.json']['sha256']))
    result=case.check()
    for stage in ('dataset','labels','train','eval','flow_or_adoption'):
        assert result['stages'][stage]['state']=='verified',result
    assert result['stages']['export']['state']!='verified',result
    assert result['record_chain_verified'] is False
