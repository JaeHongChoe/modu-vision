/**
 * src/renderer/components/labeling/LabelingToolbar.tsx
 * Inspection / Industrial Benchmark: Industrial Toolset & Labeling Toolbar.
 * Features:
 *   - 7 Industrial Tools (Select, BBox, Rotated OBB, Polygon, Brush, Eraser, AutoSelector)
 *   - Integrated Mask Layer Opacity Slider with strict tabular-nums
 *   - Anti-AI Styling: 0 gradients, 0 blurs, high-contrast text
 *   - Dark steel chassis palette (#131822, #1A212E, #2B3547)
 */

import React, { useState } from 'react';
import {
  CheckCircle2,
  ChevronDown,
  CornerUpLeft,
  CornerUpRight,
  Eraser,
  Eye,
  EyeOff,
  Maximize2,
  MousePointer,
  Paintbrush,
  RotateCw,
  Save,
  Scissors,
  Sparkles,
  Square,
  Wand2,
  ZoomIn,
  ZoomOut,
} from 'lucide-react';
import { useAnnotationStore } from '../../stores/useAnnotationStore';
import { calculateActualSize, calculateCenterZoom, calculateFitToScreen } from '../../utils/coordinateMath';

export const LabelingToolbar: React.FC = () => {
  const {
    activeTool,
    setActiveTool,
    brushRadius,
    setBrushRadius,
    autoSelectTolerance,
    setAutoSelectTolerance,
    convertShape,
    selectedAnnotationId,
    viewTransform,
    imageDimensions,
    setViewTransform,
    undo,
    redo,
    history,
    future,
    isDirty,
    isSaving,
    saveMessage,
    saveAnnotations,
    markNormal,
    annotations,
    currentImage,
    maskOpacity,
    setMaskOpacity,
    maskVisible,
    toggleMaskVisible,
  } = useAnnotationStore();

  const [isConverterOpen, setIsConverterOpen] = useState(false);
  const isNormalMarked = annotations.some((a) => a.is_normal);
  const selectedAnn = annotations.find((a) => a.id === selectedAnnotationId);
  const canConvertShape = !!(
    selectedAnn &&
    (selectedAnn.bbox || selectedAnn.polygon || selectedAnn.points || selectedAnn.rotated_bbox)
  );

  const handleFit = () => {
    const parent = document.querySelector('[data-canvas-container="true"]');
    if (parent) {
      const rect = parent.getBoundingClientRect();
      const imgW = imageDimensions?.width || currentImage?.width || 8192;
      const imgH = imageDimensions?.height || currentImage?.height || 5464;
      const t = calculateFitToScreen(rect.width, rect.height, imgW, imgH);
      setViewTransform(t);
    }
  };

  const handle100 = () => {
    const parent = document.querySelector('[data-canvas-container="true"]');
    if (parent) {
      const rect = parent.getBoundingClientRect();
      const imgW = imageDimensions?.width || currentImage?.width || 8192;
      const imgH = imageDimensions?.height || currentImage?.height || 5464;
      const t = calculateActualSize(rect.width, rect.height, imgW, imgH);
      setViewTransform(t);
    }
  };

  const handleZoom = (factor: number) => {
    const parent = document.querySelector('[data-canvas-container="true"]');
    const rect = parent ? parent.getBoundingClientRect() : { width: window.innerWidth, height: window.innerHeight };
    setViewTransform((prev) => calculateCenterZoom(prev, factor, rect.width, rect.height, 0.01, 40.0));
  };

  return (
    <div className="h-12 bg-[#131822] border-b border-[#2B3547] px-4 flex items-center justify-between text-slate-200 select-none">
      {/* Tool Selector: 7 Industrial Tools */}
      <div className="flex items-center space-x-1">
        {/* Tool 1: Select / Move */}
        <button
          onClick={() => setActiveTool('select')}
          title="선택 및 이동 (Select / Move - 1)"
          className={`p-2 rounded border transition-all cursor-pointer ${
            activeTool === 'select'
              ? 'bg-[#1F2E40] text-cyan-400 border-cyan-500/80 ring-1 ring-cyan-500/30 font-semibold shadow-sm'
              : 'bg-[#1A212E] hover:bg-[#222B3D] text-slate-400 hover:text-slate-200 border-[#2B3547]'
          }`}
        >
          <MousePointer className="w-4 h-4" />
        </button>

        {/* Tool 2: BBox */}
        <button
          onClick={() => setActiveTool('bbox')}
          title="바운딩 박스 (BBox - 2)"
          className={`p-2 rounded border transition-all cursor-pointer ${
            activeTool === 'bbox'
              ? 'bg-[#1F2E40] text-cyan-400 border-cyan-500/80 ring-1 ring-cyan-500/30 font-semibold shadow-sm'
              : 'bg-[#1A212E] hover:bg-[#222B3D] text-slate-400 hover:text-slate-200 border-[#2B3547]'
          }`}
        >
          <Square className="w-4 h-4" />
        </button>

        {/* Tool 3: Rotated BBox (OBB) */}
        <button
          onClick={() => setActiveTool('rotated_bbox')}
          title="회전 바운딩 박스 (Rotated BBox OBB - 3)"
          className={`p-2 rounded border transition-all cursor-pointer ${
            activeTool === 'rotated_bbox'
              ? 'bg-[#1F2E40] text-purple-400 border-purple-500/80 ring-1 ring-purple-500/30 font-semibold shadow-sm'
              : 'bg-[#1A212E] hover:bg-[#222B3D] text-slate-400 hover:text-slate-200 border-[#2B3547]'
          }`}
        >
          <RotateCw className="w-4 h-4" />
        </button>

        {/* Tool 4: Polygon */}
        <button
          onClick={() => setActiveTool('polygon')}
          title="다각형 폴리곤 (Polygon - 4)"
          className={`p-2 rounded border transition-all cursor-pointer ${
            activeTool === 'polygon'
              ? 'bg-[#1F2E40] text-amber-400 border-amber-500/80 ring-1 ring-amber-500/30 font-semibold shadow-sm'
              : 'bg-[#1A212E] hover:bg-[#222B3D] text-slate-400 hover:text-slate-200 border-[#2B3547]'
          }`}
        >
          <Scissors className="w-4 h-4" />
        </button>

        {/* Tool 5: Brush Mask */}
        <button
          onClick={() => setActiveTool('brush')}
          title="브러시 마스크 (Brush - 5)"
          className={`p-2 rounded border transition-all cursor-pointer ${
            activeTool === 'brush'
              ? 'bg-[#1F2E40] text-emerald-400 border-emerald-500/80 ring-1 ring-emerald-500/30 font-semibold shadow-sm'
              : 'bg-[#1A212E] hover:bg-[#222B3D] text-slate-400 hover:text-slate-200 border-[#2B3547]'
          }`}
        >
          <Paintbrush className="w-4 h-4" />
        </button>

        {/* Tool 6: Eraser */}
        <button
          onClick={() => setActiveTool('eraser')}
          title="마스크 지우개 (Eraser - 6)"
          className={`p-2 rounded border transition-all cursor-pointer ${
            activeTool === 'eraser'
              ? 'bg-rose-950/60 text-rose-300 border-rose-600/80 ring-1 ring-rose-500/30 font-semibold shadow-sm'
              : 'bg-[#1A212E] hover:bg-[#222B3D] text-slate-400 hover:text-slate-200 border-[#2B3547]'
          }`}
        >
          <Eraser className="w-4 h-4" />
        </button>

        <div className="w-[1px] h-5 bg-[#2B3547] mx-1" />

        {/* Tool 7: AI Auto-Selector (Clean Anti-AI Design without gradient) */}
        <button
          onClick={() => setActiveTool('auto_select')}
          title="AI 오토 셀렉터: 클릭 한 번으로 결함 외곽선 자동 추출 (Wand)"
          className={`flex items-center space-x-1.5 px-2.5 py-1.5 rounded text-xs font-medium transition-all border cursor-pointer ${
            activeTool === 'auto_select'
              ? 'bg-[#1F2E40] text-cyan-400 border-cyan-500/80 ring-1 ring-cyan-500/30 font-semibold shadow-sm'
              : 'bg-[#1A212E] hover:bg-[#222B3D] text-cyan-400 border-[#2B3547]'
          }`}
        >
          <Wand2 className="w-3.5 h-3.5" />
          <span>오토 셀렉터</span>
        </button>

        {/* AI Shape Converter Menu */}
        <div className="relative inline-flex items-center">
          <button
            onClick={() => setIsConverterOpen(!isConverterOpen)}
            disabled={!canConvertShape}
            title={
              canConvertShape
                ? '형상 변환: BBox, Polygon, Rotated BBox, Mask 상호 변환'
                : '변환할 객체를 먼저 선택해주세요'
            }
            className={`flex items-center space-x-1.5 px-2.5 py-1.5 rounded text-xs font-medium transition-all border cursor-pointer ${
              canConvertShape
                ? 'bg-[#1A212E] hover:bg-[#222B3D] text-indigo-300 border-indigo-500/60 shadow-sm'
                : 'bg-[#1A212E]/50 text-slate-500 border-[#2B3547] cursor-not-allowed'
            }`}
          >
            <Sparkles className="w-3.5 h-3.5" />
            <span>형상 변환</span>
            <ChevronDown className="w-3 h-3 ml-0.5" />
          </button>

          {isConverterOpen && canConvertShape && (
            <div className="absolute top-full left-0 mt-1 w-48 bg-[#131822] border border-[#2B3547] rounded shadow-xl py-1 z-50 text-xs">
              <div className="px-3 py-1 text-[10px] text-slate-400 font-semibold uppercase tracking-wider border-b border-[#2B3547]">
                변환 대상 ({selectedAnn?.type || '선택됨'})
              </div>
              <button
                onClick={() => {
                  if (selectedAnnotationId) convertShape(selectedAnnotationId, 'polygon');
                  setIsConverterOpen(false);
                }}
                disabled={selectedAnn?.type === 'polygon'}
                className="w-full flex items-center space-x-2 px-3 py-1.5 text-left hover:bg-[#1A212E] text-slate-200 disabled:opacity-40 cursor-pointer"
              >
                <Scissors className="w-3.5 h-3.5 text-amber-400" />
                <span>➔ 다각형 (Polygon)</span>
              </button>
              <button
                onClick={() => {
                  if (selectedAnnotationId) convertShape(selectedAnnotationId, 'bbox');
                  setIsConverterOpen(false);
                }}
                disabled={selectedAnn?.type === 'bbox'}
                className="w-full flex items-center space-x-2 px-3 py-1.5 text-left hover:bg-[#1A212E] text-slate-200 disabled:opacity-40 cursor-pointer"
              >
                <Square className="w-3.5 h-3.5 text-cyan-400" />
                <span>➔ 사각 박스 (BBox)</span>
              </button>
              <button
                onClick={() => {
                  if (selectedAnnotationId) convertShape(selectedAnnotationId, 'rotated_bbox');
                  setIsConverterOpen(false);
                }}
                disabled={selectedAnn?.type === 'rotated_bbox'}
                className="w-full flex items-center space-x-2 px-3 py-1.5 text-left hover:bg-[#1A212E] text-slate-200 disabled:opacity-40 cursor-pointer"
              >
                <RotateCw className="w-3.5 h-3.5 text-purple-400" />
                <span>➔ 회전 박스 (OBB)</span>
              </button>
              <button
                onClick={() => {
                  if (selectedAnnotationId) convertShape(selectedAnnotationId, 'mask');
                  setIsConverterOpen(false);
                }}
                disabled={selectedAnn?.type === 'brush_mask'}
                className="w-full flex items-center space-x-2 px-3 py-1.5 text-left hover:bg-[#1A212E] text-slate-200 disabled:opacity-40 cursor-pointer"
              >
                <Paintbrush className="w-3.5 h-3.5 text-emerald-400" />
                <span>➔ 래스터 마스크 (Mask)</span>
              </button>
            </div>
          )}
        </div>

        {/* Auto-Select Tolerance Slider */}
        {activeTool === 'auto_select' && (
          <div className="flex items-center space-x-2 ml-2 pl-2 border-l border-[#2B3547] text-xs">
            <span className="text-cyan-400 font-mono tabular-nums">민감도: {autoSelectTolerance}</span>
            <input
              type="range"
              min="5"
              max="60"
              value={autoSelectTolerance}
              onChange={(e) => setAutoSelectTolerance(Number(e.target.value))}
              className="w-16 accent-cyan-400 h-1 bg-[#1A212E] rounded cursor-pointer"
            />
          </div>
        )}

        {/* Brush Radius Slider */}
        {(activeTool === 'brush' || activeTool === 'eraser') && (
          <div className="flex items-center space-x-2 ml-2 pl-2 border-l border-[#2B3547] text-xs">
            <span className="text-slate-400 font-mono tabular-nums">{brushRadius}px</span>
            <input
              type="range"
              min="2"
              max="64"
              value={brushRadius}
              onChange={(e) => setBrushRadius(Number(e.target.value))}
              className="w-20 accent-cyan-400 h-1 bg-[#1A212E] rounded cursor-pointer"
            />
          </div>
        )}

        {/* Integrated Mask Opacity Slider */}
        <div className="flex items-center space-x-2 ml-2 pl-2 border-l border-[#2B3547] text-xs">
          <button
            onClick={toggleMaskVisible}
            title={maskVisible ? 'Mask Layer Visible' : 'Mask Layer Hidden'}
            className="p-1 rounded bg-[#1A212E] hover:bg-[#222B3D] text-slate-300 border border-[#2B3547] cursor-pointer"
          >
            {maskVisible ? <Eye className="w-3.5 h-3.5 text-cyan-400" /> : <EyeOff className="w-3.5 h-3.5 text-slate-500" />}
          </button>
          <span className="text-[11px] font-mono tabular-nums text-slate-400">Mask:</span>
          <input
            type="range"
            min="0"
            max="1"
            step="0.05"
            value={maskOpacity}
            onChange={(e) => setMaskOpacity(parseFloat(e.target.value))}
            disabled={!maskVisible}
            className="w-16 accent-cyan-500 h-1 bg-[#1A212E] rounded cursor-pointer disabled:opacity-40"
          />
          <span className="text-[11px] font-mono tabular-nums text-slate-300 w-8">
            {Math.round(maskOpacity * 100)}%
          </span>
        </div>
      </div>

      {/* Normal Part (OK) 1-Click Fast Tagging */}
      <div className="flex items-center space-x-2">
        <button
          onClick={() => markNormal(!isNormalMarked)}
          className={`flex items-center space-x-1.5 px-3 py-1.5 rounded text-xs font-semibold transition-all border cursor-pointer ${
            isNormalMarked
              ? 'bg-[#10B981]/20 text-[#10B981] border-[#10B981] ring-1 ring-[#10B981]/40'
              : 'bg-[#1A212E] hover:bg-[#222B3D] text-slate-300 border-[#2B3547]'
          }`}
        >
          <CheckCircle2 className={`w-4 h-4 ${isNormalMarked ? 'text-[#10B981]' : 'text-slate-400'}`} />
          <span>{isNormalMarked ? 'Normal (OK) Part' : 'Mark as Normal (OK)'}</span>
        </button>
      </div>

      {/* Navigation, Zoom Controls & Save */}
      <div className="flex items-center space-x-2">
        {/* Undo / Redo */}
        <button
          onClick={undo}
          disabled={history.length === 0}
          title="Undo (Ctrl+Z)"
          className="p-1.5 rounded bg-[#1A212E] hover:bg-[#222B3D] disabled:opacity-30 text-slate-300 border border-[#2B3547] cursor-pointer"
        >
          <CornerUpLeft className="w-4 h-4" />
        </button>
        <button
          onClick={redo}
          disabled={future.length === 0}
          title="Redo (Ctrl+Y)"
          className="p-1.5 rounded bg-[#1A212E] hover:bg-[#222B3D] disabled:opacity-30 text-slate-300 border border-[#2B3547] cursor-pointer"
        >
          <CornerUpRight className="w-4 h-4" />
        </button>

        <div className="h-4 w-px bg-[#2B3547] mx-1" />

        {/* Zoom */}
        <button
          onClick={() => handleZoom(0.85)}
          title="Zoom Out"
          className="p-1.5 rounded bg-[#1A212E] hover:bg-[#222B3D] text-slate-400 border border-[#2B3547] cursor-pointer"
        >
          <ZoomOut className="w-4 h-4" />
        </button>
        <span className="text-xs font-mono tabular-nums text-slate-300 w-12 text-center">
          {Math.round(viewTransform.scale * 100)}%
        </span>
        <button
          onClick={() => handleZoom(1.15)}
          title="Zoom In"
          className="p-1.5 rounded bg-[#1A212E] hover:bg-[#222B3D] text-slate-400 border border-[#2B3547] cursor-pointer"
        >
          <ZoomIn className="w-4 h-4" />
        </button>

        <button
          onClick={handleFit}
          title="Fit to Screen (F)"
          className="p-1.5 rounded bg-[#1A212E] hover:bg-[#222B3D] text-slate-400 border border-[#2B3547] cursor-pointer"
        >
          <Maximize2 className="w-4 h-4" />
        </button>
        <button
          onClick={handle100}
          title="100% Zoom (1:1)"
          className="p-1.5 rounded bg-[#1A212E] hover:bg-[#222B3D] text-slate-400 text-xs font-mono tabular-nums border border-[#2B3547] cursor-pointer"
        >
          1:1
        </button>

        <div className="h-4 w-px bg-[#2B3547] mx-1" />

        {/* Save Button */}
        <button
          onClick={() => saveAnnotations()}
          disabled={isSaving}
          className={`flex items-center space-x-1.5 px-3 py-1.5 rounded text-xs font-medium transition-colors border cursor-pointer ${
            isDirty
              ? 'bg-cyan-600 hover:bg-cyan-500 text-white border-cyan-400 shadow-sm'
              : 'bg-[#1A212E] text-slate-400 border-[#2B3547] hover:bg-[#222B3D]'
          }`}
        >
          <Save className="w-3.5 h-3.5" />
          <span>{isSaving ? 'Saving...' : saveMessage || (isDirty ? 'Save Changes' : 'Saved')}</span>
        </button>
      </div>
    </div>
  );
};

export default LabelingToolbar;
