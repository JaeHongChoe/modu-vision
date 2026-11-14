import { useEffect, useState } from 'react';
import { ArrowLeft, ArrowRight } from 'lucide-react';
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

const STEP_NAMES: Record<WorkflowStep, readonly [string, string]> = {
  1: ['사진 준비', 'Prepare photos'], 2: ['라벨 달기', 'Label photos'], 3: ['모델 학습', 'Train a model'],
  4: ['결과 평가', 'Evaluate results'], 5: ['검사 흐름 만들기', 'Build inspection workflow'], 6: ['검사 실행', 'Run inspection'],
};

/** One place for the step's state: what is done, what blocks the next action, how to clear it, and the one button
 * that does it. Moving between steps is a quiet arrow at each end so the primary action stands alone. */
export const WizardFooter = () => {
  const {activeStep,setStep,language,viewMode}=useProjectStore();
  const {readiness,refresh}=useWorkflowReadiness();
  const [expanded,setExpanded]=useState(false);
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
  // The step's one number line (step 2: confirmed/drafts/remaining; 3: epochs and time; 4: accuracy and misses; 5: saved version; 6: inspected images).
  // It sits in an aria-live="off" span inside the status: a screen reader reads it with each state change, not on
  // every inspected image, epoch or clock step.
  const progress=progressLine(readiness.stage,readiness.progress,ko);
  const tone=readiness.status==='ready'?'border-emerald-500/60 bg-emerald-950/30 text-emerald-100':readiness.status==='blocked'?'border-amber-500/60 bg-amber-950/30 text-amber-100':'border-slate-600 bg-slate-900 text-slate-200';
  const statusColor=readiness.status==='ready'?'var(--workspace-success)':readiness.status==='blocked'?'var(--workspace-warning)':'var(--workspace-muted)';
  const previousButton=<button className={full?'workspace-button':'workspace-button workspace-button--quiet'} disabled={activeStep===1} onClick={()=>move(activeStep-1)} aria-label="이전 단계 보기" title={ko?(full?'이전 단계 (Alt+←)':'이전 세부 작업 (Alt+←)'):'Previous step (Alt+←)'}><ArrowLeft className="h-3 w-3"/></button>;
  const nextButton=<button className={full?'workspace-button':'workspace-button workspace-button--quiet'} disabled={activeStep===6} onClick={()=>move(activeStep+1)} aria-label="다음 단계 보기" title={ko?(full?'다음 단계 (Alt+→)':'다음 세부 작업 (Alt+→)'):'Next step (Alt+→)'}><ArrowRight className="h-3 w-3"/></button>;
  return <footer className="relative shrink-0 border-t border-slate-700 bg-[#0B0E14] px-4 py-2 text-xs text-slate-200" style={full?undefined:{background:'var(--workspace-surface)',borderColor:'var(--workspace-border)',color:'var(--workspace-text)'}}>
    {expanded&&<section aria-label="단계 준비도 상세" className="absolute bottom-full left-4 right-4 max-h-56 overflow-auto rounded-t-lg border border-slate-600 bg-[#142131] p-3 shadow-xl">
      {readiness.dataChangedJobId&&<div className="mb-2"><DataChangeNotice jobId={readiness.dataChangedJobId}/></div>}
      <p className="font-semibold">끝난 것</p><ul className="mt-1 list-disc pl-5">{readiness.completedInputs.length?readiness.completedInputs.map(item=><li key={item}>{item}</li>):<li>확인된 입력 없음</li>}</ul>
      <p className="mt-2 font-semibold">아직 필요한 것</p><ul className="mt-1 list-disc pl-5">{readiness.missingInputs.length?readiness.missingInputs.map(item=><li key={item}>{item}</li>):<li>안내된 다음 행동의 입력이 준비되었습니다.</li>}</ul>
      {panelErrors.length>0&&<><p className="mt-2 font-semibold text-red-200">패널 오류</p><ul className="mt-1 list-disc pl-5">{panelErrors.map(item=><li key={item.source}><span className="font-semibold">{item.source}</span>: {item.text}</li>)}</ul></>}
      <p className="mt-2 text-slate-400">학습·평가·승인·현장 적용은 각각의 실행 응답으로 확인합니다. 이전 결과와 다른 단계는 계속 볼 수 있습니다.</p>
      <div className="mt-2 flex flex-wrap gap-2"><button className="workspace-button" onClick={refresh}>현재 입력 다시 확인</button>
        {/* 5.3: from any step the assistant answers from this same picture (it is optional; nothing here needs it). */}
        <button className="workspace-button" onClick={()=>{setExpanded(false);useAssistantStore.getState().open();}}>{ko?'도우미에게 묻기':'Ask the assistant'}</button></div>
    </section>}
    {/* Full view keeps its numbered arrows. Simple view names the internal task beneath the four-goal rail. */}
    <div className="flex flex-wrap items-center gap-2">
      {full?<>{previousButton}<span className="font-mono text-slate-400">{activeStep}/6</span></>:<div className="flex shrink-0 items-center gap-1">
        {previousButton}<span className="whitespace-nowrap" style={{color:'var(--workspace-muted)'}}>{ko?'세부 작업':'Current step'} · <strong style={{color:'var(--workspace-text)'}}>{STEP_NAMES[activeStep][ko?0:1]}</strong></span>{nextButton}
      </div>}
      <div role="status" aria-live="polite" title={blocker?`${blocker} → ${actionLabel}${progress}`:progress?`${ko?`다음: ${actionLabel}`:`Next: ${actionLabel}`}${progress}`:undefined} className={`flex min-w-0 flex-1 items-center gap-x-3 rounded px-3 py-0.5 ${full?`border ${tone}`:''}`} style={full?undefined:{minWidth:'min(100%, 16rem)'}}>
        <span className="inline-flex items-center gap-1.5 whitespace-nowrap font-semibold" style={full?undefined:{color:statusColor}}>{!full&&<span className="h-1.5 w-1.5 shrink-0 rounded-full" style={{background:statusColor}} aria-hidden="true"/>}{labels[readiness.status]}</span>
        <span className="min-w-0 flex-1 truncate">{blocker?(!full||readiness.status==='ready'?blocker:`${blocker} → ${actionLabel}`):(ko?`다음: ${actionLabel}`:`Next: ${actionLabel}`)}{progress&&<span className="text-slate-400" aria-live="off">{progress}</span>}<span className="sr-only">{ko?` · 완료 입력 ${readiness.completedInputs.length}`:` · ${readiness.completedInputs.length} inputs done`}</span></span>
        <button className={`workspace-button ${full?'':'workspace-button--quiet '}min-h-0 whitespace-nowrap py-0.5`} aria-expanded={expanded} onClick={()=>setExpanded(value=>!value)}>{ko?(expanded?'자세히 닫기':'왜 그런가요?'):(expanded?'Close details':'Why?')}</button>
      </div>
      <button className={`workspace-button ${full?'':'workspace-button--quiet '}whitespace-nowrap`} aria-haspopup="dialog" onClick={()=>setAutoOpen(true)}>{ko?(autoRunning?`${AUTO_TITLE} 상태 보기`:`${AUTO_TITLE} 설정 열기`):(autoRunning?'View automatic progress':'Open automatic-run setup')}</button>
      <AsyncAction className="workspace-button--primary whitespace-nowrap" aria-label={`${ko?'권장 행동':'Suggested action'}: ${actionLabel}`} onClick={()=>void action()}>{actionLabel}</AsyncAction>
      {full&&nextButton}
    </div>
    {autoOpen&&<AutoProgressDialog onClose={()=>setAutoOpen(false)}/>}
  </footer>;
};
