"""Real CPU inference fixtures; deterministic weights carry no quality claim."""
from pathlib import Path
import hashlib
import torch
from backend.engine.classification.model import create_classification_model


def real_classification_checkpoints(models):
    import json
    # This supported backbone halves repeated contract-fixture storage. These
    # initialized weights prove actual process inference, never model quality.
    model=create_classification_model(backbone='efficientnet_b0',num_classes=2,pretrained=False)
    with torch.no_grad():
        for value in model.parameters():value.zero_()
        model.classifier[-1].bias.copy_(torch.tensor([3.,0.]))
    for checkpoint in models.values():
        torch.save({'task':'classification','backbone':'efficientnet_b0','classes':['OK','NG'],
                    'image_size':[32,32],'model_state_dict':model.state_dict()},checkpoint)
        metadata=checkpoint.with_name('model_meta.json')
        previous=json.loads(metadata.read_text()) if metadata.exists() else {}
        metadata.write_text(json.dumps({**previous,'task':'classification','backbone':'efficientnet_b0',
                                       'classes':['OK','NG'],'image_size':[32,32]}))


def cohort_receipt(package,graph,checkpoints,images):
    from backend.engine.flow_package import verify_flow_parity_cohort,write_parity_receipt
    previous=torch.get_num_threads()
    try:
        torch.set_num_threads(1)
        report=verify_flow_parity_cohort(package_dir=Path(package),pipeline=graph,checkpoints=checkpoints,
                                       images=images,device='cpu')
    finally:torch.set_num_threads(previous)
    assert report['status']=='passed',report
    assert all(row['packaged']['final_verdict'] in ('OK','NG') for row in report['images']),report
    write_parity_receipt(Path(package),report)
    return report


def bind_policy(policy,package):
    return {**policy,'device':'cpu','parity_receipt_sha256':hashlib.sha256((Path(package)/'parity_receipt.json').read_bytes()).hexdigest()}


def synthetic_service_truth(project,images,*,models=None):
    import json
    from backend.engine import image_truth
    source=Path(project['source_dataset_dir']).resolve()
    relative=[str(Path(image).resolve().relative_to(source)) for image in images]
    split=Path(project['dataset_dir'])/'splits'/f'{hashlib.sha256(str(source).encode()).hexdigest()}.json'
    split.parent.mkdir(parents=True,exist_ok=True)
    if split.exists():
        saved=json.loads(split.read_text())
        assert saved['folder_path']==str(source)
        assert all(saved['assignments'].get(name)=='test' for name in relative)
    else:split.write_text(json.dumps({'folder_path':str(source),'assignments':{name:'test' for name in relative}}))
    for image in images:
        verdict=Path(image).parent.name
        assert verdict in {'OK','NG'}
        current=image_truth.read_truth(project,str(image),task='classification',classes=['OK','NG'])
        if current['verdict']==verdict:continue
        image_truth.declare_truth(project,str(image),task='classification',classes=['OK','NG'],verdict=verdict,
            defect_classes=['NG'] if verdict=='NG' else [],reviewer='Synthetic fixture authority',
            expected_revision=current['truth_revision'],expected_image_revision=current['image_revision'])
    from backend.api.routes_model_deployments import _fingerprint
    from backend.engine.release_eligibility import evidence_context
    with evidence_context(project):fingerprint=_fingerprint(source)
    if models is not None:
        # Explicit setup only, before comparison/approval: these initialized
        # models belong to this synthetic source and fixed heldout split.
        for checkpoint in models.values():
            receipt=checkpoint.with_name('job_receipt.json')
            saved=json.loads(receipt.read_text())
            assert saved['status']=='completed' and saved['task']=='classification'
            assert Path(saved['source_dataset_path']).resolve()==source
            receipt.write_text(json.dumps({**saved,'dataset_fingerprint':fingerprint}))
    return fingerprint


def synthetic_model_report(project,source,fingerprint,models,**kwargs):
    """Bind controlled comparison rows to current truth and actual model bytes.

    Predictions remain synthetic authority controls, never quality evidence.
    """
    import json
    from backend.tests.test_model_deployments import _report
    from backend.engine.release_eligibility import evidence_context
    from backend.engine.comparison_truth import bind_truth
    from backend.api.routes_model_comparisons import _model,_fingerprint
    report=_report(project,source,fingerprint,models,**kwargs)
    with evidence_context(project):
        for row in report['images']:row['ground_truth_label']=Path(row['file_path']).parent.name
        report['truth_binding']=bind_truth(project,source,'classification',
            [_model(project,source,'classification',report[key]) for key in ('incumbent_job_id','candidate_job_id')],report['images'])
        report['dataset_fingerprint']=_fingerprint(source)
    (Path(project['reports_dir'])/'model_comparisons'/(report['comparison_id']+'.json')).write_text(json.dumps(report))
    return report


def reviewed_graph_fixture(project,graph,images):
    """Actual CPU graph evaluation with explicitly permissive synthetic policy.

    The deterministic all-OK weights deliberately miss the one defect input.
    This control accepts escape_rate=1 solely to exercise service authority;
    it cannot supply manufacturing quality or a real person's approval.
    """
    import json,uuid
    from backend.engine import flow_evaluation,whole_flow_approval
    synthetic_service_truth(project,images)
    source=Path(project['source_dataset_dir']).resolve()
    relative=[str(Path(image).resolve().relative_to(source)) for image in images]
    split=Path(project['dataset_dir'])/'splits'/f'{hashlib.sha256(str(source).encode()).hexdigest()}.json'
    split.parent.mkdir(parents=True,exist_ok=True)
    if split.exists():
        saved=json.loads(split.read_text())
        assert saved['folder_path']==str(source)
        assert all(saved['assignments'].get(name)=='test' for name in relative)
    else:split.write_text(json.dumps({'folder_path':str(source),'assignments':{name:'test' for name in relative}}))
    version=uuid.uuid4().hex
    versions=Path(project['project_dir'])/'flowcharts'/'versions';versions.mkdir(parents=True,exist_ok=True)
    (versions/(version+'.json')).write_text(json.dumps({'version_id':version,'source_dataset_path':str(source),
        'labelset_id':project.get('active_labelset_id','default'),'recipe_task':'classification','pipeline':graph.model_dump()}))
    from backend.engine.release_eligibility import evidence_context
    previous=torch.get_num_threads()
    try:
        torch.set_num_threads(1)
        with evidence_context(project):
            cohort=flow_evaluation.freeze_cohort(project,version,relative_paths=relative,name='Synthetic OK/NG authority control')
            evaluation=flow_evaluation.evaluate_flow(project,version,cohort['cohort_id'])
    finally:torch.set_num_threads(previous)
    assert evaluation['status']=='completed',evaluation['errors']
    assert evaluation['coverage']['known']==2 and not evaluation['errors'],evaluation['coverage']
    assert evaluation['metrics']['escape_rate']==1,evaluation['metrics']
    current=whole_flow_approval.current_approval(project)
    return whole_flow_approval.approve_flow(project,evaluation_id=evaluation['evaluation_id'],
        policy={'policy_id':'synthetic-service-authority','revision':1,'minimum_normal':1,'minimum_defect':1,
                'maximum_escape_rate':1.,'maximum_overkill_rate':1.,'maximum_review_rate':0.},
        reviewer='Synthetic fixture authority',reason='Permissive synthetic process control only; not manufacturing quality',
        holdout_reviewed=True,expected_revision=current['revision_id'] if current else None)
