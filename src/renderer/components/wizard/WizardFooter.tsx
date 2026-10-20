import { useEffect, useState } from 'react';
import { ArrowLeft, ArrowRight } from 'lucide-react';
import { useProjectStore } from '../../stores/useProjectStore';
import { AsyncAction } from '../common/AsyncAction';
import { useWorkflowReadiness } from './useWorkflowReadiness';
import { DataChangeNotice } from './DataChangeNotice';
import { focusWorkflowTarget, type WorkflowStep } from './workflowReadiness';

/** One place for the step's state: what is done, what blocks the next action, how to clear it, and the one button
 * that does it. Moving between steps is a quiet arrow at each end so the primary action stands alone. */
export const WizardFooter = () => {
  const {activeStep,setStep,language}=useProjectStore();
  const {readiness,refresh}=useWorkflowReadiness();
  const [expanded,setExpanded]=useState(false);
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
    await setStep(readiness.nextAction.step);focusWorkflowTarget(readiness.nextAction.target);
  };
  const ko=language==='ko';
  const labels=ko?{ready:'입력 준비됨',blocked:'다음 할 일',checking:'확인 중',unknown:'확인 필요'}:{ready:'Inputs ready',blocked:'Next to do',checking:'Checking',unknown:'Needs a check'};
  const blocker=readiness.missingInputs[0];
  const tone=readiness.status==='ready'?'border-emerald-500/60 bg-emerald-950/30 text-emerald-100':readiness.status==='blocked'?'border-amber-500/60 bg-amber-950/30 text-amber-100':'border-slate-600 bg-slate-900 text-slate-200';
  return <footer className="relative shrink-0 border-t border-slate-700 bg-[#0B0E14] px-4 py-2 text-xs text-slate-200">
    {expanded&&<section aria-label="단계 준비도 상세" className="absolute bottom-full left-4 right-4 max-h-56 overflow-auto rounded-t-lg border border-slate-600 bg-[#142131] p-3 shadow-xl">
      {readiness.dataChangedJobId&&<div className="mb-2"><DataChangeNotice jobId={readiness.dataChangedJobId}/></div>}
      <p className="font-semibold">끝난 것</p><ul className="mt-1 list-disc pl-5">{readiness.completedInputs.length?readiness.completedInputs.map(item=><li key={item}>{item}</li>):<li>확인된 입력 없음</li>}</ul>
      <p className="mt-2 font-semibold">아직 필요한 것</p><ul className="mt-1 list-disc pl-5">{readiness.missingInputs.length?readiness.missingInputs.map(item=><li key={item}>{item}</li>):<li>안내된 다음 행동의 입력이 준비되었습니다.</li>}</ul>
      <p className="mt-2 text-slate-400">학습·평가·승인·현장 적용은 각각의 실행 응답으로 확인합니다. 이전 결과와 다른 단계는 계속 볼 수 있습니다.</p>
      <button className="workspace-button mt-2" onClick={refresh}>현재 입력 다시 확인</button>
    </section>}
    {/* One line at 1280px and wider: the status text truncates (its title holds the full text) so the canvas keeps its height. */}
    <div className="flex items-center gap-2 whitespace-nowrap">
      <button className="workspace-button" disabled={activeStep===1} onClick={()=>move(activeStep-1)} aria-label="이전 단계 보기" title={ko?'이전 단계 (Alt+←)':'Previous step (Alt+←)'}><ArrowLeft className="h-3 w-3"/></button>
      <span className="font-mono text-slate-400">{activeStep}/6</span>
      <div role="status" aria-live="polite" title={blocker?`${blocker} → ${readiness.nextAction.label}`:undefined} className={`flex min-w-0 flex-1 items-center gap-x-3 rounded border px-3 py-0.5 ${tone}`}>
        <span className="whitespace-nowrap font-semibold">{labels[readiness.status]}</span>
        <span className="min-w-0 flex-1 truncate">{blocker?(readiness.status==='ready'?blocker:`${blocker} → ${readiness.nextAction.label}`):(ko?`다음: ${readiness.nextAction.label}`:`Next: ${readiness.nextAction.label}`)}<span className="sr-only">{ko?` · 완료 입력 ${readiness.completedInputs.length}`:` · ${readiness.completedInputs.length} inputs done`}</span></span>
        <button className="workspace-button min-h-0 whitespace-nowrap py-0.5" aria-expanded={expanded} onClick={()=>setExpanded(value=>!value)}>{ko?(expanded?'자세히 닫기':'왜 그런가요?'):(expanded?'Close details':'Why?')}</button>
      </div>
      <AsyncAction className="workspace-button--primary whitespace-nowrap" aria-label={`권장 행동: ${readiness.nextAction.label}`} onClick={()=>void action()}>{readiness.nextAction.label}</AsyncAction>
      <button className="workspace-button" disabled={activeStep===6} onClick={()=>move(activeStep+1)} aria-label="다음 단계 보기" title={ko?'다음 단계 (Alt+→)':'Next step (Alt+→)'}><ArrowRight className="h-3 w-3"/></button>
    </div>
  </footer>;
};
