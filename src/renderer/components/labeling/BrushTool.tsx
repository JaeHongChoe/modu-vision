/**
 * src/renderer/components/labeling/BrushTool.tsx
 * Tool handler for freehand brush painting and eraser mode with radius slider.
 */

import React from 'react';
import { Eraser, Paintbrush } from 'lucide-react';
import { useAnnotationStore } from '../../stores/useAnnotationStore';

export const BrushTool: React.FC = () => {
  const { activeTool, setActiveTool, brushRadius, setBrushRadius } = useAnnotationStore();
  const isBrush = activeTool === 'brush';
  const isEraser = activeTool === 'eraser';

  return (
    <div className="flex items-center space-x-1">
      <button
        onClick={() => setActiveTool('brush')}
        title="Brush Tool"
        className={`p-2 rounded-lg transition-colors cursor-pointer ${
          isBrush ? 'bg-blue-600 text-white shadow' : 'hover:bg-slate-800 text-slate-400'
        }`}
      >
        <Paintbrush className="w-4 h-4" />
      </button>

      <button
        onClick={() => setActiveTool('eraser')}
        title="Eraser Tool"
        className={`p-2 rounded-lg transition-colors cursor-pointer ${
          isEraser ? 'bg-rose-600 text-white shadow' : 'hover:bg-slate-800 text-slate-400'
        }`}
      >
        <Eraser className="w-4 h-4" />
      </button>

      {(isBrush || isEraser) && (
        <div className="flex items-center space-x-2 ml-2 pl-2 border-l border-slate-800">
          <span className="text-xs text-slate-400 font-mono">{brushRadius}px</span>
          <input
            type="range"
            min="2"
            max="64"
            value={brushRadius}
            onChange={(e) => setBrushRadius(Number(e.target.value))}
            className="w-20 accent-blue-500 h-1 bg-slate-700 rounded-lg cursor-pointer"
          />
        </div>
      )}
    </div>
  );
};

export default BrushTool;
