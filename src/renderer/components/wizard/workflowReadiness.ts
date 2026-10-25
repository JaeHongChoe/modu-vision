export type WorkflowStep = 1 | 2 | 3 | 4 | 5 | 6;
export type RecordedInput = { job_id?: string; task?: string; checkpoint_state?: string; data_state?: string; state?: string; scope_matches?: boolean; version_id?: string; revision_id?: string; evaluation_id?: string; package_id?: string; model_job_ids?: string[] };
export type WorkflowImpact = { project_id?: string; source_dataset_path?: string; labelset_id?: string; models: RecordedInput[]; model_evaluations: RecordedInput[]; flows: RecordedInput[]; flow_evaluations: RecordedInput[]; approvals: RecordedInput[]; packages: RecordedInput[]; required_actions?: string[] };
export type WorkflowEvidence = { status: 'checking' | 'error' | 'ready'; error?: string; review?: { ready: boolean; counts: { eligible: number; pending?: number; approved?: number }; blockers?: string[] }; impact?: WorkflowImpact };
/** Step 2's count line: images a person labeled or adopted, images with a draft waiting, images with neither. */
export type StepTwoProgress = { confirmed: number; drafts: number; remaining: number };
/** Step 3: the running job's epoch counter and remaining time, or a finished model on the current data (completed). */
export type StepThreeProgress = { epoch: number; totalEpochs: number; etaSeconds: number | null; completed: boolean };
/** Step 4: the loaded evaluation, counted the way the evaluation screen counts it (image verdicts at the current threshold).
 *  accuracy and its 95% range low~high (the step-4 card's accuracyNote) are whole percents, all null when no image has an
 *  answer to score against; dataChanged marks counts measured before the model's training data changed; unconfirmed marks
 *  counts the impact report does not back (the selected model is not confirmed current, or no current evaluation of it). */
export type StepFourProgress = { samples: number; escapes: number; overkills: number; accuracy: number | null; low: number | null; high: number | null; dataChanged?: boolean; unconfirmed?: boolean };
/** Step 5: the active saved flow version, whether its whole-flow evaluation is current, and whether the editor differs from it (unsaved);
 *  reevaluate when its evaluation exists but must be redone (revalidation_required). */
export type StepFiveProgress = { versionId: string; evaluated: boolean; unsaved: boolean; reevaluate?: boolean };
/** Step 6: the batch inspection the step shows: images done so far (failed ones included), images in the run, images
 *  judged NG, and images that failed (present only when some did). */
export type StepSixProgress = { inspected: number; total: number; ng: number; failed?: number };
/** One number line per step; the footer tells them apart by the action's stage (step 2's shape stays exactly three keys). */
export type ReadinessProgress = StepTwoProgress | StepThreeProgress | StepFourProgress | StepFiveProgress | StepSixProgress;
export type ReadinessAction = { stage: WorkflowStep; status: 'ready' | 'blocked' | 'checking' | 'unknown'; completedInputs: string[]; missingInputs: string[]; nextAction: { label: string; step: WorkflowStep; kind?: 'refresh' | 'details'; target?: string };
  /** A model whose training data changed since it was trained: the footer shows what changed and the ways back. */
  dataChangedJobId?: string;
  /** The step's number line (see ReadinessProgress); absent when the step has nothing to count yet. */
  progress?: ReadinessProgress };
export type ReadinessContext = { stage: WorkflowStep; projectReady: boolean; sourceReady: boolean; totalImages: number; /** Every image in the source, labelled or not (a detection or segmentation folder starts with none labelled); the
   *  import check counts these, training counts totalImages. */ sourceImages?: number; train: number; val: number; busy: boolean; task: string; modelJobId?: string | null; flowVersionId?: string | null; flowDirty: boolean; flowMatchesSaved?: boolean; splitState?: 'saved' | 'source_folders' | 'automatic' | 'stale' | null; populatedClasses?: number; classBlocker?: string | null; trainingStatus?: string; evidence: WorkflowEvidence; /** Images with machine drafts waiting for a person (step 2's draft review). */ drafts?: { pendingImages: number };
  /** The saved split leaves out labeled images (newly labeled or adopted), so it counts none until applied again. */ splitMissesLabels?: boolean;
  /** Models trained on exactly the current data (they can draft the unlabeled images). */ currentModels?: number;
  /** Step 2's label counts when the task has them (detection, segmentation): images in the source and those without a label. */ labels?: { total: number; unlabeled: number };
  /** Step 3's run from the training store: epoch counter, remaining seconds (null: unknown) and whether it belongs to the current data. */ training?: { epoch: number; totalEpochs: number; etaSeconds: number | null; current: boolean } | null;
  /** Step 4's loaded evaluation, already counted per image with the accuracy range in whole percents (null: none loaded; accuracy null: no image has an answer). */ evaluation?: { samples: number; escapes: number; overkills: number; accuracy: number | null; low: number | null; high: number | null } | null;
  /** Step 4: the evaluation screen is still reading its results (no counts yet). */ evaluationLoading?: boolean;
  /** Step 4: why the evaluation screen could not read its results (null: no failure). */ evaluationError?: string | null;
  /** Step 6's batch inspection of the current source (null: none shown). */ inspection?: StepSixProgress | null;
  /** The editor differs from the active saved version: unsaved edits, or an autosaved draft that is not that version.
   *  Step 5's line only; the advice keeps reading flowDirty. */ flowEditing?: boolean };

const ACTIVE_TRAINING = ['queued', 'preparing', 'running', 'transferring', 'syncing', 'stopping'];
// Step 4's second 'why' line when the model missed test defects: the ways to fix it, and that step 5 stays one press away.
const MISSED_NEXT = '놓친 이미지를 하나씩 보고, 라벨이 틀렸으면 \'라벨 수정\'으로 고치세요. 라벨이 맞으면 필요한 학습 이미지를 더 모으고 다시 학습·평가하세요. 이대로 5단계로 가려면 오른쪽 끝 → 버튼을 누르세요.';

/** Navigation advice only. A recorded result is never authorization to run work. */
export function getNextAction(input: ReadinessContext): ReadinessAction {
  const {stage,evidence}=input;
  const completedInputs: string[]=[];
  // Steps 3–6 set their number line once and every later return carries it; step 2 attaches its own below.
  let stageProgress:ReadinessProgress|undefined;
  const result=(status:ReadinessAction['status'],missingInputs:string[],label:string,step:WorkflowStep,target?:string,kind?:'refresh'|'details'):ReadinessAction=>({stage,status,completedInputs,missingInputs,nextAction:{label,step,target,kind},...(stageProgress?{progress:stageProgress}:{})});
  if(!input.projectReady)return result('blocked',['현재 프로젝트 선택 필요'],'프로젝트 만들기·열기',1,'workflow-project');
  completedInputs.push('현재 프로젝트 선택');
  // Set before the busy and evidence checks: the evidence is re-read whenever a run ends or the data moves, and the line
  // stays through it (a running job goes straight to '모델 준비됨'), beside blocked and checking states alike.
  if(stage>=3)stageProgress=(stage===3?stepThreeProgress(input):stage===4?stepFourProgress(input):stage===5?stepFiveProgress(input):stepSixProgress(input))??undefined;
  if(input.busy)return result('checking',['프로젝트·데이터 변경 확인 중'],'데이터 변경 상태 보기',1);
  const imported=Math.max(input.totalImages,input.sourceImages??0);
  if(!input.sourceReady || imported<1)return result('blocked',['현재 원본의 이미지 가져오기 필요'],'데이터 가져오기 (1단계)',1,'workflow-data');
  completedInputs.push(`현재 원본 이미지 ${imported}장`);
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
    if(ACTIVE_TRAINING.includes(input.trainingStatus||''))return result('checking',['작업 완료 응답 확인 중'],'학습 진행 상태 보기',3,'workflow-training');
    if(input.trainingStatus==='failed')return result('blocked',['학습 작업이 실패했습니다. 패널 오류에서 원인과 다음 행동을 확인하세요.'],'학습 오류 확인',3,undefined,'details');
    if(input.trainingStatus==='completed')return result('ready',[],'학습 결과 평가 (4단계)',4);
    return result('ready',[],'학습 시작 버튼으로 이동 (3단계)',3,'workflow-training');
  }
  const currentModels=evidence.impact.models.filter(row=>row.task===input.task && row.scope_matches===true && row.checkpoint_state==='current' && row.data_state==='current');
  if(stage===4){
    const model=currentModels.find(row=>row.job_id===input.modelJobId);
    // With no model selected (a data edit clears the selection) and none current, explain the newest model of this task.
    const latest=currentModels.length?[]:evidence.impact.models.filter(row=>row.task===input.task).map(row=>row.job_id||'').sort().slice(-1);
    const changed=model?undefined:dataChangedModel(evidence.impact,input.task,input.modelJobId?[input.modelJobId]:latest);
    if(changed){
      // The loaded counts were measured before the change; the line says so instead of presenting them as current.
      if(stageProgress&&'samples' in stageProgress)stageProgress={...stageProgress,dataChanged:true};
      return {...result('blocked',[`모델 ${changed}을(를) 학습한 뒤 데이터가 바뀌었습니다`],'바뀐 내용과 되돌리기',4,undefined,'details'),dataChangedJobId:changed};
    }
    // Not backed by the report (no current model selected, or no current evaluation of it): a neutral word on the line.
    const unconfirmed=()=>{if(stageProgress&&'samples' in stageProgress)stageProgress={...stageProgress,unconfirmed:true};};
    if(!model){unconfirmed();return result('blocked',['선택 모델의 현재 체크포인트·데이터 근거 확인 필요'],'학습 모델 확인 (3단계)',3,'workflow-training');}
    completedInputs.push(`현재 입력과 일치하는 모델 ${model.job_id}`);
    const evaluation=evidence.impact.model_evaluations.find(row=>row.job_id===model.job_id && row.state==='current' && row.evaluation_id);
    if(!evaluation){unconfirmed();return result('unknown',['이 모델로 시험 이미지를 평가하면 5단계로 갈 수 있습니다 (승인은 필요하지 않습니다)'],'시험 이미지로 평가하기 (4단계)',4);}
    completedInputs.push(`현재 평가 ${evaluation.evaluation_id}`);
    // The step-4 screen reads the evaluation when it opens: until its counts are in, the step is not called done (an
    // automatic run would otherwise go on before the misses are known).
    if(!stageProgress&&input.evaluationLoading)return result('checking',['시험 이미지 평가 결과를 불러오는 중'],'준비도 다시 확인',4,undefined,'refresh');
    // Recorded but not on the screen (its read failed, or a selection on the screen cleared it): the misses are unknown,
    // so the step is not done. The action brings the screen's own 새로고침 button into view (it is not pressed); a failed
    // read is blocked (a person looks), results not read yet are unknown (an automatic run waits for them).
    if(!stageProgress)return input.evaluationError
      ?result('blocked',[`시험 이미지 평가 결과를 읽지 못했습니다: ${input.evaluationError}`],'새로고침 버튼으로 이동 (4단계)',4,'evaluation-refresh')
      :result('unknown',['시험 이미지 평가 결과가 화면에 없습니다'],'새로고침 버튼으로 이동 (4단계)',4,'evaluation-refresh');
    // 5.3-4: a model that missed test defects is looked at before step 5. The action opens the 미검 tab; step 5 stays one
    // press of the step arrow. False alarms alone keep the ready path: the recommended score trades them for no misses,
    // and the number line and the cards still show them.
    if(stageProgress&&'escapes' in stageProgress&&stageProgress.escapes>0)return result('blocked',['모델이 시험 이미지의 불량을 놓쳤습니다',MISSED_NEXT],'틀린 이미지 보기',4,'workflow-missed-defects');
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
export function stepTwoProgress(input:Pick<ReadinessContext,'labels'|'drafts'>):StepTwoProgress|null{
  const labels=input.labels;
  if(!labels||!Number.isFinite(labels.total)||!Number.isFinite(labels.unlabeled))return null;
  const unlabeled=Math.max(0,Math.min(labels.total,labels.unlabeled)),drafts=Math.min(unlabeled,input.drafts?.pendingImages??0);
  return {confirmed:labels.total-unlabeled,drafts,remaining:unlabeled-drafts};
}

const count=(value:unknown):number=>typeof value==='number'&&Number.isFinite(value)&&value>0?Math.floor(value):0;

/** Step 3's line: the live epoch counter while a job runs (once it knows its epoch total); '모델 준비됨' when the last
 *  run completed on the current data or the impact report holds a current model of this task; null otherwise (a model
 *  trained on older data is not ready). */
export function stepThreeProgress(input:Pick<ReadinessContext,'trainingStatus'|'training'|'evidence'|'task'>):StepThreeProgress|null{
  const run=input.training;
  if(ACTIVE_TRAINING.includes(input.trainingStatus||'')){
    const totalEpochs=count(run?.totalEpochs);
    if(!totalEpochs)return null;
    const eta=run?.etaSeconds;
    return {epoch:Math.min(totalEpochs,count(run?.epoch)),totalEpochs,etaSeconds:typeof eta==='number'&&Number.isFinite(eta)&&eta>0?eta:null,completed:false};
  }
  const currentRun=input.trainingStatus==='completed'&&run?.current===true;
  const currentModel=Boolean(input.evidence.impact?.models.some(row=>row.task===input.task&&row.scope_matches===true&&row.checkpoint_state==='current'&&row.data_state==='current'));
  if(!currentRun&&!currentModel)return null;
  const totalEpochs=currentRun?count(run?.totalEpochs):0;
  return {epoch:totalEpochs,totalEpochs,etaSeconds:null,completed:true};
}

/** Step 4's line: the loaded evaluation's counts, only when it holds images (the hook counts them with the evaluation
 *  screen's own verdict and range, so the footer and the result cards agree). The range shows only beside an accuracy. */
export function stepFourProgress(input:Pick<ReadinessContext,'evaluation'>):StepFourProgress|null{
  const evaluation=input.evaluation;
  if(!evaluation||!(count(evaluation.samples)>0))return null;
  const percent=(value:unknown):number|null=>typeof value==='number'&&Number.isFinite(value)?Math.max(0,Math.min(100,Math.round(value))):null;
  const accuracy=percent(evaluation.accuracy),low=percent(evaluation.low),high=percent(evaluation.high),ranged=accuracy!==null&&low!==null&&high!==null;
  return {samples:count(evaluation.samples),escapes:count(evaluation.escapes),overkills:count(evaluation.overkills),accuracy,low:ranged?low:null,high:ranged?high:null};
}

/** Step 5's line: the active saved version, whether the impact report records a current whole-flow evaluation of it
 *  (or one that must be redone), and whether the editor differs from it (unsaved edits, or an autosaved draft that is not
 *  that version). Null without a saved version (the blocker already says so). */
export function stepFiveProgress(input:Pick<ReadinessContext,'flowVersionId'|'flowDirty'|'flowEditing'|'evidence'>):StepFiveProgress|null{
  const versionId=input.flowVersionId;
  if(!versionId)return null;
  const rows=input.evidence.impact?.flows.filter(row=>row.version_id===versionId&&row.scope_matches===true)??[];
  const evaluated=rows.some(row=>row.state==='current'),reevaluate=!evaluated&&rows.some(row=>row.state==='revalidation_required');
  return {versionId,evaluated,unsaved:Boolean(input.flowDirty||input.flowEditing),...(reevaluate?{reevaluate:true}:{})};
}

/** Step 6's line: the batch inspection shown for the current source, once it has images. */
export function stepSixProgress(input:Pick<ReadinessContext,'inspection'>):StepSixProgress|null{
  const inspection=input.inspection;
  if(!inspection||!(count(inspection.total)>0))return null;
  const total=count(inspection.total),inspected=Math.min(total,count(inspection.inspected)),failed=Math.min(inspected,count(inspection.failed));
  return {inspected,total,ng:count(inspection.ng),...(failed?{failed}:{})};
}

/** Seconds as mm:ss (minutes keep growing past an hour), like the training screen's clock. */
export function formatEta(seconds:number):string{
  const whole=Math.max(0,Math.floor(seconds));
  return `${String(Math.floor(whole/60)).padStart(2,'0')}:${String(whole%60).padStart(2,'0')}`;
}

/** The footer's number line for the step, with its leading separator; empty when the step has none or the shape
 *  belongs to another step. */
export function progressLine(stage:WorkflowStep,progress:ReadinessProgress|null|undefined,ko:boolean):string{
  if(!progress)return '';
  if(stage===2&&'confirmed' in progress)return ko?` · 확정 ${progress.confirmed}장 / 초안 ${progress.drafts}장 / 남은 ${progress.remaining}장`:` · ${progress.confirmed} confirmed / ${progress.drafts} drafts / ${progress.remaining} left`;
  if(stage===3&&'epoch' in progress){
    if(progress.completed)return ko?' · 모델 준비됨':' · model ready';
    const eta=progress.etaSeconds===null?'':ko?` · 남은 시간 ${formatEta(progress.etaSeconds)}`:` · ${formatEta(progress.etaSeconds)} left`;
    return (ko?` · 학습 ${progress.epoch}/${progress.totalEpochs}회`:` · epoch ${progress.epoch}/${progress.totalEpochs}`)+eta;
  }
  if(stage===4&&'escapes' in progress){
    const before=progress.dataChanged?(ko?'바뀌기 전 데이터 기준: ':'on the data before it changed: '):progress.unconfirmed?(ko?'확인 전 결과: ':'unconfirmed: '):'';
    // No image had an answer to compare (all REVIEW): the image count only, never a false 0% or 0 misses.
    if(progress.accuracy===null)return ko?` · ${before}시험 ${progress.samples}장 · 정답이 없어 정확도를 셀 수 없음`:` · ${before}${progress.samples} test images · no answers to score against`;
    // The card's "could be" wording, shortened: the true accuracy is likely, not certainly, inside the range.
    const range=progress.low===null||progress.high===null?'':ko?` (${progress.low}~${progress.high}% 사이일 수 있음)`:` (could be ${progress.low}~${progress.high}%)`;
    return ko?` · ${before}정확도 ${progress.accuracy}%${range} · 놓친 불량 ${progress.escapes}장 · 잘못 잡은 정상 ${progress.overkills}장`:` · ${before}${progress.accuracy}% right${range} · ${progress.escapes} missed defects · ${progress.overkills} false alarms`;
  }
  if(stage===5&&'versionId' in progress){
    const state=progress.unsaved?(ko?'편집 중':'editing'):progress.evaluated?(ko?'평가 완료':'evaluated'):progress.reevaluate?(ko?'평가 다시 필요':'re-evaluation needed'):(ko?'평가 전':'not evaluated');
    return ko?` · 저장 버전 ${progress.versionId.slice(0,8)} · ${state}`:` · saved version ${progress.versionId.slice(0,8)} · ${state}`;
  }
  if(stage===6&&'inspected' in progress){
    const inspected=progress.inspected<progress.total?`${progress.inspected}/${progress.total}`:`${progress.inspected}`;
    // The panel's own word for images the run could not judge ('실패' filter).
    const failed=progress.failed?(ko?` · 실패 ${progress.failed}장`:` · ${progress.failed} failed`):'';
    return (ko?` · 검사 ${inspected}장 · 불량 ${progress.ng}장`:` · ${inspected} inspected · ${progress.ng} NG`)+failed;
  }
  return '';
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
  // Every target is an element id (the project button, the dataset folder button and the team row's button carry theirs),
  // so the jump does not depend on the interface language. A closed opener of a dialog is pressed and the dialog keeps
  // the focus it takes; any other target is brought into view and focused.
  const focus=()=>{const element=document.getElementById(target);if(element){openFoldsAround(element);if(element.getAttribute('role')==='tab'&&element.getAttribute('aria-selected')!=='true')element.click();if(element.hasAttribute('data-workflow-open-on-focus')&&element.getAttribute('aria-expanded')==='false'){const dialog=element.getAttribute('aria-haspopup')==='dialog';element.click();if(dialog)return;}element.scrollIntoView({block:'center'});element.focus();
      // A target that takes no focus (the labeling work area has no tabIndex, so a canvas click keeps BODY/CANVAS as the
      // key target): the pressed button lets go of the keyboard, so Enter or Space does not press it again.
      if(document.activeElement!==element&&document.activeElement instanceof HTMLElement)document.activeElement.blur();}else{
    // A target inside a closed popover names its opener with data-workflow-opens; open it, then look again.
    const opener=document.querySelector<HTMLElement>(`[data-workflow-opens="${target}"]`);if(opener&&opener.getAttribute('aria-expanded')==='false')opener.click();
    if(++attempts<12)requestAnimationFrame(focus);}};
  requestAnimationFrame(focus);
}
