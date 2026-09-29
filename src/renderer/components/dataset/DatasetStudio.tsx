/**
 * src/renderer/components/dataset/DatasetStudio.tsx
 * Step 1: Industrial Dataset Studio with folder import, synthetic generator, split controls, and distribution charts.
 */

import React, { useEffect, useState } from 'react';
import {
  FolderOpen,
  Sparkles,
  Sliders,
  AlertTriangle,
  Layers,
  ChevronLeft,
  ChevronRight,
} from 'lucide-react';
import { useProjectStore } from '../../stores/useProjectStore';
import { useDatasetStore } from '../../stores/useDatasetStore';
import { useAnnotationStore } from '../../stores/useAnnotationStore';
import { resolveApiUrl } from '../../services/api';
import { ProceduralGeneratorModal } from './ProceduralGeneratorModal';
import { OperatorGuidanceBanner } from '../common/OperatorGuidanceBanner';
import { JargonTooltip } from '../common/JargonTooltip';
import { GuardrailBanner } from '../common/GuardrailBanner';

export const DatasetStudio: React.FC = () => {
  const { task, language, openImageForLabeling } = useProjectStore();
  const {
    folderPath,
    importError,
    splitError,
    splitSupported,
    splitUnavailableReason,
    totalImages,
    sourceImages,
    unlabeledImages,
    classes,
    split,
    images,
    totalImagesCount,
    page,
    pageSize,
    activeSplitFilter,
    activeClassFilter,
    trainRatio,
    isLoading,
    isSplitting,
    corruptedImages,
    setSplitFilter,
    setClassFilter,
    setTrainRatio,
    importFolder,
    ensureImported,
    applySplit,
    loadImages,
    setShowGeneratorModal,
    generateSynthetic,
  } = useDatasetStore();

  // Inspection Thumbnail Grid Density (S: compact 96px, M: default 144px, L: detailed 200px)
  const [density, setDensity] = useState<'S' | 'M' | 'L'>('M');
  const [openingImageId, setOpeningImageId] = useState<string | null>(null);
  const [imageOpenError, setImageOpenError] = useState<string | null>(null);
  const splitUnavailableForTask = splitSupported === false || task === 'detection' || task === 'anomaly';
  const splitUnavailableHint = splitUnavailableReason || (language === 'ko'
    ? '이 작업 유형은 화면 재분할을 지원하지 않습니다. 원본 train/val/test 폴더 구성을 사용하세요.'
    : 'This task does not support re-splitting here. Use the source train/val/test folders.');

  // Natural resolution dimension cache for image cards
  const [imgDimensions, setImgDimensions] = useState<Record<string, { w: number; h: number }>>({});

  // Precision 3-Way Split Calibrator Ratios (Train / Val / Test)
  const [splitRatios, setSplitRatios] = useState<{ train: number; val: number; test: number }>(() => {
    const train = Math.round(trainRatio * 100) || 70;
    const remaining = 100 - train;
    const val = Math.round(remaining / 2);
    return { train, val, test: remaining - val };
  });

  const handleImageLoad = (filePath: string, naturalWidth: number, naturalHeight: number) => {
    setImgDimensions((prev) => {
      if (prev[filePath] && prev[filePath].w === naturalWidth && prev[filePath].h === naturalHeight) return prev;
      return { ...prev, [filePath]: { w: naturalWidth, h: naturalHeight } };
    });
  };

  const handlePresetSplit = (t: number, v: number, te: number) => {
    setSplitRatios({ train: t, val: v, test: te });
    setTrainRatio(t / 100);
  };

  const handleTrainSliderChange = (newTrain: number) => {
    const remaining = 100 - newTrain;
    const newVal = Math.round(remaining * (2 / 3));
    const newTest = remaining - newVal;
    setSplitRatios({ train: newTrain, val: newVal, test: newTest });
    setTrainRatio(newTrain / 100);
  };

  useEffect(() => {
    if (folderPath) ensureImported(task).catch(() => {});
  }, [folderPath, task, ensureImported]);

  const handleSelectFolder = async () => {
    if (typeof window !== 'undefined' && window.api?.selectFolder) {
      const folder = await window.api.selectFolder({ title: 'Select Industrial Dataset' });
      if (folder) {
        await importFolder(folder, task).catch(() => {});
      }
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
  const maxClassCount = Math.max(...Object.values(classes), 1);
  const minClassCount = Math.min(...Object.values(classes), 1);
  const isImbalanced = classEntries.length > 1 && maxClassCount / minClassCount > 20;

  // Calibrator estimates; status cards and gallery tabs use the saved split instead.
  const trainCount = Math.round(totalImages * (splitRatios.train / 100));
  const valCount = Math.round(totalImages * (splitRatios.val / 100));
  const testCount = Math.max(0, totalImages - trainCount - valCount);
  const appliedTotal = split.train + split.val + split.test;
  const showAppliedSplit = splitUnavailableForTask && appliedTotal > 0;
  const shownCounts = showAppliedSplit ? split : { train: trainCount, val: valCount, test: testCount };
  const shownRatios = showAppliedSplit
    ? {
        train: Math.round((split.train / appliedTotal) * 100),
        val: Math.round((split.val / appliedTotal) * 100),
        test: 100 - Math.round((split.train / appliedTotal) * 100) - Math.round((split.val / appliedTotal) * 100),
      }
    : splitRatios;

  const gridClassByDensity = {
    S: 'grid grid-cols-[repeat(auto-fill,minmax(96px,1fr))] gap-2',
    M: 'grid grid-cols-[repeat(auto-fill,minmax(144px,1fr))] gap-3',
    L: 'grid grid-cols-[repeat(auto-fill,minmax(200px,1fr))] gap-4',
  }[density];

  return (
    <div className="flex-1 flex flex-col h-full bg-[#0B0E14] text-slate-100 overflow-hidden">
      <OperatorGuidanceBanner step={1} />
      {/* Top Action Toolbar (Inspection Deep Steel Panel #131822) */}
      <div className="p-3 bg-[#131822] border-b border-[#2B3547] flex items-center justify-between">
        <div className="flex items-center space-x-3">
          <button
            onClick={handleSelectFolder}
            disabled={isLoading}
            className="flex items-center space-x-2 px-3 py-1.5 bg-blue-600 hover:bg-blue-500 rounded-[4px] text-xs font-semibold text-white border border-blue-400 transition-tactile cursor-pointer"
          >
            <FolderOpen className="w-4 h-4" />
            <span>{language === 'ko' ? '데이터셋 폴더 열기' : 'Open Dataset Folder'}</span>
          </button>

          <button
            onClick={() => setShowGeneratorModal(true)}
            className="flex items-center space-x-2 px-3 py-1.5 bg-[#1A212E] hover:bg-[#222B3D] border border-[#2B3547] rounded-[4px] text-xs font-semibold transition-tactile text-amber-300 cursor-pointer"
          >
            <Sparkles className="w-4 h-4 text-amber-400" />
            <span>{language === 'ko' ? '합성 데이터 생성기' : 'Procedural Generator'}</span>
          </button>

          <span className="text-xs text-slate-400 font-mono truncate max-w-sm" title={folderPath}>
            {folderPath}
          </span>
        </div>

        {/* Partition Status Annunciator */}
        <div className="flex items-center space-x-3 bg-[#0B0E14] px-3 py-1.5 rounded-[4px] border border-[#2B3547] text-xs font-mono tabular-nums">
          <div className="flex items-center space-x-1.5">
            <span className="w-2 h-2 rounded-full bg-[#3B82F6]" />
            <span className="text-slate-400">Train:</span>
            <span className="font-bold text-blue-400">{split.train}</span>
          </div>
          <span className="w-[1px] h-3 bg-[#2B3547]" />
          <div className="flex items-center space-x-1.5">
            <span className="w-2 h-2 rounded-full bg-[#F59E0B]" />
            <span className="text-slate-400">Val:</span>
            <span className="font-bold text-amber-400">{split.val}</span>
          </div>
          <span className="w-[1px] h-3 bg-[#2B3547]" />
          <div className="flex items-center space-x-1.5">
            <span className="w-2 h-2 rounded-full bg-[#10B981]" />
            <span className="text-slate-400">Test:</span>
            <span className="font-bold text-emerald-400">{split.test}</span>
          </div>
        </div>
      </div>

      {/* Main Studio Body: Left Sidebar (Stats, Split Calibrator & Distribution) + Right (Gallery) */}
      <div className="flex-1 flex overflow-hidden">
        {/* Left Stats, Split Calibrator & Class Distribution Panel */}
        <aside className="w-84 bg-[#131822] border-r border-[#2B3547] p-3 flex flex-col space-y-3 overflow-y-auto">
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
                {language === 'ko'
                  ? 'ERR_005: 0바이트 또는 헤더 손상 이미지가 제외되었습니다.'
                  : 'Corrupted image headers were excluded.'}
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
                <JargonTooltip termKey="focal_loss" />
              </div>
              <p className="text-[11px] text-amber-300">
                {language === 'ko'
                  ? '다수 클래스와 소수 클래스 비율이 20:1을 초과합니다. 가중 손실함수(Focal Loss)가 적용됩니다.'
                  : 'Class ratio exceeds 20:1. Weighted loss rebalancing will be used.'}
              </p>
            </div>
          )}

          {/* Precision 3-Way Split Calibrator */}
          <div className="p-3 bg-[#1A212E] rounded-[4px] border border-[#2B3547] flex flex-col space-y-2.5">
            <div className="flex items-center justify-between pb-2 border-b border-[#2B3547]">
              <div className="flex items-center space-x-1.5">
                <Sliders className="w-3.5 h-3.5 text-blue-400" />
                <span className="text-xs font-bold text-slate-200 tracking-wide uppercase">
                  {showAppliedSplit
                    ? (language === 'ko' ? '원본 폴더 분할' : 'Source Folder Split')
                    : (language === 'ko' ? '3-Way 분할 캘리브레이터' : '3-Way Split Calibrator')}
                </span>
              </div>
              <JargonTooltip termKey="early_stopping" />
            </div>

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
                  title={`Train: ${shownRatios.train}% (${shownCounts.train}장)`}
                />
                <div
                  className="bg-[#F59E0B] h-full transition-all duration-200"
                  style={{ width: `${shownRatios.val}%` }}
                  title={`Val: ${shownRatios.val}% (${shownCounts.val}장)`}
                />
                <div
                  className="bg-[#10B981] h-full transition-all duration-200"
                  style={{ width: `${shownRatios.test}%` }}
                  title={`Test: ${shownRatios.test}% (${shownCounts.test}장)`}
                />
              </div>
              <div className="flex items-center justify-between text-[10px] font-mono tabular-nums">
                <span className="text-blue-400 font-semibold">
                  Train: {shownRatios.train}% ({shownCounts.train})
                </span>
                <span className="text-amber-400 font-semibold">
                  Val: {shownRatios.val}% ({shownCounts.val})
                </span>
                <span className="text-emerald-400 font-semibold">
                  Test: {shownRatios.test}% ({shownCounts.test})
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
              onClick={() => void applySplit(splitRatios.train / 100, splitRatios.val / 100, splitRatios.test / 100).catch(() => {})}
              disabled={isSplitting || totalImages === 0 || splitUnavailableForTask}
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
                  ? '3-Way 분할 적용'
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

          {/* Industrial Digital Class Gauges */}
          <div className="p-3 bg-[#1A212E] rounded-[4px] border border-[#2B3547] flex-1 flex flex-col">
            <div className="flex items-center justify-between mb-2.5 pb-2 border-b border-[#2B3547]">
              <div className="flex items-center space-x-1.5">
                <Layers className="w-3.5 h-3.5 text-blue-400" />
                <span className="text-xs font-bold text-slate-200 tracking-wide uppercase">
                  {task === 'segmentation' && splitUnavailableForTask
                    ? (language === 'ko' ? '마스크 채널별 이미지 수' : 'Images per Mask Channel')
                    : (language === 'ko' ? '클래스별 계측 분포' : 'Class Digital Gauges')}
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

            <div className="space-y-2 flex-1 overflow-y-auto pr-1">
              {classEntries.length === 0 ? (
                <div className="text-xs text-slate-400 italic py-4 text-center">
                  {language === 'ko' ? '클래스 정보 없음' : 'No classes loaded'}
                </div>
              ) : (
                classEntries.map(([cName, count]) => {
                  const pct = Math.round((count / maxClassCount) * 100);
                  const pctOfTotal = totalImages > 0 ? Math.round((count / totalImages) * 100) : 0;
                  const isNormal = cName.toLowerCase() === 'ok' || cName.toLowerCase() === 'good';
                  const isSelected = activeClassFilter === cName;

                  // Stratified 3-way split estimates for this class
                  const cTrain = Math.round(count * (splitRatios.train / 100));
                  const cVal = Math.round(count * (splitRatios.val / 100));
                  const cTest = Math.max(0, count - cTrain - cVal);

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
                            {count}장
                          </span>
                        </div>
                      </div>

                      {/* Middle row: Industrial Linear Level Meter */}
                      <div className="w-full bg-[#0B0E14] h-1.5 rounded-[2px] border border-[#2B3547] overflow-hidden my-1">
                        <div
                          className={`h-full transition-all duration-300 ${
                            isNormal ? 'bg-[#10B981]' : 'bg-[#EF4444]'
                          }`}
                          style={{ width: `${pct}%` }}
                        />
                      </div>

                      {/* Class split counts are estimates only when the split can be applied here. */}
                      {!splitUnavailableForTask && <div className="mt-1 pt-1 border-t border-[#2B3547]/50 flex items-center justify-between text-[10px] font-mono tabular-nums text-slate-400">
                        <div className="flex space-x-2">
                          <span className="text-blue-400">T: {cTrain}</span>
                          <span className="text-amber-400">V: {cVal}</span>
                          <span className="text-emerald-400">Test: {cTest}</span>
                        </div>
                        <div className="w-16 bg-[#0B0E14] h-1 rounded-[1px] overflow-hidden flex border border-[#2B3547]/60">
                          <div
                            className="bg-[#3B82F6] h-full"
                            style={{ width: `${count > 0 ? (cTrain / count) * 100 : 0}%` }}
                            title={`Train: ${cTrain}`}
                          />
                          <div
                            className="bg-[#F59E0B] h-full"
                            style={{ width: `${count > 0 ? (cVal / count) * 100 : 0}%` }}
                            title={`Val: ${cVal}`}
                          />
                          <div
                            className="bg-[#10B981] h-full"
                            style={{ width: `${count > 0 ? (cTest / count) * 100 : 0}%` }}
                            title={`Test: ${cTest}`}
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
          <div className="px-4 py-2 bg-[#131822] border-b border-[#2B3547] flex items-center justify-between text-xs">
            <div className="flex items-center space-x-2">
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
              <div className="hidden sm:flex items-center space-x-1.5 text-[11px] font-mono bg-[#0B0E14] px-2 py-1 rounded-[4px] border border-[#2B3547]">
                <span className="text-slate-400">Pixel Pitch:</span>
                <span className="text-slate-200 tabular-nums font-semibold">Not calibrated</span>
              </div>

              {/* Inspection-style Density Toggle (S: 96px, M: 144px, L: 200px) */}
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
                    onClick={() => loadImages(page - 1)}
                    className="p-1 rounded-[3px] bg-[#1A212E] hover:bg-[#222B3D] text-slate-300 disabled:opacity-30 border border-[#2B3547] cursor-pointer"
                  >
                    <ChevronLeft className="w-3.5 h-3.5" />
                  </button>
                  <button
                    disabled={page >= totalPages}
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
            {images.length === 0 ? (
              <div className="h-full flex flex-col items-center justify-center text-center text-slate-400">
                <FolderOpen className="w-12 h-12 mb-3 text-slate-500" />
                <p className="text-sm font-medium">
                  {language === 'ko'
                    ? '가져온 이미지가 없습니다. 상단에서 폴더를 열거나 합성 데이터를 생성하세요.'
                    : 'No images available. Open a folder or generate synthetic data.'}
                </p>
              </div>
            ) : (
              <div className={gridClassByDensity}>
                {images.map((img) => {
                  const thumbUrl = resolveApiUrl(img.thumbnail_url);
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
                        <img
                          src={thumbUrl}
                          alt={img.file_name}
                          loading="lazy"
                          decoding="async"
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
                          {img.split}
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
    </div>
  );
};
