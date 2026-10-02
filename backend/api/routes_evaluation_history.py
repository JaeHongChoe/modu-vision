"""Project-scoped immutable evaluation history for every model family."""
from pathlib import Path
from typing import Literal
import re
import json
from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel
from backend.api.routes_project import get_current_project
from backend.api.routes_model_comparisons import _scope, _fingerprint, _sha256
from backend.api.routes_evaluation import run_or_load_evaluation
from backend.engine.evaluation_history import EvaluationHistory,archive_specialized_evaluation

router=APIRouter(prefix='/api/evaluation',tags=['evaluation-history'])
HistoryTask=Literal['classification','patch_classification','detection','segmentation','anomaly','ocr','rotated_detection','enhancement','defect_gan','rotation']
SPECIALIZED=('ocr','rotated_detection','enhancement','defect_gan','rotation')
class ReevaluateRequest(BaseModel):
    source_dataset_path:str
    task:HistoryTask
    job_id:str
    dataset_path:str|None=None


def history_store(project):return EvaluationHistory(Path(project['project_dir'])/'reports'/'evaluations')


def history_scope(request,source_dataset_path,task):
    project=get_current_project(request)
    source=Path(source_dataset_path).expanduser().resolve()
    if not source.is_dir() or not project.get('source_dataset_dir') or source!=Path(project['source_dataset_dir']).resolve():
        raise HTTPException(409,'Evaluation source differs from active project')
    return project,source


@router.post('/reevaluate')
def reevaluate(payload:ReevaluateRequest,request:Request):
    project,source=history_scope(request,payload.source_dataset_path,payload.task)
    if payload.task not in SPECIALIZED:
        from backend.api.routes_model_comparisons import _model
        model = _model(project, source, payload.task, payload.job_id)
        if model is None: raise HTTPException(409, 'Reevaluation requires a completed source-bound model in the active project')
        from backend.api import routes_dataset
        fingerprint = _fingerprint(source)
        dataset = source
        if (payload.task in ('detection','segmentation') and routes_dataset._paired_labelme_images(source)
                and not routes_dataset._read_split_manifest(source)):
            import uuid
            dataset = Path(project['reports_dir']) / 'reevaluation_inputs' / uuid.uuid4().hex
            assignments = routes_dataset._read_split_manifest(source)
            from backend.engine.annotation_storage import scoped_annotation_root
            if payload.task == 'detection':
                from backend.engine.labelme_detection_preparation import prepare_labelme_detection as prepare
            else:
                from backend.engine.labelme_preparation import prepare_labelme_segmentation as prepare
            prepare(source,dataset,image_size=256,assignments=assignments,require_complete_assignments=bool(assignments),annotation_root=scoped_annotation_root(routes_dataset.STUDIO_ANNOTATIONS_DIR))
        if _fingerprint(source) != fingerprint: raise HTTPException(409, 'Evaluation source changed during preparation')
        return run_or_load_evaluation(job_id=payload.job_id,dataset_path=str(dataset),force_recompute=True,source_dataset_path=str(source),source_task=payload.task,allow_source_revision=True)
    if not re.fullmatch('[0-9a-f]{32}',payload.job_id):raise HTTPException(422,'Invalid specialized job UUID')
    try:
        if payload.task=='defect_gan':
            from backend.api.routes_defect_gan import _checkpoint
            from backend.engine.defect_gan import evaluate_defect_generator
            checkpoint=_checkpoint(request,payload.job_id)
            from backend.engine.defect_gan import load_defect_gan_manifest
            metadata=json.loads(checkpoint.with_name('model_meta.json').read_text(encoding='utf-8'))
            dataset=Path(payload.dataset_path or metadata.get('dataset_path') or source).resolve()
            if dataset!=source and not dataset.is_relative_to(Path(project['dataset_dir']).resolve()):raise ValueError('GAN evaluation inputs belong to another project')
            manifest=load_defect_gan_manifest(dataset)
            if dataset!=source and Path(manifest.get('source_dataset_path','')).resolve()!=source:raise ValueError('GAN evaluation inputs differ from active source')
            execute=lambda:evaluate_defect_generator(checkpoint,dataset,split='test',count=8,seed=0)
        else:
            from backend.engine.specialized_models import resolve_specialized_checkpoint
            checkpoint,metadata=resolve_specialized_checkpoint(project['models_dir'],payload.job_id,payload.task)
            state_file=checkpoint.parent/'job.json'
            if state_file.is_file() and (state_file.is_symlink() or json.loads(state_file.read_text(encoding='utf-8')).get('status')!='completed'):
                raise ValueError('Reevaluation requires a completed family model')
            recorded_source=metadata.get('source_dataset_path')
            binding=metadata.get('training_provenance') or {}
            if binding.get('version_dir'):
                version=Path(binding['version_dir'])
                if not version.resolve().is_relative_to(Path(project['project_dir']).resolve()/'versions') or version.is_symlink():
                    raise ValueError('Training version belongs to another project')
                version_manifest=json.loads((version/'manifest.json').read_text(encoding='utf-8'))
                from backend.api.routes_dataset_versions import _manifest_digest
                if (_manifest_digest(version_manifest)!=binding.get('manifest_sha256')
                        or version_manifest.get('project_id')!=project['id']):
                    raise ValueError('Training version lineage is corrupt')
                if recorded_source and Path(recorded_source).resolve()!=Path(version_manifest['source_dataset_dir']).resolve():
                    raise ValueError('Model source metadata conflicts with bound training version')
                recorded_source=version_manifest.get('source_dataset_dir')
            if recorded_source:
                if Path(recorded_source).resolve()!=source:raise ValueError('Evaluation model belongs to another source')
            else:
                # Legacy weights without source lineage retain the original exact
                # family provenance guard; a changed-label evaluation needs lineage.
                checkpoint,metadata=resolve_specialized_checkpoint(project['models_dir'],payload.job_id,payload.task,str(source))
            requested_dataset=Path(payload.dataset_path or metadata.get('dataset_path') or source)
            if requested_dataset.is_symlink():raise ValueError('Evaluation dataset cannot be linked')
            dataset=requested_dataset.resolve()
            if payload.task=='enhancement':
                if not dataset.is_relative_to(Path(project['dataset_dir']).resolve()) or dataset.is_symlink():raise ValueError('Enhancement pairs must be under the active project dataset directory')
                from backend.engine.enhancement import load_enhancement_manifest,evaluate_enhancement
                manifest=load_enhancement_manifest(dataset)
                if Path(manifest['provenance']['source_dataset_path']).resolve()!=source:raise ValueError('Enhancement pairs belong to another source')
                execute=lambda:evaluate_enhancement(checkpoint,dataset,split='test',device='cpu',allow_dataset_revision=True)
            elif payload.task=='rotation':
                from backend.api.routes_rotation import _owned_dataset
                from backend.engine.rotation import evaluate_rotation_checkpoint
                dataset=_owned_dataset(project,str(dataset)).root
                execute=lambda:evaluate_rotation_checkpoint(checkpoint,dataset,split='test',device='cpu',allow_dataset_revision=True)
            elif payload.task=='ocr':
                from backend.engine.prepared_family_datasets import resolve_family_dataset
                dataset=resolve_family_dataset(project,payload.task,dataset).root
                from backend.engine.ocr import evaluate_ocr_checkpoint
                execute=lambda:evaluate_ocr_checkpoint(checkpoint,dataset,split='test',device='cpu',allow_dataset_revision=True)
            else:
                from backend.engine.prepared_family_datasets import resolve_family_dataset
                dataset=resolve_family_dataset(project,payload.task,dataset).root
                from backend.engine.rotated_detection import evaluate_rotated_detector
                execute=lambda:evaluate_rotated_detector(checkpoint,dataset,split='test',device='cpu',allow_dataset_revision=True)
        fingerprint=_fingerprint(source);model_hash=_sha256(checkpoint)
        result=execute()
        if _fingerprint(source)!=fingerprint or _sha256(checkpoint)!=model_hash:raise ValueError('Evaluation inputs changed during execution')
        return archive_specialized_evaluation(project,checkpoint,source,result,task=payload.task,dataset_path=dataset)
    except (ValueError,OSError,RuntimeError,KeyError,TypeError) as exc:raise HTTPException(422,str(exc)) from exc

@router.get('/history')
def history(request:Request,source_dataset_path:str,task:HistoryTask,job_id:str|None=None,product:str|None=None,lot:str|None=None,labelset_id:str|None=None):
    project,source=history_scope(request,source_dataset_path,task)
    from backend.engine.project_labelsets import load_labelsets
    if labelset_id and labelset_id not in {row['id'] for row in load_labelsets(Path(project['project_dir']))['labelsets']}:raise HTTPException(404,'Label set not found in this project')
    try:items=[row for row in history_store(project).list(job_id,labelset_id) if row['binding']['source_dataset_path']==str(source) and row['result']['task']==task]
    except ValueError as exc:raise HTTPException(409,str(exc))
    if product is not None or lot is not None:
        items=[row for row in items if any((product is None or sample.get('product')==product) and (lot is None or sample.get('lot')==lot) for sample in row['result'].get('test_predictions',[]))]
    return {'items':items,'total':len(items)}

@router.get('/history/{evaluation_id}')
def read_history(evaluation_id:str,request:Request,source_dataset_path:str,task:HistoryTask):
    project,source=history_scope(request,source_dataset_path,task)
    try:row=history_store(project).get(evaluation_id)
    except FileNotFoundError:raise HTTPException(404,'Evaluation history not found')
    except ValueError as exc:raise HTTPException(409,str(exc))
    if row['binding']['source_dataset_path']!=str(source) or row['result']['task']!=task:raise HTTPException(404,'Evaluation belongs to another source/task')
    return row
