/**
 * src/renderer/components/labeling/LabelingStudio.tsx
 * Step 2: Inspection / Industrial Industrial Labeling Studio Component.
 * Features:
 *   - Dark steel chassis layout (bg-[#0B0E14] text-slate-200)
 *   - 3-Layer Interactive Canvas
 *   - Precision Geometric Measurement Inspector
 *   - Defect Mask Layer HUD Controls
 *   - Category Selector & Image Filmstrip
 */

import React, { useEffect } from 'react';
import { useDatasetStore } from '../../stores/useDatasetStore';
import { useAnnotationStore } from '../../stores/useAnnotationStore';
import { useProjectStore } from '../../stores/useProjectStore';
import { LabelingToolbar } from './LabelingToolbar';
import { CategorySelector } from './CategorySelector';
import { LabelingCanvas } from './LabelingCanvas';
import { MaskLayerControls } from './MaskLayerControls';
import { AnnotationList } from './AnnotationList';
import { ImageFilmstrip } from './ImageFilmstrip';

export const LabelingStudio: React.FC = () => {
  const { images: datasetImages } = useDatasetStore();
  const {
    images: annotImages, syncDatasetImages, task: annotTask, setTask,
    currentImage, annotationLoadStatus, annotationLoadError, loadAnnotationsForCurrent,
    autoSelectError, clearAutoSelectError,
  } = useAnnotationStore();
  const { task: projectTask } = useProjectStore();

  // Sync vision task from project store
  useEffect(() => {
    if (annotTask !== projectTask) {
      setTask(projectTask);
    }
  }, [projectTask, annotTask, setTask]);

  // Keep the selected file when the gallery refreshes or changes page.
  useEffect(() => {
    const currentImg = useAnnotationStore.getState().currentImage;
    if (annotImages !== datasetImages || (datasetImages.length > 0 && !currentImg)) {
      void syncDatasetImages(datasetImages);
    }
  }, [datasetImages, annotImages, syncDatasetImages]);

  return (
    <div className="flex flex-col h-full w-full bg-[#0B0E14] text-slate-200 overflow-hidden select-none">
      {/* Top Action Toolbar */}
      <LabelingToolbar />

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

      {/* Center Work Area: 3-Layer Canvas + Mask Controls + Annotation Sidebar */}
      <div className="flex-1 flex overflow-hidden relative">
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
      <ImageFilmstrip />
    </div>
  );
};

export const LabelingTool = LabelingStudio;
export default LabelingStudio;
