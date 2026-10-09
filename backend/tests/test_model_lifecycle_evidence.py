"""Synthetic stdlib records only: no model, service or original artifact execution."""
from pathlib import Path
import ast
import copy
import contextlib
import hashlib
import importlib.util
import io
import json
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[2]
MODULE=ROOT/'scripts/check_model_lifecycle.py'
verifier=None
if MODULE.is_file():
    spec=importlib.util.spec_from_file_location('scoped_lifecycle_source_verifier',MODULE)
    verifier=importlib.util.module_from_spec(spec);spec.loader.exec_module(verifier)
def canonical(value):return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()
def sha(raw):return hashlib.sha256(raw).hexdigest()

def family_record_model(family,labels,*,prepared=True):
    """Pure records with original producer formulas; no image/model execution."""
    source='/original/source';dataset='/original/project/dataset/'+family if prepared else source
    version='/original/project/versions/version_1';name='ocr.json' if family=='ocr' else 'patches.json'
    pixels={'train/검사.bin':b'synthetic original pixels; not an actual image/model run',
            'val/b.bin':b'synthetic distinct held-out pixels; no image decoder used'}
    originals={'train/검사.bin':'a.bin','val/b.bin':'b.bin'}
    hashes={image:sha(raw) for image,raw in pixels.items()}
    if not prepared:
        pixels={originals[image]:raw for image,raw in pixels.items()};hashes={image:sha(raw) for image,raw in pixels.items()}
        originals={image:image for image in pixels}
    mapping={image:{'source_relative_path':originals[image],'source_sha256':hashes[image]} for image in pixels}
    entries=[{'image':image,'split':'train' if index==0 else 'val','source_sha256':hashes[image],
              **({'text':'가나' if index==0 else '가'} if family=='ocr' else {'label':'OK' if index==0 else 'NG','box':[0,0,2,2]})}
             for index,image in enumerate(pixels)]
    body={'version':1,('samples' if family=='ocr' else 'patches'):entries}
    if family=='patch_classification':body.update(classes=['OK','NG'],normal_class='OK',patch_size=2,stride=2)
    if prepared:body.update(source_dataset_path=source,source_map=mapping)
    raw=(json.dumps(body,ensure_ascii=False,indent=2)+'\n').encode();manifest_sha=sha(raw)
    digest=hashlib.sha256(b'ocr-dataset-v1\0'+raw if family=='ocr' else b'patch-classification-dataset-v1\0'+manifest_sha.encode('ascii'))
    for image,value in sorted(hashes.items()):digest.update(b'\0'+image.encode()+b'\0'+value.encode())
    provenance={'dataset_sha256':'sha256:'+digest.hexdigest(),'manifest_sha256':manifest_sha,
                'source_sha256':dict(sorted(hashes.items())),'split_counts':{'train':1,'val':1,'test':0},'source_image_count':2}
    if family=='patch_classification':provenance['patch_count']=2
    if prepared:provenance.update(source_dataset_path=source,source_map=mapping)
    snapshot=version+'/labels/family/'+family+'/'+name
    rows=sorted([{'relative_path':name,'source_path':dataset+'/'+name,'sha256':manifest_sha,'snapshot_path':snapshot},
                 *({'relative_path':image,'source_path':dataset+'/'+image,'sha256':value,'snapshot_path':None} for image,value in hashes.items())],
                key=lambda row:row['source_path'])
    copies={row['source_path']:'family/current/'+row['relative_path'] for row in rows}
    copies[snapshot]='family/frozen/'+name
    files={copies[dataset+'/'+name]:raw,copies[snapshot]:raw,
           **{copies[dataset+'/'+image]:value for image,value in pixels.items()}}
    inventory={row['relative_path']:row['image_uuid'] for row in labels['eligibility']}
    binding={'family_task':family,'family_dataset_path':dataset,'version_dir':version,'family_provenance':provenance,
             'family_inputs':rows,'family_inputs_sha256':sha(json.dumps(rows,sort_keys=True,separators=(',',':')).encode()),
             'family_source_image_uuids':sorted({inventory[image] for image in originals.values() if image in inventory})}
    if family=='ocr':binding['family_dataset_sha256']=provenance['dataset_sha256']
    return {'binding':binding,'copies':copies,'files':files,'images':{source+'/'+originals[image]:hashes[image] for image in hashes}}

class LifecycleEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name).resolve();self.files={};self.receipt=self.fixture()
        if verifier is not None:self.assertTrue(verifier.verify_lifecycle(self.receipt,self.root)['record_chain_verified'],'synthetic base chain must verify before any corruption control')
    def put(self,path,value):
        raw=value if isinstance(value,bytes) else (json.dumps(value,indent=2)+'\n').encode()
        p=self.root/path;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(raw)
        self.files[path]={'sha256':sha(raw),'size':len(raw)}
        return path
    def fixture(self):
        image=b'synthetic original pixels; not an actual image/model run'
        truth=b'synthetic class labels'
        checkpoint=b'synthetic checkpoint bytes; not a torch checkpoint'
        self.put('source/a.bin',image);self.put('source/labels.bin',truth)
        manifest={'schema_version':1,'id':'version_1','project_id':'project_1','labelset_id':'default',
          'source_dataset_dir':'/original/source','task':'classification','dataset_fingerprint':'v1:'+'1'*64,
          'files':[{'origin':'source','kind':'image','relative_path':'a.bin','source_path':'/original/source/a.bin','sha256':sha(image),'size_bytes':len(image),'snapshot_path':None},
                   {'origin':'source','kind':'label','relative_path':'labels.bin','source_path':'/original/source/labels.bin','sha256':sha(truth),'size_bytes':len(truth),'snapshot_path':'labels/source/labels.bin'}]}
        manifest['content_digest']=sha(canonical(manifest));self.put('version/manifest.json',manifest)
        labels={'scope':{'project_id':'project_1','source':'/original/source','labelset_id':'default'},
          'book_sha256':'2'*64,'policy_sha256':'3'*64,'eligibility_sha256':'4'*64,'eligibility':[]}
        self.put('version/team-data.json',labels)
        binding={'dataset_version_id':'version_1','labelset_id':'default','dataset_fingerprint':'v1:'+'1'*64,
          'manifest_sha256':manifest['content_digest'],'split_sha256':sha(json.dumps([{key:row[key] for key in ('origin','relative_path','sha256')} for row in manifest['files']],sort_keys=True,separators=(',',':')).encode()),'split_binding':'versioned_dataset_layout','team_data':labels,'team_data_sha256':sha(canonical(labels))}
        job={'job_id':'job_original','task':'classification','status':'completed','source_dataset_path':'/original/source',
          'dataset_fingerprint':'v1:'+'1'*64,'training_provenance':binding,'checkpoint_sha256':sha(checkpoint)}
        self.put('model/best_model.pt',checkpoint);self.put('model/job_receipt.json',job)
        self.put('model/model_meta.json',{'task':'classification','training_provenance':binding,'checkpoint_sha256':sha(checkpoint)})
        evaluation={'evaluation_id':'evaluation_'+'a'*32,'created_at':1.0,
          'binding':{'source_dataset_path':'/original/source','dataset_fingerprint':'v1:'+'1'*64,'checkpoint_sha256':sha(checkpoint),
                     'labelset_id':'default','training_labelset_id':'default','training_provenance':binding},
          'result':{'job_id':'job_original','task':'classification'},'grouped_errors':{}}
        evaluation['evidence_sha256']=sha(canonical(evaluation));self.put('evaluation.json',evaluation)
        graph={'id':'pipeline_1','nodes':[{'id':'model_1','data':{'node_type':'inspection','task':'classification','model_job_id':'job_original'}},{'id':'out','data':{'node_type':'output'}}],'edges':[]}
        self.put('saved/pipeline.json',graph);self.put('package/pipeline.json',canonical(graph))
        self.put('package/models/job_original/best_model.pt',checkpoint)
        self.put('package/run_flow.py',b'synthetic runner source, never executed')
        self.put('package/backend/engine/flow_package_runtime.py',b'synthetic runtime source, never executed')
        package={'schema_version':1,'pipeline_id':'pipeline_1','models':[{'job_id':'job_original','task':'classification','checkpoint':'models/job_original/best_model.pt'}],
          'files':[{'path':'pipeline.json',**self.files['package/pipeline.json']},
                   {'path':'models/job_original/best_model.pt',**self.files['package/models/job_original/best_model.pt']},
                   {'path':'run_flow.py',**self.files['package/run_flow.py']},
                   {'path':'backend/engine/flow_package_runtime.py',**self.files['package/backend/engine/flow_package_runtime.py']}], 'runtime':{'device':'cpu'}}
        self.put('package/manifest.json',package)
        flow={'final_verdict':'OK','routed_output_node_id':'out','roi_count':0,
          'branch_path':[{'node_id':'model_1','status':'passed','branch_verdict':'OK','selected_edge_ids':[]}],'rois':[]}
        parity={'contract':'flow_parity_v1','status':'passed','scope':'single_image','device':'cpu','resolved_device':'cpu','package_runtime_device':'cpu',
          'manifest_sha256':self.files['package/manifest.json']['sha256'],'graph_sha256':self.files['package/pipeline.json']['sha256'],
          'checkpoints':{'job_original':sha(checkpoint)},'image_count':1,'completed_count':1,'cohort_sha256':sha(canonical([sha(image)])),
          'tolerance':{'defect_score_abs':1e-4,'float_abs':1e-4,'raster_abs':1e-6},
          'compared_fields':['final_verdict','roi_count','defective_roi_count','routed_output_node_id','rejection_reason','inspected_image_size','execution_resources','execution_steps','crops'],
          'reference_runtime':{'kind':'in_process_app_engine','device':'cpu','engine_sha256':'5'*64},
          'packaged_runtime':{'kind':'isolated_python_runner','independent_process':True,'package_runner_sha256':self.files['package/run_flow.py']['sha256'],
                              'package_runtime_sha256':self.files['package/backend/engine/flow_package_runtime.py']['sha256']},
          'images':[{'index':0,'image_path':'/original/source/a.bin','image_sha256':sha(image),'status':'passed','mismatched_fields':[],
                     'reference':flow,'packaged':copy.deepcopy(flow)}], 'mismatched_fields':[],'error':None}
        self.put('package/parity_receipt.json',parity)
        return {'schema_version':1,'family':'classification','dataset':{'manifest':'version/manifest.json','files':{'source:a.bin':'source/a.bin','source:labels.bin':'source/labels.bin'}},
          'labels':{'receipt':'version/team-data.json','kind':'synthetic_control'},'target':{'kind':'source_cpu','device':'cpu'},
          'train':{'receipt':'model/job_receipt.json','metadata':'model/model_meta.json','checkpoint':'model/best_model.pt'},'eval':'evaluation.json',
          'flow_or_adoption':{'kind':'flow','graph':'saved/pipeline.json'},'export':{'manifest':'package/manifest.json','parity':'package/parity_receipt.json'},'hashes':self.files}
    def check(self,receipt=None):
        self.assertIsNotNone(verifier,'The planned offline LifecycleReceipt verifier API is absent')
        return verifier.verify_lifecycle(self.receipt if receipt is None else receipt,self.root)
    def change(self,path,mutate,semantic=None):
        value=json.loads((self.root/path).read_bytes());mutate(value)
        if semantic:value[semantic]=sha(canonical({k:v for k,v in value.items() if k!=semantic}))
        self.put(path,value)
    def reject(self,stage=None):
        result=self.check();self.assertFalse(result['record_chain_verified']);self.assertEqual(result['state'],'pending')
        if stage:self.assertNotEqual(result['stages'][stage]['state'],'verified')
        return result
    def test_complete_synthetic_chain(self):
        result=self.check();self.assertTrue(result['record_chain_verified']);self.assertEqual(result['state'],'verified')
        for key in ('runtime_execution_reproduced','human_truth_approved','model_quality_approved','target_execution_approved','parent_accepted'):
            self.assertIs(result[key],False)
        self.assertIn('human-reviewed representative class_labels',result['prerequisites'])
    def test_missing_and_unsupported_are_explicit_pending(self):
        for field in ('dataset','labels','train','eval','flow_or_adoption','export','target'):
            with self.subTest(field=field):
                value=copy.deepcopy(self.receipt);value[field]=None
                result=self.check(value);self.assertFalse(result['record_chain_verified']);self.assertTrue(result['prerequisites'])
        self.put('model/job_receipt.json',{'passed':True,'status':'completed'});self.reject('train')
    def test_raw_bytes_are_not_replaced_by_canonical_hash(self):
        self.assertNotEqual(self.files['model/job_receipt.json']['sha256'],sha(canonical(json.loads((self.root/'model/job_receipt.json').read_bytes()))))
        self.assertTrue(self.check()['record_chain_verified'])
        self.files['model/job_receipt.json']['sha256']=sha(canonical(json.loads((self.root/'model/job_receipt.json').read_bytes())))
        self.reject('train')
    def test_dataset_digest_and_complete_inventory(self):
        for mutate in (lambda v:v.update(project_id='foreign'),lambda v:v.update(content_digest='0'*64),lambda v:v['files'][0].update(sha256='0'*64)):
            with self.subTest(mutate=mutate):
                original=(self.root/'version/manifest.json').read_bytes();pin=copy.deepcopy(self.files['version/manifest.json'])
                self.change('version/manifest.json',mutate);self.reject('dataset');self.put('version/manifest.json',original);self.files['version/manifest.json']=pin
        del self.receipt['dataset']['files']['source:a.bin'];self.reject('dataset')
    def test_foreign_project_source_labelset_and_training_bindings(self):
        for field in ('project_id','source','labelset_id'):
            with self.subTest(field=field):
                old=(self.root/'version/team-data.json').read_bytes();self.change('version/team-data.json',lambda v:v['scope'].update({field:'foreign'}))
                self.reject('labels');self.put('version/team-data.json',old)
        self.change('model/job_receipt.json',lambda v:v['training_provenance'].update(dataset_version_id='foreign'));self.reject('train')
    def test_completed_tag_checkpoint_and_metadata_causality(self):
        for path,mutate in [('model/job_receipt.json',lambda v:v.update(status='failed')),('model/job_receipt.json',lambda v:v.update(checkpoint_sha256='0'*64)),
                            ('model/model_meta.json',lambda v:v.update(task='ocr')),('model/model_meta.json',lambda v:v['training_provenance'].update(labelset_id='foreign'))]:
            with self.subTest(path=path):
                old=(self.root/path).read_bytes();self.change(path,mutate);self.reject('train');self.put(path,old)
    def test_evaluation_integrity_and_same_producer_identity(self):
        for field,value in [('source_dataset_path','/foreign'),('checkpoint_sha256','0'*64),('dataset_fingerprint','0'*64),('labelset_id','foreign')]:
            with self.subTest(field=field):
                old=(self.root/'evaluation.json').read_bytes();self.change('evaluation.json',lambda v:v['binding'].update({field:value}),'evidence_sha256')
                self.reject('eval');self.put('evaluation.json',old)
        self.change('evaluation.json',lambda v:v['result'].update(job_id='job_foreign'),'evidence_sha256');self.reject('eval')
    def test_saved_flow_must_select_the_original_checkpoint(self):
        self.change('saved/pipeline.json',lambda v:v['nodes'][0]['data'].update(model_job_id='job_foreign'));self.reject('flow_or_adoption')
    def test_package_graph_model_member_hash_and_parity_binding(self):
        for path,mutate in [('package/manifest.json',lambda v:v.update(pipeline_id='foreign')),('package/manifest.json',lambda v:v['models'][0].update(task='ocr')),
                            ('package/parity_receipt.json',lambda v:v.update(manifest_sha256='0'*64)),('package/parity_receipt.json',lambda v:v['checkpoints'].update(job_original='0'*64)),
                            ('package/parity_receipt.json',lambda v:v.update(status='failed'))]:
            with self.subTest(path=path):
                old=(self.root/path).read_bytes();self.change(path,mutate);self.reject('export');self.put(path,old)
    def test_pass_tag_cannot_hide_missing_skipped_foreign_or_mismatched_rows(self):
        for mutate in (lambda v:v.update(images=[]),lambda v:v['images'][0].update(image_path='/foreign/a.bin'),
                       lambda v:v['images'][0].update(status='not_run'),lambda v:v['images'][0].update(mismatched_fields=['mask']),
                       lambda v:v['images'][0]['reference']['branch_path'][0].update(status='skipped')):
            with self.subTest(mutate=mutate):
                old=(self.root/'package/parity_receipt.json').read_bytes();self.change('package/parity_receipt.json',mutate);self.reject('export');self.put('package/parity_receipt.json',old)
    def test_target_or_human_booleans_never_mint_approval(self):
        self.receipt['target']['kind']='windows_native';self.reject('target');self.receipt['target']['kind']='source_cpu'
        self.receipt['labels']['human_approved']=True;self.reject('labels')
    def test_parity_contract_and_packaged_runtime_declarations(self):
        for mutate in (lambda v:v['tolerance'].update(float_abs=1),lambda v:v.update(compared_fields=['final_verdict']),
                       lambda v:v['packaged_runtime'].update(independent_process=1),lambda v:v['packaged_runtime'].update(package_runner_sha256='0'*64)):
            with self.subTest(mutate=mutate):
                old=(self.root/'package/parity_receipt.json').read_bytes();self.change('package/parity_receipt.json',mutate);self.reject('export');self.put('package/parity_receipt.json',old)
    def test_missing_duplicate_json_and_invalid_pin_bounds(self):
        missing=copy.deepcopy(self.receipt);missing['train']['checkpoint']='model/missing.pt';missing['hashes']['model/missing.pt']={'sha256':'0'*64,'size':0}
        self.assertFalse(self.check(missing)['record_chain_verified'])
        self.put('model/job_receipt.json',b'{"status":"completed","status":"completed"}');self.reject('train')
        self.files['model/best_model.pt']['size']=True;self.reject('train')
    def test_cli_uses_exact_wrapper_raw_pin_and_does_not_write_outputs(self):
        value=copy.deepcopy(self.receipt);raw=json.dumps(value,indent=1).encode();(self.root/'receipt.json').write_bytes(raw)
        args=['--root',str(self.root),'--receipt','receipt.json','--receipt-sha256',sha(raw),'--receipt-size',str(len(raw))]
        before=set(self.root.rglob('*'));output=io.StringIO()
        with contextlib.redirect_stdout(output):code=verifier.main(args)
        self.assertEqual(code,0);self.assertTrue(json.loads(output.getvalue())['record_chain_verified']);self.assertEqual(before,set(self.root.rglob('*')))
        args[-3]='0'*64
        with contextlib.redirect_stdout(io.StringIO()):self.assertEqual(verifier.main(args),1)
    def test_gan_generation_is_not_inspection_flow_adoption(self):
        self.receipt['family']='defect_gan';self.receipt['flow_or_adoption']={'kind':'adoption'}
        result=self.reject();self.assertIn('flow_or_adoption: GAN generation/adoption receipt format',result['prerequisites'])
    def test_namespace_traversal_links_and_byte_tampering_refused(self):
        for path in ('../outside','/absolute','source/../a.bin','source//a.bin','source/./a.bin','source\\a.bin'):
            with self.subTest(path=path):
                value=copy.deepcopy(self.receipt);value['train']['checkpoint']=path;self.assertFalse(self.check(value)['record_chain_verified'])
        p=self.root/'source/a.bin';p.unlink();p.symlink_to(self.root/'source/labels.bin');self.reject('dataset')
    def test_stale_checkpoint_between_read_passes(self):
        self.assertIsNotNone(verifier);original=verifier._read_pin;count=0
        def read(root,path,pin,parse=False):
            nonlocal count
            result=original(root,path,pin,parse)
            if path=='model/best_model.pt':
                count+=1
                if count==1:(self.root/path).write_bytes(b'stale after original hash')
            return result
        with patch.object(verifier,'_read_pin',side_effect=read):self.reject()
    def test_malformed_producer_objects_return_pending(self):
        cases=[('model/job_receipt.json',lambda value:value.update(training_provenance=None),'train'),
               ('saved/pipeline.json',lambda value:value['nodes'][0].update(data=None),'flow_or_adoption'),
               ('saved/pipeline.json',lambda value:value['nodes'][0]['data'].update(node_type='preprocess',params=None),'flow_or_adoption'),
               ('package/manifest.json',lambda value:None,'export'),
               ('package/manifest.json',lambda value:value.update(runtime=None),'target')]
        for path,mutate,stage in cases:
            with self.subTest(path=path,stage=stage):
                old=(self.root/path).read_bytes()
                value=json.loads(old)
                if stage=='export':self.put(path,None)
                else:mutate(value);self.put(path,value)
                try:self.reject(stage)
                finally:self.put(path,old)
    def test_consistent_foreign_split_cannot_rebind_immutable_manifest(self):
        for field,value in [('split_sha256','0'*64),('split_binding','foreign_layout')]:
            with self.subTest(field=field):
                originals={path:(self.root/path).read_bytes() for path in ['model/job_receipt.json','model/model_meta.json','evaluation.json']}
                self.change('model/job_receipt.json',lambda v:v['training_provenance'].update({field:value}))
                self.change('model/model_meta.json',lambda v:v['training_provenance'].update({field:value}))
                self.change('evaluation.json',lambda v:v['binding']['training_provenance'].update({field:value}),'evidence_sha256')
                try:self.reject('train')
                finally:
                    for path,raw in originals.items():self.put(path,raw)
    def test_saved_graph_duplicate_identity_is_refused(self):
        self.change('saved/pipeline.json',lambda value:value['nodes'].append({'id':'model_1','data':{'node_type':'output'}}))
        self.reject('flow_or_adoption')
    def test_foreign_parity_branch_node_is_refused(self):
        def foreign(value):
            step={'node_id':'foreign_model','status':'passed','branch_verdict':'OK','selected_edge_ids':[]}
            value['images'][0]['reference']['branch_path'].append(copy.deepcopy(step))
            value['images'][0]['packaged']['branch_path'].append(copy.deepcopy(step))
        self.change('package/parity_receipt.json',foreign)
        self.reject('export')
    def test_foreign_routed_output_and_selected_edge_are_refused(self):
        for mutate in (lambda flow:flow.update(routed_output_node_id='foreign_output'),
                       lambda flow:flow['branch_path'][0].update(selected_edge_ids=['foreign_edge'])):
            with self.subTest(mutate=mutate):
                old=(self.root/'package/parity_receipt.json').read_bytes()
                def update(value):
                    mutate(value['images'][0]['reference'])
                    mutate(value['images'][0]['packaged'])
                self.change('package/parity_receipt.json',update)
                try:self.reject('export')
                finally:self.put('package/parity_receipt.json',old)
    def test_split_digest_matches_original_producer_for_layout_and_saved_manifests(self):
        tree=ast.parse((ROOT/'backend/engine/training_provenance.py').read_text())
        function=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='bind_training_version')
        statements=[n for n in function.body if isinstance(n,ast.Assign) and any(isinstance(target,ast.Name) and target.id in ('split_rows','split_digest') for target in n.targets)]
        self.assertEqual(len(statements),2)
        for count in (0,1,2):
            with self.subTest(split_members=count):
                manifest=json.loads((self.root/'version/manifest.json').read_bytes())
                rows=manifest['files']+[{'origin':'split','relative_path':'분할-'+str(i)+'.json','sha256':sha(str(i).encode())} for i in range(count)]
                namespace={'manifest':{'files':rows},'json':json,'hashlib':hashlib}
                exec(compile(ast.Module(body=statements,type_ignores=[]),'original-split-producer','exec'),namespace)
                expected=(rows[-1]['sha256'] if count==1 else sha(json.dumps([{key:row[key] for key in ('origin','relative_path','sha256')} for row in rows],sort_keys=True,separators=(',',':')).encode()))
                self.assertEqual(namespace['split_digest'],expected)
    def test_deep_raw_pinned_json_is_refused_with_a_finite_report(self):
        self.put('model/model_meta.json',b'['*10000+b'0'+b']'*10000)
        self.reject('train')
        nested={'value':0}
        for _ in range(129):nested={'child':nested}
        self.put('model/model_meta.json',nested)
        self.reject('train')
    def test_duplicate_parity_node_cannot_hide_a_skipped_model(self):
        def duplicate(value):
            for key in ['reference','packaged']:
                branch=value['images'][0][key]['branch_path']
                executed=copy.deepcopy(branch[0])
                branch[0]['status']='skipped'
                branch.append(executed)
        self.change('package/parity_receipt.json',duplicate)
        self.reject('export')
    def test_original_canonical_producer_definitions_are_the_same(self):
        self.assertIsNotNone(verifier)
        for path,name in [('backend/api/routes_dataset_versions.py','_manifest_digest'),('backend/engine/evaluation_history.py','canonical')]:
            tree=ast.parse((ROOT/path).read_text());node=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name==name)
            namespace={'json':json,'hashlib':hashlib};node.returns=None
            for arg in node.args.args:arg.annotation=None
            exec(compile(ast.Module(body=[node],type_ignores=[]),str(ROOT/path),'exec'),namespace)
            value={'b':'한글','a':1};expected=namespace[name](value)
            actual=sha(canonical(value)) if name=='_manifest_digest' else verifier.canonical(value)
            self.assertEqual(actual,expected)

    def _prepare_family(self,family='ocr',*,prepared=True):
        self.files={};self.receipt=self.fixture()
        labels=json.loads((self.root/'version/team-data.json').read_bytes())
        labels['settings']={'approved_only_training':True}
        labels['eligibility']=[{'relative_path':'a.bin','image_uuid':'a'*64},{'relative_path':'b.bin','image_uuid':'b'*64}]
        model=family_record_model(family,labels,prepared=prepared)
        for path,raw in model['files'].items():self.put(path,raw)
        heldout=b'synthetic distinct held-out pixels; no image decoder used';self.put('source/b.bin',heldout)
        manifest=json.loads((self.root/'version/manifest.json').read_bytes())
        manifest['files'].append({'origin':'source','kind':'image','relative_path':'b.bin','source_path':'/original/source/b.bin',
                                 'sha256':sha(heldout),'size_bytes':len(heldout),'snapshot_path':None})
        manifest['content_digest']=sha(canonical({key:value for key,value in manifest.items() if key!='content_digest'}))
        self.put('version/manifest.json',manifest);self.put('version/team-data.json',labels)
        self.receipt['dataset']['files']['source:b.bin']='source/b.bin'
        self.receipt['family']=family;self.receipt['train']['family_files']=model['copies']
        binding=json.loads((self.root/'model/job_receipt.json').read_bytes())['training_provenance']
        binding.update(model['binding'],team_data=labels,team_data_sha256=sha(canonical(labels)),manifest_sha256=manifest['content_digest'],
                       split_sha256=sha(json.dumps([{key:row[key] for key in ('origin','relative_path','sha256')} for row in manifest['files']],sort_keys=True,separators=(',',':')).encode()))
        self._replace_family_binding(binding,family)
        graph=json.loads((self.root/'saved/pipeline.json').read_bytes());graph['nodes'][0]['data']['task']=family
        self.put('saved/pipeline.json',graph);self.put('package/pipeline.json',canonical(graph))
        package=json.loads((self.root/'package/manifest.json').read_bytes());package['models'][0]['task']=family
        for row in package['files']:row.update(self.files['package/'+row['path']])
        self.put('package/manifest.json',package)
        parity=json.loads((self.root/'package/parity_receipt.json').read_bytes())
        parity.update(manifest_sha256=self.files['package/manifest.json']['sha256'],graph_sha256=self.files['package/pipeline.json']['sha256'])
        self.put('package/parity_receipt.json',parity)
        return model

    def _replace_family_binding(self,binding,family=None):
        family=family or self.receipt['family']
        job=json.loads((self.root/'model/job_receipt.json').read_bytes())
        job.update(task=family,training_provenance=binding,dataset_path=binding['family_dataset_path'])
        self.put('model/job_receipt.json',job)
        meta=json.loads((self.root/'model/model_meta.json').read_bytes())
        meta.update(task=family,training_provenance=binding)
        if family=='ocr':meta['dataset_path']=binding['family_dataset_path']
        else:meta.pop('dataset_path',None)
        self.put('model/model_meta.json',meta)
        evaluation=json.loads((self.root/'evaluation.json').read_bytes())
        evaluation['binding']['training_provenance']=binding;evaluation['result']['task']=family
        evaluation['evidence_sha256']=sha(canonical({key:value for key,value in evaluation.items() if key!='evidence_sha256'}))
        self.put('evaluation.json',evaluation)

    def test_prepared_ocr_and_patch_keep_all_approval_flags_false(self):
        for family in ('ocr','patch_classification'):
            for prepared in (False,True):
                with self.subTest(family=family,prepared=prepared):
                    self._prepare_family(family,prepared=prepared);result=self.check()
                    if family=='patch_classification' and not prepared:
                        self.assertFalse(result['record_chain_verified'],result)
                        self.assertEqual(result['stages']['train']['state'],'pending',result)
                        continue
                    self.assertTrue(result['record_chain_verified'],result)
                    for field in ('runtime_execution_reproduced','human_truth_approved','model_quality_approved','target_execution_approved','parent_accepted'):
                        self.assertIs(result[field],False)

    def test_family_inventory_digest_uses_original_ascii_default(self):
        self._prepare_family();binding=json.loads((self.root/'model/job_receipt.json').read_bytes())['training_provenance']
        self.assertNotEqual(binding['family_inputs_sha256'],sha(canonical(binding['family_inputs'])))
        tree=ast.parse((ROOT/'backend/engine/training_provenance.py').read_text())
        producer=next(node for node in tree.body if isinstance(node,ast.FunctionDef) and node.name=='bind_family_training')
        update=next(node for node in producer.body if isinstance(node,ast.Expr) and isinstance(node.value,ast.Call)
                    and isinstance(node.value.func,ast.Attribute) and node.value.func.attr=='update')
        expression=next(keyword.value for keyword in update.value.keywords if keyword.arg=='family_inputs_sha256')
        original=eval(compile(ast.Expression(expression),'original-family-row-digest','eval'),{'hashlib':hashlib,'json':json,'rows':binding['family_inputs']})
        self.assertEqual(original,binding['family_inputs_sha256']);self.assertTrue(self.check()['record_chain_verified'])
        binding['family_inputs_sha256']=sha(canonical(binding['family_inputs']));self._replace_family_binding(binding);self.reject('train')

    def test_family_current_and_frozen_bytes_must_both_match_raw_pins(self):
        for family in ('ocr','patch_classification'):
            for kind in ('current','frozen','image'):
                with self.subTest(family=family,kind=kind):
                    model=self._prepare_family(family);name='ocr.json' if family=='ocr' else 'patches.json'
                    path=('family/'+kind+'/'+name if kind!='image' else 'family/current/train/검사.bin')
                    self.put(path,(self.root/path).read_bytes()+b' ');self.reject('train')

    def test_family_copy_map_is_exact_relative_and_one_to_one(self):
        for mutation in ('missing','extra','absolute','traversal','duplicate'):
            with self.subTest(mutation=mutation):
                self._prepare_family();copies=self.receipt['train']['family_files'];keys=list(copies)
                if mutation=='missing':del copies[keys[0]]
                elif mutation=='extra':copies['/foreign/file']='source/a.bin'
                elif mutation=='absolute':copies[keys[0]]='/foreign/file'
                elif mutation=='traversal':copies[keys[0]]='family/../current/ocr.json'
                else:copies[keys[1]]=copies[keys[0]]
                self.reject('train')

    def test_family_inventory_duplicates_extras_order_and_snapshot_refused(self):
        mutations=[lambda rows:rows.append(copy.deepcopy(rows[0])),lambda rows:rows.reverse(),
                   lambda rows:rows[0].update(snapshot_path='/foreign/labels/ocr.json'),
                   lambda rows:rows[0].update(extra=True),lambda rows:rows[0].update(source_path='/foreign/ocr.json')]
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                self._prepare_family();binding=json.loads((self.root/'model/job_receipt.json').read_bytes())['training_provenance']
                mutation(binding['family_inputs']);binding['family_inputs_sha256']=sha(json.dumps(binding['family_inputs'],sort_keys=True,separators=(',',':')).encode())
                self._replace_family_binding(binding);self.reject('train')

    def test_family_provenance_purpose_dataset_and_version_refused(self):
        mutations=[lambda binding:binding.update(family_task='segmentation'),lambda binding:binding.update(family_dataset_path='/foreign/dataset'),
                   lambda binding:binding.update(version_dir='/foreign/versions/other'),
                   lambda binding:binding['family_provenance'].update(source_dataset_path='/foreign/source'),
                   lambda binding:binding['family_provenance'].update(dataset_sha256='sha256:'+'0'*64),
                   lambda binding:binding.update(family_source_image_uuids=['foreign'])]
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                self._prepare_family();binding=json.loads((self.root/'model/job_receipt.json').read_bytes())['training_provenance']
                mutation(binding);self._replace_family_binding(binding);self.reject('train')

    def test_family_legacy_unknown_and_relocated_aliases_stay_pending(self):
        for mutation in ('missing_copies','legacy_uuid','alias','unsupported'):
            with self.subTest(mutation=mutation):
                self._prepare_family();binding=json.loads((self.root/'model/job_receipt.json').read_bytes())['training_provenance']
                if mutation=='missing_copies':del self.receipt['train']['family_files']
                elif mutation=='legacy_uuid':del binding['family_source_image_uuids']
                elif mutation=='alias':binding['family_inputs'][0]['restored_source_sha256']=binding['family_inputs'][0]['sha256']
                else:binding['family_task']='enhancement';self.receipt['family']='enhancement'
                self._replace_family_binding(binding);result=self.reject('train')
                self.assertEqual(result['stages']['train']['state'],'pending',result)

    def test_family_missing_raw_copy_and_link_remain_refused_or_pending(self):
        self._prepare_family();path=self.root/'family/frozen/ocr.json';path.unlink();self.reject('train')
        self._prepare_family();path=self.root/'family/current/ocr.json';raw=path.read_bytes();path.unlink()
        (self.root/'foreign.json').write_bytes(raw);path.symlink_to(self.root/'foreign.json');self.reject('train')

    def test_family_map_cannot_hide_empty_or_malformed_inventory(self):
        for value in ([],{},'',None):
            with self.subTest(value=value):
                self._prepare_family();binding=json.loads((self.root/'model/job_receipt.json').read_bytes())['training_provenance']
                binding['family_inputs']=value;self._replace_family_binding(binding);self.reject('train')

    def test_family_raw_manifest_read_keeps_full_hash_and_json_size_bound(self):
        self._prepare_family();path='family/current/ocr.json';pin=self.files[path];raw=(self.root/path).read_bytes()
        self.assertEqual(verifier._read_pin(self.root,path,pin,False,raw=True),raw)
        self.assertNotEqual(raw,canonical(json.loads(raw)))
        with patch.object(verifier,'MAX_JSON_BYTES',len(raw)-1):
            with self.assertRaises(verifier.Gap):verifier._read_pin(self.root,path,pin,False,raw=True)
        (self.root/path).write_bytes(raw+b' ')
        with self.assertRaises(verifier.Gap):verifier._read_pin(self.root,path,pin,False,raw=True)

    def test_family_foreign_mapping_cannot_hide_behind_coherent_manifest_digests(self):
        self._prepare_family();copies=self.receipt['train']['family_files'];binding=json.loads((self.root/'model/job_receipt.json').read_bytes())['training_provenance']
        path=copies[binding['family_dataset_path']+'/ocr.json'];body=json.loads((self.root/path).read_bytes())
        body['source_map']['train/검사.bin']['source_relative_path']='foreign.bin'
        raw=(json.dumps(body,ensure_ascii=False,indent=2)+'\n').encode();manifest_sha=sha(raw)
        self.put(path,raw);self.put(copies[binding['version_dir']+'/labels/family/ocr/ocr.json'],raw)
        next(row for row in binding['family_inputs'] if row['relative_path']=='ocr.json')['sha256']=manifest_sha
        binding['family_inputs_sha256']=sha(json.dumps(binding['family_inputs'],sort_keys=True,separators=(',',':')).encode())
        provenance=binding['family_provenance'];provenance.update(manifest_sha256=manifest_sha,source_map=body['source_map'])
        digest=hashlib.sha256(b'ocr-dataset-v1\0'+raw)
        for image,value in sorted(provenance['source_sha256'].items()):digest.update(b'\0'+image.encode()+b'\0'+value.encode())
        provenance['dataset_sha256']='sha256:'+digest.hexdigest()
        binding['family_dataset_sha256']=provenance['dataset_sha256']
        self._replace_family_binding(binding);self.reject('train')

    def test_prepared_train_joins_original_job_and_optional_patch_metadata(self):
        for family in ('ocr','patch_classification'):
            for path in ('model/job_receipt.json','model/model_meta.json'):
                for mutation in ('foreign','missing','null'):
                    with self.subTest(family=family,path=path,mutation=mutation):
                        self._prepare_family(family)
                        if mutation=='missing':self.change(path,lambda value:value.pop('dataset_path',None))
                        else:self.change(path,lambda value:value.update(dataset_path=None if mutation=='null' else '/foreign/data'))
                        if family=='patch_classification' and path=='model/model_meta.json' and mutation=='missing':
                            self.assertTrue(self.check()['record_chain_verified'])
                        else:
                            result=self.reject('train')
                            self.assertEqual(result['stages']['train']['state'],'pending' if mutation=='missing' else 'refused',result)
        self._prepare_family('patch_classification')
        job=json.loads((self.root/'model/job_receipt.json').read_bytes())
        self.change('model/model_meta.json',lambda value:value.update(dataset_path=job['dataset_path']))
        self.assertTrue(self.check()['record_chain_verified'])

    def test_ocr_training_digest_joins_recomputed_manifest_and_member_bytes(self):
        for mutation in ('foreign','missing','null'):
            with self.subTest(mutation=mutation):
                self._prepare_family();binding=json.loads((self.root/'model/job_receipt.json').read_bytes())['training_provenance']
                if mutation=='missing':binding.pop('family_dataset_sha256')
                else:binding['family_dataset_sha256']=None if mutation=='null' else 'sha256:'+'0'*64
                self._replace_family_binding(binding);result=self.reject('train')
                self.assertEqual(result['stages']['train']['state'],'pending' if mutation=='missing' else 'refused',result)

    def test_approved_only_training_cannot_filter_a_missing_original_member(self):
        for family in ('ocr','patch_classification'):
            for approved in (True,False):
                with self.subTest(family=family,approved=approved):
                    self._prepare_family(family);binding=json.loads((self.root/'model/job_receipt.json').read_bytes())['training_provenance']
                    labels=binding['team_data'];labels['settings']['approved_only_training']=approved
                    labels['eligibility']=labels['eligibility'][:1]
                    binding['family_source_image_uuids']=['a'*64];binding['team_data_sha256']=sha(canonical(labels))
                    self.put('version/team-data.json',labels);self._replace_family_binding(binding)
                    if approved:self.assertEqual(self.reject('train')['stages']['train']['state'],'refused')
                    else:self.assertTrue(self.check()['record_chain_verified'])

    def test_patch_optional_source_hash_null_omitted_and_wrong_claim(self):
        for mutation in ('null','omitted','foreign'):
            with self.subTest(mutation=mutation):
                self._prepare_family('patch_classification');binding=json.loads((self.root/'model/job_receipt.json').read_bytes())['training_provenance']
                copies=self.receipt['train']['family_files'];path=copies[binding['family_dataset_path']+'/patches.json']
                body=json.loads((self.root/path).read_bytes())
                if mutation=='omitted':body['patches'][0].pop('source_sha256')
                else:body['patches'][0]['source_sha256']=None if mutation=='null' else '0'*64
                raw=(json.dumps(body,ensure_ascii=False,indent=2)+'\n').encode();digest=sha(raw)
                self.put(path,raw);self.put(copies[binding['version_dir']+'/labels/family/patch_classification/patches.json'],raw)
                next(row for row in binding['family_inputs'] if row['relative_path']=='patches.json')['sha256']=digest
                binding['family_inputs_sha256']=sha(json.dumps(binding['family_inputs'],sort_keys=True,separators=(',',':')).encode())
                provenance=binding['family_provenance'];provenance['manifest_sha256']=digest
                value=hashlib.sha256(b'patch-classification-dataset-v1\0'+digest.encode())
                for image,member_sha in sorted(provenance['source_sha256'].items()):value.update(b'\0'+image.encode()+b'\0'+member_sha.encode())
                provenance['dataset_sha256']='sha256:'+value.hexdigest()
                self._replace_family_binding(binding)
                if mutation=='foreign':self.assertEqual(self.reject('train')['stages']['train']['state'],'refused')
                else:self.assertTrue(self.check()['record_chain_verified'])

    def test_missing_or_malformed_prepared_training_settings_remain_unqualified(self):
        for setting in (None,{}, {'approved_only_training':1}):
            with self.subTest(setting=setting):
                self._prepare_family();binding=json.loads((self.root/'model/job_receipt.json').read_bytes())['training_provenance']
                labels=binding['team_data']
                if setting is None:labels.pop('settings')
                else:labels['settings']=setting
                binding['team_data_sha256']=sha(canonical(labels));self.put('version/team-data.json',labels)
                self._replace_family_binding(binding);result=self.reject('train')
                self.assertEqual(result['stages']['train']['state'],'refused' if setting else 'pending',result)

if __name__=='__main__':unittest.main()
