import json
from pathlib import Path

import pytest
from PIL import Image


def _fixture(tmp_path):
    source=tmp_path/'source';source.mkdir()
    original=source/'bow_06.png';Image.new('RGB',(32,32),'white').save(original)
    job=tmp_path/'models'/'job_remote'
    prepared=job/'dataset';remote=job/'remote_snapshot'/'data'
    relative=Path('images/test/sample_00006.png')
    for root in [prepared,remote]:
        (root/relative).parent.mkdir(parents=True)
        (root/relative).write_bytes(original.read_bytes())
    (prepared/'source_manifest.json').write_text(json.dumps([{'image':str(prepared/relative),'source_image':str(original)}]))
    return source,original,job,prepared,remote,relative


def test_remote_snapshot_prediction_opens_exact_original_image(tmp_path):
    from backend.engine.evaluation_sources import remap_prepared_predictions
    source,original,job,prepared,remote,relative=_fixture(tmp_path)
    rows=[{'image_id':'sample_00006','file_name':'sample_00006.png','file_path':str(remote/relative)}]
    remap_prepared_predictions(rows,remote,source,job)
    assert rows[0]['file_path']==str(original)
    assert rows[0]['evaluation_file_path']==str(remote/relative)
    assert rows[0]['image_id']=='sample_00006'
    assert rows[0]['source_image_id']=='bow_06'
    assert rows[0]['file_name']=='bow_06.png'
    assert rows[0]['labeling_supported'] is True


def test_already_remapped_cached_prediction_is_idempotent(tmp_path):
    from backend.engine.evaluation_sources import remap_prepared_predictions
    source,original,job,prepared,remote,relative=_fixture(tmp_path)
    rows=[{'image_id':'sample_00006','file_path':str(original),
           'evaluation_file_path':str(remote/relative),'source_image_id':original.stem}]
    remap_prepared_predictions(rows,remote,source,job)
    first=dict(rows[0])
    remap_prepared_predictions(rows,remote,source,job)
    assert rows[0]==first
    assert rows[0]['labeling_supported'] is True


def test_cached_original_symlink_is_rejected(tmp_path):
    from backend.engine.evaluation_sources import remap_prepared_predictions
    source,original,job,prepared,remote,relative=_fixture(tmp_path)
    alias=source/'alias.png';alias.symlink_to(original)
    rows=[{'file_path':str(alias),'evaluation_file_path':str(remote/relative)}]
    with pytest.raises(ValueError,match='symlink'):
        remap_prepared_predictions(rows,remote,source,job)


def test_handoff_uses_exact_prepared_relative_path_not_basename(tmp_path):
    from backend.engine.evaluation_sources import remap_prepared_predictions
    source,original,job,prepared,remote,relative=_fixture(tmp_path)
    other=remote/'images/val'/relative.name
    other.parent.mkdir(parents=True);other.write_bytes(original.read_bytes())
    rows=[{'file_path':str(other)}]
    remap_prepared_predictions(rows,remote,source,job)
    assert rows[0]['file_path']==str(other)
    assert rows[0]['labeling_supported'] is False


def test_ambiguous_prepared_mapping_is_rejected(tmp_path):
    from backend.engine.evaluation_sources import remap_prepared_predictions
    source,original,job,prepared,remote,relative=_fixture(tmp_path)
    other=source/'bow_07.png';other.write_bytes(original.read_bytes())
    (prepared/'source_manifest.json').write_text(json.dumps([
        {'image':str(prepared/relative),'source_image':str(path)} for path in (original,other)]))
    with pytest.raises(ValueError,match='ambiguous'):
        remap_prepared_predictions([{'file_path':str(remote/relative)}],remote,source,job)


@pytest.mark.parametrize('linked',['source','prepared','snapshot','manifest'])
def test_source_handoff_rejects_symlinks(tmp_path,linked):
    from backend.engine.evaluation_sources import remap_prepared_predictions
    source,original,job,prepared,remote,relative=_fixture(tmp_path)
    selected={'source':original,'prepared':prepared/relative,'snapshot':remote/relative,
              'manifest':prepared/'source_manifest.json'}[linked]
    backing=tmp_path/f'{linked}-backing';selected.rename(backing);selected.symlink_to(backing)
    with pytest.raises(ValueError,match='symlink'):
        remap_prepared_predictions([{'file_path':str(remote/relative)}],remote,source,job)


def test_relative_prepared_manifest_path_remaps_exact_image(tmp_path):
    from backend.engine.evaluation_sources import remap_prepared_predictions
    source,original,job,prepared,remote,relative=_fixture(tmp_path)
    (prepared/'source_manifest.json').write_text(json.dumps([
        {'image':str(relative),'source_image':str(original)}]))
    rows=[{'file_path':str(remote/relative)}]
    remap_prepared_predictions(rows,remote,source,job)
    assert rows[0]['file_path']==str(original)


def test_source_handoff_rejects_changed_prepared_bytes(tmp_path):
    from backend.engine.evaluation_sources import remap_prepared_predictions
    source,original,job,prepared,remote,relative=_fixture(tmp_path)
    (prepared/relative).write_bytes(b'changed local preparation')
    with pytest.raises(ValueError,match='prepared'):
        remap_prepared_predictions([{'file_path':str(remote/relative)}],remote,source,job)


def test_source_handoff_rejects_foreign_source_mapping(tmp_path):
    from backend.engine.evaluation_sources import remap_prepared_predictions
    source,original,job,prepared,remote,relative=_fixture(tmp_path)
    foreign=tmp_path/'foreign.png';foreign.write_bytes(original.read_bytes())
    (prepared/'source_manifest.json').write_text(json.dumps([{'image':str(prepared/relative),'source_image':str(foreign)}]))
    with pytest.raises(ValueError,match='source'):
        remap_prepared_predictions([{'file_path':str(remote/relative)}],remote,source,job)


def test_missing_mapping_keeps_evidence_but_disables_label_handoff(tmp_path):
    from backend.engine.evaluation_sources import remap_prepared_predictions
    source,original,job,prepared,remote,relative=_fixture(tmp_path)
    (prepared/'source_manifest.json').unlink()
    rows=[{'image_id':'sample_00006','file_path':str(remote/relative)}]
    remap_prepared_predictions(rows,remote,source,job)
    assert rows[0]['file_path']==str(remote/relative)
    assert rows[0]['labeling_supported'] is False


def test_direct_source_evaluation_keeps_label_handoff_without_preparation(tmp_path):
    from backend.engine.evaluation_sources import remap_prepared_predictions
    source,original,job,prepared,remote,relative=_fixture(tmp_path)
    rows=[{'image_id':'bow_06','file_path':str(original)}]
    remap_prepared_predictions(rows,source,source,job/'unprepared')
    assert rows[0]['file_path']==str(original)
    assert rows[0]['labeling_supported'] is True


@pytest.mark.parametrize('cached',[False,True])
def test_evaluation_route_remaps_fresh_and_cached_snapshot_results(tmp_path,monkeypatch,cached):
    import copy
    from types import SimpleNamespace
    from backend.api import routes_evaluation
    from backend.api.routes_model_comparisons import _fingerprint,_sha256
    from backend.engine.evaluation_history import evaluation_model_context
    from backend.remote import operations
    source,original,job,prepared,remote,relative=_fixture(tmp_path)
    checkpoint=job/'best_model.pt';checkpoint.write_bytes(b'cache identity checkpoint')
    metadata={'task':'segmentation','classes':['background','Bow']}
    semantics=routes_evaluation._evaluation_class_semantics(metadata,'segmentation')
    payload={'job_id':job.name,'task':'segmentation','class_semantics':semantics,
             'evaluation_contract_version':routes_evaluation.EVALUATION_CONTRACT_VERSION,
             'metrics':{'evaluated_split':'test'},
             'confusion_matrix':{'cell_samples':{'0_1':[str(remote/relative)]}},
             'test_predictions':[{'image_id':'sample_00006','file_path':str(remote/relative),
                                  'ground_truth':'Bow','prediction':'background'}]}
    binding={'source_dataset_path':str(source),'dataset_fingerprint':_fingerprint(source),
             'checkpoint_sha256':_sha256(checkpoint)}
    binding.update(evaluation_model_context(job.parent.parent,metadata))
    payload['binding']=binding
    monkeypatch.setattr(routes_evaluation,'_resolve_job_artifacts',lambda **kw:
        (job,checkpoint,metadata,'segmentation',job.name,remote))
    monkeypatch.setattr(operations,'remote_job_context',lambda *args:SimpleNamespace(dataset_path=remote))
    if cached:
        (job/'eval_results.json').write_text(json.dumps(payload))
        def unexpected_evaluation(*args,**kwargs):
            raise AssertionError('Valid cached evidence must not submit evaluation')
        monkeypatch.setattr(operations,'run_remote_evaluation',unexpected_evaluation)
    else:
        monkeypatch.setattr(operations,'run_remote_evaluation',lambda *args,**kwargs:copy.deepcopy(payload))
    result=routes_evaluation.run_or_load_evaluation(job_id=job.name,source_dataset_path=str(source))
    prediction=result['test_predictions'][0]
    assert prediction['file_path']==str(original)
    assert prediction['evaluation_file_path']==str(remote/relative)
    assert prediction['image_id']=='sample_00006'
    assert prediction['source_image_id']=='bow_06'
    assert prediction['labeling_supported'] is True
