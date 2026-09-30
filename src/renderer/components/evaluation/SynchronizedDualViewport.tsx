/**
 * src/renderer/components/evaluation/SynchronizedDualViewport.tsx
 * Synchronized Pan/Zoom Dual Viewport: Raw Image vs Defect Heatmap Overlay.
 * Synchronized pan/zoom, crosshair reticle, and pixel-scale readout.
 */

import React, { useRef, useState, useEffect, useCallback } from 'react';
import {
  ZoomIn,
  ZoomOut,
  Maximize2,
  Lock,
  Unlock,
  Sliders,
  Crosshair,
} from 'lucide-react';
import type { TestPredictionItem, ViewTransform, Point, VisionTask } from '../../types';
import { calculateZoomAtPoint, calculateFitToScreen, viewportToImage } from '../../utils/coordinateMath';
import { PhysicalScaleOverlay } from './PhysicalScaleOverlay';
import { resolveApiUrl } from '../../services/api';

export interface SynchronizedDualViewportProps {
  task: VisionTask;
  prediction: TestPredictionItem | null;
  heatmapOverlayBase64: string | null;
  heatmapLoading: boolean;
  confidenceThreshold: number;
  onThresholdChange: (tau: number) => void;
  defectScore?: number;
  groundTruth?: string;
  predictedClass?: string;
}

export const SynchronizedDualViewport: React.FC<SynchronizedDualViewportProps> = ({
  task,
  prediction,
  heatmapOverlayBase64,
  heatmapLoading,
  confidenceThreshold,
  onThresholdChange,
  defectScore,
  groundTruth,
  predictedClass,
}) => {
  const rawSurfaceRef = useRef<HTMLDivElement | null>(null);
  const overlaySurfaceRef = useRef<HTMLDivElement | null>(null);
  const [transforms, setTransforms] = useState<[ViewTransform, ViewTransform]>([
    { scale: 1, offsetX: 0, offsetY: 0 }, { scale: 1, offsetX: 0, offsetY: 0 },
  ]);
  const [activeViewport, setActiveViewport] = useState<0 | 1>(0);
  const [isLocked, setIsLocked] = useState(true);
  const [drag, setDrag] = useState<{ viewport: 0 | 1; start: Point } | null>(null);
  const [cursorPos, setCursorPos] = useState<Point | null>(null);
  const [overlayOpacity, setOverlayOpacity] = useState(0.70);

  // Raw Image Dimension tracking
  const [imgDim, setImgDim] = useState<{ w: number; h: number }>({ w: 1024, h: 1024 });

  const applyTransform = (viewport: 0 | 1, update: (current: ViewTransform) => ViewTransform) => {
    setTransforms((previous) => {
      const next = update(previous[viewport]);
      if (isLocked) return [next, next];
      return viewport === 0 ? [next, previous[1]] : [previous[0], next];
    });
  };
  const fitSurface = useCallback((surface: HTMLDivElement | null) => surface
    ? calculateFitToScreen(surface.clientWidth, surface.clientHeight, imgDim.w, imgDim.h, 24)
    : null, [imgDim]);
  const handleFit = () => {
    const fit = fitSurface(activeViewport === 0 ? rawSurfaceRef.current : overlaySurfaceRef.current);
    if (fit) applyTransform(activeViewport, () => fit);
  };
  useEffect(() => {
    const raw = fitSurface(rawSurfaceRef.current);
    const overlay = fitSurface(overlaySurfaceRef.current);
    if (raw && overlay) setTransforms([raw, raw]);
    setActiveViewport(0);
    setDrag(null);
    setCursorPos(null);
  }, [prediction?.image_id, fitSurface]);

  const handleWheel = (viewport: 0 | 1, e: React.WheelEvent) => {
    e.preventDefault();
    setActiveViewport(viewport);
    const rect = e.currentTarget.getBoundingClientRect();
    const cursorVp = { x: e.clientX - rect.left, y: e.clientY - rect.top };
    applyTransform(viewport, current => calculateZoomAtPoint(cursorVp, current, e.deltaY < 0 ? 1 : -1, 0.1, 32.0));
  };
  const handleMouseDown = (viewport: 0 | 1, e: React.MouseEvent) => {
    if (e.button === 0 || e.button === 1) {
      e.preventDefault();
      setActiveViewport(viewport);
      setDrag({ viewport, start: { x: e.clientX - transforms[viewport].offsetX,
        y: e.clientY - transforms[viewport].offsetY } });
    }
  };
  const handleMouseMove = (viewport: 0 | 1, e: React.MouseEvent) => {
    const rect = e.currentTarget.getBoundingClientRect();
    const imgPt = viewportToImage({ x: e.clientX - rect.left, y: e.clientY - rect.top }, transforms[viewport]);
    setCursorPos({ x: Math.max(0, Math.min(imgDim.w, Math.round(imgPt.x * 10) / 10)),
      y: Math.max(0, Math.min(imgDim.h, Math.round(imgPt.y * 10) / 10)) });
    if (drag?.viewport === viewport) {
      applyTransform(viewport, current => ({ ...current,
        offsetX: e.clientX - drag.start.x, offsetY: e.clientY - drag.start.y }));
    }
  };
  const handleMouseUp = () => setDrag(null);
  const toggleLock = () => {
    if (!isLocked) setTransforms(previous => [previous[activeViewport], previous[activeViewport]]);
    setDrag(null);
    setIsLocked(previous => !previous);
  };
  const rawTransform = transforms[0];
  const overlayTransform = transforms[1];
  const transform = transforms[activeViewport];

  const rawImgUrl = prediction ? resolveApiUrl(prediction.thumbnail_url) : '';

  return (
    <div className="min-w-0 min-h-0 flex-1 flex flex-col bg-[#0B0E14] overflow-hidden select-none">
      {/* Top Viewport Toolbar */}
      <div className="min-h-12 shrink-0 bg-[#131822] border-b border-[#2B3547] px-3 py-2 flex flex-wrap items-center justify-between gap-x-4 gap-y-2 text-xs">
        {/* Left: Confidence Threshold Slider */}
        <div className="flex min-w-0 flex-wrap items-center gap-2">
          <Sliders className="w-4 h-4 text-[#3B82F6]" />
          <span className="font-semibold text-slate-300">Threshold (τ):</span>
          <span className="font-mono font-bold text-[#3B82F6] tabular-nums text-sm">
            {confidenceThreshold.toFixed(2)}
          </span>
          <input
            type="range"
            min="0.01"
            max="0.99"
            step="0.01"
            value={confidenceThreshold}
            onChange={(e) => onThresholdChange(parseFloat(e.target.value))}
            className="w-28 sm:w-40 min-w-0 accent-[#3B82F6] cursor-pointer"
          />
        </div>

        {/* Center: Overlay Opacity Slider */}
        <div className="flex items-center space-x-2 text-slate-400 font-mono text-[11px]">
          <span>Opacity:</span>
          <input
            type="range"
            min="0.0"
            max="1.0"
            step="0.05"
            value={overlayOpacity}
            onChange={(e) => setOverlayOpacity(parseFloat(e.target.value))}
            className="w-24 accent-slate-400 cursor-pointer"
          />
          <span className="tabular-nums">{(overlayOpacity * 100).toFixed(0)}%</span>
        </div>

        {!isLocked && <span className="text-[10px] text-cyan-300">조작 대상: {activeViewport === 0 ? '원본' : '오버레이'}</span>}
        {/* Right: Zoom & Lock Controls */}
        <div className="flex items-center space-x-1.5">
          <button
            type="button"
            onClick={handleFit}
            className="p-1.5 bg-[#1A212E] hover:bg-[#2B3547] text-slate-300 rounded-[4px] border border-[#2B3547] cursor-pointer transition-all"
            title="Fit to Screen"
          >
            <Maximize2 className="w-3.5 h-3.5" />
          </button>
          <button
            type="button"
            onClick={() => applyTransform(activeViewport, current => ({ ...current, scale: 1 }))}
            className="px-2 py-1 bg-[#1A212E] hover:bg-[#2B3547] text-slate-300 rounded-[4px] border border-[#2B3547] font-mono text-[11px] font-bold tabular-nums cursor-pointer transition-all"
            title="1:1 Pixel Native"
          >
            1:1
          </button>
          <button
            type="button"
            onClick={() => applyTransform(activeViewport, current => ({ ...current, scale: Math.min(32, current.scale * 1.25) }))}
            className="p-1.5 bg-[#1A212E] hover:bg-[#2B3547] text-slate-300 rounded-[4px] border border-[#2B3547] cursor-pointer transition-all"
            title="Zoom In"
          >
            <ZoomIn className="w-3.5 h-3.5" />
          </button>
          <button
            type="button"
            onClick={() => applyTransform(activeViewport, current => ({ ...current, scale: Math.max(0.1, current.scale * 0.8) }))}
            className="p-1.5 bg-[#1A212E] hover:bg-[#2B3547] text-slate-300 rounded-[4px] border border-[#2B3547] cursor-pointer transition-all"
            title="Zoom Out"
          >
            <ZoomOut className="w-3.5 h-3.5" />
          </button>
          <button
            type="button"
            onClick={toggleLock}
            className={`p-1.5 rounded-[4px] border cursor-pointer transition-all ${
              isLocked ? 'bg-[#10B981]/20 border-[#10B981] text-[#10B981]' : 'bg-[#1A212E] border-[#2B3547] text-slate-400'
            }`}
            aria-pressed={isLocked}
            title={isLocked ? 'Dual Viewports Synchronized' : 'Viewports Unlocked'}
          >
            {isLocked ? <Lock className="w-3.5 h-3.5" /> : <Unlock className="w-3.5 h-3.5" />}
          </button>
        </div>
      </div>

      {/* Main Dual Stage */}
      <div className="min-h-0 flex-1 grid grid-cols-2 gap-2 p-3 overflow-hidden">
        {/* Viewport 1: Raw Original Image */}
        <div
          className="relative bg-[#131822] border border-[#2B3547] rounded-[6px] overflow-hidden cursor-grab active:cursor-grabbing flex flex-col"
          onWheel={event => handleWheel(0, event)}
          onMouseDown={event => handleMouseDown(0, event)}
          onMouseMove={event => handleMouseMove(0, event)}
          onMouseUp={handleMouseUp}
          onMouseLeave={handleMouseUp}
          aria-label="원본 이미지 뷰포트"
        >
          {/* Header Tag */}
          <div className="absolute top-2 left-2 z-10 px-2 py-0.5 bg-[#0B0E14]/90 border border-[#2B3547] rounded-[3px] text-[10px] font-mono text-slate-300 flex items-center space-x-1.5">
            <span className="w-2 h-2 rounded-full bg-slate-400" />
            <span className="font-bold">RAW INSPECTION IMAGE</span>
          </div>

          {/* Canvas Render Surface */}
          <div ref={rawSurfaceRef} className="min-h-0 flex-1 relative overflow-hidden flex items-center justify-center">
            {rawImgUrl ? (
              <div
                style={{
                  transform: `translate3d(${rawTransform.offsetX}px, ${rawTransform.offsetY}px, 0px) scale(${rawTransform.scale})`,
                  transformOrigin: '0 0',
                  willChange: 'transform',
                }}
                className="absolute top-0 left-0"
              >
                <img
                  src={rawImgUrl}
                  alt="Raw Inspection"
                  onLoad={(e) => setImgDim({ w: e.currentTarget.naturalWidth, h: e.currentTarget.naturalHeight })}
                  style={{ imageRendering: rawTransform.scale >= 3.0 ? 'pixelated' : 'auto' }}
                  className="max-w-none block pointer-events-none"
                />
              </div>
            ) : (
              <span className="text-slate-400 font-mono text-xs">No sample selected</span>
            )}
          </div>

          {/* Physical Scale Overlay */}
          <PhysicalScaleOverlay scale={rawTransform.scale} />

          {/* Bottom Readout */}
          <div className="p-2 bg-[#0B0E14] border-t border-[#2B3547] text-[10px] font-mono tabular-nums text-slate-400 flex justify-between">
            <span>GT: <strong className="text-slate-200">{prediction ? groundTruth || '—' : '—'}</strong></span>
            <span>{task === 'detection' ? 'TOP BOX (no τ/IoU)' : 'PRED'}: <strong className="text-slate-200">{prediction ? predictedClass || '—' : '—'}</strong></span>
          </div>
        </div>

        {/* Viewport 2: Defect Heatmap Overlay */}
        <div
          className="relative bg-[#131822] border border-[#2B3547] rounded-[6px] overflow-hidden cursor-grab active:cursor-grabbing flex flex-col"
          onWheel={event => handleWheel(1, event)}
          onMouseDown={event => handleMouseDown(1, event)}
          onMouseMove={event => handleMouseMove(1, event)}
          onMouseUp={handleMouseUp}
          onMouseLeave={handleMouseUp}
          aria-label="오버레이 이미지 뷰포트"
        >
          {/* Header Tag */}
          <div className="absolute top-2 left-2 z-10 px-2 py-0.5 bg-[#0B0E14]/90 border border-[#2B3547] rounded-[3px] text-[10px] font-mono text-slate-300 flex items-center space-x-1.5">
            <span className="w-2 h-2 rounded-full bg-[#EF4444] animate-pulse" />
            <span className="font-bold text-[#EF4444]">{task === 'detection' ? 'DETECTION BOX OVERLAY' : 'DEFECT HEATMAP OVERLAY'}</span>
            {heatmapLoading && <span className="text-[#3B82F6] text-[9px] animate-pulse ml-1">[UPDATING...]</span>}
          </div>

          {/* Canvas Render Surface */}
          <div ref={overlaySurfaceRef} className="min-h-0 flex-1 relative overflow-hidden flex items-center justify-center">
            {rawImgUrl ? (
              <div
                style={{
                  transform: `translate3d(${overlayTransform.offsetX}px, ${overlayTransform.offsetY}px, 0px) scale(${overlayTransform.scale})`,
                  transformOrigin: '0 0',
                  willChange: 'transform',
                }}
                className="absolute top-0 left-0"
              >
                {/* Underlay Raw Base Image */}
                <img
                  src={rawImgUrl}
                  alt="Base"
                  style={{ imageRendering: overlayTransform.scale >= 3.0 ? 'pixelated' : 'auto' }}
                  className="max-w-none block pointer-events-none filter brightness-75"
                />
                {/* Defect Heatmap Overlay Layer with Alpha Blending */}
                {heatmapOverlayBase64 && (
                  <img
                    src={heatmapOverlayBase64}
                    alt={task === 'detection' ? 'Detection boxes' : 'Heatmap'}
                    style={{
                      opacity: overlayOpacity,
                      imageRendering: overlayTransform.scale >= 3.0 ? 'pixelated' : 'auto',
                    }}
                    className="absolute top-0 left-0 w-full h-full max-w-none block pointer-events-none mix-blend-screen"
                  />
                )}
              </div>
            ) : (
              <span className="text-slate-400 font-mono text-xs">No overlay available</span>
            )}
          </div>

          {/* Physical Scale Overlay */}
          <PhysicalScaleOverlay scale={overlayTransform.scale} />

          {/* Bottom Readout */}
          <div className="p-2 bg-[#0B0E14] border-t border-[#2B3547] text-[10px] font-mono tabular-nums text-slate-400 flex justify-between">
            <span>{task === 'detection' ? 'MAX BOX SCORE' : 'DEFECT SCORE'}: <strong className="text-slate-200">{prediction && defectScore != null ? `${(defectScore * 100).toFixed(1)}%` : '—'}</strong></span>
            <span>{task === 'detection' ? `IMAGE τ=${confidenceThreshold.toFixed(2)}` : 'VERDICT'}: <strong className={prediction && defectScore != null ? (defectScore >= confidenceThreshold ? 'text-[#EF4444]' : 'text-[#10B981]') : 'text-slate-400'}>
              {prediction && defectScore != null ? (defectScore >= confidenceThreshold ? (task === 'detection' ? 'NG' : 'REJECT (NG)') : (task === 'detection' ? 'OK' : 'PASS (OK)')) : '—'}
            </strong></span>
          </div>
        </div>
      </div>

      {/* Bottom Reticle Telemetry Strip */}
      <div className="min-h-8 shrink-0 bg-[#0B0E14] border-t border-[#2B3547] px-3 py-1 flex flex-wrap gap-2 items-center justify-between text-[11px] font-mono tabular-nums text-slate-400">
        <div className="flex items-center space-x-4">
          <span className="flex items-center space-x-1">
            <Crosshair className="w-3 h-3 text-[#3B82F6]" />
            <span>X: {cursorPos?.x ?? 0} px</span>
          </span>
          <span>Y: {cursorPos?.y ?? 0} px</span>
        </div>
        <div className="flex items-center space-x-3 text-slate-500 text-[10px]">
          <span>Physical scale uncalibrated</span>
          <span>Zoom: {(transform.scale * 100).toFixed(0)}%</span>
        </div>
      </div>
    </div>
  );
};

export default SynchronizedDualViewport;
