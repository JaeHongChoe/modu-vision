import {useRef,useState} from 'react';
import type {FlowchartPipeline, FlowModelTask} from '../../types';
import type {FlowModelCatalogItem} from '../../services/api';
import {WorkspaceDialog} from '../common/WorkspaceDialog';
import {AsyncAction} from '../common/AsyncAction';
import {mapFlowRecipe,recipeModelNodes,recipeNodeTasks,recipePorts,selectableModels,type OcrExpectation} from './flowRecipes';

const TASK_LABELS: Partial<Record<FlowModelTask,string>> = {
  classification:'분류', segmentation:'분할', anomaly:'이상 탐지', patch_classification:'패치 분류', ocr:'OCR',
  rotated_detection:'회전 검출', detection:'검출', rotation:'회전 보정',
};

export function FlowRecipeDialog({preview,models,onClose,onAdopt}:{preview:FlowchartPipeline;models:FlowModelCatalogItem[];onClose:()=>void;onAdopt:(pipeline:FlowchartPipeline)=>Promise<void>}) {
  const [draft,setDraft]=useState(preview);
  const [mapping,setMapping]=useState<Record<string,string>>({});
  const [policies,setPolicies]=useState<Record<string,string>>({});
  const [expectations,setExpectations]=useState<Record<string,OcrExpectation>>({});
  const [busy,setBusy]=useState(false),[error,setError]=useState('');
  const alive=useRef(true);
  const close=()=>{alive.current=false;onClose();};
  let missing='';try{mapFlowRecipe(draft,models,mapping,policies,expectations);}catch(cause){missing=cause instanceof Error?cause.message:String(cause);}
  const adopt=async()=>{if(busy)return;setBusy(true);setError('');try{await onAdopt(mapFlowRecipe(draft,models,mapping,policies,expectations));}catch(cause){if(alive.current)setError(cause instanceof Error?cause.message:String(cause));}finally{if(alive.current)setBusy(false);}};
  const setTask=(nodeId:string,task:FlowModelTask)=>{
    setDraft(previous=>({...previous,nodes:previous.nodes.map(row=>row.id===nodeId?{...row,data:{...row.data,task}}:row)}));
    setMapping(previous=>({...previous,[nodeId]:''}));setPolicies(previous=>({...previous,[nodeId]:''}));
  };
  return <WorkspaceDialog title="레시피 미리보기·모델 매핑" onClose={close} description="현재 그래프는 채택할 때까지 유지됩니다. 채택하면 전체 그래프를 새 초안으로 교체하며 저장·활성화는 따로 실행합니다.">
    <h3 className="text-lg font-semibold">{preview.name}</h3>
    <ol aria-label="레시피 노드" className="my-3 flex flex-wrap gap-2 text-xs text-slate-300">{preview.nodes.map(node=><li key={node.id} className="rounded border border-slate-700 p-2">{node.data.label} · {node.data.node_type}</li>)}</ol>
    <ul aria-label="레시피 포트 연결" className="mb-4 space-y-1 text-xs text-slate-400">{recipePorts(preview).map(port=><li key={port}>{port}</li>)}</ul>
    <p className="mb-4 text-xs text-slate-400">고정 ROI 좌표와 판정 규칙은 채택 후 편집에서 확인하세요.</p>
    <div className="space-y-4">{recipeModelNodes(draft).map(node=>{
      const selected=models.find(row=>row.job_id===mapping[node.id]);
      const tasks=recipeNodeTasks(node).filter(task=>task===node.data.task||models.some(model=>model.task===task));
      const choices=selectableModels(node,models);
      const expectation=expectations[node.id]||{kind:'expected_text',value:''};
      return <fieldset key={node.id} className="workspace-section space-y-3"><legend className="px-1 font-semibold">{node.data.label} · {TASK_LABELS[node.data.task as FlowModelTask]||node.data.task}</legend>
        {node.data.node_type==='inspection'&&<div className="workspace-field"><label htmlFor={`recipe-task-${node.id}`}>검사 작업</label>
          <select id={`recipe-task-${node.id}`} aria-label={`${node.data.label} 레시피 작업`} disabled={busy} value={node.data.task} onChange={event=>setTask(node.id,event.target.value as FlowModelTask)}>
            {tasks.map(task=><option key={task} value={task}>{TASK_LABELS[task]||task}</option>)}</select></div>}
        <div className="workspace-field"><label htmlFor={`recipe-model-${node.id}`}>완료 모델 (채택할 때 다시 검증)</label>
          <select id={`recipe-model-${node.id}`} aria-label={`${node.data.label} 레시피 모델`} disabled={busy} value={mapping[node.id]||''} onChange={event=>{setMapping(previous=>({...previous,[node.id]:event.target.value}));setPolicies(previous=>({...previous,[node.id]:''}));}}>
            <option value="">{choices.length?'이 노드가 실행할 수 있는 완료 모델 선택':'이 작업의 완료 모델 없음'}</option>{choices.map(model=><option key={model.job_id} value={model.job_id}>{model.label} · {model.job_id}</option>)}</select></div>
        {selected&&<p className="break-all text-xs text-slate-400">모델 {selected.job_id} · 클래스 {selected.class_names?.join(', ')||'기록 없음'}{selected.score_spec&&` · ${selected.score_spec.unit} · 임계값 ${selected.score_spec.threshold} · 보정 ${selected.score_spec.calibration_id}`}</p>}
        <div className="workspace-field"><label htmlFor={`recipe-classes-${node.id}`}>클래스 적용 범위</label>
          <select id={`recipe-classes-${node.id}`} aria-label={`${node.data.label} 클래스 적용 범위`} disabled={!selected||busy} value={policies[node.id]||''} onChange={event=>setPolicies(previous=>({...previous,[node.id]:event.target.value}))}>
            <option value="">명시적으로 선택</option><option value="all">전체 클래스 · 클래스별 규칙 없음</option></select>
          <p className="text-xs text-slate-400">이 레시피는 클래스 ID를 추측하지 않습니다. 클래스별 규칙은 선택 모델의 어휘를 확인하여 편집에서 추가하세요.</p></div>
        {node.data.task==='ocr'&&<div className="workspace-field"><label htmlFor={`recipe-ocr-${node.id}`}>OCR 판정 기준</label>
          <select aria-label={`${node.data.label} OCR 기준 종류`} disabled={busy} value={expectation.kind} onChange={event=>setExpectations(previous=>({...previous,[node.id]:{...expectation,kind:event.target.value as OcrExpectation['kind']}}))}>
            <option value="expected_text">기대 문자열</option><option value="regex">정규식</option></select>
          <input id={`recipe-ocr-${node.id}`} aria-label={`${node.data.label} OCR 기준`} disabled={busy} value={expectation.value} onChange={event=>setExpectations(previous=>({...previous,[node.id]:{...expectation,value:event.target.value}}))}/></div>}
      </fieldset>;})}</div>
    {error&&<p role="alert" className="mt-3 text-amber-200">{error}</p>}
    <div className="mt-5 flex gap-3"><button className="workspace-button" onClick={close}>취소 · 현재 그래프 유지</button><AsyncAction className="workspace-button workspace-button--primary" pending={busy} disabledReason={missing||undefined} onClick={()=>void adopt()}>매핑 확인·새 초안으로 채택</AsyncAction></div>
  </WorkspaceDialog>;
}
