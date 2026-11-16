import {useEffect, useRef, useState, useSyncExternalStore} from 'react';
import {getApiDiagnosticScope, getApiPersistenceIdentity, subscribeProjectContext} from '../../services/api';
import {GetiPlatformController, platformJobActive} from '../../services/getiPlatform';
import {useProjectStore} from '../../stores/useProjectStore';
import {useComputeStore} from '../../stores/useComputeStore';
import {useTrainingStore} from '../../stores/useTrainingStore';
import {inputPreparationScope, type FixedInputRecipe} from '../training/inputPreparation';
import {PlatformWorkspaceView} from './PlatformWorkspaceView';
import './platform-workspace.css';

export interface PlatformWorkspaceProps {onNavigate?: (step:number) => void | Promise<boolean | void>; onClose?: () => void}
const operationNames: Record<string,string> = {crop:'자르기',rotate90:'90도 단위 회전',brightness:'밝기',contrast:'대비',resize:'크기 맞추기',code:'숫자식 코드·FFT'};
type CurrentProject = NonNullable<ReturnType<typeof useProjectStore.getState>['project']>;

function LiveWorkspace({controller,project,recipe,onNavigate,onClose}:PlatformWorkspaceProps & {controller:GetiPlatformController;project:CurrentProject;recipe:FixedInputRecipe|null}) {
  const state = useSyncExternalStore(controller.subscribe,controller.getSnapshot,controller.getSnapshot);
  const epochRef = useRef<HTMLInputElement>(null), resultsRef = useRef<HTMLElement>(null);
  useEffect(() => {
    if (state.epochsError) epochRef.current?.focus();
  },[state.epochsError]);
  useEffect(() => {
    if (state.error || !state.status || state.loading || state.busy) return;
    if (state.status.runtime.state !== 'starting' && !state.status.jobs.some(platformJobActive)) return;
    const timer = setTimeout(() => {void controller.refresh();},3_000);
    return () => clearTimeout(timer);
  },[controller,state.status,state.error,state.loading,state.busy]);
  return <PlatformWorkspaceView state={state} controller={controller} hasSource={!!project.source_dataset_dir}
    projectName={project.name} task={project.task} preparationCount={recipe?.operations.length ?? 0}
    preparationSummary={recipe?.operations.length ? `저장한 입력 규칙 ${recipe.operations.length.toLocaleString('ko-KR')}개: ${recipe.operations.map(row => operationNames[row.op] || row.op).join(' → ')}` : undefined}
    onNavigate={onNavigate ? step => {void controller.navigate(step,onNavigate);} : undefined} onClose={onClose} epochRef={epochRef} resultsRef={resultsRef}
    onUseAdoptedLabels={() => {void controller.useAdoptedLabels(async id => {
      const owner = useProjectStore.getState();
      if (owner.isProjectBusy || owner.project?.id !== project.id || owner.project.source_dataset_dir !== project.source_dataset_dir || owner.task !== project.task) return false;
      const accepted = await owner.activateLabelset(id), current = useProjectStore.getState();
      if (!accepted || current.project?.id !== project.id || current.project.source_dataset_dir !== project.source_dataset_dir || current.project.active_labelset_id !== id) return false;
      await onNavigate?.(2);return true;
    });}}
    onShowResults={() => {resultsRef.current?.focus();resultsRef.current?.scrollIntoView({block:'nearest'});}}/>;
}
function ScopedWorkspace({scope,project,recipe,currentScope,onNavigate,onClose}:PlatformWorkspaceProps & {scope:string;project:CurrentProject;recipe:FixedInputRecipe|null;currentScope:()=>string}) {
  const [controller,setController] = useState<GetiPlatformController | null>(null);
  useEffect(() => {
    // Effect-owned construction also supports React StrictMode's setup/cleanup cycle.
    const owned = new GetiPlatformController({project,inputRecipe:recipe ? JSON.parse(JSON.stringify(recipe)) as FixedInputRecipe : null,isCurrent:() => scope === currentScope()});
    setController(owned);void owned.refresh();
    return () => owned.dispose();
  },[scope]);
  if (!controller) return <section className="workspace-section platform-workspace" data-testid="geti-platform-workspace" aria-busy="true"><h2>학습 작업대</h2><p role="status" className="platform-live">현재 프로젝트의 상태를 읽는 중…</p></section>;
  return <LiveWorkspace controller={controller} project={project} recipe={recipe} onNavigate={onNavigate} onClose={onClose}/>;
}

/** Uses the existing account/project and preparation stores; owns no parallel project selector. */
export function PlatformWorkspace({onNavigate,onClose}:PlatformWorkspaceProps) {
  const project = useProjectStore(state => state.project);
  const task = useProjectStore(state => state.task);
  const transportRevision = useComputeStore(state => state.transportRevision);
  const savedRecipe = useTrainingStore(state => state.inputRecipe);
  const savedRecipeScope = useTrainingStore(state => state.inputRecipeScope);
  const [contextRevision,setContextRevision] = useState(0);
  useEffect(() => subscribeProjectContext(() => setContextRevision(value => value + 1)),[]);
  const prepScope = inputPreparationScope({apiIdentity:getApiPersistenceIdentity(),projectId:project?.id || '',labelsetId:project?.active_labelset_id || 'default',source:project?.source_dataset_dir || '',task});
  const recipe = savedRecipeScope === prepScope ? savedRecipe : null;
  const scope = JSON.stringify([getApiDiagnosticScope(),project?.id,project?.project_dir,project?.source_dataset_dir,project?.active_labelset_id,task,transportRevision,contextRevision,recipe]);
  const scopeRef = useRef(scope);scopeRef.current=scope;
  if (!project) return <section className="workspace-section platform-workspace" data-testid="geti-platform-workspace"><h2>학습 작업대</h2><p>기존 계정에서 프로젝트를 선택한 뒤 학습 작업대를 여세요.</p>{onClose && <button type="button" className="workspace-button" onClick={onClose}>프로젝트 화면으로 돌아가기</button>}</section>;
  return <ScopedWorkspace key={scope} scope={scope} project={project} recipe={recipe} currentScope={() => scopeRef.current} onNavigate={onNavigate} onClose={onClose}/>;
}

export default PlatformWorkspace;
