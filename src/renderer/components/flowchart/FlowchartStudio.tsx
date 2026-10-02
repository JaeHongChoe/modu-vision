/**
 * src/renderer/components/flowchart/FlowchartStudio.tsx
 * Stage 5: editable inspection graph with model fan-out and verdict branches.
 */

import React, { useEffect, useLayoutEffect, useRef, useState } from 'react';
import {
  AlertTriangle,
  ChevronLeft,
  ChevronRight,
  GitFork,
  Image as ImageIcon,
  Link2,
  ListTree,
  Plus,
  Play,
  RotateCcw,
  Redo2,
  Save,
  Sliders,
  Trash2,
  Undo2,
  ZoomIn,
  ZoomOut,
} from 'lucide-react';
import { flowSemanticKey, isExecutionResultCurrent, useFlowchartStore } from '../../stores/useFlowchartStore';
import { useDatasetStore } from '../../stores/useDatasetStore';
import { useEvaluationStore } from '../../stores/useEvaluationStore';
import { useProjectStore } from '../../stores/useProjectStore';
import { useTrainingStore } from '../../stores/useTrainingStore';
import { useComputeStore } from '../../stores/useComputeStore';
import { api, resolveApiUrl,getApiPersistenceIdentity, getProjectContext, getProjectContextGeneration, type FlowModelCatalogItem, type SavedFlowVersion } from '../../services/api';
import {readModelFlowHandoff,clearModelFlowHandoff,bindModelToFlow,compatibleModelNode,modelScoreBinding,thresholdUpdate} from './modelFlowHandoff';
import type { FlowNode, FlowchartPipeline, FlowModelTask } from '../../types';
import {ClassRulesEditor,FlowResourcesEditor,MeasurementEditor,OCRRulesEditor} from './FlowGeometryEditors';
import { CustomNode } from './CustomNode';
import { DAGCircuitOverlay } from './DAGCircuitOverlay';
import { ImagePickerModal } from './ImagePickerModal';
import { ImageRoiEditor } from './ImageRoiEditor';
import { FlowNodeDebugger } from './FlowNodeDebugger';
import { FlowWorkspacePanel } from './FlowWorkspacePanel';
import { FlowEvaluationPanel } from './FlowEvaluationPanel';
import {nodeClassChoices} from './flowWorkspace';
import { WorkflowImpactPanel } from '../common/WorkflowImpactPanel';
import {FlowEditorWorkspace, flowWorkspaceIdentity, type FlowWorkspaceTab} from './FlowEditorWorkspace';
import {FlowRecipeDialog} from './FlowRecipeDialog';
import {createFlowRecipe, FLOW_RECIPES, type FlowRecipeKind} from './flowRecipes';
import type {FlowEvaluation} from '../../services/flowEvaluation';
import {flowPackageExport, type FlowApprovalPrerequisites} from '../../services/flowPackageExport';
import {FlowDraftControls,flowDraftStatus} from './FlowDraftControls';
import { IntermediateCropDrawer } from './IntermediateCropDrawer';
import { CropDetailModal } from './CropDetailModal';
import { computeFlowchartViewport, readableFlowScale } from './flowchartViewport';
import { getFlowchartModelReferences, getFlowchartModelTask, pipelineMatchesTask, recoverThenLoadFlowchart, singleModelAutoBinding } from './flowchartStartup';
import { flowRecipeLabel, flowRunSourceLabel } from './flowHandoff';
import { flowExecutionOptions, type FlowExecutionChoice } from './flowExecution';
import { connectFlowNodes, decisionRulePatch, flowIssuesByTarget, layoutFlowchart, locateFlowIssue, removeFlowNode, shouldShowThreshold, updateFlowEdgeBranch, updateFlowEdgePayload, validateFlowchartGraph, type FlowPortPayload } from './flowchartGraph';

const verifyModelReferences = async (
  sourceFolder: string,
  models: Array<{ job_id: string; task: FlowModelTask }>,
) => {
  await api.flowchart.verifyModels({ source_dataset_path: sourceFolder, models });
};

type FixedRoiField = 'x' | 'y' | 'width' | 'height';

const FixedRoiCoordinateInput: React.FC<{
  field: FixedRoiField;
  label: string;
  value: number;
  min: number;
  onCommit: (field: FixedRoiField, value: string) => void;
}> = ({ field, label, value, min, onCommit }) => {
  const [draft, setDraft] = useState(String(value));
  const [editing, setEditing] = useState(false);
  const cancelCommitRef = useRef(false);

  useEffect(() => {
    if (!editing) setDraft(String(value));
  }, [editing, value]);

  const commit = () => {
    if (cancelCommitRef.current) {
      cancelCommitRef.current = false;
      setDraft(String(value));
      setEditing(false);
      return;
    }
    const parsed = Number(draft);
    if (draft.trim() && Number.isFinite(parsed)) {
      onCommit(field, String(Math.max(min, Math.trunc(parsed))));
    } else {
      setDraft(String(value));
    }
    setEditing(false);
  };

  return <label className="text-xs text-slate-400">
    {label} (px)
    <input type="number" min={min} step="1" value={draft}
      onFocus={() => setEditing(true)}
      onChange={(event) => setDraft(event.target.value)}
      onBlur={commit}
      onKeyDown={(event) => {
        if (event.key === 'Enter') event.currentTarget.blur();
        if (event.key === 'Escape') {
          cancelCommitRef.current = true;
          event.currentTarget.blur();
        }
      }}
      className="mt-1 w-full rounded border border-[#33465C] bg-[#1A212E] px-2 py-1.5 text-xs text-[#F8FAFC] tabular-nums focus:border-sky-400 outline-none" />
  </label>;
};


/** Identity and thumbnail of an image inside the project source, as the dataset API names it. */
function datasetImageReference(imagePath: string) {
  const fileName = imagePath.split(/[\\/]/).pop() || imagePath;
  const imageId = fileName.replace(/\.[^.]+$/, '');
  return { fileName, imageId,
    thumbnailUrl: `/api/dataset/thumbnail/${encodeURIComponent(imageId)}?file_path=${encodeURIComponent(imagePath)}&size=256` };
}

export const FlowchartStudio: React.FC = () => {
  const { language, task, setStep } = useProjectStore();
  const { jobId: trainedJobId, status: trainingStatus, isCurrentData } = useTrainingStore();
  const evaluatedJobId = useEvaluationStore((state) => state.jobId);
  const allowLatestRecovery = useEvaluationStore((state) => state.allowLatestRecovery);
  const loadEvaluation = useEvaluationStore((state) => state.loadEvaluation);
  const { folderPath, datasetKey, hasSelectedFolder, isLoading: datasetIsLoading, importError } = useDatasetStore();
  const completedCurrentJobId = isCurrentData && trainingStatus === 'completed' ? trainedJobId : null;
  const {
    pipeline,
    pipelineDirty,
    pipelineIsDraft,
    persistedDraftHash,
    contextRevision,
    executionResult,
    executionIdentity,
    lastRunSource,
    isLoading,
    isSaving,
    isRunning,
    activeRunningNodeId,
    selectedNodeId,
    saveMessage,
    errorMessage,
    selectedImage,
    isImagePickerOpen,
    inspectedCrop,
    loadPipeline,
    loadPipelineVersion,
    loadSingleSegmentationTemplate,
    savePipeline,
    runPipeline,
    selectNode,
    updateNodeData,
    replacePipeline,
    moveNode,
    beginHistoryGroup,
    endHistoryGroup,
    undo,
    redo,
    canUndo,
    canRedo,
    setImagePickerOpen,
    setInspectedCrop,
    clearError,
  } = useFlowchartStore();
  // Results stay visible after edits; they are current only for the semantics they ran with.
  const resultIsCurrent = isExecutionResultCurrent({ executionResult, executionIdentity, pipeline });
  // The canvas pairs evidence with the current rules, so it only shows a current run.
  const canvasResult = resultIsCurrent ? executionResult : null;

  const [activeTab, setActiveTab] = useState<FlowWorkspaceTab>('edit');
  const { profiles: computeProfiles, selectedProfileId, isLoaded: isComputeLoaded,
    load: loadCompute, loadError: computeLoadError } = useComputeStore();
  const executionChoiceOverride = useFlowchartStore(state => state.executionChoiceOverride);
  const setExecutionChoiceOverride = useFlowchartStore(state => state.setExecutionChoice);
  const executionChoice = executionChoiceOverride || (selectedProfileId ? 'selected_compute' : 'local_cpu');
  const executionProfile = computeProfiles.find((profile) => profile.id === selectedProfileId);
  const executionReady = (isComputeLoaded || executionChoiceOverride !== null)
    && (executionChoice !== 'selected_compute' || Boolean(executionProfile));
  useEffect(() => { void loadCompute().catch(() => {}); }, [loadCompute]);
  const [modelCheck, setModelCheck] = useState<{ status: 'checking' | 'ready' | 'blocked'; reason?: string }>({ status: 'checking' });
  const [verificationRetry, setVerificationRetry] = useState(0);
  const [isVerifyingAction, setIsVerifyingAction] = useState(false);
  const [actionValidationError, setActionValidationError] = useState<string | null>(null);
  const [zoomScale, setZoomScale] = useState<number | null>(null);
  const [connectionSourceId, setConnectionSourceId] = useState<string | null>(null);
  // The payloads of the output port the connection started from: the wrong port is refused when the target is clicked.
  const [connectionPayloads, setConnectionPayloads] = useState<FlowPortPayload[] | undefined>(undefined);
  const [selectedEdgeId, setSelectedEdgeId] = useState<string | null>(null);
  const [editorError, setEditorError] = useState<string | null>(null);
  const [modelCatalog, setModelCatalog] = useState<FlowModelCatalogItem[]>([]);
  const [modelCatalogLoading, setModelCatalogLoading] = useState(false);
  const [savedVersions, setSavedVersions] = useState<SavedFlowVersion[]>([]);
  const [selectedVersionId, setSelectedVersionId] = useState('');
  const [modelHandoffDismissed,setModelHandoffDismissed]=useState('');
  const [handoffNode,setHandoffNode]=useState('');
  const handoffState={...useProjectStore.getState(),...useComputeStore.getState(),apiTransportIdentity:getApiPersistenceIdentity()};
  const modelHandoff=readModelFlowHandoff(localStorage,handoffState);
  const offeredModel=modelHandoff&&modelHandoffDismissed!==`${modelHandoff.scope}:${modelHandoff.selectedAt}`
    ?modelCatalog.find(row=>row.job_id===modelHandoff.modelId&&row.task===modelHandoff.family):null;
  const dismissModelHandoff=()=>{if(modelHandoff){clearModelFlowHandoff(localStorage,handoffState);setModelHandoffDismissed(`${modelHandoff.scope}:${modelHandoff.selectedAt}`);}};
  const applyModelHandoff=async()=>{
    if(!pipeline||!offeredModel||!modelHandoff||isRunning||isSaving||isVerifyingAction)return;
    const captured=modelHandoff.scope;setIsVerifyingAction(true);
    try{await verifyModelReferences(folderPath,[{job_id:offeredModel.job_id,task:offeredModel.task}]);
      const current=readModelFlowHandoff(localStorage,{...useProjectStore.getState(),...useComputeStore.getState(),apiTransportIdentity:getApiPersistenceIdentity()});
      if(current?.scope!==captured||current.modelId!==offeredModel.job_id)throw new Error('프로젝트 또는 선택 모델이 바뀌었습니다. 다시 선택하세요.');
      const graph=useFlowchartStore.getState().pipeline;if(!graph)throw new Error('플로우를 먼저 여세요.');
      const result=bindModelToFlow(graph,offeredModel,handoffNode||undefined);replacePipeline(result.pipeline);selectNode(result.nodeId);setActiveTab('edit');dismissModelHandoff();
    }catch(cause){const current=readModelFlowHandoff(localStorage,{...useProjectStore.getState(),...useComputeStore.getState(),apiTransportIdentity:getApiPersistenceIdentity()});if(current?.scope===captured)setEditorError(cause instanceof Error?cause.message:String(cause));}
    finally{setIsVerifyingAction(false);}
  };
  const [isRoiEditing,setIsRoiEditing]=useState(false);
  const [dragViewport, setDragViewport] = useState<ReturnType<typeof computeFlowchartViewport> | null>(null);
  const canvasRef = useRef<HTMLDivElement>(null);
  const [canvasSize, setCanvasSize] = useState({ width: 0, height: 0 });
  const dragRef = useRef<{
    nodeId: string; startX: number; startY: number; x: number; y: number; scale: number;
  } | null>(null);

  useLayoutEffect(() => {
    if (activeTab !== 'edit' || !canvasRef.current) return;
    const canvas = canvasRef.current;
    const measure = () => {
      const rect = canvas.getBoundingClientRect();
      setCanvasSize((previous) =>
        previous.width === rect.width && previous.height === rect.height
          ? previous
          : { width: rect.width, height: rect.height }
      );
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(canvas);
    return () => observer.disconnect();
  }, [activeTab]);

  useEffect(() => {
    if (!folderPath || !hasSelectedFolder) { setModelCatalog([]); setSavedVersions([]); return; }
    let active = true;
    setModelCatalogLoading(true);
    Promise.all([
      api.flowchart.modelCatalog(folderPath),
      api.flowchart.listPipelines(folderPath),
    ]).then(([catalog, versions]) => {
      if (!active) return;
      setModelCatalog(catalog.models);
      setSavedVersions(versions.pipelines);
      if (!useFlowchartStore.getState().pipelineDirty && !useFlowchartStore.getState().pipelineIsDraft) {
        setSelectedVersionId(versions.pipelines.find((version) => version.is_active)?.version_id || '');
      }
    }).catch((cause) => {
      if (active) setEditorError(cause instanceof Error ? cause.message : String(cause));
    }).finally(() => { if (active) setModelCatalogLoading(false); });
    return () => { active = false; };
  }, [folderPath, hasSelectedFolder, contextRevision, verificationRetry]);

  useEffect(() => {
    let cancelled = false;
    const openFlow = async () => {
      setModelCheck({ status: 'checking' });
      const sourceKey = datasetKey;
      const isCurrent = () => !cancelled && useDatasetStore.getState().datasetKey === sourceKey
        && useProjectStore.getState().task === task;
      const result = await recoverThenLoadFlowchart({
        folderPath, task, datasetKey, hasSelectedFolder, datasetIsLoading, importError,
        allowLatestRecovery, completedCurrentJobId,
        getVerifiedJobId: () => useEvaluationStore.getState().jobId,
        loadEvaluation,
        loadSavedPipeline: () => {
          const current = useFlowchartStore.getState();
          return current.pipeline && current.pipelineDirty && pipelineMatchesTask(current.pipeline, task)
            ? Promise.resolve(current.pipeline)
            : loadPipeline(true, task, folderPath);
        },
        verifyModels: verifyModelReferences,
        isCurrent,
      });
      if (!isCurrent() || result.status === 'cancelled') return;
      if (result.status === 'waiting') return;
      if (result.status === 'blocked') {
        setModelCheck({ status: 'blocked', reason: result.reason });
        if (!useFlowchartStore.getState().pipelineIsDraft && !pipelineMatchesTask(useFlowchartStore.getState().pipeline, task)) {
          await loadSingleSegmentationTemplate(undefined, task);
        }
        return;
      }
      const binding = result.verifiedJobId && !useFlowchartStore.getState().pipelineIsDraft
        ? singleModelAutoBinding(result.pipeline, task, result.verifiedJobId)
        : null;
      if (binding) {
        const catalog = await api.flowchart.modelCatalog(folderPath!);
        if (!isCurrent()) return;
        const model = catalog.models.find(row=>row.job_id===binding.modelJobId);
        updateNodeData(binding.nodeId, model ? modelScoreBinding(model) : {model_job_id:binding.modelJobId,score_spec:undefined,threshold:.5});
      }
      setZoomScale(null);
      setModelCheck({ status: 'ready' });
    };
    openFlow().catch(() => {
      if (!cancelled) setModelCheck({ status: 'blocked', reason: 'saved_flow_unavailable' });
    });
    return () => { cancelled = true; };
  }, [loadPipeline, loadSingleSegmentationTemplate, loadEvaluation, updateNodeData,
    folderPath, datasetKey, hasSelectedFolder, datasetIsLoading, importError,
    allowLatestRecovery, completedCurrentJobId, evaluatedJobId, contextRevision, verificationRetry, task]);

  useEffect(() => { setActionValidationError(null); }, [pipeline]);

  useEffect(() => {
    if (activeTab !== 'edit') return;
    const onHistoryKeyDown = (event: KeyboardEvent) => {
      if (isLoading || isSaving || isRunning || isVerifyingAction || !(event.metaKey || event.ctrlKey) || event.altKey) return;
      const target = event.target as HTMLElement | null;
      if (target?.closest('input, textarea, select, [contenteditable="true"]')) return;
      const key = event.key.toLowerCase();
      const action = key === 'z' ? (event.shiftKey ? 'redo' : 'undo')
        : key === 'y' && event.ctrlKey && !event.metaKey ? 'redo' : null;
      if (action === 'undo' ? !canUndo : action === 'redo' ? !canRedo : true) return;
      event.preventDefault();
      if (action === 'undo') undo(); else redo();
      setSelectedEdgeId(null);
      setConnectionSourceId(null);
      setEditorError(null);
    };
    window.addEventListener('keydown', onHistoryKeyDown);
    return () => window.removeEventListener('keydown', onHistoryKeyDown);
  }, [activeTab, isLoading, isSaving, isRunning, isVerifyingAction, canUndo, canRedo, undo, redo]);

  const applyHistoryAction = (action: 'undo' | 'redo') => {
    if (action === 'undo') undo(); else redo();
    setSelectedEdgeId(null);
    setConnectionSourceId(null);
    setEditorError(null);
  };

  const hasDetectionNode = pipeline?.nodes.some((node) => node.data.node_type === 'detection_crop') ?? false;
  const missingDetectionModel = pipeline?.nodes.some((node) =>
    node.data.node_type === 'detection_crop' && !node.data.model_job_id
  ) ?? false;
  const missingInspectionModel = pipeline?.nodes.some((node) =>
    node.data.node_type === 'inspection' && !node.data.model_job_id
  ) ?? false;
  const needsModel = !pipeline || missingDetectionModel || missingInspectionModel || pipeline.nodes.some(node=>getFlowchartModelTask(node)!==null&&!node.data.model_job_id);
  const graphError = pipeline ? validateFlowchartGraph(pipeline) : null;
  const graphIssue = pipeline ? locateFlowIssue(pipeline, graphError) : null;
  // Every node's and connection's own problem, marked where it is (the banner keeps the first one).
  const flowIssues = pipeline ? flowIssuesByTarget(pipeline) : null;
  const canVerifyGraph = modelCheck.status !== 'checking' && hasSelectedFolder && !datasetIsLoading
    && !importError && datasetKey === `${folderPath}\0${task}`;

  const verifyCurrentPipeline = async (current: FlowchartPipeline, sourceFolder: string) => {
    const modelNodes = current.nodes.filter((node) => getFlowchartModelTask(node) !== null);
    const models = getFlowchartModelReferences(current);
    if (modelNodes.length === 0 || models.length !== modelNodes.length) {
      throw new Error('검출·검사 노드마다 현재 데이터로 학습한 모델 작업 ID를 지정하세요.');
    }
    await verifyModelReferences(sourceFolder, models);
  };

  const handleSave = async () => {
    if (!canVerifyGraph || !pipeline || isVerifyingAction || needsModel) return;
    if (graphError) { setActionValidationError(graphError); return; }
    const currentPipeline = pipeline;
    const sourceKey = datasetKey;
    setIsVerifyingAction(true);
    setActionValidationError(null);
    try {
      await verifyCurrentPipeline(currentPipeline, folderPath);
      if (useDatasetStore.getState().datasetKey !== sourceKey || useFlowchartStore.getState().pipeline !== currentPipeline) return;
      const inspectionTasks = new Set(currentPipeline.nodes.filter((node) => node.data.node_type === 'inspection').map((node) => node.data.task));
      const recipeTask = inspectionTasks.size > 1 ? 'mixed'
        : inspectionTasks.size === 1 ? [...inspectionTasks][0] as FlowModelTask : 'detection';
      await savePipeline(undefined, recipeTask, folderPath);
      const versions = await api.flowchart.listPipelines(folderPath);
      if (useDatasetStore.getState().datasetKey === sourceKey) {
        setSavedVersions(versions.pipelines);
        setSelectedVersionId(versions.pipelines.find((version) => version.is_active)?.version_id || '');
      }
    } catch (error) {
      setActionValidationError(error instanceof Error ? error.message : '모델의 데이터 출처를 확인할 수 없습니다.');
    } finally {
      setIsVerifyingAction(false);
    }
  };

  const handleRun = async () => {
    if (!canVerifyGraph || !pipeline || isVerifyingAction || needsModel) return;
    if (!executionReady) { setActionValidationError(computeLoadError || '플로우 실행 위치를 확인하세요.'); return; }
    if (graphError) { setActionValidationError(graphError); return; }
    const currentPipeline = pipeline;
    const sourceKey = datasetKey;
    setIsVerifyingAction(true);
    setActionValidationError(null);
    try {
      const execution = flowExecutionOptions(executionChoice, selectedProfileId);
      await verifyCurrentPipeline(currentPipeline, folderPath);
      if (useDatasetStore.getState().datasetKey !== sourceKey || useFlowchartStore.getState().pipeline !== currentPipeline) return;
      const activeVersionId = !pipelineDirty && !useFlowchartStore.getState().pipelineIsDraft
        ? savedVersions.find((version) => version.version_id === selectedVersionId && version.is_active)?.version_id || null
        : null;
      if (await runPipeline(undefined, undefined, { savedVersionId: activeVersionId, ...execution })) setActiveTab('test');
    } catch (error) {
      setActionValidationError(error instanceof Error ? error.message : '모델의 데이터 출처를 확인할 수 없습니다.');
    } finally {
      setIsVerifyingAction(false);
    }
  };

  const handleRunToNode = async () => {
    if (!pipeline || !selectedNodeId || !selectedImage || isRunning || graphError) return;
    setActionValidationError(null);
    if (!executionReady) {setActionValidationError('실행 컴퓨터 또는 서버를 먼저 선택하세요.');return;}
    const execution = executionChoice==='model_compute' ? {executionTarget:'local' as const,device:'cpu' as const} : flowExecutionOptions(executionChoice,selectedProfileId);
    const ok = await runPipeline(undefined,undefined,{savedVersionId:null,...execution,stopNodeId:selectedNodeId});
    if (ok) setActiveTab('edit');
  };

  const recipeScope = () => JSON.stringify([getProjectContextGeneration(),getProjectContext(),getApiPersistenceIdentity(),
    useProjectStore.getState().project?.id,useProjectStore.getState().projectDir,useProjectStore.getState().project?.active_labelset_id,
    useDatasetStore.getState().datasetKey,useProjectStore.getState().task]);
  const scopeKey=recipeScope();
  const [recipe,setRecipe]=useState<{preview:FlowchartPipeline;scope:string;graph:FlowchartPipeline|null;token:number}|null>(null);
  const recipeToken=useRef(0);
  const [evaluationReceipt,setEvaluationReceipt]=useState<FlowEvaluation|null>(null);
  const [approval,setApproval]=useState<{scope:string;version:string;semantic:string;receipt:FlowApprovalPrerequisites}|null>(null);
  const [approvalBusy,setApprovalBusy]=useState(false);
  const [approvalError,setApprovalError]=useState('');
  const approvalRequest=useRef(0);
  const semanticKey=flowSemanticKey(pipeline);
  useEffect(()=>()=>{recipeToken.current++;approvalRequest.current++;},[]);
  useEffect(()=>{setActiveTab('edit');recipeToken.current++;setRecipe(null);setEvaluationReceipt(null);},[scopeKey]);
  useEffect(()=>{approvalRequest.current++;setApproval(null);setApprovalError('');setApprovalBusy(false);},[scopeKey,selectedVersionId,semanticKey]);
  const closeRecipe=()=>{recipeToken.current++;setRecipe(null);};
  const openRecipe=(kind:FlowRecipeKind)=>{
    const live=useFlowchartStore.getState();if(live.isRunning||live.isSaving||live.isLoading||useProjectStore.getState().isProjectBusy)return;
    if(!live.pipeline){setEditorError('현재 플로우를 불러온 뒤 레시피를 적용하세요.');return;}
    setRecipe({preview:createFlowRecipe(kind,task),scope:recipeScope(),graph:live.pipeline,token:++recipeToken.current});
  };
  const adoptRecipe=async(next:FlowchartPipeline)=>{
    if(!recipe)return;const captured=recipe;
    const current=()=>captured.token===recipeToken.current&&recipeScope()===captured.scope&&useFlowchartStore.getState().pipeline===captured.graph;
    const idle=()=>{const live=useFlowchartStore.getState();return !live.isRunning&&!live.isSaving&&!live.isLoading&&!useProjectStore.getState().isProjectBusy;};
    if(!current()||!idle())throw new Error('프로젝트 또는 그래프가 바뀌었습니다. 레시피를 다시 여세요.');
    if(!captured.graph)throw new Error('현재 플로우를 불러온 뒤 레시피를 적용하세요.');
    await verifyModelReferences(folderPath,getFlowchartModelReferences(next));
    if(!current()||!idle())throw new Error('프로젝트 또는 그래프가 바뀌었습니다. 레시피를 다시 여세요.');
    replacePipeline(next);selectNode(next.nodes.find(node=>node.data.node_type==='fixed_roi')?.id||next.nodes.find(node=>node.data.model_job_id)?.id||null);
    setSelectedVersionId('');setSelectedEdgeId(null);setZoomScale(null);setActionValidationError(null);setActiveTab('edit');closeRecipe();
  };
  const handleSingleModel=()=>openRecipe('single');
  const handleDetectorRoi=()=>openRecipe('detector');
  const handleFixedRoi=()=>openRecipe('fixed');
  const handleExampleTemplate=async(kind:'chain'|'conditional')=>{
    const live=useFlowchartStore.getState();if(live.isRunning||live.isSaving||live.isLoading||useProjectStore.getState().isProjectBusy)return;
    const captured=recipeScope(),graph=live.pipeline,token=++recipeToken.current;
    try{const preview=await (kind==='chain'?api.flowchart.getFiveModelChainTemplate():api.flowchart.getConditionalInspectionTemplate());
      if(token!==recipeToken.current||captured!==recipeScope()||graph!==useFlowchartStore.getState().pipeline)return;
      setRecipe({preview,scope:captured,graph,token});
    }catch(cause){if(token===recipeToken.current&&captured===recipeScope())setEditorError(cause instanceof Error?cause.message:String(cause));}
  };
  const checkApproval=async()=>{
    const version=savedVersions.find(row=>row.version_id===selectedVersionId);
    if(!version||pipelineDirty||pipelineIsDraft||approvalBusy)return;
    const captured=recipeScope(),graph=flowSemanticKey(useFlowchartStore.getState().pipeline),requestId=++approvalRequest.current;
    setApprovalBusy(true);setApprovalError('');
    try{const receipt=await flowPackageExport.prerequisites({source_dataset_path:folderPath,recipe_task:version.recipe_task,version_id:version.version_id});
      if(requestId===approvalRequest.current&&captured===recipeScope()&&graph===flowSemanticKey(useFlowchartStore.getState().pipeline))setApproval({scope:captured,version:version.version_id,semantic:graph,receipt});
    }catch(cause){if(requestId===approvalRequest.current&&captured===recipeScope())setApprovalError(cause instanceof Error?cause.message:String(cause));}
    finally{if(requestId===approvalRequest.current&&captured===recipeScope())setApprovalBusy(false);}
  };
  const selectedSaved=savedVersions.find(row=>row.version_id===selectedVersionId);
  const savedIsExact=Boolean(selectedSaved&&!pipelineDirty&&!pipelineIsDraft);
  const currentApproval=approval?.scope===scopeKey&&approval.version===selectedVersionId&&approval.semantic===semanticKey&&savedIsExact?approval.receipt:null;
  const identity=flowWorkspaceIdentity({
    revision:useFlowchartStore.getState().flowIdentity?.semantic_revision||0,
    draft:flowDraftStatus(pipelineDirty,persistedDraftHash,!pipelineIsDraft&&Boolean(selectedSaved?.is_active),Boolean(selectedVersionId)),
    dirty:pipelineDirty, isDraft:pipelineIsDraft, savedVersions, selectedVersionId,
    modelNodes:pipeline?.nodes.filter(node=>node.data.model_job_id).map(node=>({id:node.id,job:node.data.model_job_id!,task:String(node.data.task||node.data.params?.operation||'')}))||[],
    resultLabel:`검사 결과${executionResult?` (${executionResult.roi_count} ROI)`: ''}${executionResult&&!resultIsCurrent?' · 이전 버전':''}`,
    lastImage:executionResult?.image_path||executionResult?.image_id||undefined, nextImage:selectedImage?.imagePath,
    inspection:executionResult?`${resultIsCurrent?'현재 규칙':'이전 버전'} · ${executionResult.image_path||executionResult.image_id||'이미지 식별자 미기록'} · ${lastRunSource?.kind||'출처 미기록'} ${lastRunSource?.versionId||''} · 그래프 ${executionResult.graph_sha256||'미기록'} · ${executionResult.execution_target||'대상 미기록'}/${executionResult.execution_device||'장치 미기록'}`:undefined,
    evaluation:evaluationReceipt, approval:currentApproval,
  });
  const exactSaved=identity.exactSaved;
  const currentEvaluation=identity.evaluationCurrent;
  const approvalLabel=identity.approval;

  const handleRestoreSaved = async () => {
    if (!canVerifyGraph) return;
    if (pipelineDirty && !window.confirm('현재 플로우의 저장하지 않은 변경 사항을 버리고 저장본을 불러올까요?')) return;
    setModelCheck({ status: 'checking' });
    const sourceKey = datasetKey;
    const result = await recoverThenLoadFlowchart({
      folderPath, task, datasetKey, hasSelectedFolder, datasetIsLoading, importError,
      allowLatestRecovery, completedCurrentJobId,
      getVerifiedJobId: () => useEvaluationStore.getState().jobId,
      loadEvaluation,
      loadSavedPipeline: () => loadPipeline(true, task, folderPath),
      verifyModels: verifyModelReferences,
      isCurrent: () => useDatasetStore.getState().datasetKey === sourceKey
        && useProjectStore.getState().task === task,
    });
    if (result.status === 'ready') {
      setModelCheck({ status: 'ready' });
      setSelectedVersionId(savedVersions.find((version) => version.is_active)?.version_id || '');
    }
    else if (result.status === 'blocked') setModelCheck({ status: 'blocked', reason: result.reason });
    setActiveTab('edit');
  };

  const openSavedVersion = async (versionId: string) => {
    if (!versionId || isLoading || isRunning || isSaving) return;
    if (pipelineDirty && !window.confirm('저장하지 않은 플로우 변경 사항을 버리고 선택한 버전을 열까요?')) return;
    const sourceKey = datasetKey;
    const currentPipeline = pipeline;
    setIsVerifyingAction(true);
    try {
      if (useDatasetStore.getState().datasetKey !== sourceKey || useFlowchartStore.getState().pipeline !== currentPipeline) {
        throw new Error('플로우 또는 데이터가 검증 중 변경되었습니다. 버전을 다시 선택하세요.');
      }
      const opened = await loadPipelineVersion(versionId, folderPath);
      if (!opened) throw new Error('선택한 플로우 버전을 열지 못했습니다.');
      setSelectedVersionId(versionId);
      setZoomScale(null);
      try { await verifyCurrentPipeline(opened, folderPath); setModelCheck({ status: 'ready' }); }
      catch { setModelCheck({ status: 'blocked', reason: 'saved_model_mismatch' }); }
      setSelectedEdgeId(null);
      setActionValidationError(null);
      setActiveTab('edit');
    } catch (cause) {
      setActionValidationError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setIsVerifyingAction(false);
    }
  };

  const activateSavedVersion = async () => {
    if (!selectedVersionId || !pipeline || pipelineDirty || pipelineIsDraft || isVerifyingAction || isRunning || isSaving) return;
    const selected = savedVersions.find(v => v.version_id === selectedVersionId);
    if (!selected || !window.confirm(`“${selected.name}” 버전을 활성 검사 흐름으로 지정할까요? 다음 일괄 검사와 자동 운영에서 사용됩니다.`)) return;
    const sourceKey = datasetKey;
    const graph = pipeline;
    setIsVerifyingAction(true); setActionValidationError(null);
    try {
      await verifyCurrentPipeline(graph, folderPath);
      if (useDatasetStore.getState().datasetKey !== sourceKey || useFlowchartStore.getState().pipeline !== graph) throw new Error('검증 중 데이터 또는 플로우가 바뀌었습니다.');
      await api.flowchart.activatePipelineVersion(selectedVersionId, folderPath);
      const readback = await api.flowchart.listPipelines(folderPath);
      if (!readback.pipelines.some(v => v.version_id === selectedVersionId && v.is_active)) throw new Error('활성 버전 변경을 확인하지 못했습니다.');
      if (useDatasetStore.getState().datasetKey === sourceKey && useFlowchartStore.getState().pipeline === graph) setSavedVersions(readback.pipelines);
    } catch (cause) { setActionValidationError(cause instanceof Error ? cause.message : String(cause)); }
    finally { setIsVerifyingAction(false); }
  };

  const selectedNode = pipeline?.nodes.find((n) => n.id === selectedNodeId);
  const selectedEdge = pipeline?.edges.find((edge) => edge.id === selectedEdgeId);

  const updateFixedRoiField = (field: 'x' | 'y' | 'width' | 'height', rawValue: string) => {
    if (!selectedNode || selectedNode.data.node_type !== 'fixed_roi') return;
    const parsed = Number(rawValue);
    if (!Number.isFinite(parsed)) return;
    const [x1, y1, x2, y2] = (selectedNode.data.params?.roi_bbox as number[] | undefined) || [0, 0, 512, 512];
    const width = Math.max(16, x2 - x1);
    const height = Math.max(16, y2 - y1);
    const value = Math.max(field === 'width' || field === 'height' ? 16 : 0, Math.trunc(parsed));
    const rectangle = field === 'x' ? [value, y1, value + width, y2]
      : field === 'y' ? [x1, value, x2, value + height]
        : field === 'width' ? [x1, y1, x1 + value, y2]
          : [x1, y1, x2, y1 + value];
    updateNodeData(selectedNode.id, { params: { ...selectedNode.data.params, roi_bbox: rectangle } });
  };

  const addEditableNode = (nodeType: 'patch_split' | 'preprocess' | 'fixed_roi' | 'detection_crop' | 'inspection' | 'blob_measure' | 'measurement' | 'aggregate' | 'output') => {
    if (!pipeline || isRunning || isSaving) return;
    if (nodeType === 'output' && pipeline.nodes.filter((node) => node.data.node_type === 'output').length >= 3) {
      setEditorError('출력 분기는 최대 세 개입니다.'); return;
    }
    if (nodeType === 'fixed_roi' && pipeline.nodes.filter((node) => node.data.node_type === 'fixed_roi').length >= 8) {
      setEditorError('고정 ROI 노드는 최대 여덟 개입니다.'); return;
    }
    if (['blob_measure','measurement'].includes(nodeType) && pipeline.nodes.filter((node) => ['blob_measure','measurement'].includes(node.data.node_type)).length >= 8) {
      setEditorError('Blob·기하 측정 노드는 합계 최대 여덟 개입니다.'); return;
    }
    if (nodeType === 'aggregate' && pipeline.nodes.filter((node) => node.data.node_type === 'aggregate').length >= 4) {
      setEditorError('결과 집계 노드는 최대 네 개입니다.'); return;
    }
    if ((nodeType === 'detection_crop' || nodeType === 'inspection') && pipeline.nodes.filter((node) =>
      node.data.node_type === 'detection_crop' || node.data.node_type === 'inspection').length >= 8) {
      setEditorError('모델 노드는 최대 여덟 개입니다.'); return;
    }
    const id = `node_${nodeType}_${Date.now()}_${Math.random().toString(36).slice(2, 7)}`;
    const modelCount = pipeline.nodes.filter((node) => getFlowchartModelTask(node) !== null).length;
    const outputCount = pipeline.nodes.filter((node) => node.data.node_type === 'output').length;
    const blobCount = pipeline.nodes.filter((node) => node.data.node_type === 'blob_measure').length;
    const aggregateCount = pipeline.nodes.filter((node) => node.data.node_type === 'aggregate').length;
    const resultNode = ['blob_measure','measurement','aggregate'].includes(nodeType);
    const resultSourceTypes = nodeType === 'blob_measure' || nodeType === 'measurement'
      ? ['inspection'] : ['inspection', 'detection_crop', 'blob_measure', 'measurement'];
    const resultX = resultNode ? Math.max(340, ...pipeline.nodes
      .filter((item) => resultSourceTypes.includes(item.data.node_type))
      .map((item) => item.position.x)) + 300 : 0;
    const node: FlowNode = {
      id,
      position: nodeType === 'output'
        ? { x: 1320, y: 80 + outputCount * 240 }
        : { x: resultNode ? resultX : nodeType === 'fixed_roi' ? 325 : nodeType === 'detection_crop' ? 340 : 670,
          y: 80 + (nodeType === 'blob_measure' ? blobCount : nodeType === 'aggregate' ? aggregateCount : modelCount) * 240 },
      data: {
        label: nodeType === 'patch_split' ? '패치 분할' : nodeType === 'preprocess' ? '영상 전처리' : nodeType === 'output' ? `판정 출력 ${outputCount + 1}`
          : nodeType === 'fixed_roi' ? `고정 ROI ${pipeline.nodes.filter((item) => item.data.node_type === 'fixed_roi').length + 1}`
            : nodeType === 'detection_crop' ? `검출 모델 ${modelCount + 1}`
              : nodeType === 'blob_measure' ? `Blob 측정 ${blobCount + 1}`
                : nodeType === 'measurement' ? '원본 기하 측정' : nodeType === 'aggregate' ? `결과 집계 ${aggregateCount + 1}` : `검사 모델 ${modelCount + 1}`,
        node_type: nodeType,
        task: nodeType === 'detection_crop' ? 'detection' : nodeType === 'inspection' ? (task === 'detection' ? 'segmentation' : task) : undefined,
        model_job_id: undefined,
        threshold: nodeType === 'detection_crop' || nodeType === 'inspection' ? 0.5 : undefined,
        crop_padding: nodeType === 'detection_crop' ? 10 : undefined,
        rule: nodeType === 'aggregate' ? 'any_ng' : undefined,
        params: nodeType === 'patch_split' ? { patch_width: 224, patch_height: 224, overlap: 0 } : nodeType === 'preprocess' ? { operation: 'rotate', angle_deg: 0 } : nodeType === 'inspection' ? { min_defect_area_px: 8 }
          : nodeType === 'fixed_roi' ? { roi_bbox: [0, 0, 512, 512] }
            : nodeType === 'blob_measure' ? { min_blob_area_px: 1, min_blob_count_for_ng: 1 } : nodeType === 'measurement' ? {paths:[]} : {},
      },
    };
    const existingNodes = resultNode ? pipeline.nodes.map((item) => {
      if (item.data.node_type !== 'decision' && item.data.node_type !== 'output') return item;
      const minimumX = resultX + (item.data.node_type === 'decision' ? 300 : 600);
      return item.position.x >= minimumX ? item
        : { ...item, position: { ...item.position, x: minimumX } };
    }) : pipeline.nodes;
    replacePipeline({ ...pipeline, nodes: [...existingNodes, node] });
    selectNode(id);
    setSelectedEdgeId(null);
    setEditorError(null);
  };

  const startConnection = (nodeId: string, payloads?: FlowPortPayload[]) => {
    setConnectionSourceId(nodeId);
    setConnectionPayloads(payloads);
    setSelectedEdgeId(null);
    selectNode(nodeId);
    setEditorError(null);
  };

  const finishConnection = (targetId: string) => {
    if (!pipeline || !connectionSourceId) {
      setEditorError('먼저 출발 노드의 출력 포트를 클릭하세요.'); return;
    }
    try {
      replacePipeline(connectFlowNodes(pipeline, connectionSourceId, targetId, connectionPayloads));
      setConnectionSourceId(null);
      setEditorError(null);
      selectNode(targetId);
    } catch (error) {
      setEditorError(error instanceof Error ? error.message : '연결에 실패했습니다.');
    }
  };

  const deleteSelectedNode = () => {
    if (!pipeline || !selectedNode) return;
    try {
      replacePipeline(removeFlowNode(pipeline, selectedNode.id));
      selectNode(null);
      setEditorError(null);
      if (connectionSourceId === selectedNode.id) setConnectionSourceId(null);
    } catch (error) {
      setEditorError(error instanceof Error ? error.message : '노드를 삭제할 수 없습니다.');
    }
  };

  const deleteSelectedEdge = () => {
    if (!pipeline || !selectedEdgeId) return;
    replacePipeline({ ...pipeline, edges: pipeline.edges.filter((edge) => edge.id !== selectedEdgeId) });
    setSelectedEdgeId(null);
    setEditorError(null);
  };

  const changeEdgeBranch = (branch: 'pass' | 'fail' | 'review' | 'default') => {
    if (!pipeline || !selectedEdgeId) return;
    try {
      replacePipeline(updateFlowEdgeBranch(pipeline, selectedEdgeId, branch));
      setEditorError(null);
    } catch (error) {
      setEditorError(error instanceof Error ? error.message : '분기를 바꿀 수 없습니다.');
    }
  };

  const changeEdgePayload = (payload: 'image' | 'roi' | 'result') => {
    if (!pipeline || !selectedEdgeId) return;
    try {
      replacePipeline(updateFlowEdgePayload(pipeline, selectedEdgeId, payload));
      setEditorError(null);
    } catch (error) {
      setEditorError(error instanceof Error ? error.message : '전달 데이터 형식을 바꿀 수 없습니다.');
    }
  };
  const modelCheckMessage = modelCheck.reason === 'dataset_unavailable'
    ? (language === 'ko' ? '현재 데이터 폴더를 확인할 수 없습니다. 1단계에서 데이터셋을 다시 불러오세요.' : 'The current dataset is unavailable. Import it again in Step 1.')
    : modelCheck.reason === 'model_recovery_disabled'
      ? (language === 'ko' ? '현재 데이터에 맞는 모델 연결을 확인할 수 없습니다. 3단계에서 학습을 완료하고 4단계에서 평가를 확인하세요.' : 'No model can be verified for this dataset. Complete training in Step 3 and check evaluation in Step 4.')
      : modelCheck.reason === 'model_unavailable'
        ? (language === 'ko' ? '4단계에서 선택한 모델이 없습니다. 완료 모델이 있다면 검사 노드에서 선택해 연결할 수 있습니다.' : 'No model is selected in Step 4. You can connect a completed model in the inspection node.')
        : modelCheck.reason === 'saved_model_mismatch'
          ? (language === 'ko' ? '저장된 모델 중 현재 데이터와 맞지 않는 항목이 있습니다. 노드에서 모델을 다시 선택한 뒤 저장·실행을 검증하세요.' : 'A saved model does not match this dataset. Choose a compatible model in the node, then verify by saving or running.')
          : (language === 'ko' ? '저장된 플로우를 불러올 수 없습니다. 데이터와 평가 결과를 확인한 뒤 다시 5단계에 들어오세요.' : 'The saved flow could not be loaded. Check the dataset and evaluation, then reopen Step 5.');

  // Normalize node positions if not set
  const getNodePosition = (nodeId: string, idx: number, currentPos?: { x: number; y: number }) => {
    if (currentPos && (currentPos.x > 0 || currentPos.y > 0)) {
      return currentPos;
    }
    switch (nodeId) {
      case 'node_input':
        return { x: 40, y: 160 };
      case 'node_crop':
        return { x: 340, y: 160 };
      case 'node_inspect':
        return { x: 640, y: 160 };
      case 'node_decision':
        return { x: 940, y: 160 };
      case 'node_output_pass':
        return { x: 1260, y: 100 };
      case 'node_output_ng':
        return { x: 1260, y: 260 };
      case 'node_output':
        return { x: 1260, y: 160 };
      default:
        return { x: 40 + idx * 300, y: 160 };
    }
  };
  const positionedNodes = pipeline?.nodes.map((node, idx) => ({
    ...node,
    position: getNodePosition(node.id, idx, node.position),
  })) || [];
  const fitViewport = computeFlowchartViewport(positionedNodes, canvasSize);
  const defaultScale = readableFlowScale(fitViewport.scale);
  const desiredScale = zoomScale ?? defaultScale;
  const viewport = computeFlowchartViewport(positionedNodes, canvasSize, desiredScale / fitViewport.scale);
  const displayViewport = dragViewport || viewport;

  const handleLayout = () => {
    if (!pipeline || isLoading || isSaving || isRunning || isVerifyingAction) return;
    try {
      const arranged = layoutFlowchart(pipeline);
      useFlowchartStore.getState().endHistoryGroup();
      replacePipeline(arranged);
      setZoomScale(computeFlowchartViewport(arranged.nodes, canvasSize).scale);
      setDragViewport(null);
      setEditorError(null);
      canvasRef.current?.scrollTo({ left: 0, top: 0, behavior: 'smooth' });
    } catch (error) {
      setEditorError(error instanceof Error ? error.message : '흐름을 정렬하지 못했습니다.');
    }
  };

  useEffect(() => {
    if (canvasRef.current) canvasRef.current.scrollLeft = 0;
  }, [pipeline?.id]);

  const beginNodeDrag = (event: React.PointerEvent<HTMLDivElement>, node: FlowNode) => {
    if (event.button !== 0 || (event.target as HTMLElement).closest('button, input, select')) return;
    dragRef.current = {
      nodeId: node.id, startX: event.clientX, startY: event.clientY,
      x: node.position.x, y: node.position.y, scale: displayViewport.scale,
    };
    setDragViewport(displayViewport);
    event.currentTarget.setPointerCapture(event.pointerId);
    beginHistoryGroup();
    selectNode(node.id);
    setSelectedEdgeId(null);
  };

  const dragNode = (event: React.PointerEvent<HTMLDivElement>) => {
    const drag = dragRef.current;
    if (!drag) return;
    const dx = (event.clientX - drag.startX) / drag.scale;
    const dy = (event.clientY - drag.startY) / drag.scale;
    if (Math.abs(dx) + Math.abs(dy) < 2) return;
    moveNode(drag.nodeId, { x: Math.round(drag.x + dx), y: Math.round(drag.y + dy) });
  };

  const endNodeDrag = () => {
    endHistoryGroup();
    dragRef.current = null;
    setDragViewport(null);
  };

  return (
    <div className="min-h-0 flex-1 flex flex-col h-full bg-[#0B0E14] text-[#E2E8F0] overflow-y-auto overflow-x-hidden select-none">
      <FlowEditorWorkspace area={activeTab} onAreaChange={setActiveTab} identity={identity}/>
      {/* Top Flowchart Toolbar (Dark Steel Panel) */}
      <div className="shrink-0 min-h-14 bg-[#131822] border-b border-[#2B3547] px-4 py-2 flex flex-wrap items-center justify-between gap-x-4 gap-y-2 text-xs">
        <div className="flex min-w-0 items-center gap-3">
          <div className="w-8 h-8 shrink-0 rounded-md bg-cyan-950/40 border border-cyan-800/70 flex items-center justify-center">
            <GitFork className="w-4 h-4 text-cyan-300" />
          </div>
          <div className="min-w-0">
            <h2 className="font-bold text-sm text-[#F8FAFC] tracking-wide">
              {language === 'ko' ? '검사 플로우 편집기' : 'Inspection Flow Editor'}
            </h2>
            <p className="text-xs text-[#94A3B8]">
              {language === 'ko' ? '검사 노드·연결·판정 분기 편집' : 'Edit inspection nodes, edges and verdict branches'}
            </p>
          </div>
        </div>

        <div hidden={activeTab!=='edit'}><FlowDraftControls blocked={isVerifyingAction||dragViewport!==null||isRoiEditing} activeVersion={!pipelineDirty&&!pipelineIsDraft&&savedVersions.some(v=>v.version_id===selectedVersionId&&v.is_active)} hasSavedVersion={Boolean(selectedVersionId)}/></div>
        {/* Action Controls */}
        <div hidden={activeTab!=='edit'} className={activeTab==='edit'?"flex flex-wrap items-center justify-end gap-2":"hidden"}>
          <div className="flex rounded border border-[#2B3547] bg-[#0B0E14] p-0.5" aria-label="플로우 편집 기록">
            <button type="button" onClick={() => applyHistoryAction('undo')}
              disabled={!canUndo || isLoading || isSaving || isRunning || isVerifyingAction}
              title="실행 취소 (⌘/Ctrl+Z)" aria-label="플로우 실행 취소"
              className="flex items-center gap-1 rounded px-2 py-1 text-slate-300 hover:bg-[#243247] disabled:opacity-40 disabled:cursor-not-allowed">
              <Undo2 className="h-4 w-4" /> <span>실행 취소</span>
            </button>
            <button type="button" onClick={() => applyHistoryAction('redo')}
              disabled={!canRedo || isLoading || isSaving || isRunning || isVerifyingAction}
              title="다시 실행 (⌘/Ctrl+Shift+Z, Ctrl+Y)" aria-label="플로우 다시 실행"
              className="flex items-center gap-1 rounded px-2 py-1 text-slate-300 hover:bg-[#243247] disabled:opacity-40 disabled:cursor-not-allowed">
              <Redo2 className="h-4 w-4" /> <span>다시 실행</span>
            </button>
          </div>
          {saveMessage && (
            <span role="status" className="text-xs text-emerald-300 font-bold">
              ✓ {saveMessage}
            </span>
          )}

          {/* Save Pipeline Button */}
          <button type="button" onClick={handleLayout}
            disabled={!pipeline || isLoading || isSaving || isRunning || isVerifyingAction}
            title="연결 순서대로 노드를 배치하고 전체 흐름을 표시합니다. 실행 취소할 수 있습니다."
            className="flex items-center gap-1.5 rounded border border-[#2B3547] bg-[#1A212E] px-3 py-1.5 text-xs font-semibold text-slate-200 hover:bg-[#222B3D] disabled:opacity-50">
            <ListTree className="h-3.5 w-3.5" /> <span>흐름 정렬</span>
          </button>
          <button
            data-primary-action="true" onClick={handleSave}
            disabled={!canVerifyGraph || isVerifyingAction || isSaving || isLoading || isRunning || !pipeline || needsModel || !!graphError}
            title={!canVerifyGraph ? modelCheckMessage : graphError || (needsModel ? '각 모델 노드에 완료 모델을 선택하세요.' : undefined)}
            className="flex items-center space-x-1.5 px-3 py-1.5 bg-cyan-800 hover:bg-cyan-700 text-white rounded border border-cyan-600 text-xs font-bold cursor-pointer transition-colors disabled:opacity-50"
          >
            <Save className="w-3.5 h-3.5 text-[#94A3B8]" />
            <span>{isSaving ? (language === 'ko' ? '저장 중...' : 'Saving...') : (language === 'ko' ? '플로우 저장' : 'Save')}</span>
          </button>


        </div>
      </div>

      <div hidden={activeTab!=='edit'}>
      <details className="shrink-0 border-b border-slate-700 bg-[#101722] text-xs"><summary className="cursor-pointer px-4 py-1 text-slate-400">빠른 노드 추가·연결·실행 자원</summary>
      {pipeline && <div className="shrink-0 border-b border-slate-700 px-4 py-2"><FlowResourcesEditor pipeline={pipeline} onChange={replacePipeline} disabled={isRunning||isSaving||isLoading}/></div>}

      <div className="min-h-10 bg-[#101722] border-b border-[#2B3547] px-4 py-1.5 flex flex-wrap items-center gap-x-2 gap-y-1.5 text-xs">
        <span className="mr-1 text-xs font-bold tracking-wider text-slate-400">노드 추가</span>
        <button onClick={() => addEditableNode('patch_split')} disabled={!pipeline || isRunning} className="rounded border border-slate-600 px-2 py-1 text-xs">패치 분할 추가</button>
        <button onClick={() => addEditableNode('preprocess')} disabled={!pipeline || isRunning} className="rounded border border-slate-600 px-2 py-1 text-xs">영상 전처리 추가</button>
        <button onClick={() => addEditableNode('fixed_roi')} disabled={!pipeline || isLoading || isSaving || isRunning}
          className="px-2 py-1 border border-sky-700 rounded text-sky-200 hover:bg-sky-950 disabled:opacity-50 flex items-center gap-1">
          <Plus className="w-3 h-3" /> 고정 ROI
        </button>
        <button onClick={() => addEditableNode('detection_crop')} disabled={!pipeline || isLoading || isSaving || isRunning}
          className="px-2 py-1 border border-amber-700 rounded text-amber-200 hover:bg-amber-950 disabled:opacity-50 flex items-center gap-1">
          <Plus className="w-3 h-3" /> 검출 모델
        </button>
        <button onClick={() => addEditableNode('inspection')} disabled={!pipeline || isLoading || isSaving || isRunning}
          className="px-2 py-1 border border-purple-700 rounded text-purple-200 hover:bg-purple-950 disabled:opacity-50 flex items-center gap-1">
          <Plus className="w-3 h-3" /> 검사 모델
        </button>
        <button onClick={() => addEditableNode('blob_measure')} disabled={!pipeline || isLoading || isSaving || isRunning}
          className="px-2 py-1 border border-teal-700 rounded text-teal-200 hover:bg-teal-950 disabled:opacity-50 flex items-center gap-1">
          <Plus className="w-3 h-3" /> Blob 측정
        </button>
        <button onClick={() => addEditableNode('measurement')} disabled={!pipeline || isLoading || isSaving || isRunning} className="rounded border border-teal-700 px-2 py-1 text-teal-200 disabled:opacity-50">길이·면적 측정</button>
        <button onClick={() => addEditableNode('aggregate')} disabled={!pipeline || isLoading || isSaving || isRunning}
          className="px-2 py-1 border border-indigo-700 rounded text-indigo-200 hover:bg-indigo-950 disabled:opacity-50 flex items-center gap-1">
          <Plus className="w-3 h-3" /> 결과 집계
        </button>
        <button onClick={() => addEditableNode('output')} disabled={!pipeline || isLoading || isSaving || isRunning}
          className="px-2 py-1 border border-emerald-700 rounded text-emerald-200 hover:bg-emerald-950 disabled:opacity-50 flex items-center gap-1">
          <Plus className="w-3 h-3" /> 판정 출력
        </button>
        <span className="border-l border-[#2B3547] pl-3 text-slate-400 flex items-center gap-1">
          <Link2 className="w-3 h-3" /> 출력 포트 → 입력 포트를 클릭해 연결 · 노드를 끌어 이동 · 선을 클릭해 편집
        </span>
        {connectionSourceId && (
          <button onClick={() => setConnectionSourceId(null)} className="ml-auto px-2 py-1 border border-cyan-700 rounded text-cyan-200">
            연결 시작: {pipeline?.nodes.find((node) => node.id === connectionSourceId)?.data.label || connectionSourceId} · 취소
          </button>
        )}
      </div>

      </details>
      <div className="shrink-0 relative z-20 min-h-12 bg-[#111923] border-b border-[#2B3547] px-4 py-1.5 flex flex-wrap items-center justify-between gap-2 text-xs">
        <div className="flex flex-wrap items-center gap-2 min-w-0">
          <span className="font-semibold text-slate-100 truncate max-w-[280px]" title={pipeline?.name}>{pipeline?.name || '플로우 불러오는 중'}</span>
          {pipeline && <span role="status" className={`shrink-0 rounded-full border px-2 py-0.5 text-xs font-semibold ${pipelineDirty
            ? 'border-amber-700 bg-amber-950/50 text-amber-200'
            : selectedVersionId && savedVersions.some((version) => version.version_id === selectedVersionId && version.is_active)
              ? 'border-emerald-800 bg-emerald-950/40 text-emerald-200'
              : 'border-slate-700 bg-slate-900 text-slate-300'}`}>
            {flowDraftStatus(pipelineDirty,persistedDraftHash,!pipelineIsDraft&&savedVersions.some(version=>version.version_id===selectedVersionId&&version.is_active),Boolean(selectedVersionId))}
          </span>}
          {savedVersions.length > 0 && <label className="ml-2 flex shrink-0 items-center gap-1.5 text-xs text-slate-400">
            저장 버전
            <select value={selectedVersionId} onChange={(event) => openSavedVersion(event.target.value)}
              disabled={isLoading || isSaving || isRunning || isVerifyingAction}
              className="max-w-[220px] rounded border border-[#364357] bg-[#1A212E] px-2 py-1 text-xs text-slate-100 disabled:opacity-50">
              <option value="">버전 선택</option>
              {savedVersions.map((version) => <option key={version.version_id} value={version.version_id}>
                {version.is_active ? '● 활성 · ' : ''}{version.name} · {version.is_active && !pipelineDirty && selectedVersionId === version.version_id && pipeline
                  ? flowRecipeLabel(pipeline, version.recipe_task)
                  : version.recipe_task === 'mixed' ? '복합 모델' : version.recipe_task} · {new Date(version.saved_at).toLocaleString('ko-KR')}
              </option>)}
            </select>
          </label>}
          {selectedVersionId && !savedVersions.some(v => v.version_id === selectedVersionId && v.is_active) &&
            <button onClick={() => void activateSavedVersion()} disabled={pipelineDirty || pipelineIsDraft || isLoading || isSaving || isRunning || isVerifyingAction || needsModel || Boolean(graphError)}
              title="저장 버전을 열면 편집 화면에만 표시됩니다. 검증 후 이 버튼으로 활성 검사 흐름을 지정하세요."
              className="shrink-0 rounded border border-emerald-700 bg-emerald-950/40 px-2 py-1 text-xs text-emerald-100 disabled:opacity-40">이 버전 활성화</button>}
          <span className="shrink-0 rounded border border-[#344255] px-2 py-1 text-xs text-slate-300">
            모델 {pipeline?.nodes.filter((node) => getFlowchartModelTask(node) !== null && Boolean(node.data.model_job_id)).length || 0}/{pipeline?.nodes.filter((node) => getFlowchartModelTask(node) !== null).length || 0} 연결
          </span>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          <details className="group relative">
            <summary className="cursor-pointer list-none rounded border border-sky-700 bg-sky-950/50 px-3 py-1.5 font-semibold text-sky-100 hover:bg-sky-900">
              새 플로우 ▾
            </summary>
            <div className="absolute right-0 top-full mt-2 grid w-64 gap-1 rounded-lg border border-[#3B4B60] bg-[#192333] p-2 shadow-xl">
              <span className="px-2 py-1 text-xs font-bold uppercase tracking-wider text-slate-400">기본</span>
              <button onClick={handleSingleModel} disabled={isLoading || isSaving || isRunning || isVerifyingAction}
                className="rounded px-2.5 py-2 text-left text-slate-100 hover:bg-[#293B51] disabled:opacity-50">
                {task === 'classification' ? '원본 이미지 분류' : task === 'anomaly' ? '원본 이미지 이상 탐지' : task === 'detection' ? '결함 검출' : '원본 타일 분할'}
              </button>
              <button onClick={handleDetectorRoi} disabled={isLoading || isSaving || isRunning || isVerifyingAction}
                className="rounded px-2.5 py-2 text-left text-slate-100 hover:bg-[#293B51] disabled:opacity-50">검출 ROI 검사</button>
              <button onClick={handleFixedRoi} disabled={isLoading || isSaving || isRunning || isVerifyingAction}
                className="rounded px-2.5 py-2 text-left text-sky-100 hover:bg-[#293B51] disabled:opacity-50">고정 ROI 검사 · 원본 픽셀 좌표</button>
              <button onClick={()=>openRecipe('rotation')} disabled={isLoading||isSaving||isRunning} className="rounded px-2.5 py-2 text-left text-slate-100 hover:bg-[#293B51]">학습 회전 → OCR·검사</button>
              <button onClick={()=>openRecipe('multi')} disabled={isLoading||isSaving||isRunning} className="rounded px-2.5 py-2 text-left text-slate-100 hover:bg-[#293B51]">전처리 → 다중 모델 → 집계</button>
              <span className="mt-1 border-t border-[#344255] px-2 pt-2 text-xs font-bold uppercase tracking-wider text-slate-400">복합 검사 예시</span>
              <button onClick={() => handleExampleTemplate('chain')} disabled={isLoading || isSaving || isRunning || isVerifyingAction}
                className="rounded px-2.5 py-2 text-left text-slate-100 hover:bg-[#293B51] disabled:opacity-50">5개 모델 연속 검사</button>
              <button onClick={() => handleExampleTemplate('conditional')} disabled={isLoading || isSaving || isRunning || isVerifyingAction}
                className="rounded px-2.5 py-2 text-left text-slate-100 hover:bg-[#293B51] disabled:opacity-50">조건 분기 검사</button>
            </div>
          </details>
          <button
            onClick={handleRestoreSaved}
            disabled={!canVerifyGraph || isVerifyingAction || isLoading || isSaving || isRunning}
            className="px-2.5 py-1 bg-[#1A212E] hover:bg-[#222B3D] text-slate-300 border border-[#2B3547] rounded disabled:opacity-50"
          >
            저장본 불러오기
          </button>
        </div>
      </div>

      </div>
      {modelHandoff&&!offeredModel&&modelHandoffDismissed!==`${modelHandoff.scope}:${modelHandoff.selectedAt}`&&!modelCatalogLoading&&<div role="alert" className="border-b border-amber-700 bg-amber-950/30 p-3 text-sm text-amber-200">선택한 완료 모델이 현재 프로젝트·정답 버전의 모델 목록에 없습니다. 학습 화면에서 출처를 확인하세요.<button className="ml-3 underline" onClick={dismissModelHandoff}>선택 닫기</button></div>}
      {offeredModel&&<section aria-label="선택 모델 플로우 연결" className="shrink-0 border-b border-cyan-800 bg-cyan-950/30 p-3 text-sm text-slate-200"><strong>{offeredModel.label} · 선택한 완료 모델</strong><p className="mt-1 text-slate-400">기존 연결을 유지하며 모델을 추가하거나 호환 노드에 지정합니다. 새 노드는 연결선을 직접 지정하세요.</p><div className="mt-2 flex flex-wrap gap-2"><select aria-label="선택 모델 연결 대상" value={handoffNode} onChange={event=>setHandoffNode(event.target.value)} className="rounded border border-slate-600 bg-slate-950 p-2"><option value="">새 모델 노드 추가</option>{pipeline?.nodes.filter(node=>compatibleModelNode(node,offeredModel)).map(node=><option key={node.id} value={node.id}>{node.data.label}</option>)}</select><button disabled={!pipeline||isRunning||isSaving||isVerifyingAction} onClick={()=>void applyModelHandoff()} className="rounded bg-cyan-700 px-3 py-2 disabled:opacity-40">선택 모델 연결</button><button onClick={dismissModelHandoff} className="rounded border border-slate-600 px-3 py-2">선택 닫기</button></div></section>}
      {modelCheck.status !== 'ready' && (
        <div className="shrink-0 min-h-11 bg-amber-950/40 border-b border-amber-800 px-5 py-2 flex items-center justify-between gap-3 text-xs text-amber-200" role="status">
          <span>{modelCheck.status === 'checking'
            ? (language === 'ko' ? '현재 데이터와 학습 모델을 확인한 뒤 저장된 플로우를 불러오는 중입니다.' : 'Verifying the dataset and model before loading the saved flow.')
            : modelCheckMessage}</span>
          {modelCheck.status === 'blocked' && (
            <div className="flex gap-2 shrink-0">
              <button onClick={() => setVerificationRetry((previous) => previous + 1)} className="px-2 py-1 border border-amber-700 rounded hover:bg-amber-900/50">
                {language === 'ko' ? '모델 다시 확인' : 'Recheck Models'}
              </button>
              <button onClick={() => setStep(3)} className="px-2 py-1 border border-amber-700 rounded hover:bg-amber-900/50">
                {language === 'ko' ? '3단계 학습' : 'Step 3 Training'}
              </button>
              <button onClick={() => setStep(4)} className="px-2 py-1 border border-amber-700 rounded hover:bg-amber-900/50">
                {language === 'ko' ? '4단계 평가' : 'Step 4 Evaluation'}
              </button>
            </div>
          )}
        </div>
      )}

      {graphError && pipeline && (
        <div className="shrink-0 min-h-9 bg-amber-950/40 border-b border-amber-800 px-5 py-2 text-xs text-amber-200 flex items-center justify-between gap-3" role="status">
          <span>연결을 완성하면 저장·실행할 수 있습니다: {graphError}</span>
          {graphIssue && <button type="button" className="shrink-0 rounded border border-amber-600 px-2 py-1 hover:bg-amber-900/60"
            onClick={() => {
              setActiveTab('edit');
              if (graphIssue.kind === 'edge') {
                setSelectedEdgeId(graphIssue.id);
                selectNode(null);
              } else {
                selectNode(graphIssue.id);
                setSelectedEdgeId(null);
                window.requestAnimationFrame(() => {
                  const element = [...(canvasRef.current?.querySelectorAll<HTMLElement>('[data-flow-node-id]') || [])]
                    .find((item) => item.dataset.flowNodeId === graphIssue.id);
                  element?.scrollIntoView({ block: 'center', inline: 'center', behavior: 'smooth' });
                });
              }
            }}>문제 {graphIssue.kind === 'edge' ? '연결선' : '노드'} 보기</button>}
        </div>
      )}

      {modelCheck.status === 'ready' && needsModel && !isLoading && (
        <div className="shrink-0 min-h-11 bg-amber-950/40 border-b border-amber-800 px-5 py-2 flex items-center justify-between gap-3 text-xs text-amber-200">
          <span>
            {hasDetectionNode
              ? (language === 'ko'
                ? missingDetectionModel && missingInspectionModel
                  ? '검출·검사 모델을 연결하세요. 각 모델을 학습한 뒤 오른쪽에서 호환 모델을 선택하세요.'
                  : missingDetectionModel
                    ? '검출 모델을 연결하세요. 같은 데이터로 학습한 모델을 오른쪽에서 선택하세요.'
                    : 'ROI 검사 모델을 연결하세요. 검사 노드에서 호환 모델을 선택하세요.'
                : missingDetectionModel && missingInspectionModel
                  ? 'Both detector and inspection model job IDs are required. Train each task with this dataset, then enter its ID on the matching node.'
                  : missingDetectionModel
                    ? 'The detector model job ID is missing. Train detection with this dataset and enter its ID on the detector node.'
                    : 'The ROI inspection model job ID is missing. Enter a model trained with this dataset on the inspection node.')
              : task === 'segmentation'
              ? (language === 'ko'
                ? '검사 모델이 연결되지 않았습니다. 3단계에서 학습을 완료하고 4단계에서 평가 결과를 확인하면 단일 분할 플로우에 자동 연결됩니다.'
                : 'No inspection model is connected. Complete Step 3 training and load Step 4 evaluation to link the single segmentation flow.')
              : (language === 'ko'
                ? '이 작업에는 자동 연결되는 검사 플로우가 없습니다. 3단계에서 학습을 완료한 뒤 검사 노드에서 호환 모델을 선택하세요.'
                : 'This task has no automatic flow link. Complete Step 3 training, then enter a compatible model job ID on the inspection node.')}
          </span>
          {!hasDetectionNode && <div className="flex gap-2 shrink-0">
            <button onClick={() => setStep(3)} className="px-2 py-1 border border-amber-700 rounded hover:bg-amber-900/50">
              {language === 'ko' ? '3단계 학습' : 'Step 3 Training'}
            </button>
            <button onClick={() => setStep(4)} className="px-2 py-1 border border-amber-700 rounded hover:bg-amber-900/50">
              {language === 'ko' ? '4단계 평가' : 'Step 4 Evaluation'}
            </button>
          </div>}
        </div>
      )}

      {/* Target Image Selector Bar */}
      <div style={{display:activeTab==='edit'||activeTab==='test'?undefined:'none'}} className="shrink-0 min-h-11 bg-[#0E131C] border-b border-[#2B3547] px-4 py-1.5 flex flex-wrap items-center justify-between gap-2 text-xs">
        <div className="flex min-w-0 items-center gap-3">
          <span className="shrink-0 text-xs font-bold tracking-wide text-[#94A3B8]">검사 대상</span>
          {selectedImage ? (
            <div className="flex min-w-0 items-center space-x-2 bg-[#131822] px-2 py-0.5 rounded border border-[#2B3547]">
              {selectedImage.thumbnailUrl && (
                <img
                  src={resolveApiUrl(selectedImage.thumbnailUrl)}
                  alt="Thumbnail"
                  className="w-4 h-4 object-cover rounded bg-[#0B0E14]"
                />
              )}
              <span className="text-[#F8FAFC] font-bold truncate max-w-xs" title={selectedImage.fileName}>{selectedImage.fileName}</span>
              <span className="text-xs text-cyan-400 bg-[#0B0E14] px-1.5 py-0.5 rounded border border-[#2B3547]">
                {selectedImage.source.toUpperCase()}
              </span>
            </div>
          ) : (
            <span className="text-[#94A3B8] italic">검사 이미지가 선택되지 않았습니다.</span>
          )}
        </div>

        <div className="flex items-center gap-3">
          {/* Zoom Controls */}
          <div className="flex items-center space-x-1 bg-[#131822] px-1 py-0.5 rounded border border-[#2B3547]">
            <button
              onClick={() => setZoomScale((scale) => Math.max(0.35, (scale ?? desiredScale) - 0.1))}
              className="p-1 hover:bg-[#1A212E] rounded text-[#94A3B8] hover:text-[#F8FAFC]"
              title="Zoom Out"
              aria-label="플로우 축소"
            >
              <ZoomOut className="w-3 h-3" />
            </button>
            <span className="text-xs font-mono tabular-nums px-1 text-slate-300">
              {Math.round(displayViewport.scale * 100)}%
            </span>
            <button
              onClick={() => setZoomScale((scale) => Math.min(1.5, (scale ?? desiredScale) + 0.1))}
              className="p-1 hover:bg-[#1A212E] rounded text-[#94A3B8] hover:text-[#F8FAFC]"
              title="Zoom In"
              aria-label="플로우 확대"
            >
              <ZoomIn className="w-3 h-3" />
            </button>
            <button
              onClick={() => { setZoomScale(fitViewport.scale); canvasRef.current?.scrollTo({ left: 0, behavior: 'smooth' }); }}
              className="p-1 hover:bg-[#1A212E] rounded text-[#94A3B8] hover:text-[#F8FAFC]"
              title="Fit graph"
              aria-label="플로우 전체 맞춤"
            >
              <RotateCcw className="w-3 h-3" />
            </button>
          </div>

          {displayViewport.contentWidth > canvasSize.width + 16 && <div className="flex items-center gap-1 rounded border border-[#2B3547] bg-[#131822] px-1 py-0.5 text-xs text-slate-400">
            <button type="button" onClick={() => canvasRef.current?.scrollBy({ left: -Math.max(320, canvasSize.width * 0.65), behavior: 'smooth' })}
              className="rounded p-1 text-slate-200 hover:bg-[#1A212E]" aria-label="이전 노드 보기" title="이전 노드 보기"><ChevronLeft className="h-3.5 w-3.5" /></button>
            <span>좌우 탐색</span>
            <button type="button" onClick={() => canvasRef.current?.scrollBy({ left: Math.max(320, canvasSize.width * 0.65), behavior: 'smooth' })}
              className="rounded p-1 text-slate-200 hover:bg-[#1A212E]" aria-label="다음 노드 보기" title="다음 노드 보기"><ChevronRight className="h-3.5 w-3.5" /></button>
          </div>}

          <button
            onClick={() => setImagePickerOpen(true)}
            className="flex items-center space-x-1.5 px-2.5 py-1 bg-[#131822] hover:bg-[#1A212E] text-cyan-400 rounded border border-[#2B3547] font-bold cursor-pointer transition-colors"
          >
            <ImageIcon className="w-3 h-3" />
            <span>이미지 변경...</span>
          </button>
        </div>
      </div>

      {/* Error Notice */}
      {errorMessage && (
        <div className="shrink-0 bg-rose-950/90 border-b border-rose-800 px-5 py-2 flex items-center justify-between text-xs text-rose-300 font-mono">
          <div className="flex items-center space-x-2">
            <AlertTriangle className="w-4 h-4 text-rose-400" />
            <span>{errorMessage}</span>
          </div>
          <button onClick={clearError} className="text-rose-400 hover:text-rose-200 font-bold cursor-pointer">
            닫기
          </button>
        </div>
      )}
      {actionValidationError && (
        <div className="shrink-0 bg-rose-950/90 border-b border-rose-800 px-5 py-2 text-xs text-rose-300" role="alert">
          검사 플로우 오류: {actionValidationError}
        </div>
      )}
      {editorError && (
        <div className="shrink-0 bg-rose-950/90 border-b border-rose-800 px-5 py-2 text-xs text-rose-300 flex justify-between" role="alert">
          {editorError}<button onClick={() => setEditorError(null)} className="underline">닫기</button>
        </div>
      )}

      {/* Main Flow Canvas or Results View */}
      <section role="tabpanel" id="flow-panel-edit" aria-labelledby="flow-area-edit" hidden={activeTab!=='edit'} className={activeTab==='edit'?'flex min-h-[240px] flex-1 shrink-0 flex-col overflow-hidden':undefined}>
        <div className="min-h-[240px] shrink-0 flex-1 flex overflow-hidden">
          <aside aria-label="검사 노드 팔레트" className="w-40 shrink-0 border-r border-slate-700 bg-[#101722] p-3 space-y-2 overflow-auto text-xs">
            <h3 className="font-semibold text-slate-200">검사 노드</h3>
            {([['fixed_roi','고정 ROI'],['patch_split','패치 분할'],['preprocess','영상 전처리'],['detection_crop','검출 모델'],['inspection','검사 모델'],['blob_measure','Blob 규칙'],['measurement','치수 측정'],['aggregate','결과 집계'],['output','판정 출력']] as const).map(([type,label])=>
              <button key={type} disabled={!pipeline||isRunning||isSaving||isLoading} onClick={()=>addEditableNode(type)} className="w-full rounded border border-slate-600 px-2 py-2 text-left hover:bg-slate-800 disabled:opacity-50">{label}</button>)}
            <h3 className="pt-2 font-semibold text-slate-200">목적 레시피</h3>
            <ul aria-label="목적 레시피" className="space-y-2">{FLOW_RECIPES.map(card=><li key={card.id}><button onClick={()=>openRecipe(card.id)} disabled={!pipeline||isRunning||isSaving||isLoading}
              aria-describedby={`recipe-card-${card.id}`} className="w-full rounded border border-slate-600 px-2 py-2 text-left hover:bg-slate-800 disabled:opacity-50">
              <span className="block font-semibold text-slate-100">{card.title}</span><span id={`recipe-card-${card.id}`} className="block text-[11px] leading-snug text-slate-400">{card.description}</span></button></li>)}</ul>
            <p className="pt-2 text-slate-400 leading-relaxed">노드를 추가하고 출력 포트 → 입력 포트로 연결하세요. 내 템플릿은 아래에서 관리합니다.</p>
          </aside>
          {/* 2D PCB DAG Circuit Canvas */}
          <div
            ref={canvasRef}
            className="min-h-0 min-w-0 flex-1 bg-[#0B0E14] overflow-auto relative"
            style={{
              backgroundImage:
                'linear-gradient(to right, #131822 1px, transparent 1px), linear-gradient(to bottom, #131822 1px, transparent 1px)',
              backgroundSize: '32px 32px',
            }}
          >
            <div
              style={{
                width: displayViewport.contentWidth,
                height: displayViewport.contentHeight,
                position: 'relative',
              }}
            >
              <div
                style={{
                  transform: `translate(${displayViewport.offsetX}px, ${displayViewport.offsetY}px) scale(${displayViewport.scale})`,
                  transformOrigin: '0 0',
                  width: displayViewport.layerWidth,
                  height: displayViewport.layerHeight,
                  position: 'absolute',
                  left: 0,
                  top: 0,
                }}
              >
              {/* SVG PCB Trace Wiring Overlay */}
              {pipeline && (
                <DAGCircuitOverlay
                  nodes={positionedNodes}
                  edges={pipeline.edges}
                  activeRunningNodeId={activeRunningNodeId}
                  finalVerdict={canvasResult?.final_verdict}
                  routedOutputNodeId={canvasResult?.routed_output_node_id}
                  selectedEdgeId={selectedEdgeId}
                  edgeIssues={flowIssues?.edges}
                  executionSteps={canvasResult?.execution_steps}
                  onSelectEdge={(edgeId) => { setSelectedEdgeId(edgeId); selectNode(null); }}
                />
              )}

              {/* 2D Positioned Custom Nodes */}
              {positionedNodes.map((node) => {
                const isSelected = selectedNodeId === node.id;
                const isActive = activeRunningNodeId === node.id;
                const step = canvasResult?.execution_steps?.find((s) => s.node_id === node.id);
                const isStepPassed = step?.status === 'passed';
                const isFlaggedNg = step && step.status === 'flagged_ng';
                const pos = node.position;

                return (
                  <div
                    key={node.id}
                    data-flow-node-id={node.id}
                    style={{
                      position: 'absolute',
                      left: pos.x,
                      top: pos.y,
                      touchAction: 'none',
                    }}
                    onPointerDown={(event) => beginNodeDrag(event, node)}
                    onPointerMove={dragNode}
                    onPointerUp={endNodeDrag}
                    onPointerCancel={endNodeDrag}
                  >
                    <CustomNode
                      node={{ ...node, position: pos }}
                      isSelected={isSelected}
                      isActive={isActive}
                      isPassed={!!isStepPassed}
                      isFlaggedNg={!!isFlaggedNg}
                      isSkipped={step?.status === 'skipped'}
                      isReviewRequired={step?.status === 'review_required'}
                      latencyMs={step?.latency_ms}
                      isDetectorOnly={pipeline?.edges.some((edge) => edge.source === node.id &&
                        pipeline.nodes.some((item) => item.id === edge.target && item.data.node_type === 'decision'))}
                      isConnectionSource={connectionSourceId === node.id}
                      onSelect={() => { selectNode(node.id); setSelectedEdgeId(null); }}
                      onConnectStart={(payloads) => startConnection(node.id, payloads)}
                      onConnectFinish={() => finishConnection(node.id)}
                      issues={flowIssues?.nodes.get(node.id)}
                    />
                  </div>
                );
              })}
              </div>
            </div>
          </div>

          {/* Node Property Inspector Sidebar (Dark Steel Panel) */}
          <div className="shrink-0 bg-[#131822] border-l border-[#2B3547] p-5 flex flex-col gap-4 overflow-y-auto" style={{ width: 'clamp(300px, 26vw, 380px)' }}>
            <div className="border-b border-[#2B3547] pb-3">
              <h3 className="text-sm font-bold text-[#F8FAFC] flex items-center gap-2">
                <Sliders className="w-4 h-4 text-cyan-400" />
                <span>{language === 'ko' ? '노드·연결 속성' : 'Node and Connection Properties'}</span>
              </h3>
              <p className="mt-1 text-xs text-slate-400 truncate" title={selectedEdge?.label || selectedNode?.data.label}>
                {selectedEdge ? '선택한 연결선의 조건과 전달 데이터' : selectedNode ? `선택한 노드 · ${selectedNode.data.label}` : '그래프의 노드나 연결선을 선택하세요.'}
              </p>
              {(() => {
                const selectedIssues = selectedEdge ? flowIssues?.edges.get(selectedEdge.id) : selectedNode ? flowIssues?.nodes.get(selectedNode.id) : undefined;
                return selectedIssues?.length ? (
                  <ul aria-label="선택한 항목의 문제" className="mt-2 space-y-1 rounded border border-amber-600/70 bg-amber-950/30 p-2 text-xs text-amber-200">
                    {selectedIssues.map((message, index) => <li key={index}>{message}</li>)}
                  </ul>
                ) : null;
              })()}
            </div>

            {selectedEdge ? (
              <div className="space-y-4 text-xs">
                <div className="rounded border border-[#2B3547] bg-[#1A212E] p-3 text-slate-200">
                  {pipeline?.nodes.find((node) => node.id === selectedEdge.source)?.data.label || selectedEdge.source}
                  <span className="mx-2 text-cyan-400">→</span>
                  {pipeline?.nodes.find((node) => node.id === selectedEdge.target)?.data.label || selectedEdge.target}
                </div>
                <div>
                  <label className="text-[#94A3B8] block mb-1">연결선 이름</label>
                  <input type="text" value={selectedEdge.label || ''}
                    onChange={(event) => pipeline && replacePipeline({ ...pipeline, edges: pipeline.edges.map((edge) =>
                      edge.id === selectedEdge.id ? { ...edge, label: event.target.value } : edge) })}
                    className="w-full bg-[#1A212E] border border-[#2B3547] rounded px-2.5 py-1.5 text-[#F8FAFC]" />
                </div>
                {['detection_crop','inspection'].includes(pipeline?.nodes.find((node) => node.id===selectedEdge.source)?.data.node_type || '') && <div className="space-y-2"><label className="block">클래스 조건<select value={selectedEdge.predicate?.operator || ''} onChange={(e) => pipeline && replacePipeline({...pipeline,edges:pipeline.edges.map((edge) => edge.id===selectedEdge.id?{...edge,isBranch:undefined,predicate:e.target.value?{kind:'class',operator:e.target.value as 'present'|'absent',class_name:edge.predicate?.class_name || '',min_confidence:edge.predicate?.min_confidence || 0}:undefined}:edge)})} className="ml-2 rounded bg-slate-800 p-1"><option value="">사용 안 함</option><option value="present">클래스 있음</option><option value="absent">클래스 없음</option></select></label>{selectedEdge.predicate && <><input aria-label="분기 클래스 이름" placeholder="클래스 이름" value={selectedEdge.predicate.class_name} onChange={(e) => pipeline && replacePipeline({...pipeline,edges:pipeline.edges.map((edge) => edge.id===selectedEdge.id?{...edge,predicate:{...selectedEdge.predicate!,class_name:e.target.value}}:edge)})} className="w-full rounded bg-slate-800 p-1" /><label>최소 신뢰도<input type="number" min="0" max="1" step="0.05" value={selectedEdge.predicate.min_confidence || 0} onChange={(e) => pipeline && replacePipeline({...pipeline,edges:pipeline.edges.map((edge) => edge.id===selectedEdge.id?{...edge,predicate:{...selectedEdge.predicate!,min_confidence:Number(e.target.value)}}:edge)})} className="ml-2 w-20 rounded bg-slate-800 p-1" /></label></>}</div>}
                {(['detection_crop', 'inspection', 'blob_measure', 'measurement', 'aggregate'].includes(pipeline?.nodes.find((node) => node.id === selectedEdge.source)?.data.node_type || '')) && (
                  <div>
                    <label className="text-[#94A3B8] block mb-1">다음 노드 실행 조건</label>
                    <select value={selectedEdge.isBranch || 'default'}
                      onChange={(event) => changeEdgeBranch(event.target.value as 'default' | 'pass' | 'fail' | 'review')}
                      className="w-full bg-[#1A212E] border border-[#2B3547] rounded px-2.5 py-1.5 text-[#F8FAFC]">
                      <option value="default">항상 실행</option>
                      <option value="pass">상류 결과가 OK일 때</option>
                      <option value="fail">상류 결과가 NG일 때</option>
                      <option value="review">상류 결과가 REVIEW일 때</option>
                    </select>
                    <p className="mt-1.5 text-xs leading-relaxed text-slate-400">실행하지 않은 경로도 결과 추적에 남습니다. 최종 판정은 실행된 모델의 증거만 사용합니다.</p>
                  </div>
                )}
                {(['detection_crop', 'inspection'].includes(pipeline?.nodes.find((node) => node.id === selectedEdge.source)?.data.node_type || '')) &&
                  ['detection_crop', 'inspection'].includes(pipeline?.nodes.find((node) => node.id === selectedEdge.target)?.data.node_type || '') && (
                  <div>
                    <label className="text-[#94A3B8] block mb-1">다음 모델로 전달할 데이터</label>
                    <select value={selectedEdge.payload_type || 'roi'}
                      onChange={(event) => changeEdgePayload(event.target.value as 'image' | 'roi')}
                      className="w-full bg-[#1A212E] border border-[#2B3547] rounded px-2.5 py-1.5 text-[#F8FAFC]">
                      <option value="roi">검사 영역(ROI)과 원본 좌표</option>
                      <option value="image">원본 이미지</option>
                    </select>
                    <p className="mt-1.5 text-xs leading-relaxed text-slate-400">ROI를 선택하면 상류 모델이 찾은 영역을 그대로 다음 모델에 전달합니다.</p>
                  </div>
                )}
                {pipeline?.nodes.find((node) => node.id === selectedEdge.source)?.data.node_type === 'decision' &&
                  pipeline.edges.filter((edge) => edge.source === selectedEdge.source).length > 1 && (
                  <div>
                    <label className="text-[#94A3B8] block mb-1">판정 분기</label>
                    <select value={selectedEdge.isBranch || ''}
                      onChange={(event) => changeEdgeBranch(event.target.value as 'pass' | 'fail' | 'review')}
                      className="w-full bg-[#1A212E] border border-[#2B3547] rounded px-2.5 py-1.5 text-[#F8FAFC]">
                      <option value="pass">OK (pass)</option>
                      <option value="fail">NG (fail)</option>
                      {pipeline.edges.filter((edge) => edge.source === selectedEdge.source).length === 3 &&
                        <option value="review">REVIEW</option>}
                    </select>
                    <p className="mt-2 text-slate-400">분기를 바꾸면 기존 출력과 자동으로 맞바꿉니다.</p>
                  </div>
                )}
                <button onClick={deleteSelectedEdge} disabled={isRunning || isSaving}
                  className="w-full px-3 py-2 rounded border border-rose-800 text-rose-300 hover:bg-rose-950 disabled:opacity-50 flex justify-center items-center gap-2">
                  <Trash2 className="w-3.5 h-3.5" /> 연결선 삭제
                </button>
              </div>
            ) : selectedNode ? (
              <div className="space-y-4 text-xs">
                <div>
                  <label className="text-[#94A3B8] block mb-1">노드 명칭 (LABEL)</label>
                  <input
                    type="text"
                    value={selectedNode.data.label}
                    onChange={(e) => updateNodeData(selectedNode.id, { label: e.target.value })}
                    className="w-full bg-[#1A212E] border border-[#2B3547] rounded px-2.5 py-1.5 text-[#F8FAFC] focus:border-cyan-400 outline-none"
                  />
                </div>

                {(selectedNode.data.node_type === 'detection_crop' || selectedNode.data.node_type === 'inspection') && (
                  <div className="space-y-2">
                    {selectedNode.data.node_type === 'inspection' && (
                      <label className="block text-[#94A3B8]">검사 모델 종류
                        <select
                          value={selectedNode.data.task || 'anomaly'}
                          onChange={(e) => updateNodeData(selectedNode.id, { task: e.target.value, model_job_id: undefined, score_spec:undefined, threshold:.5, params: e.target.value === 'ocr' ? { expected_text: '' } : { min_defect_area_px: 8 } })}
                          className="mt-1 w-full bg-[#1A212E] border border-[#2B3547] rounded px-2.5 py-1.5 text-[#F8FAFC]"
                        >
                          <option value="anomaly">이상 탐지</option>
                          <option value="segmentation">영역 분할</option>
                          <option value="classification">이미지 분류</option>
                          <option value="patch_classification">패치 분류</option>
                          <option value="ocr">문자 인식·판정</option>
                          <option value="rotated_detection">회전 객체 검출</option>
                        </select>
                      </label>
                    )}
                    <label className="block text-[#94A3B8]">완료된 학습 모델
                      <select value={selectedNode.data.model_job_id || ''}
                        onChange={(event) => {const model=modelCatalog.find(row=>row.job_id===event.target.value);updateNodeData(selectedNode.id,model?modelScoreBinding(model):{model_job_id:undefined,score_spec:undefined,threshold:.5});}}
                        className="mt-1 w-full rounded border border-[#2B3547] bg-[#1A212E] px-2.5 py-1.5 text-[#F8FAFC]">
                        <option value="">모델 선택</option>
                        {selectedNode.data.model_job_id && !modelCatalog.some((model) => model.job_id === selectedNode.data.model_job_id) &&
                          <option value={selectedNode.data.model_job_id}>현재 지정된 모델 · {selectedNode.data.model_job_id}</option>}
                        {modelCatalog.filter((model) => model.task === (selectedNode.data.node_type === 'detection_crop' ? 'detection' : selectedNode.data.task)).map((model) =>
                          <option key={model.job_id} value={model.job_id}>{model.label}{model.threshold_settings?.optimal_threshold!==undefined?` · τ ${model.threshold_settings.optimal_threshold.toFixed(3)}`:''}{model.training_labelset_id?` · 라벨 ${model.training_labelset_id}`:''}{model.parent_job_id?' · 이어 학습':''}</option>)}
                      </select>
                    </label>
                    {(() => {const model=modelCatalog.find(m=>m.job_id===selectedNode.data.model_job_id);return model&&<div className="flex flex-wrap gap-1 text-xs text-teal-200">{Object.entries(model.threshold_settings||{}).map(([key,value])=><span key={key} className="rounded bg-teal-950 px-1">{({optimal_threshold:'추천 임계치',threshold:'학습 임계치',probability_threshold:'픽셀 확률',size_threshold:'크기 기준',min_defect_area_px:'최소 면적'})[key as 'threshold']} {value}</span>)}{model.training_labelset_id&&<span>라벨 {model.training_labelset_id}</span>}{model.parent_job_id&&<span title={model.parent_job_id}>부모 {model.parent_job_id}</span>}</div>;})()}
                    <p className="text-xs text-slate-400">{modelCatalogLoading ? '모델을 확인하는 중입니다.' : '현재 데이터와 작업이 검증된 완료 모델만 표시합니다.'}</p>
                    <details className="text-xs text-slate-400"><summary className="cursor-pointer hover:text-slate-200">고급: 작업 ID 직접 입력</summary>
                      <input type="text" value={selectedNode.data.model_job_id || ''}
                        onChange={(event) => updateNodeData(selectedNode.id, { model_job_id: event.target.value,score_spec:undefined,threshold:.5 })}
                        placeholder="job_..."
                        className="mt-1 w-full rounded border border-[#2B3547] bg-[#1A212E] px-2.5 py-1.5 text-[#F8FAFC]" />
                    </details>
                    <p className="text-amber-400 text-xs">
                      {selectedNode.data.node_type === 'inspection' && pipeline?.nodes.some((node) => node.data.node_type === 'fixed_roi')
                        ? '고정 ROI를 원본 픽셀 좌표로 잘라 모델 입력 크기에 맞춰 검사합니다. ROI가 이미지 밖이면 REVIEW로 남습니다.'
                        : selectedNode.data.node_type === 'inspection' && selectedNode.data.task === 'segmentation' && !pipeline?.nodes.some((node) => node.data.node_type === 'detection_crop')
                        ? '원본 해상도를 타일로 검사합니다. 타일 상한 초과 시 REVIEW로 표시하고, 결과 이미지는 축소 미리보기입니다.'
                        : selectedNode.data.node_type === 'detection_crop' && !pipeline?.nodes.some((node) => node.data.node_type === 'inspection')
                          ? '검출된 결함 객체가 있으면 NG, 없으면 OK로 판정합니다. 검출 모델 하나만 필요합니다.'
                        : pipeline?.nodes.some((node) => node.data.node_type === 'detection_crop')
                          ? '검출 모델과 검사 모델이 모두 필요합니다. 결과는 로컬 화면에만 표시됩니다.'
                          : '전체 이미지를 선택한 모델로 검사합니다. 결과는 로컬 화면에만 표시됩니다.'}
                    </p>
                  </div>
                )}

                {selectedNode.data.node_type === 'patch_split' && <div className="space-y-2">
                  {(['patch_width','patch_height','overlap'] as const).map((key) => <label key={key} className="block">{({patch_width:'패치 너비',patch_height:'패치 높이',overlap:'겹침 픽셀'})[key]}<input type="number" value={selectedNode.data.params?.[key] ?? (key==='overlap'?0:224)} onChange={(e) => updateNodeData(selectedNode.id,{params:{...selectedNode.data.params,[key]:Number(e.target.value)}})} className="ml-2 w-24 rounded bg-slate-800 p-1" /></label>)}
                </div>}
                {selectedNode.data.node_type === 'preprocess' && <div className="space-y-2">
                  <label className="block">영상 처리<select value={selectedNode.data.params?.operation || 'rotate'} onChange={(e) => updateNodeData(selectedNode.id,{task:e.target.value==='enhancement'?'enhancement':e.target.value==='learned_rotation'?'rotation':undefined,model_job_id:undefined,params:{operation:e.target.value,...(e.target.value==='align'?{target_angle_deg:0}:{angle_deg:0})}})} className="ml-2 rounded bg-slate-800 p-1">
                    <option value="rotate">회전</option><option value="align">방향 정렬</option><option value="improve">밝기·노이즈 개선</option><option value="enhancement">학습 모델 영상 개선</option><option value="learned_rotation">학습 모델 회전 보정</option><option value="fitted_roi">회전 검출 영역 맞춤·정렬</option>
                  </select></label>
                  {['rotate','align'].includes(selectedNode.data.params?.operation || 'rotate') && <label className="block">{selectedNode.data.params?.operation==='align'?'목표 방향':'회전 각도'}<input type="number" value={selectedNode.data.params?.[selectedNode.data.params?.operation==='align'?'target_angle_deg':'angle_deg'] ?? 0} onChange={(e) => updateNodeData(selectedNode.id,{params:{...selectedNode.data.params,[selectedNode.data.params?.operation==='align'?'target_angle_deg':'angle_deg']:Number(e.target.value)}})} className="ml-2 w-24 rounded bg-slate-800 p-1" /></label>}
                  {selectedNode.data.params?.operation==='improve' && <select aria-label="개선 방법" value={selectedNode.data.params.method || 'clahe'} onChange={(e) => updateNodeData(selectedNode.id,{params:{...selectedNode.data.params,method:e.target.value}})} className="rounded bg-slate-800 p-1"><option value="clahe">대비 개선</option><option value="denoise">노이즈 제거</option><option value="sharpen">선명도 개선</option></select>}
                  {selectedNode.data.params?.operation==='enhancement' && <select aria-label="영상 개선 모델" value={selectedNode.data.model_job_id || ''} onChange={(e) => updateNodeData(selectedNode.id,{model_job_id:e.target.value})} className="w-full rounded bg-slate-800 p-1"><option value="">영상 개선 모델 선택</option>{modelCatalog.filter((m) => m.task==='enhancement').map((m) => <option key={m.job_id} value={m.job_id}>{m.label}</option>)}</select>}
                  {selectedNode.data.params?.operation==='learned_rotation' && <select aria-label="회전 보정 모델" value={selectedNode.data.model_job_id || ''} onChange={(e)=>updateNodeData(selectedNode.id,{model_job_id:e.target.value})} className="w-full rounded bg-slate-800 p-1"><option value="">회전 모델 선택</option>{modelCatalog.filter(m=>m.task==='rotation').map(m=><option key={m.job_id} value={m.job_id}>{m.label}</option>)}</select>}
                  {selectedNode.data.params?.operation==='fitted_roi' && <p className="text-xs text-teal-200">회전 객체 검출의 ROI 연결 뒤에 배치하세요. 검출 다각형의 최소 면적 사각형을 원본 영상에서 정렬해 자릅니다.</p>}
                  <p className="text-xs text-slate-400">변환 결과와 원본 좌표를 검사 결과에서 확인할 수 있습니다. 방향 정렬은 회전 검출 결과의 방향을 사용합니다.</p>
                </div>}
                {selectedNode.data.task==='ocr' && <OCRRulesEditor key={selectedNode.id} params={selectedNode.data.params||{}} onChange={params=>updateNodeData(selectedNode.id,{params})}/>}
                {selectedNode.data.node_type==='measurement' && <MeasurementEditor key={selectedNode.id} params={selectedNode.data.params||{}} onChange={params=>updateNodeData(selectedNode.id,{params})} imagePath={selectedImage?.imagePath} sourcePreview={selectedImage?.thumbnailUrl?resolveApiUrl(selectedImage.thumbnailUrl):undefined} sourceSize={executionResult?.inspected_image_size}/>}
                {selectedNode.data.node_type==='inspection' && selectedNode.data.task==='anomaly' && <label className="block">이상 검사 방식<select value={selectedNode.data.params?.anomaly_mode || 'classification'} onChange={(e) => updateNodeData(selectedNode.id,{params:{...selectedNode.data.params,anomaly_mode:e.target.value}})} className="ml-2 rounded bg-slate-800 p-1"><option value="classification">이미지 점수 분류</option><option value="segmentation">결함 영역 검사·마스크</option></select></label>}
                {pipeline && (executionResult && !resultIsCurrent
                  ? <section role="status" aria-label="이전 버전 노드 근거" className="rounded border border-amber-700 bg-amber-950/30 p-3 text-xs text-amber-100">마지막 실행은 이전 버전의 검사 규칙으로 계산되어 현재 노드 설정과 함께 표시하지 않습니다. 검사 결과 탭에서 이전 결과를 보거나 다시 실행하세요.</section>
                  : <FlowNodeDebugger node={selectedNode} pipeline={pipeline} result={canvasResult} />)}

                {selectedNode.data.node_type === 'fixed_roi' && (() => {
                  const [x1, y1, x2, y2] = (selectedNode.data.params?.roi_bbox as number[] | undefined) || [0, 0, 512, 512];
                  const fields = [
                    { key: 'x' as const, label: 'X 시작', value: x1, min: 0 },
                    { key: 'y' as const, label: 'Y 시작', value: y1, min: 0 },
                    { key: 'width' as const, label: '너비', value: x2 - x1, min: 16 },
                    { key: 'height' as const, label: '높이', value: y2 - y1, min: 16 },
                  ];
                  return <div className="rounded border border-sky-800/70 bg-sky-950/20 p-3 space-y-2">
                    <div className="text-sky-200 font-bold">원본 이미지의 고정 ROI</div>
                    <ImageRoiEditor imagePath={selectedImage?.imagePath} preview={selectedImage?.thumbnailUrl} roi={[x1,y1,x2,y2]} disabled={isRunning||isSaving}
                      onEditingChange={setIsRoiEditing}
                      onChange={roi=>updateNodeData(selectedNode.id,{params:{...selectedNode.data.params,roi_bbox:roi}})}/>
                    <div className="grid grid-cols-2 gap-2">
                      {fields.map((field) => <FixedRoiCoordinateInput
                        key={`${selectedNode.id}:${field.key}`}
                        field={field.key} label={field.label} value={field.value} min={field.min}
                        onCommit={updateFixedRoiField}
                      />)}
                    </div>
                    <p className="text-xs leading-relaxed text-slate-400">
                      원본 기준 [{x1}, {y1}, {x2}, {y2}] · 이미지와 겹치지 않으면 REVIEW로 기록합니다.
                    </p>
                  </div>;
                })()}

                {shouldShowThreshold(selectedNode) && (
                  <div>
                    <div className="flex justify-between text-[#94A3B8] mb-1">
                      <span>결함 판정 임계치 · {selectedNode.data.score_spec?.unit || 'probability'}</span>
                      <span className="text-cyan-400 font-bold tabular-nums">
                        {(selectedNode.data.threshold ?? 0.5).toFixed(2)}
                      </span>
                    </div>
                    <input
                      type="number"
                      aria-label="결함 판정 임계치"
                      min="0"
                      max={selectedNode.data.score_spec?.domain === 'distance' ? undefined : 1}
                      step="any"
                      value={selectedNode.data.threshold ?? 0.5}
                      onChange={(e) => {
                        const value=e.target.valueAsNumber;
                        if(Number.isFinite(value)&&value>=0&&(selectedNode.data.score_spec?.domain==='distance'||value<=1))
                          updateNodeData(selectedNode.id, thresholdUpdate(selectedNode.data,value));
                      }}
                      className="w-full bg-[#1A212E] rounded p-2"
                    />
                  </div>
                )}

                {selectedNode.data.node_type === 'inspection' && selectedNode.data.task === 'segmentation' && (
                  <div>
                    <label className="text-[#94A3B8] block mb-1">
                      최소 결함 면적 (원본 이미지 px²)
                    </label>
                    <input
                      type="number"
                      min="1"
                      value={selectedNode.data.params?.min_defect_area_px ?? 8}
                      onChange={(e) => updateNodeData(selectedNode.id, {
                        params: { ...selectedNode.data.params, min_defect_area_px: Math.max(1, Number(e.target.value) || 1) },
                      })}
                      className="w-full bg-[#1A212E] border border-[#2B3547] rounded px-2.5 py-1.5 text-[#F8FAFC] tabular-nums focus:border-cyan-400 outline-none"
                    />
                  </div>
                )}

                {selectedNode.data.node_type === 'inspection' && selectedNode.data.task === 'segmentation' && <ClassRulesEditor key={selectedNode.id} classes={pipeline?nodeClassChoices(pipeline,selectedNode.id,modelCatalog):[]} params={selectedNode.data.params||{}} onChange={params=>updateNodeData(selectedNode.id,{params})}/>}

                {selectedNode.data.node_type === 'detection_crop' && selectedNode.data.crop_padding !== undefined && (
                  <div>
                    <label className="text-[#94A3B8] block mb-1">ROI 확장 패딩 (PX)</label>
                    <input
                      type="number"
                      value={selectedNode.data.crop_padding}
                      onChange={(e) =>
                        updateNodeData(selectedNode.id, {
                          crop_padding: Math.max(0, parseInt(e.target.value) || 0),
                        })
                      }
                      className="w-full bg-[#1A212E] border border-[#2B3547] rounded px-2.5 py-1.5 text-[#F8FAFC] tabular-nums focus:border-cyan-400 outline-none"
                    />
                  </div>
                )}

                {selectedNode.data.node_type === 'blob_measure' && (
                  <div className="space-y-3 rounded border border-teal-800/70 bg-teal-950/20 p-3">
                    <p className="text-teal-200 font-bold">분할 마스크의 연결된 결함 덩어리 측정</p>
                    <label className="block text-[#94A3B8]">최소 Blob 면적 (px²)
                      <input type="number" min="1" step="1" value={selectedNode.data.params?.min_blob_area_px ?? 1}
                        onChange={(event) => updateNodeData(selectedNode.id, {
                          params: { ...selectedNode.data.params, min_blob_area_px: Math.max(1, Math.trunc(Number(event.target.value) || 1)) },
                        })}
                        className="mt-1 w-full bg-[#1A212E] border border-[#2B3547] rounded px-2.5 py-1.5 text-[#F8FAFC] tabular-nums" />
                    </label>
                    <label className="block text-[#94A3B8]">NG 기준 Blob 개수
                      <input type="number" min="1" step="1" value={selectedNode.data.params?.min_blob_count_for_ng ?? 1}
                        onChange={(event) => updateNodeData(selectedNode.id, {
                          params: { ...selectedNode.data.params, min_blob_count_for_ng: Math.max(1, Math.trunc(Number(event.target.value) || 1)) },
                        })}
                        className="mt-1 w-full bg-[#1A212E] border border-[#2B3547] rounded px-2.5 py-1.5 text-[#F8FAFC] tabular-nums" />
                    </label>
                    <ClassRulesEditor key={selectedNode.id} blob classes={pipeline?nodeClassChoices(pipeline,selectedNode.id,modelCatalog):[]} params={selectedNode.data.params||{}} onChange={params=>updateNodeData(selectedNode.id,{params})}/>
                    <p className="text-xs text-slate-400">분할 검사 결과 하나를 입력받아 측정하고, 결과를 집계 또는 판정에 연결합니다.</p>
                  </div>
                )}

                {selectedNode.data.node_type === 'aggregate' && (
                  <div className="space-y-2 rounded border border-indigo-800/70 bg-indigo-950/20 p-3">
                    <label className="block text-[#94A3B8]">결과 집계 룰
                      <select value={selectedNode.data.rule || 'any_ng'}
                        onChange={(event) => updateNodeData(selectedNode.id, { rule: event.target.value })}
                        className="mt-1 w-full bg-[#1A212E] border border-[#2B3547] rounded px-2.5 py-1.5 text-indigo-200 font-bold">
                        <option value="any_ng">하나라도 NG</option>
                        <option value="all_ng">모두 NG</option>
                      </select>
                    </label>
                    <p className="text-xs text-slate-400">모델 또는 Blob 결과 1~8개를 모아 판정 노드에 직접 연결합니다. REVIEW 입력은 REVIEW로 유지됩니다.</p>
                  </div>
                )}

                {selectedNode.data.node_type === 'decision' && (
                  <div>
                    <label className="text-[#94A3B8] block mb-1">판정 룰 정책 (RULE POLICY)</label>
                    <select value={selectedNode.data.rule || 'any_defect_is_ng'}
                      onChange={(event) => updateNodeData(selectedNode.id, decisionRulePatch(selectedNode.data, event.target.value, pipeline?.nodes.filter(node=>['inspection','detection_crop'].includes(node.data.node_type))))}
                      className="w-full bg-[#1A212E] border border-[#2B3547] rounded px-2.5 py-1.5 text-amber-400 font-bold">
                      <option value="any_defect_is_ng">결함 하나라도 NG</option>
                      <option value="score_gt_threshold">최고 점수 임계치</option>
                      <option value="max_flaws_allowed">허용 NG 검사 영역 수</option>
                      <option value="aggregate_verdict">집계 결과 판정</option>
                    </select>
                    {selectedNode.data.rule === 'max_flaws_allowed' && (
                      <div className="mt-2">
                        <label className="text-[#94A3B8] block mb-1">허용 NG 검사 영역 수</label>
                        <input type="number" min="0" value={selectedNode.data.params?.max_flaws_allowed ?? 0}
                          onChange={(event) => updateNodeData(selectedNode.id, {
                            params: { ...selectedNode.data.params, max_flaws_allowed: Math.max(0, Number(event.target.value) || 0) },
                          })}
                          className="w-full bg-[#1A212E] border border-[#2B3547] rounded px-2.5 py-1.5 text-[#F8FAFC]" />
                      </div>
                    )}
                  </div>
                )}

                <div className="pt-4 border-t border-[#2B3547] text-xs text-[#94A3B8] space-y-1">
                  <div>NODE ID: <span className="text-slate-300 font-bold">{selectedNode.id}</span></div>
                  <div>TYPE: <span className="text-slate-300 font-bold">{selectedNode.data.node_type}</span></div>
                  <div>COORD: <span className="text-slate-300 tabular-nums">({selectedNode.position?.x ?? 0}, {selectedNode.position?.y ?? 0})</span></div>
                </div>
                {selectedNode.data.node_type !== 'input' && selectedNode.data.node_type !== 'decision' && (
                  <button onClick={deleteSelectedNode} disabled={isRunning || isSaving}
                    className="w-full px-3 py-2 rounded border border-rose-800 text-rose-300 hover:bg-rose-950 disabled:opacity-50 flex justify-center items-center gap-2">
                    <Trash2 className="w-3.5 h-3.5" /> 노드 삭제
                  </button>
                )}
              </div>
            ) : (
              <div className="rounded-md border border-dashed border-[#3B4B60] bg-[#192333]/50 p-4 text-xs text-slate-300">
                <p className="font-semibold text-slate-100">검사 흐름 설정</p>
                <ol className="mt-3 space-y-2 text-xs leading-relaxed text-slate-400">
                  <li>1. 그래프의 노드 또는 연결선을 선택합니다.</li>
                  <li>2. 사용할 모델과 판정 조건을 확인합니다.</li>
                  <li>3. 변경 사항을 저장하거나 이미지를 검사합니다.</li>
                </ol>
              </div>
            )}
          </div>
        </div>
      </section>
      <section role="tabpanel" id="flow-panel-test" aria-labelledby="flow-area-test" hidden={activeTab!=='test'}>
        <div className="shrink-0 flex flex-wrap items-center gap-3 border-b border-slate-700 p-3">
          {/* Industrial Solid Run Button (Zero Gradients / Zero Diffuse Shadows) */}
          <label className="flex items-center gap-1.5 text-xs text-slate-300">
            실행 위치
            <select aria-label="플로우 실행 위치" value={executionChoice}
              disabled={isRunning || isVerifyingAction}
              onChange={(event) => { setExecutionChoiceOverride(event.target.value as FlowExecutionChoice);
                useFlowchartStore.getState().resetExecution(); }}
              className="max-w-56 rounded border border-[#3B4B60] bg-[#152033] px-2 py-1.5 text-xs text-slate-100">
              <option value="selected_compute" disabled={!executionProfile}>
                선택 서버 · {executionProfile?.name || selectedProfileId || '서버 미선택'} (CUDA{executionProfile?.gpu_selector ? ` GPU ${executionProfile.gpu_selector}` : ''})
              </option>
              <option value="local_cpu">이 컴퓨터 · CPU</option>
              <option value="local_mps">이 컴퓨터 · Apple MPS</option>
              <option value="local_cuda">이 컴퓨터 · CUDA</option>
              <option value="model_compute">모델 학습 서버 · 기존 방식</option>
            </select>
          </label>
          <button type="button" onClick={handleRunToNode} disabled={!pipeline||!selectedNodeId||!selectedImage||isRunning||isLoading||!!graphError}
            className="rounded border border-sky-700 px-3 py-1.5 text-sky-200 disabled:opacity-50" title="선택한 실행 위치에서 이 노드와 그 상류만 실행합니다. 모델 학습 서버 자동 선택 방식에서는 이 컴퓨터 CPU로 디버그합니다.">선택 노드까지 실행</button>
          <button
            data-primary-action="true" onClick={handleRun}
            disabled={!selectedImage || !canVerifyGraph || !executionReady || isVerifyingAction || isRunning || isLoading || needsModel || !!graphError}
            title={!canVerifyGraph ? modelCheckMessage : graphError || (needsModel ? '각 모델 노드에 완료 모델을 선택하세요.' : undefined)}
            className="flex items-center space-x-2 px-4 py-1.5 bg-[#10B981] hover:bg-[#059669] active:bg-[#047857] text-[#0B0E14] font-black rounded border border-[#34D399] text-xs transition-colors cursor-pointer disabled:opacity-50"
          >
            <Play className={`w-3.5 h-3.5 fill-current ${isRunning ? 'animate-spin' : ''}`} />
            <span>
              {isRunning
                ? language === 'ko'
                  ? '검사 실행 중...'
                  : 'Inspecting...'
                : language === 'ko'
                ? '선택 이미지 검사'
                : 'Run Circuit'}
            </span>
          </button>
        </div>
        {!selectedImage&&<p className="px-4 py-2 text-sm text-amber-200">먼저 검사할 이미지를 선택하세요.</p>}
        {!executionResult&&<p className="p-4 text-sm text-slate-400">검사 결과가 없습니다. 선택 이미지 검사로 현재 초안을 확인하세요. 저장된 일괄 평가·승인과 구분됩니다.</p>}
        {selectedImage?.thumbnailUrl&&<details className="px-4 py-2"><summary className="cursor-pointer text-sm text-cyan-300">다음 검사 이미지 미리보기</summary><img alt="다음 검사 이미지 원본 보기" src={resolveApiUrl(selectedImage.thumbnailUrl)} className="max-h-64 object-contain"/></details>}
        <div className="flex min-h-[240px] shrink-0 flex-1 flex-col">
          {executionResult && !resultIsCurrent && <div role="status" aria-label="이전 버전 실행 결과" className="shrink-0 border-b border-amber-700 bg-amber-950/40 px-5 py-2 text-xs text-amber-100">
            플로우의 검사 규칙이 이 실행 뒤에 바뀌었습니다. 아래 결과는 이전 버전의 실행 결과이며 현재 플로우의 판정이 아닙니다. 다시 실행하세요.
          </div>}
          {executionResult && lastRunSource && <div role="status" className={`shrink-0 border-b px-5 py-2 text-xs ${lastRunSource.kind === 'draft'
            ? 'border-amber-700 bg-amber-950/30 text-amber-200'
            : 'border-sky-800 bg-sky-950/30 text-sky-200'}`}>
            {executionResult?.status==='partial'?'선택 노드 디버그 결과 · 전체 검사·배포 검증은 아직 실행하지 않았습니다.':flowRunSourceLabel(lastRunSource)}
            {executionResult.execution_target && <span className="ml-4">
              실행 위치: {(executionResult.execution_target === 'local' || (executionResult.execution_target === 'model_compute' && !executionResult.compute_profile_id)) ? '이 컴퓨터'
                : executionResult.compute_profile_name || computeProfiles.find((profile) => profile.id === executionResult.compute_profile_id)?.name
                  || executionResult.compute_profile_id || '모델 학습 서버'} · {executionResult.execution_device || '장치 확인 필요'}
            </span>}
          </div>}
          {executionResult?.crops.some(crop=>crop.score_spec) && <div className="px-5 py-2 text-xs text-cyan-200" aria-label="실행 점수 기준">
            {Array.from(new Set(executionResult.crops.filter(crop=>crop.score_spec).map(crop=>`${crop.score_spec!.unit} · 임계값 ${crop.score_spec!.threshold}${crop.score_basis==='saved_model_calibration_legacy_flow'?' · 기존 플로우에 저장된 모델 보정값 적용':''}`))).join(' / ')}
          </div>}
          <IntermediateCropDrawer onSelectNode={id=>{selectNode(id);setActiveTab('edit');}} />
        </div>
      </section>

      <FlowWorkspacePanel area={activeTab} versions={savedVersions} models={modelCatalog} onOpenImage={()=>setActiveTab('edit')}/>
      <div hidden={activeTab!=='release'}><WorkflowImpactPanel /></div>
      <section role="tabpanel" id="flow-panel-evaluate" aria-labelledby="flow-area-evaluate" hidden={activeTab!=='evaluate'}>
      <FlowEvaluationPanel embedded visible={activeTab==='evaluate'} onEvidence={setEvaluationReceipt} sourceDatasetPath={folderPath} savedVersionId={selectedVersionId || null}
        contextKey={scopeKey}
        onOpenImage={imagePath=>{const image=datasetImageReference(imagePath);return useProjectStore.getState().openImageForLabeling(image.imageId,imagePath);}}
        onInspectImage={imagePath=>{const image=datasetImageReference(imagePath);useFlowchartStore.getState().setSelectedImage({source:'dataset',imagePath,fileName:image.fileName,imageId:image.imageId,thumbnailUrl:image.thumbnailUrl});setActiveTab('test');}}/>
      </section>
      <section role="tabpanel" id="flow-panel-release" aria-labelledby="flow-area-release" hidden={activeTab!=='release'} className="space-y-4 p-4 text-sm">
        <h3 className="font-semibold">저장 버전에서 패키지·배포로</h3>
        <p>저장 {selectedVersionId||'미선택'} · 활성 {savedVersions.find(row=>row.is_active)?.version_id||'없음'}</p>
        <p className="break-all text-slate-400">저장 그래프 {selectedSaved?.pipeline_hash||'식별 근거 없음'}</p>
        <p>평가: {currentEvaluation?'현재 저장 버전의 유효 근거':'현재 저장 버전 근거 확인 필요'} · 승인: {approvalLabel}</p>
        <p className="text-slate-400">평가·승인·대상 적용은 별도 근거입니다. 이 화면의 조회는 승인이나 배포를 수행하지 않습니다.</p>
        {!exactSaved&&<p className="text-amber-200">현재 초안을 저장하거나 확인할 저장 버전을 여세요.</p>}
        <button className="workspace-button" disabled={!exactSaved||approvalBusy} onClick={()=>void checkApproval()}>{approvalBusy?'승인 근거 조회 중…':'이 저장 버전의 승인 근거 확인'}</button>
        {approvalError&&<p role="alert" className="text-amber-200">{approvalError}</p>}
        {currentApproval&&<details><summary className="cursor-pointer text-cyan-300">승인 조회 응답 · 모델/체크포인트/revision</summary><pre className="max-h-64 overflow-auto whitespace-pre-wrap break-all text-xs">{JSON.stringify(currentApproval,null,2)}</pre></details>}
        <p className="text-slate-400">배포: 대상 적용 응답 확인 필요. 다음 화면에서 패키지 생성·동등성 검증·대상 상태를 확인하세요.</p>
        <button data-primary-action="true" className="workspace-button workspace-button--primary" disabled={!exactSaved||isSaving||isRunning} aria-describedby={!exactSaved||isSaving||isRunning?'flow-release-reason':undefined} onClick={()=>setStep(6)}>패키지·배포로 이동</button>
        {(!exactSaved||isSaving||isRunning)&&<p id="flow-release-reason" className="text-amber-200">{isSaving||isRunning?'저장·실행이 끝난 뒤 이동하세요.':'저장된 버전을 연 상태에서만 패키지·배포로 이동합니다.'}</p>}
      </section>
      {recipe&&<FlowRecipeDialog key={recipe.token} preview={recipe.preview} models={modelCatalog} onClose={closeRecipe} onAdopt={adoptRecipe}/>}
      {/* Modals */}
      <ImagePickerModal isOpen={isImagePickerOpen} onClose={() => setImagePickerOpen(false)} />
      <CropDetailModal crop={inspectedCrop} onClose={() => setInspectedCrop(null)} />
    </div>
  );
};

export default FlowchartStudio;
