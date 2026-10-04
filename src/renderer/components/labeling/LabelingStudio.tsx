/**
 * src/renderer/components/labeling/LabelingStudio.tsx
 * Step 2: Industrial Labeling Studio Component.
 * Features:
 *   - Dark steel chassis layout (bg-[#0B0E14] text-slate-200)
 *   - 3-Layer Interactive Canvas
 *   - Precision Geometric Measurement Inspector
 *   - Defect Mask Layer HUD Controls
 *   - Category Selector & Image Filmstrip
 */

import React, { useEffect, useState } from 'react';
import { useDatasetStore } from '../../stores/useDatasetStore';
import { useAnnotationStore } from '../../stores/useAnnotationStore';
import { useProjectStore } from '../../stores/useProjectStore';
import { LabelingToolbar } from './LabelingToolbar';
import { CategorySelector } from './CategorySelector';
import { LabelingCanvas } from './LabelingCanvas';
import { MaskLayerControls } from './MaskLayerControls';
import { AnnotationList } from './AnnotationList';
import { ImageFilmstrip } from './ImageFilmstrip';
import { ModelAssistPanel } from './ModelAssistPanel';
import { LabelSetBar } from './LabelSetBar';
import { ImageReviewPanel } from './ImageReviewPanel';
import {DerivedImagePanel} from './DerivedImagePanel';
import {SavedReviewQueuePanel} from './SavedReviewQueuePanel';
import { DicomPanel } from './DicomPanel';
import {TeamDataPanel} from './TeamDataPanel';
import {WorkflowImpactPanel} from '../common/WorkflowImpactPanel';
import { FOCUS_SWITCH_NAME, focusStatus, readLabelingFocus, writeLabelingFocus, type LabelingHelperPanel } from './labelingLayout';

export const LabelingStudio: React.FC = () => {
  const { images: datasetImages } = useDatasetStore();
  const {
    images: annotImages, syncDatasetImages, task: annotTask, setTask,
    currentImage, annotationLoadStatus, annotationLoadError, loadAnnotationsForCurrent,
    autoSelectError, clearAutoSelectError,
  } = useAnnotationStore();
  const { task: projectTask } = useProjectStore();
  // Focus editing folds the helper panels so the canvas keeps the window's height; the choice is remembered.
  const [focus, setFocus] = useState(() => readLabelingFocus());
  const toggleFocus = () => setFocus(value => { writeLabelingFocus(!value); return !value; });
  // Every folded panel is named in LABELING_HELPER_PANELS (the record's keys must be exactly that list).
  const helperPanels: Record<LabelingHelperPanel, React.ReactNode> = {
    '이미지 검토': <ImageReviewPanel />, '파생 이미지': <DerivedImagePanel />, '저장된 검토 대기열': <SavedReviewQueuePanel />,
    '워크플로 영향': <WorkflowImpactPanel />, '모델 보조': <ModelAssistPanel />,
  };
  const folded = (...names: LabelingHelperPanel[]) => focus ? null : names.map(name => <React.Fragment key={name}>{helperPanels[name]}</React.Fragment>);

  // Sync vision task from project store
  useEffect(() => {
    if (annotTask !== projectTask) {
      setTask(projectTask);
    }
  }, [projectTask, annotTask, setTask]);

  // Keep the selected file when the gallery refreshes or changes page.
  useEffect(() => {
    const currentImg = useAnnotationStore.getState().currentImage;
    if (currentImg && useAnnotationStore.getState().externalSelectionPath === currentImg.file_path) return;
    if (annotImages !== datasetImages || (datasetImages.length > 0 && !currentImg)) {
      void syncDatasetImages(datasetImages);
    }
  }, [datasetImages, annotImages, syncDatasetImages]);

  return (
    <div className="flex flex-col h-full w-full bg-[#0B0E14] text-slate-200 overflow-hidden select-none">
      {/* Top Action Toolbar */}
      <LabelingToolbar />
      <div className="flex items-center justify-end gap-2 border-b border-slate-800 px-3 py-0.5 text-[11px] text-slate-400">
        {focus && <span>{focusStatus(focus)}</span>}
        <button type="button" aria-pressed={focus} onClick={toggleFocus} title={focus ? '보조 패널을 다시 보입니다' : '보조 패널을 접어 캔버스를 키웁니다'}
          className={`rounded border px-2 py-0.5 hover:bg-slate-800 ${focus ? 'border-cyan-500 bg-cyan-950/40 text-cyan-100' : 'border-slate-600 text-slate-200'}`}>{FOCUS_SWITCH_NAME}</button>
      </div>
      <LabelSetBar compact={focus} />
      {/* The team row stays: it loads the team settings and the label book and holds the shared editing lock. */}
      <TeamDataPanel compact={focus} />
      {folded('이미지 검토', '파생 이미지', '저장된 검토 대기열', '워크플로 영향')}
      <DicomPanel />

      {currentImage && annotationLoadStatus === 'error' && (
        <div role="alert" className="px-4 py-2 bg-red-950/50 border-b border-red-700 text-red-200 text-xs flex items-center justify-between gap-3">
          <span>기존 라벨 조회 실패: {annotationLoadError}. 저장을 막았습니다.</span>
          <button type="button" onClick={() => void loadAnnotationsForCurrent()} className="px-2 py-1 rounded border border-red-500 hover:bg-red-900/50 font-semibold whitespace-nowrap">
            다시 시도
          </button>
        </div>
      )}

      {autoSelectError && (
        <div role="alert" className="px-4 py-2 bg-amber-950/50 border-b border-amber-700 text-amber-200 text-xs flex items-center justify-between gap-3">
          <span>{autoSelectError}</span>
          <button type="button" onClick={clearAutoSelectError} className="text-amber-200 hover:text-white" aria-label="자동 추출 오류 닫기">닫기</button>
        </div>
      )}

      {/* Class Tag Bar */}
      <CategorySelector />
      {folded('모델 보조')}

      {/* Center Work Area: 3-Layer Canvas + Mask Controls + Annotation Sidebar */}
      <div className="flex-1 flex overflow-hidden relative" data-labeling-work-area>
        <div className="flex-1 relative h-full bg-[#0B0E14]">
          <LabelingCanvas />
          <MaskLayerControls />
        </div>
        <AnnotationList />
        {currentImage && annotationLoadStatus !== 'ready' && (
          <div className="absolute inset-0 z-30 bg-[#0B0E14]/85 flex items-center justify-center text-center px-6" aria-live="polite">
            <p className="text-sm text-slate-200">
              {annotationLoadStatus === 'loading'
                ? '기존 라벨을 불러오는 중입니다.'
                : '기존 라벨을 불러오지 못해 편집을 중지했습니다. 위의 다시 시도를 누르세요.'}
            </p>
          </div>
        )}
      </div>

      {/* Bottom Thumbnail Filmstrip */}
      <ImageFilmstrip compact={focus} />
    </div>
  );
};

export const LabelingTool = LabelingStudio;
export default LabelingStudio;
