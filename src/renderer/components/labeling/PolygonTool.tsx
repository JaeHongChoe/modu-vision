/**
 * src/renderer/components/labeling/PolygonTool.tsx
 * Tool handler for polygon contour placement with vertex snapping.
 */

import React from 'react';
import { Scissors } from 'lucide-react';
import { useAnnotationStore } from '../../stores/useAnnotationStore';

export const PolygonTool: React.FC = () => {
  const { activeTool, setActiveTool } = useAnnotationStore();
  const isActive = activeTool === 'polygon';

  return (
    <button
      onClick={() => setActiveTool('polygon')}
      title="Polygon Contour Tool"
      className={`p-2 rounded-lg transition-colors cursor-pointer ${
        isActive ? 'bg-blue-600 text-white shadow' : 'hover:bg-slate-800 text-slate-400'
      }`}
    >
      <Scissors className="w-4 h-4" />
    </button>
  );
};

export default PolygonTool;
