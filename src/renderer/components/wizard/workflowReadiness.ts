export type WorkflowStep = 1 | 2 | 3 | 4 | 5 | 6;
export type RecordedInput = { job_id?: string; task?: string; checkpoint_state?: string; data_state?: string; state?: string; scope_matches?: boolean; version_id?: string; revision_id?: string; evaluation_id?: string; package_id?: string; model_job_ids?: string[] };
export type WorkflowImpact = { project_id?: string; source_dataset_path?: string; labelset_id?: string; models: RecordedInput[]; model_evaluations: RecordedInput[]; flows: RecordedInput[]; flow_evaluations: RecordedInput[]; approvals: RecordedInput[]; packages: RecordedInput[]; required_actions?: string[] };
export type WorkflowEvidence = { status: 'checking' | 'error' | 'ready'; error?: string; review?: { ready: boolean; counts: { eligible: number; pending?: number; approved?: number }; blockers?: string[] }; impact?: WorkflowImpact };
export type ReadinessAction = { stage: WorkflowStep; status: 'ready' | 'blocked' | 'checking' | 'unknown'; completedInputs: string[]; missingInputs: string[]; nextAction: { label: string; step: WorkflowStep; kind?: 'refresh' | 'details'; target?: string };
  /** A model whose training data changed since it was trained: the footer shows what changed and the ways back. */
  dataChangedJobId?: string;
  /** Step 2's count line: images a person labeled or adopted, images with a draft waiting, images with neither. */
  progress?: { confirmed: number; drafts: number; remaining: number } };
export type ReadinessContext = { stage: WorkflowStep; projectReady: boolean; sourceReady: boolean; totalImages: number; train: number; val: number; busy: boolean; task: string; modelJobId?: string | null; flowVersionId?: string | null; flowDirty: boolean; flowMatchesSaved?: boolean; splitState?: 'saved' | 'source_folders' | 'automatic' | 'stale' | null; populatedClasses?: number; classBlocker?: string | null; trainingStatus?: string; evidence: WorkflowEvidence; /** Images with machine drafts waiting for a person (step 2's draft review). */ drafts?: { pendingImages: number };
  /** The saved split leaves out labeled images (newly labeled or adopted), so it counts none until applied again. */ splitMissesLabels?: boolean;
  /** Models trained on exactly the current data (they can draft the unlabeled images). */ currentModels?: number;
  /** Step 2's label counts when the task has them (detection, segmentation): images in the source and those without a label. */ labels?: { total: number; unlabeled: number }};

/** Navigation advice only. A recorded result is never authorization to run work. */
export function getNextAction(input: ReadinessContext): ReadinessAction {
  const {stage,evidence}=input;
  const completedInputs: string[]=[];
  const result=(status:ReadinessAction['status'],missingInputs:string[],label:string,step:WorkflowStep,target?:string,kind?:'refresh'|'details'):ReadinessAction=>({stage,status,completedInputs,missingInputs,nextAction:{label,step,target,kind}});
  if(!input.projectReady)return result('blocked',['현재 프로젝트 선택 필요'],'프로젝트 만들기·열기',1,'workflow-project');
  completedInputs.push('현재 프로젝트 선택');
  if(input.busy)return result('checking',['프로젝트·데이터 변경 확인 중'],'데이터 변경 상태 보기',1);
  if(!input.sourceReady || input.totalImages<1)return result('blocked',['현재 원본의 이미지 가져오기 필요'],'데이터 가져오기 (1단계)',1,'workflow-data');
  completedInputs.push(`현재 원본 이미지 ${input.totalImages}장`);
  if(stage===1)return result('ready',[],'라벨 확인하기 (2단계)',2);
  if(evidence.status!=='ready')return result(evidence.status==='checking'?'checking':'unknown',[evidence.error || '현재 입력 근거 확인 중'],'준비도 다시 확인',stage,undefined,'refresh');
  if(!evidence.review)return result('unknown',['현재 입력의 검수·이력 근거 확인 필요'],'준비도 다시 확인',stage,undefined,'refresh');
  if(stage===2){
    // Drafts are not labels until a person adopts them, so they come first while any wait.
    const drafts=input.drafts?.pendingImages??0,approved=evidence.review.counts.approved;
    const progress=stepTwoProgress(input);
    const withProgress=(action:ReadinessAction):ReadinessAction=>progress?{...action,progress}:action;
    if(drafts>0)return withProgress(result(evidence.review.ready?'ready':'blocked',[`초안 ${drafts}장 검토 대기${approved?` · 검수 승인 ${approved}장`:''}`],'초안 검토',2,'draft-review-queue'));
    if(!evidence.review.ready)return withProgress(result('blocked',evidence.review.blockers?.length?evidence.review.blockers:['학습에 사용할 승인 이미지가 부족합니다.'],'라벨 작업 열기 (2단계)',2,'workflow-label-review'));
    completedInputs.push(`검수 기준을 통과한 이미지 ${evidence.review.counts.eligible}장`);
    // Images without a label and without a draft: a finished model can draft them, otherwise a person labels them.
    // Training is still possible, so the state stays ready; the action is what reduces the remaining work.
    if(progress&&progress.remaining>0){
      if((input.currentModels??0)>0)return withProgress(result('ready',[`라벨 없는 이미지 ${progress.remaining}장`],'초안 만들기',2,'draft-create'));
      return withProgress(result('ready',[`라벨 없는 이미지 ${progress.remaining}장`],'직접 라벨 달기',2,input.task==='classification'?'image-class-picker':'labeling-canvas'));
    }
    return withProgress(result('ready',[],'모델 학습 시작 (3단계)',3,'workflow-training'));
  }
  if(!evidence.impact)return result('unknown',['현재 입력의 검수·이력 근거 확인 필요'],'준비도 다시 확인',stage,undefined,'refresh');
  if(stage===3){
    if(!evidence.review.ready)return result('blocked',evidence.review.blockers?.length?evidence.review.blockers:['검수 기준을 통과한 학습 입력 필요'],'라벨 확인하기 (2단계)',2);
    completedInputs.push(`검수 기준 통과 ${evidence.review.counts.eligible}장`);
    if(input.task==='classification'&&input.populatedClasses!==undefined&&input.populatedClasses<2)return result('blocked',[`분류에는 이미지가 있는 클래스가 2개 이상 필요합니다 (지금 ${input.populatedClasses}개)`],'데이터 다시 확인 (1단계)',1,'workflow-data');
    // The per-class minimum and a class without training images (F-03); the text comes from the step-1 checklist rules.
    if(input.task==='classification'&&input.classBlocker)return result('blocked',[input.classBlocker],'데이터 다시 확인 (1단계)',1,'workflow-data');
    if(input.task==='classification'&&(input.splitState==='automatic'||input.splitState==='stale'))return result('blocked',[input.splitState==='stale'?'폴더 이미지가 저장한 분할과 달라졌습니다 (다시 나눠 저장 필요)':'분할이 저장되지 않았습니다 (자동 분할로는 4단계 평가를 할 수 없음)'],'분할 저장하기 (1단계)',1);
    if(input.splitMissesLabels)return result('blocked',['저장된 분할에 없는 라벨 이미지가 있습니다 (새로 라벨했거나 초안에서 채택한 이미지)'],'분할 다시 적용 (1단계)',1);
    if(input.train<1 || input.val<1)return result('blocked',[`저장된 학습·검증 분할 부족 (Train ${input.train} / Val ${input.val})`],'학습·시험 분할 확인 (1단계)',1);
    completedInputs.push(`저장 분할 Train ${input.train} / Val ${input.val}`);
    if(['queued','preparing','running','transferring','syncing','stopping'].includes(input.trainingStatus||''))return result('checking',['작업 완료 응답 확인 중'],'학습 진행 상태 보기',3,'workflow-training');
    if(input.trainingStatus==='completed')return result('ready',[],'학습 결과 평가 (4단계)',4);
    return result('ready',[],'학습 시작 버튼으로 이동 (3단계)',3,'workflow-training');
  }
  const currentModels=evidence.impact.models.filter(row=>row.task===input.task && row.scope_matches===true && row.checkpoint_state==='current' && row.data_state==='current');
  if(stage===4){
    const model=currentModels.find(row=>row.job_id===input.modelJobId);
    // With no model selected (a data edit clears the selection) and none current, explain the newest model of this task.
    const latest=currentModels.length?[]:evidence.impact.models.filter(row=>row.task===input.task).map(row=>row.job_id||'').sort().slice(-1);
    const changed=model?undefined:dataChangedModel(evidence.impact,input.task,input.modelJobId?[input.modelJobId]:latest);
    if(changed)return {...result('blocked',[`모델 ${changed}을(를) 학습한 뒤 데이터가 바뀌었습니다`],'바뀐 내용과 되돌리기',4,undefined,'details'),dataChangedJobId:changed};
    if(!model)return result('blocked',['선택 모델의 현재 체크포인트·데이터 근거 확인 필요'],'학습 모델 확인 (3단계)',3,'workflow-training');
    completedInputs.push(`현재 입력과 일치하는 모델 ${model.job_id}`);
    const evaluation=evidence.impact.model_evaluations.find(row=>row.job_id===model.job_id && row.state==='current' && row.evaluation_id);
    if(!evaluation)return result('unknown',['이 모델로 시험 이미지를 평가하면 5단계로 갈 수 있습니다 (승인은 필요하지 않습니다)'],'시험 이미지로 평가하기 (4단계)',4);
    completedInputs.push(`현재 평가 ${evaluation.evaluation_id}`);
    return result('ready',[],'검사 흐름 만들기 (5단계)',5);
  }
  if(!input.flowVersionId || input.flowDirty)return result('blocked',['현재 규칙의 저장된 플로우 버전 필요'],'검사 흐름 저장·평가 (5단계)',5,'flow-area-edit');
  if(input.flowMatchesSaved===false)return result('blocked',['편집 화면과 활성 저장 버전의 규칙이 다릅니다.'],'보이는 검사 흐름 저장·평가 (5단계)',5,'flow-area-edit');
  completedInputs.push(`활성 저장 플로우 ${input.flowVersionId}`);
  const flow=evidence.impact.flows.find(row=>row.version_id===input.flowVersionId && row.scope_matches===true && row.state==='current');
  const saved=flow?undefined:evidence.impact.flows.find(row=>row.version_id===input.flowVersionId && row.scope_matches===true && row.state==='revalidation_required');
  const changed=saved?dataChangedModel(evidence.impact,input.task,saved.model_job_ids||[]):undefined;
  if(changed)return {...result('blocked',[`검사 흐름의 모델 ${changed}을(를) 학습한 뒤 데이터가 바뀌었습니다`],'바뀐 내용과 되돌리기',stage,undefined,'details'),dataChangedJobId:changed};
  if(!flow)return result('blocked',['저장 버전과 현재 정답에 일치하는 전체 플로우 평가 필요'],'검사 흐름 평가 확인 (5단계)',5,'flow-area-evaluate');
  completedInputs.push('현재 입력과 일치하는 전체 플로우 평가');
  if(stage===5)return result('ready',[],'검사 결과 확인 (6단계)',6,'workflow-package');
  return result('unknown',['검사 결과까지 확인했습니다. 현장에 쓰려면 플로우 승인, 패키지 만들기, 대상 장치 확인을 차례로 진행하세요.'],'승인·패키지·대상 확인',6,'workflow-package');
}

/** Step 2's counts, when the task counts labels per image: confirmed = labeled by a person or adopted, drafts =
 *  waiting for a person, remaining = neither. Null for tasks without that count (a classification image always
 *  carries its folder class). */
export function stepTwoProgress(input:Pick<ReadinessContext,'labels'|'drafts'>):ReadinessAction['progress']|null{
  const labels=input.labels;
  if(!labels||!Number.isFinite(labels.total)||!Number.isFinite(labels.unlabeled))return null;
  const unlabeled=Math.max(0,Math.min(labels.total,labels.unlabeled)),drafts=Math.min(unlabeled,input.drafts?.pendingImages??0);
  return {confirmed:labels.total-unlabeled,drafts,remaining:unlabeled-drafts};
}

/** The first of ``jobIds`` whose checkpoint is unchanged but whose training data changed since training. */
function dataChangedModel(impact:WorkflowImpact,task:string,jobIds:string[]):string|undefined{
  return impact.models.find(row=>jobIds.includes(row.job_id||'') && row.scope_matches===true && row.checkpoint_state==='current' && row.data_state==='changed' && (!task||!row.task||row.task===task||jobIds.length>1))?.job_id;
}

export const recoveryStep = (action:string):WorkflowStep => ({review_data:2,train_candidate:3,evaluate_model:4,compare_fixed_cohort:4,approve_model:4,review_approval:4,evaluate_flow:5,export_package:6,verify_target:6} as Record<string,WorkflowStep>)[action] || 1;

/** Focus a mounted recovery section after the normal stage transition. */
/** The simple view folds secondary panels into <details>; a footer jump to a target inside one opens it first, or
 *  the user lands on a closed line with nothing in sight. */
export function openFoldsAround(element: Element) {
  for (let node = element.parentElement; node; node = node.parentElement) if (node.tagName === 'DETAILS') (node as HTMLDetailsElement).open = true;
}

export function focusWorkflowTarget(target?: string) {
  if(!target || typeof document==='undefined')return;
  let attempts=0;
  const focus=()=>{const label=target==='workflow-label-review'?'팀 작업 · 라벨 기준·검수':target==='workflow-data'?'데이터셋 폴더 열기':null;const element=document.getElementById(target)||(target==='workflow-project'?document.querySelector<HTMLElement>('[title="프로젝트 관리"]'):label?Array.from(document.querySelectorAll<HTMLButtonElement>('button')).find(button=>button.textContent?.trim()===label):null);if(element){openFoldsAround(element);if(element.getAttribute('role')==='tab'&&element.getAttribute('aria-selected')!=='true')element.click();element.scrollIntoView({block:'center'});element.focus();}else{
    // A target inside a closed popover names its opener with data-workflow-opens; open it, then look again.
    const opener=document.querySelector<HTMLElement>(`[data-workflow-opens="${target}"]`);if(opener&&opener.getAttribute('aria-expanded')==='false')opener.click();
    if(++attempts<12)requestAnimationFrame(focus);}};
  requestAnimationFrame(focus);
}
