/**
 * src/renderer/components/dataset/DatasetStudio.tsx
 * Step 1: Industrial Dataset Studio with folder import, synthetic generator, split controls, and distribution charts.
 */

import React, { useCallback, useEffect, useRef, useState } from 'react';
import {
  FolderOpen,
  Sparkles,
  Sliders,
  AlertTriangle,
  Layers,
  ChevronLeft,
  ChevronRight,
  FolderArchive,
  ListChecks,
} from 'lucide-react';
import { useProjectStore } from '../../stores/useProjectStore';
import { readDatasetAuthority, useDatasetStore } from '../../stores/useDatasetStore';
import { registerPanelError } from '../../stores/assistContext';
import { useScopedError } from '../common/useScopedError';
import { useAnnotationStore } from '../../stores/useAnnotationStore';
import { api, getApiPersistenceIdentity, getProjectContext, getProjectContextGeneration, type DatasetRevisionRow, type DatasetSourceStatus } from '../../services/api';
import { host, hostErrorMessage } from '../../services/hostAdapter';
import { ProceduralGeneratorModal } from './ProceduralGeneratorModal';
import { DatasetVersionPanel } from './DatasetVersionPanel';
import { DatasetImportPanel } from './DatasetImportPanel';
import { validationStatus } from './datasetImportView';
import { DatasetWorkflowPanel } from './DatasetWorkflowPanel';
import {CaptureIntakePanel} from './CaptureIntakePanel';
import {DerivedDatasetPanel} from './DerivedDatasetPanel';
import {WorkflowImpactPanel} from '../common/WorkflowImpactPanel';
import {useDeliveryScope} from '../runtime/useDeliveryScope';
import {DataReadinessPanel} from './DataReadinessPanel';
import {DatasetStatisticsPanel} from './DatasetStatisticsPanel';
import { OperatorGuidanceBanner } from '../common/OperatorGuidanceBanner';
import { SimpleViewFold } from '../common/SimpleViewFold';
import { BrowserFolderPathInput } from './BrowserFolderPathInput';
import { ImportTaskSwitch } from './ImportTaskSwitch';
import { JargonTooltip } from '../common/JargonTooltip';
import { GuardrailBanner } from '../common/GuardrailBanner';
import { classDistributionStats } from './classDistribution';
import { calibratorView, severeClassImbalance, splitStatus } from './splitCalibrator';
import { importChecks } from './importChecklist';
import { isSplitUnavailable } from '../../utils/datasetSplitCapability';
import { ScopedDatasetThumbnail } from '../common/ScopedDatasetThumbnail';
import { subscribeDataVersionRequest, takeDataVersionRequest } from '../../stores/dataVersionRequest';
import { FieldReadinessPanel } from '../common/FieldReadinessPanel';
import { SpecialistFamilyNote } from '../common/SpecialistFamilyNote';
import {
  AUTO_FOLD_STATUS, FOCUS_SWITCH_NAME, FOLD_STATUS_SHORT, NARROW_BODY_HEIGHT, NARROW_BODY_NOTICE_SHORT, autoFoldDecision, focusStatus, narrowBodyNotice,
  storedDatasetFocus, writeDatasetFocus,
} from './datasetLayout';

const SPLIT_BADGE_KO: Record<string, string> = { train: '학습', val: '검증', test: '시험', not_split: '미분할', not_used: '미사용' };

/** The narrow-body notice's id for the switch's aria-describedby (one DatasetStudio mounts; the render tests' React mock has no useId). */
const NARROW_NOTICE_ID = 'dataset-narrow-notice';

/** Shown while a shared login, project switch or logout runs: it rebuilds this step, which would cancel a ZIP upload. */
const SESSION_CHANGING_NOTICE = '공동 작업 서버 연결을 바꾸는 중입니다. 끝나면 검증된 데이터 버전을 열 수 있습니다.';

export const DatasetStudio: React.FC = () => {
  const {key:sourceContextKey}=useDeliveryScope();
  const { task, language, projectDir, openImageForLabeling, project, isProjectBusy } = useProjectStore();
  const full = useProjectStore(state => state.viewMode) === 'full';
  const simpleView = useProjectStore(state => state.viewMode) === 'simple';
  const sessionChanging = useProjectStore(state => state.sharedSessionChanging);
  // The focus view folds the full-width panels above the toolbar (DATASET_HELPER_PANELS) so the sidebar and the gallery
  // keep the window's height; the choice is remembered. Off in both views until pressed (the simple view keeps 데이터 현황
  // on screen), with one exception: in the simple view, a body measured under UNUSABLE_BODY_HEIGHT folds the panels once
  // by itself for a viewer who never chose (a 0 px body is a hard failure for a first-time user). The full view keeps
  // every panel open unless pressed (its switch is highlighted instead). Their own press always wins afterwards.
  const [focusChoice, setFocusChoice] = useState<boolean | null>(() => storedDatasetFocus());
  const [autoFolded, setAutoFolded] = useState(false);
  const focus = focusChoice ?? (autoFolded && !full);
  const toggleFocus = () => { const next = !focus; writeDatasetFocus(next); setFocusChoice(next); };
  // The open panels can leave the body row a few pixels tall (or none): the toolbar's switch then says so and is
  // highlighted, and the panels' layout is untouched. Only shortness matters, so the height is kept clamped at the
  // threshold and resizing a roomy window does not render the screen again.
  const bodyRef = useRef<HTMLDivElement>(null);
  const [bodyHeight, setBodyHeight] = useState<number | null>(null);
  useEffect(() => {
    const body = bodyRef.current;
    if (!body || typeof ResizeObserver === 'undefined') return; // never measured: no notice, no fold by itself
    const measure = () => {
      const height = Math.round(body.getBoundingClientRect().height);
      setBodyHeight(Math.min(height, NARROW_BODY_HEIGHT));
      // Folds once: the fold itself gives the body room, and a later press is the viewer's choice (never unset here).
      if (autoFoldDecision(height, storedDatasetFocus())) setAutoFolded(true);
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(body);
    return () => observer.disconnect();
  }, []);
  const narrowNotice = narrowBodyNotice(bodyHeight, focus);
  // Said while the panels are folded: what the screen folded by itself and how to undo it, or what the viewer folded.
  const foldStatus = focusChoice === null && autoFolded && !full ? AUTO_FOLD_STATUS : focusStatus(focus);
  const {
    folderPath,
    datasetKey,
    sourceAuthority,
    hasSelectedFolder,
    importError,
    sourceSaveError,
    splitError,
    splitSupported,
    splitUnavailableReason,
    totalImages,
    sourceImages,
    unlabeledImages,
    classes,
    classCountUnit,
    classSplitCounts,
    split,
    images,
    totalImagesCount,
    page,
    pageSize,
    activeSplitFilter,
    activeClassFilter,
    activeLabelFilter,
    trainRatio,
    splitPrefill,
    isLoading,
    isSplitting,
    corruptedImages,
    excludedUndecodable,
    splitState,
    labelsMissing,
    expectedLayout,
    quickValidation,
    setSplitFilter,
    setClassFilter,
    setLabelFilter,
    setTrainRatio,
    importFolder,
    ensureImported,
    applySplit,
    loadImages,
    setShowGeneratorModal,
    generateSynthetic,
  } = useDatasetStore();
  // Relative thumbnails must never be rebuilt on a transport that did not
  // accept this source. Project acceptance can follow a server change later.
  const currentAuthority = readDatasetAuthority();
  const galleryCurrent = useCallback(() => {
    const selected = useProjectStore.getState(), dataset = useDatasetStore.getState(), context = getProjectContext();
    return Boolean(selected.project && !selected.isProjectBusy && sourceAuthority
      && dataset.sourceAuthority === sourceAuthority && sourceAuthority === readDatasetAuthority()
      && selected.project.source_dataset_dir === dataset.folderPath && selected.project.task === selected.task
      && dataset.datasetKey === `${dataset.folderPath}\0${selected.task}`
      && (context ? context.project_id === selected.project.id : getApiPersistenceIdentity() === 'local'));
  }, [sourceAuthority]);
  const sourceIsCurrent = galleryCurrent();
  const galleryImages = sourceIsCurrent ? images : [];

  // Thumbnail Grid Density (S: compact 96px, M: default 144px, L: detailed 200px)
  const [density, setDensity] = useState<'S' | 'M' | 'L'>('M');
  const [splitOpen, setSplitOpen] = useState(false);
  const [openingImageId, setOpeningImageId] = useState<string | null>(null);
  const [showVersions, setShowVersions] = useState(false);
  // The footer's data-change notice asks for the version a model was trained on; take the request now or when it comes.
  const [highlightVersionId, setHighlightVersionId] = useState<string | null>(null);
  useEffect(() => {
    const open = () => { const wanted = takeDataVersionRequest(); if (wanted) { setHighlightVersionId(wanted.versionId); setShowVersions(true); } };
    open();
    return subscribeDataVersionRequest(open);
  }, []);
  const [showImports, setShowImports] = useState(false);
  // The active validated version of this project (full validation), so the step can say validation is done.
  const [activeRevision, setActiveRevision] = useState<DatasetRevisionRow | null>(null);
  // Only the latest read applies: an older read still in flight (from the mount) never replaces a later one.
  const revisionRead = useRef(0);
  const loadActiveRevision = useCallback(() => {
    const read = ++revisionRead.current;
    api.datasetImports.revisions().then(listed => {
      if (revisionRead.current === read) setActiveRevision(listed.revisions.find(row => row.revision_id === listed.active_revision) || null);
    }).catch(() => { if (revisionRead.current === read) setActiveRevision(null); });
    return () => { if (revisionRead.current === read) revisionRead.current += 1; };
  }, []);
  useEffect(() => loadActiveRevision(), [loadActiveRevision, project?.id, project?.source_dataset_dir, task]);
  // Whether the active version still matches the source, asked of the backend by the index's own inventory definition
  // (the quick import counts differently: nqa2 review P2-1); asked again after each import of the folder.
  const [sourceStatus, setSourceStatus] = useState<DatasetSourceStatus | null>(null);
  const statusRead = useRef(0);
  const activeRevisionId = activeRevision?.revision_id ?? null;
  useEffect(() => {
    const read = ++statusRead.current;
    setSourceStatus(null);
    if (activeRevisionId && !isLoading && hasSelectedFolder) {
      api.datasetImports.sourceStatus(activeRevisionId).then(status => {
        if (statusRead.current === read) setSourceStatus(status);
      }).catch(() => { if (statusRead.current === read) setSourceStatus(null); });
    }
    return () => { if (statusRead.current === read) statusRead.current += 1; };
  }, [activeRevisionId, isLoading, hasSelectedFolder, quickValidation]);
  const validation = validationStatus(quickValidation, activeRevision, project?.source_dataset_dir, task, sourceStatus);
  const errorScope = JSON.stringify([sourceContextKey, projectDir, project?.id, project?.active_labelset_id, project?.source_dataset_dir, task, folderPath, datasetKey, currentAuthority]);
  const [imageOpenError, setImageOpenError] = useScopedError(errorScope);
  const rawErrors = [sourceSaveError, splitError, importError];
  const errorOwner = useRef({scope: errorScope, values: rawErrors, retired: [] as (string | null)[]});
  // Import failures have no accepted sourceAuthority yet. Keep their attempted folder in scope while refusing
  // unchanged errors carried over by a project/source reset before the dataset store has caught up.
  if (errorOwner.current.scope !== errorScope) {
    const previous = errorOwner.current.values;
    errorOwner.current = {scope: errorScope, values: rawErrors, retired: rawErrors.map((text, index) => text === previous[index] ? text : null)};
  } else {
    errorOwner.current.retired = errorOwner.current.retired.map((text, index) => text === rawErrors[index] ? text : null);
    errorOwner.current.values = rawErrors;
  }
  const currentErrors = rawErrors.filter((text, index) => text && text !== errorOwner.current.retired[index]);
  const storeErrorsCurrent = datasetKey === `${folderPath}\0${task}` && (!sourceAuthority || sourceAuthority === currentAuthority);
  const sharedError = [...new Set([...(storeErrorsCurrent ? currentErrors : []), imageOpenError].filter(Boolean))].join(' · ');
  useEffect(() => {
    if (!projectDir || !project || project.task !== task || sessionChanging || isProjectBusy) return;
    return registerPanelError('데이터 준비', sharedError);
  }, [errorScope, projectDir, project?.id, project?.task, task, sessionChanging, isProjectBusy, sharedError]);
  const folderSelection = useRef(0);
  useEffect(() => () => { folderSelection.current += 1; }, []);
  const splitUnavailableForTask = isSplitUnavailable(task, splitSupported);
  const splitUnavailableHint = splitUnavailableReason || (language === 'ko'
    ? '이 작업 유형은 화면 재분할을 지원하지 않습니다. 원본 train/val/test 폴더 구성을 사용하세요.'
    : 'This task does not support re-splitting here. Use the source train/val/test folders.');

  // Natural resolution dimension cache for image cards
  const [imgDimensions, setImgDimensions] = useState<Record<string, { w: number; h: number }>>({});

  // Precision 3-Way Split Calibrator Ratios (Train / Val / Test)
  // `touchedFor` names the dataset whose calibrator the user changed: until then the saved split is shown.
  const [splitRatios, setSplitRatios] = useState<{ train: number; val: number; test: number; touchedFor?: string | null }>(() => {
    const train = Math.round(trainRatio * 100) || 70;
    const remaining = 100 - train;
    const val = Math.round(remaining / 2);
    return { train, val, test: remaining - val };
  });
  const calibratorTouched = Boolean(datasetKey) && splitRatios.touchedFor === datasetKey;
  const consumedPrefill = useRef(0);
  useEffect(() => {
    if (!splitPrefill || consumedPrefill.current === splitPrefill.id) return;
    consumedPrefill.current = splitPrefill.id;
    if (splitPrefill.datasetKey !== datasetKey || splitPrefill.authority !== currentAuthority
        || sourceAuthority !== currentAuthority || splitUnavailableForTask) return;
    setSplitRatios({ train: splitPrefill.train, val: splitPrefill.val, test: splitPrefill.test, touchedFor: datasetKey });
  }, [splitPrefill, datasetKey, sourceAuthority, currentAuthority, splitUnavailableForTask]);

  const handleImageLoad = (filePath: string, naturalWidth: number, naturalHeight: number) => {
    setImgDimensions((prev) => {
      if (prev[filePath] && prev[filePath].w === naturalWidth && prev[filePath].h === naturalHeight) return prev;
      return { ...prev, [filePath]: { w: naturalWidth, h: naturalHeight } };
    });
  };

  const handlePresetSplit = (t: number, v: number, te: number) => {
    setSplitRatios({ train: t, val: v, test: te, touchedFor: datasetKey });
    setTrainRatio(t / 100);
  };

  const handleTrainSliderChange = (newTrain: number) => {
    const remaining = 100 - newTrain;
    const newVal = Math.round(remaining * (2 / 3));
    const newTest = remaining - newVal;
    setSplitRatios({ train: newTrain, val: newVal, test: newTest, touchedFor: datasetKey });
    setTrainRatio(newTrain / 100);
  };

  useEffect(() => {
    if (folderPath) ensureImported(task).catch(() => {});
  }, [folderPath, datasetKey, task, sourceAuthority, currentAuthority, sourceContextKey,
    project?.id, project?.project_dir, project?.source_dataset_dir, project?.task, projectDir, ensureImported]);

  const handleSelectFolder = async () => {
    const selection = ++folderSelection.current;
    const started = useProjectStore.getState();
    const projectId = started.project?.id;
    const startedDir = started.projectDir;
    const backendPort = started.backendPort;
    const generation = getProjectContextGeneration();
    const backend = getApiPersistenceIdentity();
    const isCurrent = () => selection === folderSelection.current
      && generation === getProjectContextGeneration() && backend === getApiPersistenceIdentity()
      && useProjectStore.getState().project?.id === projectId
      && useProjectStore.getState().backendPort === backendPort
      && useProjectStore.getState().projectDir === startedDir && useProjectStore.getState().task === task;
    if (!isCurrent()) return;
    setImageOpenError(null);
    let folder: string | null;
    try {
      folder = await host.selectFolder({ title: 'Select Industrial Dataset' });
    } catch (error) {
      // A browser has no folder dialog: say so instead of doing nothing.
      if (isCurrent()) setImageOpenError(hostErrorMessage(error, '폴더 선택 창을 열 수 없습니다.'));
      return;
    }
    if (folder && isCurrent()) {
      await importFolder(folder, task, undefined, false, isCurrent).catch(() => {});
    }
  };

  const handleOpenImage = async (imageId: string, filePath: string) => {
    if (openingImageId) return;
    setImageOpenError(null);
    setOpeningImageId(imageId);
    try {
      const opened = await openImageForLabeling(imageId, filePath);
      if (!opened) {
        setImageOpenError(useAnnotationStore.getState().saveMessage ||
          (language === 'ko' ? '이미지를 라벨링 화면에서 열지 못했습니다.' : 'Could not open image in Labeling Studio.'));
      }
    } catch (error) {
      setImageOpenError(error instanceof Error ? error.message : String(error));
    } finally {
      setOpeningImageId(null);
    }
  };

  const totalPages = Math.ceil(totalImagesCount / pageSize) || 1;

  // Class distribution calculation
  const classEntries = Object.entries(classes);
  const distribution = classDistributionStats(task, classes, classCountUnit === 'images' ? (totalImages || sourceImages) : totalImages, classCountUnit ?? undefined);
  const isImbalanced = severeClassImbalance(classes);
  const usesClassWeights = ['classification', 'patch_classification'].includes(task);
  const maxClassCount = Math.max(...Object.values(classes), 1);
  const appliedTotal = split.train + split.val + split.test;

  // The saved split until the calibrator is touched; status cards and gallery tabs always use the saved split.
  const calibrator = calibratorView({ touched: calibratorTouched, locked: splitUnavailableForTask, applied: split, totalImages, ratios: splitRatios });
  const showAppliedSplit = calibrator.mode === 'applied';
  // A saved split needs no attention from a first-time user: the simple view shows its counts on one line and keeps
  // the presets, slider and apply button one click away.
  // Labels come from what the backend says the split is (saved, source folders, automatic or stale), so an unsaved
  // automatic split is never shown as saved and only a saved one folds to a single line.
  const splitInfo = splitStatus({ mode: calibrator.mode, splitState, locked: splitUnavailableForTask });
  const splitFolded = simpleView && splitInfo.foldable && !splitOpen;
  const checklist = (importChecks({ task, totalImages: totalImages || 0, classes: classes || {}, split: split || { train: 0, val: 0, test: 0 },
    splitState: splitState ?? null, classSplitCounts: classSplitCounts ?? null, corrupted: corruptedImages?.length ?? 0,
    excludedUndecodable: excludedUndecodable ?? null, labelsMissing: Boolean(labelsMissing), expectedLayout: expectedLayout || '',
    splitLocked: splitUnavailableForTask }) as ReturnType<typeof importChecks> | null) ?? [];
  const shownCounts = calibrator.counts;
  const shownRatios = calibrator.ratios;

  const gridClassByDensity = {
    S: 'grid grid-cols-[repeat(auto-fill,minmax(96px,1fr))] gap-2',
    M: 'grid grid-cols-[repeat(auto-fill,minmax(144px,1fr))] gap-3',
    L: 'grid grid-cols-[repeat(auto-fill,minmax(200px,1fr))] gap-4',
  }[density];

  return (
    <div className="flex-1 flex flex-col h-full bg-[#0B0E14] text-slate-100 overflow-hidden">
      <OperatorGuidanceBanner step={1} />
      {/* The focus view folds these three panels (DATASET_HELPER_PANELS, in this order). The step title above, the
          alert and session rows and the toolbar below never fold. */}
      {!focus && <>
      <SimpleViewFold label="데이터 관리 도구" hint="이미지 검토·그룹 분할·라벨 교환, 현장 캡처, 파생 편집, 변경 영향 확인">
        <DatasetWorkflowPanel />
        <CaptureIntakePanel sourceDatasetPath={folderPath} contextKey={sourceContextKey} onUseSource={source=>importFolder(source,task,false)}/>
        <DerivedDatasetPanel sourceDatasetPath={folderPath} contextKey={sourceContextKey} onUseSource={source=>importFolder(source,task,false)}/>
        <WorkflowImpactPanel />
        {!full && <DataReadinessPanel />}
      </SimpleViewFold>
      <DatasetStatisticsPanel />
      {full && <DataReadinessPanel />}
      </>}
      {sourceSaveError && (
        <div role="alert" className="border-b border-amber-700 bg-amber-950/40 px-4 py-2 text-xs text-amber-200">
          {sourceSaveError}
        </div>
      )}
      {sessionChanging && (
        <div role="status" className="border-b border-sky-800/50 bg-sky-950/20 px-4 py-2 text-xs text-sky-200">
          {SESSION_CHANGING_NOTICE}
        </div>
      )}
      {/* Top Action Toolbar (Dark Steel Panel #131822) */}
      <div className="p-3 bg-[#131822] border-b border-[#2B3547] flex items-center justify-between gap-3">
        {/* 모두 보기 at 1024px: the left group is wider than the window, so it scrolls sideways and the right group (집중 보기,
            the split counts) stays on screen. Only there: the simple view's 더 보기 menu below would be clipped by it. */}
        <div className={`flex items-center space-x-3${full ? ' min-w-0 overflow-x-auto whitespace-nowrap' : ''}`}>
          <button
            id="workflow-data"
            onClick={handleSelectFolder}
            disabled={isLoading}
            className="flex items-center space-x-2 px-3 py-1.5 bg-blue-600 hover:bg-blue-500 rounded-[4px] text-xs font-semibold text-white border border-blue-400 transition-tactile cursor-pointer"
          >
            <FolderOpen className="w-4 h-4" />
            <span className="whitespace-nowrap">{language === 'ko' ? '데이터셋 폴더 열기' : 'Open Dataset Folder'}</span>
          </button>
          <BrowserFolderPathInput disabled={isLoading} onImport={folder => importFolder(folder, task, undefined, false).catch(() => {})} />

          {full ? <>
          <button
            onClick={() => setShowGeneratorModal(true)}
            className="flex items-center space-x-2 px-3 py-1.5 bg-[#1A212E] hover:bg-[#222B3D] border border-[#2B3547] rounded-[4px] text-xs font-semibold transition-tactile text-amber-300 cursor-pointer"
          >
            <Sparkles className="w-4 h-4 text-amber-400" />
            <span>{language === 'ko' ? '합성 데이터 생성기' : 'Procedural Generator'}</span>
          </button>

          <button
            type="button"
            onClick={() => setShowVersions(true)}
            disabled={!hasSelectedFolder || !projectDir || isLoading}
            title={hasSelectedFolder ? '데이터와 라벨 버전 저장·검증·복원' : '데이터셋을 먼저 불러오세요'}
            className="flex items-center gap-2 rounded-[4px] border border-sky-700/60 bg-sky-950/30 px-3 py-1.5 text-xs font-semibold text-sky-200 hover:bg-sky-900/40 disabled:cursor-not-allowed disabled:opacity-40"
          >
            <FolderArchive className="h-4 w-4" />
            <span>데이터 버전</span>
          </button>

          </> : <details className="relative">
            <summary className="list-none cursor-pointer whitespace-nowrap rounded-[4px] border border-[#2B3547] bg-[#1A212E] px-3 py-1.5 text-xs font-semibold text-slate-300">{language === 'ko' ? '더 보기' : 'More'}</summary>
            <div className="absolute left-0 z-30 mt-1 flex min-w-[200px] flex-col gap-1 rounded border border-[#3B4B63] bg-[#131822] p-2 shadow-xl">
          <button
            onClick={() => setShowGeneratorModal(true)}
            className="flex items-center space-x-2 px-3 py-1.5 bg-[#1A212E] hover:bg-[#222B3D] border border-[#2B3547] rounded-[4px] text-xs font-semibold transition-tactile text-amber-300 cursor-pointer"
          >
            <Sparkles className="w-4 h-4 text-amber-400" />
            <span>{language === 'ko' ? '합성 데이터 생성기' : 'Procedural Generator'}</span>
          </button>

          <button
            type="button"
            onClick={() => setShowVersions(true)}
            disabled={!hasSelectedFolder || !projectDir || isLoading}
            title={hasSelectedFolder ? '데이터와 라벨 버전 저장·검증·복원' : '데이터셋을 먼저 불러오세요'}
            className="flex items-center gap-2 rounded-[4px] border border-sky-700/60 bg-sky-950/30 px-3 py-1.5 text-xs font-semibold text-sky-200 hover:bg-sky-900/40 disabled:cursor-not-allowed disabled:opacity-40"
          >
            <FolderArchive className="h-4 w-4" />
            <span>데이터 버전</span>
          </button>
          {/* The simple view keeps the verified-version workflow here instead of in the toolbar. */}
          {simpleView && (
            <button
              type="button"
              onClick={() => setShowImports(true)}
              disabled={!hasSelectedFolder || !project?.id || isLoading || sessionChanging}
              title={sessionChanging ? SESSION_CHANGING_NOTICE : hasSelectedFolder ? '모든 이미지를 열어 검증한 고정 버전을 만들고 채택합니다' : '데이터셋을 먼저 불러오세요'}
              className="flex items-center gap-2 rounded-[4px] border border-emerald-700/60 bg-emerald-950/30 px-3 py-1.5 text-xs font-semibold text-emerald-200 hover:bg-emerald-900/40 disabled:cursor-not-allowed disabled:opacity-40"
            >
              <ListChecks className="h-4 w-4" />
              <span className="whitespace-nowrap">검증된 데이터 버전</span>
            </button>
          )}

            </div>
          </details>}
          {!simpleView && (
          <button
            type="button"
            onClick={() => setShowImports(true)}
            disabled={!hasSelectedFolder || !project?.id || isLoading || sessionChanging}
            title={sessionChanging ? SESSION_CHANGING_NOTICE : hasSelectedFolder ? '모든 이미지를 열어 검증한 고정 버전을 만들고 채택합니다' : '데이터셋을 먼저 불러오세요'}
            className="flex items-center gap-2 rounded-[4px] border border-emerald-700/60 bg-emerald-950/30 px-3 py-1.5 text-xs font-semibold text-emerald-200 hover:bg-emerald-900/40 disabled:cursor-not-allowed disabled:opacity-40"
          >
            <ListChecks className="h-4 w-4" />
            <span className="whitespace-nowrap">검증된 데이터 버전</span>
          </button>
          )}

          {/* The simple view names the folder, not its whole path (the tooltip keeps the path). */}
          <span className={`text-xs text-slate-400 truncate max-w-sm ${simpleView ? '' : 'font-mono'}`} title={folderPath}>
            {simpleView && folderPath ? `폴더: ${folderPath.split(/[\\/]/).filter(Boolean).pop()}` : folderPath}
          </span>
        </div>

        {/* The focus switch and the Partition Status Annunciator (the gallery tabs below show the same counts in the
            simple view). One group on this row that never wraps or shrinks: a new row would take its height from the body.
            The fold/narrow hints start at width 0 and take only the row's leftover room (full text in the title, and the
            switch turns amber), so on a short row the left group's buttons keep their room. */}
        <div className="flex shrink-0 grow items-center justify-end gap-2">
          {focus && <span title={foldStatus} className="w-0 min-w-0 max-w-sm grow truncate text-right text-[11px] text-slate-400"><span className="xl:hidden">{FOLD_STATUS_SHORT}</span><span className="hidden xl:inline">{foldStatus}</span></span>}
          {narrowNotice && <span id={NARROW_NOTICE_ID} role="status" title={narrowNotice} className="w-0 min-w-0 max-w-xs grow truncate text-right text-[11px] text-amber-200"><span className="xl:hidden">{NARROW_BODY_NOTICE_SHORT}</span><span className="hidden xl:inline">{narrowNotice}</span></span>}
          <button type="button" aria-pressed={focus} onClick={toggleFocus} title={focus ? '위쪽 패널을 다시 보입니다' : '위쪽 패널을 접어 목록과 갤러리를 키웁니다'}
            aria-describedby={narrowNotice ? NARROW_NOTICE_ID : undefined}
            className={`shrink-0 rounded border px-2 py-0.5 text-[11px] hover:bg-slate-800 ${focus ? 'border-cyan-500 bg-cyan-950/40 text-cyan-100' : narrowNotice ? 'border-amber-400 bg-amber-950/50 text-amber-100' : 'border-slate-600 text-slate-200'}`}>{FOCUS_SWITCH_NAME}</button>
          <div className={`${simpleView ? 'hidden' : 'flex'} shrink-0 items-center space-x-3 bg-[#0B0E14] px-3 py-1.5 rounded-[4px] border border-[#2B3547] text-xs font-mono tabular-nums`}>
            <div className="flex items-center space-x-1.5">
              <span className="w-2 h-2 rounded-full bg-[#3B82F6]" />
              <span className="text-slate-400">{language === 'ko' ? '학습' : 'Train'}:</span>
              <span className="font-bold text-blue-400">{split.train}</span>
            </div>
            <span className="w-[1px] h-3 bg-[#2B3547]" />
            <div className="flex items-center space-x-1.5">
              <span className="w-2 h-2 rounded-full bg-[#F59E0B]" />
              <span className="text-slate-400">{language === 'ko' ? '검증' : 'Val'}:</span>
              <span className="font-bold text-amber-400">{split.val}</span>
            </div>
            <span className="w-[1px] h-3 bg-[#2B3547]" />
            <div className="flex items-center space-x-1.5">
              <span className="w-2 h-2 rounded-full bg-[#10B981]" />
              <span className="text-slate-400">{language === 'ko' ? '시험' : 'Test'}:</span>
              <span className="font-bold text-emerald-400">{split.test}</span>
            </div>
          </div>
        </div>
      </div>

      {/* Main Studio Body: Left Sidebar (Stats, Split Calibrator & Distribution) + Right (Gallery) */}
      <div ref={bodyRef} className="flex-1 flex overflow-hidden" data-dataset-work-area>
        {/* Left Stats, Split Calibrator & Class Distribution Panel. A fixed width that never shrinks: `w-84` is not on
            this Tailwind 3 scale, so the sidebar took its longest unwrapped line (a checklist warning is ~2000px) and
            left the gallery zero pixels wide, off screen, with only the first cards inside its lazy-read margin. */}
        <aside data-surface="page" className="w-[21rem] shrink-0 bg-[#131822] border-r border-[#2B3547] p-3 flex flex-col space-y-3 overflow-y-auto">
          {/* Before-training checks from the import result: what will block training or step 4, and what is weak. */}
          {hasSelectedFolder && checklist.length > 0 && (
            <section aria-label="학습 전 점검" className="rounded-[4px] border border-[#2B3547] bg-[#1A212E] p-2.5 text-xs">
              <h3 className="mb-1.5 font-semibold text-slate-100">학습 전 점검</h3>
              <ul className="space-y-1">
                {checklist.map(check => (
                  <li key={check.id} data-check={check.id} data-level={check.level}
                    className={check.level === 'block' ? 'text-rose-200' : check.level === 'warn' ? 'text-amber-200' : 'text-emerald-200'}>
                    <span aria-hidden="true">{check.level === 'block' ? '✕ ' : check.level === 'warn' ? '! ' : '✓ '}</span>{check.text}
                  </li>
                ))}
              </ul>
            </section>
          )}
          {hasSelectedFolder && <FieldReadinessPanel />}
          {hasSelectedFolder && <SpecialistFamilyNote step={1} />}
          {/* Summary KPI Readout Tiles */}
          <div className="grid grid-cols-2 gap-2 text-xs">
            <div className="p-2.5 bg-[#1A212E] rounded-[4px] border border-[#2B3547]">
              <span className="text-slate-400 block text-[10px] tracking-wide uppercase mb-0.5">
                {language === 'ko' ? '학습 가능 이미지' : 'Trainable Images'}
              </span>
              <span className="text-lg font-bold font-mono tabular-nums text-slate-100">{totalImages}</span>
            </div>
            <div className="p-2.5 bg-[#1A212E] rounded-[4px] border border-[#2B3547]">
              <span className="text-slate-400 block text-[10px] tracking-wide uppercase mb-0.5">
                {task === 'segmentation' && splitUnavailableForTask
                  ? (language === 'ko' ? '마스크 채널' : 'Mask Channels')
                  : (language === 'ko' ? '클래스 종류' : 'Classes')}
              </span>
              <span className="text-lg font-bold font-mono tabular-nums text-blue-400">
                {classEntries.length}
              </span>
            </div>
            {full && <>
            <div className="p-2.5 bg-[#1A212E] rounded-[4px] border border-[#2B3547]">
              <div className="flex items-center justify-between">
                <span className="text-slate-400 block text-[10px] tracking-wide uppercase mb-0.5">
                  {language === 'ko' ? '적용된 학습 분할' : 'Applied Train Split'}
                </span>
                <span className="w-1.5 h-1.5 rounded-full bg-[#3B82F6]" />
              </div>
              <span className="text-base font-bold font-mono tabular-nums text-blue-300">
                {split.train}
              </span>
            </div>
            <div className="p-2.5 bg-[#1A212E] rounded-[4px] border border-[#2B3547]">
              <div className="flex items-center justify-between">
                <span className="text-slate-400 block text-[10px] tracking-wide uppercase mb-0.5">
                  {language === 'ko' ? '적용된 검증 / 테스트 분할' : 'Applied Val / Test Split'}
                </span>
                <span className="w-1.5 h-1.5 rounded-full bg-[#F59E0B]" />
              </div>
              <span className="text-base font-bold font-mono tabular-nums text-amber-300">
                {split.val + split.test}
              </span>
            </div>
            </>}
          </div>

          {split.train + split.val + split.test === 0 && totalImages > 0 && (
            <div className="text-[11px] text-amber-300">
              {language === 'ko' ? '분할을 아직 적용하지 않았습니다. 아래 비율은 적용 전 예상치입니다.' : 'Split not applied yet. Ratios below are estimates.'}
            </div>
          )}

          {/* Corrupted Images Alert if any */}
          {unlabeledImages > 0 && (
            <div className="p-2.5 bg-amber-950/30 border border-amber-500/40 rounded text-xs text-amber-200">
              원본 {sourceImages}장 중 주석 없는 {unlabeledImages}장은 갤러리에서만 보이며 학습 분할에서 제외됩니다.
            </div>
          )}
          {task === 'segmentation' && !splitUnavailableForTask && classes.defect_mask && !classes.OK && !classes.good && (
            <div className="p-2.5 bg-amber-950/30 border border-amber-500/40 rounded text-xs text-amber-200">
              결함 주석만 확인되었습니다. 정상(OK) 이미지가 없으면 과검률과 양산 판정 품질을 검증할 수 없습니다.
            </div>
          )}
          {validation && (
            <div role="status" className={validation.tone === 'ok'
              ? 'p-2.5 bg-emerald-950/30 border border-emerald-500/40 rounded text-xs text-emerald-200'
              : 'p-2.5 bg-amber-950/30 border border-amber-500/40 rounded text-xs text-amber-200'}>
              {validation.text}
            </div>
          )}
          {corruptedImages.length > 0 && (
            <div className="p-2.5 bg-[#EF4444]/10 border border-[#EF4444]/40 rounded-[4px] text-xs text-red-200">
              <div className="flex items-center space-x-1.5 font-semibold text-[#EF4444] mb-1">
                <AlertTriangle className="w-4 h-4" />
                <span>
                  {language === 'ko'
                    ? `손상된 이미지 ${corruptedImages.length}건 감지`
                    : `${corruptedImages.length} Corrupted Images`}
                </span>
              </div>
              <p className="text-[11px] text-red-300">
                {language !== 'ko' ? (excludedUndecodable?.recorded ? 'Unreadable images were left out of training and splits; the files are unchanged.' : 'Unreadable images found; reopen the project and import again to leave them out.')
                  : excludedUndecodable?.recorded
                    ? `읽을 수 없는 이미지 ${excludedUndecodable.excluded.length}장을 학습·분할에서 뺐습니다 (원본 파일은 그대로): ${excludedUndecodable.excluded.slice(0, 5).join(', ')}${excludedUndecodable.excluded.length > 5 ? ' …' : ''}`
                    : '읽을 수 없는 이미지가 있습니다. 프로젝트를 열고 다시 불러오면 학습에서 자동으로 빠집니다.'}
              </p>
            </div>
          )}

          {/* Class Imbalance Warning if > 20:1 */}
          {isImbalanced && (
            <div className="p-2.5 bg-[#F59E0B]/10 border border-[#F59E0B]/40 rounded-[4px] text-xs text-amber-200">
              <div className="flex items-center justify-between mb-1">
                <div className="flex items-center space-x-1.5 font-semibold text-[#F59E0B]">
                  <AlertTriangle className="w-4 h-4" />
                  <span>
                    {language === 'ko' ? '극심한 클래스 불균형 (ERR_003)' : 'Class Imbalance Warning'}
                  </span>
                </div>
              </div>
              <p className="text-[11px] text-amber-300">
                {language === 'ko'
                  ? (usesClassWeights
                    ? '다수 클래스와 소수 클래스 비율이 20:1을 초과합니다. 분류 학습에는 클래스별 이미지 수에 따른 가중치가 적용됩니다. 적은 클래스의 이미지를 더 모으고 클래스별 평가 결과를 확인하세요.'
                    : '다수 클래스와 소수 클래스 비율이 20:1을 초과합니다. 적은 클래스의 이미지와 라벨을 확인하고 클래스별 평가 결과를 검토하세요.')
                  : (usesClassWeights
                    ? 'Class ratio exceeds 20:1. Classification training uses weights from class image counts. Collect more minority-class images and review per-class evaluation results.'
                    : 'Class ratio exceeds 20:1. Review minority-class images, labels and per-class evaluation results.')}
              </p>
            </div>
          )}

          {/* Precision 3-Way Split Calibrator */}
          <div className="p-3 bg-[#1A212E] rounded-[4px] border border-[#2B3547] flex flex-col space-y-2.5">
            <div className="flex items-center justify-between pb-2 border-b border-[#2B3547]">
              <div className="flex items-center space-x-1.5">
                <Sliders className="w-3.5 h-3.5 text-blue-400" />
                <span className="text-xs font-bold text-slate-200 tracking-wide uppercase">
                  {language === 'ko' ? `학습 · 검증 · 시험 분할 (${splitInfo.label})`
                    : showAppliedSplit ? 'Train / val / test split (saved)' : 'Train / val / test split (preview)'}
                </span>
              </div>
              {simpleView && splitInfo.foldable ? <span className="flex items-center gap-2 text-[11px] text-slate-300">
                {splitFolded && <span className="tabular-nums">학습 {calibrator.counts.train} · 검증 {calibrator.counts.val} · 시험 {calibrator.counts.test}</span>}
                <button type="button" aria-expanded={!splitFolded} onClick={() => setSplitOpen(open => !open)} className="rounded border border-[#2B3547] px-2 py-0.5 text-slate-200 hover:bg-[#222B3D]">{splitFolded ? '바꾸기' : '접기'}</button>
              </span> : <JargonTooltip termKey="early_stopping" />}
            </div>
            <div className={splitFolded ? 'hidden' : 'flex flex-col space-y-2.5'}>

            <p className={`text-xs ${splitInfo.tone === 'warn' ? 'text-amber-200' : 'text-slate-400'}`} role={splitInfo.tone === 'warn' ? 'status' : undefined}>
              {language === 'ko' ? splitInfo.description
                : showAppliedSplit ? (splitUnavailableForTask ? 'The source folder\'s own split is used.' : 'This is the saved split. Pick a ratio below and apply it to change it.')
                : 'Preview of the new split. Saved splits change when you select Apply.'}
            </p>

            {/* Quick Calibration Presets */}
            <div className="grid grid-cols-3 gap-1.5">
              {[
                { label: '70 / 20 / 10', t: 70, v: 20, te: 10, title: '표준 분할' },
                { label: '80 / 10 / 10', t: 80, v: 10, te: 10, title: '학습 집중' },
                { label: '60 / 20 / 20', t: 60, v: 20, te: 20, title: '검증 집중' },
              ].map((preset) => {
                const isActive =
                  splitRatios.train === preset.t &&
                  splitRatios.val === preset.v &&
                  splitRatios.test === preset.te;
                return (
                  <button
                    key={preset.label}
                    type="button"
                    onClick={() => handlePresetSplit(preset.t, preset.v, preset.te)}
                    disabled={splitUnavailableForTask}
                    className={`py-1 text-[10px] font-mono tabular-nums font-semibold rounded-[3px] border transition-tactile cursor-pointer ${
                      isActive && !splitUnavailableForTask
                        ? 'bg-[#3B82F6] text-white border-blue-400 font-bold'
                        : 'bg-[#131822] text-slate-300 border-[#2B3547] hover:bg-[#222B3D] hover:text-white disabled:opacity-40 disabled:cursor-not-allowed'
                    }`}
                    title={preset.title}
                  >
                    {preset.label}
                  </button>
                );
              })}
            </div>

            {/* Master 3-Way Calibration Bar */}
            <div className="space-y-1">
              <div className="w-full bg-[#0B0E14] h-2.5 rounded-[2px] border border-[#2B3547] overflow-hidden flex">
                <div
                  className="bg-[#3B82F6] h-full transition-all duration-200"
                  style={{ width: `${shownRatios.train}%` }}
                  title={`학습: ${shownRatios.train}% (${shownCounts.train}장)`}
                />
                <div
                  className="bg-[#F59E0B] h-full transition-all duration-200"
                  style={{ width: `${shownRatios.val}%` }}
                  title={`검증: ${shownRatios.val}% (${shownCounts.val}장)`}
                />
                <div
                  className="bg-[#10B981] h-full transition-all duration-200"
                  style={{ width: `${shownRatios.test}%` }}
                  title={`시험: ${shownRatios.test}% (${shownCounts.test}장)`}
                />
              </div>
              <div className="flex items-center justify-between text-[10px] font-mono tabular-nums">
                <span className="text-blue-400 font-semibold">
                  {language === 'ko' ? '학습' : 'Train'}: {shownRatios.train}% ({shownCounts.train})
                </span>
                <span className="text-amber-400 font-semibold">
                  {language === 'ko' ? '검증' : 'Val'}: {shownRatios.val}% ({shownCounts.val})
                </span>
                <span className="text-emerald-400 font-semibold">
                  {language === 'ko' ? '시험' : 'Test'}: {shownRatios.test}% ({shownCounts.test})
                </span>
              </div>
            </div>

            {/* Train Ratio Slider */}
            <div className="space-y-1">
              <div className="flex justify-between items-center text-[11px] text-slate-300">
                <span>{language === 'ko' ? '학습 세트 비율' : 'Train Ratio'}</span>
                <span className="font-mono tabular-nums font-bold text-blue-400">
                  {shownRatios.train}%
                </span>
              </div>
              <input
                type="range"
                min="50"
                max="90"
                step="5"
                value={shownRatios.train}
                onChange={(e) => handleTrainSliderChange(parseInt(e.target.value, 10))}
                disabled={splitUnavailableForTask}
                className="w-full accent-blue-500 bg-[#0B0E14] h-1.5 rounded cursor-pointer disabled:cursor-not-allowed disabled:opacity-50"
              />
            </div>

            {/* Apply Split Button */}
            <button
              type="button"
              onClick={() => void applySplit(splitRatios.train / 100, splitRatios.val / 100, splitRatios.test / 100)
                .then(() => setSplitRatios(prev => ({ ...prev, touchedFor: null })))
                .catch(() => {})}
              disabled={!sourceIsCurrent || isSplitting || totalImages === 0 || splitUnavailableForTask}
              title={splitUnavailableForTask ? splitUnavailableHint : undefined}
              className="w-full py-1.5 bg-[#2563EB] hover:bg-blue-500 text-white rounded-[4px] border border-blue-400 text-xs font-semibold flex items-center justify-center space-x-1.5 transition-tactile cursor-pointer disabled:opacity-40"
            >
              <Sliders className="w-3.5 h-3.5" />
              <span>
                {isSplitting
                  ? language === 'ko'
                    ? '분할 연산 중...'
                    : 'Splitting...'
                  : language === 'ko'
                  ? '이 비율로 나누기'
                  : 'Apply 3-Way Split'}
              </span>
            </button>
            {splitUnavailableForTask && totalImages > 0 && (
              <p className="text-[11px] text-amber-300">{splitUnavailableHint}</p>
            )}
            {splitError && (
              <div role="alert" className="text-[11px] text-red-200 bg-red-950/40 border border-red-700/60 rounded px-2 py-1.5">
                {splitError}
              </div>
            )}
            </div>
          </div>

          {/* Digital Class Gauges */}
          <div className="p-3 bg-[#1A212E] rounded-[4px] border border-[#2B3547] flex-1 flex flex-col">
            <div className="flex items-center justify-between mb-2.5 pb-2 border-b border-[#2B3547]">
              <div className="flex items-center space-x-1.5">
                <Layers className="w-3.5 h-3.5 text-blue-400" />
                <span className="text-xs font-bold text-slate-200 tracking-wide uppercase">
                  {task === 'segmentation' && splitUnavailableForTask
                    ? (language === 'ko' ? '마스크 채널별 이미지 수' : 'Images per Mask Channel')
                    : distribution.countUnit === 'objects'
                    ? (language === 'ko' ? '클래스별 객체 주석 분포' : 'Annotated Objects by Class')
                    : (language === 'ko' ? '클래스별 이미지 수' : 'Class Digital Gauges')}
                </span>
              </div>
              <span className="text-[11px] font-mono tabular-nums text-slate-400 bg-[#0B0E14] px-1.5 py-0.5 rounded border border-[#2B3547]">
                {classEntries.length} {task === 'segmentation' && splitUnavailableForTask
                  ? (language === 'ko' ? '채널' : 'channels')
                  : (language === 'ko' ? '분류' : 'classes')}
              </span>
            </div>

            {task === 'segmentation' && splitUnavailableForTask && (
              <p className="mb-2 text-[10px] text-slate-400">
                {language === 'ko'
                  ? '이미지별 정상·결함 수가 아닌 마스크 채널별 이미지 수입니다.'
                  : 'Counts images per mask channel, not normal versus defect images.'}
              </p>
            )}
            {distribution.countUnit === 'objects' && classEntries.length > 0 && (
              <p className="mb-2 text-[10px] text-slate-400">
                {language === 'ko'
                  ? `라벨된 이미지 ${distribution.imageCount}장 · 객체 주석 ${distribution.objectCount}개. 비율은 전체 객체 주석 수 기준입니다.`
                  : `${distribution.imageCount} labeled images · ${distribution.objectCount} object annotations. Percentages use the object total.`}
              </p>
            )}
            {classCountUnit === 'images' && classEntries.length > 0 && (
              <p className="mb-2 text-[10px] text-slate-400">
                {language === 'ko'
                  ? `학습에 쓰는 이미지 ${totalImages || sourceImages}장 기준입니다${sourceImages > totalImages && totalImages ? ` (사용 안 함 ${sourceImages - totalImages}장 제외)` : ''}. 여러 클래스가 있는 이미지는 클래스별로 각각 집계합니다.`
                  : `Percentages use the ${totalImages || sourceImages} images training reads. Images with multiple classes count once in each class.`}
              </p>
            )}

            <div className="space-y-2 flex-1 overflow-y-auto pr-1">
              {classEntries.length === 0 ? (
                <div className="text-xs text-slate-400 italic py-4 text-center">
                  {language === 'ko' ? '클래스 정보 없음' : 'No classes loaded'}
                </div>
              ) : (
                classEntries.map(([cName, count]) => {
                  const pct = Math.round((count / maxClassCount) * 100);
                  const pctOfTotal = distribution.sharePercent(count);
                  const isNormal = cName.toLowerCase() === 'ok' || cName.toLowerCase() === 'good';
                  const isSelected = activeClassFilter === cName;

                  const appliedClassSplit = appliedTotal > 0 && (task === 'classification' || task === 'anomaly');
                  const confirmedClassSplit = appliedClassSplit && classSplitCounts != null;
                  const savedCounts = classSplitCounts?.[cName] || { train: 0, val: 0, test: 0 };
                  const cTrain = confirmedClassSplit ? savedCounts.train : Math.round(count * (splitRatios.train / 100));
                  const cVal = confirmedClassSplit ? savedCounts.val : Math.round(count * (splitRatios.val / 100));
                  const cTest = confirmedClassSplit ? savedCounts.test : Math.max(0, count - cTrain - cVal);

                  return (
                    <div
                      key={cName}
                      onClick={() => { if (!(task === 'segmentation' && splitUnavailableForTask)) setClassFilter(isSelected ? null : cName); }}
                      className={`p-2 rounded-[4px] bg-[#131822] border transition-tactile select-none ${
                        task === 'segmentation' && splitUnavailableForTask ? 'cursor-default' : 'cursor-pointer'
                      } ${
                        isSelected
                          ? 'border-[#3B82F6] ring-1 ring-[#3B82F6] bg-[#1E293B]'
                          : 'border-[#2B3547] hover:border-slate-500 hover:bg-[#222B3D]'
                      }`}
                    >
                      {/* Top row: Indicator, Name, Count Badge, Percentage */}
                      <div className="flex items-center justify-between text-xs mb-1">
                        <div className="flex items-center space-x-1.5 truncate">
                          <span
                            className={`w-2 h-2 rounded-full shrink-0 ${
                              task === 'segmentation' && splitUnavailableForTask
                                ? 'bg-[#3B82F6]'
                                : isNormal ? 'bg-[#10B981]' : 'bg-[#EF4444]'
                            }`}
                          />
                          <span className="font-semibold text-slate-200 truncate">{cName}</span>
                        </div>
                        <div className="flex items-center space-x-2 shrink-0">
                          <span className="font-mono tabular-nums text-[10px] text-slate-400">
                            {pctOfTotal}%
                          </span>
                          <span className="font-mono tabular-nums text-[11px] font-bold text-slate-100 bg-[#0B0E14] px-1.5 py-0.5 rounded-[3px] border border-[#2B3547]">
                            {language === 'ko'
                              ? `${count}${distribution.countUnit === 'objects' ? '개 객체' : '장'}`
                              : `${count} ${distribution.countUnit}`}
                          </span>
                        </div>
                      </div>

                      {/* Middle row: Linear Level Meter */}
                      <div className="w-full bg-[#0B0E14] h-1.5 rounded-[2px] border border-[#2B3547] overflow-hidden my-1">
                        <div
                          className={`h-full transition-all duration-300 ${
                            isNormal ? 'bg-[#10B981]' : 'bg-[#EF4444]'
                          }`}
                          style={{ width: `${pct}%` }}
                        />
                      </div>

                      {appliedClassSplit && !confirmedClassSplit && <p className="mt-1 text-[10px] text-slate-400">
                        {language === 'ko' ? '적용된 분할 집계 확인 중' : 'Loading applied split counts'}
                      </p>}
                      {!splitUnavailableForTask && (!appliedClassSplit || confirmedClassSplit) && <div className="mt-1 pt-1 border-t border-[#2B3547]/50 flex flex-wrap items-center justify-between gap-1 text-[10px] font-mono tabular-nums text-slate-400">
                        <span>{language === 'ko'
                          ? (confirmedClassSplit ? '적용된 분할' : '분할 예상')
                          : (confirmedClassSplit ? 'Applied split' : 'Split preview')}</span>
                        <div className="flex space-x-2">
                          <span className="text-blue-400">{language === 'ko' ? '학습' : 'T'}: {cTrain}</span>
                          <span className="text-amber-400">{language === 'ko' ? '검증' : 'V'}: {cVal}</span>
                          <span className="text-emerald-400">{language === 'ko' ? '시험' : 'Test'}: {cTest}</span>
                        </div>
                        <div className="w-16 bg-[#0B0E14] h-1 rounded-[1px] overflow-hidden flex border border-[#2B3547]/60">
                          <div
                            className="bg-[#3B82F6] h-full"
                            style={{ width: `${count > 0 ? (cTrain / count) * 100 : 0}%` }}
                            title={`학습: ${cTrain}`}
                          />
                          <div
                            className="bg-[#F59E0B] h-full"
                            style={{ width: `${count > 0 ? (cVal / count) * 100 : 0}%` }}
                            title={`검증: ${cVal}`}
                          />
                          <div
                            className="bg-[#10B981] h-full"
                            style={{ width: `${count > 0 ? (cTest / count) * 100 : 0}%` }}
                            title={`시험: ${cTest}`}
                          />
                        </div>
                      </div>}
                    </div>
                  );
                })
              )}
            </div>
          </div>
        </aside>

        {/* Right Gallery Container */}
        <main className="flex-1 flex flex-col bg-[#0B0E14] overflow-hidden">
          {importError && (
            <div role="alert" className="m-4 mb-0 p-3 bg-red-950/30 border border-red-500/40 rounded text-xs text-red-200">
              <div className="font-semibold mb-1">{language === 'ko' ? '데이터셋을 불러오지 못했습니다' : 'Could not import dataset'}</div>
              <div>{importError}</div>
              <ImportTaskSwitch error={importError} folder={folderPath} onImport={(folder, nextTask) => importFolder(folder, nextTask, undefined, false)} />
            </div>
          )}
          {imageOpenError && (
            <div role="alert" className="m-4 mb-0 p-3 bg-red-950/30 border border-red-500/40 rounded text-xs text-red-200">
              {imageOpenError}
            </div>
          )}
          {totalImages === 0 && !importError && (
            <div className="p-4 pb-0">
              <GuardrailBanner
                type="warning"
                stepContext="1단계 데이터 안내"
                title="검사 대상 데이터셋이 등록되지 않았습니다"
                description="AI 딥러닝 모델 학습 및 불량 영역 라벨링을 시작하려면 반도체/부품 검사 이미지를 먼저 등록해야 합니다."
                shopFloorTip="실제 검사 이미지 폴더를 선택하거나 기능 확인용 합성 데이터를 생성할 수 있습니다."
                actions={[
                  {
                    label: '검사 이미지 폴더 선택',
                    icon: FolderOpen,
                    variant: 'primary',
                    loadingText: '폴더 선택 중...',
                    onClick: handleSelectFolder,
                  },
                  {
                    label: '합성 결함 데이터 100장 즉시 생성',
                    icon: Sparkles,
                    variant: 'secondary',
                    loadingText: '합성 데이터 생성 중...',
                    onClick: async () => {
                      await generateSynthetic({
                        task,
                        num_samples: 100,
                        modality: 'pcb',
                        split_ratio: 0.8,
                      });
                    },
                  },
                ]}
              />
            </div>
          )}
          {/* Gallery Filter & Grid Density Toolbar */}
          <div className="px-4 py-2 bg-[#131822] border-b border-[#2B3547] flex flex-wrap items-center justify-between gap-3 text-xs">
            <div className="flex flex-wrap items-center gap-1.5">
              {[
                { id: 'all', labelKo: `전체 (${sourceImages})`, labelEn: `All (${sourceImages})` },
                { id: 'train', labelKo: `학습용 (${split.train})`, labelEn: `Train (${split.train})` },
                { id: 'val', labelKo: `검증용 (${split.val})`, labelEn: `Val (${split.val})` },
                { id: 'test', labelKo: `테스트용 (${split.test})`, labelEn: `Test (${split.test})` },
              ].map((tab) => (
                <button
                  key={tab.id}
                  onClick={() => setSplitFilter(tab.id as any)}
                  className={`px-2.5 py-1 rounded-[4px] font-mono tabular-nums text-xs transition-tactile cursor-pointer ${
                    activeSplitFilter === tab.id
                      ? 'bg-[#2563EB] text-white font-semibold border border-blue-400'
                      : 'bg-[#1A212E] text-slate-300 hover:bg-[#222B3D] hover:text-white border border-[#2B3547]'
                  }`}
                >
                  {language === 'ko' ? tab.labelKo : tab.labelEn}
                </button>
              ))}

              {(task === 'detection' || task === 'segmentation') && (unlabeledImages > 0 || activeLabelFilter !== 'all') && (
                <div className="flex items-center gap-1 border-l border-[#2B3547] pl-2 ml-1" aria-label={language === 'ko' ? '라벨 상태 필터' : 'Label status filter'}>
                  {([
                    { id: 'all', ko: '라벨 전체', en: 'All labels' },
                    { id: 'labeled', ko: `라벨 완료 ${sourceImages - unlabeledImages}`, en: `Labeled ${sourceImages - unlabeledImages}` },
                    { id: 'unlabeled', ko: `미라벨 ${unlabeledImages}`, en: `Unlabeled ${unlabeledImages}` },
                  ] as const).map((option) => (
                    <button
                      key={option.id}
                      type="button"
                      onClick={() => setLabelFilter(option.id)}
                      aria-pressed={activeLabelFilter === option.id}
                      className={`px-2.5 py-1 rounded-[4px] border font-medium transition-tactile cursor-pointer ${
                        activeLabelFilter === option.id
                          ? 'bg-[#153A5E] border-[#38BDF8] text-[#E0F2FE]'
                          : 'bg-[#1A212E] border-[#2B3547] text-slate-300 hover:bg-[#222B3D] hover:text-white'
                      }`}
                    >
                      {language === 'ko' ? option.ko : option.en}
                    </button>
                  ))}
                </div>
              )}

              {activeClassFilter && (
                <div className="flex items-center space-x-1 px-2 py-0.5 bg-[#1E293B] border border-[#3B82F6] rounded-[4px] text-blue-300 font-medium text-xs">
                  <span>Class: {activeClassFilter}</span>
                  <button onClick={() => setClassFilter(null)} className="hover:text-white ml-1 cursor-pointer font-bold">
                    ×
                  </button>
                </div>
              )}
            </div>

            {/* Density Toggle + Sensor Scale + Pagination */}
            <div className="flex items-center space-x-4 text-slate-400">
              {/* Defect Physical Scale Indicator (μm unit) */}
              <div className={`${simpleView ? 'hidden' : 'hidden sm:flex'} items-center space-x-1.5 text-[11px] font-mono bg-[#0B0E14] px-2 py-1 rounded-[4px] border border-[#2B3547]`}>
                <span className="text-slate-400">픽셀당 길이:</span>
                <span className="text-slate-200 tabular-nums font-semibold">미보정</span>
              </div>

              {/* Thumbnail Density Toggle (S: 96px, M: 144px, L: 200px) */}
              <div className="flex items-center space-x-1 bg-[#0B0E14] p-0.5 rounded-[4px] border border-[#2B3547]">
                {(['S', 'M', 'L'] as const).map((d) => (
                  <button
                    key={d}
                    type="button"
                    onClick={() => setDensity(d)}
                    className={`px-2 py-0.5 text-[10px] font-mono font-bold rounded-[3px] transition-tactile cursor-pointer ${
                      density === d
                        ? 'bg-[#3B82F6] text-white'
                        : 'text-slate-400 hover:text-slate-200 hover:bg-[#1A212E]'
                    }`}
                    title={
                      d === 'S'
                        ? (language === 'ko' ? '컴팩트 (96px)' : 'Compact (96px)')
                        : d === 'M'
                        ? (language === 'ko' ? '기본 (144px)' : 'Default (144px)')
                        : (language === 'ko' ? '상세 (200px)' : 'Detailed (200px)')
                    }
                  >
                    {d}
                  </button>
                ))}
              </div>

              {/* Pagination Controls */}
              <div className="flex items-center space-x-2 text-slate-400 font-mono tabular-nums text-xs">
                <span>
                  {page} / {totalPages}
                </span>
                <div className="flex space-x-1">
                  <button
                    disabled={page <= 1}
                    aria-label="이전 페이지"
                    onClick={() => loadImages(page - 1)}
                    className="p-1 rounded-[3px] bg-[#1A212E] hover:bg-[#222B3D] text-slate-300 disabled:opacity-30 border border-[#2B3547] cursor-pointer"
                  >
                    <ChevronLeft className="w-3.5 h-3.5" />
                  </button>
                  <button
                    disabled={page >= totalPages}
                    aria-label="다음 페이지"
                    onClick={() => loadImages(page + 1)}
                    className="p-1 rounded-[3px] bg-[#1A212E] hover:bg-[#222B3D] text-slate-300 disabled:opacity-30 border border-[#2B3547] cursor-pointer"
                  >
                    <ChevronRight className="w-3.5 h-3.5" />
                  </button>
                </div>
              </div>
            </div>
          </div>

          {/* Thumbnail Grid */}
          <div className="flex-1 p-6 overflow-y-auto">
            {galleryImages.length === 0 ? (
              <div className="h-full flex flex-col items-center justify-center text-center text-slate-400">
                <FolderOpen className="w-12 h-12 mb-3 text-slate-500" />
                <p className="text-sm font-medium">
                  {!sourceIsCurrent && hasSelectedFolder
                    ? (language === 'ko' ? '프로젝트·서버 선택을 확인하는 중입니다.' : 'Confirming the selected project and server.')
                    : sourceImages > 0
                    ? (language === 'ko' ? '현재 필터에 맞는 이미지가 없습니다. 라벨 상태나 분할 조건을 바꿔보세요.' : 'No images match these filters. Change the label status or split.')
                    : (language === 'ko' ? '가져온 이미지가 없습니다. 상단에서 폴더를 열거나 합성 데이터를 생성하세요.' : 'No images available. Open a folder or generate synthetic data.')}
                </p>
              </div>
            ) : (
              <div className={gridClassByDensity}>
                {galleryImages.map((img) => {
                  const isNormal = img.label?.toLowerCase() === 'ok' || img.label?.toLowerCase() === 'good';
                  const dim = imgDimensions[img.file_path];
                  const w = img.width || dim?.w || 0;
                  const h = img.height || dim?.h || 0;
                  const resBadge = `${w}×${h} px`;

                  return (
                    <button
                      key={img.file_path}
                      type="button"
                      onClick={() => void handleOpenImage(img.image_id, img.file_path)}
                      disabled={openingImageId !== null}
                      aria-label={language === 'ko' ? `${img.file_name} 라벨링에서 열기` : `Open ${img.file_name} in Labeling Studio`}
                      className="group bg-[#1A212E] rounded-[4px] border border-[#2B3547] hover:border-[#3B82F6] hover:bg-[#222B3D] overflow-hidden transition-tactile flex flex-col cursor-pointer select-none text-left disabled:opacity-60 disabled:cursor-wait"
                    >
                      <div className="relative aspect-square bg-[#0B0E14] overflow-hidden flex items-center justify-center border-b border-[#2B3547]">
                        <ScopedDatasetThumbnail
                          path={img.thumbnail_url}
                          authority={sourceAuthority!}
                          context={getProjectContext()}
                          current={galleryCurrent}
                          alt={img.file_name}
                          onLoad={(e) =>
                            handleImageLoad(img.file_path, e.currentTarget.naturalWidth, e.currentTarget.naturalHeight)
                          }
                          className="w-full h-full object-cover group-hover:scale-105 transition-transform duration-100"
                        />
                        {/* Split Badge */}
                        <span
                          className={`absolute top-1 left-1 text-[8px] font-mono font-bold px-1 py-0.5 rounded-[2px] uppercase tracking-wider ${
                            img.split === 'train'
                              ? 'bg-[#3B82F6] text-white'
                              : img.split === 'val'
                              ? 'bg-[#F59E0B] text-slate-950 font-black'
                              : img.split === 'test'
                              ? 'bg-[#10B981] text-slate-950 font-black'
                              : 'bg-slate-600 text-white'
                          }`}
                        >
                          {language === 'ko' ? (SPLIT_BADGE_KO[img.split] ?? img.split) : img.split}
                        </span>
                        {/* Resolution Hover Overlay / Badge */}
                        <span className="absolute bottom-1 right-1 font-mono tabular-nums text-[9px] bg-[#0B0E14]/90 text-slate-200 px-1 py-0.5 rounded-[2px] border border-[#2B3547] opacity-80 group-hover:opacity-100 transition-opacity">
                          {resBadge}
                        </span>
                        {/* Detailed L Mode Hover HUD */}
                        {density === 'L' && (
                          <div className="absolute inset-x-0 top-0 p-1.5 bg-[#0B0E14]/80 opacity-0 group-hover:opacity-100 transition-opacity flex justify-between items-center text-[9px] font-mono text-slate-300">
                            <span>{img.file_name.split('.').pop()?.toUpperCase()}</span>
                            <span className="tabular-nums">Pixel pitch not calibrated</span>
                          </div>
                        )}
                      </div>
                      <div className="p-1.5 bg-[#131822] flex flex-col justify-between flex-1">
                        <span className="text-[11px] font-mono text-slate-300 truncate block" title={img.file_name}>
                          {img.file_name}
                        </span>
                        {img.label && (
                          <div className="flex items-center space-x-1 mt-1">
                            <span
                              className={`w-1.5 h-1.5 rounded-full shrink-0 ${
                                isNormal ? 'bg-[#10B981]' : 'bg-[#EF4444]'
                              }`}
                            />
                            <span
                              className={`text-[10px] font-semibold px-1 py-0.2 rounded-[2px] truncate ${
                                isNormal
                                  ? 'bg-emerald-950/80 text-emerald-400 border border-emerald-800/50'
                                  : 'bg-rose-950/80 text-rose-400 border border-rose-800/50'
                              }`}
                            >
                              {img.label}
                            </span>
                          </div>
                        )}
                      </div>
                    </button>
                  );
                })}
              </div>
            )}
          </div>
        </main>
      </div>

      <ProceduralGeneratorModal />
      {showImports && project?.id && <DatasetImportPanel projectId={project.id} datasetPath={folderPath}
        registeredSource={project.source_dataset_dir || null} task={task}
        onClose={() => setShowImports(false)} onAccepted={() => { loadActiveRevision(); }} />}
      {showVersions && <DatasetVersionPanel datasetPath={folderPath} highlightVersionId={highlightVersionId} onClose={() => { setShowVersions(false); setHighlightVersionId(null); }} onRestored={async () => {
        await importFolder(folderPath, task, false);
      }} />}
    </div>
  );
};
