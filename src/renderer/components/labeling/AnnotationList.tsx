/**
 * src/renderer/components/labeling/AnnotationList.tsx
 * Geometric Measurement Inspector Panel.
 * Features:
 *   - Precision geometric readouts: X, Y, W, H, θ, Area with strict tabular-nums
 *   - Real-time optical calibration in physical units: px, °, px², μm
 *   - Subpixel manual calibration input fields for machine vision verification
 *   - Dark steel chassis palette (#131822, #1A212E, #2B3547)
 *   - Anti-AI styling compliance: 0 gradients, 0 blurs, 0 low-contrast text
 */

import React, { useState } from 'react';
import {
  CheckCircle2,
  Crosshair,
  Paintbrush,
  RotateCw,
  Ruler,
  Scissors,
  Square,
  Trash2,
} from 'lucide-react';
import { useAnnotationStore } from '../../stores/useAnnotationStore';
import type { AnnotationItem } from '../../types';

// Shoelace formula for polygon area
function calculatePolygonArea(pts: Array<[number, number]>): number {
  if (!pts || pts.length < 3) return 0;
  let sum = 0;
  for (let i = 0; i < pts.length; i++) {
    const j = (i + 1) % pts.length;
    sum += pts[i][0] * pts[j][1] - pts[j][0] * pts[i][1];
  }
  return Math.abs(sum) / 2;
}

export const AnnotationList: React.FC = () => {
  const {
    annotations,
    selectedAnnotationId,
    setSelectedAnnotationId,
    deleteAnnotation,
    updateAnnotation,
  } = useAnnotationStore();

  // Physical units are shown only after the operator enters a measured scale.
  const [pixelPitchUm, setPixelPitchUm] = useState<number>(0);
  const [isCalibratorOpen, setIsCalibratorOpen] = useState<boolean>(false);

  const selectedAnn = annotations.find((a) => a.id === selectedAnnotationId);

  // Compute geometry for an annotation
  const getAnnotationGeometry = (ann: AnnotationItem) => {
    let x = 0;
    let y = 0;
    let w = 0;
    let h = 0;
    let theta = 0;
    let area = 0;
    let vertexCount = 0;

    if (ann.type === 'bbox' && ann.bbox) {
      const [xmin, ymin, xmax, ymax] = ann.bbox;
      x = xmin;
      y = ymin;
      w = Math.max(0, xmax - xmin);
      h = Math.max(0, ymax - ymin);
      theta = 0;
      area = w * h;
    } else if (ann.type === 'rotated_bbox' && ann.rotated_bbox) {
      const [cx, cy, rw, rh, angle] = ann.rotated_bbox;
      x = Math.round(cx - rw / 2);
      y = Math.round(cy - rh / 2);
      w = rw;
      h = rh;
      theta = angle || 0;
      area = rw * rh;
    } else if (ann.type === 'polygon' && (ann.polygon || ann.points)) {
      const pts = ann.polygon || ann.points || [];
      vertexCount = pts.length;
      if (pts.length > 0) {
        const xs = pts.map((p) => p[0]);
        const ys = pts.map((p) => p[1]);
        const minX = Math.min(...xs);
        const maxX = Math.max(...xs);
        const minY = Math.min(...ys);
        const maxY = Math.max(...ys);
        x = minX;
        y = minY;
        w = maxX - minX;
        h = maxY - minY;
        area = calculatePolygonArea(pts);
      }
    } else if (ann.type === 'brush_mask' && ann.bbox) {
      const [xmin, ymin, xmax, ymax] = ann.bbox;
      x = xmin;
      y = ymin;
      w = Math.max(0, xmax - xmin);
      h = Math.max(0, ymax - ymin);
      area = w * h;
    }

    return { x, y, w, h, theta, area, vertexCount };
  };

  // Handler for BBox coordinate manual input
  const handleBBoxUpdate = (key: 'x' | 'y' | 'w' | 'h', value: number) => {
    if (!selectedAnn?.id || !selectedAnn.bbox) return;
    const [xmin, ymin, xmax, ymax] = selectedAnn.bbox;
    const curW = Math.max(1, xmax - xmin);
    const curH = Math.max(1, ymax - ymin);
    let newBBox: [number, number, number, number];

    switch (key) {
      case 'x':
        newBBox = [value, ymin, value + curW, ymax];
        break;
      case 'y':
        newBBox = [xmin, value, xmax, value + curH];
        break;
      case 'w':
        newBBox = [xmin, ymin, xmin + Math.max(1, value), ymax];
        break;
      case 'h':
        newBBox = [xmin, ymin, xmax, ymin + Math.max(1, value)];
        break;
    }
    updateAnnotation(selectedAnn.id, { bbox: newBBox });
  };

  // Handler for Rotated BBox manual input
  const handleRotatedBBoxUpdate = (key: 'cx' | 'cy' | 'w' | 'h' | 'theta', value: number) => {
    if (!selectedAnn?.id || !selectedAnn.rotated_bbox) return;
    const [cx, cy, rw, rh, angle] = selectedAnn.rotated_bbox;
    let newRot: [number, number, number, number, number];

    switch (key) {
      case 'cx':
        newRot = [value, cy, rw, rh, angle];
        break;
      case 'cy':
        newRot = [cx, value, rw, rh, angle];
        break;
      case 'w':
        newRot = [cx, cy, Math.max(1, value), rh, angle];
        break;
      case 'h':
        newRot = [cx, cy, rw, Math.max(1, value), angle];
        break;
      case 'theta':
        newRot = [cx, cy, rw, rh, value];
        break;
    }

    // Recalculate axis-aligned envelope
    const rad = ((newRot[4] || 0) * Math.PI) / 180;
    const cos = Math.abs(Math.cos(rad));
    const sin = Math.abs(Math.sin(rad));
    const boundW = newRot[2] * cos + newRot[3] * sin;
    const boundH = newRot[2] * sin + newRot[3] * cos;
    const newBBox: [number, number, number, number] = [
      Math.round(newRot[0] - boundW / 2),
      Math.round(newRot[1] - boundH / 2),
      Math.round(newRot[0] + boundW / 2),
      Math.round(newRot[1] + boundH / 2),
    ];

    updateAnnotation(selectedAnn.id, {
      rotated_bbox: newRot,
      bbox: newBBox,
    });
  };

  // Handler for Polygon position shift
  const handlePolygonShift = (axis: 'x' | 'y', targetVal: number) => {
    if (!selectedAnn?.id) return;
    const pts = selectedAnn.polygon || selectedAnn.points;
    if (!pts || pts.length === 0) return;
    const minVal = Math.min(...pts.map((p) => (axis === 'x' ? p[0] : p[1])));
    const delta = targetVal - minVal;

    const shiftedPts: Array<[number, number]> = pts.map(([px, py]) => [
      axis === 'x' ? Math.round(px + delta) : px,
      axis === 'y' ? Math.round(py + delta) : py,
    ]);
    const xs = shiftedPts.map((p) => p[0]);
    const ys = shiftedPts.map((p) => p[1]);

    updateAnnotation(selectedAnn.id, {
      polygon: shiftedPts,
      points: shiftedPts,
      bbox: [Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)],
    });
  };

  const selGeom = selectedAnn ? getAnnotationGeometry(selectedAnn) : null;

  return (
    <div className="w-80 bg-[#131822] border-l border-[#2B3547] flex flex-col h-full select-none text-slate-200">
      {/* Header: Title & Optical Calibration Scale */}
      <div className="p-3 border-b border-[#2B3547] bg-[#0B0E14] flex items-center justify-between">
        <div className="flex items-center space-x-2">
          <span className="w-2 h-2 rounded-full bg-cyan-400" />
          <span className="text-xs font-bold tracking-wider text-slate-200 uppercase">
            Geometric Inspector
          </span>
        </div>
        <div className="flex items-center space-x-1.5">
          <button
            onClick={() => setIsCalibratorOpen(!isCalibratorOpen)}
            title="Optical Calibration Settings (μm/px)"
            className="p-1 rounded bg-[#1A212E] hover:bg-[#222B3D] border border-[#2B3547] text-slate-300 text-[10px] font-mono tabular-nums flex items-center space-x-1 cursor-pointer"
          >
            <Ruler className="w-3 h-3 text-cyan-400" />
            <span>{pixelPitchUm > 0 ? `${pixelPitchUm} μm/px` : 'Not calibrated'}</span>
          </button>
          <span className="px-1.5 py-0.5 text-[10px] font-mono tabular-nums bg-[#1A212E] border border-[#2B3547] rounded text-cyan-400 font-bold">
            {annotations.length}
          </span>
        </div>
      </div>

      {selectedAnn?.type==='rotated_bbox'&&<label className="block border-b border-slate-700 p-3 text-xs text-slate-300">객체 방향 (0–360°, 선택)<input aria-label="객체 독립 방향 라벨" type="number" min="0" max="359.999" step=".1" value={selectedAnn.direction_deg??''} onChange={event=>{const value=event.target.value===''?undefined:Number(event.target.value);if(value===undefined||Number.isFinite(value)&&value>=0&&value<360)updateAnnotation(selectedAnn.id!,{direction_deg:value});}} className="mt-1 w-full rounded border border-slate-600 bg-slate-900 p-2"/><span className="mt-1 block text-slate-400">머리·꼬리 방향 타깃. 박스의 축 회전 각도와 별도로 저장합니다.</span></label>}
      {/* Optical Calibration Popdown */}
      {isCalibratorOpen && (
        <div className="p-2.5 bg-[#1A212E] border-b border-[#2B3547] text-xs space-y-2">
          <div className="text-[10px] font-semibold text-slate-400 uppercase tracking-wider flex items-center justify-between">
            <span>Optical Calibration</span>
            <span className="text-cyan-400 font-mono">{pixelPitchUm > 0 ? `1 px = ${pixelPitchUm} μm` : 'Measured scale required'}</span>
          </div>
          <div className="flex items-center space-x-2">
            <span className="text-[11px] text-slate-400">Scale:</span>
            <input
              type="number"
              step="0.1"
              min="0.1"
              max="100"
              value={pixelPitchUm || ''}
              placeholder="Enter measured pitch"
              onChange={(e) => setPixelPitchUm(Math.max(0, parseFloat(e.target.value) || 0))}
              className="flex-1 bg-[#131822] border border-[#2B3547] rounded px-2 py-0.5 text-xs font-mono tabular-nums text-slate-200 focus:outline-none focus:border-cyan-500"
            />
            <span className="text-[10px] font-mono text-slate-400">μm/px</span>
          </div>
          <p className="text-[10px] text-slate-400">촬영 장비의 실측 μm/px 값만 입력하세요.</p>
        </div>
      )}

      {/* Main Annotation List */}
      <div className="flex-1 overflow-y-auto p-2 space-y-1.5">
        <div className="text-[10px] font-semibold text-slate-400 uppercase tracking-wider px-1">
          Annotations ({annotations.length})
        </div>

        {annotations.length === 0 ? (
          <div className="text-center py-10 px-4 bg-[#1A212E]/50 border border-[#2B3547] rounded">
            <div className="w-8 h-8 mx-auto mb-2 text-slate-500 flex items-center justify-center">
              <Ruler className="w-5 h-5 stroke-[1.5]" />
            </div>
            <div className="text-xs font-medium text-slate-300">No annotations on this image</div>
            <div className="mt-1 text-[11px] text-slate-400">Select a canvas tool [1-6] to calibrate regions.</div>
          </div>
        ) : (
          annotations.map((ann, idx) => {
            const isSelected = ann.id === selectedAnnotationId;
            const geom = getAnnotationGeometry(ann);

            return (
              <div
                key={ann.id || idx}
                onClick={() => setSelectedAnnotationId(ann.id || null)}
                className={`flex flex-col p-2 rounded border transition-colors cursor-pointer ${
                  isSelected
                    ? 'bg-[#1F2E40] border-cyan-500/80 ring-1 ring-cyan-500/30 text-white'
                    : 'bg-[#1A212E] hover:bg-[#222B3D] border-[#2B3547] text-slate-300'
                }`}
              >
                <div className="flex items-center justify-between">
                  <div className="flex items-center space-x-2 truncate">
                    <span
                      className="w-2.5 h-2.5 rounded-full flex-shrink-0"
                      style={{ backgroundColor: ann.color || '#3b82f6' }}
                    />
                    {ann.type === 'bbox' && <Square className="w-3.5 h-3.5 text-cyan-400 flex-shrink-0" />}
                    {ann.type === 'rotated_bbox' && <RotateCw className="w-3.5 h-3.5 text-purple-400 flex-shrink-0" />}
                    {ann.type === 'polygon' && <Scissors className="w-3.5 h-3.5 text-amber-400 flex-shrink-0" />}
                    {ann.type === 'brush_mask' && <Paintbrush className="w-3.5 h-3.5 text-emerald-400 flex-shrink-0" />}
                    {ann.type === 'tag' && <CheckCircle2 className="w-3.5 h-3.5 text-emerald-400 flex-shrink-0" />}
                    <span className="truncate text-xs font-medium text-slate-200">{ann.label}</span>
                  </div>

                  <div className="flex items-center space-x-1">
                    <button
                      onClick={(e) => {
                        e.stopPropagation();
                        if (ann.id) deleteAnnotation(ann.id);
                      }}
                      className="p-1 hover:text-red-400 text-slate-500 transition-colors cursor-pointer"
                      title="Delete annotation"
                    >
                      <Trash2 className="w-3.5 h-3.5" />
                    </button>
                  </div>
                </div>

                {/* Sub-item geometric summary */}
                {ann.type === 'brush_mask' && (
                  <div className="mt-1 border-t border-[#2B3547]/50 pt-1 text-[10px] text-slate-400">
                    브러시 마스크 · 픽셀 면적 미계측
                  </div>
                )}
                {ann.type !== 'tag' && ann.type !== 'brush_mask' && (
                  <div className="mt-1 flex items-center justify-between text-[10px] font-mono tabular-nums text-slate-400 border-t border-[#2B3547]/50 pt-1">
                    <span>
                      {Math.round(geom.w)}×{Math.round(geom.h)} <span className="text-slate-500">px</span>
                    </span>
                    <span>
                      {pixelPitchUm > 0 ? `${Math.round(geom.w * pixelPitchUm)}×${Math.round(geom.h * pixelPitchUm)} μm` : 'Physical scale unset'}
                    </span>
                    <span>
                      {Math.round(geom.area).toLocaleString()} <span className="text-slate-500">px²</span>
                    </span>
                  </div>
                )}
              </div>
            );
          })
        )}
      </div>

      {/* Selected Annotation Geometric Measurement Inspector Panel */}
      {selectedAnn && selGeom && selectedAnn.type !== 'brush_mask' && selectedAnn.type !== 'tag' && (
        <div className="border-t border-[#2B3547] bg-[#0B0E14] p-3 space-y-3">
          <div className="flex items-center justify-between border-b border-[#2B3547] pb-2">
            <div className="flex items-center space-x-1.5">
              <Crosshair className="w-3.5 h-3.5 text-cyan-400" />
              <span className="text-[11px] font-bold text-slate-200 uppercase tracking-wide">
                Geometric Inspector
              </span>
            </div>
            <span className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-[#1A212E] border border-[#2B3547] text-cyan-400 uppercase">
              {selectedAnn.type}
            </span>
          </div>

          {/* Readout & Manual Calibration Grid: X, Y, W, H */}
          <div className="grid grid-cols-2 gap-2 text-xs">
            {/* X coordinate */}
            <div className="bg-[#1A212E] border border-[#2B3547] rounded p-1.5 flex flex-col space-y-1">
              <div className="flex items-center justify-between text-[10px] text-slate-400">
                <span className="font-bold text-slate-300">X</span>
                <span className="font-mono tabular-nums text-cyan-400">
                  {pixelPitchUm > 0 ? `${(selGeom.x * pixelPitchUm).toFixed(1)} μm` : '—'}
                </span>
              </div>
              <div className="flex items-center space-x-1">
                <input
                  type="number"
                  step="1"
                  value={Math.round(selGeom.x)}
                  onChange={(e) => {
                    const val = parseFloat(e.target.value) || 0;
                    if (selectedAnn.type === 'bbox') handleBBoxUpdate('x', val);
                    else if (selectedAnn.type === 'rotated_bbox') handleRotatedBBoxUpdate('cx', val + selGeom.w / 2);
                    else if (selectedAnn.type === 'polygon') handlePolygonShift('x', val);
                  }}
                  className="w-full bg-[#131822] border border-[#2B3547] rounded px-1.5 py-0.5 text-xs font-mono tabular-nums text-slate-100 focus:outline-none focus:border-cyan-500"
                />
                <span className="text-[10px] font-mono text-slate-400">px</span>
              </div>
            </div>

            {/* Y coordinate */}
            <div className="bg-[#1A212E] border border-[#2B3547] rounded p-1.5 flex flex-col space-y-1">
              <div className="flex items-center justify-between text-[10px] text-slate-400">
                <span className="font-bold text-slate-300">Y</span>
                <span className="font-mono tabular-nums text-cyan-400">
                  {pixelPitchUm > 0 ? `${(selGeom.y * pixelPitchUm).toFixed(1)} μm` : '—'}
                </span>
              </div>
              <div className="flex items-center space-x-1">
                <input
                  type="number"
                  step="1"
                  value={Math.round(selGeom.y)}
                  onChange={(e) => {
                    const val = parseFloat(e.target.value) || 0;
                    if (selectedAnn.type === 'bbox') handleBBoxUpdate('y', val);
                    else if (selectedAnn.type === 'rotated_bbox') handleRotatedBBoxUpdate('cy', val + selGeom.h / 2);
                    else if (selectedAnn.type === 'polygon') handlePolygonShift('y', val);
                  }}
                  className="w-full bg-[#131822] border border-[#2B3547] rounded px-1.5 py-0.5 text-xs font-mono tabular-nums text-slate-100 focus:outline-none focus:border-cyan-500"
                />
                <span className="text-[10px] font-mono text-slate-400">px</span>
              </div>
            </div>

            {/* W (Width) */}
            <div className="bg-[#1A212E] border border-[#2B3547] rounded p-1.5 flex flex-col space-y-1">
              <div className="flex items-center justify-between text-[10px] text-slate-400">
                <span className="font-bold text-slate-300">W</span>
                <span className="font-mono tabular-nums text-cyan-400">
                  {pixelPitchUm > 0 ? `${(selGeom.w * pixelPitchUm).toFixed(1)} μm` : '—'}
                </span>
              </div>
              <div className="flex items-center space-x-1">
                <input
                  type="number"
                  step="1"
                  min="1"
                  value={Math.round(selGeom.w)}
                  onChange={(e) => {
                    const val = Math.max(1, parseFloat(e.target.value) || 1);
                    if (selectedAnn.type === 'bbox') handleBBoxUpdate('w', val);
                    else if (selectedAnn.type === 'rotated_bbox') handleRotatedBBoxUpdate('w', val);
                  }}
                  disabled={selectedAnn.type === 'polygon'}
                  className="w-full bg-[#131822] border border-[#2B3547] rounded px-1.5 py-0.5 text-xs font-mono tabular-nums text-slate-100 focus:outline-none focus:border-cyan-500 disabled:opacity-40"
                />
                <span className="text-[10px] font-mono text-slate-400">px</span>
              </div>
            </div>

            {/* H (Height) */}
            <div className="bg-[#1A212E] border border-[#2B3547] rounded p-1.5 flex flex-col space-y-1">
              <div className="flex items-center justify-between text-[10px] text-slate-400">
                <span className="font-bold text-slate-300">H</span>
                <span className="font-mono tabular-nums text-cyan-400">
                  {pixelPitchUm > 0 ? `${(selGeom.h * pixelPitchUm).toFixed(1)} μm` : '—'}
                </span>
              </div>
              <div className="flex items-center space-x-1">
                <input
                  type="number"
                  step="1"
                  min="1"
                  value={Math.round(selGeom.h)}
                  onChange={(e) => {
                    const val = Math.max(1, parseFloat(e.target.value) || 1);
                    if (selectedAnn.type === 'bbox') handleBBoxUpdate('h', val);
                    else if (selectedAnn.type === 'rotated_bbox') handleRotatedBBoxUpdate('h', val);
                  }}
                  disabled={selectedAnn.type === 'polygon'}
                  className="w-full bg-[#131822] border border-[#2B3547] rounded px-1.5 py-0.5 text-xs font-mono tabular-nums text-slate-100 focus:outline-none focus:border-cyan-500 disabled:opacity-40"
                />
                <span className="text-[10px] font-mono text-slate-400">px</span>
              </div>
            </div>
          </div>

          {/* Readout & Manual Calibration: Angle θ and Area */}
          <div className="grid grid-cols-2 gap-2 text-xs">
            {/* Angle θ */}
            <div className="bg-[#1A212E] border border-[#2B3547] rounded p-1.5 flex flex-col space-y-1">
              <div className="flex items-center justify-between text-[10px] text-slate-400">
                <span className="font-bold text-slate-300">Angle (θ)</span>
                <span className="font-mono tabular-nums text-purple-400">{selGeom.theta.toFixed(1)}°</span>
              </div>
              <div className="flex items-center space-x-1">
                <input
                  type="number"
                  step="0.5"
                  value={selGeom.theta}
                  onChange={(e) => {
                    const val = parseFloat(e.target.value) || 0;
                    if (selectedAnn.type === 'rotated_bbox') handleRotatedBBoxUpdate('theta', val);
                  }}
                  disabled={selectedAnn.type !== 'rotated_bbox'}
                  className="w-full bg-[#131822] border border-[#2B3547] rounded px-1.5 py-0.5 text-xs font-mono tabular-nums text-slate-100 focus:outline-none focus:border-cyan-500 disabled:opacity-40"
                />
                <span className="text-[10px] font-mono text-slate-400">°</span>
              </div>
            </div>

            {/* Area */}
            <div className="bg-[#1A212E] border border-[#2B3547] rounded p-1.5 flex flex-col justify-between">
              <div className="flex items-center justify-between text-[10px] text-slate-400">
                <span className="font-bold text-slate-300">Area</span>
                <span className="font-mono tabular-nums text-cyan-400">
                  {pixelPitchUm > 0 ? `${Math.round(selGeom.area * pixelPitchUm * pixelPitchUm).toLocaleString()} μm²` : '—'}
                </span>
              </div>
              <div className="flex items-center justify-between pt-1">
                <span className="font-mono tabular-nums text-xs text-slate-100 font-semibold">
                  {Math.round(selGeom.area).toLocaleString()}
                </span>
                <span className="text-[10px] font-mono text-slate-400">px²</span>
              </div>
            </div>
          </div>

          {/* Subpixel Nudge Instruction Footer */}
          <div className="bg-[#1A212E]/60 border border-[#2B3547] rounded px-2 py-1 text-[10px] font-mono text-slate-400 flex items-center justify-between">
            <span className="text-slate-300">Nudge:</span>
            <span>
              [↑↓←→] <span className="text-cyan-400">±1 px</span> | [Shift+↑↓←→]{' '}
              <span className="text-cyan-400">±10 px</span>
            </span>
          </div>
        </div>
      )}
    </div>
  );
};

export default AnnotationList;
