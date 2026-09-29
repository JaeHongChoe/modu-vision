/**
 * src/renderer/components/training/OscilloscopeLossCurve.tsx
 * Tektronix / Keyence Industrial CRT Phosphor Storage Oscilloscope (DSO).
 * Dual-channel real-time loss streaming (CH1: Train Loss, CH2: Val Loss),
 * selectable Linear vs Logarithmic Decibel (dB) scale, precision graticule grid,
 * live electron beam sweep cursor, and interactive inspection reticle.
 */

import React, { useState, useMemo, useRef, useCallback } from 'react';
import { Activity } from 'lucide-react';
import type { LossPoint } from '../../stores/useTrainingStore';

export interface OscilloscopeLossCurveProps {
  lossHistory: LossPoint[];
  trainLoss: number | null;
  valLoss: number | null;
  currentEpoch: number;
  totalEpochs: number;
  currentStep: number;
  totalSteps: number;
  isTraining: boolean;
  etaSeconds: number | null;
  language: 'ko' | 'en';
}

export type ScaleMode = 'linear' | 'db';
export type ChannelFilter = 'all' | 'ch1' | 'ch2';

export const OscilloscopeLossCurve: React.FC<OscilloscopeLossCurveProps> = ({
  lossHistory,
  trainLoss,
  valLoss,
  currentEpoch,
  totalEpochs,
  currentStep,
  totalSteps,
  isTraining,
  etaSeconds,
  language,
}) => {
  const [scaleMode, setScaleMode] = useState<ScaleMode>('linear');
  const [channelFilter, setChannelFilter] = useState<ChannelFilter>('all');
  const [hoverIndex, setHoverIndex] = useState<number | null>(null);
  const [mousePos, setMousePos] = useState<{ x: number; y: number } | null>(null);

  const containerRef = useRef<HTMLDivElement>(null);

  // Oscilloscope viewport dimensions
  const svgWidth = 640;
  const svgHeight = 240;
  const padding = { top: 24, right: 20, bottom: 32, left: 52 };
  const plotWidth = svgWidth - padding.left - padding.right;
  const plotHeight = svgHeight - padding.top - padding.bottom;

  // Maximum epoch ceiling
  const maxEpoch = Math.max(totalEpochs || 20, ...lossHistory.map((p) => p.epoch), 1);

  // Compute min/max loss across history
  const { minLoss, maxLoss, initialLoss } = useMemo(() => {
    const validPoints = lossHistory.filter((p) => !isNaN(p.trainLoss) && !isNaN(p.valLoss));
    if (validPoints.length === 0) {
      return { minLoss: 0.0, maxLoss: 1.0, initialLoss: 1.0 };
    }
    const all = validPoints.flatMap((p) => [p.trainLoss, p.valLoss]).filter((v) => v > 0);
    const minVal = Math.min(...all, 0.001);
    const maxVal = Math.max(...all, 1.0);
    const initVal = validPoints[0]?.trainLoss || 1.0;
    return { minLoss: minVal, maxLoss: maxVal, initialLoss: initVal };
  }, [lossHistory]);

  // Coordinate projection math
  const getX = useCallback(
    (epoch: number) => {
      const ratio = (epoch - 1) / Math.max(1, maxEpoch - 1);
      return padding.left + ratio * plotWidth;
    },
    [maxEpoch, plotWidth, padding.left]
  );

  const getY = useCallback(
    (loss: number) => {
      if (scaleMode === 'linear') {
        const range = Math.max(0.001, maxLoss - minLoss);
        const ratio = (loss - minLoss) / range;
        return padding.top + (1 - Math.min(1, Math.max(0, ratio))) * plotHeight;
      } else {
        // Logarithmic Decibel (dB) scale: 20 * log10(loss / initialLoss)
        const safeLoss = Math.max(loss, 1e-4);
        const lossDb = 20 * Math.log10(safeLoss / Math.max(initialLoss, 1e-4));
        const minDb = -40; // Floor at -40 dB
        const maxDb = 0; // Ceiling at 0 dB (initial loss)
        const ratio = (lossDb - minDb) / (maxDb - minDb);
        return padding.top + (1 - Math.min(1, Math.max(0, ratio))) * plotHeight;
      }
    },
    [scaleMode, minLoss, maxLoss, initialLoss, plotHeight, padding.top]
  );

  // Trace Polylines Generation
  const { trainPolyline, valPolyline } = useMemo(() => {
    if (lossHistory.length < 1) {
      return { trainPolyline: '', valPolyline: '' };
    }
    const trainPts = lossHistory.map((p) => `${getX(p.epoch).toFixed(1)},${getY(p.trainLoss).toFixed(1)}`).join(' ');
    const valPts = lossHistory.map((p) => `${getX(p.epoch).toFixed(1)},${getY(p.valLoss).toFixed(1)}`).join(' ');
    return { trainPolyline: trainPts, valPolyline: valPts };
  }, [lossHistory, getX, getY]);

  // Format Time Helper
  const formatTime = (secs: number | null): string => {
    if (secs === null || secs <= 0) return '--:--';
    const m = Math.floor(secs / 60);
    const s = Math.floor(secs % 60);
    return `${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}`;
  };

  // Hover hit-testing
  const handleMouseMove = (e: React.MouseEvent<SVGSVGElement>) => {
    if (!containerRef.current || lossHistory.length === 0) return;
    const rect = e.currentTarget.getBoundingClientRect();
    const clientX = e.clientX - rect.left;
    const clientY = e.clientY - rect.top;

    if (clientX < padding.left || clientX > svgWidth - padding.right) {
      setHoverIndex(null);
      setMousePos(null);
      return;
    }

    setMousePos({ x: clientX, y: clientY });

    // Find nearest point along X
    let closestIdx = 0;
    let closestDist = Infinity;
    lossHistory.forEach((pt, idx) => {
      const ptX = getX(pt.epoch);
      const dist = Math.abs(ptX - clientX);
      if (dist < closestDist) {
        closestDist = dist;
        closestIdx = idx;
      }
    });

    setHoverIndex(closestIdx);
  };

  const handleMouseLeave = () => {
    setHoverIndex(null);
    setMousePos(null);
  };

  // Live Sweep Cursor X Position
  const sweepX = useMemo(() => {
    if (!isTraining || maxEpoch <= 1) return null;
    const epochRatio = (currentEpoch - 1) / Math.max(1, maxEpoch - 1);
    const stepRatio = totalSteps > 0 ? currentStep / totalSteps / maxEpoch : 0;
    const combinedRatio = Math.min(1, Math.max(0, epochRatio + stepRatio));
    return padding.left + combinedRatio * plotWidth;
  }, [isTraining, currentEpoch, maxEpoch, currentStep, totalSteps, padding.left, plotWidth]);

  // Selected hover point
  const hoverPoint = hoverIndex !== null ? lossHistory[hoverIndex] : null;

  // Grid divisions: 4 vertical steps for Y axis
  const yDivisions = useMemo(() => {
    const steps = 4;
    return Array.from({ length: steps + 1 }, (_, i) => {
      const y = padding.top + (i / steps) * plotHeight;
      let label = '';
      if (scaleMode === 'linear') {
        const val = maxLoss - (i / steps) * (maxLoss - minLoss);
        label = val >= 10 ? val.toFixed(1) : val.toFixed(3);
      } else {
        const dbVal = 0 - (i / steps) * 40;
        label = `${dbVal.toFixed(0)} dB`;
      }
      return { y, label };
    });
  }, [scaleMode, minLoss, maxLoss, padding.top, plotHeight]);

  // Grid divisions: 5 horizontal steps for X axis (Epochs)
  const xDivisions = useMemo(() => {
    const steps = Math.min(5, maxEpoch);
    return Array.from({ length: steps }, (_, i) => {
      const epoch = Math.round(1 + (i / (steps - 1 || 1)) * (maxEpoch - 1));
      const x = getX(epoch);
      return { x, label: `EP ${epoch}` };
    });
  }, [maxEpoch, getX]);

  return (
    <div
      ref={containerRef}
      className="bg-[#0B0E14] border border-[#2B3547] rounded-[4px] p-3 flex flex-col select-none relative"
    >
      {/* Top CRT Bezel Control Bar */}
      <div className="flex items-center justify-between pb-2.5 mb-2 border-b border-[#1E293B]">
        {/* Left: Model Stamp & Channel Annunciators */}
        <div className="flex items-center space-x-3">
          <div className="flex items-center space-x-1.5">
            <Activity className="w-3.5 h-3.5 text-blue-400" />
            <span className="font-mono text-[11px] font-bold tracking-wider text-slate-200">
              CRT OSCILLOSCOPE DS-9000V
            </span>
          </div>

          <div className="flex items-center space-x-2">
            {/* CH1 Train Annunciator */}
            <button
              type="button"
              onClick={() => setChannelFilter(channelFilter === 'ch1' ? 'all' : 'ch1')}
              className={`inline-flex items-center space-x-1.5 px-2 py-0.5 rounded-[2px] border text-[10px] font-mono font-bold transition-all cursor-pointer ${
                channelFilter === 'ch1' || channelFilter === 'all'
                  ? 'bg-[#0B1528] border-[#3B82F6] text-[#3B82F6]'
                  : 'bg-[#0B0E14] border-[#1E293B] text-slate-500'
              }`}
            >
              <span className="w-1.5 h-1.5 rounded-full bg-[#3B82F6]" />
              <span>CH1: TRAIN</span>
              <span className="text-slate-100 tabular-nums">
                {trainLoss !== null ? trainLoss.toFixed(4) : '--'}
              </span>
            </button>

            {/* CH2 Val Annunciator */}
            <button
              type="button"
              onClick={() => setChannelFilter(channelFilter === 'ch2' ? 'all' : 'ch2')}
              className={`inline-flex items-center space-x-1.5 px-2 py-0.5 rounded-[2px] border text-[10px] font-mono font-bold transition-all cursor-pointer ${
                channelFilter === 'ch2' || channelFilter === 'all'
                  ? 'bg-[#1F1707] border-[#F59E0B] text-[#F59E0B]'
                  : 'bg-[#0B0E14] border-[#1E293B] text-slate-500'
              }`}
            >
              <span className="w-1.5 h-1.5 rounded-full bg-[#F59E0B]" />
              <span>CH2: VAL</span>
              <span className="text-slate-100 tabular-nums">
                {valLoss !== null ? valLoss.toFixed(4) : '--'}
              </span>
            </button>
          </div>
        </div>

        {/* Right: Scale Mode & Trace Filter Controls */}
        <div className="flex items-center space-x-2">
          {/* Scale Mode Switcher */}
          <div className="inline-flex rounded-[3px] bg-[#131822] p-0.5 border border-[#2B3547]">
            <button
              type="button"
              onClick={() => setScaleMode('linear')}
              className={`px-2 py-0.5 rounded-[2px] text-[10px] font-mono font-bold transition-all cursor-pointer ${
                scaleMode === 'linear'
                  ? 'bg-[#2B3547] text-slate-100'
                  : 'text-slate-400 hover:text-slate-200'
              }`}
            >
              LINEAR
            </button>
            <button
              type="button"
              onClick={() => setScaleMode('db')}
              className={`px-2 py-0.5 rounded-[2px] text-[10px] font-mono font-bold transition-all cursor-pointer ${
                scaleMode === 'db'
                  ? 'bg-[#2B3547] text-slate-100'
                  : 'text-slate-400 hover:text-slate-200'
              }`}
            >
              LOG (dB)
            </button>
          </div>
        </div>
      </div>

      {/* Recessed CRT Screen Chassis */}
      <div className="relative w-full bg-[#070A0F] border border-[#1A2433] rounded-[2px] overflow-hidden flex items-center justify-center">
        {/* Subtle Phosphor Graticule Mesh Lines */}
        <svg
          width="100%"
          height={svgHeight}
          viewBox={`0 0 ${svgWidth} ${svgHeight}`}
          className="w-full h-full cursor-crosshair overflow-visible"
          onMouseMove={handleMouseMove}
          onMouseLeave={handleMouseLeave}
        >
          <defs>
            {/* Phosphor Persistence Decay Gradient */}
            <linearGradient id="sweepGradient" x1="0%" y1="0%" x2="100%" y2="0%">
              <stop offset="0%" stopColor="#3B82F6" stopOpacity="0.0" />
              <stop offset="90%" stopColor="#3B82F6" stopOpacity="0.15" />
              <stop offset="100%" stopColor="#60A5FA" stopOpacity="0.4" />
            </linearGradient>

            {/* Subtle Phosphor Grid Pattern */}
            <pattern id="crtSubGrid" width="16" height="16" patternUnits="userSpaceOnUse">
              <path d="M 16 0 L 0 0 0 16" fill="none" stroke="#101722" strokeWidth="0.5" />
            </pattern>
          </defs>

          {/* Sub-grid pattern background */}
          <rect
            x={padding.left}
            y={padding.top}
            width={plotWidth}
            height={plotHeight}
            fill="url(#crtSubGrid)"
          />

          {/* Major Horizontal Graticule Grid & Y-Labels */}
          {yDivisions.map((div, i) => (
            <g key={`y-${i}`}>
              <line
                x1={padding.left}
                y1={div.y}
                x2={svgWidth - padding.right}
                y2={div.y}
                stroke="#1A2433"
                strokeWidth="1"
                strokeDasharray={i === 0 || i === yDivisions.length - 1 ? 'none' : '2,2'}
              />
              <text
                x={padding.left - 6}
                y={div.y + 3.5}
                textAnchor="end"
                fill="#64748B"
                fontSize="9"
                fontFamily="JetBrains Mono, monospace"
                className="tabular-nums"
              >
                {div.label}
              </text>
            </g>
          ))}

          {/* Major Vertical Graticule Grid & X-Labels */}
          {xDivisions.map((div, i) => (
            <g key={`x-${i}`}>
              <line
                x1={div.x}
                y1={padding.top}
                x2={div.x}
                y2={svgHeight - padding.bottom}
                stroke="#1A2433"
                strokeWidth="1"
                strokeDasharray="2,2"
              />
              <text
                x={div.x}
                y={svgHeight - padding.bottom + 14}
                textAnchor="middle"
                fill="#64748B"
                fontSize="9"
                fontFamily="JetBrains Mono, monospace"
                className="tabular-nums"
              >
                {div.label}
              </text>
            </g>
          ))}

          {/* Center Reticle Crosshairs */}
          <line
            x1={padding.left + plotWidth / 2}
            y1={padding.top}
            x2={padding.left + plotWidth / 2}
            y2={svgHeight - padding.bottom}
            stroke="#202F44"
            strokeWidth="1"
          />
          <line
            x1={padding.left}
            y1={padding.top + plotHeight / 2}
            x2={svgWidth - padding.right}
            y2={padding.top + plotHeight / 2}
            stroke="#202F44"
            strokeWidth="1"
          />

          {/* Live Sweep Persistence Band */}
          {sweepX !== null && (
            <g>
              <rect
                x={Math.max(padding.left, sweepX - 40)}
                y={padding.top}
                width={Math.min(40, sweepX - padding.left)}
                height={plotHeight}
                fill="url(#sweepGradient)"
              />
              <line
                x1={sweepX}
                y1={padding.top}
                x2={sweepX}
                y2={svgHeight - padding.bottom}
                stroke="#60A5FA"
                strokeWidth="1.5"
                opacity="0.9"
              />
            </g>
          )}

          {/* CH1 Train Loss Trace */}
          {(channelFilter === 'all' || channelFilter === 'ch1') && trainPolyline && (
            <g>
              {/* Outer Glow Path */}
              <polyline
                fill="none"
                stroke="#3B82F6"
                strokeWidth="3.5"
                strokeOpacity="0.25"
                strokeLinecap="round"
                strokeLinejoin="round"
                points={trainPolyline}
              />
              {/* Core High-Intensity Beam */}
              <polyline
                fill="none"
                stroke="#60A5FA"
                strokeWidth="1.5"
                strokeLinecap="round"
                strokeLinejoin="round"
                points={trainPolyline}
              />
            </g>
          )}

          {/* CH2 Val Loss Trace */}
          {(channelFilter === 'all' || channelFilter === 'ch2') && valPolyline && (
            <g>
              {/* Outer Glow Path */}
              <polyline
                fill="none"
                stroke="#F59E0B"
                strokeWidth="3.5"
                strokeOpacity="0.25"
                strokeLinecap="round"
                strokeLinejoin="round"
                points={valPolyline}
              />
              {/* Core High-Intensity Beam */}
              <polyline
                fill="none"
                stroke="#FBBF24"
                strokeWidth="1.5"
                strokeLinecap="round"
                strokeLinejoin="round"
                points={valPolyline}
              />
              {/* Validation Epoch Markers */}
              {lossHistory.map((p) => {
                const px = getX(p.epoch);
                const py = getY(p.valLoss);
                return (
                  <circle
                    key={`val-dot-${p.epoch}`}
                    cx={px}
                    cy={py}
                    r="2.5"
                    fill="#FBBF24"
                    stroke="#0B0E14"
                    strokeWidth="1"
                  />
                );
              })}
            </g>
          )}

          {/* Interactive Inspection Reticle */}
          {mousePos && hoverPoint && (
            <g>
              {/* Vertical Crosshair */}
              <line
                x1={getX(hoverPoint.epoch)}
                y1={padding.top}
                x2={getX(hoverPoint.epoch)}
                y2={svgHeight - padding.bottom}
                stroke="#94A3B8"
                strokeWidth="1"
                strokeDasharray="2,2"
              />
              {/* Horizontal Crosshair */}
              <line
                x1={padding.left}
                y1={mousePos.y}
                x2={svgWidth - padding.right}
                y2={mousePos.y}
                stroke="#94A3B8"
                strokeWidth="1"
                strokeDasharray="2,2"
              />
              {/* Marker on Train Loss */}
              <circle
                cx={getX(hoverPoint.epoch)}
                cy={getY(hoverPoint.trainLoss)}
                r="4"
                fill="#3B82F6"
                stroke="#FFFFFF"
                strokeWidth="1.5"
              />
              {/* Marker on Val Loss */}
              <circle
                cx={getX(hoverPoint.epoch)}
                cy={getY(hoverPoint.valLoss)}
                r="4"
                fill="#F59E0B"
                stroke="#FFFFFF"
                strokeWidth="1.5"
              />
            </g>
          )}

          {/* Empty State Message */}
          {lossHistory.length === 0 && (
            <text
              x={svgWidth / 2}
              y={svgHeight / 2}
              textAnchor="middle"
              fill="#475569"
              fontSize="11"
              fontFamily="JetBrains Mono, monospace"
            >
              {language === 'ko'
                ? '학습 시작 시 실시간 오실로스코프 손실 곡선이 스트리밍됩니다.'
                : 'Real-time oscilloscope loss traces will stream upon training.'}
            </text>
          )}
        </svg>

        {/* Floating Telemetry HUD Badge (Top-Right) */}
        <div className="absolute top-2 right-2 bg-[#0B0E14]/90 border border-[#2B3547] rounded-[3px] p-2 text-[10px] font-mono tabular-nums pointer-events-none">
          <div className="grid grid-cols-2 gap-x-3 gap-y-1 text-slate-300">
            <div>
              <span className="text-slate-500 mr-1.5">EPOCH:</span>
              <span className="font-bold text-slate-100">
                {currentEpoch.toString().padStart(2, '0')}/{totalEpochs.toString().padStart(2, '0')}
              </span>
            </div>
            <div>
              <span className="text-slate-500 mr-1.5">STEP:</span>
              <span className="font-bold text-slate-100">
                {currentStep}/{totalSteps}
              </span>
            </div>
            <div>
              <span className="text-blue-400 mr-1.5">CH1:</span>
              <span className="font-bold text-blue-300">
                {trainLoss !== null ? trainLoss.toFixed(4) : '--'}
              </span>
            </div>
            <div>
              <span className="text-amber-400 mr-1.5">CH2:</span>
              <span className="font-bold text-amber-300">
                {valLoss !== null ? valLoss.toFixed(4) : '--'}
              </span>
            </div>
            <div className="col-span-2 border-t border-[#1E293B] pt-0.5 mt-0.5 flex justify-between text-[9px]">
              <span className="text-slate-500">TIME REMAINING:</span>
              <span className="font-bold text-amber-400">{formatTime(etaSeconds)}</span>
            </div>
          </div>
        </div>

        {/* Hover Readout Tooltip Card */}
        {hoverPoint && mousePos && (
          <div
            className="absolute z-20 pointer-events-none bg-[#131822] border border-[#3B82F6] rounded-[3px] p-2 text-[10px] font-mono tabular-nums text-slate-200"
            style={{
              left: Math.min(mousePos.x + 12, svgWidth - 160),
              top: Math.max(10, Math.min(mousePos.y - 40, svgHeight - 80)),
            }}
          >
            <div className="font-bold text-slate-100 pb-1 border-b border-[#2B3547] mb-1">
              EPOCH {hoverPoint.epoch} MEASUREMENT
            </div>
            <div className="space-y-0.5">
              <div className="flex justify-between space-x-3">
                <span className="text-blue-400">Train Loss:</span>
                <span className="font-bold text-slate-100">{hoverPoint.trainLoss.toFixed(4)}</span>
              </div>
              <div className="flex justify-between space-x-3">
                <span className="text-amber-400">Val Loss:</span>
                <span className="font-bold text-slate-100">{hoverPoint.valLoss.toFixed(4)}</span>
              </div>
              {hoverPoint.lr && (
                <div className="flex justify-between space-x-3 text-slate-400 text-[9px]">
                  <span>Learning Rate:</span>
                  <span>{hoverPoint.lr.toExponential(2)}</span>
                </div>
              )}
            </div>
          </div>
        )}
      </div>

      {/* Oscilloscope Chassis Footer Ticks */}
      <div className="flex items-center justify-between pt-2 mt-1 text-[9px] font-mono text-slate-500">
        <div>TIMEBASE: 1.0 EPOCH/DIV • SAMPLING: 10Hz REALTIME</div>
        <div>BANDWIDTH: FULL CONVERGENCE • TRIGGER: AUTO</div>
      </div>
    </div>
  );
};
