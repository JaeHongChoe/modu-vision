import { useEffect, useState } from 'react';
import { ArrowLeft, ArrowRight } from 'lucide-react';
import { useProjectStore } from '../../stores/useProjectStore';
import { AsyncAction } from '../common/AsyncAction';
import { StatusBadge } from '../common/StatusBadge';
import { useWorkflowReadiness } from './useWorkflowReadiness';
import { focusWorkflowTarget, type WorkflowStep } from './workflowReadiness';

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
    await setStep(readiness.nextAction.step);focusWorkflowTarget(readiness.nextAction.target);
  };
  const labels={ready:'입력 준비됨',blocked:'부족한 입력',checking:'확인 중',unknown:'확인 필요'};
  labels.ready=language==='ko'?'입력 준비됨':'Inputs ready';
  return <footer className="relative shrink-0 border-t border-slate-700 bg-[#0B0E14] px-4 py-2 text-xs text-slate-200">
    {expanded&&<section aria-label="단계 준비도 상세" className="absolute bottom-full left-4 right-4 max-h-52 overflow-auto rounded-t-lg border border-slate-600 bg-[#142131] p-3 shadow-xl">
      <p className="font-semibold">완료된 입력</p><ul className="mt-1 list-disc pl-5">{readiness.completedInputs.length?readiness.completedInputs.map(item=><li key={item}>{item}</li>):<li>확인된 입력 없음</li>}</ul>
      <p className="mt-2 font-semibold">부족한 입력·확인할 근거</p><ul className="mt-1 list-disc pl-5">{readiness.missingInputs.length?readiness.missingInputs.map(item=><li key={item}>{item}</li>):<li>안내된 다음 행동의 입력이 준비되었습니다.</li>}</ul>
      <p className="mt-2 text-slate-400">학습·평가·승인·현장 적용은 각각의 실행 응답으로 확인합니다. 이전 결과와 다른 단계는 계속 볼 수 있습니다.</p>
      <button className="workspace-button mt-2" onClick={refresh}>현재 입력 다시 확인</button>
    </section>}
    <div className="flex flex-wrap items-center gap-2">
      <button className="workspace-button" disabled={activeStep===1} onClick={()=>move(activeStep-1)} aria-label="이전 단계 보기" title="Alt+←"><ArrowLeft className="h-3 w-3"/></button>
      <span className="font-mono text-slate-400">{activeStep}/6</span>
      <div role="status" className="min-w-0 flex-1" aria-live="polite">
        <span className="font-semibold">완료 입력 {readiness.completedInputs.length} · </span>
        <span>{readiness.missingInputs.length?`${labels[readiness.status]}: ${readiness.missingInputs[0]}`:'안내된 행동의 입력 준비됨'}</span>
      </div>
      <button className="workspace-button" aria-expanded={expanded} onClick={()=>setExpanded(value=>!value)}>입력 상세</button>
      <StatusBadge tone={readiness.status==='ready'?'success':readiness.status==='blocked'?'warning':'neutral'}>{labels[readiness.status]}</StatusBadge>
      <AsyncAction className="border-sky-500 bg-sky-900/50" aria-label={`권장 행동: ${readiness.nextAction.label}`} onClick={()=>void action()}>{readiness.nextAction.label}</AsyncAction>
      <button className="workspace-button" disabled={activeStep===6} onClick={()=>move(activeStep+1)} title="Alt+→">다음 단계 보기<ArrowRight className="ml-1 inline h-3 w-3"/></button>
    </div>
  </footer>;
};
