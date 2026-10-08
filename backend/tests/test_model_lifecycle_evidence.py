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

if __name__=='__main__':unittest.main()
