/**
 * src/renderer/App.tsx
 * Vision AI Studio — Desktop 6-Step Industrial Vision AI Suite Root Orchestrator.
 * Dark Steel Chassis & 1px Precision Hairline Grid Architecture.
 */

import { useEffect, useRef, useState } from 'react';
import { WorkspaceDialog } from './components/common/WorkspaceDialog';
import { TaskCenter } from './components/training/TaskCenter';
import { ProductDeliveryWorkspace } from './components/runtime/ProductDeliveryWorkspace';
import { OperatorWorkspace } from './components/runtime/OperatorWorkspace';
import { WizardHeader } from './components/wizard/WizardHeader';
import { WizardFooter } from './components/wizard/WizardFooter';
import { DatasetStudio } from './components/dataset/DatasetStudio';
import { LabelingTool } from './components/labeling/LabelingTool';
import { TrainingController } from './components/training/TrainingController';
import { EvaluationStudio } from './components/evaluation/EvaluationStudio';
import { FlowchartStudio } from './components/flowchart/FlowchartStudio';
import { InferenceCenterStudio } from './components/inference/InferenceCenterStudio';
import { ErrorDiagnosticsModal } from './components/common/ErrorDiagnosticsModal';
import { FirstRunStudio } from './components/onboarding/FirstRunStudio';
import { useFirstRunStore } from './components/onboarding/firstRunStore';
import { EXAMPLE_NOTICE, shouldOpenFirstRun } from './components/onboarding/firstRun';
import { useProjectStore } from './stores/useProjectStore';
import { useTrainingStore } from './stores/useTrainingStore';
import { useComputeStore } from './stores/useComputeStore';
import { telemetryService } from './services/websocket';
import { getBackendPort, request } from './services/api';
import { needsBrowserHealthProbe, startBrowserHealthProbe } from './services/browserHealth';
import { createCatchUp, createJobEventStream } from './services/jobEventFeed';

// S1-10: after every (re)connection the app reads the job events it may have missed and re-reads its job if needed.
const jobEventStream = createJobEventStream();
// A failed catch-up moved nothing; it is retried once, the next connection catches up again, and live telemetry and
// polling continue meanwhile.
const catchUpJobEvents = createCatchUp(jobEventStream, () => useTrainingStore.getState().jobId,
  () => useTrainingStore.getState().refreshCurrentJob());

export default function App() {
  const { language, activeStep, backendStatus, setBackendStatus, showError, syncCurrentProject } = useProjectStore();
  const { updateFromTelemetry } = useTrainingStore();
  const [workspace,setWorkspace]=useState<'studio'|'operator'>('studio');
  const [utility,setUtility]=useState<'tasks'|'delivery'|null>(null);
  const transportRevision=useComputeStore(s=>s.transportRevision);
  const project=useProjectStore(s=>s.project);
  const {onboarding,open:firstRunOpen,demo,setOpen:setFirstRunOpen,dismiss:dismissFirstRun}=useFirstRunStore();
  const firstRunOffered=useRef(false);
  // S2-01: the example marker is written by the backend into project.json and comes back with the project.
  const exampleProject=Boolean((project as {example?:unknown}|null)?.example);

  useEffect(() => {
    let unsubStatus: (() => void) | undefined;
    let unsubCrash: (() => void) | undefined;
    let stopHealthProbe: (() => void) | undefined;

    // 1. Hook Electron preload IPC events if running under Electron
    if (typeof window !== 'undefined' && window.api) {
      window.api.getBackendStatus().then(setBackendStatus).catch(console.error);

      unsubStatus = window.api.onBackendStatusChange?.((newStatus) => {
        setBackendStatus(newStatus);
      });

      unsubCrash = window.api.onBackendCrashed?.((crashData) => {
        showError({
          code: 'ERR_001',
          title_en: 'Backend Process Crash',
          title_kr: '백엔드 프로세스 충돌',
          description_en: crashData.message,
          description_kr: `백엔드 데몬이 비정상 종료되었습니다: ${crashData.message}`,
          remediation_en: 'Check system memory and logs.',
          remediation_kr: '시스템 메모리 및 로그를 확인하세요.',
          severity: 'critical',
          auto_fixable: false,
        });
      });
    } else if (needsBrowserHealthProbe(typeof window === 'undefined' ? undefined : window)) {
      // 1b. A plain browser has no preload status events; the backend's /health through the same
      // transport is the status source that starts project discovery (and with it telemetry).
      stopHealthProbe = startBrowserHealthProbe({
        check: async signal => {
          const health = await request<{ status: string; device?: string; device_name?: string }>('/health', { signal, projectContext: null });
          return { healthy: health.status === 'ok', device: health.device, deviceName: health.device_name };
        },
        port: getBackendPort,
        onStatus: setBackendStatus,
      });
    }

    // 2. Initialize WebSocket telemetry streaming
    telemetryService.connect();
    const unsubTelemetry = telemetryService.subscribe((event, data) => {
      if (event === 'telemetry_connected') { void catchUpJobEvents(); return; }
      updateFromTelemetry(event, data);
      if (event === 'training_error') {
        showError(data);
      }
    });

    return () => {
      if (unsubStatus) unsubStatus();
      if (unsubCrash) unsubCrash();
      if (stopHealthProbe) stopHealthProbe();
      unsubTelemetry();
      telemetryService.disconnect();
    };
  }, [setBackendStatus, showError, updateFromTelemetry]);

  useEffect(() => {
    if (!backendStatus.healthy) return;
    // The guide's state is read after the first project sync: read together, the sync's step restore could land after a
    // stage the user already chose and take the app back to step 1.
    void syncCurrentProject().finally(() => { void useFirstRunStore.getState().load(); });
  }, [backendStatus.healthy, backendStatus.port, syncCurrentProject]);

  // The first-run guide opens by itself once per start, only before any project has data and until dismissed.
  useEffect(() => {
    if (firstRunOffered.current || !onboarding || !project) return;
    firstRunOffered.current = true;
    if (shouldOpenFirstRun(onboarding, Boolean(project.source_dataset_dir))) setFirstRunOpen(true);
  }, [onboarding, project, setFirstRunOpen]);

  return (
    <div className="flex flex-col h-screen w-screen bg-[#0B0E14] text-slate-200 select-none overflow-hidden font-sans">
      {/* Precision Industrial Header Frame */}
      <WizardHeader />

      <nav aria-label={language==='ko'?'프로젝트 작업 공간':'Project workspace'} className="flex shrink-0 items-center gap-2 border-b border-slate-700 bg-[#101722] px-4 py-1.5 text-xs">
        <button className={`rounded px-3 py-1.5 ${workspace==='studio'?'bg-sky-950 text-sky-200':'text-slate-300'}`} aria-pressed={workspace==='studio'} onClick={()=>setWorkspace('studio')}>{language==='ko'?'모델·플로우 작업':'Models and flows'}</button>
        <button className={`rounded px-3 py-1.5 ${workspace==='operator'?'bg-sky-950 text-sky-200':'text-slate-300'}`} aria-pressed={workspace==='operator'} onClick={()=>setWorkspace('operator')}>{language==='ko'?'운영자 검사':'Operator inspection'}</button>
        <span className="flex-1"/>
        <button className="rounded border border-slate-600 px-3 py-1.5" onClick={()=>setFirstRunOpen(true)}>{language==='ko'?(demo?.state==='running'?'처음 시작 · 예제 만드는 중':'처음 시작 안내'):(demo?.state==='running'?'Getting started · building example':'Getting started')}</button>
        <button className="rounded border border-slate-600 px-3 py-1.5" onClick={()=>setUtility('tasks')}>{language==='ko'?'작업 센터':'Task center'}</button>
        <button className="rounded border border-slate-600 px-3 py-1.5" onClick={()=>setUtility('delivery')}>{language==='ko'?'패키지·장치·진단':'Packages, devices and diagnostics'}</button>
      </nav>
      {exampleProject&&<p role="note" aria-label="예제 프로젝트" className="shrink-0 border-b border-amber-700/60 bg-amber-950/30 px-4 py-1.5 text-xs text-amber-200">예제 프로젝트 · {EXAMPLE_NOTICE}</p>}
      {/* Primary Inspection Studio Workspace with 1px Hairline Grid Containment */}
      <main key={transportRevision} className="flex-1 min-h-0 flex overflow-hidden bg-[#0B0E14]">
        {workspace==='studio' && activeStep === 1 && <DatasetStudio />}
        {workspace==='studio' && activeStep === 2 && <LabelingTool />}
        {workspace==='studio' && activeStep === 3 && <TrainingController key={transportRevision}/>}
        {workspace==='studio' && activeStep === 4 && <EvaluationStudio />}
        {workspace==='studio' && activeStep === 5 && <FlowchartStudio />}
        {workspace==='studio' && activeStep === 6 && <InferenceCenterStudio />}
        {workspace==='operator'&&<div className="product-workbench flex-1 min-h-0 overflow-auto"><OperatorWorkspace/></div>}
      </main>

      {/* Docked Telemetry & Navigation Footer */}
      {workspace==='studio'&&<WizardFooter />}

      {/* Global Industrial Fault Diagnostic Dialog */}
      <ErrorDiagnosticsModal />
      {firstRunOpen&&<WorkspaceDialog title="처음 시작하기" onClose={()=>setFirstRunOpen(false)}>
        <FirstRunStudio onClose={()=>setFirstRunOpen(false)} onDismiss={()=>void dismissFirstRun()}/>
      </WorkspaceDialog>}
      {utility&&<WorkspaceDialog key={`${utility}:${transportRevision}`} title={utility==='tasks'?'작업 센터':'패키지·장치·설치·진단'} onClose={()=>setUtility(null)}>
        {utility==='tasks'?<TaskCenter initialOpen onNavigate={()=>{setUtility(null);setWorkspace('studio');}}/>:<ProductDeliveryWorkspace onNavigate={step=>{setUtility(null);setWorkspace('studio');void useProjectStore.getState().setStep(step);}}/>}
      </WorkspaceDialog>}
    </div>
  );
}
