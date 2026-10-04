"""Only recorded training context is displayed after compatible-parent validation."""
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


def _context(tmp_path):
    from backend.tests.test_warm_start import _parent
    models, source, checkpoint, _ = _parent(tmp_path)
    meta=checkpoint.with_name('model_meta.json');receipt=checkpoint.with_name('job_receipt.json')
    obj=json.loads(meta.read_text());obj.update(best_metric=0.91,created_at='2026-09-30T00:00:00Z');meta.write_text(json.dumps(obj))
    obj=json.loads(receipt.read_text());obj.update(metrics={'val_loss':0.25,'val_accuracy':0.9,'private_training_note':'not display context'},completed_at='2026-09-30T01:00:00+00:00');receipt.write_text(json.dumps(obj))
    return models,source,checkpoint


def _core(models,source,monkeypatch):
    from backend.api import routes_training
    monkeypatch.setattr(routes_training,'_warm_start_scope',lambda request,source:models)
    monkeypatch.setattr('backend.engine.warm_start.training_classes',lambda task,source:('OK','NG'))
    return routes_training.list_warm_start_parents(str(source),'classification',request=SimpleNamespace(),backbone='resnet18')['parents'][0]


def test_core_parent_context_reads_saved_metrics_dates_without_changing_sources(tmp_path,monkeypatch):
    models,source,checkpoint=_context(tmp_path);files=[checkpoint,checkpoint.with_name('model_meta.json'),checkpoint.with_name('job_receipt.json')]
    before={p:hashlib.sha256(p.read_bytes()).hexdigest() for p in files};row=_core(models,source,monkeypatch)
    assert row['summary']['model_recorded_at']=='2026-09-30T00:00:00Z'
    assert row['summary']['completed_at']=='2026-09-30T01:00:00Z'
    assert row['summary']['training_metrics']=={'best_metric':0.91,'val_loss':0.25,'val_accuracy':0.9}
    assert before=={p:hashlib.sha256(p.read_bytes()).hexdigest() for p in files}


def test_parent_context_never_invents_completion_from_checkpoint_date_or_file_mtime(tmp_path,monkeypatch):
    models,source,checkpoint=_context(tmp_path);receipt=checkpoint.with_name('job_receipt.json');obj=json.loads(receipt.read_text());obj.pop('completed_at');obj['metrics']={'val_loss':float('nan'),'val_accuracy':True,'accuracy':0};receipt.write_text(json.dumps(obj))
    row=_core(models,source,monkeypatch)
    assert row['summary']['completed_at'] is None
    assert row['summary']['training_metrics']=={'best_metric':0.91,'accuracy':0}


def test_specialist_list_preserves_its_eligibility_and_uses_the_same_summary(tmp_path,monkeypatch):
    from backend.engine import specialized_warm_start
    models,source,checkpoint=_context(tmp_path)
    directory=models/'ocr'/'0123456789abcdef0123456789abcdef';directory.mkdir(parents=True)
    for name in ['best_model.pt','model_meta.json','job_receipt.json']:(directory/name).write_bytes(checkpoint.with_name(name).read_bytes())
    seen=[]
    def resolve(*args):
        seen.append(args);return SimpleNamespace(job_id=directory.name,architecture='controlled-compatible',classes=('A','B'),checkpoint_sha256='a'*64,dataset_fingerprint='v1:controlled',semantics='weight_initialization',checkpoint_path=directory/'best_model.pt')
    monkeypatch.setattr(specialized_warm_start,'resolve_family_parent',resolve)
    row=specialized_warm_start.list_family_parents(models,'ocr',source,source)['parents'][0]
    assert len(seen)==1 and row['architecture']=='controlled-compatible'
    assert row['summary']['training_metrics']['val_loss']==0.25
    assert row['summary']['completed_at']=='2026-09-30T01:00:00Z'

@pytest.mark.parametrize('value', [None, True, '2026-09-30', '2026-09-30T00:00:00', '2026-02-30T00:00:00Z', float('inf')])
def test_display_context_keeps_unqualified_or_invalid_dates_unknown(tmp_path,value):
    from backend.engine.parent_summary import parent_summary
    folder=tmp_path/'record';folder.mkdir()
    (folder/'model_meta.json').write_text(json.dumps({'created_at':value,'best_metric':True}))
    (folder/'job_receipt.json').write_text(json.dumps({'completed_at':value,'metrics':{'val_loss':float('inf'),'accuracy':0}}))
    summary=parent_summary(folder)
    assert summary=={'model_recorded_at':None,'completed_at':None,'training_metrics':{'accuracy':0}}


def test_display_context_is_bounded_and_refuses_linked_or_non_object_records(tmp_path):
    from backend.engine.parent_summary import parent_summary
    folder=tmp_path/'record';folder.mkdir();other=tmp_path/'other.json';other.write_text(json.dumps({'created_at':'2026-09-30T00:00:00Z','best_metric':1}))
    (folder/'model_meta.json').symlink_to(other)
    (folder/'job_receipt.json').write_text('[]')
    assert parent_summary(folder)=={'model_recorded_at':None,'completed_at':None,'training_metrics':{}}
    (folder/'model_meta.json').unlink();(folder/'model_meta.json').write_text(json.dumps({'created_at':'2026-09-30T00:00:00Z','padding':'a'*(256*1024)}))
    assert parent_summary(folder)['model_recorded_at'] is None


def test_timezone_offset_is_normalized_but_checkpoint_time_never_becomes_completion(tmp_path):
    from backend.engine.parent_summary import parent_summary
    folder=tmp_path/'record';folder.mkdir()
    (folder/'model_meta.json').write_text(json.dumps({'created_at':'2026-09-30T09:00:00+09:00'}))
    assert parent_summary(folder)=={'model_recorded_at':'2026-09-30T00:00:00Z','completed_at':None,'training_metrics':{}}


@pytest.mark.parametrize('metadata,expected', [
    ({'best_validation_loss':0.4}, {'saved_val_loss':0.4}),
    ({'best_validation_loss':0.3,'validation':{'loss':0.3,'angular_mae_deg':12,'predictions':[{'private':'excluded'}]}}, {'saved_val_loss':0.3,'saved_angular_mae_deg':12}),
    ({'validation':{'mean_oriented_iou':0.7,'mAP_50':0.8,'mAP_50_95':0.5,'test_predictions':[{'private':'excluded'}]}}, {'saved_mean_oriented_iou':0.7,'saved_mAP_50':0.8,'saved_mAP_50_95':0.5}),
    ({'last_generator_loss':0.2,'last_discriminator_loss':0.6}, {'last_generator_loss':0.2,'last_discriminator_loss':0.6}),
])
def test_specialist_saved_context_uses_existing_record_shapes_and_keeps_last_epoch_separate(tmp_path,metadata,expected):
    from backend.engine.parent_summary import parent_summary
    (tmp_path/'model_meta.json').write_text(json.dumps(metadata))
    (tmp_path/'job_receipt.json').write_text(json.dumps({'metrics':{'val_loss':0.9}}))
    assert parent_summary(tmp_path)['training_metrics']=={**expected,'val_loss':0.9}


def test_extreme_numeric_display_context_cannot_break_the_parent_list(tmp_path):
    from backend.engine.parent_summary import parent_summary
    (tmp_path/'model_meta.json').write_text(json.dumps({'best_metric':10**400,'created_at':10**400}))
    assert parent_summary(tmp_path)=={'model_recorded_at':None,'completed_at':None,'training_metrics':{}}


def test_deeply_nested_display_record_is_ignored_without_recursion_error(tmp_path):
    from backend.engine.parent_summary import parent_summary
    import sys
    levels = sys.getrecursionlimit()+100
    (tmp_path/'model_meta.json').write_text('['*levels+'0'+']'*levels)
    assert parent_summary(tmp_path)=={'model_recorded_at':None,'completed_at':None,'training_metrics':{}}
