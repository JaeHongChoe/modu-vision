/**
 * src/renderer/components/wizard/WizardHeader.tsx
 *
 * The app header a first-time user reads. 간단히 보기 is one row: the project, the six steps, 모두 보기 and one 더 보기
 * menu that holds everything else (where work runs, the inspection type, history, technical details, the theme, the
 * language and the items App adds). 모두 보기 keeps its two rows: the project, where work runs, history and language
 * on the first; the inspection type and the six steps on the second. Port numbers, driver names and acceleration
 * badges are facts for support, not for the first screen; they stay behind the "기술 상세" switch (a device
 * preference kept with the language choice).
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
const PRESSED = 'border-sky-500 bg-sky-950/60 text-sky-100';
// 간단히 보기 uses the shared secondary button (the footer's); the header never carries the primary one. Menu items
// line up at the left.
const SIMPLE_CONTROL = 'workspace-button whitespace-nowrap';
const MENU_ITEM = 'workspace-button justify-start whitespace-nowrap';

/** App adds its own items to the simple view's 더 보기 menu: `simpleControls` (the first-run guide) and `simpleMenu`
 * (the workspace switch, the task center, packages) render after the header's own items, by their usual names. */
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

  // The 더 보기 menu (simple view only). A <details> is keyboard reachable as it is; Escape, a click outside and focus
  // leaving it close it, and choosing an item closes it. The element is kept in state, not a ref, so the unit
  // harnesses (static markup, a hook stub without refs) never see one.
  const [menu, setMenu] = useState<HTMLDetailsElement | null>(null);
  // Focus moves to the 더 보기 button before the item's own dialog opens, so the dialog returns it there and a toggle
  // (기술 상세, theme, language) leaves it there; the chosen item itself is hidden with the menu.
  const closeMenu = () => {
    if (!menu?.open) return;
    menu.open = false;
    menu.querySelector('summary')?.focus();
  };
  useEffect(() => {
    if (!menu || typeof document === 'undefined' || typeof document.addEventListener !== 'function') return;
    const onPointerDown = (event: PointerEvent) => {
      if (menu.open && !menu.contains(event.target as Node)) menu.open = false;
    };
    document.addEventListener('pointerdown', onPointerDown);
    return () => document.removeEventListener('pointerdown', onPointerDown);
  }, [menu]);

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
  const engineLabel = <>{selectedProfileId ? (selectedProfile?.name || (ko ? '서버' : 'Server')) : localLabel} · {engineText}</>;

  // The controls both views render: 모두 보기 in its two rows (unchanged), 간단히 보기 in its one row and 더 보기 menu.
  // Every name and aria-label is the same in both places, so a control is found the same way wherever it sits.
  const control = full ? CONTROL : MENU_ITEM;
  const noDrag = { WebkitAppRegion: 'no-drag' } as React.CSSProperties;
  const runOn = (
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
  );
  const serversButton = <button type="button" onClick={() => setShowComputePanel(true)} className={control}>{ko ? '서버 관리' : 'Servers'}</button>;
  const loadingNote = isLoading && <span className="text-sm text-slate-400">{ko ? '설정 확인 중…' : 'Checking…'}</span>;
  // The alert stays in the row in both views: a closed menu would not announce it.
  const computeAlert = (loadError || error) && <span role="alert" className="max-w-[340px] truncate text-sm text-red-300" title={loadError || error || undefined}>{loadError || error}</span>;
  const probeNote = selectedProfileId && !loadError && (
    <span className={`text-sm ${selectedProbe?.ready ? 'text-emerald-300' : 'text-amber-200'}`}>
      {selectedProbe?.ready ? (ko ? `준비 완료 · ${selectedProbe.device_name || '장치 확인됨'}` : `Ready · ${selectedProbe.device_name || 'device verified'}`) : (ko ? '연결 검사 필요' : 'Connection check needed')}
    </span>
  );
  // 간단히 보기 keeps these two in 더 보기 while all is well and lifts them into the row while they report a problem
  // (the backend not answering yet, a chosen server not checked), so a person never has to open a menu to see why.
  const engineProblem = !isHealthy;
  const probeProblem = Boolean(selectedProfileId && !loadError && !selectedProbe?.ready);
  const engineChip = <span role="status" className={`rounded border px-2.5 py-1 text-sm ${engineTone}`}>{engineLabel}</span>;
  const historyButton = <button type="button" onClick={() => setShowProvenance(true)} className={control}>{ko ? '데이터·판정 이력' : 'Data and verdict history'}</button>;
  const technicalButton = (
    <button type="button" aria-pressed={technicalDetails} onClick={() => setTechnicalDetails(!technicalDetails)} className={`${control} ${technicalDetails ? PRESSED : ''}`}>
      {ko ? '기술 상세' : 'Technical details'}
    </button>
  );
  const themeButton = (
    <button type="button" onClick={() => setTheme(shownTheme === 'light' ? 'dark' : 'light')} className={control} aria-pressed={shownTheme === 'light'}>
      {shownTheme === 'light' ? (ko ? '어두운 화면' : 'Dark screen') : (ko ? '밝은 화면' : 'Light screen')}
    </button>
  );
  const languageButton = (
    <button type="button" onClick={() => setLanguage(ko ? 'en' : 'ko')} className={control} aria-label="언어 전환 (KR / EN)" title={ko ? 'Switch to English' : '한국어로 전환'}>
      {ko ? 'English' : '한국어'}
    </button>
  );
  const taskSelect = (
    <label className="flex items-center gap-2 text-sm text-slate-300">
      <span>{ko ? '검사 종류' : 'Inspection type'}</span>
      <span className="relative flex items-center">
        <span className="pointer-events-none absolute left-2.5 flex items-center text-blue-400"><CurrentTaskIcon className="h-4 w-4" aria-hidden="true" /></span>
        <select
          aria-label="검사 작업 종류"
          aria-describedby={taskError ? 'task-change-error' : undefined}
          value={task}
          disabled={isProjectBusy}
          // A new type opens the change dialog; in 더 보기 that closes the menu first, so the dialog hands focus back
          // to 더 보기 instead of to this (then hidden) select. 모두 보기 has no menu.
          onChange={(e) => { closeMenu(); void changeTask(e.target.value as VisionTask); }}
          className={`appearance-none pl-8 pr-8 font-semibold ${CONTROL}`}
        >
          {tasks.map((t) => (
            <option key={t.id} value={t.id} className="bg-[#131822] text-slate-200">{ko ? t.labelKo : t.labelEn}</option>
          ))}
        </select>
        <ChevronDown className="pointer-events-none absolute right-2.5 h-3.5 w-3.5 text-slate-400" aria-hidden="true" />
      </span>
    </label>
  );
  const stepBar = (
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
  );
  const modelLocation = activeStep >= 4 && jobId && (
    <span className="rounded border border-[#364357] px-2.5 py-1 text-sm text-slate-300">
      {ko ? '현재 모델 위치' : 'Current model location'}: <strong className="text-white">{resultLocation}</strong>
    </span>
  );

  return (<>
    {taskProposal && <TaskChangeImpactDialog nextTask={taskProposal.task} scope={taskProposal.scope} onClose={()=>setTaskProposal(null)} onResult={outcome=>{setTaskProposal(null);if(!outcome.ok)setTaskFailure({scope:taskScope(),message:outcome.error});}}/>}
    <header className="bg-[#0B0E14] border-b border-[#2B3547] text-slate-200 select-none">
      {/* Row 1 (draggable). 모두 보기: the project, where work runs, history, language. 간단히 보기: the whole header —
          the project, the six steps, 모두 보기 and the 더 보기 menu. */}
      <div
        className={`flex min-h-11 flex-wrap items-center justify-between gap-x-3 gap-y-1 ${full ? 'border-b border-[#2B3547] ' : ''}bg-[#0E121A] py-1 ${window.api?.platform === 'darwin' ? 'pl-24 pr-4' : 'px-4'}`}
        style={{ WebkitAppRegion: 'drag' } as React.CSSProperties}
      >
        <div className="flex flex-wrap items-center gap-2" style={noDrag}>
          {/* 간단히 보기 drops the wordmark below 1440px so the project, the steps and the two toggles fit one row. */}
          <span className={`text-sm font-bold tracking-wide text-slate-100${full ? '' : ' hidden min-[1440px]:inline'}`}>Vision AI Studio</span>
          <button type="button" onClick={() => setShowProjectPanel(true)} title={ko ? '프로젝트 관리' : 'Manage projects'} className={`group flex max-w-xs items-center gap-1.5 ${QUIET}`}>
            <FolderKanban className="h-4 w-4 shrink-0 text-cyan-400" aria-hidden="true" />
            <span className="truncate font-semibold">{projectName}</span>
            <ChevronDown className="h-3.5 w-3.5 shrink-0 text-slate-400" aria-hidden="true" />
          </button>
          {projectError && <button type="button" onClick={() => setShowProjectPanel(true)} title={projectError} className="max-w-[220px] truncate rounded border border-red-700/60 bg-red-950/40 px-2.5 py-1.5 text-sm text-red-200">{ko ? '프로젝트 오류 · 자세히' : 'Project error · details'}</button>}
          {full && runOn}
          {full && serversButton}
          {full && loadingNote}
          {computeAlert}
          {(full || probeProblem) && probeNote}
          {!full && engineProblem && engineChip}
        </div>

        {/* 간단히 보기: the six steps share the row. On a narrow window the bar moves to a line of its own, and when even
            that line is too short its steps wrap inside it instead of running past the window edge. */}
        {!full && <div className="ml-auto flex min-w-0 items-center" style={noDrag}>{stepBar}</div>}

        {/* 간단히 보기 keeps this group at the right edge even when it wraps alone onto the next line, so the 더 보기
            menu, which opens leftward from it, stays inside the window. */}
        <div className={`flex flex-wrap items-center gap-2${full ? '' : ' ml-auto'}`} style={noDrag}>
          {full && <span role="status" className={`rounded border px-2.5 py-1 text-sm ${engineTone}`}>{engineLabel}</span>}
          <button type="button" aria-pressed={full} onClick={() => setViewMode(full ? 'simple' : 'full')} className={full ? `${CONTROL} ${PRESSED}` : SIMPLE_CONTROL} title={ko ? (full ? '처음 쓰는 사람에게 필요한 항목만 보입니다' : '모든 설정과 관리 항목이 보입니다') : undefined}>
            {full ? (ko ? '간단히 보기' : 'Simple view') : (ko ? '모두 보기' : 'Show everything')}
          </button>
          {full ? <>
            {historyButton}
            {technicalButton}
            {themeButton}
          </> : <details
            className="relative"
            ref={setMenu}
            onKeyDown={(event) => {
              if (event.key !== 'Escape' || !event.currentTarget.open) return;
              event.preventDefault();
              event.stopPropagation();
              event.currentTarget.open = false;
              event.currentTarget.querySelector('summary')?.focus();
            }}
            // Tab past the last item (or Shift+Tab before 더 보기) closes the menu, so the control that takes focus
            // is not left under the open panel.
            onBlur={(event) => {
              const next = event.relatedTarget as Node | null;
              if (next && event.currentTarget.open && !event.currentTarget.contains(next)) event.currentTarget.open = false;
            }}
            // The panel ends above the window bottom and scrolls, so on a short window (or a tall wrapped header) its
            // last items are not cut off by the app frame. Its items keep their height (*:shrink-0) and scroll instead.
            onToggle={(event) => {
              const panel = event.currentTarget.lastElementChild as HTMLElement | null;
              if (event.currentTarget.open && panel) panel.style.maxHeight = `${Math.max(160, window.innerHeight - panel.getBoundingClientRect().top - 8)}px`;
            }}
          >
            <summary className={`list-none cursor-pointer ${SIMPLE_CONTROL}`}>{ko ? '더 보기' : 'More'}</summary>
            <div
              className="absolute right-0 z-30 mt-1 flex min-w-[260px] flex-col gap-1 overflow-y-auto rounded border border-[#3B4B63] bg-[#131822] p-2 shadow-xl *:shrink-0"
              style={noDrag}
              onClick={(event) => { if ((event.target as Element).closest?.('button')) closeMenu(); }}
            >
              {runOn}
              {loadingNote}
              {!probeProblem && probeNote}
              {!engineProblem && engineChip}
              {modelLocation}
              {serversButton}
              <hr className="my-1 border-[#2B3547]" />
              {taskSelect}
              <hr className="my-1 border-[#2B3547]" />
              {historyButton}
              {technicalButton}
              {themeButton}
              {languageButton}
              <hr className="my-1 border-[#2B3547]" />
              {simpleControls}
              {simpleMenu}
            </div>
          </details>}
          {full && languageButton}
        </div>
      </div>

      {/* Row 2 (모두 보기): the inspection type and the six steps. Labels wrap on a narrow window instead of being cut off. */}
      {full && (
        <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2 bg-[#131822] px-4 py-2">
          {taskSelect}
          {stepBar}
          {modelLocation}
        </div>
      )}

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
