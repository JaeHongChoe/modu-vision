/**
 * src/renderer/components/labeling/MaskLayerControls.tsx
 * Defect Mask / Heatmap HUD Controls.
 * Features:
 *   - Solid dark steel chassis styling (zero blurs)
 *   - Strict tabular-nums for alpha opacity readout
 *   - High-contrast text styling
 *   - Discrete LED status annunciator
 */

import React from 'react';
import { Eye, EyeOff, Layers } from 'lucide-react';
import { useAnnotationStore } from '../../stores/useAnnotationStore';

export const MaskLayerControls: React.FC = () => {
  const { maskOpacity, setMaskOpacity, maskVisible, toggleMaskVisible } = useAnnotationStore();

  return (
    <div className="absolute top-3 right-3 z-40 bg-[#131822] border border-[#2B3547] rounded p-2.5 flex items-center space-x-3 text-slate-200 shadow-lg select-none">
      <div className="flex items-center space-x-1.5 text-xs font-medium text-slate-300">
        <span
          className="w-2 h-2 rounded-full"
          style={{ backgroundColor: maskVisible ? '#10B981' : '#4B5563' }}
        />
        <Layers className="w-3.5 h-3.5 text-cyan-400" />
        <span>Mask Layer</span>
      </div>

      <button
        onClick={toggleMaskVisible}
        title={maskVisible ? 'Hide Mask Layer' : 'Show Mask Layer'}
        className="p-1 rounded bg-[#1A212E] hover:bg-[#222B3D] text-slate-300 border border-[#2B3547] transition-colors cursor-pointer"
      >
        {maskVisible ? (
          <Eye className="w-3.5 h-3.5 text-cyan-400" />
        ) : (
          <EyeOff className="w-3.5 h-3.5 text-slate-500" />
        )}
      </button>

      <div className="flex items-center space-x-2">
        <input
          type="range"
          min="0"
          max="1"
          step="0.05"
          value={maskOpacity}
          onChange={(e) => setMaskOpacity(parseFloat(e.target.value))}
          disabled={!maskVisible}
          className="w-20 accent-cyan-500 h-1 bg-[#1A212E] rounded cursor-pointer disabled:opacity-40"
        />
        <span className="text-[11px] font-mono tabular-nums text-slate-300 w-8">
          {Math.round(maskOpacity * 100)}%
        </span>
      </div>
    </div>
  );
};

export default MaskLayerControls;
