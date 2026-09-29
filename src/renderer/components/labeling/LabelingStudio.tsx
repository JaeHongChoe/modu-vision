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
  const { images: annotImages, setImages, task: annotTask, setTask } = useAnnotationStore();
  const { task: projectTask } = useProjectStore();

  // Sync vision task from project store
  useEffect(() => {
    if (annotTask !== projectTask) {
      setTask(projectTask);
    }
  }, [projectTask, annotTask, setTask]);

  // Sync images from dataset store if not yet loaded in annotation store
  useEffect(() => {
    const currentImg = useAnnotationStore.getState().currentImage;
    if (datasetImages.length > 0 && (annotImages.length === 0 || annotImages !== datasetImages || !currentImg)) {
      setImages(datasetImages, 0);
    }
  }, [datasetImages, annotImages, setImages]);

  return (
    <div className="flex flex-col h-full w-full bg-[#0B0E14] text-slate-200 overflow-hidden select-none">
      {/* Top Action Toolbar */}
      <LabelingToolbar />

      {/* Class Tag Bar */}
      <CategorySelector />

      {/* Center Work Area: 3-Layer Canvas + Mask Controls + Annotation Sidebar */}
      <div className="flex-1 flex overflow-hidden relative">
        <div className="flex-1 relative h-full bg-[#0B0E14]">
          <LabelingCanvas />
          <MaskLayerControls />
        </div>
        <AnnotationList />
      </div>

      {/* Bottom Thumbnail Filmstrip */}
      <ImageFilmstrip />
    </div>
  );
};

export const LabelingTool = LabelingStudio;
export default LabelingStudio;
