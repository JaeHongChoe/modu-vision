import { useEffect, useRef, useState } from 'react';
import { api, getApiPersistenceIdentity, getProjectContext, request, subscribeProjectContext } from '../../services/api';
import { useProjectStore } from '../../stores/useProjectStore';
import { useComputeStore } from '../../stores/useComputeStore';
import { useDatasetStore } from '../../stores/useDatasetStore';
import { useTrainingStore } from '../../stores/useTrainingStore';
import { useEvaluationStore } from '../../stores/useEvaluationStore';
import { useAnnotationStore } from '../../stores/useAnnotationStore';
import { flowSemanticKey, useFlowchartStore } from '../../stores/useFlowchartStore';
import { getNextAction, type WorkflowEvidence, type WorkflowImpact } from './workflowReadiness';

export function useWorkflowReadiness() {
  const project=useProjectStore(s=>s.project),projectDir=useProjectStore(s=>s.projectDir),task=useProjectStore(s=>s.task),stage=useProjectStore(s=>s.activeStep),projectBusy=useProjectStore(s=>s.isProjectBusy);
  const revision=useComputeStore(s=>s.transportRevision);
  const data=useDatasetStore(),training=useTrainingStore();
  const modelJobId=useEvaluationStore(s=>s.jobId);
  const flow=useFlowchartStore();
  const annotationDirty=useAnnotationStore(s=>s.isDirty);
  const annotationRevision=useAnnotationStore(s=>s.metadata?.revision),labelbookVersion=useAnnotationStore(s=>s.labelbookVersion);
  const [context,setContext]=useState(getProjectContext);
  useEffect(()=>subscribeProjectContext(setContext),[]);
  const sourceReady=Boolean(project?.source_dataset_dir && project.source_dataset_dir===data.folderPath && data.datasetKey===`${data.folderPath}\0${task}` && !data.importError);
  const apiScope=JSON.stringify([context,getApiPersistenceIdentity()]);
  const key=JSON.stringify([context,getApiPersistenceIdentity(),revision,projectDir,project?.id,task,project?.source_dataset_dir,project?.active_labelset_id,stage,data.totalImages,data.split,training.status,flow.flowIdentity.semantic_revision,annotationDirty,annotationRevision,labelbookVersion]);
  const live=useRef(key);live.current=key;
  const sequence=useRef(0);
  const [refresh,setRefresh]=useState(0);
  const [receipt,setReceipt]=useState<{key:string;evidence:WorkflowEvidence;versionId?:string;flowMatchesSaved?:boolean}|null>(null);
  useEffect(()=>{
    const generation=++sequence.current;
    const current=()=>live.current===key&&sequence.current===generation&&apiScope===JSON.stringify([getProjectContext(),getApiPersistenceIdentity()]);
    if(stage===1 || !projectDir || !project || !sourceReady || !context || context.project_id!==project.id){setReceipt(null);return;}
    setReceipt({key,evidence:{status:'checking'}});
    Promise.all([
      request<NonNullable<WorkflowEvidence['review']>>('/api/team-data/readiness'),
      request<WorkflowImpact>('/api/provenance/impact'),
      api.flowchart.listPipelines(data.folderPath),
    ]).then(async ([review,impact,versions])=>{
      if(!current())return;
      if(impact.project_id!==project.id || impact.source_dataset_path!==project.source_dataset_dir || impact.labelset_id!==(project.active_labelset_id||'default'))throw new Error('응답의 프로젝트·원본·라벨셋이 현재 입력과 다릅니다.');
      if(!Array.isArray(impact.models)||!Array.isArray(impact.flows)||!Array.isArray(impact.model_evaluations)||typeof review.ready!=='boolean'||!Number.isFinite(review.counts?.eligible)||!Array.isArray(versions.pipelines))throw new Error('준비도 근거 형식을 확인할 수 없습니다.');
      const versionId=versions.pipelines.find(row=>row.is_active)?.version_id;
      const saved=stage>=5&&versionId&&flow.pipeline?await api.flowchart.getPipelineVersion(versionId):null;
      if(!current())return;
      const flowMatchesSaved=!flow.pipeline || Boolean(saved&&flowSemanticKey(saved)===flowSemanticKey(flow.pipeline));
      setReceipt({key,evidence:{status:'ready',review,impact},versionId,flowMatchesSaved});
    }).catch(cause=>{if(current())setReceipt({key,evidence:{status:'error',error:String(cause.message||cause)}});});
    return()=>{sequence.current++;};
  },[key,sourceReady,refresh,data.isLoading,data.isSplitting,training.status,flow.flowIdentity.semantic_revision,flow.pipelineDirty]);
  const evidence=receipt?.key===key?receipt.evidence:{status:'error' as const,error:'현재 프로젝트 근거 확인 필요'};
  return {readiness:getNextAction({stage,projectReady:Boolean(projectDir&&project),sourceReady,totalImages:data.totalImages,train:data.split.train,val:data.split.val,
    busy:projectBusy||data.isLoading||data.isSplitting||annotationDirty,task,modelJobId:modelJobId || (training.isCurrentData?training.jobId:null),flowVersionId:receipt?.key===key?receipt.versionId:null,
    flowDirty:flow.pipelineDirty,flowMatchesSaved:receipt?.key===key?receipt.flowMatchesSaved:false,trainingStatus:training.status,evidence}),refresh:()=>setRefresh(value=>value+1)};
}
