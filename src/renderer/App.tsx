/**
 * src/renderer/App.tsx
 * Vision AI Studio — Desktop 6-Step Industrial Vision AI Suite Root Orchestrator.
 * Inspection Dark Steel Chassis & 1px Precision Hairline Grid Architecture.
 */

import { useEffect } from 'react';
import { WizardHeader } from './components/wizard/WizardHeader';
import { WizardFooter } from './components/wizard/WizardFooter';
import { DatasetStudio } from './components/dataset/DatasetStudio';
import { LabelingTool } from './components/labeling/LabelingTool';
import { TrainingController } from './components/training/TrainingController';
import { EvaluationStudio } from './components/evaluation/EvaluationStudio';
import { FlowchartStudio } from './components/flowchart/FlowchartStudio';
import { InferenceCenterStudio } from './components/inference/InferenceCenterStudio';
import { ErrorDiagnosticsModal } from './components/common/ErrorDiagnosticsModal';
import { useProjectStore } from './stores/useProjectStore';
import { useTrainingStore } from './stores/useTrainingStore';
import { telemetryService } from './services/websocket';

export default function App() {
  const { activeStep, setBackendStatus, showError } = useProjectStore();
  const { updateFromTelemetry } = useTrainingStore();

  useEffect(() => {
    let unsubStatus: (() => void) | undefined;
    let unsubCrash: (() => void) | undefined;

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
    }

    // 2. Initialize WebSocket telemetry streaming
    telemetryService.connect();
    const unsubTelemetry = telemetryService.subscribe((event, data) => {
      updateFromTelemetry(event, data);
      if (event === 'training_error') {
        showError(data);
      }
    });

    return () => {
      if (unsubStatus) unsubStatus();
      if (unsubCrash) unsubCrash();
      unsubTelemetry();
      telemetryService.disconnect();
    };
  }, [setBackendStatus, showError, updateFromTelemetry]);

  return (
    <div className="flex flex-col h-screen w-screen bg-[#0B0E14] text-slate-200 select-none overflow-hidden font-sans">
      {/* Precision Industrial Header Frame */}
      <WizardHeader />

      {/* Primary Inspection Studio Workspace with 1px Hairline Grid Containment */}
      <main className="flex-1 min-h-0 flex overflow-hidden bg-[#0B0E14]">
        {activeStep === 1 && <DatasetStudio />}
        {activeStep === 2 && <LabelingTool />}
        {activeStep === 3 && <TrainingController />}
        {activeStep === 4 && <EvaluationStudio />}
        {activeStep === 5 && <FlowchartStudio />}
        {activeStep === 6 && <InferenceCenterStudio />}
      </main>

      {/* Docked Telemetry & Navigation Footer */}
      <WizardFooter />

      {/* Global Industrial Fault Diagnostic Dialog */}
      <ErrorDiagnosticsModal />
    </div>
  );
}
