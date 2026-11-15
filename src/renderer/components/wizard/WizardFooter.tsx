import { useEffect, useState } from 'react';
import { ArrowLeft, ArrowRight, Sparkles } from 'lucide-react';
import { useProjectStore } from '../../stores/useProjectStore';
import { AsyncAction } from '../common/AsyncAction';
import { useWorkflowReadiness } from './useWorkflowReadiness';
import { DataChangeNotice } from './DataChangeNotice';
import { focusWorkflowTarget, progressLine, type WorkflowStep } from './workflowReadiness';
import { openUnlabeledImage } from '../labeling/unlabeledImage';
import { assistPanelErrors, subscribeAssistContext } from '../../stores/assistContext';
import { AUTO_TITLE, AutoProgressDialog, useAutoProgressStore } from './AutoProgressDialog';
import { useAssistantStore } from '../../stores/useAssistantStore';
import { navigationActionLabel } from './beginnerJourney';
import { STEP_GUIDANCE_DATA } from '../../data/jargonDictionary';

const STEP_NAMES: Record<WorkflowStep, readonly [string, string]> = {
  1: ['사진 준비', 'Prepare photos'], 2: ['라벨 달기', 'Label photos'], 3: ['모델 학습', 'Train a model'],
  4: ['결과 평가', 'Evaluate results'], 5: ['검사 흐름 만들기', 'Build inspection workflow'], 6: ['검사 실행', 'Run inspection'],
};

/** One place for the step's state: what is done, what blocks the next action, how to clear it, and the one button
 * that does it. Moving between steps is a quiet arrow at each end so the primary action stands alone. */
export const WizardFooter = () => {
  const {activeStep,setStep,language,viewMode}=useProjectStore();
  const {readiness,refresh}=useWorkflowReadiness();
  const [expanded,setExpanded]=useState(()=>{try{return localStorage.getItem('vision-step-guidance')==='expanded';}catch{return false;}});
  useEffect(()=>{try{localStorage.setItem('vision-step-guidance',expanded?'expanded':'collapsed');}catch{}},[expanded]);
  // 원클릭 자동 진행 (5.2): a secondary entry beside the one primary action; the run goes on when its dialog closes.
  const [autoOpen,setAutoOpen]=useState(false);
  const autoRunning=useAutoProgressStore(state=>state.run?.state==='running');
  // Panels report their current error here; the details list shows them together with the step's state.
  const [panelErrors,setPanelErrors]=useState(assistPanelErrors);
  useEffect(()=>subscribeAssistContext(()=>setPanelErrors(assistPanelErrors())),[]);
  const move=(step:number)=>{if(step>=1&&step<=6)void setStep(step as WorkflowStep);};
  useEffect(()=>{
    const navigate=(event:KeyboardEvent)=>{
      const target=event.target as HTMLElement|null;
      if(target?.closest('input,textarea,select,[contenteditable="true"],[role="dialog"]') || document.querySelector('[role="dialog"]'))return;
      if((event.altKey&&event.key==='ArrowLeft') || (event.key==='['&&!event.ctrlKey&&!event.metaKey&&!event.altKey)){event.preventDefault();move(activeStep-1);}
      if((event.altKey&&event.key==='ArrowRight') || (event.key===']'&&!event.ctrlKey&&!event.metaKey&&!event.altKey)){event.preventDefault();move(activeStep+1);}
    };
    window.addEventListener('keydown',navigate);return()=>window.removeEventListener('keydown',navigate);
  },[activeStep]);
  const action=async()=>{
    if(readiness.nextAction.kind==='refresh'){refresh();return;}
    if(readiness.nextAction.kind==='details'){setExpanded(true);return;}
    // '직접 라벨 달기' on a labelled image goes to the first image still without a label (its edits are saved first).
    await setStep(readiness.nextAction.step);if(readiness.nextAction.target==='labeling-canvas')await openUnlabeledImage();focusWorkflowTarget(readiness.nextAction.target);
  };
  const ko=language==='ko';
  const full=viewMode==='full';
  const actionLabel=navigationActionLabel(readiness.nextAction,ko);
  const labels=ko?{ready:'입력 준비됨',blocked:'다음 할 일',checking:'확인 중',unknown:'확인 필요'}:{ready:'Inputs ready',blocked:'Next to do',checking:'Checking',unknown:'Needs a check'};
  const blocker=readiness.missingInputs[0];
  const guidance=STEP_GUIDANCE_DATA[activeStep];
  // The step's one number line (step 2: confirmed/drafts/remaining; 3: epochs and time; 4: accuracy and misses; 5: saved version; 6: inspected images).
  // It sits in an aria-live="off" span inside the status: a screen reader reads it with each state change, not on
  // every inspected image, epoch or clock step.
  const progress=progressLine(readiness.stage,readiness.progress,ko);
  const previousButton=<button className={full?'workspace-button':'workspace-button workspace-button--quiet'} disabled={activeStep===1} onClick={()=>move(activeStep-1)} aria-label="이전 단계 보기" title={ko?(full?'이전 단계 (Alt+←)':'이전 세부 작업 (Alt+←)'):'Previous step (Alt+←)'}><ArrowLeft className="h-3 w-3"/></button>;
  const nextButton=<button className={full?'workspace-button':'workspace-button workspace-button--quiet'} disabled={activeStep===6} onClick={()=>move(activeStep+1)} aria-label="다음 단계 보기" title={ko?(full?'다음 단계 (Alt+→)':'다음 세부 작업 (Alt+→)'):'Next step (Alt+→)'}><ArrowRight className="h-3 w-3"/></button>;
  return <aside className="modu-next-panel" aria-label={ko?'다음 할 일':'Next action'}>
    <div className="modu-next-panel__context">
      <span>{ko?'현재 작업':'Current task'}</span>
      <div className="modu-next-panel__step">{previousButton}<strong>{STEP_NAMES[activeStep][ko?0:1]}</strong>{nextButton}</div>
    </div>
    <div className="modu-next-panel__action" role="status" aria-live="polite" title={`${actionLabel}${progress}`}>
      <p className="modu-eyebrow">{ko?'다음 할 일':'Next action'}</p>
      <h2>{actionLabel}</h2>
      <p className="modu-next-panel__description">{blocker || (ko?'현재 입력이 준비됐습니다. 다음 화면에서 내용을 확인하고 진행하세요.':'The required inputs are ready. Review them in the next screen to continue.')}</p>
      <p className="modu-next-panel__status" data-state={readiness.status}>{labels[readiness.status]}<span className="sr-only">{ko?` · 완료 입력 ${readiness.completedInputs.length}`:` · ${readiness.completedInputs.length} inputs done`}</span></p>
      {progress&&<p className="modu-next-panel__progress" aria-live="off">{progress.replace(/^\s*·\s*/, '')}</p>}
    </div>
    <AsyncAction className="workspace-button--primary modu-next-panel__primary" aria-label={`${ko?'권장 행동':'Suggested action'}: ${actionLabel}`} onClick={()=>void action()}>{actionLabel}<ArrowRight className="h-4 w-4" aria-hidden="true"/></AsyncAction>
    <button className="workspace-button workspace-button--quiet modu-next-panel__detail-toggle" aria-expanded={expanded} onClick={()=>setExpanded(value=>!value)}>{ko?(expanded?'자세히 닫기':'왜 그런가요?'):(expanded?'Close details':'Why?')}</button>
    {panelErrors.length>0&&!expanded&&<p role="alert" className="modu-next-panel__error">{panelErrors[0].text}</p>}
    {expanded&&<section aria-label="단계 준비도 상세" className="modu-next-panel__details">
      {readiness.dataChangedJobId&&<DataChangeNotice jobId={readiness.dataChangedJobId}/>}
      <h3>{ko?'끝난 것':'Completed inputs'}</h3><ul>{readiness.completedInputs.length?readiness.completedInputs.map(item=><li key={item}>{item}</li>):<li>{ko?'확인된 입력 없음':'No inputs confirmed yet'}</li>}</ul>
      <h3>{ko?'아직 필요한 것':'Required inputs'}</h3><ul>{readiness.missingInputs.length?readiness.missingInputs.map(item=><li key={item}>{item}</li>):<li>{ko?'안내된 다음 행동의 입력이 준비되었습니다.':'Inputs for the next action are ready.'}</li>}</ul>
      {panelErrors.length>0&&<><h3>{ko?'패널 오류':'Panel errors'}</h3><ul>{panelErrors.map(item=><li key={item.source}><strong>{item.source}</strong>: {item.text}</li>)}</ul></>}
      {guidance&&<><h3>{ko?'이 단계의 목적':'Purpose of this step'}</h3><p>{ko?guidance.purposeKo:guidance.purposeEn}</p><h3>{ko?'다음으로 가기 전에':'Before continuing'}</h3><p>{ko?guidance.conditionKo:guidance.conditionEn}</p><h3>{ko?'도움말':'Practical tip'}</h3><p>{ko?guidance.tipKo:guidance.tipEn}</p></>}
      <p>{ko?'학습·평가·승인·현장 적용은 각각의 실행 결과로 확인합니다.':'Training, evaluation, approval and deployment each require their own results.'}</p>
      <button className="workspace-button" onClick={refresh}>{ko?'현재 입력 다시 확인':'Check inputs again'}</button>
    </section>}
    <div className="modu-next-panel__secondary">
      <button className="workspace-button workspace-button--quiet" aria-haspopup="dialog" onClick={()=>setAutoOpen(true)}>{ko?(autoRunning?`${AUTO_TITLE} 상태 보기`:`${AUTO_TITLE} 설정 열기`):(autoRunning?'View automatic progress':'Open automatic-run setup')}</button>
      {autoRunning&&<p role="status">{ko?'자동 진행 중 · 상태 화면에서 멈출 수 있습니다.':'Automatic progress is running. Open its status to stop.'}</p>}
    </div>
    <div className="modu-next-panel__assistant">
      <h3><Sparkles className="h-4 w-4" aria-hidden="true"/>{ko?'도우미와 함께':'Work with the assistant'}</h3>
      <p>{ko?'무엇을 해야 할지 모르겠다면 현재 작업을 바탕으로 도움을 받아보세요.':'Ask for help based on your current task when you are unsure what to do next.'}</p>
      <button className="workspace-button workspace-button--quiet" onClick={()=>useAssistantStore.getState().open()}>{ko?'도우미 열기':'Open assistant'}<ArrowRight className="h-4 w-4" aria-hidden="true"/></button>
    </div>
    {autoOpen&&<AutoProgressDialog onClose={()=>setAutoOpen(false)}/>}
  </aside>;
};
