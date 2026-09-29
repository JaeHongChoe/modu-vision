/**
 * src/renderer/components/labeling/Layer3VectorUI.tsx
 * Layer 3 (Vector UI): Interactive vector canvas for bounding boxes, polygons, and cursor interactions.
 */

import React from 'react';

export interface Layer3Props {
  canvasRef?: React.RefObject<HTMLCanvasElement>;
  onPointerDown?: (e: React.PointerEvent<HTMLCanvasElement>) => void;
  onPointerMove?: (e: React.PointerEvent<HTMLCanvasElement>) => void;
  onPointerUp?: (e: React.PointerEvent<HTMLCanvasElement>) => void;
  onPointerLeave?: (e: React.PointerEvent<HTMLCanvasElement>) => void;
}

export const Layer3VectorUI: React.FC<Layer3Props> = ({
  canvasRef,
  onPointerDown,
  onPointerMove,
  onPointerUp,
  onPointerLeave,
}) => {
  return (
    <canvas
      ref={canvasRef}
      className="absolute inset-0 z-30"
      data-layer="3-vector-ui"
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={onPointerUp}
      onPointerLeave={onPointerLeave}
    />
  );
};

export default Layer3VectorUI;
