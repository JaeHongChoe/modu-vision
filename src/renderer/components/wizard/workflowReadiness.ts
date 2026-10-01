export type WorkflowStep = 1 | 2 | 3 | 4 | 5 | 6;
export type RecordedInput = { job_id?: string; task?: string; checkpoint_state?: string; data_state?: string; state?: string; scope_matches?: boolean; version_id?: string; revision_id?: string; evaluation_id?: string; package_id?: string };
export type WorkflowImpact = { project_id?: string; source_dataset_path?: string; labelset_id?: string; models: RecordedInput[]; model_evaluations: RecordedInput[]; flows: RecordedInput[]; flow_evaluations: RecordedInput[]; approvals: RecordedInput[]; packages: RecordedInput[]; required_actions?: string[] };
export type WorkflowEvidence = { status: 'checking' | 'error' | 'ready'; error?: string; review?: { ready: boolean; counts: { eligible: number; pending?: number }; blockers?: string[] }; impact?: WorkflowImpact };
export type ReadinessAction = { stage: WorkflowStep; status: 'ready' | 'blocked' | 'checking' | 'unknown'; completedInputs: string[]; missingInputs: string[]; nextAction: { label: string; step: WorkflowStep; kind?: 'refresh'; target?: string } };
export type ReadinessContext = { stage: WorkflowStep; projectReady: boolean; sourceReady: boolean; totalImages: number; train: number; val: number; busy: boolean; task: string; modelJobId?: string | null; flowVersionId?: string | null; flowDirty: boolean; flowMatchesSaved?: boolean; trainingStatus?: string; evidence: WorkflowEvidence };

/** Navigation advice only. A recorded result is never authorization to run work. */
export function getNextAction(input: ReadinessContext): ReadinessAction {
  const {stage,evidence}=input;
  const completedInputs: string[]=[];
  const result=(status:ReadinessAction['status'],missingInputs:string[],label:string,step:WorkflowStep,target?:string,kind?:'refresh'):ReadinessAction=>({stage,status,completedInputs,missingInputs,nextAction:{label,step,target,kind}});
  if(!input.projectReady)return result('blocked',['현재 프로젝트 선택 필요'],'프로젝트·데이터 선택',1,'workflow-project');
  completedInputs.push('현재 프로젝트 선택');
  if(input.busy)return result('checking',['프로젝트·데이터 변경 확인 중'],'데이터 변경 상태 보기',1);
  if(!input.sourceReady || input.totalImages<1)return result('blocked',['현재 원본의 이미지 가져오기 필요'],'데이터 원본 선택으로 이동',1,'workflow-data');
  completedInputs.push(`현재 원본 이미지 ${input.totalImages}장`);
  if(stage===1)return result('ready',[],'라벨·검수 확인',2);
  if(evidence.status!=='ready')return result(evidence.status==='checking'?'checking':'unknown',[evidence.error || '현재 입력 근거 확인 중'],'준비도 다시 확인',stage,undefined,'refresh');
  if(!evidence.review || !evidence.impact)return result('unknown',['현재 입력의 검수·이력 근거 확인 필요'],'준비도 다시 확인',stage,undefined,'refresh');
  if(stage===2){
    if(!evidence.review.ready)return result('blocked',evidence.review.blockers?.length?evidence.review.blockers:['학습에 사용할 승인 이미지가 부족합니다.'],'라벨·검수 작업 열기',2,'workflow-label-review');
    completedInputs.push(`검수 기준을 통과한 이미지 ${evidence.review.counts.eligible}장`);
    return result('ready',[],'학습 입력 확인',3,'workflow-training');
  }
  if(stage===3){
    if(!evidence.review.ready)return result('blocked',evidence.review.blockers?.length?evidence.review.blockers:['검수 기준을 통과한 학습 입력 필요'],'라벨·검수 확인',2);
    completedInputs.push(`검수 기준 통과 ${evidence.review.counts.eligible}장`);
    if(input.train<1 || input.val<1)return result('blocked',[`저장된 학습·검증 분할 부족 (Train ${input.train} / Val ${input.val})`],'데이터 분할 확인',1);
    completedInputs.push(`저장 분할 Train ${input.train} / Val ${input.val}`);
    if(['queued','preparing','running','transferring','syncing','stopping'].includes(input.trainingStatus||''))return result('checking',['작업 완료 응답 확인 중'],'학습 진행 상태 보기',3,'workflow-training');
    return result('ready',[],'실행 자원·학습 설정 확인',3,'workflow-training');
  }
  const currentModels=evidence.impact.models.filter(row=>row.task===input.task && row.scope_matches===true && row.checkpoint_state==='current' && row.data_state==='current');
  if(stage===4){
    const model=currentModels.find(row=>row.job_id===input.modelJobId);
    if(!model)return result('blocked',['선택 모델의 현재 체크포인트·데이터 근거 확인 필요'],'모델 학습·선택 확인',3,'workflow-training');
    completedInputs.push(`현재 입력과 일치하는 모델 ${model.job_id}`);
    const evaluation=evidence.impact.model_evaluations.find(row=>row.job_id===model.job_id && row.state==='current' && row.evaluation_id);
    if(!evaluation)return result('unknown',['저장 평가의 현재 입력 일치 여부와 비교·승인 확인 필요'],'평가·승인 근거 확인',4,'workflow-approval');
    completedInputs.push(`현재 평가 ${evaluation.evaluation_id}`);
    return result('ready',[],'플로우 구성 확인',5);
  }
  if(!input.flowVersionId || input.flowDirty)return result('blocked',['현재 규칙의 저장된 플로우 버전 필요'],'플로우 저장·평가',5);
  if(input.flowMatchesSaved===false)return result('blocked',['편집 화면과 활성 저장 버전의 규칙이 다릅니다.'],'표시된 플로우 저장·평가',5);
  completedInputs.push(`활성 저장 플로우 ${input.flowVersionId}`);
  const flow=evidence.impact.flows.find(row=>row.version_id===input.flowVersionId && row.scope_matches===true && row.state==='current');
  if(!flow)return result('blocked',['저장 버전과 현재 정답에 일치하는 전체 플로우 평가 필요'],'전체 플로우 평가 확인',5);
  completedInputs.push('현재 입력과 일치하는 전체 플로우 평가');
  if(stage===5)return result('ready',[],'승인·패키지 입력 확인',6,'workflow-package');
  return result('unknown',['정확한 승인 revision·패키지 검증·대상 응답을 별도로 확인하세요.'],'승인·패키지·대상 확인',6,'workflow-package');
}

export const recoveryStep = (action:string):WorkflowStep => ({review_data:2,train_candidate:3,evaluate_model:4,compare_fixed_cohort:4,approve_model:4,review_approval:4,evaluate_flow:5,export_package:6,verify_target:6} as Record<string,WorkflowStep>)[action] || 1;

/** Focus a mounted recovery section after the normal stage transition. */
export function focusWorkflowTarget(target?: string) {
  if(!target || typeof document==='undefined')return;
  let attempts=0;
  const focus=()=>{const label=target==='workflow-label-review'?'팀 작업 · 라벨 기준·검수':target==='workflow-data'?'데이터셋 폴더 열기':null;const element=document.getElementById(target)||(target==='workflow-project'?document.querySelector<HTMLElement>('[title="프로젝트 관리"]'):label?Array.from(document.querySelectorAll<HTMLButtonElement>('button')).find(button=>button.textContent?.trim()===label):null);if(element){element.scrollIntoView({block:'center'});element.focus();}else if(++attempts<12)requestAnimationFrame(focus);};
  requestAnimationFrame(focus);
}
