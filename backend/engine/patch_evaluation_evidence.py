"""Adopt portable held-out patch outcomes only for the exact original cohort."""
from pathlib import Path
from collections import Counter
import json
from backend.remote.recipe import sha


def archive_patch_evaluation(project,checkpoint,dataset,result,*,execution):
    from backend.api.routes_evaluation import _patch_manifest_for_checkpoint
    from backend.api.routes_model_comparisons import _fingerprint
    from backend.engine.evaluation_history import EvaluationHistory,evaluation_model_context
    from backend.engine.evaluation_evidence import evaluation_analysis
    from backend.engine.dataset_metadata import metadata_for_path
    from backend.engine.annotation_storage import scoped_annotation_root
    from backend.api import routes_dataset
    checkpoint=Path(checkpoint);dataset=Path(dataset);source=Path(project['source_dataset_dir']).resolve()
    metadata=json.loads(checkpoint.with_name('model_meta.json').read_text());manifest=_patch_manifest_for_checkpoint(dataset,metadata);model_hash=sha(checkpoint)
    split=result.get('metrics',{}).get('evaluated_split');mapping=manifest.provenance.get('source_map')
    if (split not in ('val','test') or not isinstance(mapping,dict) or manifest.provenance.get('source_dataset_path')!=str(source)
            or result.get('dataset_sha256')!=manifest.provenance['dataset_sha256'] or result.get('model_sha256')!=model_hash or result.get('quality_approved') is not False):
        raise ValueError('Patch evaluation cohort or model binding differs')
    rows=result.get('test_predictions');expected=Counter((row.image,row.box,row.label,row.source_sha256) for row in manifest.patches if row.split==split)
    if not isinstance(rows,list) or Counter((row.get('image'),tuple(row.get('box',[])),row.get('ground_truth'),row.get('source_sha256')) for row in rows)!=expected:
        raise ValueError('Patch evaluation outcomes differ from the exact held-out patches')
    payload=json.loads(json.dumps(result));cells={f'{a}:{b}':[] for a in manifest.classes for b in manifest.classes}
    for row in payload['test_predictions']:
        name=row['image'];original=source/mapping[name]['source_relative_path']
        if original.is_symlink() or not original.resolve().is_relative_to(source) or sha(original)!=row['source_sha256']:
            raise ValueError('Patch evaluation original source bytes changed')
        if row.get('model_sha256')!=model_hash or row.get('dataset_sha256')!=manifest.provenance['dataset_sha256'] or row.get('predicted_class') not in manifest.classes:
            raise ValueError('Patch evaluation outcome identity differs')
        facts=metadata_for_path(Path(project['project_dir']),source,original,scoped_annotation_root(routes_dataset.STUDIO_ANNOTATIONS_DIR))
        row.update({key:facts.get(key) for key in ('image_uuid','file_path','content_hash','content_version','revision','product','lot','group','tags')})
        row.update(file_path=str(original),evaluation_file_path=str(dataset/name),thumbnail_url=f'/api/dataset/thumbnail/{original.name}?file_path={original}')
        cells[f"{row['ground_truth']}:{row['predicted_class']}"].append(str(original))
    payload['confusion_matrix']['cell_samples']=cells
    payload['analysis']=evaluation_analysis(payload['test_predictions'],'patch_classification')
    binding={'source_dataset_path':str(source),'evaluation_dataset_path':str(dataset),'dataset_fingerprint':_fingerprint(source),
        'checkpoint_sha256':model_hash,'family_dataset_sha256':manifest.provenance['dataset_sha256'],
        **evaluation_model_context(project['project_dir'],metadata),**execution}
    record=EvaluationHistory(Path(project['reports_dir'])/'evaluations').append(payload,binding)
    return {**payload,'evaluation_id':record['evaluation_id'],'binding':binding,'grouped_errors':record['grouped_errors']}
