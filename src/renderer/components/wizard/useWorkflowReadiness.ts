import { useEffect, useMemo, useRef, useState } from 'react';
import { api, getApiPersistenceIdentity, getProjectContext, request, subscribeProjectContext } from '../../services/api';
import { useProjectStore } from '../../stores/useProjectStore';
import { useComputeStore } from '../../stores/useComputeStore';
import { useDatasetStore } from '../../stores/useDatasetStore';
import { useTrainingStore } from '../../stores/useTrainingStore';
import { computeSampleVerdict, useEvaluationStore } from '../../stores/useEvaluationStore';
import { useAnnotationStore } from '../../stores/useAnnotationStore';
import { flowSemanticKey, useFlowchartStore } from '../../stores/useFlowchartStore';
import { getNextAction, type WorkflowEvidence, type WorkflowImpact } from './workflowReadiness';
import { subscribeWorkflowEvidence, workflowEvidenceRevision } from '../../stores/workflowEvidence';
import { inspectionProgressFor, publishWorkflowProgress, subscribeInspectionProgress } from '../../stores/workflowProgress';
import { stepsFromEvidence } from './stepProgress';
import { classStartBlocker, splitMissesLabels } from '../dataset/importChecklist';
import { wilsonInterval } from '../evaluation/sampleConfidence';
import { draftReviewScope, useDraftReviewStore } from '../../stores/useDraftReviewStore';
import { publishAssistContext } from '../../stores/assistContext';

export function useWorkflowReadiness() {
  const project=useProjectStore(s=>s.project),projectDir=useProjectStore(s=>s.projectDir),task=useProjectStore(s=>s.task),stage=useProjectStore(s=>s.activeStep),projectBusy=useProjectStore(s=>s.isProjectBusy);
  const [evidenceRevision,setEvidenceRevision]=useState(workflowEvidenceRevision);
  useEffect(()=>subscribeWorkflowEvidence(()=>setEvidenceRevision(workflowEvidenceRevision())),[]);
  const revision=useComputeStore(s=>s.transportRevision);
  const data=useDatasetStore(),training=useTrainingStore();
  const modelJobId=useEvaluationStore(s=>s.jobId);
  // A finished model evaluation is saved as evidence; re-read when its results arrive so step 4 can move on.
  const evaluatedSamples=useEvaluationStore(s=>s.testPredictions?.length ?? 0);
  // Step 4 waits for the evaluation screen's own read before calling the step done (see getNextAction).
  const evaluationLoading=useEvaluationStore(s=>s.isLoading),evaluationError=useEvaluationStore(s=>s.errorMessage);
  // Step 4's number line counts the loaded evaluation the way the evaluation screen does (same verdict, same inputs).
  const testPredictions=useEvaluationStore(s=>s.testPredictions),confidenceThreshold=useEvaluationStore(s=>s.confidenceThreshold);
  const overkillAnalysis=useEvaluationStore(s=>s.overkillAnalysis),classRoles=useEvaluationStore(s=>s.classSemantics?.roles);
  const evaluation=useMemo(()=>{
    if(!Array.isArray(testPredictions)||testPredictions.length===0)return null;
    let escapes=0,overkills=0,right=0;
    for(const item of testPredictions){
      const verdict=computeSampleVerdict(item,confidenceThreshold,overkillAnalysis,classRoles);
      if(verdict==='ESCAPE')escapes++;else if(verdict==='OVERKILL')overkills++;else if(verdict==='CORRECT_OK'||verdict==='CORRECT_NG')right++;
    }
    const samples=testPredictions.length;
    // Every image without an answer to compare (REVIEW): nothing was scored, so no accuracy rather than a false 0%.
    if(right+escapes+overkills===0)return {samples,escapes,overkills,accuracy:null,low:null,high:null};
    // The same 95% range the step-4 card prints beside its accuracy (accuracyNote: right images of all test images).
    const range=wilsonInterval(right,samples);
    return {samples,escapes,overkills,accuracy:Math.round(right/samples*100),low:range?Math.round(range.low*100):null,high:range?Math.round(range.high*100):null};
  },[testPredictions,confidenceThreshold,overkillAnalysis,classRoles]);
  // Step 3's line reads the training store the footer already follows; the clock is rounded to 10 s so the published
  // picture does not change on every telemetry tick. The training screen shows the same clock (total, else epoch).
  const etaSource=training.totalEtaSeconds||training.epochEtaSeconds;
  const etaSeconds=typeof etaSource==='number'&&Number.isFinite(etaSource)&&etaSource>0?Math.max(10,Math.round(etaSource/10)*10):null;
  const trainingRun={epoch:training.currentEpoch??0,totalEpochs:training.totalEpochs??0,etaSeconds,current:Boolean(training.isCurrentData)};
  const flow=useFlowchartStore();
  // Step 6's line comes from the batch inspection panel, which publishes the counts of the run it shows for this source.
  // Read during render for the current scope, so the first render after a project or source switch never shows the
  // previous source's counts; the state only re-renders on a publish (and once on subscribe, for one sent before it).
  const inspectionScope=JSON.stringify([data.folderPath,projectDir,task]);
  const [,setInspectionRevision]=useState(0);
  useEffect(()=>{const bump=()=>setInspectionRevision(value=>value+1);bump();return subscribeInspectionProgress(bump);},[]);
  const inspection=inspectionProgressFor(inspectionScope);
  const annotationDirty=useAnnotationStore(s=>s.isDirty);
  const draftScope=draftReviewScope({projectDir,project,task});
  const pendingDrafts=useDraftReviewStore(s=>s.scope===draftScope?s.pendingImages:0);
  // The queue's count of models that fit the current data; before it has read them, the training store's last run.
  const draftModels=useDraftReviewStore(s=>s.scope===draftScope?(s.models??0):0);
  const currentModels=draftModels||(training.isCurrentData&&training.status==='completed'&&training.jobId?1:0);
  // Per-image labels exist for detection and segmentation; a classification image always carries its folder class.
  const labels=data.hasSelectedFolder&&(task==='detection'||task==='segmentation')?{total:data.sourceImages||data.totalImages,unlabeled:data.unlabeledImages}:undefined;
  const annotationRevision=useAnnotationStore(s=>s.metadata?.revision),labelbookVersion=useAnnotationStore(s=>s.labelbookVersion);
  const [context,setContext]=useState(getProjectContext);
  useEffect(()=>subscribeProjectContext(setContext),[]);
  const sourceReady=Boolean(project?.source_dataset_dir && project.source_dataset_dir===data.folderPath && data.datasetKey===`${data.folderPath}\0${task}` && !data.importError);
  const apiScope=JSON.stringify([context,getApiPersistenceIdentity()]);
  const key=JSON.stringify([context,getApiPersistenceIdentity(),revision,projectDir,project?.id,task,project?.source_dataset_dir,project?.active_labelset_id,stage,data.totalImages,data.split,training.status,flow.flowIdentity.semantic_revision,annotationDirty,annotationRevision,labelbookVersion]);
  const live=useRef(key);live.current=key;
  // Step 5's line keeps the version the last report named while an edit re-checks (see flowVersionId below). The scope
  // holds the editor's base version, so a save or an activation never carries the version it replaced.
  const flowScope=JSON.stringify([apiScope,projectDir,project?.id,task,project?.source_dataset_dir,data.folderPath,flow.baseVersionId]);
  const lastFlowVersion=useRef<{scope:string;versionId?:string}|null>(null);
  // The editor differs from the active saved version: unsaved edits, or a draft the autosave kept that is not that
  // version (the store's own test, as for the activate button). A draft equal to the active version is not editing.
  const flowEditing=flow.pipelineDirty||flow.pipelineIsDraft;
  const sequence=useRef(0);
  const [refresh,setRefresh]=useState(0);
  const [receipt,setReceipt]=useState<{key:string;evidence:WorkflowEvidence;versionId?:string;flowMatchesSaved?:boolean}|null>(null);
  useEffect(()=>{
    const generation=++sequence.current;
    const current=()=>live.current===key&&sequence.current===generation&&apiScope===JSON.stringify([getProjectContext(),getApiPersistenceIdentity()]);
    if(stage===1 || !projectDir || !project || !sourceReady || !context || context.project_id!==project.id){setReceipt(null);return;}
    setReceipt({key,evidence:{status:'checking'}});
    if(stage===2){
      request<NonNullable<WorkflowEvidence['review']>>('/api/team-data/readiness').then(review=>{
        if(!current())return;
        if(typeof review.ready!=='boolean'||!Number.isFinite(review.counts?.eligible))throw new Error('준비도 근거 형식을 확인할 수 없습니다.');
        setReceipt({key,evidence:{status:'ready',review}});
      }).catch(cause=>{if(current())setReceipt({key,evidence:{status:'error',error:String(cause.message||cause)}});});
      return()=>{sequence.current++;};
    }
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
      lastFlowVersion.current={scope:flowScope,versionId};
      setReceipt({key,evidence:{status:'ready',review,impact},versionId,flowMatchesSaved});
      // Share what the report proves with the step bar, so its checkmarks survive a reload without another request.
      publishWorkflowProgress(JSON.stringify([project.id,project.source_dataset_dir,project.active_labelset_id||'default',task]),stepsFromEvidence(impact,task));
    }).catch(cause=>{if(current())setReceipt({key,evidence:{status:'error',error:String(cause.message||cause)}});});
    return()=>{sequence.current++;};
  },[key,sourceReady,refresh,data.isLoading,data.isSplitting,training.status,flow.flowIdentity.semantic_revision,flow.pipelineDirty,flow.pipelineIsDraft,flow.baseVersionId,evidenceRevision,modelJobId,evaluatedSamples]);
  const evidence=receipt?.key===key?receipt.evidence:{status:'error' as const,error:'현재 프로젝트 근거 확인 필요'};
  // Every flow edit (and the draft autosave after it) starts a re-check, so step 5's line would blank until it lands.
  // While the editor differs from the saved version, the version the last report named for this project, source and
  // base still names what is saved, and the line reads '편집 중' whatever the report says. Only the line takes the
  // carried id: advice past the evidence check needs a ready receipt.
  const readyReceipt=receipt?.key===key&&receipt.evidence.status==='ready'?receipt:null;
  const flowVersionId=readyReceipt?readyReceipt.versionId:flowEditing&&lastFlowVersion.current?.scope===flowScope?lastFlowVersion.current.versionId:null;
  const readiness=getNextAction({stage,projectReady:Boolean(projectDir&&project),sourceReady,totalImages:data.totalImages,sourceImages:data.sourceImages,train:data.split.train,val:data.split.val,training:trainingRun,evaluation,evaluationLoading,evaluationError,inspection,flowEditing,
    busy:projectBusy||data.isLoading||data.isSplitting||annotationDirty,task,splitState:data.splitState,populatedClasses:data.hasSelectedFolder?Object.values(data.classes||{}).filter(count=>count>0).length:undefined,
    classBlocker:data.hasSelectedFolder?classStartBlocker(data.classes||{},data.splitState==='saved'||data.splitState==='source_folders'?data.classSplitCounts??null:null,data.split):undefined,modelJobId:modelJobId || (training.isCurrentData?training.jobId:null),flowVersionId,
    flowDirty:flow.pipelineDirty,flowMatchesSaved:receipt?.key===key?receipt.flowMatchesSaved:false,trainingStatus:training.status,evidence,drafts:{pendingImages:pendingDrafts},
    splitMissesLabels:data.splitSupported!==false&&splitMissesLabels({totalImages:data.totalImages,split:data.split,splitState:data.splitState}),currentModels,labels});
  // The same picture the footer shows, published once per change for the footer's error list and a later assistant.
  const assist=JSON.stringify([draftScope,stage,readiness.status,readiness.missingInputs[0]??null,readiness.nextAction,readiness.progress??null]);
  useEffect(()=>{publishAssistContext({scope:draftScope,stage,status:readiness.status,blocker:readiness.missingInputs[0]??null,nextAction:{label:readiness.nextAction.label,step:readiness.nextAction.step,target:readiness.nextAction.target},progress:readiness.progress??null});},[assist]); // eslint-disable-line react-hooks/exhaustive-deps
  return {readiness,refresh:()=>setRefresh(value=>value+1)};
}
