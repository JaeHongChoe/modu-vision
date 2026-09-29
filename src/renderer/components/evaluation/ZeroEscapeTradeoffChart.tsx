/**
 * src/renderer/components/evaluation/ZeroEscapeTradeoffChart.tsx
 * Keyence Oscilloscope Style Interactive Zero-Escape Calibration Curve.
 * Plots Escape Rate (Crimson) vs Overkill Rate (Amber) vs Threshold tau in [0.0, 1.0].
 */

import React, { useRef, useState, useCallback, useMemo } from 'react';
import { Target, Zap } from 'lucide-react';
import type { OverkillUnderkillPoint } from '../../types';

export interface ZeroEscapeTradeoffChartProps {
  tradeoffCurve: OverkillUnderkillPoint[];
  currentThreshold: number;
  optimalThreshold: number;
  onThresholdChange: (tau: number) => void;
  onApplyOptimal: () => void;
  height?: number;
}

export const ZeroEscapeTradeoffChart: React.FC<ZeroEscapeTradeoffChartProps> = ({
  tradeoffCurve,
  currentThreshold,
  optimalThreshold,
  onThresholdChange,
  onApplyOptimal,
  height = 190,
}) => {
  const svgRef = useRef<SVGSVGElement | null>(null);
  const [isDragging, setIsDragging] = useState(false);
  const [hoverTau, setHoverTau] = useState<number | null>(null);

  // SVG viewport geometry
  const width = 480;
  const padLeft = 40;
  const padRight = 15;
  const padTop = 15;
  const padBottom = 25;
  const plotW = width - padLeft - padRight;
  const plotH = height - padTop - padBottom;

  const tauToX = useCallback((tau: number) => padLeft + Math.max(0, Math.min(1, tau)) * plotW, [padLeft, plotW]);
  const rateToY = useCallback((rate: number) => padTop + plotH - (Math.max(0, Math.min(100, rate)) / 100) * plotH, [padTop, plotH]);
  const xToTau = useCallback((x: number) => {
    const raw = (x - padLeft) / plotW;
    return Math.max(0.01, Math.min(0.99, Math.round(raw * 100) / 100));
  }, [padLeft, plotW]);

  // Generate smooth SVG paths
  const { escapePath, overkillPath, costPath } = useMemo(() => {
    if (!tradeoffCurve || tradeoffCurve.length === 0) {
      return { escapePath: '', overkillPath: '', costPath: '' };
    }
    const sorted = [...tradeoffCurve].sort((a, b) => a.threshold - b.threshold);
    const maxCost = Math.max(...sorted.map((p) => p.total_cost || 1), 1);

    const escPts = sorted.map((p) => `${tauToX(p.threshold).toFixed(1)},${rateToY(p.underkill_rate).toFixed(1)}`);
    const ovkPts = sorted.map((p) => `${tauToX(p.threshold).toFixed(1)},${rateToY(p.overkill_rate).toFixed(1)}`);
    const cstPts = sorted.map((p) => `${tauToX(p.threshold).toFixed(1)},${(padTop + plotH - ((p.total_cost || 0) / maxCost) * plotH).toFixed(1)}`);

    return {
      escapePath: `M ${escPts.join(' L ')}`,
      overkillPath: `M ${ovkPts.join(' L ')}`,
      costPath: `M ${cstPts.join(' L ')}`,
    };
  }, [tradeoffCurve, tauToX, rateToY, padTop, plotH]);

  // Active hover data point
  const activePoint = useMemo(() => {
    if (!tradeoffCurve || tradeoffCurve.length === 0) return null;
    const target = hoverTau !== null ? hoverTau : currentThreshold;
    return tradeoffCurve.reduce((prev, curr) =>
      Math.abs(curr.threshold - target) < Math.abs(prev.threshold - target) ? curr : prev
    );
  }, [tradeoffCurve, hoverTau, currentThreshold]);

  const handlePointerDown = (e: React.PointerEvent<SVGSVGElement>) => {
    if (!svgRef.current) return;
    svgRef.current.setPointerCapture(e.pointerId);
    setIsDragging(true);
    const rect = svgRef.current.getBoundingClientRect();
    const x = ((e.clientX - rect.left) / rect.width) * width;
    onThresholdChange(xToTau(x));
  };

  const handlePointerMove = (e: React.PointerEvent<SVGSVGElement>) => {
    if (!svgRef.current) return;
    const rect = svgRef.current.getBoundingClientRect();
    const x = ((e.clientX - rect.left) / rect.width) * width;
    const tau = xToTau(x);
    setHoverTau(tau);
    if (isDragging) {
      onThresholdChange(tau);
    }
  };

  const handlePointerUp = (e: React.PointerEvent<SVGSVGElement>) => {
    if (svgRef.current && svgRef.current.hasPointerCapture(e.pointerId)) {
      svgRef.current.releasePointerCapture(e.pointerId);
    }
    setIsDragging(false);
  };

  const curX = tauToX(currentThreshold);
  const optX = tauToX(optimalThreshold);

  return (
    <div className="bg-[#1A212E] border border-[#2B3547] rounded-[6px] p-3 flex flex-col space-y-2 select-none">
      {/* Header bar */}
      <div className="flex items-center justify-between text-xs">
        <div className="flex items-center space-x-2">
          <Target className="w-3.5 h-3.5 text-[#10B981]" />
          <span className="font-bold text-slate-200">Zero-Escape (τ*) Tradeoff Calibration</span>
        </div>
        <button
          type="button"
          onClick={onApplyOptimal}
          className="px-2.5 py-1 bg-[#10B981] hover:bg-[#059669] active:bg-[#047857] text-[#0B0E14] font-bold text-[11px] rounded-[4px] border border-[#059669] flex items-center space-x-1 cursor-pointer transition-all"
        >
          <Zap className="w-3 h-3 fill-current" />
          <span>Apply τ* ({optimalThreshold.toFixed(4)})</span>
        </button>
      </div>

      {/* SVG Canvas */}
      <div className="relative w-full overflow-hidden bg-[#0B0E14] rounded-[4px] border border-[#2B3547]">
        <svg
          ref={svgRef}
          viewBox={`0 0 ${width} ${height}`}
          className="w-full h-auto cursor-crosshair block"
          onPointerDown={handlePointerDown}
          onPointerMove={handlePointerMove}
          onPointerUp={handlePointerUp}
          onPointerLeave={() => setHoverTau(null)}
        >
          {/* Background Grid */}
          {[0, 25, 50, 75, 100].map((rate) => {
            const y = rateToY(rate);
            return (
              <g key={rate}>
                <line x1={padLeft} y1={y} x2={padLeft + plotW} y2={y} stroke="#1E293B" strokeWidth="1" strokeDasharray="2,2" />
                <text x={padLeft - 6} y={y + 3} textAnchor="end" fill="#64748B" className="font-mono text-[9px] tabular-nums">
                  {rate}%
                </text>
              </g>
            );
          })}
          {[0.0, 0.2, 0.4, 0.6, 0.8, 1.0].map((tau) => {
            const x = tauToX(tau);
            return (
              <g key={tau}>
                <line x1={x} y1={padTop} x2={x} y2={padTop + plotH} stroke="#1E293B" strokeWidth="1" strokeDasharray="2,2" />
                <text x={x} y={padTop + plotH + 14} textAnchor="middle" fill="#64748B" className="font-mono text-[9px] tabular-nums">
                  {tau.toFixed(1)}
                </text>
              </g>
            );
          })}

          {/* Rate Curve Traces */}
          {costPath && <path d={costPath} fill="none" stroke="#38BDF8" strokeWidth="1" strokeDasharray="3,3" opacity="0.4" />}
          {overkillPath && <path d={overkillPath} fill="none" stroke="#F59E0B" strokeWidth="2" strokeLinejoin="round" />}
          {escapePath && <path d={escapePath} fill="none" stroke="#EF4444" strokeWidth="2" strokeLinejoin="round" />}

          {/* Optimal Zero-Escape Marker (tau*) */}
          <line x1={optX} y1={padTop} x2={optX} y2={padTop + plotH} stroke="#10B981" strokeWidth="1.5" strokeDasharray="3,3" />
          <g transform={`translate(${Math.min(optX, padLeft + plotW - 65)}, ${padTop})`}>
            <rect x="0" y="0" width="65" height="14" fill="#064E3B" stroke="#10B981" strokeWidth="1" rx="2" />
            <text x="32" y="10" textAnchor="middle" fill="#A7F3D0" className="font-mono text-[8px] font-bold tabular-nums">
              τ* = {optimalThreshold.toFixed(4)}
            </text>
          </g>

          {/* Active Operating Threshold Scrubber (tau) */}
          <line x1={curX} y1={padTop} x2={curX} y2={padTop + plotH} stroke="#3B82F6" strokeWidth="2" />
          <g transform={`translate(${curX - 22}, ${padTop + plotH - 16})`}>
            <rect x="0" y="0" width="44" height="16" fill="#1D4ED8" stroke="#60A5FA" strokeWidth="1" rx="2" />
            <text x="22" y="11" textAnchor="middle" fill="#FFFFFF" className="font-mono text-[9px] font-bold tabular-nums">
              τ={currentThreshold.toFixed(2)}
            </text>
          </g>
        </svg>
      </div>

      {/* Real-time HUD Telemetry Readout */}
      {activePoint && (
        <div className="grid grid-cols-4 gap-1.5 p-1.5 bg-[#0B0E14] border border-[#2B3547] rounded-[4px] text-[10px] font-mono tabular-nums">
          <div className="text-slate-400">
            Threshold: <span className="font-bold text-slate-100">{activePoint.threshold.toFixed(2)}</span>
          </div>
          <div className="text-[#EF4444]">
            Escape: <span className="font-bold">{activePoint.underkill_rate.toFixed(1)}%</span> ({activePoint.underkill_count})
          </div>
          <div className="text-[#F59E0B]">
            Overkill: <span className="font-bold">{activePoint.overkill_rate.toFixed(1)}%</span> ({activePoint.overkill_count})
          </div>
          <div className="text-slate-300">
            Cost: <span className="font-bold">${activePoint.total_cost?.toFixed(0) ?? 0}</span>
          </div>
        </div>
      )}
    </div>
  );
};

export default ZeroEscapeTradeoffChart;
