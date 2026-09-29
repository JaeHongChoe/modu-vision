/**
 * src/renderer/components/labeling/BoundingBoxTool.tsx
 * Tool handler for 8-handle resizable bounding box creation and editing.
 */

import React from 'react';
import { Square } from 'lucide-react';
import { useAnnotationStore } from '../../stores/useAnnotationStore';

export const BoundingBoxTool: React.FC = () => {
  const { activeTool, setActiveTool } = useAnnotationStore();
  const isActive = activeTool === 'bbox';

  return (
    <button
      onClick={() => setActiveTool('bbox')}
      title="Bounding Box Tool"
      className={`p-2 rounded-lg transition-colors cursor-pointer ${
        isActive ? 'bg-blue-600 text-white shadow' : 'hover:bg-slate-800 text-slate-400'
      }`}
    >
      <Square className="w-4 h-4" />
    </button>
  );
};

export default BoundingBoxTool;
