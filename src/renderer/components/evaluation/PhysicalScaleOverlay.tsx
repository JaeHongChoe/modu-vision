/**
 * src/renderer/components/evaluation/PhysicalScaleOverlay.tsx
 * Optical Calibration Scale Bar (Example Sensor CMOS Standard: 3.45 μm/px).
 * Dynamically selects 1-2-5 engineering step sizes based on zoom scale.
 */

import React, { useMemo } from 'react';

interface PhysicalScaleOverlayProps {
  scale: number; // Viewport zoom scale
  pixelPitchUm?: number; // Default: 3.45 um/px
}

const STEPS_UM = [5, 10, 20, 50, 100, 200, 500, 1000, 2000, 5000, 10000];

export const PhysicalScaleOverlay: React.FC<PhysicalScaleOverlayProps> = ({
  scale,
  pixelPitchUm = 3.45,
}) => {
  const { barWidthPx, labelText, resolutionText } = useMemo(() => {
    const targetBarPx = 90; // Preferred screen width of scale bar
    const idealUm = (targetBarPx * pixelPitchUm) / Math.max(0.01, scale);

    // Pick closest step in STEPS_UM
    let chosenUm = STEPS_UM[0];
    let minDiff = Infinity;
    for (const step of STEPS_UM) {
      const diff = Math.abs(Math.log10(step) - Math.log10(idealUm));
      if (diff < minDiff) {
        minDiff = diff;
        chosenUm = step;
      }
    }

    const calculatedPx = (chosenUm / pixelPitchUm) * scale;
    const label = chosenUm >= 1000 ? `${(chosenUm / 1000).toFixed(1)} mm` : `${chosenUm} μm`;
    const res = `${pixelPitchUm.toFixed(2)} μm/px (${(scale * 100).toFixed(0)}%)`;

    return {
      barWidthPx: Math.max(30, Math.min(220, calculatedPx)),
      labelText: label,
      resolutionText: res,
    };
  }, [scale, pixelPitchUm]);

  return (
    <div className="absolute bottom-3 left-3 z-20 pointer-events-none select-none bg-[#0B0E14]/90 border border-[#2B3547] px-2 py-1 rounded-[3px]">
      {/* Hairline bracket */}
      <div className="relative flex flex-col items-center" style={{ width: `${barWidthPx}px` }}>
        <div className="w-full flex items-center justify-between border-b border-[#F8FAFC]">
          <div className="w-[1px] h-2 bg-[#F8FAFC]" />
          <div className="w-[1px] h-2 bg-[#F8FAFC]" />
        </div>
        <span className="text-[10px] font-mono tabular-nums text-slate-100 font-bold mt-0.5">
          {labelText}
        </span>
        <span className="text-[8px] font-mono tabular-nums text-slate-400">
          {resolutionText}
        </span>
      </div>
    </div>
  );
};

export default PhysicalScaleOverlay;
