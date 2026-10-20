/**
 * src/renderer/components/wizard/WizardHeader.tsx
 *
 * The app header a first-time user reads: the project, where work runs, the inspection type and the six steps.
 * Port numbers, driver names and acceleration badges are facts for support, not for the first screen; they stay
 * behind the "기술 상세" switch (a device preference kept with the language choice).
 */

import React, { useEffect, useState } from 'react';
import {
  Activity,
  Check,
  ChevronDown,
  Disc,
  FolderKanban,
  GitFork,
  Rocket,
  Scan,
  ShieldAlert,
  Tag,
  Wand2,
} from 'lucide-react';
import { useProjectStore } from '../../stores/useProjectStore';
import { useComputeStore } from '../../stores/useComputeStore';
import { useTrainingStore } from '../../stores/useTrainingStore';
import { getApiPersistenceIdentity } from '../../services/api';
import { ComputeServerPanel } from '../compute/ComputeServerPanel';
import { ProjectWorkspaceDialog } from './ProjectWorkspaceDialog';
import { ProvenancePanel } from '../common/ProvenancePanel';
import { TaskChangeImpactDialog, taskPreviewScope } from './TaskChangeImpactDialog';
import type { VisionTask } from '../../types';
import { ThumbnailDiagnostics } from '../common/ThumbnailDiagnostics';

const CONTROL = 'rounded border border-[#3B4B63] bg-[#0B0E14] px-2.5 py-1.5 text-sm text-slate-100 hover:bg-[#1B2537] disabled:opacity-50';
const QUIET = 'rounded border border-transparent px-2.5 py-1.5 text-sm text-slate-200 hover:border-[#3B4B63] hover:bg-[#1B2537]';

/** The simple view takes the second row's few controls here: `simpleControls` sit next to 모두 보기 and `simpleMenu`
 * items join the 더 보기 menu, so the row disappears until the user asks for everything. */
export const WizardHeader: React.FC<{ simpleControls?: React.ReactNode; simpleMenu?: React.ReactNode; completedSteps?: readonly number[] }> = ({ simpleControls, simpleMenu, completedSteps = [] } = {}) => {
  const [showComputePanel, setShowComputePanel] = useState(false);
  const [showProjectPanel, setShowProjectPanel] = useState(false);
  const [showProvenance, setShowProvenance] = useState(false);
  const [taskFailure, setTaskFailure] = useState<{ scope: string; message: string } | null>(null);
  const {
    activeStep,
    setStep,
    task,
    language,
    setLanguage,
    technicalDetails,
    setTechnicalDetails,
    viewMode,
    setViewMode,
    theme,
    setTheme,
    backendStatus,
    projectName,
    projectError,
    isProjectBusy,
  } = useProjectStore();
  const {
    profiles, selectedProfileId, isLoaded, isLoading, loadError, error, transportRevision,
    probeResults, load, selectTarget,
  } = useComputeStore();
  const { jobId, jobComputeProfileId, jobComputeLabel } = useTrainingStore();
  const ko = language === 'ko';
  const full = viewMode === 'full';
  // What the user currently sees; 'system' resolves through the <html> attribute App keeps in step with the OS.
  const shownTheme = theme === 'system' ? (typeof document !== 'undefined' && document.documentElement.dataset.theme === 'light' ? 'light' : 'dark') : theme;
  const taskScope = () => {
    const currentProject = useProjectStore.getState();
    const currentCompute = useComputeStore.getState();
    return JSON.stringify([currentProject.project?.id, currentProject.projectDir,
      currentCompute.transportRevision, getApiPersistenceIdentity()]);
  };
  const taskError = taskFailure?.scope === taskScope() ? taskFailure.message : null;
  const [taskProposal,setTaskProposal]=useState<{task:VisionTask;scope:string}|null>(null);
  const changeTask = async (nextTask: VisionTask) => {
    setTaskFailure(null);
    if(nextTask!==task && !useProjectStore.getState().isProjectBusy)setTaskProposal({task:nextTask,scope:taskPreviewScope()});
  };

  useEffect(() => {
    if (!backendStatus.healthy) return;
    let cancelled = false;
    let retry: number | undefined;
    let failures = 0;
    const loadWhenReady = () => {
      void load().catch(() => {
        failures += 1;
        if (!cancelled && failures < 5) {
          retry = window.setTimeout(loadWhenReady, failures * 500);
        }
      });
    };
    loadWhenReady();
    return () => {
      cancelled = true;
      if (retry !== undefined) window.clearTimeout(retry);
    };
  }, [load, backendStatus.healthy, backendStatus.port, transportRevision]);

  const selectedProfile = profiles.find((profile) => profile.id === selectedProfileId);
  const selectedProbe = selectedProfileId ? probeResults[selectedProfileId] : null;
  const localLabel = ko ? '이 컴퓨터' : 'This computer';
  const resultLocation = jobComputeProfileId && jobComputeLabel === jobComputeProfileId
    ? profiles.find((profile) => profile.id === jobComputeProfileId)?.name || jobComputeLabel
    : jobComputeLabel === 'This computer' ? localLabel : jobComputeLabel;

  const steps = [
    // nameKo is what a first-time user reads; legacyKo is the studio's older name, kept in the accessible name so saved
    // guides, receipts and the e2e suites that look for it still find the step.
    { num: 1, nameKo: '데이터 준비', legacyKo: '데이터 관리', nameEn: 'Data', icon: FolderKanban },
    { num: 2, nameKo: '라벨 달기', legacyKo: '라벨링 & AI', nameEn: 'Labeling', icon: Tag },
    { num: 3, nameKo: '모델 학습', legacyKo: '오토딥러닝', nameEn: 'Training', icon: Wand2 },
    { num: 4, nameKo: '결과 평가', legacyKo: '평가 & 과검/미검', nameEn: 'Evaluation', icon: Activity },
    { num: 5, nameKo: '검사 흐름 만들기', legacyKo: '플로우차트', nameEn: 'Flowchart', icon: GitFork },
    { num: 6, nameKo: '검사 실행', legacyKo: '추론 & 모델 내보내기', nameEn: 'Inference & export', icon: Rocket },
  ];

  const tasks: Array<{ id: VisionTask; labelKo: string; labelEn: string; icon: React.ComponentType<{ className?: string }> }> = [
    { id: 'classification', labelKo: '분류', labelEn: 'Classification', icon: Tag },
    { id: 'detection', labelKo: '객체 검출', labelEn: 'Object detection', icon: Scan },
    { id: 'segmentation', labelKo: '영역 분할', labelEn: 'Segmentation', icon: Disc },
    { id: 'anomaly', labelKo: '이상 탐지', labelEn: 'Anomaly detection', icon: ShieldAlert },
  ];
  const currentTaskMeta = tasks.find((t) => t.id === task) || tasks[0];
  const CurrentTaskIcon = currentTaskMeta.icon;

  // Where work runs, in plain words. The raw device name and port live in the technical row.
  const isHealthy = Boolean(backendStatus.healthy);
  const rawDevice = (backendStatus.device || '').toLowerCase();
  const isAccelerated = isHealthy && (rawDevice === 'mps' || rawDevice.startsWith('cuda'));
  const engineText = !isHealthy ? (ko ? '연결 중…' : 'Connecting…')
    : isAccelerated ? (ko ? 'GPU 가속' : 'GPU acceleration') : (ko ? 'CPU로 실행' : 'Runs on the CPU');
  const engineTone = !isHealthy ? 'border-amber-500/60 bg-amber-950/40 text-amber-200'
    : isAccelerated ? 'border-emerald-500/60 bg-emerald-950/40 text-emerald-200' : 'border-[#3B4B63] bg-[#131822] text-slate-200';

  return (<>
    {taskProposal && <TaskChangeImpactDialog nextTask={taskProposal.task} scope={taskProposal.scope} onClose={()=>setTaskProposal(null)} onResult={outcome=>{setTaskProposal(null);if(!outcome.ok)setTaskFailure({scope:taskScope(),message:outcome.error});}}/>}
    <header className="bg-[#0B0E14] border-b border-[#2B3547] text-slate-200 select-none">
      {/* Row 1 (draggable): the project, where work runs, history, language. */}
      <div
        className={`flex min-h-11 flex-wrap items-center justify-between gap-x-3 gap-y-1 border-b border-[#2B3547] bg-[#0E121A] py-1 ${window.api?.platform === 'darwin' ? 'pl-24 pr-4' : 'px-4'}`}
        style={{ WebkitAppRegion: 'drag' } as React.CSSProperties}
      >
        <div className="flex flex-wrap items-center gap-2" style={{ WebkitAppRegion: 'no-drag' } as React.CSSProperties}>
          <span className="text-sm font-bold tracking-wide text-slate-100">Vision AI Studio</span>
          <button type="button" onClick={() => setShowProjectPanel(true)} title={ko ? '프로젝트 관리' : 'Manage projects'} className={`group flex max-w-xs items-center gap-1.5 ${QUIET}`}>
            <FolderKanban className="h-4 w-4 shrink-0 text-cyan-400" aria-hidden="true" />
            <span className="truncate font-semibold">{projectName}</span>
            <ChevronDown className="h-3.5 w-3.5 shrink-0 text-slate-400" aria-hidden="true" />
          </button>
          {projectError && <button type="button" onClick={() => setShowProjectPanel(true)} title={projectError} className="max-w-[220px] truncate rounded border border-red-700/60 bg-red-950/40 px-2.5 py-1.5 text-sm text-red-200">{ko ? '프로젝트 오류 · 자세히' : 'Project error · details'}</button>}
          <label className="flex items-center gap-1.5 text-sm text-slate-300">
            <span>{ko ? '실행 위치' : 'Run on'}</span>
            <select
              aria-label={ko ? '실행 위치' : 'Run on'}
              value={selectedProfileId || ''}
              disabled={!isLoaded || isLoading}
              onChange={(event) => void selectTarget(event.target.value || null).catch(() => {})}
              className={`max-w-[220px] ${CONTROL}`}
            >
              <option value="">{localLabel}</option>
              {selectedProfileId && !selectedProfile && <option value={selectedProfileId}>{ko ? `연결되지 않은 서버: ${selectedProfileId}` : `Unavailable server: ${selectedProfileId}`}</option>}
              {profiles.map((profile) => <option key={profile.id} value={profile.id}>{profile.name}</option>)}
            </select>
          </label>
          {full && <button type="button" onClick={() => setShowComputePanel(true)} className={CONTROL}>{ko ? '서버 관리' : 'Servers'}</button>}
          {isLoading && <span className="text-sm text-slate-400">{ko ? '설정 확인 중…' : 'Checking…'}</span>}
          {(loadError || error) && <span role="alert" className="max-w-[340px] truncate text-sm text-red-300" title={loadError || error || undefined}>{loadError || error}</span>}
          {selectedProfileId && !loadError && (
            <span className={`text-sm ${selectedProbe?.ready ? 'text-emerald-300' : 'text-amber-200'}`}>
              {selectedProbe?.ready ? (ko ? `준비 완료 · ${selectedProbe.device_name || '장치 확인됨'}` : `Ready · ${selectedProbe.device_name || 'device verified'}`) : (ko ? '연결 검사 필요' : 'Connection check needed')}
            </span>
          )}
        </div>

        <div className="flex flex-wrap items-center gap-2" style={{ WebkitAppRegion: 'no-drag' } as React.CSSProperties}>
          {full && <span role="status" className={`rounded border px-2.5 py-1 text-sm ${engineTone}`}>
            {selectedProfileId ? (selectedProfile?.name || (ko ? '서버' : 'Server')) : localLabel} · {engineText}
          </span>}
          {/* The simple view keeps the few controls a first run needs; everything else waits under 더 보기 or in 모두 보기. */}
          {!full && simpleControls}
          <button type="button" aria-pressed={full} onClick={() => setViewMode(full ? 'simple' : 'full')} className={`${CONTROL} ${full ? 'border-sky-500 bg-sky-950/60 text-sky-100' : ''}`} title={ko ? (full ? '처음 쓰는 사람에게 필요한 항목만 보입니다' : '모든 설정과 관리 항목이 보입니다') : undefined}>
            {full ? (ko ? '간단히 보기' : 'Simple view') : (ko ? '모두 보기' : 'Show everything')}
          </button>
          {full ? <>
            <button type="button" onClick={() => setShowProvenance(true)} className={CONTROL}>{ko ? '데이터·판정 이력' : 'Data and verdict history'}</button>
            <button type="button" aria-pressed={technicalDetails} onClick={() => setTechnicalDetails(!technicalDetails)} className={`${CONTROL} ${technicalDetails ? 'border-sky-500 bg-sky-950/60 text-sky-100' : ''}`}>
              {ko ? '기술 상세' : 'Technical details'}
            </button>
            <button type="button" onClick={() => setTheme(shownTheme === 'light' ? 'dark' : 'light')} className={CONTROL} aria-pressed={shownTheme === 'light'}>
              {shownTheme === 'light' ? (ko ? '어두운 화면' : 'Dark screen') : (ko ? '밝은 화면' : 'Light screen')}
            </button>
          </> : <details className="relative">
            <summary className={`list-none cursor-pointer ${CONTROL}`}>{ko ? '더 보기' : 'More'}</summary>
            <div className="absolute right-0 z-30 mt-1 flex min-w-[200px] flex-col gap-1 rounded border border-[#3B4B63] bg-[#131822] p-2 shadow-xl" style={{ WebkitAppRegion: 'no-drag' } as React.CSSProperties}>
              <span role="status" className={`rounded border px-2.5 py-1 text-sm ${engineTone}`}>
                {selectedProfileId ? (selectedProfile?.name || (ko ? '서버' : 'Server')) : localLabel} · {engineText}
              </span>
              <button type="button" onClick={() => setShowComputePanel(true)} className={CONTROL}>{ko ? '서버 관리' : 'Servers'}</button>
              <button type="button" onClick={() => setShowProvenance(true)} className={CONTROL}>{ko ? '데이터·판정 이력' : 'Data and verdict history'}</button>
              <button type="button" aria-pressed={technicalDetails} onClick={() => setTechnicalDetails(!technicalDetails)} className={`${CONTROL} ${technicalDetails ? 'border-sky-500 bg-sky-950/60 text-sky-100' : ''}`}>
                {ko ? '기술 상세' : 'Technical details'}
              </button>
              <button type="button" onClick={() => setTheme(shownTheme === 'light' ? 'dark' : 'light')} className={CONTROL} aria-pressed={shownTheme === 'light'}>
                {shownTheme === 'light' ? (ko ? '어두운 화면' : 'Dark screen') : (ko ? '밝은 화면' : 'Light screen')}
              </button>
              {simpleMenu}
            </div>
          </details>}
          <button type="button" onClick={() => setLanguage(ko ? 'en' : 'ko')} className={CONTROL} aria-label="언어 전환 (KR / EN)" title={ko ? 'Switch to English' : '한국어로 전환'}>
            {ko ? 'English' : '한국어'}
          </button>
        </div>
      </div>

      {/* Row 2: the inspection type and the six steps. Labels wrap on a narrow window instead of being cut off. */}
      <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2 bg-[#131822] px-4 py-2">
        <label className="flex items-center gap-2 text-sm text-slate-300">
          <span>{ko ? '검사 종류' : 'Inspection type'}</span>
          <span className="relative flex items-center">
            <span className="pointer-events-none absolute left-2.5 flex items-center text-blue-400"><CurrentTaskIcon className="h-4 w-4" aria-hidden="true" /></span>
            <select
              aria-label="검사 작업 종류"
              aria-describedby={taskError ? 'task-change-error' : undefined}
              value={task}
              disabled={isProjectBusy}
              onChange={(e) => changeTask(e.target.value as VisionTask)}
              className={`appearance-none pl-8 pr-8 font-semibold ${CONTROL}`}
            >
              {tasks.map((t) => (
                <option key={t.id} value={t.id} className="bg-[#131822] text-slate-200">{ko ? t.labelKo : t.labelEn}</option>
              ))}
            </select>
            <ChevronDown className="pointer-events-none absolute right-2.5 h-3.5 w-3.5 text-slate-400" aria-hidden="true" />
          </span>
        </label>

        <nav className="flex flex-wrap items-stretch overflow-hidden rounded border border-[#2B3547] bg-[#0B0E14]" aria-label={ko ? '작업 단계 (Workflow Stages)' : 'Workflow Stages'}>
          {steps.map((s) => {
            const Icon = s.icon;
            const isActive = activeStep === s.num;
            const isPrevious = activeStep > s.num;
            const isDone = completedSteps.includes(s.num) && !isActive;
            return (
              <button
                key={s.num}
                type="button"
                aria-current={isActive ? 'step' : undefined}
                onClick={() => setStep(s.num as 1 | 2 | 3 | 4 | 5 | 6)}
                className={`relative flex min-h-9 items-center gap-2 border-r border-[#2B3547] px-3.5 py-1.5 text-sm transition-colors duration-75 last:border-r-0 ${
                  isActive
                    ? 'bg-[#1A212E] font-bold text-white shadow-[inset_0_3px_0_0_#10B981]'
                    : isPrevious
                    ? 'bg-[#131822] text-slate-200 hover:bg-[#1A212E]'
                    : 'bg-[#0E121A] text-slate-300 hover:bg-[#151C27]'
                }`}
              >
                <span className={`font-mono tabular-nums ${isActive ? 'text-[#10B981]' : isDone ? 'text-emerald-400' : 'text-slate-400'}`}>{String(s.num).padStart(2, '0')}</span>
                <Icon className={`h-4 w-4 ${isActive ? 'text-[#10B981]' : 'text-slate-400'}`} aria-hidden="true" />
                <span className="whitespace-nowrap font-medium">{ko ? s.nameKo : s.nameEn}</span>
                {ko && <span className="sr-only">({s.legacyKo})</span>}
                {/* A finished step keeps its number (specs and keyboard users find steps by it) and gains a check. */}
                {isDone && <span className="flex h-4 w-4 items-center justify-center rounded-full bg-emerald-600 text-white" title={ko ? '끝난 단계' : 'Done'}><Check className="h-3 w-3" aria-hidden="true" /><span className="sr-only">{ko ? '끝남' : 'done'}</span></span>}
              </button>
            );
          })}
        </nav>

        {activeStep >= 4 && jobId && (
          <span className="rounded border border-[#364357] px-2.5 py-1 text-sm text-slate-300">
            {ko ? '현재 모델 위치' : 'Current model location'}: <strong className="text-white">{resultLocation}</strong>
          </span>
        )}
      </div>

      {/* Technical row: only when asked for. The same facts the support bundle reports. */}
      {technicalDetails && (
        <dl className="flex flex-wrap items-center gap-x-5 gap-y-1 border-t border-[#2B3547] bg-[#0E121A] px-4 py-1.5 font-mono text-sm text-slate-300" aria-label={ko ? '기술 상세' : 'Technical details'}>
          <div className="flex gap-1.5"><dt className="text-slate-400">{ko ? '백엔드 포트' : 'Backend port'}</dt><dd>{isHealthy ? backendStatus.port : '—'}</dd></div>
          <div className="flex gap-1.5"><dt className="text-slate-400">{ko ? '장치' : 'Device'}</dt><dd>{rawDevice || '—'}{backendStatus.deviceName ? ` · ${backendStatus.deviceName}` : ''}</dd></div>
          <div className="flex gap-1.5"><dt className="text-slate-400">{ko ? '가속' : 'Acceleration'}</dt><dd>{!isHealthy ? (ko ? '연결 안 됨' : 'offline') : isAccelerated ? (ko ? '사용' : 'on') : (ko ? '없음 (CPU)' : 'none (CPU)')}</dd></div>
          {selectedProbe?.device_name && <div className="flex gap-1.5"><dt className="text-slate-400">{ko ? '서버 장치' : 'Server device'}</dt><dd>{selectedProbe.device_name}</dd></div>}
          <ThumbnailDiagnostics />
        </dl>
      )}
      {taskError && <p id="task-change-error" role="alert" className="border-t border-rose-900/50 bg-rose-950/30 px-4 py-2 text-sm text-rose-200">{taskError}</p>}
      {showComputePanel && <ComputeServerPanel onClose={() => setShowComputePanel(false)} />}
      {showProjectPanel && <ProjectWorkspaceDialog onClose={() => setShowProjectPanel(false)} />}
      {showProvenance && <ProvenancePanel onClose={() => setShowProvenance(false)} />}
    </header>
  </>);
};
