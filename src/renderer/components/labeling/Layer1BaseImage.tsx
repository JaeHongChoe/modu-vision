/**
 * src/renderer/components/labeling/Layer1BaseImage.tsx
 * Layer 1 (Base Raster): Original inspection image rendered at 60fps pan/zoom.
 */

import React from 'react';

export interface Layer1Props {
  canvasRef?: React.RefObject<HTMLCanvasElement>;
  imageUrl?: string | null;
  width?: number;
  height?: number;
}

export const Layer1BaseImage: React.FC<Layer1Props> = ({ canvasRef }) => {
  return (
    <canvas
      ref={canvasRef}
      className="absolute inset-0 pointer-events-none z-10"
      data-layer="1-base-raster"
    />
  );
};

export default Layer1BaseImage;
